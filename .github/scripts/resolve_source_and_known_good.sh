#!/usr/bin/env bash
# Sourced by main.yml (resolve-sources / "Resolve latest component commits").
# Lives in its own file because GitHub caps a run: block that contains
# expressions at 21000 characters. Expects in the environment: COMMIT_MODE,
# GITHUB_OUTPUT and IN_KERNEL_SOURCE, IN_OS_PATCH_LEVEL, IN_KVER,
# IN_ONEPLUS_BRANCH, IN_ROOT (the dispatch inputs). Leaves for the caller:
# KERNEL_SOURCE, EFFECTIVE_OPL, OEM_REPO, OEM_BRANCH, OEM_TIP, KG_KEY and the
# KG_* known-good pins.

# ---- Kernel source selection (requested auto | aosp | oneplus) ----
# Explicit oneplus is fail-closed. Auto uses OnePlus only after the remote branch,
# Makefile identity, stock minima, patch-level matrix and Kleaf files all check out;
# otherwise it selects AOSP and exports a stable, non-secret fallback reason.
oem_retry() { local i; for i in 1 2 3 4 5; do "$@" && return 0; sleep $((i*3)); done; return 1; }
REQUESTED_KERNEL_SOURCE="${IN_KERNEL_SOURCE:-aosp}"
case "$REQUESTED_KERNEL_SOURCE" in auto|aosp|oneplus) ;; *) echo "::error::invalid kernel_source '$REQUESTED_KERNEL_SOURCE'"; exit 1 ;; esac
KERNEL_SOURCE="$REQUESTED_KERNEL_SOURCE"
KERNEL_SOURCE_FALLBACK_REASON="none"
EFFECTIVE_OPL="${IN_OS_PATCH_LEVEL:-}"
OEM_REPO=""; OEM_BRANCH=""; OEM_TIP=""; OEM_SUBLEVEL=""; OEM_FAILURE_REASON=""; OEM_TMP=""

oem_reject() {
  OEM_FAILURE_REASON="$1"
  echo "::notice::OnePlus source preflight rejected: $OEM_FAILURE_REASON"
  [ -z "$OEM_TMP" ] || rm -rf "$OEM_TMP"
  OEM_TMP=""
  return 1
}

