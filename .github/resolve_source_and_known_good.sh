#!/usr/bin/env bash
# Sourced by main.yml (resolve-sources / "Resolve latest component commits").
# Lives in its own file because GitHub caps a run: block that contains
# expressions at 21000 characters. Expects in the environment: COMMIT_MODE,
# GITHUB_OUTPUT and IN_KERNEL_SOURCE, IN_OS_PATCH_LEVEL, IN_KVER,
# IN_ONEPLUS_BRANCH, IN_ROOT (the dispatch inputs). Leaves for the caller:
# KERNEL_SOURCE, EFFECTIVE_OPL, OEM_REPO, OEM_BRANCH, OEM_TIP, KG_KEY and the
# KG_* known-good pins.

# ---- Kernel source (aosp | oneplus) -------------------------------
# oneplus: build from OnePlusOSS's own common tree for this phone.
# Its base GKI release (e.g. android16-6.12-2025-06) decides which
# patch-level leg (= which Kleaf build tools) runs; kernel/common is
# then swapped for the OnePlus tree in download-kernel. Refuses a
# source older than the phone's stock kernel (.github/device.json):
# ColorOS vendor modules built for a newer KMI would not load.
oem_retry() { local i; for i in 1 2 3 4 5; do "$@" && return 0; sleep $((i*3)); done; return 1; }
KERNEL_SOURCE="${IN_KERNEL_SOURCE:-aosp}"
EFFECTIVE_OPL="${IN_OS_PATCH_LEVEL:-}"
OEM_REPO=""; OEM_BRANCH=""; OEM_TIP=""; OEM_SUBLEVEL=""
if [ "$KERNEL_SOURCE" = "oneplus" ]; then
  [ "${IN_KVER:-}" = "6.12.x-android16" ] || {
    echo "::error::kernel_source=oneplus needs kernel_build_version=6.12.x-android16"; exit 1; }
  DEV=.github/device.json
  OEM_REPO="$(jq -r .oneplus_kernel_repo "$DEV")"
  MATCH="$(jq -r .oneplus_branch_match "$DEV")"
  STOCK_SUB="$(jq -r .stock_sublevel "$DEV")"
  STOCK_OPL="$(jq -r .stock_patch_level "$DEV")"
  OEM_BRANCH="${IN_ONEPLUS_BRANCH:-}"
  if [ -z "$OEM_BRANCH" ]; then
    OEM_BRANCH="$(oem_retry git ls-remote --heads "$OEM_REPO" | sed 's#.*refs/heads/##' \
                  | while read -r b; do if [[ "$b" == $MATCH ]]; then echo "$b"; fi; done | sort -V | tail -n1)"
  fi
  [ -n "$OEM_BRANCH" ] || { echo "::error::no OnePlus branch matches '$MATCH' in $OEM_REPO"; exit 1; }
  OEM_TMP="$(mktemp -d)"
  git -C "$OEM_TMP" init -q
  oem_retry git -C "$OEM_TMP" fetch -q --depth=1 --filter=blob:none "$OEM_REPO" "refs/heads/$OEM_BRANCH"
  OEM_TIP="$(git -C "$OEM_TMP" rev-parse FETCH_HEAD)"
  OEM_SUBLEVEL="$(git -C "$OEM_TMP" show FETCH_HEAD:Makefile | sed -n 's/^SUBLEVEL = //p' | head -n1)"
  OEM_MSG="$(git -C "$OEM_TMP" log -1 --format=%B FETCH_HEAD)"
  OEM_OPL="$(printf '%s' "$OEM_MSG" | grep -oE 'android16-6\.12-[0-9]{4}-[0-9]{2}' | head -n1 | sed 's/^android16-6\.12-//')"
  if [ -z "$OEM_OPL" ]; then
    # No release tag in the message: map the sublevel onto the matrix.
    OEM_OPL="$(jq -r --arg s "$OEM_SUBLEVEL" '[.include[] | select(.sublevel == $s) | .date] | first // empty' .github/config/android16-6.12.json)"
  fi
  rm -rf "$OEM_TMP"
  echo "[+] OnePlus source: $OEM_REPO @ $OEM_BRANCH (${OEM_TIP:0:12}), kernel 6.12.${OEM_SUBLEVEL}, base ${OEM_OPL:-unknown}"
  if [ -z "$OEM_SUBLEVEL" ] || [ "$OEM_SUBLEVEL" -lt "$STOCK_SUB" ] || \
     { [ -n "$OEM_OPL" ] && [[ "$OEM_OPL" < "$STOCK_OPL" ]]; }; then
    {
      echo "::error::OnePlus has not published sources for your firmware yet."
      echo "::error::Newest OnePlus source: 6.12.${OEM_SUBLEVEL:-?} (${OEM_OPL:-?}, branch $OEM_BRANCH); your phone runs 6.12.${STOCK_SUB} (${STOCK_OPL}) per .github/device.json."
      echo "::error::A kernel older than stock cannot load ColorOS's vendor modules (boot-logo hang). Build with kernel_source=aosp until OnePlus releases the matching source; update device.json if your stock kernel changed."
    } >&2
    exit 1
  fi
  [ -n "$OEM_OPL" ] && jq -e --arg d "$OEM_OPL" 'any(.include[]; .date == $d)' .github/config/android16-6.12.json >/dev/null || {
    echo "::error::OnePlus base '${OEM_OPL:-unknown}' has no entry in .github/config/android16-6.12.json (needed for its build tools)"; exit 1; }
  EFFECTIVE_OPL="$OEM_OPL"
fi
echo "effective_os_patch_level=${EFFECTIVE_OPL}" >> "$GITHUB_OUTPUT"

# known-good: reuse the exact component commits of a build that was
# flashed and booted (recorded by "Mark build as known-good" into
# .github/known-good.json). The set is per kernel family +
# patch level + root, so it only applies to a dispatch that names one
# family, one patch level and one root flavor. Anything missing falls
# back to the latest tip of that component, loudly.
KG_ROOT=""; KG_SUSFS=""; KG_COMMON=""; KG_NOMOUNT=""; KG_BBG=""
KG_DS=""; KG_KP=""; KG_AK3=""; KG_KEY=""; KG_VPN=""; KG_USB=""
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
    echo "[+] known-good set '$KG_KEY' (recorded from $(kg from_run)):"
    jq --arg k "$KG_KEY" '.[$k]' "$KG_FILE"
    KG_POLICY="$(kg patch_policy_sha256)"
    if [ -n "$KG_POLICY" ]; then
      CURRENT_POLICY="$(sha256sum .github/patch-policy.json | cut -d' ' -f1)"
      [ "$KG_POLICY" = "$CURRENT_POLICY" ] || {
        echo "::error::patch-policy.json changed after this known-good set was validated. Review/enable patches and validate a new stable build; do not silently reuse the old verdict." >&2
        exit 1
      }
    fi
  else
    echo "::warning::commit_mode=known-good but no known-good set for '${KG_KEY:-this selection}' - building LATEST tips. After this build boots, run the 'Mark build as known-good' workflow with this run's id."
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
