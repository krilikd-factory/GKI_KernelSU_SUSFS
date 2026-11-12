#!/usr/bin/env python3
"""Find kernel_patches/common patches no build step knows about yet.

WildKernels/kernel_patches keeps adding patches to common/. The build steps
name the ones they apply; anything else used to be ignored until someone
edited the workflow. This script classifies every common/*.patch that no
action mentions so the Performance (TOP) step can try the new ones on its own:

  APPLY <name>          not denied, and safe by the header rule below
  SKIP  <name> <why>    denied, or needs a human look

Header rule: a patch may touch a header only with preprocessor lines,
comments and blank lines. Anything else in a .h (a struct member, an enum
value, a prototype) can change a type that an exported symbol reaches - that
moves modversions CRCs, and the stock vendor modules then refuse to load.
Such a patch is skipped until someone reviews it and lists it in
.github/patch-allowlist.txt.

"Known" = the patch file name (without .patch) appears in any
.github/actions/*/action.yml. Denylist: .github/patch-denylist.txt.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


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


def header_risk(patch_text: str) -> str:
    """'' when header edits are macro/comment only, else the first risky line."""
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


def classify(kp_common: Path, actions_dir: Path, deny: dict[str, str], allow: dict[str, str]):
    used = "\n".join(p.read_text() for p in sorted(actions_dir.glob("*/action.yml")))
    for patch in sorted(kp_common.glob("*.patch")):
        name = patch.stem
        if re.search(r"(?<![A-Za-z0-9_+.-])" + re.escape(name) + r"(?![A-Za-z0-9_+-])", used):
            continue  # a build step already handles it
        if name in deny:
            yield "SKIP", name, "denylist: " + (deny[name] or "no reason given")
            continue
        if name not in allow:
            risk = header_risk(patch.read_text(errors="replace"))
            if risk:
                yield "SKIP", name, f"edits a header beyond macros ('{risk}'); review, then add to patch-allowlist.txt"
                continue
        yield "APPLY", name, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--patches", required=True, help="kernel_patches/common")
    ap.add_argument("--repo-root", default=".")
    a = ap.parse_args()
    root = Path(a.repo_root)
    deny = read_list(root / ".github/patch-denylist.txt")
    allow = read_list(root / ".github/patch-allowlist.txt")
    for verdict, name, why in classify(Path(a.patches), root / ".github/actions", deny, allow):
        print(f"{verdict}\t{name}\t{why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
