#!/usr/bin/env python3
"""Compare the current build inputs with the latest recorded known-good pins.

No network requests and no device identifiers are used. Commit subjects are read
only when both objects and an ancestor relationship exist in a local checkout;
otherwise the report provides exact pins and an upstream compare URL instead.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

HEX40 = re.compile(r"[0-9a-f]{40}")
ROOT_REPOS = {
    "KernelSU": ("https://github.com/tiann/KernelSU", "kernel/KernelSU"),
    "KernelSU-Next": ("https://github.com/KernelSU-Next/KernelSU-Next", "kernel/KernelSU-Next"),
    "BakaSU": ("https://github.com/Baka-SU/BakaSU", "kernel/BakaSU"),
    "SukiSU-Ultra": ("https://github.com/SukiSU-Ultra/SukiSU-Ultra", "kernel/SukiSU-Ultra"),
    "KowSU": ("https://github.com/KOWX712/KernelSU", "kernel/KowSU"),
    "FinaSU": ("https://github.com/LyraVoid/FinaSU", "kernel/FinaSU"),
}


def pin(value: object) -> str:
    return value if isinstance(value, str) and HEX40.fullmatch(value) else ""


def features(value: object) -> set[str]:
    return {item.strip() for item in str(value or "").split("+") if item.strip()}


def clean_subject(value: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", " ", value).strip()[:300]


def compare_url(repo: str, old: str, new: str) -> str:
    if not repo or not old or not new:
        return ""
    if "googlesource.com" in repo:
        return f"{repo}/+log/{old}..{new}"
    if "gitlab.com" in repo:
        return f"{repo}/-/compare/{old}...{new}"
    if "github.com" in repo:
        return f"{repo}/compare/{old}...{new}"
    return ""


def history(tree: Path, old: str, new: str) -> tuple[str, list[dict[str, str]]]:
    if not old or not new or not tree.is_dir():
        return "local_history_unavailable", []
    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(tree), *args], text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    for commit in (old, new):
        if git("cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
            return "local_history_unavailable", []
    if git("merge-base", "--is-ancestor", old, new).returncode != 0:
        return "not_a_fast_forward_or_unrelated", []
    result = git("log", "--no-merges", "--format=%H%x09%s", "--max-count=41", f"{old}..{new}")
    if result.returncode != 0:
        return "local_history_unavailable", []
    rows = []
    for line in result.stdout.splitlines()[:40]:
        commit, _, subject = line.partition("\t")
        rows.append({"commit": commit, "subject": clean_subject(subject)})
    return ("available_truncated_to_40" if len(result.stdout.splitlines()) > 40 else "available"), rows


def build_report(args: argparse.Namespace, env: dict[str, str]) -> dict:
    workspace = Path(args.workspace).resolve()
    source = env.get("KERNEL_SOURCE", "aosp")
    key = f"{args.family}-{args.os_patch_level}/{args.variant}"
    if source == "oneplus":
        key += "+oneplus"
    known_good_path = workspace / ".github/known-good.json"
    try:
        known_good = json.loads(known_good_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        known_good = {}
    previous = known_good.get(key) if isinstance(known_good, dict) else None
    if not isinstance(previous, dict):
        previous = {}

    root_impl = env.get("ROOT_IMPLEMENTATION", "")
    root_repo, root_tree = ROOT_REPOS.get(root_impl, ("", ""))
    kernel_repo = env.get("OEM_REPO", "") if source == "oneplus" else "https://android.googlesource.com/kernel/common"
    definitions = [
        ("Kernel source", "kernel", "kernel_source_commit", env.get("KERNEL_SOURCE_COMMIT", ""), kernel_repo, "kernel/common"),
        (root_impl or "Root implementation", "root", "root_commit", env.get("ROOT_COMMIT", ""), root_repo, root_tree),
        ("SUSFS", "component", "susfs_commit", args.susfs_commit or env.get("SUSFS_COMMIT", ""), "https://gitlab.com/simonpunk/susfs4ksu", ""),
        ("NoMount / metamodule", "bundled_module", "nomount_commit", args.nomount_commit, "https://github.com/maxsteeel/nomount", "kernel/NoMount"),
        ("Baseband Guard", "component", "bbg_commit", env.get("BBG_COMMIT_FULL", ""), "https://github.com/WildKernels/kernel_patches", "kernel/Baseband-guard"),
        ("DroidSpaces", "component", "droidspaces_commit", args.droidspaces_commit, "https://github.com/ravindu644/Droidspaces-OSS", "Droidspaces-OSS"),
        ("kernel_patches", "component", "kernel_patches_commit", args.kernel_patches_commit, "https://github.com/WildKernels/kernel_patches", "kernel_patches"),
        ("AnyKernel3", "packaging", "anykernel3_commit", args.anykernel3_commit, "https://github.com/WildKernels/AnyKernel3", "AnyKernel3"),
        ("VPN Hide / companion module", "bundled_module", "vpnhide_commit", env.get("VPNHIDE_COMMIT", ""), "https://github.com/okhsunrog/vpnhide", ""),
        ("USB Arsenal", "component", "usb_arsenal_commit", env.get("USB_ARSENAL_COMMIT", ""), "https://github.com/ravindu644/USB-Arsenal", ""),
        ("Re-Kernel", "component", "rekernel_commit", env.get("REKERNEL_COMMIT", ""), "https://github.com/Sakion-Team/Re-Kernel", ""),
    ]
    current_features = features(args.feature_set)
    feature_baseline_recorded = isinstance(previous.get("feature_set"), str)
    previous_features = features(previous.get("feature_set"))
    components = []
    for name, kind, field, current_value, repo, relative_tree in (definitions if previous else []):
        old = pin(previous.get(field))
        new = pin(current_value)
        if not old and not new:
            continue
        if old and new:
            state = "changed" if old != new else "unchanged"
        elif old:
            state = "current_pin_unavailable"
        else:
            state = "baseline_pin_unavailable"
        item = {
            "name": name,
            "kind": kind,
            "previous": old or "not_recorded",
            "current": new or "not_recorded",
            "state": state,
            "compare_url": compare_url(repo, old, new) if state == "changed" else "",
        }
        if state == "changed":
            history_state, commits = history(workspace / relative_tree, old, new)
            item["history"] = history_state
            item["commits"] = commits
        components.append(item)

    return {
        "schema": 1,
        "comparison": "latest_recorded_known_good",
        "comparison_scope": "latest boot-attested known-good entry for the same family, patch level, variant and source; not necessarily the immediately preceding CI run",
        "key": key,
        "status": "available" if previous else "baseline_unavailable",
        "previous": {
            "file": previous.get("file", "not_recorded"),
            "from_run": previous.get("from_run", "not_recorded"),
            "validation_scope": (previous.get("device_validation") or {}).get("scope", "not_recorded") if isinstance(previous.get("device_validation"), dict) else "not_recorded",
        },
        "current_file": args.file_name,
        "features": {
            "status": ("available" if feature_baseline_recorded else
                       ("baseline_feature_set_not_recorded" if previous else "baseline_unavailable")),
            "previous": sorted(previous_features),
            "current": sorted(current_features),
            "added": sorted(current_features - previous_features) if feature_baseline_recorded else [],
            "removed": sorted(previous_features - current_features) if feature_baseline_recorded else [],
        },
        "components": components,
        "limitations": [
            "Commit pins and feature-set changes are build-input evidence, not physical boot or runtime proof.",
            "A compare URL is supplied when local git history is unavailable; no commit subjects are invented.",
            "Module commit changes do not by themselves prove that every source change affects the built artifact.",
            "A missing old or new pin is reported as unavailable, never inferred to mean that a component was added or removed.",
        ],
    }


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--file-name", required=True)
    ap.add_argument("--family", required=True)
    ap.add_argument("--os-patch-level", required=True)
    ap.add_argument("--variant", required=True)
    ap.add_argument("--feature-set", default="")
    ap.add_argument("--susfs-commit", default="")
    ap.add_argument("--nomount-commit", default="")
    ap.add_argument("--droidspaces-commit", default="")
    ap.add_argument("--kernel-patches-commit", default="")
    ap.add_argument("--anykernel3-commit", default="")
    return ap


def main() -> int:
    args = parser().parse_args()
    report = build_report(args, dict(os.environ))
    output = Path(args.output)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    changed = sum(item["state"] == "changed" for item in report["components"])
    print(f"Build delta: {report['status']}; {changed} changed source/component pins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
