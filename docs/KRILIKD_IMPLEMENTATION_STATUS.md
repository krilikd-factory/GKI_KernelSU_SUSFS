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
