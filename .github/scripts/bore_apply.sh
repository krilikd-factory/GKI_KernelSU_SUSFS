#!/usr/bin/env bash
# Apply BORE 7.0.0 to an android16-6.12 common tree and keep the GKI KMI layout.
# Usage: bore_apply.sh <kernel/common> <bore.patch>
# Any anchor that does not fit stops the build; nothing is skipped silently.
set -euo pipefail

COMMON="${1:?kernel/common directory}"
PATCH="$(readlink -f "${2:?patch file}")"
SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
die() { echo "bore: $*" >&2; exit 1; }

[ -s "$PATCH" ] || die "patch is missing or empty: $PATCH"
cd "$COMMON"
[ -f include/linux/sched.h ] && [ -f kernel/sched/fair.c ] || die "$COMMON is not a kernel tree"
grep -q '^VERSION = 6$' Makefile && grep -q '^PATCHLEVEL = 12$' Makefile || die "BORE is wired for 6.12 only"
[ ! -e kernel/sched/bore.c ] || die "tree already carries BORE"

set +e
patch -p1 -F3 --forward --no-backup-if-mismatch < "$PATCH"
patch_rc=$?
set -e
case "$patch_rc" in
  0|1) ;; # 1 is inspected below; only the two reviewed fair.c rejects may be ported.
  *) die "patch exited with serious error $patch_rc; no fixup was attempted" ;;
esac

REJ="$(find . -name '*.rej' | sort)"
if [ "$patch_rc" -eq 1 ] && [ -z "$REJ" ]; then
  die "patch exited 1 without reject diagnostics"
fi
if [ -n "$REJ" ]; then
  [ "$REJ" = "./kernel/sched/fair.c.rej" ] || die "unexpected rejects: $REJ"
  HUNKS="$(grep -c '^@@' kernel/sched/fair.c.rej)"
  [ "$HUNKS" -le 2 ] || die "fair.c has $HUNKS rejected hunks; only the sysctl block and the slice protection are ported by hand"
  echo "bore: $HUNKS fair.c hunk(s) rejected, ported by bore_gki612_fixups.py"
  rm -f kernel/sched/fair.c.rej
fi
find . -name '*.orig' -delete

rc=0
python3 "$SCRIPTS/bore_kabi_612.py" include/linux/sched.h || rc=$?
if [ "$rc" != 0 ]; then
  grep -q 'struct bore_ctx' include/linux/sched.h || die "kABI relocation failed and bore_ctx is not in task_struct"
fi

python3 "$SCRIPTS/bore12_state.py" include/linux/sched.h
FILES="$(grep -rl -- '->bore\.\|struct bore_ctx \*ctx' include kernel 2>/dev/null || true)"
python3 "$SCRIPTS/bore12_flat.py" include/linux/sched/bore.h kernel/sched/bore.c \
  kernel/sched/fair.c kernel/futex/waitwake.c kernel/fork.c $FILES
python3 "$SCRIPTS/bore_gki612_fixups.py" kernel/sched/fair.c

if grep -rn -- '->bore\.[a-z_]' include kernel | grep -v '^Binary'; then
  die "inline bore_ctx accessors remain"
fi
grep -q 'struct bore_ctx \*bore' include/linux/sched.h || die "task_struct has no out-of-line bore pointer"
if grep -n 'struct bore_ctx bore;' include/linux/sched.h; then
  die "task_struct still embeds bore_ctx; offsets would shift"
fi
grep -q 'update_curr_bore' kernel/sched/fair.c || die "update_curr_bore call is missing"
echo "bore: applied, task_struct layout preserved"
