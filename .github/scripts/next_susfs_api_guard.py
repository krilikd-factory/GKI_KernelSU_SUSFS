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


def kconfig_defaults(text: str, workflow_set: set[str]) -> dict[str, bool | None]:
    """Values of KernelSU's own bool options with nothing set but their Kconfig defaults.

    Run 118051915879 showed why: the booted known-good root calls the same missing
    function inside #ifndef CONFIG_KSU_THRONE_TRACKER_ALWAYS_THREADED, which is
    default y, so that call is never compiled. Options the workflow sets itself,
    options with a conditional default and anything this small evaluator cannot
    decide stay None (treated as compiled: stricter, never more lenient).
    """
    blocks: dict[str, dict] = {}
    cur = None
    for raw in strip_comments(text).splitlines():
        line = raw.strip()
        m = re.match(r"^(?:menu)?config\s+([A-Z0-9_]+)$", line)
        if m:
            cur = blocks.setdefault(m.group(1), {"defaults": [], "depends": []})
            continue
        if cur is None or not line:
            continue
        if line.startswith("depends on "):
            cur["depends"].append(line[len("depends on "):].strip())
        elif line.startswith("default "):
            cur["defaults"].append(line[len("default "):].strip())
        elif re.match(r"^(endmenu|menu |choice|endchoice|if |endif|source )", line):
            cur = None
    values: dict[str, bool | None] = {}

    def value(name: str, seen: frozenset = frozenset()) -> bool | None:
        if name.startswith("KSU_SUSFS") or name == "KSU":
            return True
        if name in values:
            return values[name]
        if name in seen or name not in blocks or f"CONFIG_{name}" in workflow_set:
            return None
        spec = blocks[name]
        result: bool | None
        if len(spec["defaults"]) > 1 or any(" if " in d for d in spec["defaults"]):
            result = None
        elif not spec["defaults"] or spec["defaults"][0] == "n":
            result = False
        elif spec["defaults"][0] in ("y", "m"):
            result = True
        else:
            result = None
        for dep in spec["depends"]:
            if "||" in dep or "(" in dep or "=" in dep:
                result = None if result is not False else False
                continue
            for term in (t.strip() for t in dep.split("&&")):
                neg = term.startswith("!")
                v = value(term.lstrip("!").strip(), seen | {name})
                if v is None:
                    result = None if result is not False else False
                elif v == neg:
                    result = False
        values[name] = result
        return result

    for name in blocks:
        value(name)
    return {f"CONFIG_{k}": v for k, v in values.items()}


KCONFIG: dict[str, bool | None] = {}


def _config_value(name: str) -> bool | None:
    if name.startswith("CONFIG_KSU_SUSFS"):
        return True
    return KCONFIG.get(name)


def _condition_state(directive: str, expr: str):
    """True/False when the condition names one decidable config, else None."""
    expr = expr.strip()
    if directive in ("ifdef", "ifndef"):
        m = re.match(r"^(CONFIG_[A-Z0-9_]+)\b", expr)
        v = _config_value(m.group(1)) if m else None
        if v is None:
            return None
        return v if directive == "ifdef" else not v
    m = re.fullmatch(r"(!?)\s*defined\s*\(?\s*(CONFIG_[A-Z0-9_]+)\s*\)?", expr)
    if m:
        v = _config_value(m.group(2))
        if v is not None:
            return v != bool(m.group(1))
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


WORKFLOW_SET: set[str] = set()


def ksu_surface(repo: Path, commit: str) -> tuple[set[str], set[str], bool]:
    tree = git(repo, "ls-tree", "-r", "--name-only", commit, "--", "kernel").splitlines()
    kconfig = "".join(git(repo, "show", f"{commit}:{f}") + "\n" for f in tree if f.split("/")[-1] == "Kconfig")
    KCONFIG.clear()
    KCONFIG.update(kconfig_defaults(kconfig, WORKFLOW_SET))
    files = [f for f in tree if f.endswith((".c", ".h"))]
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
    return set(re.findall(r'^LEGACY(?:_PARSE)?_SYMBOL\s*=\s*"(susfs_[a-z0-9_]+)"', text, flags=re.M))


def known_good_roots(path: Path | None, opl: str, oneplus: bool) -> list[tuple[str, str]]:
    if not path or not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    suffix = "/KernelSU-Next+oneplus" if oneplus else "/KernelSU-Next"
    rows = []
    for key, entry in data.items():
        base_key = key.split("@", 1)[0]
        if not (base_key.startswith("android16-6.12-") and base_key.endswith(suffix)):
            continue
        root = str(entry.get("root_commit") or "")
        if not SHA.match(root):
            continue
        exact = bool(opl) and base_key == f"android16-6.12-{opl}{suffix}"
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
    ap.add_argument("--workflow-dir", type=Path, default=Path(__file__).resolve().parents[1],
                    help="the .github tree; KernelSU options it sets are not assumed at their defaults")
    args = ap.parse_args()
    picks_wanted = args.pick if args.pick is not None else list(PERSHOOT_PICKS)

    if not SHA.match(args.want):
        print("result=unverified")
        print(f"commit={args.want}")
        print("reason=want is not a commit sha")
        return 0
    handled = handled_symbols(args.compat_script)
    if args.workflow_dir and args.workflow_dir.is_dir():
        for path in args.workflow_dir.rglob("*"):
            if path.is_file() and path.suffix in (".yml", ".yaml", ".sh", ".py", ".json") and path.resolve() != Path(__file__).resolve():
                try:
                    WORKFLOW_SET.update(re.findall(r"CONFIG_KSU_[A-Z0-9_]+", path.read_text(encoding="utf-8", errors="replace")))
                except OSError:
                    pass
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
        tried = []
        for key, root in known_good_roots(args.known_good_file, args.opl, args.kernel_source == "oneplus"):
            if root == args.want:
                continue
            try:
                fetch(ksu, args.ksu_url, root)
                kcalls, kdefined, integrated = ksu_surface(ksu, root)
            except FetchError:
                tried.append(f"{key}:{root[:12]}:fetch_failed")
                continue
            kmissing = missing_for(kcalls, kdefined, provided, handled)
            if not integrated:
                tried.append(f"{key}:{root[:12]}:no_susfs_integration")
            elif kmissing:
                tried.append(f"{key}:{root[:12]}:missing_{'+'.join(kmissing)}")
            if integrated and not kmissing:
                print("result=known_good")
                print(f"commit={root}")
                print(f"known_good_key={key}")
                return 0
        print(f"tried={' '.join(tried) or 'none'}")
        print("result=incompatible")
        print(f"commit={args.want}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
