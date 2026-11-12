#!/usr/bin/env python3
"""Replace upstream AnyKernel3 promotional output with KRILIKD build information.

The installer logic remains upstream AnyKernel3. This pass changes only its banner,
kernel display name and human-readable ui_print blocks. Unknown upstream layouts
fail loudly instead of silently shipping another project's community promotion.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

PROMO_BEGIN = 'ui_print "WildKernels Telegram Channel:"'
PROMO_END = 'ui_print "Thank you for using Wild Kernels! - TheWildJames"'


def shell_text(value: str) -> str:
    """Escape a value placed inside an installer's double-quoted ui_print string."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")


def rebrand(root: Path, release: str, feature_set: str, source: str, page_size: str) -> None:
    script_path = root / "anykernel.sh"
    banner_path = root / "banner"
    text = script_path.read_text(encoding="utf-8")
    for marker in (PROMO_BEGIN, PROMO_END, "kernel.string=", ". tools/ak3-core.sh"):
        if marker not in text:
            raise ValueError(f"AnyKernel3 layout changed; required marker is absent: {marker}")

    text, count = re.subn(r"(?m)^kernel\.string=.*$", "kernel.string=KRILIKD TOP — evidence-first kernel build", text)
    if count != 1:
        raise ValueError("AnyKernel3 must contain exactly one kernel.string property")
    text, count = re.subn(
        r'(?m)^ui_print " " "  -> Wild Kernels Supported: \$ksu_supported"$',
        'ui_print " " "  -> KRILIKD GKI compatibility check: $ksu_supported"',
        text,
    )
    if count != 1:
        raise ValueError("AnyKernel3 GKI compatibility message was not found")

    start = text.index(PROMO_BEGIN)
    end = text.index(PROMO_END, start) + len(PROMO_END)
    replacement = "\n".join([
        'ui_print " "',
        f'ui_print "KRILIKD | {shell_text(release)}"',
        f'ui_print "Mode: TOP | Feature set: {shell_text(feature_set)}"',
        f'ui_print "Source: {shell_text(source)} | ARM64 pages: {shell_text(page_size)}"',
        'ui_print "Build verified only: validate boot and device functions after reboot."',
        'ui_print "Kernel patches and component pins are listed in the BuildInfo artifact."',
        'ui_print "AnyKernel3 packaging by osm0sis @ xda-developers"',
        'ui_print " "',
    ])
    text = text[:start] + replacement + text[end:]
    script_path.write_text(text, encoding="utf-8")
    banner_path.write_text(
        "\n"
        "==================================================\n"
        "  K R I L I K D   //   T O P\n"
        "  Evidence-first Android kernel build\n"
        "==================================================\n\n",
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--release", required=True)
    ap.add_argument("--feature-set", required=True)
    ap.add_argument("--source", default="aosp")
    ap.add_argument("--page-size", default="4k")
    args = ap.parse_args()
    rebrand(Path(args.root), args.release, args.feature_set, args.source, args.page_size)
    print(f"AnyKernel3 presentation: KRILIKD, {args.release}, {args.page_size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
