#!/usr/bin/env python3
"""Newer upstream must never be dropped silently: held-back reporting,
the review-queue issue and the OnePlus-source candidate."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("review_queue", ROOT / ".github/scripts/review_queue.py")
rq = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rq)


def run_queue(summary: dict) -> tuple[str, str, str]:
    with tempfile.TemporaryDirectory() as d:
        sp, bp = Path(d) / "s.json", Path(d) / "b.md"
        sp.write_text(json.dumps(summary))
        out = subprocess.run(["python3", "-I", str(ROOT / ".github/scripts/review_queue.py"), str(sp), str(bp)],
                             capture_output=True, text=True, check=True).stdout
        state = [l.split("=", 1)[1] for l in out.splitlines() if l.startswith("state=")][0]
        sig = [l.split("=", 1)[1] for l in out.splitlines() if l.startswith("signature=")][0]
        return state, sig, bp.read_text()


class ReviewQueueTest(unittest.TestCase):
    def test_nothing_waiting_closes_the_issue(self):
        state, _, _ = run_queue({"sets": {"k": {"SUSFS": {"status": "current"}}}, "oneplus": {"ready": False}})
        self.assertEqual(state, "close")

    def test_newer_component_opens_and_signature_follows_the_tip(self):
        s1 = {"sets": {"k": {"KernelSU-Next (pershoot dev-susfs)": {"status": "newer", "tip": "a" * 40, "commits": ["x"]}}}}
        s2 = {"sets": {"k": {"KernelSU-Next (pershoot dev-susfs)": {"status": "newer", "tip": "b" * 40, "commits": ["x"]}}}}
        st1, sig1, body = run_queue(s1)
        st2, sig2, _ = run_queue(s2)
        self.assertEqual(st1, "open")
        self.assertIn("KernelSU-Next", body)
        self.assertNotEqual(sig1, sig2)
        self.assertEqual(sig1, run_queue(s1)[1])

    def test_oneplus_source_ready_and_unmatched_branches_are_reported(self):
        state, _, body = run_queue({"sets": {}, "oneplus": {
            "ready": True, "branch": "oneplus/sm8850_c_17.0.0_oneplus_15", "sublevel": "69", "tip": "c" * 40,
            "other_branches": ["oneplus/sm8850_c_17.0_oneplus_15_15t"]}})
        self.assertEqual(state, "open")
        self.assertIn("OnePlus kernel source is out", body)
        self.assertIn("oneplus_branch_match", body)

    def test_observed_patches_are_listed(self):
        state, _, body = run_queue({"sets": {"k": {"new_patches_observed": ["foo.patch"], "new_patches_candidates": []}}})
        self.assertEqual(state, "open")
        self.assertIn("foo.patch", body)


class WorkflowWiringTest(unittest.TestCase):
    def test_update_check_opens_issue_and_starts_oneplus_candidate(self):
        wf = yaml.safe_load((ROOT / ".github/workflows/update-check.yml").read_text())
        self.assertEqual(wf["permissions"].get("issues"), "write")
        steps = {s.get("name"): s for s in wf["jobs"]["check"]["steps"]}
        self.assertIn("Upstream review queue issue", steps)
        run = steps["Start a OnePlus-source candidate"]["run"]
        self.assertIn("kernel_source=oneplus", run)
        self.assertIn('oneplus_branch="$OP_BRANCH"', run)
        self.assertIn("oneplus-candidate-", json.dumps(wf))

    def test_main_publishes_held_back_and_reports_it(self):
        wf = yaml.safe_load((ROOT / ".github/workflows/main.yml").read_text())
        outs = wf["jobs"]["resolve-sources"]["outputs"]
        self.assertIn("held_back", outs)
        self.assertIn("commit_mode", outs)
        job = wf["jobs"]["held-back-issue"]
        self.assertEqual(job["permissions"]["issues"], "write")
        self.assertIn("latest", job["if"])
        self.assertIn("held-back", job["steps"][0]["run"])

    def test_guard_fallback_is_recorded_as_held_back(self):
        guard = (ROOT / ".github/scripts/next_susfs_api_guard.sh").read_text()
        self.assertIn('held_back "KernelSU-Next (dev-susfs)"', guard)

    def test_held_back_helper_writes_a_multiline_output(self):
        res = (ROOT / ".github/scripts/resolve_source_and_known_good.sh").read_text()
        start = res.index("HELD_BACK_FILE=")
        end = res.index('echo "commit_mode=')
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "out"
            script = res[start:end] + 'held_back "A" "1111" "2222" "why one"\nheld_back "B" "3" "4" "why two"\n'
            env = dict(os.environ, RUNNER_TEMP=d, GITHUB_OUTPUT=str(out))
            subprocess.run(["bash", "-c", script], env=env, check=True)
            text = out.read_text()
            last = text[text.rindex("held_back<<HELD_BACK_EOF"):]
            self.assertIn("A\t1111\t2222\twhy one", last)
            self.assertIn("B\t3\t4\twhy two", last)

    def test_waiting_for_oneplus_is_not_a_hold(self):
        res = (ROOT / ".github/scripts/resolve_source_and_known_good.sh").read_text()
        self.assertIn("oneplus_branch_unavailable|oneplus_source_older_than_stock", res)
        self.assertIn('held_back "Kernel source (OnePlus)"', res)


if __name__ == "__main__":
    unittest.main()
