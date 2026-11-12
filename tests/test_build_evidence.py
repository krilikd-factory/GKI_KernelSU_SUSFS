#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github/scripts"


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def embedded_source(relative_path, marker):
    """Return the exact Python heredoc executed by a composite action."""
    text = (ROOT / relative_path).read_text(encoding="utf-8")
    begin = f"# {marker}_BEGIN"
    end = f"# {marker}_END"
    assert text.count(begin) == 1 and text.count(end) == 1
    return textwrap.dedent(text.split(begin, 1)[1].split(end, 1)[0]).strip() + "\n"


def load_embedded(name, relative_path, marker):
    source = embedded_source(relative_path, marker)
    module = types.ModuleType(name)
    module.__file__ = str(ROOT / relative_path) + f":{marker}"
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module


npx = load("new_patches")
caps = load("kernel_capability_report")
evidence = load("device_evidence")
build_summary = load_embedded(
    "build_summary", ".github/actions/build-summary/action.yml", "KRILIKD_BUILD_SUMMARY")
rebrand = load_embedded(
    "rebrand_anykernel", ".github/actions/setup-build-environment/action.yml", "KRILIKD_REBRAND")


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


class KernelFeatureIntegrationTest(unittest.TestCase):
    def write_config(self, directory, disabled=()):
        path = Path(directory) / ".config"
        names = ["CONFIG_NET_SCH_CAKE", "CONFIG_REKERNEL", "CONFIG_REKERNEL_NETWORK",
                 "CONFIG_ASB_WAKEUP_BLOCKER"]
        names += list(caps.CAPABILITIES["network.ip_sets"]["configs"])
        path.write_text("".join(f"# {key} is not set\n" if key in disabled else f"{key}=y\n"
                                for key in names))
        return path

    def run_caps(self, config, *required):
        output = config.parent / "capabilities.json"
        command = [sys.executable, "-I", str(SCRIPTS / "kernel_capability_report.py"),
                   "--config", str(config), "--feature-set", "NET+RK+AWL",
                   "--output", str(output)]
        for name in required:
            command += ["--require", name]
        return subprocess.run(command, text=True, capture_output=True, check=False), output

    def test_selected_capabilities_are_required_in_final_config(self):
        with tempfile.TemporaryDirectory() as d:
            config = self.write_config(d)
            result, output = self.run_caps(config, "network.cake", "network.ip_sets",
                                           "app.rekernel", "power.wakeup_blocker")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(output.read_text())
            self.assertTrue(report["asb_contract"]["custom_sysfs_abi"])
            self.assertEqual(report["asb_contract"]["bounded_interfaces"]["wakeup_blocklist"]["max_rules"], 16)
            self.assertEqual(report["capabilities"]["app.rekernel"]["runtime_state"], "unknown")

    def test_requested_network_options_cannot_silently_disappear(self):
        for missing in ("CONFIG_NET_SCH_CAKE", "CONFIG_IP_SET_HASH_NET", "CONFIG_NETFILTER_XT_SET"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as d:
                config = self.write_config(d, {missing})
                result, _ = self.run_caps(config, "network.cake", "network.ip_sets")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("FINAL config", result.stderr)
                self.assertIn(missing + "=n", result.stderr)

    def test_unknown_required_capability_is_an_error_not_a_pass(self):
        with tempfile.TemporaryDirectory() as d:
            result, _ = self.run_caps(self.write_config(d), "network.unknown")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not declared", result.stderr)

    def test_workflow_gates_and_exact_rekernel_pin_are_fail_closed(self):
        build = (ROOT / ".github/workflows/build.yml").read_text()
        main = (ROOT / ".github/workflows/main.yml").read_text()
        resolver = (ROOT / ".github/scripts/resolve_source_and_known_good.sh").read_text()
        action = (ROOT / ".github/actions/rekernel/action.yml").read_text()
        self.assertIn("--require network.cake --require network.ip_sets", build)
        self.assertIn("--require app.rekernel", build)
        self.assertIn("--require power.wakeup_blocker", build)
        self.assertIn('Sakion-Team/Re-Kernel refs/heads/main', main)
        self.assertIn('KG_RK="$(kg rekernel_commit)"', resolver)
        self.assertIn('rekernel_commit: $rekernel_commit', build)
        self.assertIn('[[ "$WANT" =~ ^[0-9a-f]{40}$ ]]', action)
        self.assertIn('[ "$ACTUAL" = "$WANT" ]', action)
        self.assertNotIn("continue-on-error", action)
        self.assertNotIn("Re-Kernel skipped", action)

    def test_wakeup_patch_applies_to_reviewed_source_anchors(self):
        patch = ROOT / ".github/patches/android16-6.12/asb-wakeup-blocker.patch"
        with tempfile.TemporaryDirectory() as d:
            tree = Path(d)
            (tree / "drivers/base/power").mkdir(parents=True)
            (tree / "drivers/Kconfig").write_text(
                'source "drivers/cdx/Kconfig"\n\nsource "drivers/dpll/Kconfig"\n\nendmenu\n')
            (tree / "drivers/base/power/Makefile").write_text(
                "# SPDX-License-Identifier: GPL-2.0\n"
                "obj-$(CONFIG_PM)\t+= sysfs.o generic_ops.o common.o qos.o runtime.o wakeirq.o\n"
                "obj-$(CONFIG_PM_SLEEP)\t+= main.o wakeup.o wakeup_stats.o\n"
                "obj-$(CONFIG_PM_TRACE_RTC)\t+= trace.o\n"
                "obj-$(CONFIG_HAVE_CLK)\t+= clock_ops.o\n"
                "obj-$(CONFIG_PM_QOS_KUNIT_TEST) += qos-test.o\n")
            wakeup_source = "\n".join([
                "#include <linux/irq.h>", "#include <linux/irqdesc.h>",
                "#include <linux/wakeup_reason.h>", "#include <trace/events/power.h>", "",
                "#include \"power.h\"", "", "void __pm_stay_awake(struct wakeup_source *ws)", "{",
                "\tunsigned long flags;", "", "\tif (!ws)", "\t\treturn;", "",
                "\tspin_lock_irqsave(&ws->lock, flags);", "}",
                "void pm_wakeup_ws_event(struct wakeup_source *ws, unsigned int msec, bool hard)", "{",
                "\tunsigned long flags;", "\tunsigned long expires;", "",
                "\tif (!ws)", "\t\treturn;", "", "\tspin_lock_irqsave(&ws->lock, flags);", "}", "",
            ])
            (tree / "drivers/base/power/wakeup.c").write_text(wakeup_source)
            result = subprocess.run(["git", "apply", "--check", str(patch)], cwd=tree,
                                    text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run(["git", "apply", str(patch)], cwd=tree, check=True)
            wakeup = (tree / "drivers/base/power/wakeup.c").read_text()
            self.assertEqual(wakeup.count("asb_wakeup_source_blocked(ws->name)"), 2)
            driver = (tree / "drivers/base/power/asb_wakeup_blocker.c").read_text()
            self.assertIn("ASB_WAKEUP_MAX_RULES 16", driver)
            self.assertIn("DEFINE_STATIC_KEY_FALSE", driver)
            self.assertNotIn("LIST_WL_DEFAULT", driver)

    def test_rekernel_integrator_rewrites_only_exact_anchors(self):
        action = (ROOT / ".github/actions/rekernel/action.yml").read_text()
        marker = "python3 - \"$TREE\" \"$DST\" <<'PY'\n"
        source = textwrap.dedent(action.split(marker, 1)[1].split("\n        PY", 1)[0]) + "\n"
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); tree = root / "kernel"; dst = tree / "drivers/rekernel"
            dst.mkdir(parents=True)
            (dst / "Makefile").write_text("obj-m := rekernel.o\nrekernel-y := main.o\n")
            (dst / "Kconfig").write_text(
                'config REKERNEL\n\tbool "Re:Kernel module"\n\tdepends on MODULES\n'
                '\thelp\n\t  Tips: Enabling this will build Re-Kernel as a module\n')
            (tree / "drivers/Makefile").write_text("obj-y += base/\n")
            (tree / "drivers/Kconfig").write_text('menu "Device Drivers"\nendmenu\n')
            result = subprocess.run([sys.executable, "-c", source, str(tree), str(dst)],
                                    text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("obj-$(CONFIG_REKERNEL) += rekernel.o", (dst / "Makefile").read_text())
            self.assertIn("depends on ANDROID_VENDOR_HOOKS && NET && NETFILTER", (dst / "Kconfig").read_text())
            self.assertEqual((tree / "drivers/Kconfig").read_text().count("drivers/rekernel/Kconfig"), 1)
            bad = subprocess.run([sys.executable, "-c", source, str(tree), str(dst)],
                                 text=True, capture_output=True, check=False)
            self.assertNotEqual(bad.returncode, 0)


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

    def test_url_only_mode_records_boot_without_claiming_full_device_checks(self):
        metadata = evidence.boot_attestation(self.pins, "2026-11-13T18:00:00Z")
        self.assertEqual(metadata["status"], "boot_attested_only")
        self.assertEqual(metadata["run_id"], "123")
        self.assertEqual(metadata["build"], "kernel-test")
        self.assertIn("not attested", metadata["scope"])
        self.assertNotIn("kernel_release", metadata)
        self.assertEqual(len(metadata["sha256"]), 64)
        with self.assertRaisesRegex(ValueError, "timestamp"):
            evidence.boot_attestation(self.pins, "today")

    def test_url_only_cli_needs_no_json_and_binds_every_selected_pin(self):
        output = Path(self.tmp.name) / "metadata.json"
        result = subprocess.run([
            sys.executable, "-I", str(SCRIPTS / "device_evidence.py"), "validate",
            "--pins", str(self.pins), "--boot-confirmed",
            "--confirmed-at", "2026-11-13T18:00:00Z", "--output", str(output),
        ], text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(output.read_text())
        self.assertEqual(data["kernel-test"]["status"], "boot_attested_only")
        self.assertEqual(data["kernel-test"]["run_id"], "123")
        missing_mode = subprocess.run([
            sys.executable, "-I", str(SCRIPTS / "device_evidence.py"), "validate",
            "--pins", str(self.pins), "--output", str(output),
        ], text=True, capture_output=True, check=False)
        self.assertNotEqual(missing_mode.returncode, 0)
        self.assertIn("exactly one", missing_mode.stderr)

    def test_full_checklist_cli_remains_available(self):
        output = Path(self.tmp.name) / "full-metadata.json"
        result = subprocess.run([
            sys.executable, "-I", str(SCRIPTS / "device_evidence.py"), "validate",
            "--pins", str(self.pins), "--data", json.dumps(self.report()),
            "--output", str(output),
        ], text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(output.read_text())
        self.assertEqual(data["kernel-test"]["status"], "attested_passed")
        self.assertEqual(data["kernel-test"]["kernel_release"], "6.12.69-test")


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

    def test_rebrand_ci_no_longer_depends_on_missing_standalone_helper(self):
        """Regression for the CI exit 2: the YAML changed, but its new helper was absent."""
        sample = '''kernel.string=Wild Kernels by TheWildJames
. tools/ak3-core.sh
ui_print " " "  -> Wild Kernels Supported: $ksu_supported"
split_boot
ui_print "WildKernels Telegram Channel:"
ui_print "Thank you for using Wild Kernels! - TheWildJames"
'''
        with tempfile.TemporaryDirectory() as d:
            checkout = Path(d)
            anykernel = checkout / "AnyKernel3"
            anykernel.mkdir()
            (anykernel / "anykernel.sh").write_text(sample)

            old_helper = checkout / ".github/scripts/rebrand_anykernel.py"
            old = subprocess.run(
                [sys.executable, "-I", str(old_helper)], cwd=checkout,
                text=True, capture_output=True, check=False)
            self.assertEqual(old.returncode, 2)
            self.assertIn("No such file or directory", old.stderr)

            result = subprocess.run([
                sys.executable, "-I", "-", "--root", str(anykernel),
                "--release", "android16-6.12.69", "--feature-set", "SUSFS+Perf",
                "--source", "aosp", "--page-size", "4k",
            ], input=embedded_source(
                ".github/actions/setup-build-environment/action.yml", "KRILIKD_REBRAND"),
                cwd=checkout, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("AnyKernel3 presentation: KRILIKD", result.stdout)
            self.assertIn("KRILIKD", (anykernel / "anykernel.sh").read_text())

    def test_embedded_build_summary_runs_without_standalone_helper(self):
        with tempfile.TemporaryDirectory() as d:
            checkout = Path(d)
            output = checkout / "summary.md"
            result = subprocess.run([
                sys.executable, "-I", "-", "--workspace", str(checkout),
                "--output", str(output), "--job-status", "failure",
                "--file-name", "krilikd-test", "--kernel-version", "6.12",
                "--sublevel", "69", "--android-version", "android16",
                "--os-patch-level", "2026-03", "--variant", "KernelSU-Next",
                "--page-size", "4k", "--feature-set", "Perf",
                "--family", "android16-6.12",
            ], input=embedded_source(
                ".github/actions/build-summary/action.yml", "KRILIKD_BUILD_SUMMARY"),
                cwd=checkout, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = output.read_text()
            self.assertIn("❌ Failed", report)
            self.assertIn("Patch inventory: not generated", report)
            self.assertIn("Build-verified only", report)

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
        self.assertNotIn(".github/scripts/rebrand_anykernel.py", setup)
        self.assertNotIn(".github/scripts/build_summary.py", summary)
        for needle in ("python3 -I -", "KRILIKD_REBRAND_BEGIN", "KRILIKD_REBRAND_END",
                       '--feature-set "${{ inputs.feature_set }}"', '--page-size "${{ inputs.page_size }}"'):
            self.assertIn(needle, setup)
        for needle in ("python3 -I -", "KRILIKD_BUILD_SUMMARY_BEGIN", "KRILIKD_BUILD_SUMMARY_END"):
            self.assertIn(needle, summary)
        for needle in ("family: ${{ inputs.version }}", "droidspaces_commit: ${{ inputs.droidspaces_commit }}",
                       "kernel_patches_commit: ${{ inputs.kernel_patches_commit }}",
                       "anykernel3_commit: ${{ inputs.anykernel3_commit }}"):
            self.assertIn(needle, build)
        self.assertLess(build.index("name: Apply Performance Patches"), build.index("name: Consolidated Build Summary"))
        self.assertIn("${{ github.workspace }}/patch-inventory.json", build)
        self.assertIn('--family "${{ inputs.family }}"', summary)
        self.assertIn('ap.add_argument("--performance-report", default="/tmp/performance-patches-summary.md")',
                      embedded_source(".github/actions/build-summary/action.yml", "KRILIKD_BUILD_SUMMARY"))


class WorkflowContractTest(unittest.TestCase):
    def test_dispatch_input_limit_and_grouped_feature_selectability(self):
        """GitHub rejects a workflow at startup when dispatch has over 25 inputs."""
        import yaml

        path = ROOT / ".github/workflows/main.yml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        trigger = document.get("on", document.get(True))
        inputs = trigger["workflow_dispatch"]["inputs"]
        self.assertLessEqual(len(inputs), 25)
        self.assertEqual(len(inputs), 25)  # no accidental silent loss of an existing control
        self.assertNotIn("use_ptrace", inputs)
        self.assertNotIn("use_unicode", inputs)
        self.assertNotIn("use_rekernel", inputs)
        self.assertNotIn("use_wakeup_blocker", inputs)
        self.assertIn("test_release_notes", inputs)
        for name in ("compatibility_patches", "power_app_features"):
            self.assertEqual(inputs[name]["type"], "choice")
            self.assertEqual(inputs[name]["default"], "both")
            self.assertEqual(inputs[name]["options"], ["both", inputs[name]["options"][1],
                                                       inputs[name]["options"][2], "none"])
        self.assertEqual(inputs["compatibility_patches"]["options"][1:3], ["ptrace", "unicode"])
        self.assertEqual(inputs["power_app_features"]["options"][1:3], ["rekernel", "wakeup-blocker"])

        text = path.read_text(encoding="utf-8")
        for token in ('case "${{ inputs.compatibility_patches }}" in',
                      'case "${{ inputs.power_app_features }}" in',
                      'ptrace)  F_PTRACE=true;  F_UNICODE=false',
                      'unicode) F_PTRACE=false; F_UNICODE=true',
                      'rekernel)       F_RK=true;  F_AWL=false',
                      'wakeup-blocker) F_RK=false; F_AWL=true'):
            self.assertIn(token, text)
        for removed in ("inputs.use_ptrace", "inputs.use_unicode", "inputs.use_rekernel",
                        "inputs.use_wakeup_blocker"):
            self.assertNotIn(removed, text)

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

    def test_known_good_supports_url_only_boot_and_optional_full_evidence(self):
        text = (ROOT / ".github/workflows/mark-known-good.yml").read_text()
        resolver = (ROOT / ".github/scripts/resolve_source_and_known_good.sh").read_text()
        self.assertIn('.patch_channel == "stable"', text)
        self.assertIn("device_evidence.py validate", text)
        self.assertIn("DEVICE_VALIDATION:", text)
        self.assertIn('description: "Optional full checklist JSON; leave empty for boot-only confirmation"', text)
        self.assertIn("--boot-confirmed", text)
        self.assertIn('--data "$DEVICE_VALIDATION"', text)
        self.assertIn("boot_attested_only", text)
        self.assertIn('.[$k].device_validation.status // empty', resolver)
        self.assertIn("full device checklist was not attested", resolver)
        self.assertNotIn("Completed device-validation-template.json from the Pins artifact", text)

    def test_known_good_resolver_reads_boot_only_scope_and_keeps_exact_pins(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".github").mkdir()
            key = "android16-6.12-2026-03/KernelSU-Next"
            (root / ".github/known-good.json").write_text(json.dumps({key: {
                "root_commit": "a" * 40, "kernel_source_commit": "b" * 40,
                "susfs_commit": "c" * 40, "device_validation": {"status": "boot_attested_only"},
            }}))
            output = root / "github-output.txt"
            output.write_text("")
            result = subprocess.run([
                "bash", "-c", f'source "{ROOT}/.github/scripts/resolve_source_and_known_good.sh"',
            ], cwd=root, env={
                "PATH": "/usr/bin:/bin", "COMMIT_MODE": "known-good",
                "GITHUB_OUTPUT": str(output), "IN_KERNEL_SOURCE": "aosp",
                "IN_OS_PATCH_LEVEL": "2026-03", "IN_KVER": "6.12.x-android16",
                "IN_ROOT": "KernelSU-Next", "IN_ONEPLUS_BRANCH": "",
            }, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("URL-only manual boot attestation", result.stdout)
            pins = output.read_text()
            self.assertIn("pin_susfs_commit=" + "c" * 40, pins)
            self.assertIn("pin_kernel_common=" + "b" * 40, pins)
            self.assertIn("known_good_key=" + key, pins)


source = load("source_provenance")

class KrilikdRegressionTest(unittest.TestCase):
    def test_candidate_build_without_candidate_patches_is_promotable(self):
        mark = (ROOT / ".github/workflows/mark-known-good.yml").read_text()
        build = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertIn("candidate_patches_applied: $candidate_patches_applied", build)
        self.assertIn('select(.state == "candidate" and .decision == "apply")', build)
        self.assertIn('(.patch_channel == "stable" or ((.candidate_patches_applied // -1) == 0))', mark)

    def test_no_run_block_reaches_github_expression_limit(self):
        # GitHub rejects the whole workflow ("Exceeded max expression length
        # 21000") when one run: block that contains ${{ }} is longer than that.
        import re
        files = list((ROOT / ".github/workflows").glob("*.yml")) + list((ROOT / ".github/actions").glob("*/action.yml"))
        for path in files:
            lines = path.read_text().split("\n")
            i = 0
            while i < len(lines):
                m = re.match(r"^(\s*)(?:- )?run: [|>][+-]?\s*$", lines[i])
                if not m:
                    i += 1
                    continue
                indent = len(m.group(1))
                j = i + 1
                while j < len(lines) and (not lines[j].strip() or len(lines[j]) - len(lines[j].lstrip()) > indent):
                    j += 1
                size = sum(len(x) + 1 for x in lines[i + 1:j])
                self.assertLess(size, 20000, f"{path.relative_to(ROOT)}:{i + 1} run block is {size} chars")
                i = j


if __name__ == "__main__":
    unittest.main(verbosity=2)
