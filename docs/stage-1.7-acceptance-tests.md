# Этап 1.7 — автоматические приёмочные тесты

Дата выполнения: 28.08.2026.
Исходный checkpoint: `6611fac3816a2be8c9e076a508ed24611821079d`.
Ветка: `masha/server-control-01` (новая ветка и новый worktree не создавались).

## Результат

Единый runner: `services/masha-auth/test-stage-1.7.ps1`.
Итог локального запуска: `STAGE_1_7=PASS`.
Python: 22 теста, ошибок нет. Rust: `masha_ticket::tests`, ошибок нет.
Производственная проверка: `PRODUCTION_SERVER_TESTS=PASS`.
Все временные производственные тестовые записи удалены: `TEST_DATA_CLEANUP=PASS`.

## Матрица обязательной приёмки

| № | Сценарий | Автоматическое доказательство | Итог |
|---:|---|---|---|
| 1 | Active | authorize выдаёт подписанный ticket; lease/start разрешает сеанс | PASS |
| 2 | Blocked / expired | blocked и expired получают машинный отказ | PASS |
| 3 | Fail-closed | недоступный authorize endpoint не выдаёт ticket | PASS |
| 4 | Direct IP | без ticket gate закрыт; authorize direct-ip требует nonce | PASS |
| 5 | Replay | повторный jti/ticket и session_id отклоняются | PASS |
| 6 | Wrong binding | получатель отклоняет ticket с чужими operator/target/session bindings | PASS |
| 7 | Tamper | изменение payload или подписи даёт `invalid_ticket` | PASS |
| 8 | Lease revoke | отзыв действующего grant завершает lease не позднее grace period | PASS |
| 9 | Heartbeat loss | stale lease закрывается как `heartbeat_lost`, длительность серверная | PASS |
| 10 | Idempotency | повторные start/finish и provider source event не дублируют usage, списание или grant | PASS |
| 11 | Alternative grant | overdue payment не блокирует ad_reward/promo/admin grant | PASS |
| 12 | Concurrent sessions | транзакционно применяется настройка `max_concurrent_sessions` | PASS |

Под webhook в тесте 10 проверяется идемпотентность уже верифицированного события провайдера по паре
`(source_type, source_id)`; отдельный публичный webhook без проверки подписи не добавлялся.

## Производственная проверка

Сервис: `masha-auth.service`, VPS `77.222.38.70`.
Проверялись реальные HTTPS-запросы authorize и lease к `:8443`.
После развёртывания: service `active`, `GET /health` — `status=ok`,
SQLite `integrity=ok`, активных тестовых lease — 0, ошибок сервиса — 0.

SHA-256 локального и производственного `masha_auth.py` совпадает:
`5af332269f148824a3697311422f7a48c0411b2e4c2074c704e58160c97f2451`.

Резервная копия до обновления:
`/opt/masha-auth/backups/stage-1.7-before-20260828T120000Z`.

Временные stage-файлы после успешной проверки удалены. Резервная копия сохранена.
