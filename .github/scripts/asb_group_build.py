#!/usr/bin/env python3
"""Dispatch a single KernelSU-Next build and deliver its files to the ASB topic."""
import html
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TARGETS = {
    "OnePlus 12 · android14-6.1.157": ("6.1.x-android14", "2025-12"),
    "Ace 6 · android15-6.6.118": ("6.6.x-android15", "2026-01"),
    "OnePlus 15/15R · android16-6.12.69": ("6.12.x-android16", "2026-03"),
}


def run(*args):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout


def telegram(method, *fields):
    command = ["curl", "--silent", "--show-error", "--fail-with-body", "--max-time", "180"]
    for field in fields:
        command += ["--form-string", field]
    command.append("https://api.telegram.org/bot" + os.environ["TG_TOKEN"] + "/" + method)
    response = json.loads(run(*command))
    if not response.get("ok"):
        raise RuntimeError("Telegram rejected " + method + ": " + str(response.get("description", "unknown error")))
    return response["result"]


def main():
    target = os.environ["ASB_TARGET"]
    family, patch = TARGETS[target]
    page = os.environ["ASB_PAGE_SIZE"]
    token, topic = os.environ.get("TG_TOKEN", ""), os.environ.get("TG_TOPIC", "")
    if not token or not topic.isdecimal() or int(topic) <= 0 or page not in ("4k", "16k"):
        raise RuntimeError("Bot token, valid ASB_KERNEL_TOPIC_ID and page size are required")

    repo = os.environ["GITHUB_REPOSITORY"]
    request = "ASB-group-" + os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"]
    args = {"request_id": request, "kernel_build_version": family, "os_patch_level": patch,
            "page_size": page, "root_flavor": "KernelSU-Next", "release_type": "CI",
            "commit_mode": "latest", "kernel_source": "aosp", "use_perf": "false",
            "power_app_features": "none", "keep_kmi": "true", "allow_version_mismatch": "false"}
    cmd = ["gh", "workflow", "run", "main.yml", "--repo", repo, "--ref", "main"]
    for key, value in args.items():
        cmd += ["-f", key + "=" + value]
    subprocess.run(cmd, check=True)
    deadline = time.monotonic() + 150 * 60
    run_id = None
    while time.monotonic() < deadline:
        runs = json.loads(run("gh", "run", "list", "--repo", repo, "--workflow", "main.yml",
                              "--branch", "main", "--limit", "100",
                              "--json", "databaseId,displayTitle,status,conclusion"))
        matches = [item for item in runs if item["displayTitle"] == request]
        if len(matches) > 1:
            raise RuntimeError("Multiple builds have the same request ID; refusing ambiguous publication")
        if matches:
            entry = matches[0]
            run_id = entry["databaseId"]
            if entry["status"] == "completed":
                if entry["conclusion"] != "success":
                    raise RuntimeError(f"Kernel build {run_id} finished with {entry['conclusion']}")
                break
        time.sleep(30)
    else:
        raise RuntimeError(f"Kernel build did not finish in time: {run_id or request}")

    url = f"https://github.com/{repo}/actions/runs/{run_id}"
    artifacts = json.loads(run("gh", "api", f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100"))["artifacts"]
    names = [a["name"] for a in artifacts if not a["expired"]]
    kernel = [name for name in names if name.endswith("-AnyKernel3")]
    managers = [name for name in names if name.endswith(".apk") and "spoofed" not in name.lower()]
    modules = [name for name in names if name.startswith(("NoMount-", "VPNHide-"))]
    if len(kernel) != 1 or not managers:
        raise RuntimeError("A unique kernel artifact and KernelSU-Next manager were not found in " + url)

    chat = "chat_id=" + os.environ["TG_CHAT"]
    thread = "message_thread_id=" + topic
    header = (f"🛠 <b>KRILIKD · тестовая сборка</b>\n"
              f"📱 {html.escape(target)} · {page}\n"
              "🔑 KernelSU-Next\n"
              "Сборка основана на выбранной ветке GKI. Совместимость с конкретной прошивкой "
              "и успешная загрузка требуют проверки на устройстве.\n"
              f'🔗 <a href="{url}">Сборка и сведения на GitHub</a>')
    telegram("sendMessage", chat, thread, "parse_mode=HTML", "text=" + header)
    for name in kernel + managers + modules:
        with tempfile.TemporaryDirectory() as temporary:
            folder = str(Path(temporary) / "contents")
            Path(folder).mkdir()
            run("gh", "run", "download", str(run_id), "--repo", repo, "--name", name, "--dir", folder)
            files = list(Path(folder).rglob("*"))
            files = [path for path in files if path.is_file()]
            if not files:
                raise RuntimeError("Empty artifact: " + name)
            if name.endswith("-AnyKernel3"):
                archive = Path(temporary) / (name + ".zip")
                subprocess.run(["zip", "-qr", str(archive), "."], cwd=folder, check=True)
                file = archive
            elif len(files) == 1:
                file = files[0]
            else:
                archive = Path(temporary) / (name + ".zip")
                subprocess.run(["zip", "-qr", str(archive), "."], cwd=folder, check=True)
                file = archive
            if file.stat().st_size > 49_000_000:
                telegram("sendMessage", chat, thread, "text=" + name + "\nФайл доступен в артефактах сборки: " + url)
                continue
            command = ["curl", "--silent", "--show-error", "--fail-with-body", "--max-time", "180",
                       "--form-string", chat, "--form-string", thread,
                       "--form-string", "caption=" + name, "--form", "document=@" + str(file),
                       "https://api.telegram.org/bot" + token + "/sendDocument"]
            response = json.loads(run(*command))
            if not response.get("ok"):
                raise RuntimeError("Telegram did not accept " + name)
    print("Published kernel and companion artifacts to the configured ASB topic.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError:
        print("ASB group build failed: an external command failed", file=sys.stderr)
        sys.exit(1)
    except (KeyError, ValueError, OSError, RuntimeError) as exc:
        print("ASB group build failed: " + str(exc), file=sys.stderr)
        sys.exit(1)
