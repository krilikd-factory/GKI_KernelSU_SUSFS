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
