#!/usr/bin/env python3
"""Make KernelSU/SUSFS supercalls compile across SUSFS API generations.

KernelSU-Next may retain the legacy SUS_PATH UID-filter command while newer
susfs4ksu branches have removed its implementation.  Do not add a fake
function: remove only that unavailable dispatch arm so unsupported userspace
calls receive the normal unknown-command result, while all supported SUSFS
features remain enabled.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

LEGACY_SYMBOL = "susfs_set_sus_path_uid_filter"
LEGACY_COMMAND = "CMD_SUSFS_SET_SUS_PATH_UID_FILTER"


def find_dispatch(tree: Path) -> Path:
    candidates = sorted(
        set(tree.rglob("drivers/kernelsu/supercall/dispatch.c"))
        | set(tree.rglob("kernel/supercall/dispatch.c"))
    )
    if not candidates:
        raise SystemExit(f"ERROR: KernelSU dispatch.c not found below {tree}")
    if len(candidates) > 1:
        raise SystemExit("ERROR: multiple KernelSU dispatch.c files found: " + ", ".join(map(str, candidates)))
    return candidates[0]


def has_implementation(tree: Path) -> bool:
    for path in tree.rglob("*"):
        if path.is_file() and path.name in {"susfs.h", "susfs.c", "susfs_def.h"}:
            try:
                if LEGACY_SYMBOL in path.read_text(encoding="utf-8", errors="replace"):
                    return True
            except OSError:
                pass
    return False


def remove_legacy_arm(source: str) -> tuple[str, bool]:
    # Keep the surrounding CONFIG_KSU_SUSFS_SUS_PATH guard intact.  The exact
    # three-line arm is intentionally required so a SUSFS layout change fails
    # loudly instead of deleting an unrelated switch case.
    pattern = re.compile(
        r"(?m)^([ \t]*)case "
        + re.escape(LEGACY_COMMAND)
        + r":\n\1    "
        + re.escape(LEGACY_SYMBOL)
        + r"\(arg\);\n\1    return 0;\n"
    )
    updated, count = pattern.subn("", source)
    if count != 1:
        raise SystemExit(f"ERROR: expected exactly one unavailable {LEGACY_COMMAND} dispatch arm, found {count}")
    return updated, True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tree", type=Path, help="kernel source tree")
    args = parser.parse_args()
    tree = args.tree.resolve()
    dispatch = find_dispatch(tree)
    source = dispatch.read_text(encoding="utf-8")

    if LEGACY_SYMBOL not in source or LEGACY_COMMAND not in source:
        print("KSU/SUSFS API compatibility: legacy UID-filter dispatch is already absent")
        return 0
    if has_implementation(tree):
        print("KSU/SUSFS API compatibility: legacy UID-filter implementation is present; unchanged")
        return 0

    updated, _ = remove_legacy_arm(source)
    dispatch.write_text(updated, encoding="utf-8")
    if LEGACY_SYMBOL in dispatch.read_text(encoding="utf-8"):
        raise SystemExit("ERROR: legacy SUSFS UID-filter symbol remains in dispatch.c")
    print(f"KSU/SUSFS API compatibility: removed unsupported {LEGACY_COMMAND} arm from {dispatch}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
