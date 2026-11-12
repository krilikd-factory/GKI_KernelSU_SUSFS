#!/usr/bin/env python3
"""Preflight the actual downloaded kernel tree and emit reproducible provenance.

This runs before any feature patch modifies common/. It verifies the checked-out
commit and kernel version instead of trusting only the resolver's earlier log.
No network access or device identifiers are used.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HASH_FILES = (
    "Makefile",
    "BUILD.bazel",
    "build.config.gki.aarch64",
    "build.config.constants",
    "arch/arm64/configs/gki_defconfig",
)


def git(tree: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(tree), *args], check=True,
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def kernel_version(tree: Path) -> tuple[str, int]:
    values = {}
    for line in (tree / "Makefile").read_text(errors="replace").splitlines():
        match = re.match(r"^(VERSION|PATCHLEVEL|SUBLEVEL) = (\d+)$", line)
        if match:
            values[match.group(1)] = match.group(2)
    if len(values) != 3:
        raise ValueError("Makefile does not expose numeric VERSION/PATCHLEVEL/SUBLEVEL")
    return f"{values['VERSION']}.{values['PATCHLEVEL']}", int(values["SUBLEVEL"])


def build_report(a: argparse.Namespace) -> dict:
    tree = Path(a.tree).resolve()
    device = json.loads(Path(a.device_file).read_text(encoding="utf-8"))
    if a.source not in ("aosp", "oneplus"):
        raise ValueError("source must be aosp or oneplus")
    if not HEX40.fullmatch(a.commit):
        raise ValueError("expected source commit is not a full lowercase Git SHA")
    actual_commit = git(tree, "rev-parse", "HEAD")
    if actual_commit != a.commit:
        raise ValueError(f"source commit mismatch: resolver={a.commit}, actual={actual_commit}")
    dirty = git(tree, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise ValueError("source tree is already modified before preflight; provenance would be ambiguous")
    actual_version, actual_sublevel = kernel_version(tree)
    if actual_version != a.expected_kernel or (a.expected_sublevel != "X" and actual_sublevel != int(a.expected_sublevel)):
        raise ValueError(
            f"kernel version mismatch: expected {a.expected_kernel}.{a.expected_sublevel}, "
            f"actual {actual_version}.{actual_sublevel}")

    if a.source == "oneplus":
        expected_repo = device.get("oneplus_kernel_repo", "")
        if a.oem_repo != expected_repo:
            raise ValueError("OnePlus repository differs from .github/device.json")
        if not fnmatch.fnmatchcase(a.oem_branch, device.get("oneplus_branch_match", "")):
            raise ValueError("OnePlus branch does not match the device branch pattern")
        if a.family != device.get("stock_kernel_family"):
            raise ValueError("OnePlus source family differs from the device stock family")
        if not a.expected_sublevel.isdigit():
            raise ValueError("OnePlus source requires an explicit numeric sublevel")
        if actual_sublevel < int(device["stock_sublevel"]):
            raise ValueError("OnePlus source sublevel is older than the device stock kernel")
        if not re.fullmatch(r"\d{4}-\d{2}", a.patch_level):
            raise ValueError("OnePlus base patch level is unknown or malformed")
        if a.patch_level < device["stock_patch_level"]:
            raise ValueError("OnePlus base patch level is older than the device stock firmware")

    base_manifest = Path(a.base_manifest)
    if not base_manifest.is_file():
        raise ValueError(f"AOSP base manifest is missing: {base_manifest}")
    manifest_info = {"path": str(base_manifest), "sha256": sha256(base_manifest),
                     "bytes": base_manifest.stat().st_size}
    files = {}
    for name in HASH_FILES:
        path = tree / name
        if path.is_file():
            files[name] = {"sha256": sha256(path), "bytes": path.stat().st_size}
        elif name in ("Makefile", "build.config.gki.aarch64", "arch/arm64/configs/gki_defconfig"):
            raise ValueError(f"required source file is missing: {name}")
    clang_revisions = {}
    for name in ("build.config.constants", "build.config.gki.aarch64"):
        path = tree / name
        if path.is_file():
            for match in re.finditer(r'^(?:export\s+)?CLANG_VERSION=([A-Za-z0-9._-]+)', path.read_text(errors="replace"), re.M):
                clang_revisions[name] = match.group(1)
    identity = {
        "source": a.source,
        "family": a.family,
        "commit": actual_commit,
        "kernel": f"{actual_version}.{actual_sublevel}",
        "patch_level": a.patch_level,
        "files": files,
        "base_manifest": manifest_info,
        "clang_revision": clang_revisions or "not_exposed",
    }
    source_digest = hashlib.sha256(json.dumps(identity, sort_keys=True,
                               separators=(",", ":")).encode()).hexdigest()
    return {
        "schema": 1,
        "stage": "downloaded-source-preflight",
        **identity,
        "source_digest": source_digest,
        "git_clean_before_features": True,
        "oem": {
            "repo": a.oem_repo,
            "branch": a.oem_branch,
        } if a.source == "oneplus" else None,
        "scope": "source identity and selected build inputs; not boot or runtime validation",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--device-file", required=True)
    ap.add_argument("--base-manifest", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--family", required=True)
    ap.add_argument("--expected-kernel", required=True)
    ap.add_argument("--expected-sublevel", required=True)
    ap.add_argument("--patch-level", required=True)
    ap.add_argument("--commit", required=True)
    ap.add_argument("--oem-repo", default="")
    ap.add_argument("--oem-branch", default="")
    a = ap.parse_args()
    report = build_report(a)
    Path(a.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Source preflight: {report['source']} {report['kernel']} @ {report['commit'][:12]}, "
          f"digest {report['source_digest'][:16]}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(f"source preflight error: {exc}", file=sys.stderr)
        sys.exit(1)
