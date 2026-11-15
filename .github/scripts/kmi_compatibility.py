#!/usr/bin/env python3
"""Record OnePlus 15 build/flash scope without claiming runtime compatibility."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


def load_policy(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or data.get("allow_cross_family_flash") is not False:
        raise ValueError("schema=1 and allow_cross_family_flash=false are required")
    for key in ("device", "model", "stock_family", "stock_kernel"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise ValueError(f"policy field {key} must be a non-empty string")
    for key in ("allowed_candidate_families", "experimental_families", "field_test_order"):
        if not isinstance(data.get(key), list) or not data[key] or not all(isinstance(x, str) and x for x in data[key]):
            raise ValueError(f"policy field {key} must be a non-empty string array")
    if data["stock_family"] not in data["allowed_candidate_families"]:
        raise ValueError("stock family must be present in allowed candidate families")
    overlap = set(data["allowed_candidate_families"]) & set(data["experimental_families"])
    if overlap:
        raise ValueError(f"candidate and experimental families overlap: {sorted(overlap)}")
    return data


def evaluate(policy: dict, family: str, source: str) -> dict:
    if source not in ("aosp", "oneplus"):
        raise ValueError("actual source must be aosp or oneplus")
    if not re.fullmatch(r"android[0-9]+-[0-9]+\.[0-9]+", family):
        raise ValueError("malformed GKI family")
    if source == "oneplus" and family != policy["stock_family"]:
        raise ValueError("OnePlus phone source is fail-closed to the stock family")
    if family == policy["stock_family"]:
        classification = "stock_kmi_family"
    elif family in policy["allowed_candidate_families"]:
        classification = "allowed_same_family_candidate"
    elif family in policy["experimental_families"]:
        classification = "experimental_cross_family"
    else:
        classification = "unlisted_family"
    experimental = classification == "experimental_cross_family"
    listed = classification != "unlisted_family"
    return {
        "schema": 1,
        "device": policy["device"],
        "model": policy["model"],
        "family": family,
        "actual_source": source,
        "stock_family": policy["stock_family"],
        "stock_kernel": policy["stock_kernel"],
        "classification": classification,
        "build_scope": "lab_only" if experimental else ("same_kmi_candidate" if listed else "generic_source_build"),
        "oneplus15_daily_driver_status": "not_attested",
        "oneplus15_flash_compatibility": "lab_only_not_attested" if experimental else "not_attested_requires_field_validation",
        "allow_cross_family_flash": False,
        "runtime_evidence": "unknown",
        "field_test_order": policy["field_test_order"],
        "scope": "device policy for build selection; not physical boot, vendor-module or runtime proof",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", required=True, type=Path)
    ap.add_argument("--family", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()
    report = evaluate(load_policy(args.policy), args.family, args.source)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"OnePlus 15 KMI scope: {report['classification']}; {report['build_scope']}; runtime evidence unknown")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"KMI compatibility policy error: {exc}", file=sys.stderr)
        sys.exit(1)
