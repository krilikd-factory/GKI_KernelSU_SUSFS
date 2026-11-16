#!/usr/bin/env python3
"""Emit a not-measured scheduler A/B plan; never synthesize device metrics."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

METRICS = {
    "frame_time_p95_ms": None,
    "frame_time_p99_ms": None,
    "jank_percent": None,
    "cpu_residency": None,
    "temperature_c": None,
    "battery_current_ma": None,
    "battery_drain_percent_per_hour": None,
    "network_latency_ms": None,
    "network_jitter_ms": None,
    "thermal_throttling_duration_s": None,
    "kernel_logs": None,
    "asb_counters": None,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", required=True)
    ap.add_argument("--sublevel", required=True)
    ap.add_argument("--source", required=True, choices=("aosp", "oneplus"))
    ap.add_argument("--feature-set", required=True)
    ap.add_argument("--source-provenance", type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()
    source_commit = "not_recorded"
    if args.source_provenance and args.source_provenance.is_file():
        provenance = json.loads(args.source_provenance.read_text(encoding="utf-8"))
        source_commit = provenance.get("commit", "not_recorded")
    capture_template = {
        "schema": 1,
        "kind": "asb_scheduler_capture",
        "status": "not_measured",
        "label": "",
        "kernel": {"release": "", "version": "", "source_commit": source_commit, "family": args.family},
        "bore": {"build_config": "unknown", "runtime": "unknown"},
        "adios": {"build_config": "unknown", "runtime": "unknown"},
        "identity": {"root": "", "susfs": "", "asb": "", "feature_set": args.feature_set},
        "workload": {"name": "", "duration_s": None, "screen": "unknown"},
        "thermal_state": {"start_skin_c": None, "start_cpu_c": None, "throttled_at_start": "unknown"},
        "metrics": METRICS,
    }
    report = {
        "schema": 1,
        "kind": "scheduler_ab_plan",
        "status": "not_measured",
        "build_inputs": {"family": args.family, "sublevel": args.sublevel, "actual_source": args.source,
                         "source_commit": source_commit, "feature_set": args.feature_set},
        "required_field_order": ["6.12.69 adios_off", "6.12.69 adios_on", "6.12.92 adios_off",
                                 "6.12.92 adios_on", "6.12.69 bore_off", "6.12.69 bore_on",
                                 "6.12.92 bore_off", "6.12.92 bore_on", "6.18 lab_only_after_same_family_results"],
        "comparison_rules": [
            "Keep root, SUSFS, ASB, workload and duration identical for each BORE or ADIOS A/B pair.",
            "For ADIOS compare runtime-selected adios with mq-deadline, kyber, bfq and none on the same UFS workload; built-in config alone is not an effect measurement.",
            "Use comparable starting thermal state; record throttling instead of hiding it.",
            "Null and not_measured stay unmeasured; CI does not create frame-time, drain or network values.",
            "Host validation is not physical OnePlus 15 validation.",
        ],
        "capture_template": capture_template,
    }
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("Scheduler A/B plan: not_measured; no field metrics generated")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"scheduler A/B template error: {exc}", file=sys.stderr)
        sys.exit(1)
