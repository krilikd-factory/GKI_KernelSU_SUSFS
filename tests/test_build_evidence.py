#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github/scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


npx = load("new_patches")
caps = load("kernel_capability_report")
evidence = load("device_evidence")


def policy_entry(state="candidate", **overrides):
    entry = {
        "state": state,
        "families": ["android16-6.12"],
        "devices": ["CPH2745"],
        "soc": ["sm8850"],
        "provenance": {
            "source": "https://example.invalid/kernel.patch",
            "license": "GPL-2.0",
            "commit": "a" * 40,
        },
    }
    entry.update(overrides)
    return entry


class PatchLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = self.root / "common"
        self.actions = self.root / "actions/performance"
        self.patches.mkdir(parents=True)
        self.actions.mkdir(parents=True)
        (self.actions / "action.yml").write_text("# owned_patch is named here\n")
        (self.patches / "owned_patch.patch").write_text("already owned")
        (self.patches / "fresh.patch").write_text("--- a/a.c\n+++ b/a.c\n")

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self, entries=None, channel="stable", family="android16-6.12", device="CPH2745", soc="sm8850"):
        return npx.inventory(self.patches, self.root / "actions", {}, {},
                             {"schema": 1, "patches": entries or {}}, channel=channel,
                             family=family, device=device, soc=soc)

    def test_unknown_is_observed_and_never_applied(self):
        for channel in ("stable", "candidate"):
            rows = self.rows(channel=channel)
            self.assertEqual([r["name"] for r in rows], ["fresh"])
            self.assertEqual(rows[0]["state"], "observed")
            self.assertEqual(rows[0]["decision"], "skip")
            self.assertEqual(len(rows[0]["sha256"]), 64)

    def test_candidate_requires_explicit_channel(self):
        stable = self.rows({"fresh": policy_entry()})[0]
        candidate = self.rows({"fresh": policy_entry()}, channel="candidate")[0]
        self.assertEqual((stable["decision"], stable["state"]), ("skip", "candidate"))
        self.assertEqual((candidate["decision"], candidate["state"]), ("apply", "candidate"))

    def test_enabled_applies_to_stable(self):
        row = self.rows({"fresh": policy_entry("enabled")})[0]
        self.assertEqual((row["state"], row["decision"]), ("enabled", "apply"))

    def test_family_device_and_soc_are_hard_filters(self):
        for kwargs in ({"family": "android17-6.18"}, {"device": "other"}, {"soc": "other"}):
            row = self.rows({"fresh": policy_entry()}, channel="candidate", **kwargs)[0]
            self.assertEqual(row["decision"], "skip")
            self.assertIn("target mismatch", row["reason"])

    def test_candidate_requires_provenance_and_license(self):
        broken = policy_entry()
        del broken["provenance"]["license"]
        row = self.rows({"fresh": broken}, channel="candidate")[0]
        self.assertEqual(row["decision"], "skip")
        self.assertIn("license", row["reason"])

    def test_denylist_wins_over_enabled(self):
        rows = npx.inventory(self.patches, self.root / "actions", {"fresh": "unsafe"}, {},
                             {"patches": {"fresh": policy_entry("enabled")}})
        self.assertEqual((rows[0]["state"], rows[0]["decision"]), ("blocked", "skip"))

    def test_risky_header_needs_separate_allowlist(self):
        (self.patches / "fresh.patch").write_text("--- a/a.h\n+++ b/a.h\n+struct file { int x; };\n")
        row = self.rows({"fresh": policy_entry("enabled")})[0]
        self.assertEqual(row["decision"], "skip")
        rows = npx.inventory(self.patches, self.root / "actions", {}, {"fresh": "reviewed"},
                             {"patches": {"fresh": policy_entry("enabled")}},
                             family="android16-6.12", device="CPH2745", soc="sm8850")
        self.assertEqual(rows[0]["decision"], "apply")

    def test_policy_rejects_invalid_state(self):
        path = self.root / "policy.json"
        path.write_text(json.dumps({"schema": 1, "patches": {"x": {"state": "production"}}}))
        with self.assertRaises(ValueError):
            npx.read_policy(path)


