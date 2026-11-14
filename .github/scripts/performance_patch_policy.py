#!/usr/bin/env python3
"""Validate and export the reviewed main Performance patch policy.

The report is build evidence only. A stable/candidate decision or successful patch
application does not attest physical power, thermal or runtime behavior.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def load_policy(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1 or not isinstance(data.get("patches"), list):
        raise ValueError("schema 1 and a patches array are required")
    seen = set()
    for item in data["patches"]:
        name = item.get("name")
        if not isinstance(name, str) or not name or name in seen:
            raise ValueError(f"invalid or duplicate patch name: {name!r}")
        seen.add(name)
        if item.get("channel") not in ("stable", "candidate"):
            raise ValueError(f"{name}: channel must be stable or candidate")
        for field in ("mechanism", "power_review"):
            if not isinstance(item.get(field), str) or not item[field].strip():
                raise ValueError(f"{name}: missing {field}")
    if len(seen) != 19:
        raise ValueError(f"the reviewed main set must contain exactly 19 patches, got {len(seen)}")
    return data


def render(data: dict, channel: str, policy_path: Path, source_commit: str) -> dict:
    if channel not in ("stable", "candidate"):
        raise ValueError("channel must be stable or candidate")
    patches = []
    for item in data["patches"]:
        entry = dict(item)
        entry["decision"] = "apply" if channel == "candidate" or item["channel"] == "stable" else "candidate_only"
        patches.append(entry)
    return {
        "schema": 1,
        "channel": channel,
        "kernel_patches_commit": source_commit,
        "policy_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
        "evidence_boundary": "policy and patch success are not physical boot, power or thermal validation",
        "patches": patches,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--channel", required=True, choices=("stable", "candidate"))
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--format", choices=("json", "tsv"), default="json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data = load_policy(args.policy)
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        parser.error("--source-commit must be the exact 40-hex kernel_patches HEAD")
    report = render(data, args.channel, args.policy, args.source_commit)
    if args.format == "json":
        text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    else:
        text = "".join(f"{p['name']}\t{p['decision']}\t{p['channel']}\n" for p in report["patches"])
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
