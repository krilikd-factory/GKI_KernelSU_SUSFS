#!/usr/bin/env python3
"""Offline fail-closed applicability farm for external patches on clean Git trees.

Runs git apply --check, then applies in a disposable worktree for diff/Kconfig checks.
Compilation and physical boot are explicitly outside this host-side report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(tree: Path, *args: str, patch: Path | None = None) -> subprocess.CompletedProcess:
    stdin = patch.open("rb") if patch else None
    try:
        return subprocess.run(["git", "-C", str(tree), *args], stdin=stdin,
                              text=True, capture_output=True, check=False)
    finally:
        if stdin:
            stdin.close()


def checked(tree: Path, *args: str) -> str:
    result = run(tree, *args)
    if result.returncode:
        raise ValueError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def kconfig_symbols(tree: Path) -> set[str]:
    import re
    found: set[str] = set()
    for base, dirs, files in os.walk(tree):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            if not name.startswith("Kconfig"):
                continue
            text = (Path(base) / name).read_text(errors="replace")
            found.update(re.findall(r"(?m)^\s*(?:menu)?config\s+([A-Za-z0-9_]+)\s*$", text))
    return found


def reject_artifact(directory: Path, tree_name: str, patch: Path, head: str,
                    digest: str, stage: str, detail: str) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{tree_name}--{patch.stem}--{digest[:12]}.reject.json"
    target.write_text(json.dumps({"tree": tree_name, "tree_head": head, "patch": str(patch),
                                  "patch_sha256": digest, "stage": stage, "diagnostic": detail},
                                 indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return str(target)


def evaluate(tree_name: str, tree: Path, patch: Path, required_kconfig: list[str],
             reject_dir: Path) -> dict:
    digest = hashlib.sha256(patch.read_bytes()).hexdigest()
    row = {"tree": tree_name, "patch": str(patch), "patch_sha256": digest,
           "git_apply_check": "not_run", "apply_in_disposable_worktree": "not_run",
           "diff_check": "not_run", "kconfig_verification": "not_requested",
           "compile_only_smoke": "not_run", "physical_boot": "not_run", "status": "rejected"}
    try:
        if not tree.is_dir() or not (tree / "Makefile").is_file():
            raise ValueError("tree or Makefile is missing")
        head = checked(tree, "rev-parse", "HEAD")
        row["tree_head"] = head
        if checked(tree, "status", "--porcelain"):
            raise ValueError("source tree is not clean")
        preflight = run(tree, "apply", "--check", str(patch))
        row["git_apply_check"] = "passed" if preflight.returncode == 0 else "failed"
        if preflight.returncode:
            raise ValueError(preflight.stderr.strip() or "git apply --check failed")
        temporary = Path(tempfile.mkdtemp(prefix="asb-patch-farm-"))
        worktree = temporary / "tree"
        try:
            checked(tree, "worktree", "add", "--detach", str(worktree), head)
            applied = run(worktree, "apply", str(patch))
            row["apply_in_disposable_worktree"] = "passed" if applied.returncode == 0 else "failed"
            if applied.returncode:
                raise ValueError(applied.stderr.strip() or "disposable apply failed after check")
            diff = run(worktree, "diff", "--check")
            row["diff_check"] = "passed" if diff.returncode == 0 else "failed"
            if diff.returncode:
                raise ValueError(diff.stdout.strip() or diff.stderr.strip() or "git diff --check failed")
            if required_kconfig:
                symbols = kconfig_symbols(worktree)
                missing = [name for name in required_kconfig if name.removeprefix("CONFIG_") not in symbols]
                row["kconfig_verification"] = "passed" if not missing else "failed"
                row["required_kconfig_symbols"] = required_kconfig
                if missing:
                    raise ValueError("required Kconfig symbols not declared: " + ", ".join(missing))
            row["status"] = "applicable_host_only"
            return row
        finally:
            run(tree, "worktree", "remove", "--force", str(worktree))
            run(tree, "worktree", "prune")
            shutil.rmtree(temporary, ignore_errors=True)
    except (OSError, ValueError) as exc:
        row["error"] = str(exc)
        row["reject_artifact"] = reject_artifact(reject_dir, tree_name, patch, row.get("tree_head", "unknown"),
                                                digest, row["git_apply_check"], str(exc))
        return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", action="append", required=True, help="NAME=/path/to/clean/git/tree")
    ap.add_argument("--patch", action="append", required=True, type=Path)
    ap.add_argument("--require-kconfig", action="append", default=[])
    ap.add_argument("--reject-dir", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()
    trees = []
    for spec in args.tree:
        name, separator, path = spec.partition("=")
        if not separator or not name or not path:
            raise ValueError("--tree must be NAME=/path")
        trees.append((name, Path(path).resolve()))
    rows = [evaluate(name, tree, patch.resolve(), args.require_kconfig, args.reject_dir)
            for name, tree in trees for patch in args.patch]
    report = {"schema": 1, "scope": "host applicability only; compile-only smoke and physical boot were not run",
              "results": rows, "applicable": sum(r["status"] == "applicable_host_only" for r in rows),
              "rejected": sum(r["status"] == "rejected" for r in rows)}
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Patch farm: {report['applicable']} applicable host-only, {report['rejected']} rejected")
    return 1 if report["rejected"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"patch applicability farm error: {exc}", file=sys.stderr)
        sys.exit(2)
