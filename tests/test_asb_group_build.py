import importlib.util
import json
import os
import subprocess
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

    def test_github_read_retry_does_not_repeat_telegram_sends(self):
        error = subprocess.CalledProcessError(1, ["gh"])
        good = subprocess.CompletedProcess(["gh"], 0, stdout="ok", stderr="")
        with patch.object(subject.subprocess, "run", side_effect=[error, error, good]) as command, \
             patch.object(subject.time, "sleep"):
            self.assertEqual(subject.run("gh", "api", "example"), "ok")
            self.assertEqual(command.call_count, 3)
        failed_send = subprocess.CalledProcessError(22, ["curl", "secret-url"],
                                                    output='{"description":"Bad Request"}')
        with patch.object(subject.subprocess, "run", side_effect=failed_send) as command:
            with self.assertRaisesRegex(RuntimeError, "Bad Request") as failure:
                subject.run("curl", "secret-url")
            self.assertNotIn("secret-url", str(failure.exception))
            self.assertEqual(command.call_count, 1)

    def test_missing_topic_fails_before_dispatch(self):
        os.environ["TG_TOPIC"] = ""
        with patch.object(subject.subprocess, "run") as dispatch:
            with self.assertRaises(RuntimeError):
                subject.main()
            dispatch.assert_not_called()

    def test_failed_build_never_publishes(self):
        result = [{"databaseId": 23, "displayTitle": "ASB-group-42-1-6.1.x-android14-KernelSU-Next",
                   "status": "completed", "conclusion": "failure"}]
        with patch.object(subject.subprocess, "run"), \
             patch.object(subject, "run", return_value=json.dumps(result)), \
             patch.object(subject, "telegram") as send:
            with self.assertRaisesRegex(RuntimeError, "failure"):
                subject.main()
            send.assert_not_called()

    def test_successful_build_publishes_selected_artifacts(self):
        build = [{"databaseId": 23, "displayTitle": "ASB-group-42-1-6.1.x-android14-KernelSU-Next",
                  "status": "completed", "conclusion": "success"}]
        artifacts = {"artifacts": [
            {"name": "kernel-KRILIKD-AnyKernel3", "expired": False},
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

    def test_group_publishes_three_kernels_and_one_copy_of_common_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as work:
            old = Path.cwd()
            os.chdir(work)
            try:
                for i, target in enumerate(subject.TARGETS):
                    folder = Path("build-references") / str(i)
                    folder.mkdir(parents=True)
                    (folder / "asb-build-reference.json").write_text(
                        json.dumps({"run_id": i + 1, "target": target, "page_size": "4k"}))
                artifacts = {"artifacts": [
                    {"name": "kernel-KRILIKD-AnyKernel3", "expired": False},
                    {"name": "KernelSU-Next.apk", "expired": False},
                    {"name": "NoMount-example", "expired": False},
                    {"name": "VPNHide-example", "expired": False},
                ]}
                calls = []
                def fake_run(*args):
                    calls.append(args)
                    if args[:2] == ("gh", "api"):
                        run_id = args[2].split("/")[-2]
                        data = json.loads(json.dumps(artifacts))
                        data["artifacts"][0]["name"] = run_id + "-KRILIKD-AnyKernel3"
                        return json.dumps(data)
                    if args[:3] == ("gh", "run", "download"):
                        folder = Path(args[args.index("--dir") + 1])
                        (folder / "payload").write_bytes(b"data")
                        return ""
                    if args[0] == "curl":
                        media = json.loads(args[args.index("media=" + next(
                            item[6:] for item in args if item.startswith("media=")))][6:])
                        self.assertEqual(len(media), 6)
                        return json.dumps({"ok": True, "result": [None] * len(media)})
                    raise AssertionError(args)
                def fake_subprocess(args, **kwargs):
                    if args[0] == "zip":
                        Path(args[2]).write_bytes(b"zip")
                with patch.object(subject, "run", side_effect=fake_run), \
                     patch.object(subject.subprocess, "run", side_effect=fake_subprocess):
                    subject.publish_group()
                downloads = [args for args in calls if args[:3] == ("gh", "run", "download")]
                self.assertEqual(downloads[0][3], "3", "OnePlus 15/15R must publish first")
                self.assertEqual(sum(args[0] == "curl" for args in calls), 1)
                self.assertEqual(sum(args[:3] == ("gh", "run", "download") for args in calls), 6)
            finally:
                os.chdir(old)

    def test_multi_root_group_pairs_each_kernel_with_one_manager(self):
        import tempfile
        with tempfile.TemporaryDirectory() as work:
            old = Path.cwd(); os.chdir(work)
            try:
                roots = ("KernelSU-Next", "KernelSU")
                lookup = {}
                for i, (target, root) in enumerate(((t, r) for t in reversed(subject.TARGET_ORDER) for r in roots)):
                    run_id = str(i + 1); lookup[run_id] = (target, root)
                    folder = Path("build-references") / run_id; folder.mkdir(parents=True)
                    (folder / "asb-build-reference.json").write_text(json.dumps({"run_id": run_id, "target": target, "page_size": "4k", "root_flavor": root}))
                downloads, captions = [], []
                def fake_run(*args):
                    if args[:2] == ("gh", "api"):
                        run_id = args[2].split("/")[-2]; target, root = lookup[run_id]
                        names = [run_id + "-" + root + "-KRILIKD-AnyKernel3", root + ".apk", "NoMount-example", "VPNHide-example"]
                        return json.dumps({"artifacts": [{"name": name, "expired": False} for name in names]})
                    if args[:3] == ("gh", "run", "download"):
                        downloads.append((args[3], args[args.index("--name") + 1]))
                        (Path(args[args.index("--dir") + 1]) / "payload").write_bytes(b"data")
                        return ""
                    if args[0] == "curl":
                        raw = next(x[6:] for x in args if x.startswith("media="))
                        media = json.loads(raw); captions.append(media[0]["caption"])
                        return json.dumps({"ok": True, "result": [None] * len(media)})
                    raise AssertionError(args)
                def fake_subprocess(args, **kwargs):
                    if args[0] == "zip": Path(args[2]).write_bytes(b"zip")
                with patch.object(subject, "run", side_effect=fake_run), patch.object(subject.subprocess, "run", side_effect=fake_subprocess):
                    subject.publish_group()
                self.assertEqual(lookup[downloads[0][0]][0], subject.TARGET_ORDER[0])
                self.assertEqual(lookup[downloads[0][0]][1], "KernelSU-Next")
                names = [n for _, n in downloads]
                self.assertEqual(sum(n.endswith("-AnyKernel3") for n in names), 6)
                self.assertEqual(names.count("KernelSU-Next.apk"), 1)
                self.assertEqual(names.count("KernelSU.apk"), 1)
                self.assertEqual(names.count("NoMount-example"), 1)
                self.assertIn("KernelSU-Next, KernelSU", captions[0])
            finally:
                os.chdir(old)

    def test_matrix_selects_every_root_and_device_in_priority_order(self):
        import contextlib, io
        with patch.dict(os.environ, {"ASB_ROOT_SELECTION": "All root managers"}), contextlib.redirect_stdout(io.StringIO()) as out:
            subject.matrix()
        rows = json.loads(out.getvalue().split("=", 1)[1])["include"]
        self.assertEqual(len(rows), 18)
        self.assertEqual(rows[0]["target"], subject.TARGET_ORDER[0])
        self.assertEqual({row["root_flavor"] for row in rows}, set(subject.ROOT_FLAVORS))
        self.assertEqual(len({(row["target"], row["root_flavor"]) for row in rows}), 18)

    def test_feature_overrides_and_device_scope(self):
        with patch.dict(os.environ, {"ASB_DISABLED_FEATURES": "nomount,ptrace,bpf", "ASB_PERF_MODE":"bore_adios", "ASB_POWER_FEATURES":"all"}):
            main = subject.feature_settings("KernelSU-Next", "6.12.x-android16")
            self.assertEqual(main["use_nomount"], "false")
            self.assertEqual(main["use_bpf"], "false")
            self.assertEqual(main["compatibility_patches"], "unicode")
            self.assertEqual(main["use_perf"], "bore_adios")
            self.assertEqual(main["power_app_features"], "all")
            other = subject.feature_settings("KowSU", "6.1.x-android14")
            self.assertEqual(other["use_susfs"], "false")
            self.assertEqual(other["use_perf"], "true")
            self.assertEqual(other["power_app_features"], "none")
        with patch.dict(os.environ, {"ASB_DISABLED_FEATURES": "unknown"}):
            with self.assertRaises(ValueError): subject.feature_settings("KernelSU-Next", "6.12.x-android16")
        with patch.dict(os.environ, {"ASB_DISABLED_FEATURES": "susfs"}):
            with self.assertRaises(ValueError): subject.feature_settings("FinaSU", "6.12.x-android16")

    def test_unknown_root_is_rejected_before_dispatch(self):
        with patch.dict(os.environ, {"ASB_ROOT_FLAVOR": "unknown"}), patch.object(subject.subprocess, "run") as dispatch:
            with self.assertRaises(ValueError):
                subject.main()
            dispatch.assert_not_called()

    def test_missing_artifacts_never_publishes(self):
        build = [{"databaseId": 23, "displayTitle": "ASB-group-42-1-6.1.x-android14-KernelSU-Next",
                  "status": "completed", "conclusion": "success"}]
        with patch.object(subject.subprocess, "run"), \
             patch.object(subject, "run", side_effect=[json.dumps(build), '{"artifacts": []}']), \
             patch.object(subject, "telegram") as send:
            with self.assertRaisesRegex(RuntimeError, "artifact"):
                subject.main()
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
