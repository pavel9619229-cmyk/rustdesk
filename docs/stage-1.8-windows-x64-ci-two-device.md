# Этап 1.8 — Windows x64 CI и испытание на двух устройствах

Дата выполнения: 28.08.2026.
Ветка: `masha/server-control-01` (новая ветка не создавалась).
Исходный SHA сборки: `e93bf0d15a775e3d00504cff0fb89a3e1a321b83`.
CI receipt checkpoint: `8c1c2a73fb22090b8a48a10dbf325cccd83a72d2`.

## Windows x64 CI

Workflow: `Masha Windows x64 Stage 1.8`.
GitHub Actions run: `33172923059`, attempt 1.
Результат: SUCCESS; PE machine: `8664`; файлов в Release: 94.
CI ZIP SHA-256: `EE6109F342997D8E86073563643F784F6D629F1859C61D1E344689A3519C3924`.
Квитанция: `docs/ci/stage-1.8-windows-x64-ci.txt`.

## Локальная контрольная сборка

Команда: `python build.py --portable --flutter --skip-portable-pack --hwcodec --vram`.
Результат: `LOCAL_STAGE_1_8_BUILD=SUCCESS`; PE machine: `0x8664`.
Release: 94 файла, 77 444 651 байт.
SHA-256 `.exe`: `6EEAC0F1F142B01362700CB6858A6464E4B62161A2A4B003325021C8837C8E7D`.
Локальный ZIP SHA-256: `DF86C4D9C970AB65DBA5DBB00852E63E414FFAC298EA5A0D43A1D124189820E1`.

## Установка на двух Windows x64 устройствах

| Устройство | Роль | ID | Служба | SHA-256 установленного `.exe` |
|---|---|---:|---|---|
| `local-server` | получатель | `114983435` | Running / Auto | `6EEAC0F1F142B01362700CB6858A6464E4B62161A2A4B003325021C8837C8E7D` |
| `desktop` | оператор | `294098875` | Running / Auto | `6EEAC0F1F142B01362700CB6858A6464E4B62161A2A4B003325021C8837C8E7D` |

## Реальный сеанс desktop → local-server

- Окно оператора: `114983435@local-server - Remote Desktop`.
- Путь: `relay`.
- `operator_id`: `294098875`; `target_id`: `114983435`.
- `session_id`: `9627432069951234514`.
- `lease_id`: `_ez_ZyK0DQGrxfFWIVJyS-Zt`.
- `authorize`: `allowed=True`; `lease/start`: `allowed=True`.
- Heartbeat: шесть успешных запросов с интервалом 11 секунд.
- Серверная длительность: 72 секунды.
- `lease/finish`: `allowed=True`; причина: `Peer close`.
- Usage и consumption записаны идемпотентно по одному разу.

## Production safety

Резервная копия перед испытанием:
`/opt/masha-auth/backups/stage-1.8-two-device-before-20260828T134328Z`.

После фиксации протокола временные production-данные удалены:
оператор — 1, grants — 2, lease — 1, usage — 1, consumption — 1.
Контроль после очистки: все счётчики для `294098875` равны 0,
SQLite `integrity_check=ok`, `masha-auth.service=active`, `/health=status:ok`.
Резервная копия сохранена для возможного восстановления.

## Итог

Windows x64 CI успешен. Одна и та же сборка установлена и испытана
на двух реальных Windows-устройствах. Критерий этапа 1.8 выполнен.
