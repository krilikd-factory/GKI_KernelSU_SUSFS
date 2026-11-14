#!/usr/bin/env bash
# Sourced by main.yml (resolve-sources / "Resolve latest component commits")
# right after NEXT_COMMIT is chosen and before the manager merge base is
# computed from it. Kept in its own file because GitHub caps a run: block that
# contains expressions at 21000 characters.
#
# Checks the KernelSU-Next commit against the SUSFS the android16-6.12 build
# will use (next_susfs_api_guard.py). An incompatible commit is replaced by
# the newest boot-attested known-good root that passes the same check, with a
# warning; with no such root the run stops here instead of ten minutes into
# the kernel compile. A check that cannot run (network) never blocks a build.
#
# Expects: SCHED_SUSFS, NEXT_COMMIT, KG_ROOT, KG_SUSFS, EFFECTIVE_OPL,
# KERNEL_SOURCE, IN_KVER, IN_ROOT, GITHUB_WORKSPACE. Updates NEXT_COMMIT.

NG_APPLIES=0
if [ "${SCHED_SUSFS:-}" = "true" ]; then
  case "${IN_KVER:-All}" in ""|All|6.12.x-android16) NG_APPLIES=1 ;; esac
  case "${IN_ROOT:-}" in ""|KernelSU-Next|All*) ;; *) NG_APPLIES=0 ;; esac
fi
if [ "$NG_APPLIES" = 1 ]; then
  NG_OUT="$(python3 -I "$GITHUB_WORKSPACE/.github/scripts/next_susfs_api_guard.py" \
    --want "$NEXT_COMMIT" \
    --susfs-commit "${KG_SUSFS:-}" \
    --opl "${EFFECTIVE_OPL:-}" \
    --kernel-source "${KERNEL_SOURCE:-aosp}" \
    --known-good-file "$GITHUB_WORKSPACE/.github/known-good.json" \
    --compat-script "$GITHUB_WORKSPACE/.github/scripts/ksu_susfs_api_compat.py")" || true
  ng_get() { printf '%s\n' "$NG_OUT" | sed -n "s/^$1=//p" | head -n1; }
  NG_RESULT="$(ng_get result)"
  NG_COMMIT="$(ng_get commit)"
  NG_MISSING="$(ng_get missing)"
  NG_SUSFS="$(ng_get susfs)"
  case "$NG_RESULT" in
    compatible)
      echo "[+] KernelSU-Next ${NEXT_COMMIT:0:12} matches SUSFS ${NG_SUSFS:0:12}"
      ;;
    known_good)
      echo "::warning::KernelSU-Next ${NEXT_COMMIT:0:12} calls SUSFS functions that SUSFS ${NG_SUSFS:0:12} does not provide (${NG_MISSING}). Building the boot-attested known-good KernelSU-Next ${NG_COMMIT:0:12} ($(ng_get known_good_key)) instead."
      {
        echo "### KernelSU-Next / SUSFS API"
        echo "Selected \`${NEXT_COMMIT:0:12}\` calls \`${NG_MISSING}\`, which SUSFS \`${NG_SUSFS:0:12}\` does not provide."
        echo "Built with known-good \`${NG_COMMIT:0:12}\` instead."
      } >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
      NEXT_COMMIT="$NG_COMMIT"
      ;;
    unverified|"")
      echo "::warning::KernelSU-Next/SUSFS API check could not run (${NG_RESULT:+$(ng_get reason)}${NG_RESULT:-no result}); building ${NEXT_COMMIT:0:12} unchecked."
      ;;
    *)
      echo "::error::KernelSU-Next ${NEXT_COMMIT:0:12} calls SUSFS functions that SUSFS ${NG_SUSFS:0:12} does not provide: ${NG_MISSING:-unknown}. No known-good KernelSU-Next root passes the same check (tried: $(ng_get tried)). Wait for pershoot/KernelSU-Next dev-susfs to match SUSFS ${NG_SUSFS:0:12}, or build with commit_mode=known-good."
      exit 1
      ;;
  esac
fi
