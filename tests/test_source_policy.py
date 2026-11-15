#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github/scripts"
RESOLVER = SCRIPTS / "resolve_source_and_known_good.sh"


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(tree: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(tree), *args], check=True, text=True,
                          capture_output=True).stdout.strip()


class SourceResolverTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote"
        self.remote.mkdir()
        git(self.remote, "init", "-q", "--initial-branch=oneplus/sm8850_test_oneplus_15")
        git(self.remote, "config", "user.email", "test@example.invalid")
        git(self.remote, "config", "user.name", "Test")
        self.oem_files("69")
        self.fake_url = "https://github.com/OnePlusOSS/android_kernel_common_oneplus_sm8850"
        (self.root / ".github/config").mkdir(parents=True)
        (self.root / ".github/device.json").write_text(json.dumps({
            "oneplus_kernel_repo": self.fake_url,
            "oneplus_branch_match": "oneplus/sm8850_*_oneplus_15",
            "stock_sublevel": 69, "stock_patch_level": "2026-03",
        }))
        (self.root / ".github/config/android16-6.12.json").write_text(json.dumps({
            "include": [{"date": "2026-03", "sublevel": "69"}, {"date": "2026-04", "sublevel": "92"}]
        }))
        self.bin = self.root / "bin"
        self.bin.mkdir()
        real_git = shutil.which("git")
        (self.bin / "git").write_text(
            "#!/bin/bash\nargs=()\nfor a in \"$@\"; do\n"
            "  [ \"$a\" != \"$FAKE_OEM_URL\" ] || a=\"$REAL_OEM_REMOTE\"\n"
            "  args+=(\"$a\")\ndone\nexec \"" + real_git + "\" \"${args[@]}\"\n")
        (self.bin / "git").chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def oem_files(self, sublevel: str, kleaf: bool = True):
        (self.remote / "Makefile").write_text(f"VERSION = 6\nPATCHLEVEL = 12\nSUBLEVEL = {sublevel}\n")
        if kleaf:
            (self.remote / "BUILD.bazel").write_text("# Kleaf\n")
            (self.remote / "build.config.gki.aarch64").write_text("CLANG_VERSION=clang-test\n")
            (self.remote / "arch/arm64/configs").mkdir(parents=True, exist_ok=True)
            (self.remote / "arch/arm64/configs/gki_defconfig").write_text("CONFIG_ARM64=y\n")
        git(self.remote, "add", ".")
        git(self.remote, "commit", "-qm", f"android16-6.12-2026-03 sublevel {sublevel}")

    def run_resolver(self, requested="auto", kver="6.12.x-android16"):
        output = self.root / f"github-output-{requested}-{kver}.txt"
        output.write_text("")
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "COMMIT_MODE": "latest", "GITHUB_OUTPUT": str(output),
               "IN_KERNEL_SOURCE": requested, "IN_OS_PATCH_LEVEL": "2026-03", "IN_KVER": kver,
               "IN_ROOT": "KernelSU-Next", "IN_ONEPLUS_BRANCH": "", "FAKE_OEM_URL": self.fake_url,
               "REAL_OEM_REMOTE": str(self.remote)}
        result = subprocess.run(["bash", "-c", f'source "{RESOLVER}"'], cwd=self.root, env=env,
                                text=True, capture_output=True, check=False)
        return result, output.read_text()

    def test_auto_uses_suitable_oneplus_without_fallback(self):
        result, output = self.run_resolver()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kernel_source_requested=auto", output)
        self.assertIn("kernel_source_actual=oneplus", output)
        self.assertIn("kernel_source_fallback_reason=none", output)

    def test_old_oneplus_falls_back_only_in_auto(self):
        self.oem_files("68")
        result, output = self.run_resolver("auto")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kernel_source_actual=aosp", output)
        self.assertIn("kernel_source_fallback_reason=oneplus_source_older_than_stock", output)
        result, _ = self.run_resolver("oneplus")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kernel_source=oneplus refused", result.stderr)

    def test_incomplete_kleaf_falls_back_and_explicit_mode_fails(self):
        (self.remote / "BUILD.bazel").unlink()
        git(self.remote, "add", ".")
        git(self.remote, "commit", "-qm", "remove required Kleaf file")
        result, output = self.run_resolver("auto")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kernel_source_fallback_reason=oneplus_kleaf_files_incomplete", output)
        self.assertNotEqual(self.run_resolver("oneplus")[0].returncode, 0)

    def test_non_stock_family_auto_uses_aosp_lab_source_without_oem_probe(self):
        result, output = self.run_resolver("auto", "6.18.x-android17")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("kernel_source_actual=aosp", output)
        self.assertIn("kernel_source_fallback_reason=family_not_oneplus15_stock", output)
        self.assertNotEqual(self.run_resolver("oneplus", "6.18.x-android17")[0].returncode, 0)


