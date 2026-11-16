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

## Update Check composition and physical boot evidence (kernel dev66)

- **Known-good features survive automatic builds:** Update Check validates the selected
  non-`@previous` entry's complete `feature_set` and forwards the corresponding `main.yml` inputs.
  `Perf+BORE` becomes `use_perf=bore`; a non-BORE entry remains non-BORE. Missing, unknown,
  duplicated or internally inconsistent features stop the check with an actionable error. Multiple
  primary entries must agree on one composition; otherwise an explicit key is required.
- **Candidate cache identity includes features:** the fingerprint now covers upstream tips and the
  canonical feature set. The same updates with and without BORE therefore cannot reuse one cache
  entry. Component pins still come from `commit_mode=latest`; stable/candidate patch gates and the
  physical-attestation requirement in Mark build as known-good are unchanged.
- **6.12.92 field result:** `device-kmi-policy.json` records the owner's physical
  `boot_logo_stuck`. This result is not known-good and does not attest vendor modules or runtime
  functions. Compilation and same-KMI membership do not override it. A firmware update is not
  claimed to be a guaranteed fix, and no BORE-on follow-up is prescribed after this boot failure.

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
- **BORE scheduler:** added as an opt-in experiment (see the next section), off by default.
  The upstream branch's own notes report boot loops on 6.12 when its per-task state is left
  inside `task_struct`; the port here keeps that structure's layout. BORE targets
  responsiveness, not energy, and the phone's vendor scheduler (WALT) still makes most
  placement decisions, so no power gain is claimed.
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

## BORE scheduler, opt-in (kernel dev62)

Selected with the dispatch input `use_perf = bore` (Performance plus BORE); `true` and `false`
behave as before and the nightly build never carries it. It is wired for android16-6.12 only,
and a dispatch that names another Kernel Version, or All, is refused. The workflow dispatch
form is at GitHub's limit of 25 inputs, so this is a third value of `use_perf` instead of a new
switch.

- **Patch:** BORE 7.0.0 for 6.12 from the upstream author, vendored with its SHA-256 pinned in
  `tests/test_bore.py`, applied after the other performance patches.
- **Fit on 6.12.69:** the patch was cut against 6.12.37. Dry-run on the 2026-03 tree (6.12.69):
  two hunks of `fair.c` are rejected and one lands inside a comment. `bore_gki612_fixups.py`
  ports the base-slice sysctl block (the export of `sysctl_sched_base_slice` stays outside the
  `#ifdef`), moves the `update_curr_bore()` call to its real place and applies BORE's condition
  to the slice protection in `__pick_eevdf`. Every anchor is checked; a tree that drifted stops
  the build. Another sublevel can need new ports.
- **KMI:** BORE's per-task state is not left in `task_struct`. It is a `struct bore_ctx *` in the
  last reserved slot (`ANDROID_KABI_USE(8, ...)`, which `__GENKSYMS__` still sees as the stock
  reserved u64), allocated in `copy_process`, freed in `free_task`. The KMI CRC guard still runs
  against `abi.stg`.
- **Verified here:** the patch plus the scripts apply to a 6.12.69 tree without rejects, the
  inline `bore_ctx` is gone, the accessors are rewritten and a second run refuses the patched
  tree. **Not verified:** a compile, the CRC guard and a boot; the host has no kernel build.
  The first CI run is therefore the real test, and the result needs a boot on the phone before
  it is marked known-good. Keep the previous image to flash back.
- **Switching it off at runtime:** BORE exposes `/proc/sys/kernel/sched_bore` (0 = plain EEVDF),
  so a boot that works can be compared against stock behaviour without reflashing.
