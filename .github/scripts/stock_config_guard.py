#!/usr/bin/env python3
"""Fail a GKI build that silently lost a stock kernel option.

Feature steps only *add* options to gki_defconfig, but Kconfig can still drop
a stock option behind our back when an added option breaks one of its
dependencies. The case that motivated this: on android16-6.12 any LTO makes
`CONFIG_RUST` unselectable while `CONFIG_DEBUG_INFO_BTF=y`
(`depends on !DEBUG_INFO_BTF || (... && !LTO)`), which silently removes
CONFIG_ASHMEM_RUST (built-in ashmem) and the Rust binder - a kernel that
compiles fine and then cannot run Android.

Rule: every option the *stock* gki_defconfig sets to `y` must still be `y` in
the final .config; every stock `m` must still be `y` or `m`. Options whose
value is a string/number are not compared (features legitimately change
CONFIG_LSM, CONFIG_LOCALVERSION, ...). Page-size options are skipped because
the page_size input switches them on purpose.

Usage: stock_config_guard.py --stock <stock gki_defconfig> --final <.config>
Exit 0 = nothing lost, 1 = stock options lost (listed).
"""
import argparse
import re
import sys

SKIP = re.compile(r"^CONFIG_ARM64_(4K|16K|64K)_PAGES$|^CONFIG_ARM64_PAGE_SHIFT$")


def parse(path):
    vals = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            m = re.match(r"^(CONFIG_[A-Za-z0-9_]+)=(.*)$", line)
            if m:
                vals[m.group(1)] = m.group(2)
                continue
            m = re.match(r"^# (CONFIG_[A-Za-z0-9_]+) is not set$", line)
            if m:
                vals[m.group(1)] = "n"
    return vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stock", required=True)
    ap.add_argument("--final", required=True)
    ap.add_argument("--report", default="")
    a = ap.parse_args()

    stock = parse(a.stock)
    final = parse(a.final)
    lost = []
    for key, want in sorted(stock.items()):
        if SKIP.match(key) or want not in ("y", "m"):
            continue
        got = final.get(key, "n")
        if want == "y" and got != "y":
            lost.append((key, want, got))
        elif want == "m" and got not in ("y", "m"):
            lost.append((key, want, got))

    checked = sum(1 for k, v in stock.items() if v in ("y", "m") and not SKIP.match(k))
    out = [f"Stock config guard: {checked} stock y/m options checked, {len(lost)} lost"]
    for key, want, got in lost:
        out.append(f"  LOST {key}: stock {want} -> final {'unset' if got == 'n' else got}")
    text = "\n".join(out)
    print(text)
    if a.report:
        with open(a.report, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    return 1 if lost else 0


if __name__ == "__main__":
    sys.exit(main())
