#!/usr/bin/env python3
"""Classify previously unhandled kernel_patches/common patches.

New upstream files are *observed*, never applied by default. A human-reviewed
entry in .github/patch-policy.json can move one to ``candidate`` (only a
``patch_channel=candidate`` build may apply it) or ``enabled`` (stable builds
may apply it). Every decision carries a content digest and target scope.

Existing named patches in composite actions remain owned by those actions.
The old patch-allowlist/header check and patch-denylist remain additional
gates; an allowlist entry alone never promotes a patch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

STATES = {"observed", "candidate", "enabled"}
CHANNELS = {"stable", "candidate"}


def read_list(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, why = line.partition("#")
        out[name.strip().removesuffix(".patch")] = why.strip()
    return out


def read_policy(path: Path) -> dict:
    if not path.is_file():
        return {"schema": 1, "patches": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != 1:
        raise ValueError("patch policy must be an object with schema=1")
    patches = data.get("patches")
    if not isinstance(patches, dict):
        raise ValueError("patch policy 'patches' must be an object")
    for name, entry in patches.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            raise ValueError("every patch policy entry must be an object")
        if entry.get("state", "observed") not in STATES:
            raise ValueError(f"{name}: invalid state {entry.get('state')!r}")
    return data


def header_risk(patch_text: str) -> str:
    """Return '' for macro/comment-only header edits, else the risky line."""
    in_header = False
    continued = False
    for line in patch_text.splitlines():
        if line.startswith("+++ "):
            in_header = line.rstrip().endswith(".h")
            continued = False
            continue
        if line.startswith(("--- ", "diff ", "index ", "@@")):
            continue
        if not in_header or not line or line[0] not in "+-":
            continued = False if line[:1] == " " else continued
            continue
        body = line[1:].strip()
        was_continued, continued = continued, body.endswith("\\")
        if not body or was_continued:
            continue
        if body.startswith(("#", "/*", "*", "//")):
            continue
        return body[:80]
    return ""


def _targets_match(entry: dict, family: str, device: str, soc: str) -> str:
    for key, actual in (("families", family), ("devices", device), ("soc", soc)):
        allowed = entry.get(key)
        if not isinstance(allowed, list) or not allowed or not all(isinstance(x, str) for x in allowed):
            return f"{key} must be a non-empty string array for candidate/enabled patches"
        if "*" not in allowed and actual not in allowed:
            return f"target mismatch: {key} does not include '{actual}'"
    return ""


def _provenance_error(entry: dict) -> str:
    provenance = entry.get("provenance")
    if not isinstance(provenance, dict):
        return "candidate/enabled requires provenance.source and provenance.license"
    source = provenance.get("source", "")
    license_name = provenance.get("license", "")
    if not isinstance(source, str) or not source.startswith(("https://", "http://")):
        return "provenance.source must be an upstream HTTP(S) URL"
    if not isinstance(license_name, str) or not license_name.strip():
        return "provenance.license is required"
    commit = provenance.get("commit", "")
    if commit and (not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit)):
        return "provenance.commit must be a full lowercase Git SHA when present"
    return ""


def inventory(kp_common: Path, actions_dir: Path, deny: dict[str, str],
              allow: dict[str, str], policy: dict, *, channel: str = "stable",
              family: str = "", device: str = "", soc: str = "") -> list[dict]:
    if channel not in CHANNELS:
        raise ValueError(f"unsupported patch channel: {channel}")
    used = "\n".join(p.read_text() for p in sorted(actions_dir.glob("*/action.yml")))
    records = []
    for patch in sorted(kp_common.glob("*.patch")):
        name = patch.stem
        if re.search(r"(?<![A-Za-z0-9_+.-])" + re.escape(name) + r"(?![A-Za-z0-9_+-])", used):
            continue  # a build step already owns this patch
        raw = patch.read_bytes()
        entry = policy.get("patches", {}).get(name, {})
        state = entry.get("state", "observed")
        reason = ""
        decision = "skip"
        if name in deny:
            state = "blocked"
            reason = "denylist: " + (deny[name] or "no reason given")
        elif name not in allow and (risk := header_risk(raw.decode(errors="replace"))):
            reason = f"edits a header beyond macros ('{risk}'); review, then add to patch-allowlist.txt"
        elif state in ("candidate", "enabled"):
            reason = _targets_match(entry, family, device, soc) or _provenance_error(entry)
            if not reason:
                if state == "enabled" or channel == "candidate":
                    decision = "apply"
                else:
                    reason = "candidate is dry-run inventory only in a stable build"
        else:
            state = "observed"
            reason = "new upstream patch: observed only; add a reviewed patch-policy.json entry"
        records.append({
            "name": name,
            "path": f"common/{patch.name}",
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
            "state": state,
            "channel": channel,
            "decision": decision,
            "reason": reason,
        })
    return records


def classify(kp_common: Path, actions_dir: Path, deny: dict[str, str], allow: dict[str, str],
             policy: dict | None = None, **target):
    """Compatibility iterator: VERDICT, name, state, reason, sha256."""
    for row in inventory(kp_common, actions_dir, deny, allow, policy or {"patches": {}}, **target):
        yield row["decision"].upper(), row["name"], row["state"], row["reason"], row["sha256"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patches", required=True, help="kernel_patches/common")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--channel", choices=sorted(CHANNELS), default="stable")
    ap.add_argument("--family", default="")
    ap.add_argument("--device", default="")
    ap.add_argument("--soc", default="")
    ap.add_argument("--format", choices=("tsv", "json"), default="tsv")
    ap.add_argument("--output", help="write the selected format to this file instead of stdout")
    a = ap.parse_args()
    root = Path(a.repo_root)
    deny = read_list(root / ".github/patch-denylist.txt")
    allow = read_list(root / ".github/patch-allowlist.txt")
    policy = read_policy(root / ".github/patch-policy.json")
    rows = inventory(Path(a.patches), root / ".github/actions", deny, allow, policy,
                     channel=a.channel, family=a.family, device=a.device, soc=a.soc)
    if a.format == "json":
        counts = {state: sum(r["state"] == state for r in rows)
                  for state in ("observed", "candidate", "enabled", "blocked")}
        text = json.dumps({
            "schema": 1,
            "channel": a.channel,
            "target": {"family": a.family, "device": a.device, "soc": a.soc},
            "counts": counts,
            "patches": rows,
        }, indent=2, sort_keys=True) + "\n"
    else:
        text = "".join(f"{r['decision'].upper()}\t{r['name']}\t{r['state']}\t{r['reason']}\t{r['sha256']}\n"
                       for r in rows)
    if a.output:
        Path(a.output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"patch inventory error: {exc}", file=sys.stderr)
        sys.exit(2)
