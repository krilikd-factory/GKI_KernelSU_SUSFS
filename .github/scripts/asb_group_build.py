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
    attempts = 3 if args and args[0] == "gh" else 1
    for attempt in range(attempts):
        try:
            return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout
        except subprocess.CalledProcessError as error:
            if attempt + 1 == attempts:
                if args and args[0] == "gh":
                    raise RuntimeError("GitHub API or artifact download failed after retries") from None
                try:
                    details = json.loads(error.stdout or "{}").get("description", "")
                except ValueError:
                    details = ""
                raise RuntimeError("Telegram request failed" + (": " + str(details)[:200] if details else "")) from None
            time.sleep(5 * (attempt + 1))


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
    deferred = os.environ.get("ASB_DEFER_PUBLISH") == "1"
    if page != "4k":
        raise RuntimeError("Only 4k is supported by this group build")
    if not deferred and (not token or not topic.isdecimal() or int(topic) <= 0):
        raise RuntimeError("Bot token and valid ASB_KERNEL_TOPIC_ID are required")

    repo = os.environ["GITHUB_REPOSITORY"]
    request = "ASB-group-" + os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"] + "-" + family
    args = {"oneplus_branch": request, "kernel_build_version": family, "os_patch_level": patch,
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

    if deferred:
        Path("asb-build-reference.json").write_text(json.dumps({"run_id": run_id, "target": target, "page_size": page}))
    else:
        publish(run_id, target, page)


def publish(run_id, target, page):
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["TG_TOKEN"]
    topic = os.environ["TG_TOPIC"]
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
    print("Publishing build", run_id, "for", target, flush=True)
    telegram("sendMessage", chat, thread, "parse_mode=HTML", "text=" + header)
    print("Published build summary", flush=True)
    for name in kernel + managers + modules:
        with tempfile.TemporaryDirectory() as temporary:
            folder = str(Path(temporary) / "contents")
            Path(folder).mkdir()
            print("Downloading artifact", name, flush=True)
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
            print("Sending artifact", name, flush=True)
            response = json.loads(run(*command))
            if not response.get("ok"):
                raise RuntimeError("Telegram did not accept " + name)
            print("Sent artifact", name, flush=True)
    print("Published kernel and companion artifacts to the configured ASB topic.")


def publish_group():
    """Send the three kernels and one copy of the shared files as one album."""
    repo = os.environ["GITHUB_REPOSITORY"]
    token, topic = os.environ.get("TG_TOKEN", ""), os.environ.get("TG_TOPIC", "")
    if not token or not topic.isdecimal() or int(topic) <= 0:
        raise RuntimeError("Bot token and valid ASB_KERNEL_TOPIC_ID are required")
    references = [json.loads(path.read_text()) for path in Path("build-references").glob("*/asb-build-reference.json")]
    if len(references) != 3 or {entry["target"] for entry in references} != set(TARGETS):
        raise RuntimeError("Missing or duplicate build references")
    attachments = []
    common_added = False
    with tempfile.TemporaryDirectory() as work:
        for index, entry in enumerate(references):
            run_id = entry["run_id"]
            artifacts = json.loads(run("gh", "api", f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100"))["artifacts"]
            names = [item["name"] for item in artifacts if not item["expired"]]
            kernels = [name for name in names if name.endswith("-AnyKernel3")]
            managers = [name for name in names if name.endswith(".apk") and "spoofed" not in name.lower()]
            modules = [name for name in names if name.startswith(("NoMount-", "VPNHide-"))]
            if len(kernels) != 1 or not managers:
                raise RuntimeError(f"Required artifacts missing in build {run_id}")
            selected = kernels + ([] if common_added else managers[:1] + modules)
            common_added = True
            for number, name in enumerate(selected):
                folder = Path(work) / f"{index}-{number}"
                folder.mkdir()
                run("gh", "run", "download", str(run_id), "--repo", repo, "--name", name, "--dir", str(folder))
                files = [path for path in folder.rglob("*") if path.is_file()]
                if not files:
                    raise RuntimeError("Empty artifact: " + name)
                if name.endswith("-AnyKernel3") or len(files) != 1:
                    file = Path(work) / (name + ".zip")
                    subprocess.run(["zip", "-qr", str(file), "."], cwd=folder, check=True)
                else:
                    file = files[0]
                if file.stat().st_size > 49_000_000:
                    raise RuntimeError("Artifact exceeds Telegram upload limit: " + name)
                attachments.append(file)
        if len(attachments) > 10:
            raise RuntimeError("Too many files for a Telegram media group")
        text = ("🛠 <b>KRILIKD · тестовые ядра</b>\n"
                "📱 OnePlus 12 · 6.1.157, Ace 6 · 6.6.118, OnePlus 15/15R · 6.12.69\n"
                "🔑 KernelSU Next · 4K\n"
                "Проверьте совместимость с прошивкой и устройством.\n"
                f'🔗 <a href="https://github.com/{repo}/actions/runs/{os.environ["GITHUB_RUN_ID"]}">Сборка на GitHub</a>')
        media = []
        cmd = ["curl", "--silent", "--show-error", "--fail-with-body", "--max-time", "300",
               "--form-string", "chat_id=" + os.environ["TG_CHAT"],
               "--form-string", "message_thread_id=" + topic]
        for index, path in enumerate(attachments):
            item = {"type": "document", "media": "attach://file" + str(index)}
            if index == 0:
                item.update(caption=text, parse_mode="HTML")
            media.append(item)
            cmd += ["--form", f"file{index}=@{path}"]
        cmd += ["--form-string", "media=" + json.dumps(media, ensure_ascii=False),
                "https://api.telegram.org/bot" + token + "/sendMediaGroup"]
        result = json.loads(run(*cmd))
        if not result.get("ok") or len(result.get("result", [])) != len(attachments):
            raise RuntimeError("Telegram did not confirm all files")
        print("Published one media group with", len(attachments), "files")


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["publish-group"]:
            publish_group()
        else:
            main()
    except subprocess.CalledProcessError:
        print("ASB group build failed: an external command failed", file=sys.stderr)
        sys.exit(1)
    except (KeyError, ValueError, OSError, RuntimeError) as exc:
        print("ASB group build failed: " + str(exc), file=sys.stderr)
        sys.exit(1)
