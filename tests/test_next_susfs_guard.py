#!/usr/bin/env python3
"""KernelSU-Next / SUSFS API guard (next_susfs_api_guard.py + .sh).

Run 118021691889 compiled for ten minutes and stopped at throne_tracker.h:
pershoot dev-susfs called susfs_parse_sus_path_app_strings(), which SUSFS
v2.4.0 (395a8c0) does not provide. These fixtures rebuild that situation with
local git repositories, so the check runs offline.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / ".github/scripts/next_susfs_api_guard.py"
GUARD_SH = ROOT / ".github/scripts/next_susfs_api_guard.sh"
MAIN = ROOT / ".github/workflows/main.yml"
COMPAT = ROOT / ".github/scripts/ksu_susfs_api_compat.py"


def sh(repo: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")
    return subprocess.run(["git", "-C", str(repo), *args], check=True, text=True,
                          capture_output=True, env=env).stdout.strip()


def commit(repo: Path, files: dict[str, str], message: str) -> str:
    for name, text in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", message)
    return sh(repo, "rev-parse", "HEAD")


def new_repo(path: Path, branch: str) -> Path:
    path.mkdir(parents=True)
    sh(path, "init", "-q", "-b", branch)
    sh(path, "config", "uploadpack.allowAnySHA1InWant", "true")
    return path


SUSFS_H_240 = '''
    #define SUSFS_VERSION "v2.4.0"
    void susfs_parse_priority_app_strings(void);
    int susfs_set_priority_uid_filter(void *arg);
'''
SUSFS_H_241 = '''
    #define SUSFS_VERSION "v2.4.1"
    void susfs_parse_priority_app_strings(void);
    int susfs_set_priority_uid_filter(void *arg);
    void susfs_parse_sus_path_app_strings(void);
'''
KSU_GOOD = '''
    #include <linux/susfs.h>
    struct ops { int (*susfs_member)(int); };
    static int (*susfs_hook_ptr)(int);
    static inline int
    susfs_local_helper(int a,
                       int b)
    {
        return a + b;
    }
    void renew(struct ops *ops)
    {
        /* susfs_in_comment() is not a call */
        // susfs_in_line_comment();
        susfs_parse_priority_app_strings();
        susfs_local_helper(1, 2);
        ops->susfs_member(1);
        susfs_hook_ptr(3);
    #ifndef CONFIG_KSU_SUSFS
        susfs_only_without_susfs();
    #endif
    #ifdef CONFIG_KSU_SUSFS_SUS_KSTAT
        susfs_set_priority_uid_filter(0);
    #else
        susfs_only_when_feature_off();
    #endif
    #if defined(CONFIG_KSU_SUSFS_SUS_PATH)
        susfs_set_sus_path_uid_filter(0);
    #endif
    }
'''
KSU_BAD = KSU_GOOD.replace(
    "    #if defined(CONFIG_KSU_SUSFS_SUS_PATH)\n",
    "    #ifdef CONFIG_KSU_SUSFS_SUS_PATH\n        susfs_parse_sus_path_app_strings();\n    #endif\n"
    "    #if defined(CONFIG_KSU_SUSFS_SUS_PATH)\n")


class NextSusfsGuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.susfs = new_repo(t / "susfs", "gki-android16-6.12")
        self.s240 = commit(self.susfs, {"kernel_patches/include/linux/susfs.h": SUSFS_H_240,
                                        "kernel_patches/fs/susfs.c": "int susfs_core(void) { return 0; }\n",
                                        "kernel_patches/KernelSU/10_enable.patch": "+ susfs_parse_sus_path_app_strings();\n"},
                           "v2.4.0")
        self.s241 = commit(self.susfs, {"kernel_patches/include/linux/susfs.h": SUSFS_H_241}, "v2.4.1")
        self.picks = new_repo(t / "picks", "main")
        commit(self.picks, {"kernel_patches/fs/susfs.c": "int susfs_core(void) { return 0; }\n"}, "base")
        self.pick = commit(self.picks, {"kernel_patches/fs/susfs.c":
                                        "int susfs_core(void) { return 0; }\nint susfs_from_pick(void) { return 1; }\n"},
                           "pick")
        self.ksu = new_repo(t / "ksu", "dev-susfs")
        self.nosusfs = commit(self.ksu, {"kernel/core.c": "int main_loop(void) { return 0; }\n"}, "upstream")
        self.good = commit(self.ksu, {"kernel/manager/throne.h": KSU_GOOD,
                                      "kernel/x.c": "#include <linux/susfs.h>\nvoid f(void){ susfs_from_pick(); }\n"},
                           "good")
        self.bad = commit(self.ksu, {"kernel/manager/throne.h": KSU_BAD}, "bad")
        self.kg = t / "known-good.json"

    def tearDown(self):
        self.tmp.cleanup()

    def run_guard(self, want, kg=None, susfs_commit="", ksu_url=None, opl="2026-03", source="aosp"):
        self.kg.write_text(json.dumps(kg or {}))
        cmd = [sys.executable, "-I", str(GUARD), "--want", want, "--known-good-file", str(self.kg),
               "--opl", opl, "--kernel-source", source, "--compat-script", str(COMPAT),
               "--ksu-url", ksu_url or f"file://{self.ksu}", "--susfs-url", f"file://{self.susfs}",
               "--pick-url", f"file://{self.picks}", "--pick", self.pick, "--susfs-commit", susfs_commit]
        proc = subprocess.run(cmd, text=True, capture_output=True)
        out = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
        return proc.returncode, out, proc

    def test_compatible_commit_passes_and_follows_the_required_release(self):
        rc, out, proc = self.run_guard(self.good)
        self.assertEqual(rc, 0, proc.stderr)
        self.assertEqual(out["result"], "compatible", proc.stdout)
        self.assertEqual(out["susfs"], self.s240, "the v2.4.1 tip was used instead of the newest v2.4.0 commit")
        self.assertEqual(out["commit"], self.good)

    def test_run_118021691889_falls_back_to_the_known_good_root(self):
        kg = {"android16-6.12-2026-03/KernelSU-Next": {"root_commit": self.good, "marked_at": "2026-11-13T09:00:16Z"}}
        rc, out, proc = self.run_guard(self.bad, kg)
        self.assertEqual(rc, 0, proc.stderr)
        self.assertEqual(out["missing"], "susfs_parse_sus_path_app_strings", proc.stdout)
        self.assertEqual(out["result"], "known_good")
        self.assertEqual(out["commit"], self.good)
        self.assertEqual(out["known_good_key"], "android16-6.12-2026-03/KernelSU-Next")

    def test_a_root_without_susfs_integration_is_never_a_fallback(self):
        kg = {"android16-6.12-2026-03/KernelSU-Next": {"root_commit": self.nosusfs, "marked_at": "2026-11-13T09:00:16Z"}}
        rc, out, _ = self.run_guard(self.bad, kg)
        self.assertEqual(rc, 1)
        self.assertEqual(out["result"], "incompatible")

    def test_known_good_order_and_oneplus_suffix(self):
        kg = {
            "android16-6.12-2025-12/KernelSU-Next": {"root_commit": self.nosusfs, "marked_at": "2026-12-01T00:00:00Z"},
            "android16-6.12-2026-03/KernelSU-Next+oneplus": {"root_commit": self.good, "marked_at": "2026-11-01T00:00:00Z"},
        }
        rc, out, _ = self.run_guard(self.bad, kg)
        self.assertEqual((rc, out["result"]), (1, "incompatible"), "an aosp build took a OnePlus-source root")
        rc, out, _ = self.run_guard(self.bad, kg, source="oneplus")
        self.assertEqual((rc, out["result"], out["commit"]), (0, "known_good", self.good))

    def test_the_explicit_known_good_susfs_pin_is_checked(self):
        rc, out, _ = self.run_guard(self.bad, susfs_commit=self.s241)
        self.assertEqual((rc, out["result"], out["susfs"]), (0, "compatible", self.s241))

    def test_network_failure_never_blocks_a_build(self):
        rc, out, _ = self.run_guard(self.bad, ksu_url="file:///nonexistent/ksu")
        self.assertEqual((rc, out["result"], out["commit"]), (0, "unverified", self.bad))


class NextSusfsGuardWiringTest(unittest.TestCase):
    def test_main_sources_the_guard_between_known_good_and_the_manager_base(self):
        main = MAIN.read_text()
        src = 'source "$GITHUB_WORKSPACE/.github/scripts/next_susfs_api_guard.sh"'
        self.assertEqual(main.count(src), 1)
        self.assertLess(main.index('NEXT_COMMIT="$KG_ROOT"'), main.index(src))
        self.assertLess(main.index(src), main.index("emit next_base_commit"))
        start = main.index("      - name: Resolve latest component commits")
        run = main[main.index("run: |", start):main.index("\n      - name:", main.index("run: |", start))]
        self.assertLess(len(run), 21000, "GitHub caps an expression-bearing run: block at 21000 characters")

    def test_wrapper_handles_every_result_and_only_changes_next_commit(self):
        text = GUARD_SH.read_text()
        for word in ("compatible)", "known_good)", 'unverified|"")', "*)"):
            self.assertIn(word, text)
        self.assertIn('NEXT_COMMIT="$NG_COMMIT"', text)
        self.assertIn("6.12.x-android16", text)
        self.assertEqual(text.count("exit 1"), 1)
        subprocess.run(["bash", "-n", str(GUARD_SH)], check=True)

    def test_guard_reads_sources_and_never_patches_them(self):
        text = GUARD.read_text()
        for forbidden in ("write_text(", "open(", '"cherry-pick"', '"apply"', '"am"', '"commit"', '"push"'):
            self.assertNotIn(forbidden, text)


class AsbControlContractTest(unittest.TestCase):
    def test_nr_migrate_access_and_autogroup_policy_are_in_the_contract(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("caps", ROOT / ".github/scripts/kernel_capability_report.py")
        caps = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(caps)
        with tempfile.TemporaryDirectory() as d:
            config = Path(d) / ".config"
            config.write_text("CONFIG_DEBUG_FS=y\nCONFIG_SCHED_DEBUG=y\n# CONFIG_SCHED_AUTOGROUP is not set\n")
            controls = caps.make_report(config, "")["asb_contract"]["controls"]
        nr = controls["kernel_sched_nr_migrate"]
        self.assertEqual(nr["runtime_access"], "asb_private_debugfs_mount_per_access")
        self.assertEqual(nr["runtime_path"], "/sys/kernel/debug/sched/nr_migrate")
        self.assertEqual(controls["kernel_timer_migration"]["runtime_access"], "direct")
        ag = controls["kernel_sched_autogroup"]
        self.assertEqual((ag["build_state"], ag["build_policy"]), ("not_available_in_config", "not_enabled_kmi_signal_struct"))
        self.assertNotIn("build_policy", nr)


if __name__ == "__main__":
    unittest.main()
