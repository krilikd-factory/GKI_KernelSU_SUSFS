#!/usr/bin/env python3
"""Describe build-time kernel capabilities for ASB without inventing runtime support.

The report is generated from the final .config after Kconfig resolution. Runtime
paths are discovery hints only: their state remains ``unknown`` until ASB reads
them on the device. No kernel writer, polling service or custom sysfs ABI is
created by this script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

CAPABILITIES = {
    "memory.psi": {
        "configs": ["CONFIG_PSI"],
        "runtime_paths": ["/proc/pressure/cpu", "/proc/pressure/memory", "/proc/pressure/io"],
        "asb_use": "read-only pressure evidence for memory/IO/CPU decisions",
    },
    "memory.mglru": {
        "configs": ["CONFIG_LRU_GEN"],
        "runtime_paths": ["/sys/kernel/mm/lru_gen/enabled"],
        "asb_use": "discover MGLRU; do not assume it is enabled from config alone",
    },
    "memory.zram": {
        "configs": ["CONFIG_ZRAM", "CONFIG_ZSMALLOC"],
        "runtime_paths": ["/sys/block/zram0"],
        "asb_use": "read-only zram statistics; policy remains userspace-owned",
    },
    "network.bbr": {
        "configs": ["CONFIG_TCP_CONG_BBR"],
        "runtime_paths": ["/proc/sys/net/ipv4/tcp_available_congestion_control"],
        "asb_use": "discover congestion-control availability; BBRv3 is not inferred from this flag",
    },
    "bpf.telemetry": {
        "configs": ["CONFIG_BPF", "CONFIG_BPF_SYSCALL", "CONFIG_DEBUG_INFO_BTF"],
        "runtime_paths": ["/sys/kernel/btf/vmlinux"],
        "asb_use": "read-only telemetry potential; loading programs still requires separate validation",
    },
    "storage.ufs": {
        "configs": ["CONFIG_SCSI_UFSHCD"],
        "runtime_paths": ["/sys/class/scsi_host"],
        "asb_use": "storage discovery only; no hardcoded UFS clock or QoS writer",
    },
    "power.workqueues": {
        "configs": ["CONFIG_WQ_POWER_EFFICIENT_DEFAULT"],
        "runtime_paths": [],
        "asb_use": "build default only; no power-saving percentage is implied",
    },
}


def parse_config(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text(errors="replace").splitlines():
        match = re.match(r"^(CONFIG_[A-Za-z0-9_]+)=(.*)$", line.strip())
        if match:
            values[match.group(1)] = match.group(2)
            continue
        match = re.match(r"^# (CONFIG_[A-Za-z0-9_]+) is not set$", line.strip())
        if match:
            values[match.group(1)] = "n"
    return values


def make_report(config_path: Path, feature_set: str) -> dict:
    raw = config_path.read_bytes()
    config = parse_config(config_path)
    capabilities = {}
    for name, spec in CAPABILITIES.items():
        states = {key: config.get(key, "not_exposed") for key in spec["configs"]}
        build_state = "available_in_config" if all(v in ("y", "m") for v in states.values()) else "not_available_in_config"
        capabilities[name] = {
            "build_state": build_state,
            "configs": states,
            "runtime_state": "unknown",
            "runtime_paths": spec["runtime_paths"],
            "asb_use": spec["asb_use"],
        }
    return {
        "schema": 1,
        "scope": "final build config only; runtime and physical-device validation are separate",
        "final_config_sha256": hashlib.sha256(raw).hexdigest(),
        "feature_set": feature_set,
        "capabilities": capabilities,
        "asb_contract": {
            "ownership": "ASB owns userspace policy; kernel and vendor thermal stack retain their clamps",
            "control_plane": "not_implemented",
            "custom_sysfs_abi": False,
            "runtime_discovery_required": True,
            "writers_added": [],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--feature-set", default="")
    ap.add_argument("--output", required=True)
    a = ap.parse_args()
    report = make_report(Path(a.config), a.feature_set)
    Path(a.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    available = sum(c["build_state"] == "available_in_config" for c in report["capabilities"].values())
    print(f"ASB capability report: {available}/{len(report['capabilities'])} groups present in final config; runtime=unknown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