resolve_oneplus_source() {
  [ "${IN_KVER:-}" = "6.12.x-android16" ] || { oem_reject family_not_oneplus15_stock; return 1; }
  DEV=.github/device.json
  OEM_REPO="$(jq -r .oneplus_kernel_repo "$DEV")"
  MATCH="$(jq -r .oneplus_branch_match "$DEV")"
  STOCK_SUB="$(jq -r .stock_sublevel "$DEV")"
  STOCK_OPL="$(jq -r .stock_patch_level "$DEV")"
  case "$OEM_REPO" in https://github.com/OnePlusOSS/*) ;; *) oem_reject oneplus_repository_invalid; return 1 ;; esac
  [ -n "$MATCH" ] && [ "$MATCH" != null ] || { oem_reject oneplus_branch_pattern_missing; return 1; }
  case "$STOCK_SUB" in ''|*[!0-9]*) oem_reject device_stock_sublevel_invalid; return 1 ;; esac
  [[ "$STOCK_OPL" =~ ^[0-9]{4}-[0-9]{2}$ ]] || { oem_reject device_stock_patch_level_invalid; return 1; }
  OEM_BRANCH="${IN_ONEPLUS_BRANCH:-}"
  if [ -z "$OEM_BRANCH" ]; then
    OEM_BRANCH="$(oem_retry git ls-remote --heads "$OEM_REPO" 2>/dev/null | sed 's#.*refs/heads/##' \
                  | while read -r b; do if [[ "$b" == $MATCH ]]; then echo "$b"; fi; done | sort -V | tail -n1)"
  fi
  [[ "$OEM_BRANCH" == $MATCH ]] || { oem_reject oneplus_branch_unavailable; return 1; }
  OEM_TMP="$(mktemp -d)"
  git -C "$OEM_TMP" init -q || { oem_reject oneplus_temporary_repository_failed; return 1; }
  oem_retry git -C "$OEM_TMP" fetch -q --depth=1 --filter=blob:none "$OEM_REPO" "refs/heads/$OEM_BRANCH" || {
    oem_reject oneplus_fetch_failed; return 1; }
  OEM_TIP="$(git -C "$OEM_TMP" rev-parse FETCH_HEAD 2>/dev/null)" || { oem_reject oneplus_tip_unavailable; return 1; }
  [[ "$OEM_TIP" =~ ^[0-9a-f]{40}$ ]] || { oem_reject oneplus_tip_invalid; return 1; }
  OEM_VERSION="$(git -C "$OEM_TMP" show FETCH_HEAD:Makefile 2>/dev/null | sed -n 's/^VERSION = //p' | head -n1)"
  OEM_PATCHLEVEL="$(git -C "$OEM_TMP" show FETCH_HEAD:Makefile 2>/dev/null | sed -n 's/^PATCHLEVEL = //p' | head -n1)"
  OEM_SUBLEVEL="$(git -C "$OEM_TMP" show FETCH_HEAD:Makefile 2>/dev/null | sed -n 's/^SUBLEVEL = //p' | head -n1)"
  [ "$OEM_VERSION" = 6 ] && [ "$OEM_PATCHLEVEL" = 12 ] || { oem_reject oneplus_makefile_family_mismatch; return 1; }
  case "$OEM_SUBLEVEL" in ''|*[!0-9]*) oem_reject oneplus_sublevel_missing; return 1 ;; esac
  [ "$OEM_SUBLEVEL" -ge "$STOCK_SUB" ] || { oem_reject oneplus_source_older_than_stock; return 1; }
  for _kleaf in BUILD.bazel build.config.gki.aarch64 arch/arm64/configs/gki_defconfig; do
    git -C "$OEM_TMP" cat-file -e "FETCH_HEAD:$_kleaf" 2>/dev/null || { oem_reject oneplus_kleaf_files_incomplete; return 1; }
  done
  OEM_MSG="$(git -C "$OEM_TMP" log -1 --format=%B FETCH_HEAD)"
  OEM_OPL="$(printf '%s' "$OEM_MSG" | grep -oE 'android16-6\.12-[0-9]{4}-[0-9]{2}' | head -n1 | sed 's/^android16-6\.12-//')"
  if [ -z "$OEM_OPL" ]; then
    OEM_OPL="$(jq -r --arg s "$OEM_SUBLEVEL" '[.include[] | select(.sublevel == $s) | .date] | first // empty' .github/config/android16-6.12.json)"
  fi
  if [ -n "$OEM_OPL" ] && [[ "$OEM_OPL" < "$STOCK_OPL" ]]; then
    oem_reject oneplus_patch_level_older_than_stock; return 1
  fi
  if [ -z "$OEM_OPL" ] || ! jq -e --arg d "$OEM_OPL" 'any(.include[]; .date == $d)' .github/config/android16-6.12.json >/dev/null; then
    oem_reject oneplus_kleaf_base_missing; return 1
  fi
  rm -rf "$OEM_TMP"; OEM_TMP=""
  EFFECTIVE_OPL="$OEM_OPL"
  echo "[+] OnePlus source: $OEM_REPO @ $OEM_BRANCH (${OEM_TIP:0:12}), kernel 6.12.${OEM_SUBLEVEL}, base ${OEM_OPL}"
  return 0
}

if [ "$REQUESTED_KERNEL_SOURCE" = auto ] && [ "${IN_KVER:-}" != "6.12.x-android16" ]; then
  KERNEL_SOURCE="aosp"
  KERNEL_SOURCE_FALLBACK_REASON="family_not_oneplus15_stock"
elif [ "$REQUESTED_KERNEL_SOURCE" != aosp ]; then
  if resolve_oneplus_source; then
    KERNEL_SOURCE="oneplus"
  else
    if [ "$REQUESTED_KERNEL_SOURCE" = auto ]; then
      KERNEL_SOURCE="aosp"
      KERNEL_SOURCE_FALLBACK_REASON="${OEM_FAILURE_REASON:-oneplus_preflight_failed}"
      EFFECTIVE_OPL="${IN_OS_PATCH_LEVEL:-}"
      OEM_REPO=""; OEM_BRANCH=""; OEM_TIP=""; OEM_SUBLEVEL=""
      echo "::warning::kernel_source=auto fallback to AOSP: $KERNEL_SOURCE_FALLBACK_REASON"
    else
      echo "::error::kernel_source=oneplus refused: ${OEM_FAILURE_REASON:-oneplus_preflight_failed}. Use auto for a recorded AOSP fallback." >&2
      exit 1
    fi
  fi
fi
echo "effective_os_patch_level=${EFFECTIVE_OPL}" >> "$GITHUB_OUTPUT"
echo "kernel_source_requested=${REQUESTED_KERNEL_SOURCE}" >> "$GITHUB_OUTPUT"
echo "kernel_source_actual=${KERNEL_SOURCE}" >> "$GITHUB_OUTPUT"
echo "kernel_source_fallback_reason=${KERNEL_SOURCE_FALLBACK_REASON}" >> "$GITHUB_OUTPUT"

