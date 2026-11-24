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

    def test_successful_build_publishes_selected_artifacts(self):
        build = [{"databaseId": 23, "displayTitle": "ASB-group-42-1",
                  "status": "completed", "conclusion": "success"}]
        artifacts = {"artifacts": [
            {"name": "kernel-AnyKernel3", "expired": False},
            {"name": "KernelSU-Next.apk", "expired": False},
        ]}
        calls = []

        def fake_run(*args):
            calls.append(args)
            if args[:3] == ("gh", "run", "list"):
                return json.dumps(build)
            if args[:2] == ("gh", "api"):
                return json.dumps(artifacts)
            if args[:3] == ("gh", "run", "download"):
                directory = Path(args[args.index("--dir") + 1])
                (directory / "payload").write_bytes(b"artifact")
                return ""
            if args[0] == "curl":
                return '{"ok": true, "result": {}}'
            raise AssertionError(args)

        def fake_subprocess(args, **kwargs):
            if args[0] == "zip":
                Path(args[2]).write_bytes(b"zip")
            return None

        with patch.object(subject, "run", side_effect=fake_run), \
             patch.object(subject.subprocess, "run", side_effect=fake_subprocess) as dispatch, \
             patch.object(subject, "telegram") as send:
            subject.main()
        self.assertEqual(send.call_count, 1)
        dispatch_args = dispatch.call_args_list[0].args[0]
        self.assertIn("root_flavor=KernelSU-Next", dispatch_args)
        self.assertIn("kernel_build_version=6.1.x-android14", dispatch_args)
        self.assertEqual(sum(args[0] == "curl" for args in calls), 2)

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
