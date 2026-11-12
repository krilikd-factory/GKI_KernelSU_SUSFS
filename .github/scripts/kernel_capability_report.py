#!/usr/bin/env python3
"""Describe build-time kernel capabilities and standard ASB control candidates.

The report is generated from the final .config after Kconfig resolution. Runtime
paths are discovery hints only: their state and writability remain ``unknown``
until ASB checks them on the device. No kernel writer, polling service or custom
sysfs ABI is created by this script.
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

# These are descriptions of existing Linux interfaces, not a KRILIKD ABI. ASB must
# apply the same closed allowlist even if this JSON is absent or comes from another
# kernel. A config flag proves neither path existence nor write permission.
CONTROL_CANDIDATES = {
    "mglru_mode": {
        "configs": ["CONFIG_LRU_GEN"],
        "runtime_path": "/sys/kernel/mm/lru_gen/enabled",
        "value_type": "mglru_bitmask",
        "requests": {
            "stock": "restore the exact value captured before ASB's first write",
            "off": ["0"],
            "on": ["7"],
            "smart": ["5", "7"],
        },
        "owner": "ASB kernel-controls helper; Smart is gated through the same owner",
        "readback": "numeric bitmask equivalence is required; a clamped bitmask is reported as readback_mismatch",
    },
    "kernel_timer_migration": {
        "configs": [],
        "runtime_path": "/proc/sys/kernel/timer_migration",
        "value_type": "boolean_0_1",
        "requests": {
            "stock": "restore the exact value captured before ASB's first write",
            "off": ["0"],
            "on": ["1"],
        },
        "owner": "ASB kernel-controls helper; profile and reinforce paths do not write directly",
        "readback": "exact 0/1 readback is required",
    },
    "kernel_sched_nr_migrate": {
        "configs": [],
        "runtime_path": "/proc/sys/kernel/sched_nr_migrate",
        "value_type": "bounded_integer",
        "requests": {
            "stock": "restore the exact value captured before ASB's first write",
            "1": ["1"],
            "2": ["2"],
            "4": ["4"],
            "8": ["8"],
            "16": ["16"],
        },
        "owner": "ASB kernel-controls helper; profile and reinforce paths do not write directly",
        "readback": "exact integer readback is required",
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


def build_state(config: dict[str, str], required: list[str]) -> tuple[str, dict[str, str]]:
    states = {key: config.get(key, "not_exposed") for key in required}
    state = "available_in_config" if all(v in ("y", "m") for v in states.values()) else "not_available_in_config"
    return state, states


def make_report(config_path: Path, feature_set: str) -> dict:
    raw = config_path.read_bytes()
    config = parse_config(config_path)
    capabilities = {}
    for name, spec in CAPABILITIES.items():
        state, states = build_state(config, spec["configs"])
        capabilities[name] = {
            "build_state": state,
            "configs": states,
            "runtime_state": "unknown",
            "runtime_paths": spec["runtime_paths"],
            "asb_use": spec["asb_use"],
        }
    controls = {}
    for name, spec in CONTROL_CANDIDATES.items():
        state, states = build_state(config, spec["configs"])
        controls[name] = {
            "interface": "standard_linux",
            "build_state": state,
            "configs": states,
            "runtime_path": spec["runtime_path"],
            "runtime_state": "unknown",
            "writable": "unknown",
            "value_type": spec["value_type"],
            "requests": spec["requests"],
            "baseline": "required_before_first_write",
            "readback_required": True,
            "restore": "exact_captured_value",
            "owner": spec["owner"],
            "readback": spec["readback"],
        }
    return {
        "schema": 2,
        "scope": "final build config only; runtime and physical-device validation are separate",
        "final_config_sha256": hashlib.sha256(raw).hexdigest(),
        "feature_set": feature_set,
        "capabilities": capabilities,
        "asb_contract": {
            "schema": 1,
            "ownership": "ASB owns userspace policy; kernel and vendor thermal stack retain their clamps",
            "control_plane": "standard_linux_interfaces",
            "custom_sysfs_abi": False,
            "runtime_discovery_required": True,
            "arbitrary_path_value_writer": False,
            "writers_added": [],
            "controls": controls,
            "external_owners": {
                "vpn_hide": "VPN Hide app and its matching built-in companion; ASB has no control channel",
                "network": "existing ASB network runtime; this report adds no second network writer",
            },
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
    print(f"ASB control candidates: {len(report['asb_contract']['controls'])} standard interfaces; writable=unknown")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
