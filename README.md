<div align="center">

# ⚡ KRILIKD
### Evidence-first Android kernel forge

**GKI 2.0 build framework · reproducible component pins · reviewed patch lifecycle · ASB integration**

[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)
[![Third-party notices](https://img.shields.io/badge/upstream-attribution-lightgrey.svg)](THIRD_PARTY_NOTICES.md)
[![Primary target](https://img.shields.io/badge/OnePlus_15-SM8850-7c3aed.svg)](docs/KRILIKD.md)
[![Primary branch](https://img.shields.io/badge/android16-6.12-TOP-059669.svg)](docs/KRILIKD.md)

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
| ⚙️ Mode | **TOP** — one curated mode, not a hidden overclock preset |
| 🌱 Source choice | AOSP GKI or compatible OnePlus source, subject to provenance checks |

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
| 🎮 **Compatibility** | NTSync, DroidSpaces-OSS | Synchronization primitives and container runtime support |
| 📊 **Tracing** | BTF, eBPF, FUSE-BPF | Build support is not proof that a particular BPF program will load or run |
| 🧠 **Memory / pressure** | PSI, MGLRU, zram/zsmalloc discovery | ASB must discover live interfaces; config presence alone does not prove runtime state |
| 🚀 **Performance pass** | Reviewed memory, scheduler, wakeup, filesystem and logging patches | Dry-run and policy gates first; skipped or held patches remain visible in BuildInfo |
| 🔋 **Power controls** | Power-efficient workqueues, KFENCE sampling disabled by default, optional ASB exact-name wakeup blocker | `AWL` starts with an empty root-only policy; no automatic block rules or fixed battery-saving percentage |
| 🔧 **Additional fixes** | Ptrace and Unicode fixes, family-specific compatibility work | Applied only where the version and source tree match |

Full feature notes: **[docs/features.md](docs/features.md)**. OnePlus 15/TOP details: **[docs/KRILIKD.md](docs/KRILIKD.md)**.

## 🎛️ KRILIKD × AutoSystemBoost

The kernel project publishes evidence; ASB owns userspace policy. The current contract exposes descriptions of three **standard Linux** controls:

| Control | Closed requests | Required behavior |
|---|---|---|
| `mglru_mode` | `stock`, `off`, `on`, `smart` | Numeric MGLRU readback; Smart `5/7` only in Smart mode |
| `kernel_timer_migration` | `stock`, `off`, `on` | Exact `0/1` readback |
| `kernel_sched_nr_migrate` | `stock`, `1`, `2`, `4`, `8`, `16` | Bounded integer write and exact readback |

For every control: **discover → check access → capture baseline → write allowlisted value → read back → restore exactly on Stock/uninstall**.

The optional `AWL` feature adds one deliberately narrow ASB ABI: `/sys/kernel/asb_wakeup/blocklist` accepts only root-issued `add NAME`, `del NAME` and `clear` commands for at most 16 exact wakeup-source names. Its policy is empty by default; `/sys/kernel/asb_wakeup/blocked_events` is a read-only counter. There are no patterns, arbitrary path/value writes, automatic block rules or force-release of locks already held. The final-config gate rejects a selected AWL build without this capability. ASB still has to verify the interface and apply only the user's explicit policy on device.

There is deliberately no arbitrary path/value writer and no broad `/sys/kernel/krilikd/*` ABI. VPN Hide retains its own app/companion owner, network policy remains with the existing ASB network runtime, and vendor thermal limits are not bypassed. No OPlus SDP 900 mA writer is included because no confirmed control/readback ABI was found in the reviewed source and diagnostics.

## 🏗️ Build your own

1. **Fork this repository.** Do not use the upstream Wild repository as the source of fork-specific settings.
2. Open **Actions → Build Kernels** and select one family, patch level, root implementation and feature set.
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

## 🔬 Release discipline

- New upstream patches are **observed, not auto-applied**.
- Candidate and enabled entries require provenance, license and target scope.
- Denylist and risky-header review remain independent barriers.
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
- [AnyKernel3](https://github.com/osm0sis/AnyKernel3) by **osm0sis**

See **[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)** for attribution and licensing. Issues and pull requests should contain build IDs, BuildInfo and non-sensitive device evidence rather than serial numbers, IMEI, account data or network identifiers.