class KmiAndEvidenceTest(unittest.TestCase):
    def test_kmi_scope_is_candidate_or_lab_never_boot_proof(self):
        module = load("kmi_compatibility")
        policy = module.load_policy(ROOT / ".github/device-kmi-policy.json")
        candidate = module.evaluate(policy, "android16-6.12", "aosp")
        self.assertEqual(candidate["classification"], "stock_kmi_family")
        self.assertEqual(candidate["oneplus15_daily_driver_status"], "not_attested")
        lab = module.evaluate(policy, "android17-6.18", "aosp")
        self.assertEqual(lab["build_scope"], "lab_only")
        self.assertFalse(lab["allow_cross_family_flash"])
        with self.assertRaises(ValueError):
            module.evaluate(policy, "android17-6.18", "oneplus")

    def test_scheduler_plan_has_no_measured_values(self):
        with tempfile.TemporaryDirectory() as d:
            output = Path(d) / "plan.json"
            subprocess.run([sys.executable, "-I", str(SCRIPTS / "scheduler_ab_evidence.py"),
                            "--family", "android16-6.12", "--sublevel", "92", "--source", "aosp",
                            "--feature-set", "Perf+BORE", "--output", str(output)], check=True)
            report = json.loads(output.read_text())
            self.assertEqual(report["status"], "not_measured")
            metrics = report["capture_template"]["metrics"]
            for key in ("frame_time_p95_ms", "frame_time_p99_ms", "jank_percent", "cpu_residency",
                        "temperature_c", "battery_current_ma", "battery_drain_percent_per_hour",
                        "network_latency_ms", "network_jitter_ms", "thermal_throttling_duration_s",
                        "kernel_logs", "asb_counters"):
                self.assertIsNone(metrics[key])
            self.assertEqual(report["required_field_order"][-1], "6.18 lab_only_after_same_family_results")

    def test_ext4_commit_age_is_blocked_in_both_channels(self):
        action = (ROOT / ".github/actions/performance/action.yml").read_text()
        loop = action.split("for p in avoid_extra_s2idle_wake_attempts", 1)[1].split("done", 1)[0]
        self.assertNotIn("increase_ext4_default_commit_age", loop)
        for channel in ("stable", "candidate"):
            result = subprocess.run([sys.executable, "-I", str(SCRIPTS / "performance_patch_policy.py"),
                                     "--policy", str(ROOT / ".github/performance-patch-policy.json"),
                                     "--channel", channel, "--source-commit", "a" * 40],
                                    check=True, text=True, capture_output=True)
            report = json.loads(result.stdout)
            row = next(p for p in report["patches"] if p["name"] == "increase_ext4_default_commit_age")
            self.assertEqual(row["decision"], "blocked")


class ProvenanceAndPatchFarmTest(unittest.TestCase):
    def test_auto_fallback_is_part_of_actual_source_provenance(self):
        module = load("source_provenance")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); tree = root / "tree"; tree.mkdir()
            git(tree, "init", "-q")
            git(tree, "config", "user.email", "test@example.invalid")
            git(tree, "config", "user.name", "Test")
            (tree / "Makefile").write_text("VERSION = 6\nPATCHLEVEL = 12\nSUBLEVEL = 92\n")
            (tree / "build.config.gki.aarch64").write_text("CLANG_VERSION=clang-test\n")
            (tree / "arch/arm64/configs").mkdir(parents=True)
            (tree / "arch/arm64/configs/gki_defconfig").write_text("CONFIG_ARM64=y\n")
            git(tree, "add", "."); git(tree, "commit", "-qm", "source")
            head = git(tree, "rev-parse", "HEAD")
            manifest = root / "default.xml"; manifest.write_text("<manifest/>\n")
            device = root / "device.json"; device.write_text("{}")
            args = type("Args", (), {"tree": str(tree), "device_file": str(device), "source": "aosp",
                    "requested_source": "auto", "fallback_reason": "oneplus_source_older_than_stock",
                    "commit": head, "expected_kernel": "6.12", "expected_sublevel": "92", "family": "android16-6.12",
                    "patch_level": "2026-03", "base_manifest": str(manifest), "oem_repo": "", "oem_branch": ""})()
            report = module.build_report(args)
            self.assertEqual(report["requested_source"], "auto")
            self.assertEqual(report["actual_source"], "aosp")
            self.assertEqual(report["fallback"]["reason"], "oneplus_source_older_than_stock")
            args.fallback_reason = "none"
            with self.assertRaises(ValueError):
                module.build_report(args)

    def test_patch_farm_checks_clean_tree_kconfig_and_writes_reject(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); tree = root / "tree"; tree.mkdir()
            git(tree, "init", "-q")
            git(tree, "config", "user.email", "test@example.invalid")
            git(tree, "config", "user.name", "Test")
            (tree / "Makefile").write_text("VERSION = 6\nPATCHLEVEL = 12\nSUBLEVEL = 69\n")
            (tree / "Kconfig").write_text("menu \"Test\"\nendmenu\n")
            git(tree, "add", "."); git(tree, "commit", "-qm", "base")
            (tree / "Kconfig").write_text("menu \"Test\"\nconfig ASB_FARM\n\tbool \"Farm\"\nendmenu\n")
            patch = root / "farm.patch"; patch.write_text(git(tree, "diff") + "\n")
            git(tree, "reset", "--hard", "-q", "HEAD")
            output = root / "report.json"; rejects = root / "rejects"
            command = [sys.executable, "-I", str(SCRIPTS / "patch_applicability.py"),
                       "--tree", f"clean={tree}", "--patch", str(patch), "--require-kconfig", "CONFIG_ASB_FARM",
                       "--reject-dir", str(rejects), "--output", str(output)]
            result = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout + output.read_text())
            report = json.loads(output.read_text())
            self.assertEqual(report["results"][0]["status"], "applicable_host_only")
            self.assertEqual(report["results"][0]["compile_only_smoke"], "not_run")
            self.assertTrue((tree / ".git").exists())
            self.assertEqual(git(tree, "status", "--porcelain"), "")
            bad_command = list(command)
            bad_command[bad_command.index("CONFIG_ASB_FARM")] = "CONFIG_MISSING"
            result = subprocess.run(bad_command, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertTrue(list(rejects.glob("*.reject.json")))
            self.assertEqual(json.loads(output.read_text())["results"][0]["kconfig_verification"], "failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
