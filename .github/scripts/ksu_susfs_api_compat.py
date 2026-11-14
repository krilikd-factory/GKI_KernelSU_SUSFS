#!/usr/bin/env python3
"""Make KernelSU/SUSFS supercalls compile across SUSFS API generations.

KernelSU-Next may retain the legacy SUS_PATH UID-filter command and its
periodic filter-refresh call while susfs4ksu no longer provides either symbol.
Do not add a fake function: remove only those unavailable call sites so the
unsupported userspace command receives the normal unknown-command result and
no refresh work is scheduled. All supported SUSFS features remain enabled.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

LEGACY_SYMBOL = "susfs_set_sus_path_uid_filter"
LEGACY_PARSE_SYMBOL = "susfs_parse_sus_path_app_strings"
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


def has_implementation(tree: Path, symbol: str) -> bool:
    for path in tree.rglob("*"):
        if path.is_file() and path.name in {"susfs.h", "susfs.c", "susfs_def.h"}:
            try:
                if symbol in path.read_text(encoding="utf-8", errors="replace"):
                    return True
            except OSError:
                pass
    return False


def find_throne_headers(tree: Path) -> list[Path]:
    return sorted(
        set(tree.rglob("drivers/kernelsu/manager/throne_tracker.h"))
        | set(tree.rglob("kernel/manager/throne_tracker.h"))
    )


def remove_legacy_refresh(source: str) -> str:
    # Newer KSUN calls this from the threaded throne tracker too, so the old
    # Kconfig-default analysis is insufficient. Remove exactly the standalone
    # call and preserve CONFIG_KSU_SUSFS_SUS_PATH and all other refresh logic.
    pattern = re.compile(r"(?m)^[ \t]*" + re.escape(LEGACY_PARSE_SYMBOL) + r"\(\);\n")
    updated, count = pattern.subn("", source)
    if count != 1:
        raise SystemExit(f"ERROR: expected exactly one unavailable {LEGACY_PARSE_SYMBOL} call, found {count}")
    return updated


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

    if (LEGACY_SYMBOL in source or LEGACY_COMMAND in source) and not has_implementation(tree, LEGACY_SYMBOL):
        updated, _ = remove_legacy_arm(source)
        dispatch.write_text(updated, encoding="utf-8")
        if LEGACY_SYMBOL in dispatch.read_text(encoding="utf-8"):
            raise SystemExit("ERROR: legacy SUSFS UID-filter symbol remains in dispatch.c")
        print(f"KSU/SUSFS API compatibility: removed unsupported {LEGACY_COMMAND} arm from {dispatch}")
    else:
        print("KSU/SUSFS API compatibility: legacy UID-filter dispatch is supported or already absent")

    if has_implementation(tree, LEGACY_PARSE_SYMBOL):
        print("KSU/SUSFS API compatibility: SUS_PATH filter refresh is implemented; unchanged")
        return 0
    headers = [p for p in find_throne_headers(tree) if LEGACY_PARSE_SYMBOL in p.read_text(encoding="utf-8")]
    if not headers:
        print("KSU/SUSFS API compatibility: unsupported SUS_PATH filter refresh is already absent")
        return 0
    if len(headers) != 1:
        raise SystemExit("ERROR: multiple KernelSU throne_tracker.h call sites need review: " + ", ".join(map(str, headers)))
    header = headers[0]
    header.write_text(remove_legacy_refresh(header.read_text(encoding="utf-8")), encoding="utf-8")
    if LEGACY_PARSE_SYMBOL in header.read_text(encoding="utf-8"):
        raise SystemExit("ERROR: unsupported SUS_PATH filter refresh remains in throne_tracker.h")
    print(f"KSU/SUSFS API compatibility: removed unsupported {LEGACY_PARSE_SYMBOL} call from {header}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
