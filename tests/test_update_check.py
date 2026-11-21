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
        self.assertIn('build_args+=("-f" "release_type=Modules")', workflow)
        self.assertIn('if .update_plan.action == "modules_only"', workflow)
        self.assertIn("use_perf=bore", workflow)
        main = (ROOT / ".github/workflows/main.yml").read_text()
        self.assertNotIn('      modules_only:\n', main)
        self.assertIn('          - Modules\n', main)
        self.assertEqual(main.count("inputs.release_type != 'Modules' && !inputs.test_release_notes && ((github.event_name == 'schedule'"), 8)
        import yaml
        parsed = yaml.safe_load(main)
        self.assertEqual(len(parsed[True]["workflow_dispatch"]["inputs"]), 25)
        self.assertIn('build_args+=', workflow)

    @staticmethod
    def module_summary(paths, component="NoMount"):
        return {"updates": 1, "sets": {"key": {component: {
            "status": "newer", "tip": "b" * 40,
            "change_evidence": "available", "changed_paths": paths,
        }}}}

    def test_nomount_and_vpn_companion_only_diffs_produce_only_changed_modules(self):
        plan = update_check.classify_update_plan(self.module_summary(
            ["module/module.prop", "userspace/src/nm.c", "update.json"]))
        self.assertEqual(plan, {"action": "modules_only", "modules": ["NoMount"],
                                "reason": "every newer component diff is confined to its companion artifact inputs"})
        plan = update_check.classify_update_plan(self.module_summary(
            ["builtin/build.py", "builtin/module/module.prop", "VERSION"], "VPN Hide"))
        self.assertEqual(plan["action"], "modules_only")
        self.assertEqual(plan["modules"], ["VPN Hide"])

    def test_kernel_mixed_unknown_empty_and_unsafe_paths_fail_to_full_build(self):
        cases = [
            self.module_summary(["kernel/hooks.c", "module/module.prop"]),
            self.module_summary(["README.md"]),
            self.module_summary([], "NoMount"),
            self.module_summary(["../module/module.prop"]),
            self.module_summary(["builtin/build.py"], "KernelSU-Next"),
        ]
        unavailable = self.module_summary(["module/module.prop"])
        unavailable["sets"]["key"]["NoMount"]["change_evidence"] = "unavailable"
        cases.append(unavailable)
        unknown = self.module_summary(["module/module.prop"])
        unknown["sets"]["key"]["NoMount"]["status"] = "mystery"
        cases.append(unknown)
        for summary in cases:
            with self.subTest(summary=summary):
                self.assertEqual(update_check.classify_update_plan(summary)["action"], "full_build")

    def test_mixed_components_and_unreachable_evidence_fail_to_full_build(self):
        mixed = self.module_summary(["module/module.prop"])
        mixed["sets"]["key"]["SUSFS"] = {"status": "newer", "tip": "c" * 40}
        self.assertEqual(update_check.classify_update_plan(mixed)["action"], "full_build")
        unreachable = self.module_summary(["module/module.prop"])
        unreachable["sets"]["key"]["Kernel source (AOSP common)"] = {"status": "unreachable"}
        self.assertEqual(update_check.classify_update_plan(unreachable)["action"], "full_build")

    def test_no_updates_and_invalid_counts_do_not_create_a_module_run(self):
        self.assertEqual(update_check.classify_update_plan({"updates": 0, "sets": {}})["action"], "none")
        self.assertEqual(update_check.classify_update_plan({"updates": -1, "sets": {}})["action"], "full_build")

    def test_module_action_is_part_of_candidate_fingerprint(self):
        tips = [("key", "NoMount", "b" * 40)]
        full = {"action": "full_build", "modules": [], "reason": "mixed"}
        modules = {"action": "modules_only", "modules": ["NoMount"], "reason": "proved"}
        self.assertNotEqual(update_check.candidate_fingerprint(tips, BASE, full),
                            update_check.candidate_fingerprint(tips, BASE, modules))

    def test_known_good_without_bore_gets_an_explicit_unattested_bore_candidate(self):
        # A fixture, not the live known-good.json: once a BORE+ADIOS build is
        # promoted, the live entry already carries both tokens.
        kg_all = {"android16-6.12-2026-03/KernelSU-Next": {"feature_set": BASE}}
        before = json.dumps(kg_all, sort_keys=True)
        key, candidate, inputs = update_check.select_automatic_candidate_composition(kg_all)
        self.assertEqual(key, "android16-6.12-2026-03/KernelSU-Next")
        self.assertIn("Perf", candidate.split("+"))
        self.assertIn("BORE", candidate.split("+"))
        self.assertIn("ADIOS", candidate.split("+"))
        self.assertEqual(inputs["use_perf"], "bore_adios")
        self.assertNotIn("BORE", kg_all[key]["feature_set"].split("+"))
        self.assertEqual(json.dumps(kg_all, sort_keys=True), before)

    def test_live_known_good_keeps_bore_adios_and_is_not_rewritten(self):
        kg_all = json.loads((ROOT / ".github/known-good.json").read_text())
        before = json.dumps(kg_all, sort_keys=True)
        key, candidate, inputs = update_check.select_automatic_candidate_composition(kg_all)
        self.assertEqual(key, "android16-6.12-2026-03/KernelSU-Next")
        self.assertIn("BORE", candidate.split("+"))
        self.assertIn("ADIOS", candidate.split("+"))
        self.assertEqual(inputs["use_perf"], "bore_adios")
        self.assertEqual(json.dumps(kg_all, sort_keys=True), before)

    def test_automatic_bore_policy_fails_closed_without_perf_or_supported_family(self):
        with self.assertRaisesRegex(ValueError, "requires Perf"):
            update_check.select_automatic_candidate_composition(
                {"android16-6.12-2026-03/KernelSU-Next": {"feature_set": BASE.replace("+Perf", "")}})
        with self.assertRaisesRegex(ValueError, "supported only"):
            update_check.select_automatic_candidate_composition(
                {"android17-6.18-2026-03/KernelSU-Next": {"feature_set": BASE}})

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


    def test_nomount_update_check_uses_the_same_master_branch_as_build(self):
        update = (ROOT / ".github/scripts/update_check.py").read_text()
        build = (ROOT / ".github/workflows/main.yml").read_text()
        self.assertIn('("NoMount", "https://github.com/maxsteeel/nomount.git", "refs/heads/master", "nomount_commit")', update)
        self.assertNotIn('("NoMount", "https://github.com/maxsteeel/nomount.git", "refs/heads/dev", "nomount_commit")', update)
        self.assertIn('https://github.com/maxsteeel/nomount.git refs/heads/master', build)

if __name__ == "__main__":
    unittest.main(verbosity=2)