class CapabilityReportTest(unittest.TestCase):
    def test_config_is_not_runtime_proof(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".config"
            path.write_text("CONFIG_PSI=y\nCONFIG_LRU_GEN=y\nCONFIG_ZRAM=m\nCONFIG_ZSMALLOC=y\n"
                            "# CONFIG_TCP_CONG_BBR is not set\n")
            report = caps.make_report(path, "NET+BPF")
            self.assertEqual(report["capabilities"]["memory.psi"]["build_state"], "available_in_config")
            self.assertEqual(report["capabilities"]["memory.mglru"]["runtime_state"], "unknown")
            self.assertEqual(report["capabilities"]["network.bbr"]["build_state"], "not_available_in_config")
            self.assertFalse(report["asb_contract"]["custom_sysfs_abi"])
            self.assertEqual(report["asb_contract"]["writers_added"], [])
            self.assertEqual(report["final_config_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())


class DeviceEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pins = Path(self.tmp.name) / "pins.json"
        self.pins.write_text(json.dumps({"key": {
            "file": "kernel-test", "from_run": "https://example.invalid/actions/runs/123"
        }}))

    def tearDown(self):
        self.tmp.cleanup()

    def report(self):
        result = evidence.template(self.pins)
        result["kernel_release"] = "6.12.69-test"
        result["tested_at"] = "2026-11-12T14:00:00Z"
        result["checks"] = {name: "passed" for name in evidence.CHECKS}
        return result

    def test_template_is_not_passed_by_default(self):
        with self.assertRaises(ValueError):
            evidence.validate_one(evidence.template(self.pins), self.pins)

    def test_complete_attestation_is_bound_to_run_and_build(self):
        metadata = evidence.validate_one(self.report(), self.pins)
        self.assertEqual(metadata["status"], "attested_passed")
        bad = self.report()
        bad["run_id"] = "124"
        with self.assertRaises(ValueError):
            evidence.validate_one(bad, self.pins)

    def test_sensitive_identifier_fields_are_rejected(self):
        bad = self.report()
        bad["device_serial"] = "not-allowed"
        with self.assertRaises(ValueError):
            evidence.validate_one(bad, self.pins)


class SourceProvenanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tree = self.root / "common"
        self.tree.mkdir()
        (self.tree / "Makefile").write_text("VERSION = 6\nPATCHLEVEL = 12\nSUBLEVEL = 69\n")
        for name in ("build.config.gki.aarch64", "BUILD.bazel", "build.config.constants"):
            (self.tree / name).write_text(name + "\n")
        config = self.tree / "arch/arm64/configs"
        config.mkdir(parents=True)
        (config / "gki_defconfig").write_text("CONFIG_PSI=y\n")
        subprocess.run(["git", "init", "-q", str(self.tree)], check=True)
        subprocess.run(["git", "-C", str(self.tree), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.tree), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                        "commit", "-qm", "fixture"], check=True)
        self.commit = subprocess.check_output(["git", "-C", str(self.tree), "rev-parse", "HEAD"], text=True).strip()
        self.manifest = self.root / "default.xml"
        self.manifest.write_text('<manifest><project path="common"/></manifest>\n')
        self.device = self.root / "device.json"
        self.device.write_text((ROOT / ".github/device.json").read_text())

    def tearDown(self):
        self.tmp.cleanup()

    def run_preflight(self, **overrides):
        args = {
            "tree": str(self.tree), "output": str(self.root / "out.json"),
            "device_file": str(self.device), "base_manifest": str(self.manifest),
            "source": "oneplus", "family": "android16-6.12",
            "expected_kernel": "6.12", "expected_sublevel": "69", "patch_level": "2026-03",
            "commit": self.commit,
            "oem_repo": "https://github.com/OnePlusOSS/android_kernel_common_oneplus_sm8850",
            "oem_branch": "oneplus/sm8850_test_oneplus_15",
        }
        args.update(overrides)
        import argparse
        return source.build_report(argparse.Namespace(**args))

    def test_oneplus_actual_tree_and_provenance(self):
        report = self.run_preflight()
        self.assertEqual(report["commit"], self.commit)
        self.assertTrue(report["git_clean_before_features"])
        self.assertEqual(len(report["source_digest"]), 64)
        self.assertIn("arch/arm64/configs/gki_defconfig", report["files"])
        self.assertEqual(len(report["base_manifest"]["sha256"]), 64)
        self.assertEqual(report["clang_revision"], "not_exposed")

    def test_aosp_mode_uses_same_commit_and_manifest_evidence(self):
        report = self.run_preflight(source="aosp", oem_repo="", oem_branch="")
        self.assertEqual(report["source"], "aosp")
        self.assertIsNone(report["oem"])

    def test_oneplus_older_than_stock_fails_even_when_selected(self):
        (self.tree / "Makefile").write_text("VERSION = 6\nPATCHLEVEL = 12\nSUBLEVEL = 23\n")
        subprocess.run(["git", "-C", str(self.tree), "add", "Makefile"], check=True)
        subprocess.run(["git", "-C", str(self.tree), "-c", "user.name=test", "-c", "user.email=test@example.invalid",
                        "commit", "-qm", "older"], check=True)
        self.commit = subprocess.check_output(["git", "-C", str(self.tree), "rev-parse", "HEAD"], text=True).strip()
        with self.assertRaisesRegex(ValueError, "older than"):
            self.run_preflight(expected_sublevel="23", patch_level="2025-06")

    def test_missing_base_manifest_fails(self):
        self.manifest.unlink()
        with self.assertRaisesRegex(ValueError, "manifest"):
            self.run_preflight()

    def test_actual_commit_mismatch_fails(self):
        with self.assertRaises(ValueError):
            self.run_preflight(commit="b" * 40)

    def test_wrong_oem_repo_fails(self):
        with self.assertRaises(ValueError):
            self.run_preflight(oem_repo="https://example.invalid/other")

    def test_modified_tree_before_features_fails(self):
        (self.tree / "Makefile").write_text((self.tree / "Makefile").read_text() + "# dirty\n")
        with self.assertRaises(ValueError):
            self.run_preflight()


class WorkflowContractTest(unittest.TestCase):
    def test_channel_reaches_every_family_and_performance(self):
        main = (ROOT / ".github/workflows/main.yml").read_text()
        prepare = (ROOT / ".github/workflows/prepare.yml").read_text()
        build = (ROOT / ".github/workflows/build.yml").read_text()
        performance = (ROOT / ".github/actions/performance/action.yml").read_text()
        self.assertEqual(main.count("patch_channel: ${{ inputs.patch_channel || 'stable' }}"), 8)
        self.assertIn("patch_channel: ${{ inputs.patch_channel }}", prepare)
        self.assertIn("patch_channel: ${{ inputs.patch_channel }}", build)
        self.assertIn("--channel \"$CHANNEL\"", performance)
        self.assertNotIn("New upstream patches applied automatically", performance)

    def test_known_good_requires_stable_and_device_evidence(self):
        text = (ROOT / ".github/workflows/mark-known-good.yml").read_text()
        self.assertIn('.patch_channel == "stable"', text)
        self.assertIn("device_evidence.py validate", text)
        self.assertIn("DEVICE_VALIDATION:", text)


source = load("source_provenance")

if __name__ == "__main__":
    unittest.main(verbosity=2)
