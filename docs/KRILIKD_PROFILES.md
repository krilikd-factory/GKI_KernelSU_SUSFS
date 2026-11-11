# KRILIKD profiles

`KRILIKD` is the kernel identity. The upstream WildKernels code and third-party
licenses remain attributed in the repository notices.

## Profiles

- **safe** — small set of low-risk, KMI-aware performance patches.
- **balanced** — recommended daily profile; avoids the most invasive global
  writeback/wakelock/cache changes while retaining useful memory and CPU paths.
- **lab** — all compatible performance patches; only for controlled A/B tests.

A patch is applied only after a clean dry-run. A patch that does not fit the
selected Android Common Kernel tree is skipped and reported. The KMI CRC guard
remains mandatory. Do not use `allow_version_mismatch` for a release build.

## Recommended OnePlus 15 baseline

```text
Kernel Version: 6.12.x-android16
OS patch level: 2026-03
Root Flavor: KernelSU-Next
Commit mode: known-good
Brand: KRILIKD
Page size: 4k
LTO: thin
Performance profile: balanced
```

## ASB boundary

KRILIKD supplies safe kernel primitives, read-only telemetry and bounded hints.
ASB remains the owner of workload classification, thermal budget, per-app
policy, LTPO decisions, Smart experiments and rollback. A future policy lease
interface must have an owner, TTL, bounded values, read-back and automatic
return to vendor/default state.

The kernel must not independently fight ASB over CPU ceilings, memory bus,
thermal limits or modem policy.
