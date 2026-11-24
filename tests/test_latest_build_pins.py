import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / ".github" / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("latest_build_pins", SCRIPTS / "latest_build_pins.py")
latest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(latest)


class LatestPinsTest(unittest.TestCase):
    def test_selects_successful_matching_feature_set(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = Path.cwd()
            os.chdir(directory)
            try:
                Path(".github").mkdir()
                Path(".github/known-good.json").write_text("{}")
                runs = [
                    {"databaseId": 3, "status": "completed", "conclusion": "failure"},
                    {"databaseId": 2, "status": "completed", "conclusion": "success"},
                    {"databaseId": 1, "status": "completed", "conclusion": "success"},
                ]

                def fake_gh(*args):
                    return runs if args[:2] == ("run", "list") else {
                        "artifacts": [{"name": "kernel-Pins", "expired": False}]}

                def download(args, **kwargs):
                    run_id = args[3]
                    folder = Path(args[args.index("--dir") + 1])
                    feature_set = "other" if run_id == "2" else "SUSFS"
                    (folder / "known-good-candidate.json").write_text(
                        json.dumps({"key": {"feature_set": feature_set, "bbg_commit": "a" * 40}}))
                    return type("Result", (), {"returncode": 0})()

                with patch.dict(os.environ, {"GITHUB_REPOSITORY": "owner/repo"}), \
                     patch.object(latest.update_check, "select_automatic_candidate_composition",
                                  return_value=("key", "SUSFS", {})), \
                     patch.object(latest, "gh", side_effect=fake_gh), \
                     patch.object(latest.subprocess, "run", side_effect=download):
                    self.assertEqual(latest.main(), 0)
                pins = json.loads(Path("latest-built-pins.json").read_text())
                self.assertEqual(pins["key"]["feature_set"], "SUSFS")
                self.assertEqual(pins["key"]["bbg_commit"], "a" * 40)
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
