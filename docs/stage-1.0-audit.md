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

## 6. Checkpoint после первого блока

Первый audit-checkpoint:
`c7c255457471aa285eee15899c4ae64a9c1ea5b8` — `docs: start UDU stage 1.0 audit`.
В commit вошёл только `docs/stage-1.0-audit.md`.
Существующие modified/untracked файлы пользователя не добавлялись.

## 7. Ревизия существующей серверной авторизации

После `b0f12214c` на текущей ветке имеются:
- `c73d16819` — staged Masha session authorization gate; Rust-файлы `src/client.rs`, `src/lib.rs`, новый `src/masha_session_auth.rs`, `src/server/connection.rs`;
- `aa72f86d5` — Direct IP binding через target nonce/hash challenge;
- `380205304` — исправление типа `ConnType` на публичный `hbb_common::rendezvous_proto::ConnType`;
- `29ab87c7d`, `14b2d3013`, `5101b35d8` — только экспериментальный fast-CI workflow.

`ENFORCE_SERVER_AUTH` в существующей заготовке установлен в `false`; fail-closed не активирован.
`SwitchSidesResponse` обходит Masha ticket gate: сервер вызывает `handle_login_request_without_validation()` напрямую.
Следовательно, существующая auth-заготовка не является завершённым серверным контролем.

### Состояние сборки server-auth заготовки

Existing Full CI run `32864026887`, head SHA `380205304`, Windows x64 job `97858029648`, failed.
Точная Rust-ошибка: `future cannot be sent between threads safely`.
Причина: `std::sync::RwLockReadGuard<LoginConfigHandler>` из `lc.read().unwrap()` удерживается через `peer.send(...).await` в `src/client.rs` (`send_login`).
Компилятор фиксирует это как две ошибки через async callers в `src/ui_session_interface.rs`.

Последний fast-check run `32881618826`, head SHA `5101b35d8`, также failed, но до проверки auth-кода не дошёл:
`libs/scrap/build.rs` не нашёл `libyuv.pc` через pkg-config.
Это ошибка окружения самого экспериментального fast-CI, поэтому он не является доказательством компилируемости auth-кода.

## 8. Выбранная чистая база

Выбрана база: commit `922372ba7885fbab7bf4234f89101a58bcd00729` на линии `udu/1.4.9`.
Tree SHA: `6171906e6ab09073d4ff9aeda574f4f08c3ca32e`.
Submodule `libs/hbb_common`: `7e1c392c62d39c364127307cd408421dd5f8cfb0`.

Обоснование:
- это последний commit до смешанной Flutter/UI работы;
- он содержит исправленный Masha server public key;
- все изменения `922→b0f` в `src/` отсутствуют: Rust-код не менялся;
- exact SHA имеет успешный Full Flutter CI run `31682259399`;
- Windows x64 job `94391909680` завершён `success`;
- Windows x64 artifact id `9175273199` существует и не expired.

## 9. Локальная проверка выбранной базы

На `server` создан detached worktree:
`C:\Users\Server\Documents\UDU-worktrees\stage-1.0-audit`.

Проверено локально:
- HEAD точно `922372ba7885fbab7bf4234f89101a58bcd00729`;
- worktree чистый;
- submodule инициализирован на точном SHA `7e1c392c...`;
- `git diff --check` проходит;
- объединённый Rust-патч `b0f12214c→380205304` проходит `git apply --check` на базе `922372ba7` с exit code 0;
- после проверки worktree остаётся чистым.

Локальный `server` сейчас имеет Flutter `3.44.2`, но exact Windows x64 workflow базы использует Flutter `3.24.5`.
Локально не найдены `rustc`, `cargo`, `cmake`, `ninja` и vcpkg.
Поэтому полный локальный Windows binary build exact toolchain без установки существенного окружения не выполнен.

## 10. Итоговое разделение изменений

