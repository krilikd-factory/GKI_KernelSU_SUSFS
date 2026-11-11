#!/usr/bin/env python3
"""Compare local kernel patch files with an upstream patch repository.

The audit is intentionally read-only. It produces a reviewable report and never
copies upstream patches into the build tree automatically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

PATCH_SUFFIXES = {".patch", ".diff"}
RISK_PATTERNS = {
    "thermal-limit": re.compile(r"thermal|trip[_ -]?temp|cooling[_ -]?device", re.I),
    "voltage-frequency": re.compile(r"undervolt|overvolt|voltage|opp[_ -]?table|cpufreq|devfreq", re.I),
    "scheduler-affinity": re.compile(r"irqbalance|smp_affinity|sched[_ -]?util|uclamp|schedutil", re.I),
    "filesystem-writeback": re.compile(r"commit[_ -]?age|dirty[_ -]?ratio|writeback|fsync", re.I),
    "network-congestion": re.compile(r"bbr|tcp_congestion|cubic|fq[_ -]?codel", re.I),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect(root: Path) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if not root.exists():
        return result
    for path in sorted(p for p in root.rglob("*") if p.is_file() and p.suffix in PATCH_SUFFIXES):
        text = path.read_text(errors="replace")
        result.append(
            {
                "path": path.relative_to(root).as_posix(),
                "basename": path.name,
                "sha256": sha256(path),
                "bytes": str(path.stat().st_size),
                "risk_tags": ",".join(name for name, pattern in RISK_PATTERNS.items() if pattern.search(text)),
            }
        )
    return result


def by_basename(items: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for item in items:
        grouped[item["basename"]].append(item)
    return grouped


def render_markdown(args: argparse.Namespace, local: list[dict[str, str]], upstream: list[dict[str, str]], metadata: dict) -> str:
    local_by_name = by_basename(local)
    upstream_by_name = by_basename(upstream)
    local_names = set(local_by_name)
    upstream_names = set(upstream_by_name)
    only_upstream = sorted(upstream_names - local_names)
    only_local = sorted(local_names - upstream_names)
    changed = sorted(
        name for name in local_names & upstream_names
        if {item["sha256"] for item in local_by_name[name]} != {item["sha256"] for item in upstream_by_name[name]}
    )
    risk = [item for item in upstream if item["risk_tags"]]

    lines = [
        "# KRILIKD upstream patch audit",
        "",
        "> Read-only report. No upstream patch was copied into the production build tree.",
        "",
        f"- generated from upstream commit: `{metadata.get('upstream_commit', 'unknown')}`",
        f"- local patch files: **{len(local)}**",
        f"- upstream patch files: **{len(upstream)}**",
        f"- new upstream basenames: **{len(only_upstream)}**",
        f"- local-only basenames: **{len(only_local)}**",
        f"- changed matching basenames: **{len(changed)}**",
        f"- upstream files with review-risk markers: **{len(risk)}**",
        "",
        "## Required review order",
        "",
        "1. Review changed and new patches against the target Android/GKI branch.",
        "2. Build the affected family with the normal dry-run gate and KMI CRC guard.",
        "3. Compare boot, camera, display, modem, idle drain, thermals and gaming telemetry.",
        "4. Only then promote a tested commit to `.github/known-good.json`.",
        "",
    ]

    def section(title: str, names: list[str], source: dict[str, list[dict[str, str]]]) -> None:
        lines.extend([f"## {title}", ""])
        if not names:
            lines.append("None detected.")
            lines.append("")
            return
        for name in names:
            entries = source.get(name, [])
            detail = "; ".join(
                f"`{entry['path']}` ({entry['sha256'][:12]})"
                + (f" [{entry['risk_tags']}]" if entry["risk_tags"] else "")
                for entry in entries
            )
            lines.append(f"- `{name}` — {detail}")
        lines.append("")

    section("New upstream patch basenames", only_upstream, upstream_by_name)
    section("Local-only patch basenames", only_local, local_by_name)
    section("Matching basenames with different content", changed, {**local_by_name, **upstream_by_name})
    section("Upstream patches requiring extra review", sorted({item["basename"] for item in risk}), upstream_by_name)
    lines.extend(
        [
            "## Source status",
            "",
            "```json",
            json.dumps(metadata, indent=2, sort_keys=True),
            "```",
            "",
            "## Decision",
            "",
            "This report is informational. The workflow must not auto-merge or auto-apply a patch merely because it exists upstream. A patch becomes eligible only after a successful target-family build and device validation.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-root", required=True, type=Path)
    parser.add_argument("--upstream-root", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--json-output", required=True, type=Path)
    args = parser.parse_args()

    metadata = json.loads(args.metadata.read_text())
    local = collect(args.local_root)
    upstream = collect(args.upstream_root)
    report = render_markdown(args, local, upstream, metadata)
    args.output.write_text(report + "\n")
    args.json_output.write_text(json.dumps({"metadata": metadata, "local": local, "upstream": upstream}, indent=2, sort_keys=True) + "\n")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
