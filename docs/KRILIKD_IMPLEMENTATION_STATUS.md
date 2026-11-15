# KRILIKD implementation status

This archive implements the safe, source-level part of the KRILIKD + ASB plan:

- `KRILIKD` is the default kernel branding in main, prepare, build and branding action.
- `safe`, `balanced` and `lab` performance profiles are selectable from workflow dispatch.
- The profile is propagated through main -> prepare -> build -> performance action.
- Performance patches remain dry-run gated and incompatible patches are skipped.
- The selected profile is recorded in the known-good candidate metadata.
- KMI CRC guard and `allow_version_mismatch=false` defaults are retained.
- The recommended daily profile is `balanced`.

No blind undervolt, overclock, thermal-limit bypass, fixed refresh-rate override or
IRQ-affinity mutation is included. Those changes require the actual OnePlus 15
vendor/device tree and boot-tested stock KMI data. ASB remains the policy owner;
the future kernel telemetry/lease interface is documented in `KRILIKD_PROFILES.md`.

## Audio, video and photo (ASB dev59)

The Qualcomm audio (ADSP, codec), camera (CAMX), video codec, display and GPU drivers on
the OnePlus 15 are vendor modules loaded from `vendor_dlkm` and compiled against the GKI
KMI. A GKI `Image` built here cannot change them, and editing the core structures they use
would stop them from loading. No "audio", "camera" or "video" driver patch is therefore
shipped; claiming one would be untrue.

What the kernel does provide to ASB for media:

- **UVC gadget function** (`CONFIG_USB_CONFIGFS_F_UVC`, `CONFIG_USB_F_UVC`, stock in GKI
  android14+): ASB's Camera Tools use it for the USB webcam mode through Android's own
  DeviceAsWebcam service. The capability report lists it as `media.usb_webcam` and the build
  summary shows whether the final config still has it. Whether the ROM's USB gadget HAL
  accepts the `uvc` function is only known on the phone.
- **UCLAMP** (`CONFIG_UCLAMP_TASK`): the `kernel_rt_boost` control (real-time floor for audio
  mixers, display composition and camera HAL threads) and the camera guard's cgroup
  `uclamp.max` lift.

## External patch sources reviewed (ASB dev61)

The owner asked for "super patch" branches on GitHub that this fork does not have yet. The
upstream fork's branches and the public patch collections were read against the
android16-6.12 GKI that this fork builds. None was adopted; the reasons are below so the
question does not need to be re-researched.

- **Already here:** the 16 common performance patches from the upstream `kernel_patches`
  collection that apply to 6.12 are applied (dry-run gated). Three more stay held as
  candidates because their effect is a policy change rather than a measured saving:
  `add_timeout_wakelocks_globally`, `reduce_freeze_timeout`, `silence_system_logspam`.
- **Already in the GKI 6.12 defconfig:** `RCU_LAZY`, `RCU_NOCB_CPU_DEFAULT_ALL`,
  `SCHED_CLASS_EXT`, the TEO cpuidle governor and `LRU_GEN`. Patches that only enable these
  add nothing.
- **BORE scheduler branch:** relocates scheduler fields with KABI workarounds, and the branch's
  own notes report boot loops on 6.12. It targets interactivity, not energy, and the phone's
  vendor scheduler (WALT) makes most task placement decisions anyway.
- **HZ=1000 branch:** more timer ticks (more wakeups), and vendor modules are compiled against
  the stock HZ, so jiffies conversions inside them would be wrong.
- **sched_ext ports for other phones** (a OnePlus sched_ext scheduler for 6.6 kernels): written
  for other SoCs and kernel versions; the 6.12 GKI already includes the `SCHED_CLASS_EXT`
  core.
- **LZ4 1.9.4 and lz4kd:** the LZ4 update changes `LZ4_MEMORY_USAGE`, structure layouts and
  removes exported symbols (a KMI break) and does not apply to 6.12; lz4kd exists only up to
  6.6. zram itself is a GKI module loaded from `system_dlkm`, which an AnyKernel flash of the
  `Image` does not replace.
- **Feature branches** (fanotify, cgroup pids/ABI, USB gadget set, BBRv3): features, not power
  savings.

Newer GKI bases are already selectable in the build matrix (android16-6.12 2026-06 and
2026-09); 2026-03 stays the known-good base until a newer build has boot-tested on the phone.
