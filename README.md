<div align="center">

# ⚡ KRILIKD
### Evidence-first Android kernel forge

**GKI 2.0 build framework · reproducible component pins · reviewed patch lifecycle · ASB integration**

[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)
[![Third-party notices](https://img.shields.io/badge/upstream-attribution-lightgrey.svg)](THIRD_PARTY_NOTICES.md)
[![Primary target](https://img.shields.io/badge/OnePlus_15-SM8850-7c3aed.svg)](docs/KRILIKD.md)
[![Kernel family](https://img.shields.io/badge/android16--6.12-TOP-059669.svg)](docs/KRILIKD.md)
[![Known-good](https://img.shields.io/badge/known--good-6.12.69_boot--attested-0ea5e9.svg)](.github/known-good.json)

</div>

> [!CAUTION]
> Flashing a custom kernel can leave a device unbootable. Keep a known-good boot image, back up your data and flash only the artifact matching your device, kernel family and patch level. **Build-verified does not mean boot-verified.**

## 🚀 What makes this fork different

KRILIKD is an independent fork built on the Wild Kernels build framework and its upstream ecosystem. The fork-specific work is not a promise of “more FPS”; it is a more explicit engineering and release trail:

- 🧬 **Source provenance** — the downloaded AOSP or OnePlus tree is checked after checkout, not merely selected by a workflow label.
- 🩹 **Patch lifecycle** — new upstream patches start as `observed`; only reviewed `candidate` or `enabled` entries can reach their intended channels.
- 🎯 **Target filtering** — family, device and SoC are hard filters for reviewed patches.
- 🛡️ **Boot-safety evidence** — KMI CRC and stock-config guards run after Kconfig resolution; rejected patches are collected.
- 🧾 **Structured BuildInfo** — final `.config`, source identity, component commits, patch decisions, capabilities and safety reports travel with the build.
- 🎛️ **Bounded ASB contract** — standard Linux interfaces, runtime rediscovery, closed value lists, baseline capture, readback and exact restore.
- ✅ **Known-good promotion** — a stable run needs separate manual on-device evidence; a successful CI job is not promoted automatically.

## 💎 Primary build: OnePlus 15 TOP

| Item | Target |
|---|---|
| 📱 Device | OnePlus 15 / CPH2745 |
| 🔥 Platform | SM8850 |
| 🤖 ROM baseline | ColorOS 17 |
| 🐧 Kernel family | `android16-6.12` |
| 📌 Verified baseline in project config | `6.12.69`, patch level `2026-03` |
| 📄 ARM64 pages | `4k` |
| 👑 Root implementation | KernelSU-Next + SUSFS |
| 🧠 Schedulers | BORE (CPU, on top of EEVDF) + ADIOS (block I/O, selectable at runtime) |
| ⚙️ Mode | **TOP** — one curated mode, not a hidden overclock preset |
| 🌱 Source choice | AOSP GKI or compatible OnePlus source, subject to provenance checks |

### ✅ Current known-good build

| Item | Value |
|---|---|
| `uname -r` | `6.12.69-android16-6-KRILIKD` |
| Feature set | SUSFS, NoMount, BBG, NET, DroidSpaces, NTSync, Ptrace, Unicode, BPF, Perf, BORE, ADIOS, VPN Hide, USB Arsenal, Re-Kernel, AWL, SDP900 |
| KernelSU-Next | `v3.4.2`, version code `33555` |
| Source | AOSP GKI, LTO off |
| Status | `boot_attested_only`, 21 Nov 2026 ([run 44927495170](https://github.com/krilikd-factory/GKI_KernelSU_SUSFS/actions/runs/44927495170)) |

“Boot attested” means the phone booted with this image. Vendor modules, display, network, camera, audio, storage, thermal behavior and ASB were not separately attested. The live record is [`.github/known-good.json`](.github/known-good.json).

The target matrix evolves with upstream and stock kernels. The authoritative values for a particular run are its **Pins** and **BuildInfo**, not this README snapshot.

## ✨ Feature map

Feature availability depends on the selected family and `feature_set`. The table describes what the build framework can integrate; a run's BuildInfo identifies what was actually requested and confirmed in its final configuration.

| Area | Features | Practical scope |
|---|---|---|
| 👑 **Root** | KernelSU, KernelSU-Next, BakaSU, FinaSU, SukiSU Ultra, KowSU | Selectable root implementations; manager and kernel commits are pinned per run |
| 🛡️ **SUSFS** | susfs4ksu, version-gated compatibility fixes | Kernel-side integration requires the matching userspace setup; no universal invisibility claim |
| 🧩 **Mounts** | NoMount, Mountify support | NoMount companion is produced separately when requested |
| 🔐 **Partition guard** | Baseband Guard | Restricts unauthorized access to critical partitions and device nodes |
| 🌐 **Networking** | WireGuard, BBR/BBRv3 where supported, CUBIC, CAKE, IPSet, IPv4/IPv6 NAT, conntrack/connmark, CIFS, TTL/HL targets | NET builds fail if CAKE/IP sets disappear from the final config; runtime `tc`/`ipset` operation still needs device validation |
| 🧊 **App events** | Re-Kernel built-in with Generic Netlink and network events | Optional `RK` integration for `android16-6.12`; exact source commit is pinned, third-party freezer compatibility is not assumed |
| 🕵️ **VPN Hide** | Built-in `CONFIG_VPNHIDE=y`, full or net-only integration | Controlled by its own app and matching built-in companion; **do not install `vpnhide-kmod` on top** |
| 🔌 **USB gadget** | USB Arsenal integration, ISO/DVD/CD-ROM mode where applicable | HID and NCM availability still depends on the selected kernel baseline |
| 🎮 **Compatibility** | NTSync, DroidSpaces-OSS (v7, incl. macvlan mode) | Synchronization primitives and container runtime support; a DS build fails if `CONFIG_MACVLAN` is missing from the final config. On the OnePlus 15, `POSIX_MQUEUE` and `DEVTMPFS` stay off by the device policy |
| 📊 **Tracing** | BTF, eBPF, FUSE-BPF | Build support is not proof that a particular BPF program will load or run |
| 🧠 **Memory / pressure** | PSI, MGLRU, zram/zsmalloc discovery | ASB must discover live interfaces; config presence alone does not prove runtime state |
| 🧠 **Schedulers** | BORE 7.0.0 CPU scheduler, ADIOS 3.3.0 block-I/O scheduler | Both opt-in through `use_perf=bore_adios` on `android16-6.12`; `/proc/sys/kernel/sched_bore=0` returns to plain EEVDF and ADIOS never replaces the stock default I/O scheduler by itself |
| 🚀 **Performance pass** | Reviewed memory, scheduler, wakeup, filesystem and logging patches | Dry-run and policy gates first; skipped or held patches remain visible in BuildInfo |
| 🔋 **Power controls** | Power-efficient workqueues, KFENCE sampling disabled by default, optional ASB exact-name wakeup blocker | `AWL` starts with an empty root-only policy; no automatic block rules or fixed battery-saving percentage |
| 🔧 **Additional fixes** | Ptrace and Unicode fixes, family-specific compatibility work | Applied only where the version and source tree match |

Full feature notes: **[docs/features.md](docs/features.md)**. OnePlus 15/TOP details: **[docs/KRILIKD.md](docs/KRILIKD.md)**.

## 🎛️ KRILIKD × AutoSystemBoost

The kernel project publishes evidence; ASB owns userspace policy. The current contract exposes descriptions of six **standard Linux** controls:

| Control | Fixed runtime interface | Closed requests | Required behavior |
|---|---|---|---|
| `mglru_mode` | `/sys/kernel/mm/lru_gen/enabled` | `stock`, `off`, `on`, `smart` | Numeric MGLRU readback; Smart `5/7` only in Smart mode |
| `kernel_timer_migration` | `/proc/sys/kernel/timer_migration` | `stock`, `off`, `on` | Exact `0/1` readback |
| `kernel_sched_nr_migrate` | `/sys/kernel/debug/sched/nr_migrate` | `stock`, `1`, `2`, `4`, `8`, `16` | Bounded integer write and exact readback |
| `kernel_sched_stats` | `/proc/sys/kernel/sched_schedstats` | `stock`, `off` | Exact `0/1` readback; no unconditional profile writer |
| `kernel_sched_autogroup` | `/proc/sys/kernel/sched_autogroup_enabled` | `stock`, `off`, `on` | Exact `0/1` readback; config presence is not runtime proof |
| `kernel_sched_child_runs_first` | `/proc/sys/kernel/sched_child_runs_first` | `stock`, `off`, `on` | Exact `0/1` readback |

For every control: **discover → check access → capture baseline → write allowlisted value → read back → restore exactly on Stock/uninstall**. There is no arbitrary path/value writer and no broad `/sys/kernel/krilikd/*` ABI, and vendor thermal limits are not bypassed.

The optional `AWL` wakeup blocker, the debugfs access rule, the SUSFS API guard and what is deliberately left out are described in **[docs/asb-integration.md](docs/asb-integration.md)**.

## 🏗️ Build your own

1. **Fork this repository.** Do not use the upstream Wild repository as the source of fork-specific settings.
2. Open **Actions → Build Kernels** and select one family, patch level, root implementation and feature set. The dispatch form has exactly 25 inputs, within GitHub's startup limit. **Compatibility patches** selects Ptrace/Unicode together or separately; **Re-Kernel / ASB wakeup blocker / SDP 900 mA** selects those Android 16 / 6.12 features together (`all`) or separately. No feature choice was removed.
3. Choose `stable` for enabled patches only or `candidate` for an explicitly reviewed candidate experiment.
4. For the OnePlus target, choose AOSP or OnePlus source and keep the provenance/stock-version guards enabled.
5. Download the matching artifacts from the completed run.

Detailed guide: **[docs/build-from-fork.md](docs/build-from-fork.md)**.

### 📦 What a run gives you

| Artifact | Contents |
|---|---|
| `*-AnyKernel3` | Flashable kernel package with KRILIKD installer presentation |
| `*-BuildInfo` | Structured Markdown report, final `.config`, source provenance, capability JSON, patch inventory, KMI and stock-config reports |
| `*-Pins` | Exact component pins and a device-validation template |
| `VPNHide-Builtin-Module` | Matching companion for built-in VPN Hide when that feature is part of the release |
| `NoMount-Metamodule` | Separate NoMount companion when requested |
| `*-Rejects` | Patch rejects, if any were produced |

The BuildInfo report separates **requested features**, **final-config capabilities**, **patch decisions** and **on-device unknowns**. The main Performance patch pass and the reviewed upstream-patch inventory are consolidated there instead of living in unrelated job sections.

## ⚡ Install and validate

- 📖 [Installation](docs/installation.md)
- 💫 [Kernel Flasher](docs/kernelflasher.md)
- 🛠️ [magiskboot method](docs/magiskboot.md)
- ✅ [Post-install checklist](docs/post-install.md)

After a successful personal boot check, open **Actions → Mark build as known-good** and paste only the stable Build Kernels run URL. This records `boot_attested_only`, not a claim that every device function was tested. For broader attestation, optionally paste the completed Pins checklist covering vendor modules, display/touch, Wi-Fi, cellular, camera, audio, storage, thermal behavior and ASB. If the device does not boot, restore the saved kernel and preserve `console-ramoops` evidence where available.

## 📚 Documentation

| Topic | Page |
|---|---|
| OnePlus 15 / TOP build, change log and boundaries (Russian) | [docs/KRILIKD.md](docs/KRILIKD.md) |
| Implementation status by subsystem | [docs/KRILIKD_IMPLEMENTATION_STATUS.md](docs/KRILIKD_IMPLEMENTATION_STATUS.md) |
| ASB integration details | [docs/asb-integration.md](docs/asb-integration.md) |
| Feature reference | [docs/features.md](docs/features.md) |
| Building from a fork | [docs/build-from-fork.md](docs/build-from-fork.md) |
| Installing and first boot | [docs/installation.md](docs/installation.md), [docs/post-install.md](docs/post-install.md) |

## 🔬 Release discipline

- New upstream patches are **observed, not auto-applied**.
- Candidate and enabled entries require provenance, license and target scope.
- Denylist and risky-header review remain independent barriers.
- **Update Check is fail-closed:** only NoMount `module/`, `userspace/`, `update.json` or VPN Hide `builtin/`, `VERSION` diffs can dispatch `release_type=Modules`. Kernel, SUSFS, mixed, unknown, empty or unreachable path evidence triggers a full candidate instead. Module-only runs build only changed companions and skip kernel images, manager/reject and release jobs. NoMount upstream comparison uses the same `master` branch as Build, so known-good pins are never compared with `dev`.
- **Automatic candidates explicitly enable BORE+ADIOS** through `use_perf=bore_adios` on the supported `android16-6.12` target. The recorded known-good `feature_set` is not rewritten: a BORE candidate remains unattested until its own physical boot check and promotion.
- LTO is intentionally disabled for the primary `android16-6.12` target because of the Rust/BTF/Android boot constraint documented in [KRILIKD.md](docs/KRILIKD.md).
- No thermal bypass, hardcoded voltage/frequency table, universal 1 Hz promise or unverified performance percentage is part of this project.

## 🙏 Upstream and credits

KRILIKD retains the projects and attribution that make the fork possible, including:

- [Google GKI / Android common kernel](https://android.googlesource.com/kernel/common/)
- [KernelSU](https://github.com/tiann/KernelSU), [KernelSU-Next](https://github.com/KernelSU-Next/KernelSU-Next) and the other root implementations listed in [features.md](docs/features.md)
- [SUSFS](https://gitlab.com/simonpunk/susfs4ksu) and [susfs4ksu-module](https://github.com/sidex15/susfs4ksu-module)
- [NoMount](https://github.com/maxsteeel/nomount), [Baseband Guard](https://github.com/vc-teahouse/Baseband-guard), [DroidSpaces-OSS](https://github.com/ravindu644/Droidspaces-OSS)
- [Re-Kernel](https://github.com/Sakion-Team/Re-Kernel) by the Sakion Team
- [VPN Hide](https://github.com/okhsunrog/vpnhide), [USB Arsenal](https://github.com/ravindu644/USB-Arsenal)
- [WildKernels/kernel_patches](https://github.com/WildKernels/kernel_patches) and the upstream build-framework lineage
- [BORE](https://github.com/firelzrd/bore-scheduler) and [ADIOS](https://github.com/firelzrd/adios) by **Masahito Suzuki (firelzrd)**
- [AnyKernel3](https://github.com/osm0sis/AnyKernel3) by **osm0sis**

See **[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)** for attribution and licensing. Issues and pull requests should contain build IDs, BuildInfo and non-sensitive device evidence rather than serial numbers, IMEI, account data or network identifiers.
