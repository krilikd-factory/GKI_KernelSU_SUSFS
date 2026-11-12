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
    md: list[str] = ["# KRILIKD update check", ""]
    summary: dict = {"sets": {}, "updates": 0}


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
                sets["VPN Hide"] = {"status": "newer", "tip": sha, "tag": tag}
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

    import hashlib
    tips = sorted((k, n, v.get("tip", "")) for k, comp in summary["sets"].items()
                  for n, v in comp.items() if isinstance(v, dict) and v.get("status") == "newer")
    summary["fingerprint"] = hashlib.sha256(json.dumps(tips).encode()).hexdigest()[:16]

    md += [f"**Components with updates: {summary['updates']}.** "
           "To try component updates: run *Build Kernels* with `commit_mode=latest`. New unreviewed patches remain observed; use `patch_channel=candidate` only after adding a scoped policy entry. After your boot check, promote known-good with the run URL; the full device checklist is optional and recorded separately.", ""]
    text = "\n".join(md)
    print(text)
    if a.report:
        Path(a.report).write_text(text + "\n")
    if a.json:
        Path(a.json).write_text(json.dumps(summary, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
