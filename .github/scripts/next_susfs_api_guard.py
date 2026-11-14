#!/usr/bin/env python3
"""Check that KernelSU-Next's kernel sources only call SUSFS functions the
SUSFS checkout used for the build actually provides.

Run in the resolve step, before any kernel source is downloaded.  pershoot's
dev-susfs branch is rebased on its own schedule and can call functions that
the pinned SUSFS release (v2.4.0 on android16-6.12) does not have yet, or no
longer has.  Run 118021691889 compiled for ten minutes and then stopped at
throne_tracker.h with "call to undeclared function
'susfs_parse_sus_path_app_strings'"; this check finds that in seconds.

When the selected commit is incompatible, the newest boot-attested
KernelSU-Next root from .github/known-good.json is used instead, provided it
passes the same check against the same SUSFS.  There is no walk back along the
branch: dev-susfs is rebased, and the commits below its SUSFS integration
build a kernel without SUSFS support, which would pass a symbol check while
being wrong.  If no known-good root fits, the run fails here with the list of
missing functions.

Only build inputs are chosen here.  No SUSFS or KernelSU-Next source is
patched; the existing dispatch-arm removal in ksu_susfs_api_compat.py is
honoured as handled.

Output on stdout, one key=value per line:
  result=compatible|known_good|unverified|incompatible
  commit=<sha to build>  susfs=<sha checked>  missing=<comma list>
Exit status 0 when a commit can be built, 1 when none can.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

KSU_URL = "https://github.com/pershoot/KernelSU-Next.git"
SUSFS_URL = "https://gitlab.com/simonpunk/susfs4ksu.git"
PERSHOOT_SUSFS_URL = "https://gitlab.com/pershoot/susfs4ksu.git"
# The two pershoot SUSFS commits susfs-setup cherry-picks for the next flavor.
PERSHOOT_PICKS = (
    "6387756d64b3ab9ececefc4523627a9ee05a7519",
    "161c058fed5544a6573d87124f346dadd5c4962d",
)
SUSFS_BRANCH = "gki-android16-6.12"
SUSFS_VERSION = "v2.4.0"
SHA = re.compile(r"^[0-9a-f]{40}$")
IDENT = re.compile(r"\bsusfs_[a-z0-9_]+\b")
NAME_PAREN = re.compile(r"(?<![\w.])(susfs_[a-z0-9_]+)\s*\(")
FN_POINTER = re.compile(r"\(\s*\*\s*(susfs_[a-z0-9_]+)\s*\)")
MACRO = re.compile(r"(?m)^\s*#\s*define\s+(susfs_[a-z0-9_]+)\b")


class FetchError(Exception):
    pass


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True)
    if check and proc.returncode != 0:
        raise FetchError(f"git {' '.join(args[:3])}: {proc.stderr.strip()[:300]}")
    return proc.stdout


def fetch(repo: Path, url: str, ref: str, depth: int = 1) -> str:
    for _ in range(3):
        proc = subprocess.run(["git", "-C", str(repo), "fetch", "-q", f"--depth={depth}", url, ref],
                              text=True, capture_output=True)
        if proc.returncode == 0:
            return git(repo, "rev-parse", "FETCH_HEAD").strip()
    raise FetchError(f"fetch {url} {ref}: {proc.stderr.strip()[:300]}")


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _condition_state(directive: str, expr: str):
    """True/False when the condition only names SUSFS configs, else None."""
    expr = expr.strip()
    if directive == "ifdef":
        return True if expr.startswith("CONFIG_KSU_SUSFS") else None
    if directive == "ifndef":
        return False if expr.startswith("CONFIG_KSU_SUSFS") else None
    m = re.fullmatch(r"(!?)\s*defined\s*\(?\s*(CONFIG_[A-Z0-9_]+)\s*\)?", expr)
    if m and m.group(2).startswith("CONFIG_KSU_SUSFS"):
        return not m.group(1)
    return None


def active_text(text: str) -> str:
    """The source with every branch that is off in these builds blanked out.

    Branches guarded only by CONFIG_KSU_SUSFS* are decided (all features are on
    in these builds); any other condition is kept, so an unknown branch can only
    make the check stricter, never hide a missing function.
    """
    out: list[str] = []
    stack: list[bool | None] = []
    for line in strip_comments(text).splitlines():
        m = re.match(r"^\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b(.*)$", line)
        if m:
            kind, rest = m.group(1), m.group(2)
            if kind in ("ifdef", "ifndef", "if"):
                stack.append(_condition_state(kind, rest))
            elif kind == "elif" and stack:
                stack[-1] = None
            elif kind == "else" and stack:
                stack[-1] = None if stack[-1] is None else not stack[-1]
            elif kind == "endif" and stack:
                stack.pop()
            out.append("")
            continue
        out.append("" if any(state is False for state in stack) else line)
    return "\n".join(out)


def scan(text: str) -> tuple[set[str], set[str]]:
    """(called, defined) susfs_* names in one translation unit's active text."""
    called: set[str] = set()
    defined: set[str] = set(MACRO.findall(text)) | set(FN_POINTER.findall(text))
    for m in NAME_PAREN.finditer(text):
        name, start = m.group(1), m.start(1)
        if text[max(0, start - 2):start] == "->":
            continue
        depth, i = 0, m.end() - 1
        while i < len(text):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        rest = text[i + 1:i + 400].lstrip()
        rest = re.sub(r"^(?:__[a-z_]+(?:\(\([^)]*\)\))?\s*)+", "", rest)
        if rest.startswith("{"):
            defined.add(name)
        else:
            called.add(name)
    return called, defined


