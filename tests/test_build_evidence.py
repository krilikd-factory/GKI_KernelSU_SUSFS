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
build_summary = load("build_summary")
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
            self.assertEqual(set(controls), {
                "mglru_mode", "kernel_timer_migration", "kernel_sched_nr_migrate",
                "kernel_sched_stats", "kernel_sched_autogroup", "kernel_sched_child_runs_first",
            })
            for name, control in controls.items():
                self.assertEqual(control["interface"], "standard_linux")
                self.assertEqual(control["runtime_state"], "unknown")
                self.assertEqual(control["writable"], "unknown")
                self.assertEqual(control["baseline"], "required_before_first_write")
                self.assertTrue(control["readback_required"])
                self.assertEqual(control["restore"], "exact_captured_value")
                self.assertTrue(control["runtime_path"].startswith((
                    "/proc/sys/kernel/", "/sys/kernel/mm/lru_gen/", "/sys/kernel/debug/sched/",
                )))
                self.assertIn("stock", control["requests"], name)
            self.assertEqual(controls["mglru_mode"]["build_state"], "available_in_config")
            self.assertEqual(controls["mglru_mode"]["requests"]["smart"], ["5", "7"])
            self.assertEqual(controls["kernel_sched_nr_migrate"]["runtime_path"], "/sys/kernel/debug/sched/nr_migrate")
            self.assertEqual(controls["kernel_sched_nr_migrate"]["requests"]["16"], ["16"])
            self.assertNotIn("32", controls["kernel_sched_nr_migrate"]["requests"])
            self.assertEqual(controls["kernel_sched_stats"]["runtime_path"], "/proc/sys/kernel/sched_schedstats")
            self.assertEqual(controls["kernel_sched_stats"]["requests"], {
                "stock": "restore the exact value captured before ASB's first write", "off": ["0"],
            })
            self.assertEqual(controls["kernel_sched_autogroup"]["runtime_path"], "/proc/sys/kernel/sched_autogroup_enabled")
            self.assertEqual(controls["kernel_sched_child_runs_first"]["runtime_path"], "/proc/sys/kernel/sched_child_runs_first")

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
        kconfig_source = embedded_source(
            ".github/actions/asb-wakeup-blocker/action.yml", "ASB_WAKEUP_KCONFIG")
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
            result = subprocess.run([sys.executable, "-c", kconfig_source], cwd=tree,
                                    text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((tree / "drivers/Kconfig").read_text().count(
                "config ASB_WAKEUP_BLOCKER"), 1)
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

    def test_wakeup_and_rekernel_compose_in_workflow_order(self):
        build = (ROOT / ".github/workflows/build.yml").read_text()
        wakeup_step = "      - name: Integrate ASB wakeup blocker\n"
        rekernel_step = "      - name: Integrate Re-Kernel\n"
        self.assertEqual(build.count(wakeup_step), 1)
        self.assertEqual(build.count(rekernel_step), 1)
        self.assertLess(build.index(wakeup_step), build.index(rekernel_step))

        patch = ROOT / ".github/patches/android16-6.12/asb-wakeup-blocker.patch"
        wakeup_source = embedded_source(
            ".github/actions/asb-wakeup-blocker/action.yml", "ASB_WAKEUP_KCONFIG")
        action = (ROOT / ".github/actions/rekernel/action.yml").read_text()
        marker = "python3 - \"$TREE\" \"$DST\" <<'PY'\n"
        rekernel_source = textwrap.dedent(
            action.split(marker, 1)[1].split("\n        PY", 1)[0]) + "\n"

        def prepare(tree):
            (tree / "drivers/base/power").mkdir(parents=True)
            # KernelSU-Next inserts its source immediately before endmenu.
            (tree / "drivers/Kconfig").write_text(
                'source "drivers/cdx/Kconfig"\n\nsource "drivers/dpll/Kconfig"\n\n'
                'source "drivers/kernelsu/Kconfig"\nendmenu\n')
            (tree / "drivers/base/power/Makefile").write_text(
                "# SPDX-License-Identifier: GPL-2.0\n"
                "obj-$(CONFIG_PM)\t+= sysfs.o generic_ops.o common.o qos.o runtime.o wakeirq.o\n"
                "obj-$(CONFIG_PM_SLEEP)\t+= main.o wakeup.o wakeup_stats.o\n"
                "obj-$(CONFIG_PM_TRACE_RTC)\t+= trace.o\n"
                "obj-$(CONFIG_HAVE_CLK)\t+= clock_ops.o\n"
                "obj-$(CONFIG_PM_QOS_KUNIT_TEST) += qos-test.o\n")
            (tree / "drivers/base/power/wakeup.c").write_text("\n".join([
                "#include <linux/irq.h>", "#include <linux/irqdesc.h>",
                "#include <linux/wakeup_reason.h>", "#include <trace/events/power.h>", "",
                "#include \"power.h\"", "", "void __pm_stay_awake(struct wakeup_source *ws)", "{",
                "\tunsigned long flags;", "", "\tif (!ws)", "\t\treturn;", "",
                "\tspin_lock_irqsave(&ws->lock, flags);", "}",
                "void pm_wakeup_ws_event(struct wakeup_source *ws, unsigned int msec, bool hard)", "{",
                "\tunsigned long flags;", "\tunsigned long expires;", "",
                "\tif (!ws)", "\t\treturn;", "", "\tspin_lock_irqsave(&ws->lock, flags);", "}", "",
            ]))

        def apply_wakeup(tree):
            result = subprocess.run(["git", "apply", str(patch)], cwd=tree,
                                    text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([sys.executable, "-c", wakeup_source], cwd=tree,
                                    text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)

        def integrate_rekernel(tree):
            dst = tree / "drivers/rekernel"
            dst.mkdir()
            (dst / "Makefile").write_text("obj-m := rekernel.o\nrekernel-y := main.o\n")
            (dst / "Kconfig").write_text(
                'config REKERNEL\n\tbool "Re:Kernel module"\n\tdepends on MODULES\n'
                '\thelp\n\t  Tips: Enabling this will build Re-Kernel as a module\n')
            (tree / "drivers/Makefile").write_text("obj-y += base/\n")
            result = subprocess.run(
                [sys.executable, "-c", rekernel_source, str(tree), str(dst)],
                text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)

        # KernelSU + AWL without RK must preserve the earlier driver entry.
        with tempfile.TemporaryDirectory() as d:
            tree = Path(d)
            prepare(tree)
            apply_wakeup(tree)
            kconfig = (tree / "drivers/Kconfig").read_text()
            self.assertEqual(kconfig.count('source "drivers/kernelsu/Kconfig"'), 1)
            self.assertIn("config ASB_WAKEUP_BLOCKER", kconfig)
            self.assertNotIn("drivers/rekernel/Kconfig", kconfig)

        # AWL followed by RK is the CI order: both exact integrations survive.
        with tempfile.TemporaryDirectory() as d:
            tree = Path(d)
            prepare(tree)
            apply_wakeup(tree)
            integrate_rekernel(tree)
            kconfig = (tree / "drivers/Kconfig").read_text()
            self.assertEqual(kconfig.count("config ASB_WAKEUP_BLOCKER"), 1)
            self.assertEqual(kconfig.count('source "drivers/rekernel/Kconfig"'), 1)
            self.assertLess(kconfig.index("config ASB_WAKEUP_BLOCKER"),
                            kconfig.index('source "drivers/rekernel/Kconfig"'))
            self.assertTrue(kconfig.endswith("\n\nendmenu\n"))
            makefile = (tree / "drivers/Makefile").read_text()
            self.assertIn("obj-$(CONFIG_REKERNEL) += rekernel/", makefile)

        # RK -> AWL also works: Kconfig integration no longer depends on order.
        with tempfile.TemporaryDirectory() as d:
            tree = Path(d)
            prepare(tree)
            integrate_rekernel(tree)
            apply_wakeup(tree)
            kconfig = (tree / "drivers/Kconfig").read_text()
            self.assertEqual(kconfig.count('source "drivers/kernelsu/Kconfig"'), 1)
            self.assertEqual(kconfig.count('source "drivers/rekernel/Kconfig"'), 1)
            self.assertEqual(kconfig.count("config ASB_WAKEUP_BLOCKER"), 1)
            self.assertTrue(kconfig.endswith("\n\nendmenu\n"))

    def test_wakeup_driver_includes_what_it_uses(self):
        # Run 117316696254 failed to compile: list_for_each_entry_rcu and
        # list_add_rcu/list_del_rcu live in <linux/rculist.h>, not <linux/list.h>.
        patch = (ROOT / ".github/patches/android16-6.12/asb-wakeup-blocker.patch").read_text()
        driver = [l[1:] for l in patch.split("b/drivers/base/power/asb_wakeup_blocker.c", 2)[2]
                  .split("diff --git", 1)[0].splitlines() if l.startswith("+") and not l.startswith("+++")]
        includes = {l.split("<", 1)[1].rstrip(">") for l in driver if l.startswith("#include <")}
        body = "\n".join(driver)
        needs = {"list_for_each_entry_rcu": "linux/rculist.h", "list_add_rcu": "linux/rculist.h",
                 "list_del_rcu": "linux/rculist.h", "kfree_rcu": "linux/rcupdate.h",
                 "static_branch_unlikely": "linux/jump_label.h", "atomic64_inc": "linux/atomic.h",
                 "mutex_lock": "linux/mutex.h", "kzalloc": "linux/slab.h", "isprint": "linux/ctype.h",
                 "sysfs_emit": "linux/sysfs.h", "kobject_create_and_add": "linux/kobject.h",
                 "strscpy": "linux/string.h"}
        for symbol, header in needs.items():
            self.assertIn(symbol, body)
            self.assertIn(header, includes, f"{symbol} needs <{header}>")
        self.assertIn("linux/asb_wakeup_blocker.h", includes)  # definition sees its own prototype
        header_hunk = [l for l in patch.splitlines() if l.startswith("@@ -0,0 +1,")]
        self.assertEqual(header_hunk[0], f"@@ -0,0 +1,{len(driver)} @@")

    def test_required_capabilities_match_the_plus_separated_feature_set(self):
        # feature_set is "A+B+C"; the old ",RK," test could never match, so the
        # fail-closed gate silently required nothing.
        build = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertNotIn('== *",', build)
        for token, capability in (("NET", "network.cake"), ("RK", "app.rekernel"),
                                  ("AWL", "power.wakeup_blocker"), ("SDP900", "charging.sdp_900ma")):
            self.assertIn(f'[[ "+${{{{ inputs.feature_set }}}}+" == *"+{token}+"* ]]', build)
            self.assertIn(f"--require {capability}", build)
        with tempfile.TemporaryDirectory() as d:
            config = Path(d) / "config"; out = Path(d) / "out.json"
            config.write_text("CONFIG_OPLUS_SDP_900MA=y\n")
            ok = subprocess.run([sys.executable, "-I", str(ROOT / ".github/scripts/kernel_capability_report.py"),
                                 "--config", str(config), "--feature-set", "SDP900", "--output", str(out),
                                 "--require", "charging.sdp_900ma"], text=True, capture_output=True)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            config.write_text("# CONFIG_OPLUS_SDP_900MA is not set\n")
            bad = subprocess.run([sys.executable, "-I", str(ROOT / ".github/scripts/kernel_capability_report.py"),
                                  "--config", str(config), "--feature-set", "SDP900", "--output", str(out),
                                  "--require", "charging.sdp_900ma"], text=True, capture_output=True)
            self.assertEqual(bad.returncode, 1)

    def test_sdp_900ma_is_vendored_gated_and_only_touches_the_sdp_limit(self):
        patch = (ROOT / ".github/patches/android16-6.12/oplus-sdp-900ma.patch").read_text()
        action = (ROOT / ".github/actions/oplus-sdp-900ma/action.yml").read_text()
        build = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertEqual(sorted(l[6:] for l in patch.splitlines() if l.startswith("+++ b/")),
                         ["drivers/misc/Kconfig", "drivers/misc/Makefile", "drivers/misc/oplus_sdp_900ma.c"])
        self.assertIn("early_initcall(oplus_sdp_900ma_init);", patch)
        self.assertIn('__setup("oplus_sdp_900ma=", oplus_sdp_900ma_setup);', patch)
        self.assertNotIn("EXPORT_SYMBOL", patch)   # nothing new for vendor modules to import
        self.assertIn("git apply --check", action)
        self.assertIn("CONFIG_OPLUS_SDP_900MA=y", action)
        step = "      - name: Integrate OPLUS SDP 900 mA\n"
        self.assertEqual(build.count(step), 1)
        gate = build.split(step, 1)[1].split("\n", 1)[0]
        self.assertIn("contains(inputs.feature_set, 'SDP900')", gate)
        self.assertIn("inputs.version == 'android16-6.12'", gate)
        self.assertEqual(hashlib.sha256((ROOT / ".github/patches/android16-6.12/oplus-sdp-900ma.patch").read_bytes()).hexdigest(),
                         "b73e2b64820342c4bb2063d4e59c7742c7b271d7d92b3521d3125fe112afab97")

    def test_sdp_patch_applies_to_minimal_source_and_rejects_duplicate_driver(self):
        patch = ROOT / ".github/patches/android16-6.12/oplus-sdp-900ma.patch"
        with tempfile.TemporaryDirectory() as d:
            tree = Path(d)
            (tree / "drivers/misc").mkdir(parents=True)
            (tree / "drivers/misc/Kconfig").write_text(
                "\nsource \"drivers/misc/c2port/Kconfig\"\n"
                "source \"drivers/misc/eeprom/Kconfig\"\n"
                "source \"drivers/misc/cb710/Kconfig\"\n")
            (tree / "drivers/misc/Makefile").write_text(
                "obj-$(CONFIG_VMWARE_VMCI)\t+= vmw_vmci/\n"
                "obj-$(CONFIG_LATTICE_ECP3_CONFIG)\t+= lattice-ecp3-config.o\n"
                "obj-$(CONFIG_SRAM)\t\t+= sram.o\n"
                "obj-$(CONFIG_SRAM_EXEC)\t\t+= sram-exec.o\n")
            checked = subprocess.run(["git", "apply", "--check", str(patch)], cwd=tree,
                                     text=True, capture_output=True, check=False)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            subprocess.run(["git", "apply", str(patch)], cwd=tree, check=True)
            driver = tree / "drivers/misc/oplus_sdp_900ma.c"
            self.assertTrue(driver.is_file())
            self.assertIn("early_initcall(oplus_sdp_900ma_init);", driver.read_text())
            self.assertIn('__setup("oplus_sdp_900ma=", oplus_sdp_900ma_setup);', driver.read_text())
            self.assertEqual((tree / "drivers/misc/Kconfig").read_text().count("config OPLUS_SDP_900MA"), 1)
            duplicate = subprocess.run(["git", "apply", "--check", str(patch)], cwd=tree,
                                       text=True, capture_output=True, check=False)
            self.assertNotEqual(duplicate.returncode, 0)
        action = (ROOT / ".github/actions/oplus-sdp-900ma/action.yml").read_text()
        self.assertIn("[ ! -e drivers/misc/oplus_sdp_900ma.c ]", action)
        self.assertIn('[ "${{ inputs.version }}" = "android16-6.12" ]', action)

    def test_wakeup_kconfig_anchor_is_fail_closed(self):
        source = embedded_source(
            ".github/actions/asb-wakeup-blocker/action.yml", "ASB_WAKEUP_KCONFIG")
        with tempfile.TemporaryDirectory() as d:
            tree = Path(d)
            (tree / "drivers").mkdir()
            kconfig = tree / "drivers/Kconfig"
            kconfig.write_text(
                'source "drivers/kernelsu/Kconfig"\n'
                'source "drivers/rekernel/Kconfig"\nendmenu\n')
            result = subprocess.run([sys.executable, "-c", source], cwd=tree,
                                    text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            integrated = kconfig.read_text()
            self.assertLess(integrated.index('source "drivers/kernelsu/Kconfig"'),
                            integrated.index("config ASB_WAKEUP_BLOCKER"))
            self.assertLess(integrated.index('source "drivers/rekernel/Kconfig"'),
                            integrated.index("config ASB_WAKEUP_BLOCKER"))

            duplicate = subprocess.run([sys.executable, "-c", source], cwd=tree,
                                       text=True, capture_output=True, check=False)
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertIn("duplicate ASB wakeup Kconfig entry", duplicate.stderr)
            self.assertEqual(kconfig.read_text(), integrated)

            kconfig.write_text('source "drivers/dpll/Kconfig"\nendmenu')
            bad_anchor = subprocess.run([sys.executable, "-c", source], cwd=tree,
                                        text=True, capture_output=True, check=False)
            self.assertNotEqual(bad_anchor.returncode, 0)
            self.assertIn("final menu anchor changed", bad_anchor.stderr)
            self.assertEqual(kconfig.read_text(), 'source "drivers/dpll/Kconfig"\nendmenu')


class OnePlusPolicyTest(unittest.TestCase):
    expected = {
        "CONFIG_POSIX_MQUEUE": "n",
        "CONFIG_DEVTMPFS": "n",
        "CONFIG_DEVTMPFS_MOUNT": "n",
        "CONFIG_DEBUG_FS": "y",
        "CONFIG_SCHED_DEBUG": "y",
    }

    @staticmethod
    def line(key, value):
        return f"# {key} is not set\n" if value == "n" else f"{key}={value}\n"

    def run_guard(self, values):
        with tempfile.TemporaryDirectory() as d:
            config = Path(d) / ".config"
            report = Path(d) / "report.txt"
            config.write_text("".join(self.line(k, v) for k, v in values.items()))
            result = subprocess.run([
                sys.executable, "-I", str(SCRIPTS / "op15_config_guard.py"),
                "--config", str(config), "--report", str(report),
            ], text=True, capture_output=True, check=False)
            return result, report.read_text()

    def test_all_five_symbols_are_checked_after_final_kconfig_resolution(self):
        result, report = self.run_guard(self.expected)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("policy verified", result.stdout)
        self.assertIn("CONFIG_SCHED_AUTOGROUP: not forced", report)
        for key in self.expected:
            for wrong in ("y", "m", "missing"):
                if wrong == self.expected[key]:
                    continue
                with self.subTest(key=key, wrong=wrong):
                    values = dict(self.expected)
                    if wrong == "missing":
                        del values[key]
                    else:
                        values[key] = wrong
                    result, _ = self.run_guard(values)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(key, result.stderr)

    def test_policy_is_late_scoped_and_has_no_autogroup_or_missing_config_escape(self):
        build = (ROOT / ".github/workflows/build.yml").read_text()
        action = (ROOT / ".github/actions/build-kernel/action.yml").read_text()
        policy = build.split("      - name: Apply OnePlus 15 integrity and scheduler config\n", 1)[1]
        policy = policy.split("      - name: Apply Kernel Branding\n", 1)[0]
        for key, value in self.expected.items():
            self.assertIn(f"{key}={value}", policy)
        self.assertNotIn("CONFIG_SCHED_AUTOGROUP", policy)
        self.assertIn("if: inputs.version == 'android16-6.12'", policy)
        self.assertLess(build.index("name: DroidSpaces-OSS Patches"),
                        build.index("name: Apply OnePlus 15 integrity and scheduler config"))
        self.assertLess(build.index("name: Apply OnePlus 15 integrity and scheduler config"),
                        build.index("name: Build Normal Kernel"))
        self.assertIn("op15_config_guard.py", action)
        self.assertIn("op15-config-report.txt", action)
        self.assertIn('[ "${{ inputs.version }}" != "android16-6.12" ] && [ "$PAGE_SIZE" != "16k" ]', action)
        self.assertIn("op15-config-report.txt", build)

    def test_stock_guard_allows_only_the_three_explicit_op15_transitions(self):
        with tempfile.TemporaryDirectory() as d:
            stock = Path(d) / "stock.config"
            final = Path(d) / "final.config"
            report = Path(d) / "stock-report.txt"
            stock.write_text("".join(f"{key}=y\n" for key in (
                "CONFIG_POSIX_MQUEUE", "CONFIG_DEVTMPFS", "CONFIG_DEVTMPFS_MOUNT", "CONFIG_OTHER")))
            final.write_text("".join(self.line(key, "n") for key in (
                "CONFIG_POSIX_MQUEUE", "CONFIG_DEVTMPFS", "CONFIG_DEVTMPFS_MOUNT")) + "CONFIG_OTHER=y\n")
            command = [sys.executable, "-I", str(SCRIPTS / "stock_config_guard.py"),
                       "--stock", str(stock), "--final", str(final), "--report", str(report)]
            rejected = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(rejected.returncode, 1)
            allowed = subprocess.run(command + [
                "--allow-n", "CONFIG_POSIX_MQUEUE", "--allow-n", "CONFIG_DEVTMPFS",
                "--allow-n", "CONFIG_DEVTMPFS_MOUNT",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertIn("3 explicit y/m -> n overrides", report.read_text())
            final.write_text(final.read_text().replace("CONFIG_OTHER=y", "# CONFIG_OTHER is not set"))
            unrelated = subprocess.run(command + [
                "--allow-n", "CONFIG_POSIX_MQUEUE", "--allow-n", "CONFIG_DEVTMPFS",
                "--allow-n", "CONFIG_DEVTMPFS_MOUNT",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(unrelated.returncode, 1)
            self.assertIn("CONFIG_OTHER", unrelated.stdout)
        action = (ROOT / ".github/actions/build-kernel/action.yml").read_text()
        self.assertEqual(action.count("--allow-n CONFIG_"), 3)
        for key in ("CONFIG_POSIX_MQUEUE", "CONFIG_DEVTMPFS", "CONFIG_DEVTMPFS_MOUNT"):
            self.assertIn(f"--allow-n {key}", action)

    def test_nr_migrate_requires_debugfs_and_scheduler_debug_together(self):
        with tempfile.TemporaryDirectory() as d:
            config = Path(d) / ".config"
            config.write_text("CONFIG_SCHED_DEBUG=y\n")
            control = caps.make_report(config, "")["asb_contract"]["controls"]["kernel_sched_nr_migrate"]
            self.assertEqual(control["build_state"], "not_available_in_config")
            self.assertEqual(control["configs"]["CONFIG_DEBUG_FS"], "not_exposed")
            config.write_text("CONFIG_DEBUG_FS=y\nCONFIG_SCHED_DEBUG=y\n")
            control = caps.make_report(config, "")["asb_contract"]["controls"]["kernel_sched_nr_migrate"]
            self.assertEqual(control["build_state"], "available_in_config")
            self.assertEqual(control["runtime_path"], "/sys/kernel/debug/sched/nr_migrate")
            self.assertEqual(control["runtime_state"], "unknown")
        self.assertFalse((ROOT / ".github/kernel_capability_report.py").exists())
        stale_path = "/proc/sys/kernel/" + "sched_nr_migrate"
        for path in ROOT.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path != Path(__file__):
                try:
                    self.assertNotIn(stale_path, path.read_text(encoding="utf-8"))
                except UnicodeDecodeError:
                    pass

    def test_default_kernel_identity_is_krilikd_not_wild(self):
        main = (ROOT / ".github/workflows/main.yml").read_text()
        build = (ROOT / ".github/workflows/build.yml").read_text()
        branding = (ROOT / ".github/actions/apply-kernel-branding/action.yml").read_text()
        self.assertEqual(main.count("brand_name: ${{ inputs.brand_name || 'KRILIKD' }}"), 8)
        self.assertIn("default: KRILIKD", main)
        self.assertIn("default: KRILIKD", build)
        self.assertIn("default: KRILIKD", branding)
        self.assertNotIn("OP-WILD", main + build + branding)
        self.assertNotRegex(main + build + branding, r"default:\s*['\"]?WILD")
        self.assertFalse((ROOT / "configs/a16/OP15.json").exists())


class VpnHideFailClosedTest(unittest.TestCase):
    def test_requested_vpnhide_cannot_silently_skip_or_change_commit(self):
        action = (ROOT / ".github/actions/vpnhide/action.yml").read_text()
        for forbidden in ("mode=skipped", 'echo "ok=false"', "WITHOUT VPN Hide", "VPN Hide skipped"):
            self.assertNotIn(forbidden, action)
        for required in (
            '[[ "$WANT" =~ ^[0-9a-f]{40}$ ]] && [ "$SHA" != "$WANT" ]',
            'echo "::error::vpnhide: requested, but could not fetch $WANT"',
            'echo "::error::vpnhide: Python >= 3.12 is required for the requested integration"',
            "full|net-only)",
            "exit 1",
        ):
            self.assertIn(required, action)

    def run_integrator(self, mode):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            scripts = root / "vpnhide/builtin/scripts"
            scripts.mkdir(parents=True)
            (root / "kernel").mkdir()
            (scripts / "integrate.py").write_text(textwrap.dedent(f"""
                from pathlib import Path
                EDITS = {{"android16-6.12": {{"fs/namei.c": object(), "net/route.c": object()}}}}
                def integrate(kernel, kmi, output, apply):
                    output.mkdir(parents=True, exist_ok=True)
                    mode = {mode!r}
                    if mode == "fail" or (mode == "net-only" and output.name == "full"):
                        raise ValueError("fixture anchor mismatch")
            """))
            result = subprocess.run([
                sys.executable, "-I", str(SCRIPTS / "vpnhide_integrate.py"),
                "--vpnhide", str(root / "vpnhide"), "--kernel", str(root / "kernel"),
                "--kmi", "android16-6.12", "--output", str(root / "review"),
            ], text=True, capture_output=True, check=False)
            return result

    def test_integrator_full_net_only_and_failure_behaviour(self):
        full = self.run_integrator("full")
        self.assertEqual(full.returncode, 0, full.stderr)
        self.assertIn("RESULT=full", full.stdout)
        net = self.run_integrator("net-only")
        self.assertEqual(net.returncode, 0, net.stderr)
        self.assertIn("RESULT=net-only", net.stdout)
        failed = self.run_integrator("fail")
        self.assertEqual(failed.returncode, 1)
        self.assertNotIn("RESULT=", failed.stdout)


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

    def test_tracked_build_summary_runs_from_a_clean_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            checkout = Path(d)
            output = checkout / "summary.md"
            result = subprocess.run([
                sys.executable, "-I", str(SCRIPTS / "build_summary.py"),
                "--workspace", str(checkout),
                "--output", str(output), "--job-status", "failure",
                "--file-name", "krilikd-test", "--kernel-version", "6.12",
                "--sublevel", "69", "--android-version", "android16",
                "--os-patch-level", "2026-03", "--variant", "KernelSU-Next",
                "--page-size", "4k", "--feature-set", "Perf",
                "--family", "android16-6.12",
            ], cwd=checkout, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            report = output.read_text()
            self.assertIn("❌ Failed", report)
            self.assertIn("Patch inventory: not generated", report)
            self.assertIn("Source preflight evidence unavailable: not generated", report)
            self.assertIn("does not create a second summary failure", report)
            self.assertIn("Build-verified only", report)
            self.assertIn("Feature behavior, power and performance", report)

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

    def test_build_summary_exposes_oneplus_source_dependency_and_complete_guides(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".github").mkdir()
            (root / ".github/device.json").write_text((ROOT / ".github/device.json").read_text())
            commit = "a" * 40
            provenance = {
                "schema": 1, "stage": "downloaded-source-preflight", "source": "oneplus",
                "family": "android16-6.12", "commit": commit, "kernel": "6.12.69",
                "patch_level": "2026-03", "source_digest": "d" * 64,
                "git_clean_before_features": True,
                "oem": {"repo": "https://github.com/OnePlusOSS/android_kernel_common_oneplus_sm8850",
                        "branch": "oneplus/sm8850_test_oneplus_15"},
                "base_manifest": {"path": "kernel/.repo/manifests/default.xml", "sha256": "m" * 64, "bytes": 123},
                "files": {"Makefile": {"sha256": "f" * 64, "bytes": 456}},
                "scope": "source identity and selected build inputs; not boot or runtime validation",
            }
            (root / "source-provenance.json").write_text(json.dumps(provenance))
            args = build_summary.parser().parse_args([
                "--workspace", str(root), "--output", str(root / "out.md"), "--job-status", "success",
                "--file-name", "krilikd-test", "--kernel-version", "6.12", "--sublevel", "69",
                "--android-version", "android16", "--os-patch-level", "2026-03",
                "--feature-set", "NET+RK+AWL+SDP900+Perf", "--family", "android16-6.12",
            ])
            text = build_summary.render(args, {"KERNEL_SOURCE": "oneplus", "KERNEL_SOURCE_COMMIT": commit})
            for needle in (
                "### 🌳 Kernel source and OnePlus dependency", "fail-closed actual-tree preflight",
                "https://github.com/OnePlusOSS/android_kernel_common_oneplus_sm8850",
                "oneplus/sm8850_test_oneplus_15", commit, "Actual Makefile kernel",
                "oneplus/sm8850_*_oneplus_15", "Stock minimum sublevel", "Stock minimum firmware patch",
                "Clean tree before features", "Source identity digest", "Prefeature source file",
                "stock `/proc/config.gz` compatibility payload", "not boot or runtime validation",
                "Re-Kernel", "ASB wakeup blocker", "OPLUS SDP 900 mA", "Main Performance pass",
                "kernel_sched_nr_migrate", "/sys/kernel/debug/sched/nr_migrate",
                "kernel_sched_stats", "/proc/sys/kernel/sched_schedstats", "Stock is inert before a baseline exists",
            ):
                self.assertIn(needle, text)
            self.assertNotIn("physical boot verified", text.lower())
            self.assertNotIn("guaranteed battery improvement", text.lower())

            mismatch = build_summary.render(args, {"KERNEL_SOURCE": "oneplus", "KERNEL_SOURCE_COMMIT": "b" * 40})
            self.assertIn("resolver/actual commit", mismatch)
            self.assertIn("summary identity mismatch", mismatch)

            provenance["source"] = "aosp"
            provenance["oem"] = None
            (root / "source-provenance.json").write_text(json.dumps(provenance))
            aosp = build_summary.render(args, {"KERNEL_SOURCE": "aosp", "KERNEL_SOURCE_COMMIT": commit})
            self.assertIn("not applicable (AOSP common)", aosp)
            self.assertIn("AOSP source gate", aosp)

    def test_build_summary_missing_malformed_and_incomplete_provenance_never_overclaim(self):
        for payload in (None, "{broken", {}):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as d:
                root = Path(d)
                if payload is not None:
                    (root / "source-provenance.json").write_text(
                        payload if isinstance(payload, str) else json.dumps(payload))
                args = build_summary.parser().parse_args([
                    "--workspace", str(root), "--output", str(root / "out.md"), "--job-status", "failure",
                    "--file-name", "krilikd-test", "--kernel-version", "6.12", "--sublevel", "69",
                    "--android-version", "android16", "--os-patch-level", "2026-03",
                    "--feature-set", "", "--family", "android16-6.12",
                ])
                text = build_summary.render(args, {"KERNEL_SOURCE": "oneplus", "KERNEL_SOURCE_COMMIT": "a" * 40})
                self.assertIn("Source preflight evidence unavailable", text)
                self.assertIn("No OnePlus/AOSP identity pass is inferred", text)
                self.assertNotIn("✅ Recorded before feature patches", text)
                self.assertNotIn("✅ preflight passed", text)

    def test_performance_pass_is_saved_for_buildinfo(self):
        performance = (ROOT / ".github/actions/performance/action.yml").read_text()
        setup = (ROOT / ".github/actions/setup-build-environment/action.yml").read_text()
        build = (ROOT / ".github/workflows/build.yml").read_text()
        summary = (ROOT / ".github/actions/build-summary/action.yml").read_text()
        self.assertIn("> /tmp/performance-patches-summary.md", performance)
        self.assertNotIn(".github/scripts/rebrand_anykernel.py", setup)
        self.assertTrue((SCRIPTS / "build_summary.py").is_file())
        for needle in ("python3 -I -", "KRILIKD_REBRAND_BEGIN", "KRILIKD_REBRAND_END",
                       '--feature-set "${{ inputs.feature_set }}"', '--page-size "${{ inputs.page_size }}"'):
            self.assertIn(needle, setup)
        self.assertIn('python3 -I "${{ github.workspace }}/.github/scripts/build_summary.py"', summary)
        self.assertNotIn("KRILIKD_BUILD_SUMMARY_BEGIN", summary)
        for needle in ("family: ${{ inputs.version }}", "droidspaces_commit: ${{ inputs.droidspaces_commit }}",
                       "kernel_patches_commit: ${{ inputs.kernel_patches_commit }}",
                       "anykernel3_commit: ${{ inputs.anykernel3_commit }}"):
            self.assertIn(needle, build)
        self.assertLess(build.index("name: Apply Performance Patches"), build.index("name: Consolidated Build Summary"))
        self.assertIn("${{ github.workspace }}/patch-inventory.json", build)
        self.assertIn('--family "${{ inputs.family }}"', summary)
        self.assertIn('ap.add_argument("--performance-report", default="/tmp/performance-patches-summary.md")',
                      (SCRIPTS / "build_summary.py").read_text())


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
        self.assertEqual(inputs["compatibility_patches"]["default"], "both")
        self.assertEqual(inputs["compatibility_patches"]["options"], ["both", "ptrace", "unicode", "none"])
        self.assertEqual(inputs["power_app_features"]["default"], "all")
        self.assertEqual(inputs["power_app_features"]["options"],
                         ["all", "both", "rekernel", "wakeup-blocker", "sdp-900ma", "none"])

        text = path.read_text(encoding="utf-8")
        for token in ('case "${{ inputs.compatibility_patches }}" in',
                      'case "${{ inputs.power_app_features }}" in',
                      'ptrace)  F_PTRACE=true;  F_UNICODE=false',
                      'unicode) F_PTRACE=false; F_UNICODE=true',
                      'all)            F_RK=true;  F_AWL=true;  F_SDP=true',
                      'both)           F_RK=true;  F_AWL=true;  F_SDP=false',
                      'rekernel)       F_RK=true;  F_AWL=false; F_SDP=false',
                      'wakeup-blocker) F_RK=false; F_AWL=true;  F_SDP=false',
                      'sdp-900ma)      F_RK=false; F_AWL=false; F_SDP=true',
                      'none)           F_RK=false; F_AWL=false; F_SDP=false',
                      '[ "$F_SDP" = "true" ] && FEATURES="${FEATURES}SDP900+"'):
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
