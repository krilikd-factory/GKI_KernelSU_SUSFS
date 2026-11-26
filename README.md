<p align="center">
  <a href="README.md"><img src="https://img.shields.io/badge/🇬🇧%20English-16a34a?style=flat-square" alt="English"></a>
  <a href="docs/README.ru.md"><img src="https://img.shields.io/badge/🇷🇺%20Русский-1f2937?style=flat-square" alt="Русский"></a>
</p>

<div align="center">

# ⚡ KRILIKD Kernel

**Custom GKI kernels for OnePlus devices**<br>
KernelSU · SUSFS · modern schedulers · AutoSystemBoost integration

[![Android GKI](https://img.shields.io/badge/Android-GKI%202.0-3DDC84?logo=android&logoColor=white)](#-compatibility)
[![Builds](https://img.shields.io/badge/GitHub-Actions-2088FF?logo=githubactions&logoColor=white)](https://github.com/krilikd-factory/KRILIKD-kernel/actions/workflows/asb-kernel-topic.yml)
[![Telegram](https://img.shields.io/badge/Telegram-AutoSystemBoost-26A5E4?logo=telegram&logoColor=white)](https://t.me/AutoSystemBoost)
[![License](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)

[Compatibility](#-compatibility) · [Features](#-features) · [Download and install](#-download-and-install) · [Build your own](#-build-your-own) · [Documentation](#-documentation)

</div>

> **KRILIKD** is a build project based on Android GKI and the Wild Kernels ecosystem. It brings together root integrations, compatibility patches and device-tuning tools. The available features depend on the kernel family and build options.

> [!CAUTION]
> An incompatible kernel can leave your device unable to boot. Before installation, check the **stock kernel family**, firmware compatibility and memory page size; back up the affected partitions and prepare a recovery method for your specific device. A successful build does not prove that a kernel will boot or work correctly on your phone.

## 📱 Compatibility

The group workflow currently targets three **4 KB** builds, using KernelSU-Next by default:

| Device | Kernel family and version | GKI patch level |
|---|---|---|
| **OnePlus 12 / OnePlus Ace 5** | `android14-6.1.157` | `2025-12` |
| **OnePlus Ace 6** | `android15-6.6.118` | `2026-01` |
| **OnePlus 15 / 15R** | `android16-6.12.69` | `2026-03` |

The **Ace 5** uses the OnePlus 12 build. Check it against your firmware and hardware revision: sharing a GKI family alone does not guarantee a successful boot. The number after `android` in the kernel name identifies the GKI branch and can differ from your phone's Android version.

Find the running kernel in device settings or with:

```sh
adb shell uname -r
```

Check the family, patch level, page size, root implementation and feature set **for the chosen file and its BuildInfo**. Build-verified does not mean boot-verified; test on your device.

## ✨ Features

| Feature | What it brings | Build and device notes |
|---|---|---|
| 👑 **Root** | KernelSU-Next, KernelSU, BakaSU, SukiSU Ultra, KowSU, FinaSU or a no-root build | Select a root implementation and use its matching manager. Compatibility with SUSFS depends on the selected variant. |
| 🛡️ **SUSFS** | Kernel-side privacy integration for compatible KernelSU setups | Requires a compatible userspace setup; availability does not guarantee that every app will accept the device. |
| 🧩 **NoMount** | Metamodule support for the module mounting setup | Provided separately when requested; install a compatible metamodule for your root setup. |
| 🔐 **Baseband Guard** | Protects critical partitions and device nodes from unauthorized writes | Applies when BBG is selected for the build. |
| 🌐 **Networking** | WireGuard, BBR/BBRv3, CUBIC, CAKE, IPSet, IPv4/IPv6 NAT, conntrack, CIFS and TTL/HL targets | Kernel and userspace support vary; inspect the final configuration and verify on the device. |
| 🕵️ **VPN Hide** | Kernel-side VPN Hide integration | Use the matching built-in companion; do not install `vpnhide-kmod` on top. |
| 🔌 **USB Arsenal** | USB gadget features and ISO/DVD/CD-ROM support where applicable | Available modes depend on the kernel, device and firmware. |
| 🎮 **NTSync** | Windows NT-style synchronization primitives for compatible software | Available when selected for a compatible kernel family. |
| 📦 **DroidSpaces** | Container runtime support, including macvlan where configured | Container capabilities depend on the final config and userspace. |
| 🔎 **BPF & Perf** | BTF, eBPF, FUSE-BPF and performance/tracing capabilities | A built feature does not guarantee that a particular program will load or run. |
| 🧠 **BORE** | Burst-Oriented Response Enhancer, a CPU scheduler extension on top of EEVDF | Optional for `android16-6.12`; a runtime switch is exposed where supported. |
| 💾 **ADIOS** | Adaptive Deadline I/O Scheduler for block devices | Optional for `android16-6.12`; available for runtime selection and not forced as the default. |
| 🚀 **Performance tuning** | Reviewed scheduler, memory, wakeup, filesystem and logging patches | Selection and patch decisions are recorded in BuildInfo; no fixed speed gain is promised. |
| 🔋 **ASB wakeup blocker · AWL** | Optional exact-name wakeup-source rules | `android16-6.12`; starts with an empty root-only policy. |
| 🧊 **Re-Kernel** | Built-in app and network event delivery for compatible userspace tools | Optional `android16-6.12` integration; third-party freezer compatibility is not assumed. |
| ⚡ **SDP 900 mA** | Optional USB data-port input-limit adjustment | `android16-6.12`; actual charging behavior depends on the device and firmware. |
| 🔧 **Ptrace · Unicode** | Compatibility fixes selected for matching kernel trees | Availability is family- and source-dependent. |
| 🧠 **Memory & pressure** | MGLRU, PSI and zram-related capabilities | ASB discovers available interfaces at runtime; exact support depends on the kernel. |

**BORE, ADIOS, Re-Kernel, AWL and SDP 900 mA are available for the supported `android16-6.12` branch; they are not automatically present in every kernel.** AWL starts with an empty rule list. A configuration option alone does not guarantee that its interface is available or functional on a particular firmware.

Use the build's **BuildInfo**, final `.config` and on-device checks to establish what is actually available. Features can be enabled or disabled at build time; see the [feature reference](docs/features.md) and [ASB integration guide](docs/asb-integration.md).

### 🎛️ Working with AutoSystemBoost

[AutoSystemBoost](https://github.com/krilikd-factory/AutoSystemBoost) controls available settings from userspace. Before applying supported settings, ASB checks the interface, saves the original value and reads back the result; some controls may be unavailable on unsupported kernels. Blocking wakeup sources by exact name requires a compatible kernel interface and user-defined rules.

`kernel_sched_nr_migrate` uses `/sys/kernel/debug/sched/nr_migrate` via a temporary private debugfs mount when available; existence and writability are determined on the device, not assumed from the build. See [ASB integration](docs/asb-integration.md).

### 🔍 Build evidence & artifacts

| Artifact | What you can inspect |
|---|---|
| 📦 **AnyKernel3** | Flashable kernel ZIP for the selected build |
| 📋 **BuildInfo** | Final `.config`, Source provenance, Patch lifecycle decisions, capabilities and safety checks |
| 📌 **Pins** | Exact component revisions and a device-validation template |
| 🧩 **Companion modules** | Matching VPN Hide and NoMount files when selected |
| 🛠️ **Rejects** | Patch rejects, when produced by the build |

Sources, component revisions and selected features are recorded per run. New upstream patches follow a reviewed lifecycle; a candidate build is not automatically treated as device-tested. See [the build guide](docs/build-from-fork.md) for details.

## 📥 Download and install

1. Choose the ZIP for your device and stock GKI branch in the [ASB group](https://t.me/AutoSystemBoost) or among the artifacts of a matching [build run](https://github.com/krilikd-factory/KRILIKD-kernel/actions/workflows/asb-kernel-topic.yml). GitHub may require sign-in to download artifacts; artifacts have a retention period.
2. Check the build details and prepare backups and a recovery method suitable for your firmware and partition layout. Recovery depends on the device and installation method; no single set of partitions is assumed to work for every device.
3. Install the **AnyKernel3 ZIP** using a method appropriate for your phone. If the build requires a separate metamodule or manager, use its matching files and follow their instructions.
4. After rebooting, check the kernel version, root, connectivity, audio, camera and other functions you need. If a problem occurs, save a diagnostic report and, if available, `console-ramoops`.

[Installation guide](docs/installation.md) · [Kernel Flasher](docs/kernelflasher.md) · [After installation](docs/post-install.md)

> Built-in VPN Hide uses its matching companion module. Do not install `vpnhide-kmod` on top of it. NoMount is provided as a separate metamodule when selected for the build.

## 🏗️ Build your own

Open **Actions → [Build Kernels](https://github.com/krilikd-factory/KRILIKD-kernel/actions/workflows/main.yml) → Run workflow** and choose a specific family, patch level, root implementation and feature set. Avoid `All` for a first build: it starts many jobs. The **[KRILIKD · ASB group build](https://github.com/krilikd-factory/KRILIKD-kernel/actions/workflows/asb-kernel-topic.yml)** workflow prepares the three families listed above and supports root selection and feature exclusions; Telegram publishing is controlled separately and requires configured secrets.

Keep the **AnyKernel3**, **BuildInfo** and **Pins** artifacts for review. They contain source and component details, the final configuration and build checks. A candidate build is not automatically attested on a device. See the [build guide](docs/build-from-fork.md) for the full procedure.

## 📚 Documentation

- [Features and components](docs/features.md) · [ASB integration](docs/asb-integration.md)
- [OnePlus 15 / TOP and change history](docs/KRILIKD.md) · [Implementation status](docs/KRILIKD_IMPLEMENTATION_STATUS.md)
- [Build in your own fork](docs/build-from-fork.md) · [Installation](docs/installation.md) · [Post-install checks](docs/post-install.md)
- [License](LICENSE) · [Third-party notices and licenses](THIRD_PARTY_NOTICES.md)

## 🙏 Credits

This project builds on the work of [Android Common Kernel / GKI](https://android.googlesource.com/kernel/common/), [Wild Kernels](https://github.com/WildKernels/GKI_KernelSU_SUSFS), [WildKernels/kernel_patches](https://github.com/WildKernels/kernel_patches) and the developer community. Thanks to the authors of [KernelSU](https://github.com/tiann/KernelSU), [KernelSU-Next](https://github.com/KernelSU-Next/KernelSU-Next), [SUSFS](https://gitlab.com/simonpunk/susfs4ksu), [NoMount](https://github.com/maxsteeel/nomount), [Baseband Guard](https://github.com/vc-teahouse/Baseband-guard), [DroidSpaces](https://github.com/ravindu644/Droidspaces-OSS), [Re-Kernel](https://github.com/Sakion-Team/Re-Kernel), [VPN Hide](https://github.com/okhsunrog/vpnhide), [USB Arsenal](https://github.com/ravindu644/USB-Arsenal), [BORE](https://github.com/firelzrd/bore-scheduler), [ADIOS](https://github.com/firelzrd/adios) and [AnyKernel3](https://github.com/osm0sis/AnyKernel3). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for full attribution and license details.
