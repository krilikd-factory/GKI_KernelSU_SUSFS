import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class StockSourceBaselineTest(unittest.TestCase):
    def test_guard_reads_original_source_after_integration_commit(self):
        action = (ROOT / ".github/actions/build-kernel/action.yml").read_text()
        self.assertIn('STOCK_REF="${KERNEL_SOURCE_COMMIT:-HEAD}"', action)
        self.assertIn('git -C common show "${STOCK_REF}:arch/arm64/configs/gki_defconfig"', action)
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / "common"
            repo.mkdir()
            env = dict(os.environ, GIT_AUTHOR_NAME="Test", GIT_AUTHOR_EMAIL="test@example.invalid",
                       GIT_COMMITTER_NAME="Test", GIT_COMMITTER_EMAIL="test@example.invalid")
            def git(*args):
                return subprocess.check_output(["git", "-C", str(repo), *args], env=env, text=True)
            git("init", "-q")
            config = repo / "arch/arm64/configs/gki_defconfig"
            config.parent.mkdir(parents=True)
            config.write_text("CONFIG_INET=y\n")
            git("add", ".")
            git("commit", "-qm", "source")
            original = git("rev-parse", "HEAD").strip()
            config.write_text("CONFIG_INET=y\nCONFIG_NETFS_SUPPORT=y\n")
            git("add", ".")
            git("commit", "-qm", "integration")
            source = git("show", original + ":arch/arm64/configs/gki_defconfig")
            current = git("show", "HEAD:arch/arm64/configs/gki_defconfig")
            self.assertNotIn("CONFIG_NETFS_SUPPORT", source)
            self.assertIn("CONFIG_NETFS_SUPPORT=y", current)
            baseline = Path(directory) / "baseline"
            final = Path(directory) / "final"
            baseline.write_text(source)
            final.write_text("CONFIG_INET=y\n")
            guard = ROOT / ".github/scripts/stock_config_guard.py"
            good = subprocess.run(["python3", str(guard), "--stock", str(baseline), "--final", str(final)],
                                  capture_output=True, text=True)
            self.assertEqual(good.returncode, 0)
            baseline.write_text(current)
            bad = subprocess.run(["python3", str(guard), "--stock", str(baseline), "--final", str(final)],
                                 capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("CONFIG_NETFS_SUPPORT", bad.stdout + bad.stderr)


if __name__ == "__main__":
    unittest.main()
