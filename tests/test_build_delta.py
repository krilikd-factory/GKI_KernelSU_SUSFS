#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
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


delta = load("build_delta")
summary = load("build_summary")
OLD = "1" * 40
NEW = "2" * 40
NOMOUNT_OLD = "3" * 40
NOMOUNT_NEW = "4" * 40


def arguments(workspace: Path, output: Path):
    return delta.parser().parse_args([
        "--workspace", str(workspace), "--output", str(output),
        "--file-name", "6.12.92-android16-2026-03-KernelSU-Next",
        "--family", "android16-6.12", "--os-patch-level", "2026-03",
        "--variant", "KernelSU-Next", "--feature-set", "SUSFS+Perf+BORE+NoMount+VPN",
        "--nomount-commit", NOMOUNT_NEW,
    ])


class BuildDeltaTest(unittest.TestCase):
    def test_missing_baseline_makes_no_chronological_claim(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            report = delta.build_report(arguments(root, root / "delta.json"), {
                "KERNEL_SOURCE": "aosp", "KERNEL_SOURCE_COMMIT": NEW,
            })
            self.assertEqual(report["status"], "baseline_unavailable")
            self.assertIn("not necessarily the immediately preceding CI run", report["comparison_scope"])
            self.assertEqual(report["components"], [])
            self.assertEqual(report["features"]["added"], [])
            self.assertEqual(report["features"]["removed"], [])
            self.assertEqual(report["features"]["current"], ["BORE", "NoMount", "Perf", "SUSFS", "VPN"])

    def test_missing_pins_and_feature_baseline_are_not_add_remove_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / ".github").mkdir()
            key = "android16-6.12-2026-03/KernelSU-Next"
            known_good = root / ".github/known-good.json"
            known_good.write_text(json.dumps({key: {"file": "recorded-but-sparse"}}))
            report = delta.build_report(arguments(root, root / "delta.json"), {
                "KERNEL_SOURCE": "aosp", "KERNEL_SOURCE_COMMIT": NEW,
            })
            self.assertEqual(report["status"], "available")
            self.assertEqual(report["features"]["status"], "baseline_feature_set_not_recorded")
            self.assertEqual(report["features"]["added"], [])
            self.assertEqual(report["features"]["removed"], [])
            kernel = next(item for item in report["components"] if item["name"] == "Kernel source")
            self.assertEqual(kernel["state"], "baseline_pin_unavailable")

            known_good.write_text(json.dumps({key: {
                "file": "recorded", "feature_set": "Perf", "kernel_source_commit": OLD,
            }}))
            report = delta.build_report(arguments(root, root / "delta.json"), {"KERNEL_SOURCE": "aosp"})
            kernel = next(item for item in report["components"] if item["name"] == "Kernel source")
            self.assertEqual(kernel["state"], "current_pin_unavailable")
            self.assertEqual(report["features"]["status"], "available")

    def test_kernel_modules_and_features_use_exact_recorded_pins(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / ".github").mkdir()
            key = "android16-6.12-2026-03/KernelSU-Next"
            (root / ".github/known-good.json").write_text(json.dumps({key: {
                "file": "6.12.69-android16-2026-03-KernelSU-Next",
                "from_run": "https://github.com/example/actions/runs/1",
                "kernel_source_commit": OLD,
                "nomount_commit": NOMOUNT_OLD,
                "vpnhide_commit": OLD,
                "feature_set": "SUSFS+Perf+NoMount+VPN",
                "device_validation": {"scope": "boot only"},
            }}))
            output = root / "build-delta.json"
            args = arguments(root, output)
            report = delta.build_report(args, {
                "KERNEL_SOURCE": "aosp", "KERNEL_SOURCE_COMMIT": NEW,
                "VPNHIDE_COMMIT": OLD, "ROOT_IMPLEMENTATION": "KernelSU-Next",
            })
            output.write_text(json.dumps(report))
            self.assertEqual(report["status"], "available")
            self.assertEqual(report["features"]["added"], ["BORE"])
            by_name = {item["name"]: item for item in report["components"]}
            self.assertEqual(by_name["Kernel source"]["state"], "changed")
            self.assertIn("+log/", by_name["Kernel source"]["compare_url"])
            self.assertEqual(by_name["NoMount / metamodule"]["state"], "changed")
            self.assertEqual(by_name["NoMount / metamodule"]["kind"], "bundled_module")
            self.assertEqual(by_name["VPN Hide / companion module"]["state"], "unchanged")
            self.assertEqual(by_name["Kernel source"]["history"], "local_history_unavailable")
            self.assertEqual(by_name["Kernel source"]["commits"], [])

            summary_args = summary.parser().parse_args([
                "--workspace", str(root), "--output", str(root / "out.md"),
                "--job-status", "success", "--file-name", args.file_name,
                "--kernel-version", "6.12", "--sublevel", "92",
                "--android-version", "android16", "--os-patch-level", "2026-03",
                "--variant", "KernelSU-Next", "--feature-set", args.feature_set,
                "--family", "android16-6.12",
            ])
            text = summary.render(summary_args, {"KERNEL_SOURCE_COMMIT": NEW})
            self.assertIn("Changes vs previous recorded known-good", text)
            self.assertIn("NoMount / metamodule", text)
            self.assertIn("local_history_unavailable", text)
            self.assertIn("no change list is inferred", text.lower())

    def test_local_git_subjects_are_real_and_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); tree = root / "kernel/common"
            tree.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(tree)], check=True)
            subprocess.run(["git", "-C", str(tree), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(tree), "config", "user.name", "Test"], check=True)
            (tree / "source.txt").write_text("old\n")
            subprocess.run(["git", "-C", str(tree), "add", "source.txt"], check=True)
            subprocess.run(["git", "-C", str(tree), "commit", "-q", "-m", "upstream base"], check=True)
            old = subprocess.check_output(["git", "-C", str(tree), "rev-parse", "HEAD"], text=True).strip()
            (tree / "source.txt").write_text("new\n")
            subprocess.run(["git", "-C", str(tree), "commit", "-q", "-am", "real upstream change"], check=True)
            new = subprocess.check_output(["git", "-C", str(tree), "rev-parse", "HEAD"], text=True).strip()
            (root / ".github").mkdir()
            key = "android16-6.12-2026-03/KernelSU-Next"
            (root / ".github/known-good.json").write_text(json.dumps({key: {
                "kernel_source_commit": old, "feature_set": "Perf",
            }}))
            report = delta.build_report(arguments(root, root / "delta.json"), {
                "KERNEL_SOURCE": "aosp", "KERNEL_SOURCE_COMMIT": new,
            })
            kernel = next(item for item in report["components"] if item["name"] == "Kernel source")
            self.assertEqual(kernel["history"], "available")
            self.assertEqual(kernel["commits"], [{"commit": new, "subject": "real upstream change"}])

    def test_clean_checkout_cli_writes_machine_readable_delta(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); output = root / "delta.json"
            args = arguments(root, output)
            command = [
                sys.executable, "-I", str(SCRIPTS / "build_delta.py"),
                "--workspace", str(root), "--output", str(output), "--file-name", args.file_name,
                "--family", args.family, "--os-patch-level", args.os_patch_level,
                "--variant", args.variant, "--feature-set", args.feature_set,
                "--nomount-commit", args.nomount_commit,
            ]
            env = dict(os.environ, KERNEL_SOURCE="aosp", KERNEL_SOURCE_COMMIT=NEW)
            result = subprocess.run(command, cwd=root, env=env, text=True,
                                    capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(output.read_text())["status"], "baseline_unavailable")


if __name__ == "__main__":
    unittest.main()
