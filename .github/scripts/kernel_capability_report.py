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
import sys
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
    "network.cake": {
        "configs": ["CONFIG_NET_SCH_CAKE"],
        "runtime_paths": ["/sys/module/sch_cake"],
        "asb_use": "ASB may select CAKE only after a real tc qdisc operation succeeds",
    },
    "network.ip_sets": {
        "configs": [
            "CONFIG_IP_SET", "CONFIG_IP_SET_BITMAP_IP", "CONFIG_IP_SET_BITMAP_IPMAC",
            "CONFIG_IP_SET_BITMAP_PORT", "CONFIG_IP_SET_HASH_IP", "CONFIG_IP_SET_HASH_IPMARK",
            "CONFIG_IP_SET_HASH_IPPORT", "CONFIG_IP_SET_HASH_IPPORTIP",
            "CONFIG_IP_SET_HASH_IPPORTNET", "CONFIG_IP_SET_HASH_IPMAC", "CONFIG_IP_SET_HASH_MAC",
            "CONFIG_IP_SET_HASH_NETPORTNET", "CONFIG_IP_SET_HASH_NET", "CONFIG_IP_SET_HASH_NETNET",
            "CONFIG_IP_SET_HASH_NETPORT", "CONFIG_IP_SET_HASH_NETIFACE", "CONFIG_IP_SET_LIST_SET",
            "CONFIG_NETFILTER_XT_SET",
        ],
        "runtime_paths": ["/sys/module/ip_set"],
        "asb_use": "netfilter set availability; actual ipset create/add commands still require runtime validation",
    },
    "app.rekernel": {
        "configs": ["CONFIG_REKERNEL", "CONFIG_REKERNEL_NETWORK"],
        "runtime_paths": [],
        "asb_use": "external freezer integration through Re-Kernel Generic Netlink; ASB is not a second owner",
    },
    "power.wakeup_blocker": {
        "configs": ["CONFIG_ASB_WAKEUP_BLOCKER"],
        "runtime_paths": ["/sys/kernel/asb_wakeup/blocklist", "/sys/kernel/asb_wakeup/blocked_events", "/sys/kernel/asb_wakeup/abi_version", "/sys/kernel/asb_wakeup/rule_stats"],
        "asb_use": "bounded exact-name root policy; ABI 2 adds read-only per-rule counters; observation uses standard /sys/class/wakeup deltas",
    },
    "charging.sdp_900ma": {
        "configs": ["CONFIG_OPLUS_SDP_900MA"],
        "runtime_paths": ["/proc/sdp900ma"],
        "asb_use": "read-only status of the early-boot SDP input-current edit; ASB must not write charger limits itself",
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
        "configs": ["CONFIG_DEBUG_FS", "CONFIG_SCHED_DEBUG"],
        "runtime_path": "/sys/kernel/debug/sched/nr_migrate",
        # Android does not mount debugfs on user builds. ASB (dev49+) mounts it on a
        # private root-only directory for the one read or write and unmounts it again;
        # the value stays in the kernel. Nothing is left mounted.
        "runtime_access": "asb_private_debugfs_mount_per_access",
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
    "kernel_sched_stats": {
        "configs": ["CONFIG_SCHEDSTATS"],
        "runtime_path": "/proc/sys/kernel/sched_schedstats",
        "value_type": "boolean_0_1",
        "requests": {
            "stock": "restore the exact value captured before ASB's first write",
            "off": ["0"],
        },
        "owner": "ASB kernel-controls helper; legacy profile and reinforce writes use the same owner",
        "readback": "exact 0/1 readback is required",
    },
    "kernel_sched_autogroup": {
        "configs": ["CONFIG_SCHED_AUTOGROUP"],
        "runtime_path": "/proc/sys/kernel/sched_autogroup_enabled",
        # Deliberately not enabled by the OnePlus 15 policy: CONFIG_SCHED_AUTOGROUP adds a
        # member to struct signal_struct, which vendor modules are compiled against (KMI).
        "build_policy": "not_enabled_kmi_signal_struct",
        "value_type": "boolean_0_1",
        "requests": {
            "stock": "restore the exact value captured before ASB's first write",
            "off": ["0"],
            "on": ["1"],
        },
        "owner": "ASB kernel-controls helper; profile and reinforce paths do not write directly",
        "readback": "exact 0/1 readback is required",
    },
    "kernel_sched_child_runs_first": {
        "configs": [],
        "runtime_path": "/proc/sys/kernel/sched_child_runs_first",
        "value_type": "boolean_0_1",
        "requests": {
            "stock": "restore the exact value captured before ASB's first write",
            "off": ["0"],
            "on": ["1"],
        },
        "owner": "ASB kernel-controls helper; profile and reinforce paths do not write directly",
        "readback": "exact 0/1 readback is required",
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
            "runtime_access": spec.get("runtime_access", "direct"),
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
        if "build_policy" in spec:
            controls[name]["build_policy"] = spec["build_policy"]
    return {
        "schema": 2,
        "scope": "final build config only; runtime and physical-device validation are separate",
        "final_config_sha256": hashlib.sha256(raw).hexdigest(),
        "feature_set": feature_set,
        "capabilities": capabilities,
        "asb_contract": {
            "schema": 1,
            "ownership": "ASB owns userspace policy; kernel and vendor thermal stack retain their clamps",
            "control_plane": "standard_linux_interfaces" + ("+bounded_exact_name_sysfs" if config.get("CONFIG_ASB_WAKEUP_BLOCKER") in ("y", "m") else ""),
            "custom_sysfs_abi": config.get("CONFIG_ASB_WAKEUP_BLOCKER") in ("y", "m"),
            "runtime_discovery_required": True,
            "arbitrary_path_value_writer": False,
            "writers_added": ["/sys/kernel/asb_wakeup/blocklist"] if config.get("CONFIG_ASB_WAKEUP_BLOCKER") in ("y", "m") else [],
            "bounded_interfaces": {
                "wakeup_blocklist": {
                    "abi_version": 2,
                    "read_only_stats": ["/sys/kernel/asb_wakeup/blocked_events", "/sys/kernel/asb_wakeup/rule_stats"],
                    "observation": "ASB on-demand /sys/class/wakeup counter deltas; no kernel idle detector or polling worker",
                    "commands": ["add NAME", "del NAME", "clear"],
                    "default": "empty",
                    "max_rules": 16,
                    "max_name_bytes": 63,
                    "patterns": False,
                    "arbitrary_paths": False,
                }
            } if config.get("CONFIG_ASB_WAKEUP_BLOCKER") in ("y", "m") else {},
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
    ap.add_argument("--require", action="append", default=[], metavar="CAPABILITY",
                    help="fail when this capability is absent from the final resolved config")
    a = ap.parse_args()
    report = make_report(Path(a.config), a.feature_set)
    Path(a.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    available = sum(c["build_state"] == "available_in_config" for c in report["capabilities"].values())
    print(f"ASB capability report: {available}/{len(report['capabilities'])} groups present in final config; runtime=unknown")
    print(f"ASB control candidates: {len(report['asb_contract']['controls'])} standard interfaces; writable=unknown")
    failed = []
    for name in a.require:
        capability = report["capabilities"].get(name)
        if capability is None:
            print(f"required capability is not declared: {name}", file=sys.stderr)
            failed.append(name)
        elif capability["build_state"] != "available_in_config":
            states = ", ".join(f"{k}={v}" for k, v in capability["configs"].items())
            print(f"required capability missing from FINAL config: {name}: {states}", file=sys.stderr)
            failed.append(name)
    if failed:
        print("requested config lines are not evidence: refusing a build without the selected final capabilities", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
