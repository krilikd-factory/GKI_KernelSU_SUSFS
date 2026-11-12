#!/usr/bin/env python3
"""Create and validate privacy-bounded manual device-validation attestations.

A passed checklist is an explicit user attestation, not automated proof. It is
required before a successful *stable* build can become known-good. Candidate
builds remain ineligible until their reviewed patches are enabled and rebuilt.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

CHECKS = (
    "boot", "vendor_modules", "display_touch", "wifi", "cellular",
    "camera", "audio", "storage", "thermal", "asb",
)
FORBIDDEN_KEYS = re.compile(r"(^|_)(serial|imei|imsi|account|email|ip_address|mac_address)($|_)", re.I)


def pins_entry(path: Path) -> tuple[str, dict]:
    outer = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(outer, dict) or len(outer) != 1:
        raise ValueError(f"{path}: expected exactly one known-good candidate entry")
    key, value = next(iter(outer.items()))
    if not isinstance(value, dict) or not value.get("file"):
        raise ValueError(f"{path}: candidate has no build file identity")
    return key, value


def template(pins_path: Path) -> dict:
    _, pins = pins_entry(pins_path)
    run = pins.get("from_run", "").rstrip("/").rsplit("/", 1)[-1]
    return {
        "schema": 1,
        "run_id": run,
        "build": pins["file"],
        "device": "CPH2745",
        "kernel_release": "fill-from-uname-r",
        "checks": {name: "not_run" for name in CHECKS},
        "tested_at": "YYYY-MM-DDTHH:MM:SSZ",
        "evidence_ref": "optional issue/log URL; do not include serial, IMEI, account or network identifiers",
        "attestation": "manual_device_checks_not_independent_proof",
    }


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def validate_one(report: dict, pins_path: Path) -> dict:
    _, pins = pins_entry(pins_path)
    if not isinstance(report, dict) or report.get("schema") != 1:
        raise ValueError("validation report must be an object with schema=1")
    if any(FORBIDDEN_KEYS.search(str(key)) for key in _walk_keys(report)):
        raise ValueError("validation report contains a forbidden identifier field")
    expected_run = pins.get("from_run", "").rstrip("/").rsplit("/", 1)[-1]
    if str(report.get("run_id")) != expected_run or report.get("build") != pins.get("file"):
        raise ValueError(f"validation report does not match {pins['file']} and run {expected_run}")
    if report.get("device") != "CPH2745":
        raise ValueError("validation report targets a different device")
    release = report.get("kernel_release", "")
    if not isinstance(release, str) or not release.strip() or release == "fill-from-uname-r":
        raise ValueError("kernel_release must contain the tested uname -r value")
    checks = report.get("checks")
    if not isinstance(checks, dict):
        raise ValueError("checks must be an object")
    incomplete = [name for name in CHECKS if checks.get(name) != "passed"]
    if incomplete:
        raise ValueError("device checks not passed: " + ", ".join(incomplete))
    if set(checks) - set(CHECKS):
        raise ValueError("unknown device check keys")
    tested_at = report.get("tested_at", "")
    if not isinstance(tested_at, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", tested_at):
        raise ValueError("tested_at must be an explicit UTC timestamp")
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
    return {
        "status": "attested_passed",
        "sha256": hashlib.sha256(canonical).hexdigest(),
        "tested_at": tested_at,
        "kernel_release": release,
        "scope": "manual device attestation; not independent automated proof",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)
    tpl = sub.add_parser("template")
    tpl.add_argument("--pins", required=True)
    tpl.add_argument("--output", required=True)
    val = sub.add_parser("validate")
    val.add_argument("--pins", action="append", required=True)
    val.add_argument("--data", required=True, help="JSON object or array of completed reports")
    val.add_argument("--output", required=True)
    a = ap.parse_args()
    if a.command == "template":
        result = template(Path(a.pins))
    else:
        if len(a.data) > 262144:
            raise ValueError("validation JSON is too large")
        decoded = json.loads(a.data)
        reports = decoded if isinstance(decoded, list) else [decoded]
        result = {}
        for pin in a.pins:
            _, entry = pins_entry(Path(pin))
            matches = [r for r in reports if isinstance(r, dict) and r.get("build") == entry["file"]]
            if len(matches) != 1:
                raise ValueError(f"need exactly one validation report for {entry['file']}")
            result[entry["file"]] = validate_one(matches[0], Path(pin))
    Path(a.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"device evidence error: {exc}", file=sys.stderr)
        sys.exit(1)
