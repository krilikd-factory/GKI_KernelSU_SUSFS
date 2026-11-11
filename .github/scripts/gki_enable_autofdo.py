#!/usr/bin/env python3
"""Build //common:kernel_aarch64 with Google's GKI AutoFDO profile.

android16-6.12 ships gki/aarch64/afdo/kernel.afdo (collected on Pixel; Google
reports +4% cold app launch, +12..22% binder, +9% mmap syscall), but wires it
only into the separate kernel_aarch64_autofdo target. This adds the same
clang_autofdo_profile attribute to the kernel_aarch64 target the workflow
builds. AutoFDO only changes code layout/inlining, not type definitions, so
modversions CRCs are unaffected (the KMI CRC guard re-checks that anyway).

Only acts when:
  * the profile file exists, and
  * BUILD.bazel already uses clang_autofdo_profile somewhere (so this Kleaf
    version supports the attribute), and
  * kernel_aarch64 does not set it yet.
Run from kernel/common. Exit 0 in every "not applicable" case.
"""
import os
import re
import sys

PROFILE = "gki/aarch64/afdo/kernel.afdo"


def main():
    if not os.path.isfile(PROFILE):
        print(f"AutoFDO: {PROFILE} not in this tree, skipped")
        return 0
    s = open("BUILD.bazel").read()
    if "clang_autofdo_profile" not in s:
        print("AutoFDO: this Kleaf tree does not use clang_autofdo_profile, skipped")
        return 0
    m = re.search(r'\n(common_kernel|kernel_build)\(\n    name = "kernel_aarch64",\n', s)
    if not m:
        print("AutoFDO: kernel_aarch64 target not found, skipped")
        return 0
    end = s.find("\n)\n", m.end())
    if end < 0:
        print("AutoFDO: could not delimit kernel_aarch64, skipped")
        return 0
    block = s[m.start():end]
    if "clang_autofdo_profile" in block:
        print("AutoFDO: kernel_aarch64 already uses a profile")
        return 0
    anchor = '    name = "kernel_aarch64",\n'
    i = s.index(anchor, m.start()) + len(anchor)
    s = s[:i] + f'    clang_autofdo_profile = ":{PROFILE}",\n' + s[i:]
    open("BUILD.bazel", "w").write(s)
    print(f"AutoFDO: kernel_aarch64 now built with {PROFILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
