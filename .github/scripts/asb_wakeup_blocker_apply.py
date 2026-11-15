#!/usr/bin/env python3
"""Select and apply the reviewed ASB wakeup-blocker patch by exact kernel version.

The patch preflight runs before any target file is changed. Unknown sublevels and
source drift fail the build before compilation; diagnostics contain source/patch
identity only, never device identifiers or credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

SUPPORTED = {69, 92}
MAKEFILE_LINE = "obj-$(CONFIG_ASB_WAKEUP_BLOCKER)\t+= asb_wakeup_blocker.o"
CALL = "asb_wakeup_source_blocked(ws->name)"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def version(tree: Path) -> tuple[int, int, int]:
    values: dict[str, int] = {}
    for line in (tree / "Makefile").read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"(VERSION|PATCHLEVEL|SUBLEVEL) = (\d+)", line)
        if match:
            values[match.group(1)] = int(match.group(2))
    if set(values) != {"VERSION", "PATCHLEVEL", "SUBLEVEL"}:
        raise ValueError("Makefile lacks exact numeric VERSION/PATCHLEVEL/SUBLEVEL")
    return values["VERSION"], values["PATCHLEVEL"], values["SUBLEVEL"]


def git_head(tree: Path) -> str:
    result = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"],
                            text=True, capture_output=True, check=False)
    commit = result.stdout.strip()
    return commit if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", commit) else "unknown"


def kconfig(tree: Path) -> None:
    path = tree / "drivers/Kconfig"
    text = path.read_text(encoding="utf-8")
    if "config ASB_WAKEUP_BLOCKER" in text:
        raise ValueError("duplicate ASB wakeup Kconfig entry")
    if not text.endswith("\nendmenu\n"):
        raise ValueError("drivers/Kconfig final menu anchor changed")
    block = (
        'config ASB_WAKEUP_BLOCKER\n'
        '\tbool "ASB bounded exact-name wakeup source blocker"\n'
        '\tdepends on PM_SLEEP\n'
        '\tdefault n\n'
        '\thelp\n'
        '\t  Allow root to suppress future events from explicitly named wakeup\n'
        '\t  sources through /sys/kernel/asb_wakeup/blocklist. No sources are\n'
        '\t  blocked by default; this can break alarms, calls or networking.\n'
        '\n'
    )
    prefix = text[:-len("endmenu\n")]
    if not prefix.endswith("\n\n"):
        prefix += "\n"
    path.write_text(prefix + block + "endmenu\n", encoding="utf-8")


def apply(args: argparse.Namespace) -> dict:
    workspace = Path(args.workspace).resolve()
    tree = Path(args.tree).resolve()
    output = Path(args.output).resolve()
    log_path = Path(args.log).resolve()
    report: dict = {
        "schema": 1,
        "family": args.family,
        "status": "preflight_failed",
        "source_commit": git_head(tree),
        "scope": "source patch preflight and static integration checks; not boot or runtime validation",
    }
    log = ""
    try:
        if args.family != "android16-6.12":
            raise ValueError("ASB wakeup blocker is reviewed only for android16-6.12")
        major, patchlevel, sublevel = version(tree)
        report["kernel"] = f"{major}.{patchlevel}.{sublevel}"
        if (major, patchlevel) != (6, 12) or sublevel not in SUPPORTED:
            supported = ", ".join(f"6.12.{item}" for item in sorted(SUPPORTED))
            raise ValueError(f"no reviewed ASB blocker patch for {major}.{patchlevel}.{sublevel}; reviewed: {supported}")
        patch = Path(args.patch_root).resolve() / f"asb-wakeup-blocker-6.12.{sublevel}.patch"
        if not patch.is_file():
            raise ValueError(f"missing reviewed patch: {patch}")
        report.update({"patch": patch.name, "patch_sha256": sha256(patch)})

        wakeup_path = tree / "drivers/base/power/wakeup.c"
        wakeup = wakeup_path.read_text(encoding="utf-8")
        if CALL in wakeup or "#include <linux/asb_wakeup_blocker.h>" in wakeup:
            raise ValueError("ASB wakeup hooks already exist; refusing a duplicate integration")
        for relative in ("drivers/base/power/asb_wakeup_blocker.c", "include/linux/asb_wakeup_blocker.h"):
            if (tree / relative).exists():
                raise ValueError(f"target already exists before integration: {relative}")
        if sublevel == 92 and "#include <linux/string.h>" not in wakeup:
            raise ValueError("6.12.92 string.h source anchor is missing")
        if wakeup.count("if (!ws)") < 2:
            raise ValueError("expected wakeup call-site anchors are missing")

        checked = subprocess.run(["git", "apply", "--check", str(patch)], cwd=tree,
                                 text=True, capture_output=True, check=False)
        log = checked.stdout + checked.stderr
        log_path.write_text(log, encoding="utf-8")
        if checked.returncode != 0:
            raise ValueError(f"git apply --check rejected {patch.name}; see {log_path.name}")

        applied = subprocess.run(["git", "apply", str(patch)], cwd=tree,
                                 text=True, capture_output=True, check=False)
        log += applied.stdout + applied.stderr
        log_path.write_text(log, encoding="utf-8")
        if applied.returncode != 0:
            report["status"] = "apply_failed"
            raise ValueError(f"git apply failed after a successful preflight: {patch.name}")

        kconfig(tree)
        wakeup = wakeup_path.read_text(encoding="utf-8")
        makefile = (tree / "drivers/base/power/Makefile").read_text(encoding="utf-8")
        if wakeup.count(CALL) != 2:
            raise ValueError(f"expected exactly two wakeup call-sites, found {wakeup.count(CALL)}")
        if wakeup.count("#include <linux/asb_wakeup_blocker.h>") != 1:
            raise ValueError("expected exactly one ASB blocker include")
        if makefile.splitlines().count(MAKEFILE_LINE) != 1:
            raise ValueError("expected exactly one ASB blocker Makefile object")
        if (tree / "drivers/Kconfig").read_text(encoding="utf-8").count("config ASB_WAKEUP_BLOCKER") != 1:
            raise ValueError("expected exactly one ASB blocker Kconfig entry")
        for relative in ("drivers/base/power/asb_wakeup_blocker.c", "include/linux/asb_wakeup_blocker.h"):
            if not (tree / relative).is_file():
                raise ValueError(f"integrated file is missing: {relative}")
        report["status"] = "applied"
        report["call_sites"] = 2
        return report
    except (OSError, ValueError) as exc:
        report["error"] = str(exc)
        if not log_path.exists():
            log_path.write_text(log, encoding="utf-8")
        raise
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--tree", required=True)
    parser.add_argument("--family", required=True)
    parser.add_argument("--patch-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--log", required=True)
    args = parser.parse_args()
    try:
        report = apply(args)
    except (OSError, ValueError) as exc:
        print(f"ASB wakeup blocker preflight error: {exc}", file=sys.stderr)
        return 1
    print(f"ASB wakeup blocker: {report['kernel']}, {report['patch']}, "
          f"SHA-256 {report['patch_sha256']}, exactly two call-sites")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
