# KRILIKD — ядро для OnePlus 15 (ColorOS 17, GKI 6.12.69)

## Один режим: TOP

Профилей safe / balanced / lab больше нет. Включена **Performance** — значит применяется всё, что подходит дереву:

- каждый патч `kernel_patches/common` сначала проверяется `--dry-run`; если не подходит — пропускается с предупреждением, сборка не падает;
- после сборки работают три защиты, и если хоть одна сработала — zip не собирается:
  1. **KMI CRC guard** — CRC символов ядра совпадают со стоком (иначе вендорные модули не загрузятся → висит лого);
  2. **Stock config guard** — ни одна опция стокового `gki_defconfig` (`=y`/`=m`) не пропала из итогового `.config`;
  3. **проверка VPN Hide** — `CONFIG_VPNHIDE=y` реально в ядре.

**Новые патчи апстрима обнаруживаются автоматически, но не применяются без review.** Каждый новый `kernel_patches/common/*.patch` получает digest и состояние `observed`; `candidate` и `enabled` задаются только через `.github/patch-policy.json`. Точное совпадение контекста и `-F0` остаются обязательными для разрешённых патчей.

- `.github/patch-denylist.txt` — безусловный запрет с причиной;
- правило заголовков: патч, который меняет `.h` не только макросами/комментариями, требует отдельного `.github/patch-allowlist.txt`;
- target/provenance policy отсекает неподходящие семейства, устройства и SoC.

В режиме `known-good` коммит `kernel_patches` закреплён; для новых записей также сверяется digest проверенной patch policy.

**Настройки энергосбережения в ядре** (не трогают KMI):

- `CONFIG_WQ_POWER_EFFICIENT_DEFAULT=y` — «энергоэффективные» очереди работ выполняются на уже проснувшемся ядре CPU вместо того, чтобы будить то, которое поставило задачу;
- `CONFIG_KFENCE_SAMPLE_INTERVAL=0` — отладочный детектор ошибок памяти KFENCE остаётся в ядре, но выключен, пока не задан `kfence.sample_interval=N` в cmdline (нет таймера каждые 500 мс).

**LTO не используется намеренно.** На android16‑6.12 любой LTO (thin или full) при `DEBUG_INFO_BTF=y` выключает `CONFIG_RUST`, а с ним встроенный Rust‑ashmem и Rust‑binder — такое ядро соберётся, но Android на нём не стартует. Google собирает эту ветку тоже без LTO.

## Что в ядре по умолчанию

KernelSU‑Next + SUSFS 2.4.0, NoMount, Baseband‑guard, сеть (BBR/BBRv3, CAKE, IP sets, NAT, …), DroidSpaces, NTSync, Ptrace‑fix, Unicode‑fix, BTF/eBPF/FUSE‑BPF, Performance (TOP), **VPN Hide**, **USB Arsenal**. Для `android16-6.12` также доступны отключаемые **Re-Kernel (`RK`)** и **ASB wakeup blocker (`AWL`)**; в `All` они включены, а соответствующие inputs основного workflow по умолчанию включены.

### VPN Hide (встроенный)

