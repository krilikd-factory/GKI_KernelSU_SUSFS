#!/usr/bin/env python3
"""Render one structured, evidence-bounded KRILIKD BuildInfo report.

Optional evidence files are described as unavailable rather than turning a failed
build's summary step into a second failure. Config presence never becomes runtime
or physical-boot proof.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path

FEATURE_ORDER = ("SUSFS", "NoMount", "BBG", "NET", "DS", "NTSync", "Ptrace", "Unicode", "BPF", "Perf", "VPN", "USB", "RK", "AWL", "SDP900")
FEATURES = {
    "SUSFS": ("🛡️ SUSFS", "Kernel-side SUSFS integration; matching userspace remains required"),
    "NoMount": ("🧩 NoMount", "Mount metamodule integration; companion artifact is built separately"),
    "BBG": ("🔐 Baseband Guard", "Guards critical partitions and device nodes"),
    "NET": ("🌐 Networking", "WireGuard, CAKE, congestion control, Netfilter/IPSet, NAT and CIFS options"),
    "DS": ("📦 DroidSpaces", "Container runtime support"),
    "NTSync": ("🎮 NTSync", "NT-compatible synchronization primitives"),
    "Ptrace": ("🩹 Ptrace fix", "Version-gated ptrace information-leak fix"),
    "Unicode": ("🔤 Unicode fix", "Version-gated non-printable Unicode path fix"),
    "BPF": ("📊 BPF stack", "BTF, eBPF and FUSE-BPF build options"),
    "Perf": ("🚀 Performance pass", "Dry-run-checked patches plus stock-config and KMI guards"),
    "VPN": ("🕵️ VPN Hide", "Built-in driver; managed only by its own app and matching companion"),
    "USB": ("🔌 USB Arsenal", "USB gadget integration, including ISO/DVD mode where the patch fits"),
    "RK": ("🧊 Re-Kernel", "Pinned built-in Re-Kernel driver and its netlink transport"),
    "AWL": ("🌙 ASB wakeup blocker", "Bounded exact-name wakeup-source blocklist, empty by default"),
    "SDP900": ("🔌 OPLUS SDP 900 mA", "USB data-port SDP limit raised from 500 to 900 mA"),
}

# One guide for every feature generation: no separate, sparsely explained legacy
# list. These are mechanism/trade-off notes, not claims of measured gains.
FEATURE_GUIDE = {
    "SUSFS": (
        "Adds the selected kernel-side SUSFS interfaces and hooks at the pinned revision.",
        "No CPU-frequency, scheduler or thermal policy is changed by this group itself. Any cost depends on which SUSFS facilities userspace actually exercises.",
        "Requires matching SUSFS userspace. Successful integration is not evidence of runtime behavior or of any detector outcome."),
    "NoMount": (
        "Integrates NoMount VFS hooks and builds its separate metamodule companion.",
        "Does not tune power or performance directly; mount activity and the companion configuration determine runtime overhead.",
        "Use the matching NoMount artifact. ASB does not become a second mount-policy owner."),
    "BBG": (
        "Adds Baseband Guard checks around the protected partitions and device nodes selected by its integration.",
        "No direct frequency or suspend tuning. Protection checks may add work only on the relevant access paths.",
        "Build inclusion does not attest modem compatibility or every physical protection path; validate on the device."),
    "NET": (
        "Enables the requested networking options, including CAKE and IP-set requirements checked against the final .config; BBR and other protocol support remain runtime-selectable where exposed.",
        "CAKE can shape queues and latency when explicitly configured, while queue management consumes CPU. Congestion control changes network behavior, not guaranteed throughput or battery life. Merely compiling an option does not activate it.",
        "Final-config presence is not runtime proof. ASB diagnostics only reads existing CAKE/module/algorithm state; it does not load modules or create qdiscs."),
    "DS": (
        "Adds DroidSpaces kernel support for its container runtime.",
        "No standalone power profile is implied; container workload and userspace policy determine resource use.",
        "Requires compatible DroidSpaces userspace. Container isolation and app compatibility need separate runtime testing."),
    "NTSync": (
        "Adds NT-compatible synchronization primitives for compatible userspace workloads.",
        "May change synchronization behavior in users of the interface; it does not guarantee higher FPS or lower drain in unrelated workloads.",
        "Only workloads that use NTSync can benefit. Actual game/application behavior is not attested by compilation."),
    "Ptrace": (
        "Applies the version-gated ptrace information-leak correction when its reviewed source anchors fit.",
        "Security/correctness fix; no general performance or battery claim.",
        "Version gating and patch inventory show the decision. It is not a blanket ptrace policy change."),
    "Unicode": (
        "Applies the version-gated fix for non-printable Unicode characters in relevant paths.",
        "Correctness fix with no claimed global performance or power effect.",
        "Only the reviewed path handling is in scope; unrelated filesystem behavior is unchanged by this description."),
    "BPF": (
        "Enables the selected BTF/eBPF/FUSE-BPF build options for compatible observers and userspace.",
        "BPF programs can add measurement or execution overhead when loaded. Enabling the stack alone neither improves nor worsens a workload by a guaranteed amount.",
        "Config support is not proof that a particular program attaches or that FUSE-BPF is runtime-available."),
    "Perf": (
        "Applies only the reviewed performance patches whose family/device/SoC policy and dry-run checks pass; the inventory records every apply/skip decision.",
        "Effects are patch-specific. This pass must not be read as a measured battery, responsiveness or FPS improvement, and it does not override vendor thermal ownership.",
        "Stock-config and KMI guards limit integration risk. Patch success and zero rejects still do not prove device boot or runtime superiority."),
    "VPN": (
        "Builds the in-kernel VPN Hide driver at the pinned revision for use by its own app and matching companion.",
        "No ASB network policy or always-on worker is added. Runtime cost depends on the external owner's active configuration.",
        "ASB is not a second VPN Hide owner. Do not install the incompatible vpnhide-kmod with the built-in variant."),
    "USB": (
        "Integrates USB gadget functions, including ISO/DVD mode where the reviewed patch applies.",
        "Only active gadget use should affect the USB path; no idle battery or charging-current guarantee is made.",
        "Gadget, cable, host and ColorOS behavior require on-device validation. This is separate from the SDP 900 mA patch."),
    "RK": (
        "Integrates the exact pinned Re-Kernel source as a built-in driver and records its source-tree hash and transport.",
        "Its effect depends on Re-Kernel messages and compatible userspace. No universal app-launch, FPS or battery improvement is inferred.",
        "Freezer behavior, process delivery and application compatibility require on-device testing; ASB does not add a second Re-Kernel policy owner."),
    "AWL": (
        "Adds a bounded 16-rule exact-name wakeup-source blocklist controlled by ASB, with no default rules. ABI 2 exposes read-only aggregate and per-rule blocked-event counters.",
        "A carefully selected rule may avoid unnecessary wake work; an incorrect rule can break critical wakeups. No blanket battery claim is made.",
        "The empty list uses a disabled static key. Modem, alarm, call, charging and suspend behavior must be validated physically."),
    "SDP900": (
        "Changes only the OPLUS USB SDP data-port current limit from 500 to 900 mA for the reviewed Android 16 / 6.12 integration.",
        "Raises a negotiated data-port ceiling; it does not force 900 mA, change charging algorithms or guarantee faster charging.",
        "Cable, host port and device policy still determine current. Check /proc/sdp900ma and measure physically; build success is not charging validation."),
}

ASB_CONTROL_GUIDE = (
    ("mglru_mode", "MGLRU mode", "Selects only the declared MGLRU mode values through /sys/kernel/mm/lru_gen/enabled when the running kernel exposes them.", "Can alter reclaim and page-aging behavior. Smart/auto-like choices are workload trade-offs, not a guaranteed RAM, battery or responsiveness win.", "Stock is inert until a baseline exists; otherwise it restores the exact captured value."),
    ("kernel_timer_migration", "Timer migration", "Controls /proc/sys/kernel/timer_migration with bounded 0/1 writes.", "Migrating timers may improve locality but can affect idle residency; disabling migration may help idle consolidation but increase contention. Device measurements decide.", "Live readback is required; restore returns the captured baseline."),
    ("kernel_sched_nr_migrate", "Scheduler migration hint", "Writes the declared values to the Linux 6.x debugfs node /sys/kernel/debug/sched/nr_migrate, not the obsolete sched_nr_migrate proc path.", "Changes a scheduler load-balancing hint. Higher or lower migration is not universally better for throughput, latency or power.", "Requires debugfs exposure and write permission; absence is reported instead of a false success."),
    ("kernel_sched_stats", "Scheduler statistics", "Controls /proc/sys/kernel/sched_schedstats only through explicit off or exact-baseline restore; ASB has no hidden unconditional disable write.", "Disabling statistics can avoid instrumentation work but removes useful scheduler observability. A universal battery gain is not claimed.", "If the node is unavailable, the control is unavailable rather than silently applied elsewhere."),
    ("kernel_sched_autogroup", "Automatic process grouping", "Controls /proc/sys/kernel/sched_autogroup_enabled with validated 0/1 values.", "Changes interactive session grouping and CPU-share behavior. It can improve fairness for one workload while reducing another workload's share.", "Per-app or per-game superiority is not inferred; readback and exact restore remain mandatory."),
    ("kernel_sched_child_runs_first", "Child-first scheduling after fork", "Controls /proc/sys/kernel/sched_child_runs_first with validated 0/1 values.", "Affects post-fork scheduling order. It is a narrow latency trade-off, not a general performance profile.", "No battery percentage or global responsiveness claim; restore uses the exact captured baseline."),
    ("wakeup_blocker", "Wakeup-source blocker", "Uses the kernel's bounded exact-name blocklist, empty by default, with at most 16 reviewed user rules.", "Blocking an unnecessary wakeup can reduce avoidable wake work, but blocking a required source can break modem, alarm, call, charging or suspend behavior.", "Static key keeps the empty list low-overhead. ASB observes explicit start/end deltas from standard /sys/class/wakeup counters; no kernel idle detector or polling worker is added. Physical power behavior is not attested by the build."),
)


def md(value: object) -> str:
    text = str(value if value not in (None, "") else "N/A")
    return text.replace("|", "\\|").replace("\n", " ").strip()


def table(lines: list[str], headers: tuple[str, ...], rows: list[tuple[object, ...]]) -> None:
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(md(v) for v in row) + " |")
    lines.append("")


def read_json(path: Path):
    if not path.is_file():
        return None, "not generated"
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except (OSError, ValueError) as exc:
        return None, f"unreadable: {exc}"


def enabled_features(raw: str) -> list[str]:
    tokens = [x.strip() for x in raw.split("+") if x.strip()]
    if raw.strip() == "All":
        return list(FEATURE_ORDER)
    return [x for x in FEATURE_ORDER if x in tokens] + [x for x in tokens if x not in FEATURE_ORDER]


def gate_state(status: str, report: Path) -> str:
    if report.is_file() and status == "success":
        return "✅ Passed; report present"
    if report.is_file():
        return "📄 Report present; inspect build result"
    return "⚠️ Not recorded or guard skipped"


def render(a: argparse.Namespace, env: dict[str, str]) -> str:
    workspace = Path(a.workspace)
    success = a.job_status == "success"
    status = "✅ Success" if success else ("⚠️ Cancelled" if a.job_status == "cancelled" else "❌ Failed")
    features = enabled_features(a.feature_set)
    run_url = f"{env.get('GITHUB_SERVER_URL', 'https://github.com')}/{env.get('GITHUB_REPOSITORY', '')}/actions/runs/{env.get('GITHUB_RUN_ID', '')}"
    root_impl = env.get("ROOT_IMPLEMENTATION") or "not recorded"
    root_manager = env.get("ROOT_MANAGER") or "N/A"
    root_version = env.get("ROOT_VERSION") or env.get("KSU_GIT_TAG") or "N/A"
    root_commit = env.get("ROOT_COMMIT") or env.get("KSU_COMMIT") or "N/A"
    source = env.get("KERNEL_SOURCE") or "aosp"
    source_commit = env.get("KERNEL_SOURCE_COMMIT") or "N/A"
    patch_channel = env.get("PATCH_CHANNEL") or "stable"
    lto = env.get("KERNEL_LTO") or "unknown"
    provenance, provenance_error = read_json(workspace / "source-provenance.json")
    device, device_error = read_json(workspace / ".github/device.json")
    compact_inventory, _ = read_json(workspace / "patch-inventory.json")
    compact_counts = Counter(p.get("decision", "unknown") for p in compact_inventory.get("patches", []) if isinstance(p, dict)) if isinstance(compact_inventory, dict) else Counter()
    patch_brief = (f"apply={compact_counts.get('apply', 0)}, skip={compact_counts.get('skip', 0)}, rejects={env.get('REJ_COUNT') or 'not recorded'}"
                   if isinstance(compact_inventory, dict) else f"inventory unavailable; rejects={env.get('REJ_COUNT') or 'not recorded'}")
    if source == "aosp":
        oneplus_brief = "not used; not required for AOSP GKI mode"
    elif source == "oneplus":
        oneplus_brief = f"used and required; {env.get('OEM_BRANCH') or ((provenance.get('oem') or {}).get('branch') if isinstance(provenance, dict) else None) or 'branch not recorded'}"
    else:
        oneplus_brief = "dependency unknown for this source mode"
    susfs_brief = (a.susfs_commit or env.get("SUSFS_COMMIT") or "N/A") if "SUSFS" in features else "not requested"

    lines = [
        "# ⚡ KRILIKD Build Report",
        "",
        f"## `{md(a.file_name)}`",
        "",
        "> **Build-verified only.** A successful compile and its static guards do not prove physical boot, runtime interface writability or device compatibility.",
        "",
        "### ⚡ Build at a glance",
        "",
    ]
    table(lines, ("Result", "Concise evidence"), [
        ("Build / artifact", f"{status} · {a.file_name}"),
        ("Kernel / config", f"{a.kernel_version}.{a.sublevel}-{a.android_version}-{a.os_patch_level} · {a.family} · {a.page_size} · LTO={lto}"),
        ("Source base", f"{source} · {source_commit}"),
        ("OnePlus published source", oneplus_brief),
        ("Patches", patch_brief),
        ("Root / SUSFS pins", f"{root_impl} {root_commit} · {susfs_brief}"),
        ("Feature groups", a.feature_set or "none"),
        ("Safety", f"KMI: {gate_state(a.job_status, workspace / 'kmi-crc-report.txt')}; stock config: {gate_state(a.job_status, workspace / 'stock-config-report.txt')}"),
        ("ASB wakeup ABI", "2 + read-only per-rule counters" if "AWL" in features else "not requested"),
    ])
    lines += [
        "Full source preflight, component pins, patch decisions and artifact details follow below and in the BuildInfo attachment.",
        "",
        "### 🧱 Build identity",
        "",
    ]
    table(lines, ("Field", "Value"), [
        ("Status", status), ("Artifact", a.file_name),
        ("Kernel", f"{a.kernel_version}.{a.sublevel}-{a.android_version}-{a.os_patch_level}"),
        ("Version string", a.version_string or "not extracted"), ("Family", a.family),
        ("Source", source), ("Source commit", source_commit),
        ("OEM repository", env.get("OEM_REPO") or "N/A"),
        ("OEM branch", env.get("OEM_BRANCH") or "N/A"),
        ("Variant / mode", f"{a.variant} / TOP"), ("ARM64 page size", a.page_size),
        ("LTO in final config", lto), ("Patch channel", patch_channel), ("Run", run_url),
    ])

    lines += ["### 🌳 Kernel source and OnePlus dependency", ""]
    required_provenance = {
        "source", "family", "commit", "kernel", "patch_level", "files",
        "base_manifest", "source_digest", "git_clean_before_features", "scope",
    }
    if isinstance(provenance, dict) and required_provenance.issubset(provenance):
        prov_source = provenance.get("source")
        prov_commit = provenance.get("commit")
        identity_mismatches = []
        if env.get("KERNEL_SOURCE") and env["KERNEL_SOURCE"] != prov_source:
            identity_mismatches.append("source")
        if env.get("KERNEL_SOURCE_COMMIT") and env["KERNEL_SOURCE_COMMIT"] != prov_commit:
            identity_mismatches.append("resolver/actual commit")
        lines += [
            "**Build dependency order:** source resolver → exact source checkout → **fail-closed actual-tree preflight** → feature patches → final `.config` → compile and package.",
            "",
            "The preflight runs before feature integration. A wrong repository, branch, family, actual HEAD, kernel version/sublevel, patch level, dirty pre-feature tree or missing base manifest stops the build instead of producing an ambiguous kernel.",
            "",
        ]
        if identity_mismatches:
            lines += [f"⚠️ Summary inputs disagree with source provenance for: **{md(', '.join(identity_mismatches))}**. Trust the attached JSON for investigation; do not treat this report as a clean identity pass.", ""]
        oem = provenance.get("oem") if isinstance(provenance.get("oem"), dict) else {}
        table(lines, ("Source field", "Recorded evidence"), [
            ("Preflight result", "✅ Recorded before feature patches" if not identity_mismatches else "⚠️ Recorded, but summary identity mismatch"),
            ("Source type", prov_source),
            ("OnePlus repository", oem.get("repo") if prov_source == "oneplus" else "not applicable (AOSP common)"),
            ("OnePlus selected branch", oem.get("branch") if prov_source == "oneplus" else "not applicable"),
            ("Exact actual HEAD", prov_commit),
            ("Actual Makefile kernel", provenance.get("kernel")),
            ("GKI family", provenance.get("family")),
            ("Selected base patch level", provenance.get("patch_level")),
            ("Clean tree before features", "✅ yes" if provenance.get("git_clean_before_features") is True else "⚠️ not confirmed"),
            ("Source identity digest", provenance.get("source_digest")),
            ("Evidence scope", provenance.get("scope")),
        ])
        if prov_source == "oneplus":
            lines += [
                "In OnePlus mode only `kernel/common` is replaced by the OnePlus tree. Kleaf tooling, prebuilts and the surrounding build environment still come from the AOSP base manifest selected for that GKI release.",
                "",
            ]
            if isinstance(device, dict):
                table(lines, ("Fail-closed OnePlus gate", "Required by .github/device.json", "Actual / result"), [
                    ("Repository", device.get("oneplus_kernel_repo"), f"{oem.get('repo', 'N/A')} — ✅ preflight passed"),
                    ("Branch pattern", device.get("oneplus_branch_match"), f"{oem.get('branch', 'N/A')} — ✅ preflight passed"),
                    ("Stock family", device.get("stock_kernel_family"), f"{provenance.get('family')} — ✅ preflight passed"),
                    ("Stock minimum sublevel", device.get("stock_sublevel"), f"{provenance.get('kernel')} — ✅ not older"),
                    ("Stock minimum firmware patch", device.get("stock_patch_level"), f"{provenance.get('patch_level')} — ✅ not older"),
                    ("Exact resolver commit", source_commit, f"{prov_commit} — ✅ actual HEAD checked"),
                ])
            else:
                lines += [f"⚠️ Device rule file: {md(device_error)}; the recorded preflight above remains available, but expected rules cannot be rendered.", ""]
        else:
            table(lines, ("AOSP source gate", "Evidence"), [
                ("Actual HEAD equals resolver commit", f"✅ {prov_commit}"),
                ("Actual Makefile kernel equals selected input", f"✅ {provenance.get('kernel')}"),
                ("AOSP base manifest", "✅ Required and fingerprinted"),
            ])
        manifest = provenance.get("base_manifest") if isinstance(provenance.get("base_manifest"), dict) else {}
        table(lines, ("Base manifest field", "Value"), [
            ("Path in build workspace", manifest.get("path", "N/A")),
            ("SHA-256", manifest.get("sha256", "N/A")),
            ("Bytes", manifest.get("bytes", "N/A")),
        ])
        fingerprints = provenance.get("files") if isinstance(provenance.get("files"), dict) else {}
        table(lines, ("Prefeature source file", "SHA-256", "Bytes"), [
            (name, item.get("sha256"), item.get("bytes"))
            for name, item in fingerprints.items() if isinstance(item, dict)
        ] or [("None recorded", "⚠️ incomplete provenance", "N/A")])
        lines += [
            "**Config relationship:** feature integration and Kconfig resolution happen after this source capture. The attached `kernel-final.config` and capability report describe the resulting build. The intentionally stock `/proc/config.gz` compatibility payload is not the custom final `.config` and must not override positive runtime driver evidence in device diagnostics.",
            "",
            "**Validation boundary:** these hashes and gates establish source/build identity only. They do not attest physical boot, vendor-module compatibility in use, thermal behavior or runtime writability.",
            "",
        ]
    else:
        detail = provenance_error if not isinstance(provenance, dict) else "incomplete required fields"
        lines += [
            f"⚠️ **Source preflight evidence unavailable: {md(detail)}.** No OnePlus/AOSP identity pass is inferred from this summary.",
            "",
            "On a failed or cancelled job this section is best-effort and does not create a second summary failure. On a job marked successful, missing provenance is an evidence gap to investigate, not a pass.",
            "",
        ]
        table(lines, ("Unresolved summary input", "Value"), [
            ("Selected source", source), ("Resolver commit", source_commit),
            ("OEM repository", env.get("OEM_REPO") or "N/A"),
            ("OEM branch", env.get("OEM_BRANCH") or "N/A"),
        ])

    lines += ["### 🧩 Components", ""]
    component_rows = [
        ("Root implementation", root_impl, root_version, root_commit),
        ("Root manager", root_manager, "N/A", "N/A"),
        ("SUSFS", (env.get("KSU_VERSION") or "integrated") if "SUSFS" in features else "not requested", a.susfs_version if "SUSFS" in features else "N/A", (a.susfs_commit or env.get("SUSFS_COMMIT") or "N/A") if "SUSFS" in features else "N/A"),
        ("NoMount", "included" if "NoMount" in features else "not requested", "N/A", a.nomount_commit if "NoMount" in features else "N/A"),
        ("Baseband Guard", "included" if "BBG" in features else "not requested", "N/A", env.get("BBG_COMMIT_FULL") or "N/A"),
        ("DroidSpaces", "included" if "DS" in features else "not requested", "N/A", a.droidspaces_commit or "N/A"),
        ("VPN Hide", env.get("VPNHIDE_MODE") or ("requested" if "VPN" in features else "not requested"), env.get("VPNHIDE_VERSION") or "N/A", env.get("VPNHIDE_COMMIT") or "N/A"),
        ("USB Arsenal", "included" if "USB" in features else "not requested", "N/A", env.get("USB_ARSENAL_COMMIT") or "N/A"),
        ("kernel_patches", "input pin", "N/A", a.kernel_patches_commit or "not pinned"),
        ("AnyKernel3", "input pin", "N/A", a.anykernel3_commit or "not pinned"),
    ]
    table(lines, ("Component", "State", "Version", "Commit / pin"), component_rows)

    lines += ["### ✨ Requested feature groups", ""]
    feature_rows = []
    for flag in features:
        label, description = FEATURES.get(flag, (f"⚙️ {flag}", "Custom feature flag; inspect its integration action"))
        feature_rows.append((label, flag, description))
    table(lines, ("Feature", "Flag", "Scope"), feature_rows or [("None", "—", "No optional feature group requested")])

    lines += [
        "### 📚 Feature behavior, power and performance",
        "",
        "All requested groups are explained here in one place, including long-standing integrations. These notes describe mechanisms and trade-offs; none is a measured percentage improvement.",
        "",
    ]
    for flag in features:
        label = FEATURES.get(flag, (f"⚙️ {flag}", ""))[0]
        mechanism, tradeoff, boundary = FEATURE_GUIDE.get(flag, (
            "Custom feature flag; inspect its integration action and patch inventory.",
            "No generic power or performance effect is inferred.",
            "Build inclusion alone is not runtime validation."))
        lines += [
            f"#### {label} · `{md(flag)}`", "",
            f"- **What changes:** {mechanism}",
            f"- **Power / performance:** {tradeoff}",
            f"- **Boundary:** {boundary}", "",
        ]

    lines += ["### 🩹 Patch decisions", ""]
    inventory, inventory_error = read_json(workspace / "patch-inventory.json")
    if isinstance(inventory, dict):
        counts = Counter(p.get("state", "unknown") for p in inventory.get("patches", []) if isinstance(p, dict))
        decisions = Counter(p.get("decision", "unknown") for p in inventory.get("patches", []) if isinstance(p, dict))
        lines.append(f"Channel `{md(inventory.get('channel', patch_channel))}`; target `{md(inventory.get('target', {}))}`.")
        lines.append("")
        lines.append("States: " + ", ".join(f"**{md(k)}={v}**" for k, v in sorted(counts.items())) + ". Decisions: " + ", ".join(f"**{md(k)}={v}**" for k, v in sorted(decisions.items())) + ".")
        lines.append("")
        rows = [(p.get("name"), p.get("state"), p.get("decision"), p.get("reason"), str(p.get("sha256", ""))[:12])
                for p in inventory.get("patches", []) if isinstance(p, dict)]
        table(lines, ("Patch", "State", "Decision", "Reason", "SHA-256"), rows or [("None", "—", "—", "Inventory is empty", "—")])
    else:
        lines += [f"⚠️ Patch inventory: {md(inventory_error)}.", ""]
    performance = Path(a.performance_report)
    if performance.is_file():
        lines += ["#### 🚀 Main Performance pass", "", performance.read_text(encoding="utf-8").strip(), ""]
    else:
        lines += ["Main Performance pass report: not generated (Perf disabled or the build stopped earlier).", ""]

    lines += ["### 🛡️ Safety and evidence gates", ""]
    rejects = env.get("REJ_COUNT") or "not recorded"
    if rejects == "0":
        rejects = "✅ 0"
    table(lines, ("Gate", "Evidence"), [
        ("Patch rejects", rejects),
        ("KMI CRC guard", gate_state(a.job_status, workspace / "kmi-crc-report.txt")),
        ("Stock config guard", gate_state(a.job_status, workspace / "stock-config-report.txt")),
        ("Final resolved .config", "✅ Preserved" if (workspace / "kernel-final.config").is_file() else "⚠️ Not available"),
        ("Physical boot / vendor modules / thermal", "⚠️ Requires separate on-device validation"),
    ])

    lines += ["### 🎛️ ASB capability contract", ""]
    capabilities, caps_error = read_json(workspace / "kernel-capabilities.json")
    if isinstance(capabilities, dict):
        lines.append(f"Schema `{md(capabilities.get('schema'))}`; final config SHA-256 `{md(capabilities.get('final_config_sha256'))}`.")
        lines.append("")
        rows = [(name, item.get("build_state"), item.get("runtime_state")) for name, item in capabilities.get("capabilities", {}).items()]
        table(lines, ("Capability", "Final config", "Runtime"), rows)
        controls = capabilities.get("asb_contract", {}).get("controls", {})
        rows = [(name, item.get("runtime_path"), item.get("build_state"), item.get("runtime_state"), item.get("writable")) for name, item in controls.items()]
        table(lines, ("Bounded control", "Standard Linux path", "Build", "Runtime", "Writable"), rows)
        lines += ["ASB must rediscover every path, capture a baseline and verify readback. VPN Hide and network policy keep their existing owners; no arbitrary kernel writer is added.", ""]
    else:
        lines += [f"⚠️ Capability report: {md(caps_error)}.", ""]

    lines += [
        "#### ASB controls: mechanism and trade-offs",
        "",
        "Every bounded control follows the same path: registered key → fixed standard kernel node → mode validation → original-value baseline → bounded write → live readback → Stock/uninstall restore. **Stock is inert before a baseline exists.** No arbitrary path/value writer is exposed.",
        "",
    ]
    for key, title, mechanism, tradeoff, boundary in ASB_CONTROL_GUIDE:
        lines += [
            f"##### {title} · `{key}`", "",
            f"- **Mechanism:** {mechanism}",
            f"- **Power / performance:** {tradeoff}",
            f"- **Availability / restore:** {boundary}", "",
        ]

    rekernel, rk_error = read_json(workspace / "rekernel-provenance.json")
    if isinstance(rekernel, dict):
        lines += ["### 🧊 Re-Kernel", ""]
        table(lines, ("Field", "Evidence"), [
            ("Source", rekernel.get("repo")),
            ("Commit", rekernel.get("commit")),
            ("Mode / transport", f"{rekernel.get('mode')} / {rekernel.get('transport')}"),
            ("Source tree SHA-256", rekernel.get("source_tree_sha256")),
            ("Runtime / freezer compatibility", "⚠️ Requires separate on-device validation"),
        ])
    wakeup_sha = env.get("ASB_WAKEUP_BLOCKER_PATCH_SHA256")
    if wakeup_sha:
        lines += ["### 🌙 ASB wakeup blocker", "",
                  f"Reviewed patch SHA-256 `{md(wakeup_sha)}`. Exact-name list, empty by default; physical suspend/modem/alarm/call/charging behavior is not attested.", ""]

    sdp_sha = env.get("OPLUS_SDP_900MA_PATCH_SHA256")
    if sdp_sha:
        lines += ["### 🔌 OPLUS SDP 900 mA", "",
                  f"Vendored patch SHA-256 `{md(sdp_sha)}`. Only the USB data-port limit is raised (500 → 900 mA); check `/proc/sdp900ma` on the device. Actual charging current is not attested.", ""]

    lines += ["### 📦 Artifacts and next step", ""]
    artifact_rows = [
        (f"{a.file_name}-AnyKernel3", "Flashable kernel package"),
        (f"{a.file_name}-BuildInfo", "This report, final config, source provenance, capabilities and patch inventory"),
        (f"{a.file_name}-Pins", "Exact component pins and manual device-validation template"),
    ]
    if "VPN" in features:
        artifact_rows.append(("VPNHide-Builtin-Module", "Required matching companion for built-in VPN Hide; do not install vpnhide-kmod"))
    if "NoMount" in features:
        artifact_rows.append(("NoMount-Metamodule", "Separate NoMount companion"))
    table(lines, ("Artifact / file", "Purpose"), artifact_rows)
    lines += [
        "Flash only the matching build and keep a known-good kernel available. After a personal boot check, paste the run URL into Mark build as known-good; the optional full checklist gives broader device attestation.",
        "",
    ]
    fixes = Path("/tmp/kernel-fixes-summary.md")
    if fixes.is_file():
        lines += ["### 🔧 Kernel fixes", "", fixes.read_text(encoding="utf-8").strip(), ""]
    return "\n".join(lines).rstrip() + "\n"


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--job-status", required=True)
    ap.add_argument("--file-name", required=True)
    ap.add_argument("--kernel-version", required=True)
    ap.add_argument("--sublevel", required=True)
    ap.add_argument("--android-version", required=True)
    ap.add_argument("--os-patch-level", required=True)
    ap.add_argument("--variant", default="Normal")
    ap.add_argument("--page-size", default="4k")
    ap.add_argument("--feature-set", default="")
    ap.add_argument("--family", default="")
    ap.add_argument("--version-string", default="")
    ap.add_argument("--susfs-version", default="N/A")
    ap.add_argument("--susfs-commit", default="")
    ap.add_argument("--nomount-commit", default="")
    ap.add_argument("--droidspaces-commit", default="")
    ap.add_argument("--kernel-patches-commit", default="")
    ap.add_argument("--anykernel3-commit", default="")
    ap.add_argument("--performance-report", default="/tmp/performance-patches-summary.md")
    return ap


def main() -> int:
    args = parser().parse_args()
    output = Path(args.output)
    output.write_text(render(args, dict(os.environ)), encoding="utf-8")
    print(f"Structured KRILIKD BuildInfo written: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
