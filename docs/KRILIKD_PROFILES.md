# KRILIKD mode

`KRILIKD` is the kernel identity. The upstream WildKernels code and third-party
licenses remain attributed in the repository notices.

## One mode: TOP

The earlier `safe` / `balanced` / `lab` profiles are gone. With Performance on, every
reviewed patch that fits the tree is applied after a clean dry-run; a patch that does not
fit is skipped and reported. New upstream patches are only observed until a reviewed
`patch-policy.json` entry enables them. The KMI CRC guard, the stock config guard and the
OnePlus 15 config guard remain mandatory. Do not use `allow_version_mismatch` for a
release build.

## OnePlus 15 baseline

```text
Kernel Version: 6.12.x-android16
OS patch level: 2026-03            (stock 6.12.69)
Root Flavor: KernelSU-Next
Commit mode: latest (daily Update Check) or known-good (reproduce a booted build)
Brand: KRILIKD                     (uname -r: 6.12.69-android16-<KMI>-KRILIKD)
Page size: 4k
LTO: none                          (any LTO drops CONFIG_RUST on 6.12 with BTF; Android does not boot)
Performance: bore_adios            (Perf + BORE + ADIOS, as Update Check sends it)
```

## ASB boundary

KRILIKD supplies safe kernel primitives, read-only telemetry and bounded hints.
ASB remains the owner of workload classification, thermal budget, per-app
policy, LTPO decisions, Smart experiments, I/O scheduler choice and rollback. ASB
discovers every kernel feature at runtime and works the same on the stock kernel,
where these features are simply reported as not exposed.

The kernel must not independently fight ASB over CPU ceilings, memory bus,
thermal limits or modem policy.