Подтверждённый рабочий код: `922372ba7` (`src/common.rs`, исправленный server public key).
Документация без влияния на код: `07f4308b9`.
Flutter API migration: Dart-часть `08811109f`; целиком не переносить на Flutter 3.24.5.
HTML frontend mockups: `docs/design/masha-frontend-APP.html`, `masha-frontend-STARTPAGE.html` из `08811109f`; только reference, не рабочая логика.
Temporary preview: `1b98946dc` и `c80009ca1`; исключить.
CI retrigger: `5907b530a`; пустой commit, исключить.
Неполный compatibility fix: `b0f12214c`; на чистой базе `922` не нужен.
Server-auth draft: `c73d16819` + `aa72f86d5` + `380205304`; использовать только как источник отдельных решений после исправления известных дефектов.
Experimental fast-CI: `29ab87c7d` + `14b2d3013` + `5101b35d8`; не переносить как доказанную проверку.

## 11. Точный перечень для этапа 1.1

Работу 1.1 начинать от `922372ba7`, не от `b0f12214c` и не от текущего HEAD.

Допустимо перенести/переиспользовать из server-auth draft:
1. `src/lib.rs`: подключение отдельного `masha_session_auth` модуля.
2. `src/masha_session_auth.rs`: структуру HTTP authorize, проверку Ed25519-подписи и проверки времени как заготовку; поля ticket привести к актуальному v10 плану.
3. `src/client.rs`: сам принцип «получить ticket до LoginRequest и передать его получателю», но переписать так, чтобы lock guard не жил через `.await`.
4. `src/server/connection.rs`: проверку ticket до авторизации, но сделать единый gate для всех login-путей, включая `SwitchSidesResponse`.
5. Исправление `380205304`: использовать публичный `hbb_common::rendezvous_proto::ConnType`, если соответствующий helper сохраняется.

Не переносить в 1.1:
- Flutter/UI/preview изменения `08811109f`, `1b98946dc`, `c80009ca1`, `b0f12214c`;
- пустой `5907b530a`;
- experimental fast-CI commits `29ab87c7d`, `14b2d3013`, `5101b35d8`;
- Direct IP nonce-binding из `aa72f86d5` как обязательную часть 1.1: это материал этапа 1.3, хотя код может быть использован позже.

До перехода к 1.1 существующий server-auth draft не считать рабочим: fail-closed выключен, switch-sides обходит gate, Windows x64 Rust compile падает.

## 12. Текущий блокер критерия 1.0

Все результаты ревизии, кроме полного локального binary build exact toolchain, получены.
Для полного локального Windows build `922372ba7` требуется установить на `server` существенный build toolchain, которого сейчас нет (Rust/Cargo/CMake/vcpkg и exact Flutter 3.24.5 либо эквивалентное изолированное окружение).
Новая длительная GitHub CI не запускалась.

Блокер требует решения пользователя: разрешить установку/подготовку exact локального build toolchain либо признать успешный exact-SHA Full CI `31682259399` + чистую локальную проверку source/submodule достаточным доказательством воспроизводимости базы для закрытия этапа 1.0.

## 13. Подготовка локального exact build toolchain

Пользователь разрешил установку/подготовку локального build toolchain для проверки `922372ba7`.

Все тяжёлые инструменты размещены на том же устройстве `local-server` в `G:\UDU-stage-1.0-tools`; это не копия проекта. Основной repo и audit-worktree остаются на `C:`.

Подтверждено:
- Rust standalone `1.75.0` (`rustc 1.75.0`, `cargo 1.75.0`);
- Flutter `3.24.5`, framework revision `dec2ee5c1f98f8e84a7d5380c05eb8a3d0a81668`, Dart `3.5.4`;
- Flutter `3.22.3`, framework revision `b0850beeb25f6d5b10426284f506557f66181b36`, Dart `3.4.4`, только для bridge generation;
- libclang package `15.0.6.1`, DLL `G:\UDU-stage-1.0-tools\python-libclang-15\clang\native\libclang.dll`;
- NASM `2.16.03`;
- vcpkg repository exact commit `120deac3062162151622ca4860575a33844ba10b`;
- vcpkg tool release `2025-07-21-d4b65a2b83ae6c3526acd1c6f3b51aff2a884533`;
- Visual Studio Build Tools уже были установлены на server: `C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools`, MSVC x64 tools и Windows SDK доступны; встроенные CMake/Ninja обнаружены.

Следующий шаг: установить manifest-зависимости `vcpkg.json` для triplet `x64-windows-static` в каталог на `G:`, затем генерировать exact bridge и выполнять локальную Windows x64 сборку audit-worktree.
