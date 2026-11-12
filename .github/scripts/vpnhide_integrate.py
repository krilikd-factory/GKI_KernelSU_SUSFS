#!/usr/bin/env python3
"""Integrate vpnhide's in-tree backend (CONFIG_VPNHIDE) into a GKI kernel tree.

Drives vpnhide's own builtin/scripts/integrate.py (exact-anchor, hash-checked
apply) and adds one fallback: SUSFS and similar layers also patch fs/namei.c,
fs/readdir.c and fs/stat.c, so the optional *filesystem* concealment call
sites may not find their anchors. In that case the integration is retried
without the fs/ call sites - the network concealment (ioctl, netlink dumps,
/proc/net routes, SO_BINDTODEVICE), which is what VPN detection actually
uses, stays complete - and CONFIG_VPNHIDE_FS_HIDING must then be n.

Exit 0 and print one of:  RESULT=full   RESULT=net-only
Exit 1 when even the network-only integration does not fit (tree untouched).
"""
import argparse
import shutil
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vpnhide", required=True, help="vpnhide checkout")
    ap.add_argument("--kernel", required=True, help="kernel/common")
    ap.add_argument("--kmi", required=True, help="e.g. android16-6.12")
    ap.add_argument("--output", required=True, help="new directory for review bundles")
    a = ap.parse_args()

    scripts = Path(a.vpnhide, "builtin", "scripts").resolve()
    sys.path.insert(0, str(scripts))
    import integrate as vh  # noqa: E402  (vpnhide's integrator)

    if a.kmi not in vh.EDITS:
        print(f"vpnhide: KMI {a.kmi} not supported by this vpnhide version", file=sys.stderr)
        return 1

    out = Path(a.output)
    try:
        vh.integrate(Path(a.kernel), a.kmi, out / "full", apply=True)
        print("RESULT=full")
        return 0
    except Exception as err:  # anchors not unique / not found: tree left untouched
        print(f"vpnhide: full integration does not fit this tree: {err}", file=sys.stderr)

    edits = vh.EDITS[a.kmi]
    for name in [n for n in edits if n.startswith("fs/")]:
        del edits[name]
    try:
        vh.integrate(Path(a.kernel), a.kmi, out / "net-only", apply=True)
        print("RESULT=net-only")
        return 0
    except Exception as err:
        print(f"vpnhide: network-only integration does not fit either: {err}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(scripts / "__pycache__", ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
