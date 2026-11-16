#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/update_check.py"
spec = importlib.util.spec_from_file_location("update_check", SCRIPT)
update_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update_check)

BASE = "SUSFS+NoMount+BBG+NET+DS+NTSync+Ptrace+Unicode+BPF+Perf+VPN+USB+RK+AWL+SDP900"


class UpdateCheckFeatureInheritanceTest(unittest.TestCase):
    def test_boot_attested_bore_is_dispatched_as_bore_with_the_full_composition(self):
        bore_set = BASE.replace("+Perf+VPN", "+Perf+BORE+VPN")
        canonical, inputs = update_check.dispatch_inputs_for_feature_set(bore_set)
        self.assertEqual(canonical, bore_set)
        self.assertEqual(inputs["use_perf"], "bore")
        self.assertEqual(inputs["compatibility_patches"], "both")
        self.assertEqual(inputs["power_app_features"], "all")
        self.assertEqual(inputs["use_susfs"], "true")
        self.assertEqual(inputs["use_nomount"], "true")
        self.assertEqual(len(inputs), 12)

    def test_non_bore_known_good_does_not_acquire_bore(self):
        _, perf = update_check.dispatch_inputs_for_feature_set(BASE)
        self.assertEqual(perf["use_perf"], "true")
        without_perf = BASE.replace("+Perf", "")
        _, disabled = update_check.dispatch_inputs_for_feature_set(without_perf)
        self.assertEqual(disabled["use_perf"], "false")
        selected = update_check.select_build_composition({"key": {"feature_set": BASE}})
        self.assertEqual(selected[2]["use_perf"], "true")

    def test_missing_malformed_unknown_and_ambiguous_compositions_fail_closed(self):
        for value in (None, "", "Perf++BORE", "Perf+BORE+BORE", "Perf+Unknown", "BORE"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                update_check.dispatch_inputs_for_feature_set(value)
        with self.assertRaisesRegex(ValueError, "no usable feature_set"):
            update_check.select_build_composition({"key": {}})
        with self.assertRaisesRegex(ValueError, "not found"):
            update_check.select_build_composition({}, "missing")
        with self.assertRaisesRegex(ValueError, "different feature compositions"):
            update_check.select_build_composition({"a": {"feature_set": BASE}, "b": {"feature_set": BASE + "+BORE"}})

    def test_fingerprint_changes_with_scheduler_composition(self):
        tips = [("key", "component", "a" * 40)]
        plain = update_check.candidate_fingerprint(tips, BASE)
        bore = update_check.candidate_fingerprint(tips, BASE + "+BORE")
        again = update_check.candidate_fingerprint(tips, BASE)
        self.assertEqual(plain, again)
        self.assertNotEqual(plain, bore)
        self.assertNotEqual(plain, update_check.candidate_fingerprint([], BASE))

    def test_workflow_forwards_validated_inputs_instead_of_only_commit_mode(self):
        workflow = (ROOT / ".github/workflows/update-check.yml").read_text()
        self.assertIn('echo "build_inputs=$(jq -c', workflow)
        self.assertIn('BUILD_INPUTS: ${{ steps.check.outputs.build_inputs }}', workflow)
        self.assertIn('build_args+=("-f" "$name=$value")', workflow)
        self.assertIn('-f commit_mode=latest "${build_args[@]}"', workflow)
        self.assertNotIn('-f commit_mode=latest\n', workflow)
        self.assertIn("Perf+BORE must arrive as use_perf=bore", workflow)
        self.assertIn("updates AND known-good features", workflow)

    def test_candidate_and_physical_attestation_gates_remain_intact(self):
        mark = (ROOT / ".github/workflows/mark-known-good.yml").read_text()
        build = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertIn('.patch_channel == "stable"', mark)
        self.assertIn('(.candidate_patches_applied // -1) == 0', mark)
        self.assertIn('select(.state == "candidate" and .decision == "apply")', build)
        self.assertIn("device_evidence.py validate", mark)
        self.assertIn("boot_attested_only", mark)

    def test_61292_physical_failure_is_not_rewritten_as_kmi_compatibility(self):
        policy = json.loads((ROOT / ".github/device-kmi-policy.json").read_text())
        result = policy["field_results"]["6.12.92"]
        self.assertEqual(result["status"], "boot_logo_stuck")
        self.assertFalse(result["physical_boot_attested"])
        self.assertFalse(result["known_good_eligible_from_this_result"])
        self.assertIn("firmware update is not a guaranteed fix", result["interpretation"])
        self.assertIn("6.12.92 stopped_after_boot_logo_stuck", policy["field_test_order"])
        self.assertNotIn("6.12.92 bore_on", policy["field_test_order"])
        self.assertNotIn("6.12.92 bore_off", policy["field_test_order"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
