import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".github" / "scripts"


class ReleaseNotesVersionTest(unittest.TestCase):
    def render(self, extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("USE_", "SUSFS_", "PINS_", "MODULES_"))}
        env.pop("GITHUB_STEP_SUMMARY", None)
        env.update({"ROOT_FLAVOR": "KernelSU-Next", "KERNEL_BUILD_VERSION": "6.12.x-android16",
                    "FEATURE_SET": "SUSFS+NoMount+Perf+BORE+ADIOS+VPN+USB+RK+AWL+SDP900",
                    "USE_PERF": "true", "USE_BORE": "true", "USE_ADIOS": "true",
                    "KERNELSU_COMMIT": "a" * 40, "NOMOUNT_COMMIT": "b" * 40, "VPNHIDE_COMMIT": "c" * 40})
        env.update(extra)
        res = subprocess.run([sys.executable, "-I", str(SCRIPTS / "render_kernel_notes.py")],
                             env=env, capture_output=True, text=True, check=True)
        return res.stdout

    def test_brand_versions_and_features_come_from_the_run(self):
        with tempfile.TemporaryDirectory() as d:
            pins = Path(d, "pins", "x-Pins")
            pins.mkdir(parents=True)
            (pins / "known-good-candidate.json").write_text(json.dumps({"k": {
                "file": "6.12.69-android16-2026-03-KernelSU-Next", "root_commit": "a" * 40,
                "root_version": "v3.4.2-214-gaaaaaaa-dirty", "root_version_code": "33555",
                "kernel_version_string": "6.12.69-android16-6-KRILIKD", "rekernel_commit": "d" * 40,
                "feature_set": "SUSFS+VPN"}}))
            nm = Path(d, "mods", "NoMount-Metamodule-v2.3.1-785-bbbbbbb")
            nm.mkdir(parents=True)
            (nm / "module.prop").write_text("id=nomount\nversion=v2.3.1 (785)\n")
            vh = Path(d, "mods", "VPNHide-Builtin-Module-v1.3.1-ccccccc")
            vh.mkdir(parents=True)
            (vh / "module.prop").write_text("id=vpnhide_builtin\nversion=v1.3.1\n")
            text = self.render({"BRAND_NAME": "KRILIKD", "PINS_DIR": str(Path(d, "pins")),
                                "MODULES_DIR": str(Path(d, "mods"))})
        self.assertTrue(text.startswith("# KRILIKD kernel for GKI2 devices"))
        self.assertNotIn("Wild Kernels", text)
        self.assertNotIn("WildKernels/GKI_KernelSU_SUSFS/issues", text)
        self.assertIn("`6.12.69-android16-6-KRILIKD`", text)
        self.assertIn("KernelSU-Next v3.4.2-214-gaaaaaaa (version code 33555) [", text)
        self.assertNotIn("-dirty", text)
        self.assertIn("NoMount v2.3.1 (785)", text)
        self.assertIn("VPN Hide (built-in) v1.3.1", text)
        for feature in ("USB Arsenal", "Re-Kernel [", "ASB wakeup blocker", "OnePlus SDP 900 mA"):
            self.assertIn(feature, text)

    def test_single_family_run_has_no_matrix_availability_block(self):
        text = self.render({"BRAND_NAME": "KRILIKD"})
        self.assertNotIn("## Build availability", text)


class BuildPresentationWiringTest(unittest.TestCase):
    def test_pins_carry_the_real_release_string_and_root_version_code(self):
        build = (ROOT / ".github/workflows/build.yml").read_text()
        self.assertIn("kernel_version_string: $kernel_version_string", build)
        self.assertIn("root_version_code: $root_version_code", build)
        self.assertIn("grep -m1 '^Linux version '", build)

    def test_kleaf_build_date_is_not_unix_zero(self):
        action = (ROOT / ".github/actions/build-kernel/action.yml").read_text()
        self.assertIn("TS_LINE='build-timestamp = $(or $(KBUILD_BUILD_TIMESTAMP), $(build-timestamp-auto))'", action)
        self.assertIn('"build-timestamp = ${KBUILD_BUILD_TIMESTAMP}"', action)
        self.assertNotIn("Reproducible timestamp injected by the CI workflow", action)

    def test_build_summary_reads_the_packaged_image(self):
        action = (ROOT / ".github/actions/build-summary/action.yml").read_text()
        self.assertIn('"${{ github.workspace }}/AnyKernel3/Image"', action)

    def test_companion_artifacts_name_their_version(self):
        main = (ROOT / ".github/workflows/main.yml").read_text()
        self.assertIn("name: ${{ steps.nmver.outputs.artifact || 'NoMount-Metamodule' }}", main)
        self.assertIn("name: ${{ steps.vhver.outputs.artifact || 'VPNHide-Builtin-Module' }}", main)
        self.assertNotIn('pattern: "*NoMount-Metamodule"\n', main)
        self.assertNotIn("release-assets/NoMount-Metamodule.zip", main)
        self.assertIn("path: notes-input/modules/nomount", main)
        self.assertIn("path: notes-input/modules/vpnhide", main)
        meta = (ROOT / ".github/actions/build-metadata/action.yml").read_text()
        self.assertIn('startswith($name + "-")', meta)

    def test_update_check_watches_rekernel_and_reports_the_sent_use_perf(self):
        uc = (SCRIPTS / "update_check.py").read_text()
        self.assertIn('"https://github.com/Sakion-Team/Re-Kernel.git", "refs/heads/main", "rekernel_commit"', uc)
        wf = (ROOT / ".github/workflows/update-check.yml").read_text()
        self.assertIn("use_perf=${USE_PERF_SENT}", wf)


if __name__ == "__main__":
    unittest.main()
