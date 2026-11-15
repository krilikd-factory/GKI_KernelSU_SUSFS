#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github/scripts"
PATCH = ROOT / ".github/actions/bore/patches/0001-linux6.12.37-bore-7.0.0.patch"
PATCH_SHA256 = "4362579319376f47d0c38e2aa30347082d73e6f122d32442def194462243e2c6"

FAIR = """\
unsigned int sysctl_sched_tunable_scaling = SCHED_TUNABLESCALING_LOG;

/*
 * Minimal preemption granularity for CPU-bound tasks:
 */
unsigned int sysctl_sched_base_slice\t\t\t= 700000ULL;
EXPORT_SYMBOL_GPL(sysctl_sched_base_slice);
static unsigned int normalized_sysctl_sched_base_slice\t= 700000ULL;

static struct sched_entity *__pick_eevdf(struct cfs_rq *cfs_rq, bool protect)
{
\tif (curr && protect && protect_slice(curr))
\t\treturn curr;
\treturn se;
}

static void update_curr(struct cfs_rq *cfs_rq)
{
\tif (entity_is_task(curr)) {
\t\t/*
\t\t * If the fair_server is active, we need to account for the
\t\t * fair_server time whether or not the task is running on
#ifdef CONFIG_SCHED_BORE
\t\tupdate_curr_bore(p, delta_exec);
#endif /* CONFIG_SCHED_BORE */
\t\t * behalf of fair_server or not:
\t\t */
\t}
}
"""


def run(args, **kw):
    return subprocess.run(args, text=True, capture_output=True, **kw)


class BoreWiring(unittest.TestCase):
    def test_patch_is_the_reviewed_file(self):
        self.assertEqual(hashlib.sha256(PATCH.read_bytes()).hexdigest(), PATCH_SHA256)
        self.assertIn("From: Masahito S <firelzrd@gmail.com>", PATCH.read_text(encoding="utf-8")[:300])

    def test_dispatch_stays_within_the_input_limit_and_bore_is_opt_in(self):
        main = (ROOT / ".github/workflows/main.yml").read_text()
        i = main.index("workflow_dispatch:")
        j = main.index("\njobs:", i)
        names = re.findall(r"^      ([a-z_0-9]+):\n        description", main[i:j], re.M)
        self.assertLessEqual(len(names), 25, "GitHub allows 25 workflow_dispatch inputs")
        self.assertNotIn("use_bore", names)
        block = main[main.index("      use_perf:"):main.index("      power_app_features:")]
        for token in ('- "true"', '- "false"', "- bore", 'default: "true"', "experimental"):
            self.assertIn(token, block)
        for token in ('true)  F_PERF=true;  F_BORE=false',
                      'false) F_PERF=false; F_BORE=false',
                      'bore)  F_PERF=true;  F_BORE=true',
                      '[ "$F_BORE" = "true" ] && FEATURES="${FEATURES}BORE+"',
                      '!= "6.12.x-android16"'):
            self.assertIn(token, main)
        sched = main[main.index('if [ "${{ github.event_name }}" = "schedule" ]; then'):main.index("          else\n            F_SUSFS")]
        self.assertIn("F_BORE=false", sched, "the nightly build must never carry BORE")

    def test_build_applies_bore_only_when_asked_and_only_on_612(self):
        build = (ROOT / ".github/workflows/build.yml").read_text()
        step = build[build.index("- name: Apply BORE scheduler (experimental)"):]
        step = step[:step.index("- name: Inventory patches")]
        self.assertIn("contains(inputs.feature_set, 'BORE')", step)
        self.assertIn("inputs.version == 'android16-6.12'", step)
        self.assertLess(build.index("Apply Performance Patches"), build.index("Apply BORE scheduler"))
        action = (ROOT / ".github/actions/bore/action.yml").read_text()
        self.assertIn("CONFIG_SCHED_BORE=y", action)
        self.assertIn("sha256sum", action)
        self.assertIn("bore_apply.sh", action)

    def test_release_notes_and_summary_know_the_feature(self):
        summary = (SCRIPTS / "build_summary.py").read_text()
        self.assertIn('"BORE"', summary)
        self.assertIn("No battery or frame-rate gain is claimed", summary)
        for name in ("render_kernel_notes.py",):
            for path in (ROOT / ".github" / name, SCRIPTS / name):
                text = path.read_text()
                self.assertIn('in ("true","bore")', text)
                self.assertIn("BORE scheduler (experimental)", text)

    def test_scripts_parse(self):
        for name in ("bore_gki612_fixups.py", "bore_kabi_612.py", "bore12_state.py", "bore12_flat.py"):
            compile((SCRIPTS / name).read_text(), name, "exec")
        self.assertEqual(run(["bash", "-n", str(SCRIPTS / "bore_apply.sh")]).returncode, 0)


