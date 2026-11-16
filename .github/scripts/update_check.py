#!/usr/bin/env python3
"""KRILIKD update check: what is newer than the known-good kernel?

For every component the kernel is built from, compare the commit recorded in
.github/known-good.json (the last build that booted) with the component's
current upstream tip, and list the new commits. Also reports:
  * kernel_patches/common patches the Performance step does not use yet,
  * whether OnePlus has published kernel source for the phone's firmware,
  * new commits in the upstream workflow repo (WildKernels) you forked.

Writes a Markdown report (stdout and --report) and a JSON summary (--json).
Needs only git and network access to GitHub / GitLab / googlesource.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_LOG = 25

# Keep this order identical to the feature_set writer in main.yml.  Update Check
# must reproduce the boot-attested composition, not silently combine old pins
# with whichever feature defaults main.yml happens to have today.
FEATURE_ORDER = (
    "SUSFS", "NoMount", "BBG", "NET", "DS", "NTSync", "Ptrace", "Unicode",
    "BPF", "Perf", "BORE", "ADIOS", "VPN", "USB", "RK", "AWL", "SDP900",
)
FEATURE_INPUTS = {
    "SUSFS": "use_susfs", "NoMount": "use_nomount", "BBG": "use_bbg",
    "NET": "use_net", "DS": "use_ds", "NTSync": "use_ntsync",
    "BPF": "use_bpf", "VPN": "use_vpnhide", "USB": "use_usb_arsenal",
}
POWER_INPUTS = {
    (True, True, True): "all", (True, True, False): "both",
    (True, False, False): "rekernel", (False, True, False): "wakeup-blocker",
    (False, False, True): "sdp-900ma", (False, False, False): "none",
}


def dispatch_inputs_for_feature_set(value: str) -> tuple[str, dict[str, str]]:
    """Validate and translate a known-good feature set to main.yml inputs."""
    if not isinstance(value, str) or not value:
        raise ValueError("selected known-good entry has no usable feature_set; re-mark a successful build")
    parts = value.split("+")
    if any(not p for p in parts) or len(parts) != len(set(parts)):
        raise ValueError(f"malformed feature_set {value!r}; empty or repeated features are not allowed")
    unknown = sorted(set(parts) - set(FEATURE_ORDER))
    if unknown:
        raise ValueError("unknown known-good feature(s): " + ", ".join(unknown)
                         + "; update the explicit Update Check mapping before building")
    enabled = set(parts)
    if "BORE" in enabled and "Perf" not in enabled:
        raise ValueError("BORE requires Perf in feature_set; refusing to invent a scheduler composition")
    if "ADIOS" in enabled and "BORE" not in enabled:
        raise ValueError("ADIOS is currently composed with BORE; refusing an unreviewed standalone composition")
    canonical = "+".join(name for name in FEATURE_ORDER if name in enabled)
    inputs = {arg: ("true" if name in enabled else "false")
              for name, arg in FEATURE_INPUTS.items()}
    compat = ("Ptrace" in enabled, "Unicode" in enabled)
    inputs["compatibility_patches"] = {(True, True): "both", (True, False): "ptrace",
                                      (False, True): "unicode", (False, False): "none"}[compat]
    inputs["use_perf"] = ("bore_adios" if "ADIOS" in enabled else "bore") if "BORE" in enabled else ("true" if "Perf" in enabled else "false")
    inputs["power_app_features"] = POWER_INPUTS[tuple(name in enabled for name in ("RK", "AWL", "SDP900"))]
    return canonical, inputs


def select_build_composition(kg_all: dict, requested_key: str = "") -> tuple[str, str, dict[str, str]] | None:
    """Select one unambiguous non-@previous composition for the automatic build."""
    if not isinstance(kg_all, dict):
        raise ValueError("known-good.json must be an object")
    keys = [requested_key] if requested_key else sorted(k for k in kg_all if "@previous" not in k)
    if not keys:
        return None
    if any(not isinstance(k, str) or not k for k in keys):
        raise ValueError("known-good key must be a non-empty string")
    resolved = []
    for key in keys:
        entry = kg_all.get(key)
        if not isinstance(entry, dict):
            raise ValueError(f"known-good set {key!r} not found or not an object")
        canonical, inputs = dispatch_inputs_for_feature_set(entry.get("feature_set"))
        resolved.append((key, canonical, inputs))
    compositions = {(canonical, json.dumps(inputs, sort_keys=True)) for _, canonical, inputs in resolved}
    if len(compositions) != 1:
        raise ValueError("known-good sets have different feature compositions; rerun Update Check with an explicit key")
    return resolved[0]


def select_automatic_candidate_composition(kg_all: dict, requested_key: str = "") -> tuple[str, str, dict[str, str]] | None:
    """Compose the automatic candidate explicitly with BORE, without rewriting attestation.

    known-good.feature_set remains the exact composition that physically booted. The
    automatic update candidate is a new, explicitly un-attested composition instead.
    """
    selected = select_build_composition(kg_all, requested_key)
    if selected is None:
        return None
    key, attested, _ = selected
    if not key.startswith("android16-6.12-"):
        raise ValueError("automatic BORE candidates are supported only for android16-6.12; select a supported known-good key")
    enabled = attested.split("+")
    if "Perf" not in enabled:
        raise ValueError("automatic BORE policy requires Perf in the selected known-good feature_set")
    if "BORE" not in enabled:
        enabled.append("BORE")
    if "ADIOS" not in enabled:
        enabled.append("ADIOS")
    canonical, inputs = dispatch_inputs_for_feature_set("+".join(enabled))
    return key, canonical, inputs


def candidate_fingerprint(tips: list, feature_set: str, update_plan: dict | None = None) -> str:
    """Cache identity includes scheduler/features, artifact action and upstream tips."""
    import hashlib
    payload = {"tips": tips, "feature_set": feature_set or "workflow-defaults"}
    if update_plan is not None:
        payload["update_plan"] = update_plan
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


# Only files consumed by the companion artifact builds may classify as module-only.
# NoMount and VPN Hide also have kernel integration, so a component name alone is
# never sufficient evidence. Unknown paths fail closed to a full kernel build.
MODULE_PATH_POLICIES = {
    "NoMount": {"prefixes": ("module/", "userspace/"), "files": {"update.json"}},
    "VPN Hide": {"prefixes": ("builtin/",), "files": {"VERSION"}},
}


def module_paths_only(component: str, paths: list[str]) -> bool:
    """True only for a non-empty, fully accounted companion-module diff."""
    policy = MODULE_PATH_POLICIES.get(component)
    if not policy or not isinstance(paths, list) or not paths:
        return False
    for path in paths:
        if not isinstance(path, str) or not path or path.startswith("/") or ".." in path.split("/"):
            return False
        if path not in policy["files"] and not any(path.startswith(p) for p in policy["prefixes"]):
            return False
    return True


def classify_update_plan(summary: dict) -> dict:
    """Return none/modules_only/full_build; every uncertainty means full_build."""
    if not isinstance(summary.get("updates"), int) or summary["updates"] < 0:
        return {"action": "full_build", "modules": [], "reason": "invalid update count"}
    if summary["updates"] == 0:
        return {"action": "none", "modules": [], "reason": "no newer components"}
    modules = set()
    reasons = []
    sets = summary.get("sets")
    if not isinstance(sets, dict):
        return {"action": "full_build", "modules": [], "reason": "missing component evidence"}
    for set_name, components in sets.items():
        if not isinstance(components, dict):
            return {"action": "full_build", "modules": [], "reason": f"invalid component set {set_name}"}
        for name, evidence in components.items():
            if not isinstance(evidence, dict) or name.startswith("new_patches_"):
                continue
            status = evidence.get("status")
            if status == "current":
                continue
            if status in ("unreachable", "unpinned"):
                reasons.append(f"{name}: {status}")
                continue
            if status != "newer":
                reasons.append(f"{name}: unknown status")
                continue
            paths = evidence.get("changed_paths")
            if evidence.get("change_evidence") != "available" or not module_paths_only(name, paths):
                reasons.append(f"{name}: kernel/mixed/unknown changed paths")
                continue
            modules.add(name)
    if reasons:
        return {"action": "full_build", "modules": [], "reason": "; ".join(sorted(set(reasons)))}
    if not modules:
        return {"action": "full_build", "modules": [], "reason": "updates lack module-only evidence"}
    return {"action": "modules_only", "modules": sorted(modules),
            "reason": "every newer component diff is confined to its companion artifact inputs"}


def git(*args: str, cwd: str | None = None, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[:300]}")
    return r.stdout


def ls_remote(url: str, *refs: str, opts: tuple[str, ...] = ()) -> dict[str, str]:
    out = {}
    for line in git("ls-remote", *opts, url, *refs, check=False).splitlines():
        sha, _, ref = line.partition("\t")
        if sha and ref:
            out[ref] = sha
    return out


def newest_tag(url: str) -> tuple[str, str] | None:
    refs = ls_remote(url, opts=("--tags",))
    tags = sorted(
        {m.group(1) for r in refs if (m := re.match(r"refs/tags/(v\d+\.\d+\.\d+)$", r))},
        key=lambda t: [int(x) for x in t[1:].split(".")],
    )
    if not tags:
        return None
    t = tags[-1]
    return t, refs.get(f"refs/tags/{t}^{{}}", refs.get(f"refs/tags/{t}"))


def commits_between(url: str, ref: str, old: str, new: str) -> tuple[list[str], str]:
    """Subjects of old..new on ref (newest first). Commit objects only."""
    with tempfile.TemporaryDirectory() as d:
        git("init", "-q", d)
        for flt in ("--filter=tree:0", "--filter=blob:none"):
            r = subprocess.run(
                ["git", "-C", d, "fetch", "-q", flt, "--depth=400", url, ref],
                text=True, capture_output=True,
            )
            if r.returncode == 0:
                break
        else:
            return [], "could not fetch history"
        have_old = subprocess.run(["git", "-C", d, "cat-file", "-e", f"{old}^{{commit}}"]).returncode == 0
        if not have_old:
            log = git("-C", d, "log", "--format=%h %cs %s", f"-{MAX_LOG}", "FETCH_HEAD", check=False)
            return log.splitlines(), "known-good commit is not in the last 400 commits of this branch (history rewritten or very old)"
        log = git("-C", d, "log", "--format=%h %cs %s", f"{old}..{new}", check=False).splitlines()
        return log, ""


def changed_paths_between(url: str, ref: str, old: str, new: str) -> tuple[list[str], str]:
    """Changed paths for old..new, or an explicit evidence error (never an empty guess)."""
    if not re.fullmatch(r"[0-9a-f]{40}", old or "") or not re.fullmatch(r"[0-9a-f]{40}", new or ""):
        return [], "module-only classification requires full old/new commit SHAs"
    with tempfile.TemporaryDirectory() as d:
        git("init", "-q", d)
        r = subprocess.run(
            ["git", "-C", d, "fetch", "-q", "--filter=blob:none", "--depth=400", url, ref],
            text=True, capture_output=True,
        )
        if r.returncode != 0:
            return [], "could not fetch changed-path history"
        have_old = subprocess.run(["git", "-C", d, "cat-file", "-e", f"{old}^{{commit}}"],
                                  capture_output=True).returncode == 0
        have_new = subprocess.run(["git", "-C", d, "cat-file", "-e", f"{new}^{{commit}}"],
                                  capture_output=True).returncode == 0
        if not have_old or not have_new:
            return [], "old/new commit is not in the fetched 400-commit history"
        diff = subprocess.run(["git", "-C", d, "diff", "--name-only", old, new],
                              text=True, capture_output=True)
        if diff.returncode != 0:
            return [], "git diff failed for module-only evidence"
        paths = sorted(set(diff.stdout.splitlines()))
        if not paths:
            return [], "newer commit has no changed paths"
        return paths, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--key", default="", help="known-good key; empty = every non-@previous key")
    ap.add_argument("--report", default="")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    root = Path(a.repo_root)

    kg_all = json.loads((root / ".github/known-good.json").read_text())
    device = json.loads((root / ".github/device.json").read_text())
    keys = [a.key] if a.key else [k for k in kg_all if "@previous" not in k]
    try:
        composition = select_automatic_candidate_composition(kg_all, a.key)
    except ValueError as exc:
        print(f"::error::Update Check candidate composition failed: {exc}", file=sys.stderr)
        return 2
    md: list[str] = ["# KRILIKD update check", ""]
    summary: dict = {"sets": {}, "updates": 0}
    if composition:
        selected_key, feature_set, build_inputs = composition
        attested = kg_all[selected_key].get("feature_set", "")
        summary.update({"known_good_key": selected_key, "known_good_feature_set": attested,
                        "feature_set": feature_set, "candidate_policy": "automatic_bore",
                        "build_inputs": build_inputs})
        md += [f"Automatic candidate from `{selected_key}` uses explicit BORE policy: `{feature_set}`.",
               f"Boot-attested known-good remains `{attested}`; this candidate is not marked as physically booted.", ""]


    for key in keys:
        kg = kg_all.get(key)
        if not kg:
            md += [f"Known-good set `{key}` not found.", ""]
            continue
        m = re.match(r"(android\d+)-(\d+\.\d+)-(\d{4}-\d{2}|lts)/", key)
        family = f"{m.group(1)}-{m.group(2)}" if m else "android16-6.12"
        opl = m.group(3) if m else ""
        oneplus = key.endswith("+oneplus") or kg.get("kernel_source") == "oneplus"

        comps = [
            ("KernelSU-Next (pershoot dev-susfs)", "https://github.com/pershoot/KernelSU-Next", "refs/heads/dev-susfs", "root_commit"),
            ("SUSFS", "https://gitlab.com/simonpunk/susfs4ksu.git", f"refs/heads/gki-{family}", "susfs_commit"),
            ("NoMount", "https://github.com/maxsteeel/nomount.git", "refs/heads/dev", "nomount_commit"),
            ("Baseband-guard", "https://github.com/vc-teahouse/Baseband-guard.git", "refs/heads/main", "bbg_commit"),
            ("DroidSpaces", "https://github.com/ravindu644/Droidspaces-OSS.git", "refs/heads/main", "droidspaces_commit"),
            ("kernel_patches", "https://github.com/WildKernels/kernel_patches.git", "refs/heads/main", "kernel_patches_commit"),
            ("AnyKernel3", "https://github.com/WildKernels/AnyKernel3.git", "refs/heads/gki-2.0", "anykernel3_commit"),
            ("USB-Arsenal", "https://github.com/ravindu644/USB-Arsenal.git", "refs/heads/main", "usb_arsenal_commit"),
        ]
        if oneplus:
            comps.append(("Kernel source (OnePlus)", kg.get("oem_repo", ""), f"refs/heads/{kg.get('oem_branch', '')}", "kernel_source_commit"))
        elif opl and opl != "lts":
            comps.append(("Kernel source (AOSP common)", "https://android.googlesource.com/kernel/common", f"refs/heads/{family}-{opl}", "kernel_source_commit"))

        md += [f"## `{key}`", f"Known-good from {kg.get('from_run', '?')} (features: {kg.get('feature_set', '?')})", "",
               "| Component | Known-good | Upstream now | New commits |", "|---|---|---|---|"]
        details: list[str] = []
        sets = summary["sets"].setdefault(key, {})

        def row(name, pinned, tip, n, note=""):
            md.append(f"| {name} | `{(pinned or '-')[:10]}` | `{(tip or '?')[:10]}` | {n}{(' - ' + note) if note else ''} |")

        for name, url, ref, field in comps:
            pinned = kg.get(field, "")
            if not url:
                continue
            tip = ls_remote(url, ref).get(ref, "")
            if not tip:
                row(name, pinned, "", "?", "unreachable")
                sets[name] = {"status": "unreachable"}
                continue
            if not pinned:
                row(name, pinned, tip, "-", "not recorded in known-good")
                sets[name] = {"status": "unpinned", "tip": tip}
                continue
            if pinned == tip:
                row(name, pinned, tip, "0")
                sets[name] = {"status": "current"}
                continue
            log, note = commits_between(url, ref, pinned, tip)
            row(name, pinned, tip, str(len(log)) if not note else "?", note)
            sets[name] = {"status": "newer", "tip": tip, "commits": log[:MAX_LOG]}
            if name == "NoMount":
                paths, path_note = changed_paths_between(url, ref, pinned, tip)
                sets[name]["change_evidence"] = "available" if not path_note else "unavailable"
                sets[name]["changed_paths"] = paths
                if path_note:
                    row("NoMount changed-path gate", pinned, tip, "?", path_note + "; full build required")
            summary["updates"] += 1
            details += [f"### {name}: {len(log)} new", "```", *log[:MAX_LOG], "```", ""]

        # vpnhide follows release tags, not a branch
        vt = newest_tag("https://github.com/okhsunrog/vpnhide")
        vp = kg.get("vpnhide_commit", "")
        if vt:
            tag, sha = vt
            same = vp == sha
            if not vp:
                row(f"VPN Hide (release {tag})", vp, sha, "-", "not recorded in known-good")
            else:
                row(f"VPN Hide (release {tag})", vp, sha, "0" if same else "new release")
            if vp and not same:
                summary["updates"] += 1
                paths, path_note = changed_paths_between("https://github.com/okhsunrog/vpnhide",
                                                         f"refs/tags/{tag}", vp, sha)
                sets["VPN Hide"] = {"status": "newer", "tip": sha, "tag": tag,
                                    "change_evidence": "available" if not path_note else "unavailable",
                                    "changed_paths": paths}
                if path_note:
                    row("VPN Hide changed-path gate", vp, sha, "?", path_note + "; full build required")
        md.append("")
        md += details

        # New kernel_patches/common patches: what the next latest build will
        # try on its own, and what is held back (same rules as the build).
        with tempfile.TemporaryDirectory() as d:
            git("init", "-q", d)
            if subprocess.run(["git", "-C", d, "fetch", "-q", "--depth=1",
                               "https://github.com/WildKernels/kernel_patches.git", "refs/heads/main"]).returncode == 0:
                git("-C", d, "checkout", "-q", "FETCH_HEAD", "--", "common")
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                import new_patches as npx
                deny = npx.read_list(root / ".github/patch-denylist.txt")
                allow = npx.read_list(root / ".github/patch-allowlist.txt")
                policy = npx.read_policy(root / ".github/patch-policy.json")
                rows = npx.inventory(Path(d) / "common", root / ".github/actions", deny, allow,
                                     policy, channel="stable", family=device.get("stock_kernel_family", ""),
                                     device=device.get("model", ""), soc=device.get("soc", ""))
                enabled = [r["name"] for r in rows if r["decision"] == "apply"]
                observed = [r["name"] for r in rows if r["state"] == "observed"]
                candidates = [r["name"] for r in rows if r["state"] == "candidate"]
                held = [(r["name"], r["state"], r["reason"]) for r in rows if r["decision"] != "apply"]
                md += ["### New kernel_patches/common patches", "",
                       "Enabled and eligible for the next stable build: "
                       + (", ".join(f"`{n}`" for n in enabled) or "none"),
                       "Observed (never auto-applied): "
                       + (", ".join(f"`{n}`" for n in observed) or "none"),
                       "Reviewed candidates (require an explicit candidate build): "
                       + (", ".join(f"`{n}`" for n in candidates) or "none"), ""]
                md += [f"- held: `{n}` [{state}] - {w}" for n, state, w in held]
                md.append("")
                sets["new_patches_enabled"] = enabled
                sets["new_patches_observed"] = observed
                sets["new_patches_candidates"] = candidates
                sets["new_patches_held"] = [n for n, _, _ in held]

    # OnePlus source availability vs the phone's stock kernel
    repo = device.get("oneplus_kernel_repo", "")
    if repo:
        pat = device.get("oneplus_branch_match", "")
        rx = "^" + re.escape(pat).replace(r"\*", ".*") + "$"
        heads = sorted(r[len("refs/heads/"):] for r in ls_remote(repo, opts=("--heads",)) if re.match(rx, r[len("refs/heads/"):]))
        md += ["## OnePlus kernel source", ""]
        if heads:
            br = heads[-1]
            with tempfile.TemporaryDirectory() as d:
                git("init", "-q", d)
                subprocess.run(["git", "-C", d, "fetch", "-q", "--depth=1", "--filter=blob:none", repo, f"refs/heads/{br}"])
                mk = git("-C", d, "show", "FETCH_HEAD:Makefile", check=False)
                sub = (re.search(r"^SUBLEVEL = (\d+)", mk, re.M) or [None, "?"])[1]
            stock = device.get("stock_sublevel")
            ready = sub.isdigit() and stock and int(sub) >= int(stock)
            md += [f"Newest branch `{br}`: kernel 6.12.{sub}; phone stock 6.12.{stock}.",
                   "**Ready: `kernel_source=oneplus` will build.**" if ready else
                   "Not yet: OnePlus has not published source for your firmware; `kernel_source=oneplus` refuses older trees.", ""]
            summary["oneplus"] = {"branch": br, "sublevel": sub, "ready": bool(ready)}

    # Upstream workflow repo you forked
    up = "https://github.com/WildKernels/GKI_KernelSU_SUSFS.git"
    with tempfile.TemporaryDirectory() as d:
        git("init", "-q", d)
        if subprocess.run(["git", "-C", d, "fetch", "-q", "--filter=tree:0", "--depth=15", up, "refs/heads/main"]).returncode == 0:
            md += ["## Upstream workflow (WildKernels/GKI_KernelSU_SUSFS main), latest commits", "```",
                   *git("-C", d, "log", "--format=%h %cs %s", "-15", "FETCH_HEAD", check=False).splitlines(), "```", ""]

    tips = sorted((k, n, v.get("tip", "")) for k, comp in summary["sets"].items()
                  for n, v in comp.items() if isinstance(v, dict) and v.get("status") == "newer")
    plan = classify_update_plan(summary)
    summary["update_plan"] = plan
    if plan["action"] == "modules_only" and composition:
        # Build only companions that actually changed. Kernel jobs are gated off by
        # modules_only; all other inherited feature inputs remain explicit and harmless.
        summary["build_inputs"]["use_nomount"] = "true" if "NoMount" in plan["modules"] else "false"
        summary["build_inputs"]["use_vpnhide"] = "true" if "VPN Hide" in plan["modules"] else "false"
    summary["fingerprint"] = candidate_fingerprint(tips, summary.get("feature_set", ""), plan)

    if plan["action"] == "modules_only":
        md += ["**Update action: companion module artifacts only (`release_type=Modules`).** "
               f"Changed modules: {', '.join(plan['modules'])}. No kernel image job or release will run.", ""]
    elif plan["action"] == "full_build":
        md += [f"**Update action: full kernel build.** Fail-closed reason: {plan['reason']}.", ""]
    md += [f"**Components with updates: {summary['updates']}.** "
           "Full candidates use `commit_mode=latest` with the explicit automatic BORE composition. Module-only updates produce only the changed companion artifacts. New unreviewed patches remain observed; use `patch_channel=candidate` only after adding a scoped policy entry. After a full candidate boot check, promote known-good with the run URL; the full device checklist is optional and recorded separately.", ""]
    text = "\n".join(md)
    print(text)
    if a.report:
        Path(a.report).write_text(text + "\n")
    if a.json:
        Path(a.json).write_text(json.dumps(summary, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
