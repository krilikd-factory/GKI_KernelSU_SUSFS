# KRILIKD — ядро для OnePlus 15 (ColorOS 17, GKI 6.12.69)

## Один режим: TOP

Профилей safe / balanced / lab больше нет. Включена **Performance** — значит применяется всё, что подходит дереву:

- каждый патч `kernel_patches/common` сначала проверяется `--dry-run`; если не подходит — пропускается с предупреждением, сборка не падает;
- после сборки работают три защиты, и если хоть одна сработала — zip не собирается:
  1. **KMI CRC guard** — CRC символов ядра совпадают со стоком (иначе вендорные модули не загрузятся → висит лого);
  2. **Stock config guard** — ни одна опция стокового `gki_defconfig` (`=y`/`=m`) не пропала из итогового `.config`;
  3. **проверка VPN Hide** — `CONFIG_VPNHIDE=y` реально в ядре.

**Новые патчи апстрима подхватываются сами.** Любой новый `kernel_patches/common/*.patch`, который ещё не упомянут ни в одном шаге сборки, пробуется автоматически (точное совпадение контекста, `-F0`). Два фильтра:

- `.github/patch-denylist.txt` — патчи, которые брать нельзя, с причиной (убери строку, чтобы разрешить);
- правило заголовков: патч, который меняет `.h` не только макросами/комментариями, откладывается — он может сдвинуть CRC. После проверки его можно разрешить в `.github/patch-allowlist.txt`.

В режиме `known-good` коммит `kernel_patches` закреплён, поэтому набор патчей ровно тот, что загрузился.

**Настройки энергосбережения в ядре** (не трогают KMI):

- `CONFIG_WQ_POWER_EFFICIENT_DEFAULT=y` — «энергоэффективные» очереди работ выполняются на уже проснувшемся ядре CPU вместо того, чтобы будить то, которое поставило задачу;
- `CONFIG_KFENCE_SAMPLE_INTERVAL=0` — отладочный детектор ошибок памяти KFENCE остаётся в ядре, но выключен, пока не задан `kfence.sample_interval=N` в cmdline (нет таймера каждые 500 мс).

**LTO не используется намеренно.** На android16‑6.12 любой LTO (thin или full) при `DEBUG_INFO_BTF=y` выключает `CONFIG_RUST`, а с ним встроенный Rust‑ashmem и Rust‑binder — такое ядро соберётся, но Android на нём не стартует. Google собирает эту ветку тоже без LTO.

## Что в ядре по умолчанию

KernelSU‑Next + SUSFS 2.4.0, NoMount, Baseband‑guard, сеть (BBR/BBRv3, NAT, …), DroidSpaces, NTSync, Ptrace‑fix, Unicode‑fix, BTF/eBPF/FUSE‑BPF, Performance (TOP), **VPN Hide**, **USB Arsenal**.

### VPN Hide (встроенный)

Драйвер [okhsunrog/vpnhide](https://github.com/okhsunrog/vpnhide) вшит в ядро (`CONFIG_VPNHIDE=y`) его собственным интегратором с точными якорями. Без `.ko`, без kprobes, работает с первого запуска.

- Если якоря в `fs/` заняты SUSFS — VPN Hide встраивается **без** скрытия в файловой системе (`CONFIG_VPNHIDE_FS_HIDING=n`); сетевое скрытие (то, по чему приложения видят VPN) остаётся полным. В итоге шага видно: `full` или `net-only`.
- Ставить: **VPN Hide app** той же версии + модуль‑компаньон **`VPNHide-Builtin-Module`** из артефактов запуска (собирается из того же коммита vpnhide).
- **Не ставь** модуль `vpnhide-kmod` на это ядро — драйвер уже внутри.

### USB Arsenal

Для 6.12 нужен один патч — режим DVD/CD‑ROM у USB mass storage (монтировать ISO и грузить ПК с телефона). HID‑клавиатура/мышь и NCM‑раздача в стоковом GKI уже есть. Приложение — [ravindu644/USB-Arsenal](https://github.com/ravindu644/USB-Arsenal).

## Цикл обновлений

0. Каждый день **KRILIKD Update Check** сам запускает сборку `latest`, если что-то обновилось (один раз на каждый новый набор обновлений) — свежий кандидат всегда ждёт в Actions. Расписания в форке нужно один раз включить на вкладке Actions.
1. **KRILIKD Update Check** (Actions, ручной запуск + каждый день): таблица «known‑good ↔ сейчас» по всем компонентам с новыми коммитами, неиспользуемые патчи `kernel_patches`, появились ли исходники OnePlus, новости апстрима WildKernels. Галочка `build_latest` сразу запускает сборку с новыми версиями.
2. **Build Kernels** с `commit_mode=latest` → прошить → проверить загрузку.
3. Загрузилось → **Mark build as known-good** (ID запуска). Дальше обычный Run (`known-good`) собирает ровно эти коммиты.
4. Не загрузилось → прошить сохранённое рабочее ядро, прислать `/sys/fs/pstore/console-ramoops*`.

## Сборка из исходников OnePlus

`kernel_source = oneplus` берёт `OnePlusOSS/android_kernel_common_oneplus_sm8850`, самую новую ветку `oneplus/sm8850_*_oneplus_15` (или `oneplus_branch`). Инструменты сборки берутся из AOSP‑манифеста той GKI‑версии, на которой основан код OnePlus; подменяется только `common/`. Все фичи и защиты работают так же.

**Защита:** если исходник OnePlus старше стокового ядра телефона (`.github/device.json`: `stock_sublevel`, `stock_patch_level`) — сборка отказывается с объяснением. Сейчас у OnePlus опубликован только Android 16 (6.12.23), а на телефоне ColorOS 17 (6.12.69) — ядро 6.12.23 не загрузит вендорные модули ColorOS 17. Как только OnePlus выложит исходники ColorOS 17, Update Check напишет «Ready», и `kernel_source=oneplus` соберётся. После OTA, сменившего стоковое ядро, обнови `device.json`.

## Граница с ASB

KRILIKD даёт безопасные примитивы ядра, телеметрию только на чтение и ограниченные подсказки. ASB остаётся владельцем классификации нагрузки, теплового бюджета, политики по приложениям, LTPO, Smart‑экспериментов и отката. Будущий интерфейс «аренды политики» должен иметь владельца, TTL, ограниченные значения, обратное чтение и автоматический возврат к вендорному состоянию.

Ядро не должно само спорить с ASB за потолки частот CPU, шину памяти, тепловые лимиты и политику модема.

## Файлы

| Файл | Что |
|---|---|
| `.github/known-good.json` | проверенные наборы коммитов |
| `.github/device.json` | телефон и его стоковое ядро |
| `.github/workflows/update-check.yml` + `scripts/update_check.py` | проверка обновлений |
| `.github/actions/vpnhide` + `scripts/vpnhide_integrate.py` | VPN Hide |
| `.github/actions/usb-arsenal` | USB Arsenal |
| `scripts/kmi_crc_guard.py`, `scripts/stock_config_guard.py` | защиты загрузки |
| `.github/patch-denylist.txt`, `.github/patch-allowlist.txt` + `scripts/new_patches.py` | автоподхват новых патчей |
