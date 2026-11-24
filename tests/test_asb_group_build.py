import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "asb_group_build", Path(__file__).resolve().parents[1] / ".github/scripts/asb_group_build.py")
subject = importlib.util.module_from_spec(spec)
spec.loader.exec_module(subject)


class GroupBuildTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "ASB_TARGET": "OnePlus 12 · android14-6.1.157", "ASB_PAGE_SIZE": "4k",
            "TG_TOKEN": "test-token", "TG_TOPIC": "3042", "TG_CHAT": "@AutoSystemBoost",
            "GITHUB_REPOSITORY": "owner/repo", "GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_missing_topic_fails_before_dispatch(self):
        os.environ["TG_TOPIC"] = ""
        with patch.object(subject.subprocess, "run") as dispatch:
            with self.assertRaises(RuntimeError):
                subject.main()
            dispatch.assert_not_called()

    def test_failed_build_never_publishes(self):
        result = [{"databaseId": 23, "displayTitle": "ASB-group-42-1",
                   "status": "completed", "conclusion": "failure"}]
        with patch.object(subject.subprocess, "run"), \
             patch.object(subject, "run", return_value=json.dumps(result)), \
             patch.object(subject, "telegram") as send:
            with self.assertRaisesRegex(RuntimeError, "failure"):
                subject.main()
            send.assert_not_called()

    def test_missing_artifacts_never_publishes(self):
        build = [{"databaseId": 23, "displayTitle": "ASB-group-42-1",
                  "status": "completed", "conclusion": "success"}]
        with patch.object(subject.subprocess, "run"), \
             patch.object(subject, "run", side_effect=[json.dumps(build), '{"artifacts": []}']), \
             patch.object(subject, "telegram") as send:
            with self.assertRaisesRegex(RuntimeError, "artifact"):
                subject.main()
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
