#!/usr/bin/env python3
"""Turn update-summary.json into the body of the "Upstream review queue" issue.

The daily Update Check already knows what is newer than the boot-attested
known-good set, which new kernel_patches are only observed (never applied on
their own) and whether OnePlus has published kernel source for the phone's
firmware. That report lived in a workflow summary nobody opens. This writes it
into one GitHub issue: the issue is created when something waits, a comment is
added when the list changes (GitHub notifies the owner of both), and it is
closed when nothing waits any more.

usage: review_queue.py update-summary.json body.md
prints: state=open|close and signature=<16 hex> for the workflow.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def render(summary: dict) -> tuple[list[str], list[str]]:
    """(signature parts, markdown lines)."""
    sig: list[str] = []
    md: list[str] = []
    op = summary.get("oneplus") or {}
    if op.get("ready"):
        sig.append(f"oneplus:{op.get('tip', '')}")
        md += ["## 🟢 OnePlus kernel source is out for the phone's firmware", "",
               f"Branch `{op.get('branch')}`, kernel 6.12.{op.get('sublevel')}, tip `{(op.get('tip') or '')[:12]}`.",
               "Update Check starts a `kernel_source=oneplus` candidate for it once per OnePlus tip. "
               "Boot-test that candidate before promoting it.", ""]
    others = op.get("other_branches") or []
    if others:
        sig.append("opother:" + ",".join(others))
        md += ["## OnePlus branches outside the configured pattern", "",
               "If one of these is the phone's new firmware, update `oneplus_branch_match` in `.github/device.json`: "
               + ", ".join(f"`{b}`" for b in others), ""]
    rows: list[str] = []
    for key, comps in sorted((summary.get("sets") or {}).items()):
        for name, val in sorted(comps.items()):
            if isinstance(val, dict) and val.get("status") in ("newer", "unreachable", "unpinned"):
                st = val["status"]
                tip = (val.get("tip") or "")[:12]
                n = len(val.get("commits") or [])
                sig.append(f"{key}:{name}:{st}:{tip}")
                what = {"newer": f"{n or 'new'} commit(s) newer, tip `{tip}`" + (f" ({val['tag']})" if val.get("tag") else ""),
                        "unreachable": "upstream unreachable - check the URL/branch",
                        "unpinned": f"not recorded in known-good, tip `{tip}`"}[st]
                rows.append(f"| `{key}` | {name} | {what} |")
    if rows:
        md += ["## Components newer than the boot-attested known-good set", "",
               "| Known-good set | Component | State |", "|---|---|---|", *rows, "",
               "A full candidate is built for each new set of updates. Flash it, check boot, then promote it with "
               "*Mark build as known-good*.", ""]
    for key, comps in sorted((summary.get("sets") or {}).items()):
        observed = comps.get("new_patches_observed") or []
        candidates = comps.get("new_patches_candidates") or []
        if observed or candidates:
            sig += [f"obs:{p}" for p in observed] + [f"cand:{p}" for p in candidates]
            md += [f"## kernel_patches to review (`{key}`)", ""]
            if observed:
                md += ["Observed, never applied on their own: " + ", ".join(f"`{p}`" for p in observed)]
            if candidates:
                md += ["Reviewed candidates (need `patch_channel=candidate`): " + ", ".join(f"`{p}`" for p in candidates)]
            md += ["", "Add a scoped entry to `.github/patch-policy.json` to take one into the stable channel.", ""]
            break
    return sig, md


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    summary = json.loads(Path(sys.argv[1]).read_text())
    sig, md = render(summary)
    signature = hashlib.sha256("\n".join(sorted(sig)).encode()).hexdigest()[:16]
    Path(sys.argv[2]).write_text("\n".join(md + [f"<!-- review-queue-signature:{signature} -->", ""]))
    print("state=" + ("open" if sig else "close"))
    print("signature=" + signature)
    return 0


if __name__ == "__main__":
    sys.exit(main())
