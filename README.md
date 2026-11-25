<div align="center">

# ⚡ KRILIKD Kernel

**Кастомное GKI-ядро для OnePlus и Ace**<br>
KernelSU · SUSFS · современные планировщики · интеграция с AutoSystemBoost

[![Android GKI](https://img.shields.io/badge/Android-GKI%202.0-3DDC84?logo=android&logoColor=white)](#-совместимость)
[![Builds](https://img.shields.io/badge/GitHub-Actions-2088FF?logo=githubactions&logoColor=white)](https://github.com/krilikd-factory/KRILIKD-kernel/actions/workflows/asb-kernel-topic.yml)
[![Telegram](https://img.shields.io/badge/Telegram-AutoSystemBoost-26A5E4?logo=telegram&logoColor=white)](https://t.me/AutoSystemBoost)
[![License](https://img.shields.io/badge/license-GPL--3.0-blue)](LICENSE)

[Совместимость](#-совместимость) · [Возможности](#-возможности) · [Скачать и установить](#-скачать-и-установить) · [Собрать своё](#-собрать-своё) · [Документация](#-документация)

</div>

> **KRILIKD** — сборочный проект на базе Android GKI и экосистемы Wild Kernels. Он объединяет root-интеграции, патчи совместимости и инструменты для настройки устройства. Состав зависит от семейства ядра и выбранных параметров сборки.

> [!CAUTION]
> Неподходящее ядро может привести к невозможности загрузки. До установки проверьте семейство **стокового ядра**, совместимость прошивки и размер страницы памяти; сохраните резервные копии затрагиваемых разделов и подготовьте способ восстановления именно для своей модели. Успешная сборка не означает проверенную загрузку или совместимость устройства.

## 📱 Совместимость

В групповом workflow сейчас настроены три целевые сборки **4 KB**, по умолчанию с KernelSU-Next:

| Устройство | Семейство и версия ядра | Уровень патча GKI |
|---|---|---|
| **OnePlus 12 / OnePlus Ace 5** | `android14-6.1.157` | `2025-12` |
| **OnePlus Ace 6** | `android15-6.6.118` | `2026-01` |
| **OnePlus 15 / 15R** | `android16-6.12.69` | `2026-03` |

Для **Ace 5** используется сборка OnePlus 12. Проверяйте её соответствие конкретной прошивке и аппаратной ревизии: одинаковое семейство GKI само по себе не гарантирует загрузку. Число после `android` в имени ядра обозначает ветку GKI и может отличаться от версии Android, установленной на телефоне.

Посмотреть текущее ядро можно в настройках устройства или командой:

```sh
adb shell uname -r
```

Семейство, patch level, размер страницы, root-версию и состав функций **проверяйте у выбранного файла и в BuildInfo конкретного запуска**. Проверки системы сборки не заменяют проверки на телефоне.

### Подтверждённая загрузка

Для OnePlus 15 в записи [known-good](.github/known-good.json) отмечена сборка `6.12.69-android16-6-KRILIKD`, AOSP GKI, KernelSU-Next, LTO выключен. [Запуск 45060773115](https://github.com/krilikd-factory/KRILIKD-kernel/actions/runs/45060773115) подтверждён **только фактом загрузки** 22 ноября 2026 года. Работа дисплея, сети, камеры, звука, нагрева и ASB отдельно не подтверждена. Другие ZIP, устройства и прошивки эта отметка не покрывает.

## ✨ Возможности

| Направление | Что может входить в сборку |
|---|---|
| 🛡️ **Root и приватность** | KernelSU-Next и другие варианты KernelSU, SUSFS, NoMount, Baseband Guard, VPN Hide |
| 🌐 **Сеть** | WireGuard, BBR/BBRv3, CAKE, IPSet, NAT и другие сетевые возможности — при наличии в конфигурации ядра |
| 🎮 **Совместимость** | NTSync, DroidSpaces, исправления Ptrace и Unicode, поддержка BPF и Perf |
| 🧠 **Планировщики и оптимизации** | BORE для CPU, ADIOS для I/O, а также отобранные оптимизации памяти, планировщика и файловых систем |
| 🔋 **Энергия и фон** | Re-Kernel, блокировщик источников пробуждения ASB (AWL) и SDP 900 mA — в поддерживаемых сборках |
| 🔌 **USB** | Возможности USB Arsenal и режимы USB gadget в зависимости от устройства и сборки |

**BORE, ADIOS, Re-Kernel, AWL и SDP 900 mA доступны для поддерживаемой ветки `android16-6.12` и не присутствуют автоматически во всех ядрах.** При выборе AWL его список правил изначально пуст. Наличие опции в конфигурации ещё не гарантирует, что соответствующий интерфейс доступен и работает на конкретной прошивке.

В составе определённой сборки ориентируйтесь на её **BuildInfo**, итоговый `.config` и результат проверки на устройстве. Возможности можно включать и отключать при сборке; подробности — в [справочнике функций](docs/features.md) и [интеграции ASB](docs/asb-integration.md).

### 🎛️ Вместе с AutoSystemBoost

[AutoSystemBoost](https://github.com/krilikd-factory/AutoSystemBoost) управляет доступными настройками из пользовательского пространства. Перед применением поддерживаемых настроек ASB проверяет интерфейс, сохраняет исходное значение и считывает результат; на неподдерживаемом ядре отдельные переключатели могут быть недоступны. Блокировка пробуждений по точному имени требует соответствующего интерфейса ядра и заданных пользователем правил.

## 📥 Скачать и установить

1. Выберите ZIP для своей модели и стоковой ветки GKI в [группе ASB](https://t.me/AutoSystemBoost) или в артефактах подходящего [запуска сборки](https://github.com/krilikd-factory/KRILIKD-kernel/actions/workflows/asb-kernel-topic.yml). Для скачивания артефактов GitHub может потребовать вход; артефакты имеют срок хранения.
2. Проверьте параметры сборки и подготовьте резервные копии и способ восстановления, подходящие вашей прошивке и схеме разделов. Способ отката зависит от устройства и способа установки — универсальный набор разделов не предполагается.
3. Установите **AnyKernel3 ZIP** подходящим для устройства способом. Если сборка требует отдельного метамодуля или менеджера, используйте соответствующие ей файлы и следуйте их инструкциям.
4. После перезагрузки проверьте версию ядра, root, связь, звук, камеру и остальные нужные функции. При проблеме сохраните журнал диагностики и, если доступен, `console-ramoops`.

[Инструкция по установке](docs/installation.md) · [Kernel Flasher](docs/kernelflasher.md) · [После установки](docs/post-install.md)

> VPN Hide, встроенный в ядро, использует совместимый companion-модуль. Не устанавливайте поверх него `vpnhide-kmod`. NoMount поставляется отдельным метамодулем, когда он выбран для сборки.

## 🏗️ Собрать своё

Откройте **Actions → [Build Kernels](https://github.com/krilikd-factory/KRILIKD-kernel/actions/workflows/main.yml) → Run workflow** и выберите конкретные семейство, patch level, root-вариант и функции. Для начала не выбирайте `All`: это запускает множество сборок. Workflow **[KRILIKD · ASB group build](https://github.com/krilikd-factory/KRILIKD-kernel/actions/workflows/asb-kernel-topic.yml)** готовит три указанных выше семейства, поддерживает выбор root и отключение функций; публикация в Telegram управляется отдельным параметром и требует настройки секретов.

Для анализа результата сохраняйте артефакты **AnyKernel3**, **BuildInfo** и **Pins**. В них находятся сведения об исходниках, выбранных компонентах, итоговой конфигурации и проверках сборки. Сборка кандидата не становится проверенной на устройстве автоматически. Подробный порядок — в [руководстве по сборке](docs/build-from-fork.md).

## 📚 Документация

- [Возможности и компоненты](docs/features.md) · [Интеграция с ASB](docs/asb-integration.md)
- [OnePlus 15 / TOP и история изменений](docs/KRILIKD.md) · [Статус реализации](docs/KRILIKD_IMPLEMENTATION_STATUS.md)
- [Сборка в своём форке](docs/build-from-fork.md) · [Установка](docs/installation.md) · [Проверка после установки](docs/post-install.md)
- [Лицензия](LICENSE) · [Уведомления и лицензии сторонних проектов](THIRD_PARTY_NOTICES.md)

## 🙏 Благодарности

Проект основан на работе [Android Common Kernel / GKI](https://android.googlesource.com/kernel/common/), [Wild Kernels](https://github.com/WildKernels/GKI_KernelSU_SUSFS) и сообщества разработчиков. Отдельная благодарность авторам [KernelSU](https://github.com/tiann/KernelSU), [KernelSU-Next](https://github.com/KernelSU-Next/KernelSU-Next), [SUSFS](https://gitlab.com/simonpunk/susfs4ksu), [NoMount](https://github.com/maxsteeel/nomount), [Baseband Guard](https://github.com/vc-teahouse/Baseband-guard), [DroidSpaces](https://github.com/ravindu644/Droidspaces-OSS), [Re-Kernel](https://github.com/Sakion-Team/Re-Kernel), [VPN Hide](https://github.com/okhsunrog/vpnhide), [USB Arsenal](https://github.com/ravindu644/USB-Arsenal), [BORE](https://github.com/firelzrd/bore-scheduler), [ADIOS](https://github.com/firelzrd/adios) и [AnyKernel3](https://github.com/osm0sis/AnyKernel3). Полные сведения об авторах и лицензиях — в [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