def ksu_surface(repo: Path, commit: str) -> tuple[set[str], set[str], bool]:
    files = [f for f in git(repo, "ls-tree", "-r", "--name-only", commit, "--", "kernel").splitlines()
             if f.endswith((".c", ".h"))]
    calls: set[str] = set()
    defined: set[str] = set()
    integrated = False
    for name in files:
        text = git(repo, "show", f"{commit}:{name}")
        if "linux/susfs" in text or "CONFIG_KSU_SUSFS" in text:
            integrated = True
        c, d = scan(active_text(text))
        calls |= c
        defined |= d
    return calls, defined, integrated


def susfs_provides(repo: Path, commit: str, picks: list[str]) -> set[str]:
    names: set[str] = set()
    files = git(repo, "ls-tree", "-r", "--name-only", commit, "--",
                "kernel_patches/include/linux", "kernel_patches/fs").splitlines()
    for name in files:
        names |= set(IDENT.findall(git(repo, "show", f"{commit}:{name}")))
    for pick in picks:
        diff = git(repo, "show", "--format=", pick, "--", "kernel_patches/fs", "kernel_patches/include/linux")
        for line in diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                names |= set(IDENT.findall(line))
    return names


def susfs_commit(repo: Path, url: str, explicit: str, branch: str, version: str) -> str:
    if explicit:
        return fetch(repo, url, explicit)
    tip = fetch(repo, url, f"refs/heads/{branch}", depth=60)
    for commit in git(repo, "rev-list", "--first-parent", tip).split():
        header = git(repo, "show", f"{commit}:kernel_patches/include/linux/susfs.h", check=False)
        if f'#define SUSFS_VERSION "{version}"' in header:
            return commit
    raise FetchError(f"no commit on {branch} declares SUSFS_VERSION {version}")


def handled_symbols(compat_script: Path | None) -> set[str]:
    if not compat_script or not compat_script.is_file():
        return set()
    text = compat_script.read_text(encoding="utf-8")
    return set(re.findall(r'^LEGACY_SYMBOL\s*=\s*"(susfs_[a-z0-9_]+)"', text, flags=re.M))


def known_good_roots(path: Path | None, opl: str, oneplus: bool) -> list[tuple[str, str]]:
    if not path or not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    suffix = "/KernelSU-Next+oneplus" if oneplus else "/KernelSU-Next"
    rows = []
    for key, entry in data.items():
        if not (key.startswith("android16-6.12-") and key.endswith(suffix)):
            continue
        root = str(entry.get("root_commit") or "")
        if not SHA.match(root):
            continue
        exact = bool(opl) and key == f"android16-6.12-{opl}{suffix}"
        rows.append((not exact, str(entry.get("marked_at") or ""), key, root))
    rows.sort(key=lambda r: (r[0], [-ord(c) for c in r[1]]))
    seen, out = set(), []
    for _, _, key, root in rows:
        if root not in seen:
            seen.add(root)
            out.append((key, root))
    return out


def missing_for(calls: set[str], defined: set[str], provided: set[str], handled: set[str]) -> list[str]:
    return sorted(c for c in calls if c not in defined and c not in provided and c not in handled)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--want", required=True, help="KernelSU-Next commit selected so far")
    ap.add_argument("--susfs-commit", default="", help="explicit SUSFS commit (known-good pin)")
    ap.add_argument("--known-good-file", type=Path)
    ap.add_argument("--opl", default="")
    ap.add_argument("--kernel-source", default="aosp")
    ap.add_argument("--compat-script", type=Path)
    ap.add_argument("--ksu-url", default=KSU_URL)
    ap.add_argument("--susfs-url", default=SUSFS_URL)
    ap.add_argument("--susfs-branch", default=SUSFS_BRANCH)
    ap.add_argument("--susfs-version", default=SUSFS_VERSION)
    ap.add_argument("--pick-url", default=PERSHOOT_SUSFS_URL)
    ap.add_argument("--pick", action="append", help="SUSFS commit cherry-picked for next (default: the two susfs-setup picks)")
    args = ap.parse_args()
    picks_wanted = args.pick if args.pick is not None else list(PERSHOOT_PICKS)

    if not SHA.match(args.want):
        print("result=unverified")
        print(f"commit={args.want}")
        print("reason=want is not a commit sha")
        return 0
    handled = handled_symbols(args.compat_script)
    with tempfile.TemporaryDirectory() as tmp:
        ksu = Path(tmp) / "ksu"
        sfs = Path(tmp) / "susfs"
        for repo in (ksu, sfs):
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
        try:
            base = susfs_commit(sfs, args.susfs_url, args.susfs_commit, args.susfs_branch, args.susfs_version)
            picks = [fetch(sfs, args.pick_url, sha, depth=2) for sha in picks_wanted if sha]
            provided = susfs_provides(sfs, base, picks)
            fetch(ksu, args.ksu_url, args.want)
            calls, defined, _ = ksu_surface(ksu, args.want)
        except FetchError as exc:
            print("result=unverified")
            print(f"commit={args.want}")
            print(f"reason={str(exc).splitlines()[0][:200]}")
            return 0
        missing = missing_for(calls, defined, provided, handled)
        print(f"susfs={base}")
        if not missing:
            print("result=compatible")
            print(f"commit={args.want}")
            return 0
        print(f"missing={','.join(missing)}")
        for key, root in known_good_roots(args.known_good_file, args.opl, args.kernel_source == "oneplus"):
            if root == args.want:
                continue
            try:
                fetch(ksu, args.ksu_url, root)
                kcalls, kdefined, integrated = ksu_surface(ksu, root)
            except FetchError:
                continue
            if integrated and not missing_for(kcalls, kdefined, provided, handled):
                print("result=known_good")
                print(f"commit={root}")
                print(f"known_good_key={key}")
                return 0
        print("result=incompatible")
        print(f"commit={args.want}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
