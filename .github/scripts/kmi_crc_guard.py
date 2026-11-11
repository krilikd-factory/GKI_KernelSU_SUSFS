#!/usr/bin/env python3
"""KMI CRC guard: refuse a GKI build whose exported symbol CRCs drifted.

Stock vendor modules (vendor_boot / vendor_dlkm) were built against the GKI
KMI and carry the modversions CRC of every kernel symbol they import. If a
config or patch changes the layout of a type reachable from such a symbol
(e.g. CONFIG_FANOTIFY growing enum ucount_type), the CRC changes, the module
loader rejects the module with "disagrees about version of symbol", and the
device never gets past the boot logo. The build itself stays green, because
Kleaf's kmi_symbol_list_violations check only looks at symbol *names*.

This script compares the CRCs in the freshly built vmlinux.symvers against
the CRCs recorded in the tree's ABI representation (gki/aarch64/abi.stg),
restricted to the vendor symbol lists given (default: qcom + oplus, the
lists OnePlus / Snapdragon devices load against).

abi.stg also describes symbols exported by the GKI *modules* that live on
system_dlkm (bluetooth.ko, can-dev.ko, nfc.ko, libarc4.ko, ...). Those never
appear in vmlinux.symvers and are not replaced by an AnyKernel3 flash, so
they are not drift. With --sysmap (the vmlinux System.map) a symbol missing
from vmlinux.symvers only counts as an error when vmlinux defines it but no
longer exports it; a symbol vmlinux does not contain at all is reported as
"module-provided" and ignored. Without --sysmap missing exports only warn.

Exit 0 = every checked vmlinux symbol matches; exit 1 = CRC drift or a
vmlinux symbol that lost its export.
"""
import argparse
import os
import re
import sys


def parse_stg(path):
    """Return {symbol_name: crc_int} from a text-proto STG file."""
    crcs = {}
    name = None
    in_sym = False
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if s == "elf_symbol {":
                in_sym, name = True, None
                continue
            if not in_sym:
                continue
            if s == "}":
                in_sym = False
                continue
            if s.startswith("name: "):
                name = s[len("name: "):].strip().strip('"')
            elif s.startswith("crc: ") and name is not None:
                crcs[name] = int(s[len("crc: "):].strip(), 16)
    return crcs


def parse_symvers(path):
    crcs = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 2 and parts[0].startswith("0x"):
                crcs[parts[1]] = int(parts[0], 16)
    return crcs


def parse_sysmap(path):
    """Names of every symbol defined in vmlinux (System.map)."""
    names = set()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) >= 3:
                names.add(parts[2])
    return names


def parse_symbol_list(path):
    syms = set()
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#") or s.startswith("["):
                continue
            syms.add(s.split()[0])
    return syms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stg", required=True)
    ap.add_argument("--symvers", required=True)
    ap.add_argument("--lists", nargs="*", default=[],
                    help="vendor symbol list files; empty = every symbol in the STG")
    ap.add_argument("--sysmap", default="",
                    help="vmlinux System.map; tells vmlinux symbols from module ones")
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    stg = parse_stg(args.stg)
    built = parse_symvers(args.symvers)
    if not stg:
        print(f"ERROR: no CRCs parsed from {args.stg}", file=sys.stderr)
        return 2
    if not built:
        print(f"ERROR: no CRCs parsed from {args.symvers}", file=sys.stderr)
        return 2

    if args.lists:
        wanted = set()
        for lst in args.lists:
            wanted |= parse_symbol_list(lst)
        scope = ", ".join(os.path.basename(x) for x in args.lists)
    else:
        wanted = set(stg)
        scope = "full STG"
    # Only symbols the ABI actually records a CRC for can be compared.
    checked = sorted(s for s in wanted if s in stg)

    in_vmlinux = parse_sysmap(args.sysmap) if args.sysmap else None

    mismatched, missing, from_modules = [], [], []
    for sym in checked:
        if sym not in built:
            if in_vmlinux is not None and sym not in in_vmlinux:
                from_modules.append(sym)      # exported by a GKI module
            else:
                missing.append(sym)
        elif built[sym] != stg[sym]:
            mismatched.append((sym, stg[sym], built[sym]))

    lines = [f"KMI CRC guard ({scope}): {len(checked)} symbols checked, "
             f"{len(mismatched)} CRC mismatches, {len(missing)} missing vmlinux exports, "
             f"{len(from_modules)} provided by GKI modules (not checked)"]
    for sym, want, got in mismatched[:200]:
        lines.append(f"  CRC  {sym}: stock 0x{want:08x} != built 0x{got:08x}")
    if len(mismatched) > 200:
        lines.append(f"  ... and {len(mismatched) - 200} more mismatches")
    for sym in missing[:100]:
        where = "defined in vmlinux but no longer exported" if in_vmlinux is not None \
            else "not in vmlinux.symvers (no System.map to tell module symbols apart)"
        lines.append(f"  MISS {sym}: {where}")
    if len(missing) > 100:
        lines.append(f"  ... and {len(missing) - 100} more missing")
    out = "\n".join(lines)
    print(out)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            fh.write(out + "\n")
    if mismatched:
        return 1
    if missing and in_vmlinux is not None:
        return 1
    if missing:
        print("WARNING: missing exports not classified (no --sysmap); not failing on them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
