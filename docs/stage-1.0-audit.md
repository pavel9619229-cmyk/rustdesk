# UDU stage 1.0 audit — ревизия и фиксация рабочей базы

Дата начала: 2026-08-27
Устройство: `local-server` (project device `server`)
Основная папка: `C:\Users\Server\Documents\UDU`
Актуальный план: `masha-server-access-plan-current.pdf`

## 1. Первоначальное состояние

На момент начала ревизии:
- ветка: `masha/server-control-01`;
- HEAD: `63a396424165062b7b3c20678a5f85d90bd606fb`;
- последний commit: `63a396424 docs: update Masha server access plan v10`;
- staged-файлов нет;
- modified: только существующий пользовательский `AGENTS.md`;
- untracked: существующие design/PDF/`ops\spaceweb` файлы, включая секретные материалы;
- эти файлы не изменялись и не будут включаться в audit-коммиты.

Worktree на старте был один: основная папка UDU.
Готового `docs\stage-1.0-audit.md` до ревизии не существовало.

## 2. История от базовой точки

Линейный отрезок подтверждён Git:
`922372ba7` → `07f4308b9` → `08811109f` → `1b98946dc` → `c80009ca1` → `5907b530a` → `b0f12214c`.

## 3. Проверенные коммиты — первый результат

### `922372ba7`
- Меняет только `src/common.rs`: исправляет встроенный публичный ключ Masha rendezvous server.
- Это рабочая конфигурационная логика, не UI и не preview.
- Для exact SHA найден GitHub Actions run `31682259399`, `Full Flutter CI`, conclusion `success`.
- Windows x64 job `94391909680` (`run-ci / x86_64-pc-windows-msvc`) завершён `success`.
- Артефакт: `masha-remote-operator-windows-x86_64`, artifact id `9175273199`.
- Workflow на этом SHA фиксирует Flutter x64 `3.24.5`.

### `07f4308b9`
- Добавляет только `docs/session-handoff-2026-08-13.md`.
- Код не меняет; для рабочей базы не обязателен.

### `08811109f`
- 40 файлов: два HTML-макета в `docs/design` и массовая Dart/Flutter API-миграция.
- Миграция включает `withOpacity→withValues`, `Color.value→toARGB32`, `MaterialState*→WidgetState*`, `colorScheme.background→surface` и мелкие lint/syntax-правки.
- Самих экранов Connect/Allow в приложение этот commit ещё не добавляет.
- Часть миграции несовместима с закреплённым Flutter 3.24.5; переносить commit целиком нельзя.

### `1b98946dc`
- Добавляет временные `masha_connect_page.dart`, `masha_allow_page.dart` и preview-кнопку в `desktop_home_page.dart`.
- В коде прямо указано `TEMP` / `visual review`.
- Это preview-интерфейс, не серверный контроль; из рабочей базы исключается.

### `c80009ca1`
- Меняет только `desktop_home_page.dart`.
- Исправляет конфликт имени `Dialog` для временной preview-кнопки через `material.Dialog`.
- Это исправление только preview-кода; в рабочую базу не требуется.

### `5907b530a`
- Пустой commit: tree SHA полностью совпадает с родителем.
- Назначение — только повторный запуск CI после сбоя cache service.
- Кода и конфигурации не меняет.

### `b0f12214c`
- Частично откатывает несовместимую миграцию `withValues` обратно на `withOpacity` и добавляет импорт `ServerModel` в preview-файл.
- `flutter/lib/models/ab_model.dart` этим commit не исправлен: вызовы `toARGB32()` из `08811109f` остаются.
- Preview-файлы также остаются и продолжают содержать ошибочные обращения к `bind`.

## 4. Точная причина failure `b0f12214c`

GitHub run: `32077325498`, `Full Flutter CI`, head SHA точно `b0f12214c64952dc1d5d9e4dd01ee4d63bb37fa5`.
Windows x64 job: `95533747998`, failed step: `Build rustdesk`.
Фактические Dart compile errors из job log:
- `flutter/lib/models/ab_model.dart`: строки 1246, 1286, 1314, 1355, 1787, 1842 — `The method 'toARGB32' isn't defined for the class 'Color'`;
- `flutter/lib/desktop/pages/masha_connect_page.dart:29` — getter `bind` isn't defined;
- `flutter/lib/desktop/pages/masha_allow_page.dart:68` — getter `bind` isn't defined.

Первопричина: кодовая несовместимость с Flutter 3.24.5 плюс ошибки временных preview-экранов. Это не первичная ошибка cache/environment.

## 5. Локальная контрольная копия базы

Создан безопасный detached worktree:
`C:\Users\Server\Documents\UDU-worktrees\stage-1.0-audit`

Его HEAD: `922372ba7885fbab7bf4234f89101a58bcd00729`.
Рабочая директория worktree после создания чистая.
`libs/hbb_common` пока не инициализирован (`git submodule status` показывает `-`), поэтому локальная проверка сборочной структуры ещё продолжается.

Локальный toolchain на `server` на момент проверки:
- Git доступен;
- Flutter/Dart доступны из `C:\dev\flutter`;
- `rustc`, `cargo`, `cmake`, `ninja`, отдельный vcpkg в PATH не найдены.

Полный локальный Windows binary build без установки существенного toolchain сейчас не запускается. Новая длительная CI также не запускается.

Следующий шаг: проверить submodule/локальную структуру exact `922372ba7`, затем отдельно ревизовать уже существующие commits серверной авторизации после `b0f12214c` и определить, что из них допустимо перенести в этап 1.1.

Блокер: нет.
