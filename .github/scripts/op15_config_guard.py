#!/usr/bin/env python3
"""Verify the requested OnePlus 15 policy in the final resolved .config.

The defconfig request is applied separately through scripts/config. This gate is
intentionally later: Kconfig dependencies and feature actions cannot silently
restore POSIX mqueues/devtmpfs or remove the scheduler debug ABI prerequisites.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

EXPECTED = {
    "CONFIG_POSIX_MQUEUE": "n",
    "CONFIG_DEVTMPFS": "n",
    "CONFIG_DEVTMPFS_MOUNT": "n",
    "CONFIG_DEBUG_FS": "y",
    "CONFIG_SCHED_DEBUG": "y",
}

PERFORMANCE_EXPECTED = {
    "CONFIG_WQ_POWER_EFFICIENT_DEFAULT": "y",
    "CONFIG_KFENCE_SAMPLE_INTERVAL": "0",
}

# Kconfig omits DEVTMPFS_MOUNT entirely when its parent DEVTMPFS is disabled.
# In that case absence is semantically equivalent to n; an actual y/m value is
# still rejected below.
HIDDEN_N_WITH_DISABLED_PARENT = {
    "CONFIG_DEVTMPFS_MOUNT": "CONFIG_DEVTMPFS",
}


def parse_config(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        match = re.match(r"^(CONFIG_[A-Za-z0-9_]+)=(.*)$", line)
        if match:
            values[match.group(1)] = match.group(2)
            continue
        match = re.match(r"^# (CONFIG_[A-Za-z0-9_]+) is not set$", line)
        if match:
            values[match.group(1)] = "n"
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--require-performance", action="store_true")
    args = parser.parse_args()

    actual = parse_config(args.config)
    expected_values = dict(EXPECTED)
    if args.require_performance:
        expected_values.update(PERFORMANCE_EXPECTED)
    lines = ["OnePlus 15 final-config policy (android16-6.12):"]
    failed = []
    for key, expected in expected_values.items():
        got = actual.get(key, "not_exposed")
        effective = got
        note = ""
        parent = HIDDEN_N_WITH_DISABLED_PARENT.get(key)
        if got == "not_exposed" and expected == "n" and parent and actual.get(parent) == "n":
            effective = "n"
            note = " (hidden by disabled parent; effective n)"
        lines.append(f"  {key}: expected={expected} final={got}{note}")
        if effective != expected:
            failed.append(f"{key}: expected {expected}, got {got}")
    lines.append(
        "  Power/performance scope: " + (
            "Perf requested; WQ power-efficient default and zero KFENCE sampling interval verified"
            if args.require_performance else
            "Perf not requested; its two optional defaults were not required"
        )
    )
    lines.append(
        "  CONFIG_SCHED_AUTOGROUP: not forced by this policy; runtime availability remains separate"
    )
    text = "\n".join(lines)
    print(text)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    if failed:
        print("ERROR: OnePlus 15 final-config policy failed: " + "; ".join(failed), file=sys.stderr)
        return 1
    print("OnePlus 15 final-config policy verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
