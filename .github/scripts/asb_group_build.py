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


ROOT_FLAVORS = ("KernelSU-Next", "KernelSU", "BakaSU", "SukiSU-Ultra", "KowSU", "FinaSU")
TARGET_ORDER = ("OnePlus 15/15R · android16-6.12.69", "Ace 6 · android15-6.6.118",
                "OnePlus 12 · android14-6.1.157")
TARGET_IDS = dict(zip(TARGET_ORDER, ("612", "66", "61")))

FEATURE_INPUTS = {"susfs":"use_susfs", "nomount":"use_nomount", "bbg":"use_bbg", "net":"use_net", "ds":"use_ds", "ntsync":"use_ntsync", "bpf":"use_bpf", "vpnhide":"use_vpnhide", "usb":"use_usb_arsenal"}
DISABLABLE = set(FEATURE_INPUTS) | {"ptrace", "unicode"}

def feature_settings(root, family):
    disabled = {x.strip().lower() for x in os.environ.get("ASB_DISABLED_FEATURES", "").split(",") if x.strip()}
    if disabled - DISABLABLE:
        raise ValueError("Unknown disabled features: " + ", ".join(sorted(disabled - DISABLABLE)))
    if root == "FinaSU" and "susfs" in disabled:
        raise ValueError("FinaSU requires SUSFS")
    cfg = {arg: ("false" if name in disabled else "true") for name, arg in FEATURE_INPUTS.items()}
    if root == "KowSU": cfg["use_susfs"] = "false"
    cfg["compatibility_patches"] = ("none" if {"ptrace", "unicode"} <= disabled else "unicode" if "ptrace" in disabled else "ptrace" if "unicode" in disabled else "both")
    perf = os.environ.get("ASB_PERF_MODE", "bore_adios")
    power = os.environ.get("ASB_POWER_FEATURES", "all")
    if perf not in ("true", "false", "bore", "bore_adios") or power not in ("all", "both", "rekernel", "wakeup-blocker", "sdp-900ma", "none"):
        raise ValueError("Invalid performance or power feature selection")
    cfg["use_perf"] = perf if family == "6.12.x-android16" else ("false" if perf == "false" else "true")
    cfg["power_app_features"] = power if family == "6.12.x-android16" else "none"
    return cfg