class BoreFixups(unittest.TestCase):
    def run_fixups(self, text):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "fair.c"
            path.write_text(text)
            first = run([sys.executable, "-I", str(SCRIPTS / "bore_gki612_fixups.py"), str(path)])
            after = path.read_text()
            second = run([sys.executable, "-I", str(SCRIPTS / "bore_gki612_fixups.py"), str(path)])
            return first, second, after, path.read_text()

    def test_three_hand_ports_and_idempotence(self):
        first, second, after, again = self.run_fixups(FAIR)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(after, again, "a second run changed the file")
        self.assertIn("unsigned int sysctl_sched_min_base_slice       = CONFIG_MIN_BASE_SLICE_NS;", after)
        self.assertLess(after.index("#else /* !CONFIG_SCHED_BORE */\nunsigned int sysctl_sched_base_slice"),
                        after.index("EXPORT_SYMBOL_GPL(sysctl_sched_base_slice);"))
        self.assertIn("__maybe_unused", after)
        self.assertIn("sched_burst_protect_slice_cond_key", after)
        self.assertIn("!task_of(curr)->bore->futex_waiting", after)
        self.assertEqual(after.count("update_curr_bore("), 1)
        self.assertIn("if (entity_is_task(curr)) {\n#ifdef CONFIG_SCHED_BORE\n\t\tupdate_curr_bore(task_of(curr), delta_exec);",
                      after)
        self.assertNotIn("update_curr_bore(p,", after)

    def test_a_missing_anchor_stops_the_build(self):
        for old in ("\tif (curr && protect && protect_slice(curr))\n\t\treturn curr;\n",
                    "unsigned int sysctl_sched_tunable_scaling = SCHED_TUNABLESCALING_LOG;\n"):
            first, _, _, _ = self.run_fixups(FAIR.replace(old, "/* drifted */\n"))
            self.assertEqual(first.returncode, 1)
            self.assertIn("bore-gki612", first.stderr)

    def test_already_correct_call_is_left_alone(self):
        fixed = FAIR.replace(
            "\t\t * fair_server time whether or not the task is running on\n#ifdef CONFIG_SCHED_BORE\n\t\tupdate_curr_bore(p, delta_exec);\n#endif /* CONFIG_SCHED_BORE */\n",
            "\t\t * fair_server time whether or not the task is running on\n").replace(
            "\tif (entity_is_task(curr)) {\n",
            "\tif (entity_is_task(curr)) {\n#ifdef CONFIG_SCHED_BORE\n\t\tupdate_curr_bore(task_of(curr), delta_exec);\n#endif /* CONFIG_SCHED_BORE */\n")
        first, _, after, _ = self.run_fixups(fixed)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(after.count("update_curr_bore("), 1)


@unittest.skipUnless(os.environ.get("BORE_KERNEL_TREE"), "set BORE_KERNEL_TREE to an android16-6.12 common tree to run the full apply")
class BoreFullApply(unittest.TestCase):
    def test_apply_keeps_task_struct_layout(self):
        with tempfile.TemporaryDirectory() as d:
            tree = Path(d) / "common"
            shutil.copytree(os.environ["BORE_KERNEL_TREE"], tree, ignore=shutil.ignore_patterns(".git"))
            result = run(["bash", str(SCRIPTS / "bore_apply.sh"), str(tree), str(PATCH)])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            sched = (tree / "include/linux/sched.h").read_text()
            self.assertIn("ANDROID_KABI_USE(8, struct bore_ctx *bore);", sched)
            self.assertNotIn("struct bore_ctx bore;", sched)
            self.assertEqual(list(tree.rglob("*.rej")), [])
            again = run(["bash", str(SCRIPTS / "bore_apply.sh"), str(tree), str(PATCH)])
            self.assertNotEqual(again.returncode, 0, "a second apply must refuse an already patched tree")


if __name__ == "__main__":
    unittest.main(verbosity=2)
