#!/usr/bin/env bash
# Apply the reviewed Android in-tree ADIOS 3.3.0 patch atomically.
# Usage: adios_apply.sh <kernel/common> <adios.patch>
set -euo pipefail

COMMON="${1:?kernel/common directory}"
PATCH="$(readlink -f "${2:?patch file}")"
die() { echo "adios: $*" >&2; exit 1; }

[ -s "$PATCH" ] || die "patch is missing or empty: $PATCH"
cd "$COMMON"
[ -f block/Kconfig.iosched ] && [ -f block/Makefile ] && [ -f block/elevator.c ] \
  || die "$COMMON is not a kernel block tree"
grep -q '^VERSION = 6$' Makefile && grep -q '^PATCHLEVEL = 12$' Makefile \
  || die "ADIOS integration is wired for Linux 6.12 only"
[ ! -e block/adios.c ] || die "tree already carries ADIOS"
grep -q '/lib/modules' "$PATCH" && die "desktop host-module targets must not enter an Android in-tree patch"

# git apply is atomic: a failed check or apply leaves no partially patched block tree.
git apply --check "$PATCH"
git apply "$PATCH"

grep -q '^config MQ_IOSCHED_ADIOS$' block/Kconfig.iosched || die "Kconfig registration missing"
grep -q '^config MQ_IOSCHED_DEFAULT_ADIOS$' block/Kconfig.iosched || die "default-policy guard missing"
grep -q 'obj-$(CONFIG_MQ_IOSCHED_ADIOS).*adios.o' block/Makefile || die "in-tree object rule missing"
grep -q 'elevator_find_get("adios")' block/elevator.c || die "default-selection hook missing"
grep -q '#define ADIOS_VERSION "3.3.0"' block/adios.c || die "unexpected ADIOS source version"
! grep -q '/lib/modules' block/Makefile || die "desktop host-module targets survived"
echo "adios: ADIOS 3.3.0 applied; built-in selection remains runtime-controlled"