def matrix():
    selected = os.environ.get("ASB_ROOT_SELECTION", "KernelSU-Next")
    roots = ROOT_FLAVORS if selected == "All root managers" else (selected,)
    if any(root not in ROOT_FLAVORS + ("No Root",) for root in roots):
        raise ValueError("Unknown root implementation")
    for root in roots:
        for target in TARGET_ORDER: feature_settings(root, TARGETS[target][0])
    items = [{"target": target, "id": TARGET_IDS[target], "root_flavor": root,
              "root_id": root.lower().replace(" ", "-")}
             for target in TARGET_ORDER for root in roots]
    print("value=" + json.dumps({"include": items}, ensure_ascii=True, separators=(",", ":")))


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
    root = os.environ.get("ASB_ROOT_FLAVOR", "KernelSU-Next")
    if root not in ROOT_FLAVORS + ("No Root",):
        raise ValueError("Unknown root implementation")
    token, topic = os.environ.get("TG_TOKEN", ""), os.environ.get("TG_TOPIC", "")
    deferred = os.environ.get("ASB_DEFER_PUBLISH") == "1"
    if page != "4k":
        raise RuntimeError("Only 4k is supported by this group build")
    if not deferred and (not token or not topic.isdecimal() or int(topic) <= 0):
        raise RuntimeError("Bot token and valid ASB_KERNEL_TOPIC_ID are required")

    repo = os.environ["GITHUB_REPOSITORY"]
    request = "ASB-group-" + os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"] + "-" + family + "-" + root.replace(" ", "-")
    args = {"oneplus_branch": request, "kernel_build_version": family, "os_patch_level": patch,
            "page_size": page, "root_flavor": root, "release_type": "CI",
            "commit_mode": "latest", "kernel_source": "aosp",
            "keep_kmi": "true", "allow_version_mismatch": "false"}
    features = feature_settings(root, family)
    args.update(features)
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
        Path("asb-build-reference.json").write_text(json.dumps({"run_id": run_id, "target": target, "page_size": page, "root_flavor": root, "features": features}))
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
    for entry in references:
        entry.setdefault("root_flavor", "KernelSU-Next")  # older saved builds
    pairs = [(entry["target"], entry["root_flavor"]) for entry in references]
    roots = {root for _, root in pairs}
    if (not roots or any(root not in ROOT_FLAVORS + ("No Root",) for root in roots)
            or len(set(pairs)) != len(pairs)
            or set(pairs) != {(target, root) for target in TARGET_ORDER for root in roots}
            or any(entry.get("page_size") != "4k" for entry in references)):
        raise RuntimeError("Missing, duplicate or unsupported device/root build references")
    root_order = ROOT_FLAVORS + ("No Root",)
    references.sort(key=lambda entry: (TARGET_ORDER.index(entry["target"]), root_order.index(entry["root_flavor"])))
    attachments = []
    common_added = False
    managers_added = set()
    with tempfile.TemporaryDirectory() as work:
        for index, entry in enumerate(references):
            run_id = entry["run_id"]
            artifacts = json.loads(run("gh", "api", f"repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100"))["artifacts"]
            names = [item["name"] for item in artifacts if not item["expired"]]
            kernels = [name for name in names if name.endswith("-AnyKernel3")]
            managers = [name for name in names if name.endswith(".apk") and "spoofed" not in name.lower()]
            modules = [name for name in names if name.startswith(("NoMount-", "VPNHide-"))]
            root = entry["root_flavor"]
            if len(kernels) != 1 or (root != "No Root" and not managers):
                raise RuntimeError(f"Required artifacts missing in build {run_id} ({root})")
            selected = sorted(kernels)
            if root != "No Root" and root not in managers_added:
                selected += sorted(managers)[:1]
                managers_added.add(root)
            if not common_added:
                selected += sorted(modules)
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
        sample = references[0].get("features")
        feature_text = "Настройки сборки — в действиях GitHub."
        if sample:
            labels = {"use_susfs":"SUSFS", "use_nomount":"NoMount", "use_bbg":"BBG", "use_net":"NET", "use_ds":"DS", "use_ntsync":"NTSync", "use_bpf":"BPF", "use_vpnhide":"VPN", "use_usb_arsenal":"USB"}
            enabled = [label for key, label in labels.items() if sample.get(key) == "true"]
            compat = sample.get("compatibility_patches", "none")
            if compat in ("both", "ptrace"): enabled.append("Ptrace")
            if compat in ("both", "unicode"): enabled.append("Unicode")
            perf = sample.get("use_perf")
            if perf in ("true", "bore", "bore_adios"): enabled.append("Perf/BORE" if perf != "true" else "Perf")
            if perf == "bore_adios": enabled.append("ADIOS")
            power = sample.get("power_app_features", "none")
            if power in ("all", "both", "rekernel"): enabled.append("Re-Kernel")
            if power in ("all", "both", "wakeup-blocker"): enabled.append("ASB wakeup blocker")
            if power in ("all", "sdp-900ma"): enabled.append("SDP900")
            feature_text = "⚙️ Запрошено для 6.12: " + ", ".join(enabled) + ". Для других ядер состав может отличаться."
        links = []
        for target in TARGET_ORDER:
            entry = next(e for e in references if e["target"] == target)
            label = {TARGET_ORDER[0]:"15/15R", TARGET_ORDER[1]:"Ace 6", TARGET_ORDER[2]:"12 / Ace 5"}[target]
            links.append(f'<a href="https://github.com/{repo}/actions/runs/{entry["run_id"]}">{label}</a>')
        text = ("🛠 <b>KRILIKD · тестовые ядра</b>\n"
                "📱 OnePlus 15/15R · 6.12.69; Ace 6 · 6.6.118; OnePlus 12 / Ace 5 · 6.1.157\n"
                f"🔑 {html.escape(', '.join(root for root in root_order if root in roots))} · 4K\n"
                f"{feature_text}\n"
                "Совместимость с прошивкой и успешная загрузка требуют проверки.\n"
                "🔗 Сборки и артефакты: " + " · ".join(links))

        # Telegram limits the entire multipart request, not only each document. Split at
        # 40 MB to leave room for encoding; every file is still sent exactly once.
        groups, current, size = [], [], 0
        for path in attachments:
            n = path.stat().st_size
            if current and (size + n > 40_000_000 or len(current) >= 10):
                groups.append(current)
                current, size = [], 0
            current.append(path)
            size += n
        if current:
            groups.append(current)
        for group_index, group in enumerate(groups):
            cmd = ["curl", "--silent", "--show-error", "--fail-with-body", "--max-time", "300",
                   "--form-string", "chat_id=" + os.environ["TG_CHAT"],
                   "--form-string", "message_thread_id=" + topic]
            caption = text if group_index == 0 else "📦 Остальные файлы сборки KRILIKD"
            if len(group) == 1:
                cmd += ["--form", f"document=@{group[0]}", "--form-string", "caption=" + caption,
                        "--form-string", "parse_mode=HTML",
                        "https://api.telegram.org/bot" + token + "/sendDocument"]
            else:
                media = []
                for index, path in enumerate(group):
                    item = {"type": "document", "media": "attach://file" + str(index)}
                    if index == 0:
                        item.update(caption=caption, parse_mode="HTML")
                    media.append(item)
                    cmd += ["--form", f"file{index}=@{path}"]
                cmd += ["--form-string", "media=" + json.dumps(media, ensure_ascii=False),
                        "https://api.telegram.org/bot" + token + "/sendMediaGroup"]
            result = json.loads(run(*cmd))
            delivered = result.get("result")
            if not result.get("ok") or (len(group) > 1 and (not isinstance(delivered, list) or len(delivered) != len(group))):
                raise RuntimeError("Telegram did not confirm all files in group " + str(group_index + 1))
            print("Published group", group_index + 1, "of", len(groups), "with", len(group), "files", flush=True)



if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["matrix"]:
            matrix()
        elif sys.argv[1:] == ["publish-group"]:
            publish_group()
        else:
            main()
    except subprocess.CalledProcessError:
        print("ASB group build failed: an external command failed", file=sys.stderr)
        sys.exit(1)
    except (KeyError, ValueError, OSError, RuntimeError) as exc:
        print("ASB group build failed: " + str(exc), file=sys.stderr)
        sys.exit(1)
