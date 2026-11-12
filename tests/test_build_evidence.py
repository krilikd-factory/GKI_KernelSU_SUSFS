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
build_summary = load("build_summary")
rebrand = load("rebrand_anykernel")


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
            self.assertEqual(report["schema"], 2)
            self.assertFalse(report["asb_contract"]["custom_sysfs_abi"])
            self.assertEqual(report["asb_contract"]["control_plane"], "standard_linux_interfaces")
            self.assertFalse(report["asb_contract"]["arbitrary_path_value_writer"])
            self.assertEqual(report["asb_contract"]["writers_added"], [])
            self.assertEqual(report["final_config_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())

    def test_control_contract_is_bounded_and_never_claims_runtime_writability(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".config"
            path.write_text("CONFIG_LRU_GEN=y\n")
            controls = caps.make_report(path, "VPN")["asb_contract"]["controls"]
            self.assertEqual(set(controls), {"mglru_mode", "kernel_timer_migration", "kernel_sched_nr_migrate"})
            for name, control in controls.items():
                self.assertEqual(control["interface"], "standard_linux")
                self.assertEqual(control["runtime_state"], "unknown")
                self.assertEqual(control["writable"], "unknown")
                self.assertEqual(control["baseline"], "required_before_first_write")
                self.assertTrue(control["readback_required"])
                self.assertEqual(control["restore"], "exact_captured_value")
                self.assertTrue(control["runtime_path"].startswith(("/proc/sys/kernel/", "/sys/kernel/mm/lru_gen/")))
                self.assertIn("stock", control["requests"], name)
            self.assertEqual(controls["mglru_mode"]["build_state"], "available_in_config")
            self.assertEqual(controls["mglru_mode"]["requests"]["smart"], ["5", "7"])
            self.assertEqual(controls["kernel_sched_nr_migrate"]["requests"]["16"], ["16"])
            self.assertNotIn("32", controls["kernel_sched_nr_migrate"]["requests"])

    def test_missing_mglru_config_does_not_create_a_control_claim(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".config"
            path.write_text("# CONFIG_LRU_GEN is not set\n")
            control = caps.make_report(path, "")["asb_contract"]["controls"]["mglru_mode"]
            self.assertEqual(control["build_state"], "not_available_in_config")
            self.assertEqual(control["configs"]["CONFIG_LRU_GEN"], "n")
            self.assertEqual(control["runtime_state"], "unknown")
            self.assertEqual(control["writable"], "unknown")

    def test_vpn_hide_has_no_second_asb_owner(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".config"
            path.write_text("CONFIG_VPNHIDE=y\n")
            contract = caps.make_report(path, "VPN")["asb_contract"]
            self.assertIn("VPN Hide app", contract["external_owners"]["vpn_hide"])
            self.assertNotIn("vpn_hide", contract["controls"])


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


class PresentationContractTest(unittest.TestCase):
    def test_readme_is_fork_specific_without_upstream_community_promotion(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for needle in ("VPN Hide", "USB Arsenal", "NTSync", "DroidSpaces", "Baseband Guard",
                       "kernel_sched_nr_migrate", "BuildInfo", "Source provenance", "Patch lifecycle"):
            self.assertIn(needle, text)
        for forbidden in ("t.me/WildKernels", "wildkernels.dev", "TheWildJames", "🌿", "🍁", "## Donations"):
            self.assertNotIn(forbidden, text)
        self.assertIn("WildKernels/kernel_patches", text)  # attribution remains
        self.assertIn("Build-verified does not mean boot-verified", text)

    def test_anykernel_rebrand_preserves_logic_and_removes_wild_promotion(self):
        sample = '''kernel.string=Wild Kernels by TheWildJames aka Morgan Weedman
. tools/ak3-core.sh
ui_print " " "  -> Wild Kernels Supported: $ksu_supported"
split_boot
ui_print "WildKernels Telegram Channel:"
ui_print "https://t.me/WildKernelsTG"
ui_print "Thank you for using Wild Kernels! - TheWildJames"
'''
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "anykernel.sh").write_text(sample)
            rebrand.rebrand(root, "android16-6.12.69", "SUSFS+VPN+USB", "aosp", "4k")
            script = (root / "anykernel.sh").read_text()
            banner = (root / "banner").read_text()
            for forbidden in ("Wild", "TheWildJames", "t.me", "🌿", "🍁"):
                self.assertNotIn(forbidden, script + banner)
            self.assertIn(". tools/ak3-core.sh", script)
            self.assertIn("split_boot", script)
            self.assertIn("KRILIKD GKI compatibility check", script)
            self.assertIn("Build verified only", script)
            self.assertIn("osm0sis", script)
            self.assertIn("K R I L I K D", banner)

    def test_anykernel_unknown_layout_fails_instead_of_shipping_old_branding(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "anykernel.sh").write_text("kernel.string=changed\n")
            with self.assertRaisesRegex(ValueError, "required marker"):
                rebrand.rebrand(root, "test", "", "aosp", "4k")

    def test_build_summary_consolidates_patch_capability_and_safety_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            config = root / "kernel-final.config"
            config.write_text("CONFIG_PSI=y\nCONFIG_LRU_GEN=y\n")
            report = caps.make_report(config, "SUSFS+Perf+VPN")
            (root / "kernel-capabilities.json").write_text(json.dumps(report))
            (root / "patch-inventory.json").write_text(json.dumps({
                "schema": 1, "channel": "stable",
                "target": {"family": "android16-6.12", "device": "CPH2745", "soc": "sm8850"},
                "patches": [{"name": "reviewed", "state": "observed", "decision": "skip",
                             "reason": "not reviewed", "sha256": "a" * 64}],
            }))
            (root / "kmi-crc-report.txt").write_text("match\n")
            (root / "stock-config-report.txt").write_text("match\n")
            perf = root / "performance.md"
            perf.write_text("- applied (2): one two\n- patch channel: stable\n")
            args = build_summary.parser().parse_args([
                "--workspace", str(root), "--output", str(root / "out.md"), "--job-status", "success",
                "--file-name", "krilikd-test", "--kernel-version", "6.12", "--sublevel", "69",
                "--android-version", "android16", "--os-patch-level", "2026-03",
                "--variant", "KernelSU-Next", "--page-size", "4k", "--feature-set", "SUSFS+Perf+VPN",
                "--family", "android16-6.12", "--performance-report", str(perf),
            ])
            text = build_summary.render(args, {
                "ROOT_IMPLEMENTATION": "KernelSU-Next", "KERNEL_SOURCE": "aosp",
                "KERNEL_SOURCE_COMMIT": "b" * 40, "KERNEL_LTO": "NONE", "REJ_COUNT": "0",
                "VPNHIDE_MODE": "full", "VPNHIDE_VERSION": "1.3.1", "VPNHIDE_COMMIT": "c" * 40,
            })
            for needle in ("# ⚡ KRILIKD Build Report", "### 🩹 Patch decisions", "reviewed",
                           "Main Performance pass", "applied (2): one two", "mglru_mode",
                           "⚠️ Requires separate on-device validation", "Build-verified only"):
                self.assertIn(needle, text)
            self.assertNotIn("runtime interface writability or device compatibility.\"", text)
            self.assertNotIn("NoMount-Metamodule", text)
            args.feature_set = "Perf"
            sparse = build_summary.render(args, {"KERNEL_SOURCE": "aosp"})
            self.assertIn("| SUSFS | not requested | N/A | N/A |", sparse)
            self.assertNotIn("VPNHide-Builtin-Module", sparse)
            self.assertNotIn("KernelSU-Next (legacy path)", sparse)

    def test_performance_pass_is_saved_for_buildinfo(self):
        performance = (ROOT / ".github/actions/performance/action.yml").read_text()
        setup = (ROOT / ".github/actions/setup-build-environment/action.yml").read_text()
        build = (ROOT / ".github/workflows/build.yml").read_text()
        summary = (ROOT / ".github/actions/build-summary/action.yml").read_text()
        self.assertIn("> /tmp/performance-patches-summary.md", performance)
        self.assertIn("rebrand_anykernel.py", setup)
        for needle in ('--feature-set "${{ inputs.feature_set }}"', '--page-size "${{ inputs.page_size }}"'):
            self.assertIn(needle, setup)
        for needle in ("family: ${{ inputs.version }}", "droidspaces_commit: ${{ inputs.droidspaces_commit }}",
                       "kernel_patches_commit: ${{ inputs.kernel_patches_commit }}",
                       "anykernel3_commit: ${{ inputs.anykernel3_commit }}"):
            self.assertIn(needle, build)
        self.assertLess(build.index("name: Apply Performance Patches"), build.index("name: Consolidated Build Summary"))
        self.assertIn("${{ github.workspace }}/patch-inventory.json", build)
        for needle in ('--family "${{ inputs.family }}"', 'build_summary.py'):
            self.assertIn(needle, summary)
        self.assertIn('ap.add_argument("--performance-report", default="/tmp/performance-patches-summary.md")',
                      (SCRIPTS / "build_summary.py").read_text())


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