# known-good: reuse the exact component commits of a build that was
# flashed and booted (recorded by "Mark build as known-good" into
# .github/known-good.json). The set is per kernel family +
# patch level + root, so it only applies to a dispatch that names one
# family, one patch level and one root flavor. Anything missing falls
# back to the latest tip of that component, loudly.
KG_ROOT=""; KG_SUSFS=""; KG_COMMON=""; KG_NOMOUNT=""; KG_BBG=""
KG_DS=""; KG_KP=""; KG_AK3=""; KG_KEY=""; KG_VPN=""; KG_USB=""; KG_RK=""
if [ "$COMMIT_MODE" = "known-good" ]; then
  KVER_IN="${IN_KVER:-}"
  OPL_IN="$EFFECTIVE_OPL"
  ROOT_IN="${IN_ROOT:-}"
  KG_FILE=".github/known-good.json"
  if [[ "$KVER_IN" =~ ^([0-9]+\.[0-9]+)\.x-(android[0-9]+)$ ]] && \
     [ -n "$OPL_IN" ] && [ "${OPL_IN,,}" != "all" ] && \
     [ -n "$ROOT_IN" ] && [[ "$ROOT_IN" != All* ]]; then
    KG_KEY="${BASH_REMATCH[2]}-${BASH_REMATCH[1]}-${OPL_IN}/${ROOT_IN}"
    [ "$KERNEL_SOURCE" = "oneplus" ] && KG_KEY="${KG_KEY}+oneplus"
  fi
  if [ -n "$KG_KEY" ] && [ -f "$KG_FILE" ] && jq -e --arg k "$KG_KEY" '.[$k].root_commit // empty' "$KG_FILE" >/dev/null 2>&1; then
    kg() { jq -r --arg k "$KG_KEY" --arg f "$1" '.[$k][$f] // empty' "$KG_FILE"; }
    KG_ROOT="$(kg root_commit)";        KG_SUSFS="$(kg susfs_commit)"
    KG_COMMON="$(kg kernel_source_commit)"; KG_NOMOUNT="$(kg nomount_commit)"
    KG_BBG="$(kg bbg_commit)";          KG_DS="$(kg droidspaces_commit)"
    KG_KP="$(kg kernel_patches_commit)"; KG_AK3="$(kg anykernel3_commit)"
    KG_VPN="$(kg vpnhide_commit)";       KG_USB="$(kg usb_arsenal_commit)"
    KG_RK="$(kg rekernel_commit)"
    echo "[+] known-good set '$KG_KEY' (recorded from $(kg from_run)):"
    jq --arg k "$KG_KEY" '.[$k]' "$KG_FILE"
    KG_EVIDENCE="$(jq -r --arg k "$KG_KEY" '.[$k].device_validation.status // empty' "$KG_FILE")"
    if [ "$KG_EVIDENCE" = "boot_attested_only" ]; then
      echo "::warning::This known-good set has URL-only manual boot attestation; the full device checklist was not attested."
    fi
    KG_POLICY="$(kg patch_policy_sha256)"
    if [ -n "$KG_POLICY" ]; then
      CURRENT_POLICY="$(sha256sum .github/patch-policy.json | cut -d' ' -f1)"
      [ "$KG_POLICY" = "$CURRENT_POLICY" ] || {
        echo "::error::patch-policy.json changed after this known-good set was validated. Review/enable patches and validate a new stable build; do not silently reuse the old verdict." >&2
        exit 1
      }
    fi
  else
    echo "::warning::commit_mode=known-good but no known-good set for '${KG_KEY:-this selection}' - building LATEST tips. After this build boots, paste its run URL into 'Mark build as known-good'."
  fi
fi
echo "known_good_key=${KG_ROOT:+$KG_KEY}" >> "$GITHUB_OUTPUT"
echo "pin_susfs_commit=${KG_SUSFS}" >> "$GITHUB_OUTPUT"
if [ "$KERNEL_SOURCE" = "oneplus" ]; then
  echo "pin_kernel_common=" >> "$GITHUB_OUTPUT"   # OnePlus commit travels in extra_pins
else
  echo "pin_kernel_common=${KG_COMMON}" >> "$GITHUB_OUTPUT"
fi
echo "pin_bbg_commit=${KG_BBG}" >> "$GITHUB_OUTPUT"