Драйвер [okhsunrog/vpnhide](https://github.com/okhsunrog/vpnhide) вшит в ядро (`CONFIG_VPNHIDE=y`) его собственным интегратором с точными якорями. Без `.ko`, без kprobes, работает с первого запуска.

- Если якоря в `fs/` заняты SUSFS — VPN Hide встраивается **без** скрытия в файловой системе (`CONFIG_VPNHIDE_FS_HIDING=n`); сетевое скрытие (то, по чему приложения видят VPN) остаётся полным. В итоге шага видно: `full` или `net-only`.
- Ставить: **VPN Hide app** той же версии + модуль‑компаньон **`VPNHide-Builtin-Module`** из артефактов запуска (собирается из того же коммита vpnhide).
- **Не ставь** модуль `vpnhide-kmod` на это ядро — драйвер уже внутри.

### USB Arsenal

Для 6.12 нужен один патч — режим DVD/CD‑ROM у USB mass storage (монтировать ISO и грузить ПК с телефона). HID‑клавиатура/мышь и NCM‑раздача в стоковом GKI уже есть. Приложение — [ravindu644/USB-Arsenal](https://github.com/ravindu644/USB-Arsenal).

### Re-Kernel и wakeup blocker

`RK` скачивает только точный полный commit официального [Sakion-Team/Re-Kernel](https://github.com/Sakion-Team/Re-Kernel), встраивает `LKM-Source` как `drivers/rekernel` и включает Generic Netlink/network events. Commit и digest исходников попадают в Pins/BuildInfo; known-good сохраняет Re-Kernel pin. Это канал binder/signal/network events для совместимых сторонних «заморозчиков», а не собственный freezer ASB и не доказательство, что конкретное приложение уже умеет с ним работать.

`AWL` применяет отдельный reviewed patch только к `android16-6.12`. Он добавляет root-only `/sys/kernel/asb_wakeup/blocklist` (`add NAME`, `del NAME`, `clear`) и read-only `/sys/kernel/asb_wakeup/blocked_events`. Разрешены максимум 16 точных printable-ASCII имён длиной 1–63 байта, без пробелов, путей, glob/regex и правил по умолчанию. Пустой список не добавляет работы в hot path благодаря static key; уже удерживаемые блокировки принудительно не снимаются. ASB применяет только явный список пользователя, сверяет readback и очищает частично применённый список при ошибке, Safe mode и удалении.

Выбранные `NET`, `RK` и `AWL` проходят fail-closed проверку **финального resolved `.config`**: если CAKE, любой обязательный IP set/`NETFILTER_XT_SET`, Re-Kernel или blocker исчезли после Kconfig resolution, сборка останавливается. Requested config lines сами по себе доказательством не считаются.

## Цикл обновлений и каналы патчей

0. Каждый день **KRILIKD Update Check** запускает `latest`-сборку при обновлении компонентов (один раз на новый набор). Свежие неизвестные патчи при этом только попадают в отчёт: **apply-by-default удалён**.
1. У каждого ранее не обработанного `kernel_patches/common/*.patch` есть состояние:
   - `observed` — обнаружен, посчитан SHA-256, но не применяется;
   - `candidate` — человек указал provenance, лицензию и target в `.github/patch-policy.json`; применяется только при явном `patch_channel=candidate`;
   - `enabled` — проверенный кандидат разрешён для стабильных сборок.
2. Denylist и проверка рискованных header-изменений остаются дополнительными барьерами. Allowlist сам по себе не включает патч. Для candidate/enabled обязательны `families`, `devices`, `soc`, `provenance.source` и `provenance.license`; несовпадение с OnePlus 15/SM8850/android16-6.12 блокирует применение.
3. **Build Kernels**: `stable` применяет только `enabled`; `candidate` дополнительно проверяет `candidate`. Оба канала используют dry-run и `-F0`, затем действуют прежние stock-config и KMI guards.
4. `patch-inventory.json` в BuildInfo содержит digest, размер, состояние, target, решение и причину по каждому новому патчу. Так свежие патчи появляются регулярно, но не становятся незаметной частью production.
5. Загрузилось стабильное ядро → открой **Mark build as known-good** и вставь только ссылку на его successful runner. Пустой checklist означает `boot_attested_only` — твоё ручное подтверждение именно boot. Для более широкой аттестации можно опционально вставить заполненный `device-validation-template.json` из Pins. Candidate-сборки по-прежнему отклоняются: сначала переведи проверенные патчи в `enabled` и пересобери `stable`.
   Сборку канала `candidate` тоже можно отметить, если в ней не применился ни один candidate-патч (поле `candidate_patches_applied: 0` в Pins): её исходники те же, что у `stable`. Сборка, где candidate-патчи применились, known-good стать не может.
6. Не загрузилось → прошить сохранённое рабочее ядро, прислать `/sys/fs/pstore/console-ramoops*`. Для known-good с новой схемой evidence также фиксируется SHA-256 patch policy: её последующее изменение требует новой валидации, а не молчаливого reuse старого verdict.

## Сборка из исходников OnePlus

`kernel_source = oneplus` берёт `OnePlusOSS/android_kernel_common_oneplus_sm8850`, самую новую ветку `oneplus/sm8850_*_oneplus_15` (или `oneplus_branch`). Инструменты сборки берутся из AOSP‑манифеста той GKI‑версии, на которой основан код OnePlus; подменяется только `common/`. Все фичи и защиты работают так же.

**Двойная защита:** resolver по-прежнему отклоняет source старше стокового ядра. Новый `source_provenance.py` затем проверяет уже фактически скачанное `kernel/common`: полный Git commit, чистое дерево до feature-патчей, версию/sublevel, семейство, точный OnePlus repository и branch pattern, patch level относительно `.github/device.json`. В `source-provenance.json` сохраняются SHA-256 Makefile, defconfig, BUILD.bazel и build.config.*. Это preflight source identity, а не доказательство загрузки.

Сейчас при несовпадении опубликованного source и стокового 6.12.69/2026-03 сборка обязана отказаться. После OTA, сменившего стоковое ядро, обнови `device.json`.

## Граница с ASB

KRILIKD даёт безопасные примитивы ядра, телеметрию только на чтение и ограниченные подсказки. ASB остаётся владельцем классификации нагрузки, теплового бюджета, политики по приложениям, LTPO, Smart‑экспериментов и отката.

После сборки `kernel_capability_report.py` читает **финальный resolved `.config`** и создаёт `kernel-capabilities.json`: PSI, MGLRU, zram/zsmalloc, BBR, CAKE, IP sets, Re-Kernel, wakeup blocker, BPF/BTF, UFS и power-efficient workqueues. Для каждой группы отдельно записаны `build_state` и `runtime_state=unknown`, пути для discovery и назначение для ASB. Наличие CONFIG не выдаётся за работающий runtime interface; BBRv3 не выводится из одного `CONFIG_TCP_CONG_BBR`, а реальные `tc`/`ipset` операции требуют отдельной проверки.

Схема 2 дополнительно публикует **control candidates**, а не доказательство доступности: `mglru_mode`, `kernel_timer_migration` и `kernel_sched_nr_migrate`. Это только закрытый список штатных Linux proc/sysfs-интерфейсов и разрешённых значений. Для каждого зафиксированы `runtime_state=unknown`, `writable=unknown`, обязательные baseline/readback и exact restore. ASB обязан повторно проверить путь, права и значение на устройстве; отчёт из BuildInfo не является разрешением писать и не заменяет собственный allowlist ASB. Smart MGLRU и ручной режим используют одного владельца, а profile/reinforce не обходят настройку прямыми записями.

Единственный новый custom ABI в этой ревизии — узкий `/sys/kernel/asb_wakeup/*` при выбранном `AWL`, описанный выше. Кастомный `/sys/kernel/krilikd/*` **не создан**. Произвольный path/value channel, другие kernel writers, lease/TTL и vendor QoS не добавляются. VPN Hide управляется только собственными app/companion: ASB не создаёт второй канал и не подменяет их состояние. Существующий network runtime ASB также остаётся единственным владельцем BBR/qdisc/route policy. Camera/audio/modem/thermal остаются у vendor stack; checklist проверяет отсутствие регрессий, а не обещает улучшение. Подтверждённого OPlus SDP-интерфейса для 900 мА с readback не найдено, поэтому writer зарядного тока не добавлен.

## Device evidence

Простой режим требует только URL successful stable runner после личной проверки загрузки. Workflow сам скачивает Pins, проверяет evidence hashes и commits, а в known-good записывает `boot_attested_only`: это подтверждение boot, не утверждение о проверке остальных функций. При повторном использовании таких pins resolver показывает предупреждение об этом ограниченном scope.

Для полной аттестации Pins artifact по-прежнему содержит `device-validation-template.json`. Его необязательный checklist охватывает boot, vendor modules, display/touch, Wi-Fi, cellular, camera, audio, storage, thermal и ASB; при заполнении все пункты должны быть `passed`. В known-good сохраняются digest, время, run/build binding и статус ручной аттестации; `uname -r` сохраняется для полного checklist. Serial, IMEI, account и network identifiers в отчёт не входят и поля с ними отклоняются. Оба режима — явная ручная аттестация, а не независимое автоматическое доказательство.

## Файлы

| Файл | Что |
|---|---|
| `.github/known-good.json` | наборы коммитов и evidence с явным scope: boot-only или полный checklist |
| `.github/device.json` | телефон и его стоковое ядро |
| `.github/patch-policy.json` | observed/candidate/enabled, target и provenance |
| `.github/patch-denylist.txt`, `.github/patch-allowlist.txt` | дополнительные запреты и header review |
| `.github/scripts/new_patches.py` | lifecycle classifier и patch inventory |
| `.github/scripts/source_provenance.py` | preflight фактического AOSP/OnePlus source |
| `.github/scripts/kernel_capability_report.py` | build-time capability report и fail-closed `--require` для выбранных final-config features |
| `.github/actions/rekernel/action.yml` | exact-commit интеграция Re-Kernel и provenance |
| `.github/actions/asb-wakeup-blocker/action.yml` + `.github/patches/android16-6.12/asb-wakeup-blocker.patch` | reviewed blocker integration и пустой по умолчанию sysfs policy |
| `.github/scripts/device_evidence.py` | URL-only boot attestation, template и проверка полного device checklist |
| `.github/workflows/update-check.yml` + `scripts/update_check.py` | проверка обновлений без auto-apply |
| `.github/workflows/mark-known-good.yml` | ручное promotion только stable + явный evidence scope |
| `scripts/kmi_crc_guard.py`, `scripts/stock_config_guard.py` | прежние защиты загрузки |
| `tests/test_build_evidence.py` | regression новых contracts |

## Что сознательно не реализовано / не утверждается

- Никакие значения частот, voltage, thermal limits, IRQ affinity, camera/ISP, audio, modem или UFS QoS не hardcode'ятся.
- Не заявляются проценты прироста FPS, снижения нагрева или расхода: для этого нужны A/B-замеры на OnePlus 15.
- Не добавлены непроверенные SoC-specific writers, произвольный sysfs path/value ABI, persistent polling или app-specific политики; единственный новый ABI ограничен exact-name wakeup rules.
- Не реализован `oplus_sdp_900ma`: нет подтверждённого OPlus control/readback ABI, поэтому отсутствие writer не маскируется под успех.
- Не заданы wakeup-имена по умолчанию и не доказана безопасность блокировки любого конкретного источника. Suspend, modem, Wi‑Fi, alarms, calls и charging требуют полевой проверки.
- Не утверждается совместимость конкретного freezer с Re-Kernel или фактическое unfreeze-by-event до on-device теста.
- KMI guard, stock-config guard, `allow_version_mismatch` и вендорный thermal ownership не ослаблены.
- Host tests, source-anchor apply check и успешная CI-сборка не равны boot/runtime validation на физическом устройстве.
