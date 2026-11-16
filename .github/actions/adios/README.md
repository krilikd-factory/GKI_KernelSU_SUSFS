# ADIOS 3.3.0 integration

Source: `firelzrd/adios`, `main` snapshot commit `8117e935197fd45259ef77407b1c274d3386cd53`.
Upstream file: `patches/stable/0001-linux6.12.44-ADIOS-3.3.0.patch`.
Upstream SHA-256: `ecfa33c53e09ea9ef7cd4d2df3dc7f5a49e467f1b8da74e945367f01ed95c315`.

The vendored patch removes only the desktop-oriented `all:` / `clean:` host-module targets
from `block/Makefile`; those call `/lib/modules/$(uname -r)/build` and do not belong in an
Android GKI/Kleaf in-tree build. The ADIOS block source, Kconfig symbols and elevator hook
are otherwise retained. Vendored patch SHA-256:
`ebcd195e0715165f4cc27fcbf690f325150b47d75fcdf29719fc8a5158815af8`.

`CONFIG_MQ_IOSCHED_ADIOS=y` builds the scheduler in. `CONFIG_MQ_IOSCHED_DEFAULT_ADIOS=n`
keeps the ordinary kernel default unchanged. ASB may choose `adios` only when every targeted
non-rotational block queue exposes it, and verifies live readback. No arbitrary ADIOS sysfs
tunables are written.

Host patch application and CI compilation are not physical OnePlus 15 validation. Battery,
latency and throughput effects require same-device A/B captures against `mq-deadline`,
`kyber`, `bfq` and `none`; none are claimed here.
