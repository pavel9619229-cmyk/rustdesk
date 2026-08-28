# Этап 1.4 — Lease и heartbeat

Дата проверки: 2026-08-28

Статус: завершён

## Реализация

Production auth-сервис получил endpoints:

- `POST /v1/session/lease/start`;
- `POST /v1/session/lease/heartbeat`;
- `POST /v1/session/lease/finish`.

Lease создаётся принимающей «Машей» только после проверки ticket и фактической локальной авторизации соединения. Heartbeat выполняется каждые 10 секунд. При явном отзыве права активный сеанс закрывается после следующего heartbeat. При недоступности сервера действует grace period 30 секунд, после которого соединение закрывается fail-closed.

SQLite хранит lease ID, hash секретного lease-token, bindings, `started_at`, `last_heartbeat`, `finished_at`, `finish_reason` и серверную `duration_seconds`. Повторный finish идемпотентен.

## Проверки

Rust release: 12 passed, 0 failed.

Python: 11 passed, 0 failed.

Production live HTTPS:

- `LEASE_RENEW=PASS`;
- `LEASE_REVOKE=PASS`;
- `FINISH_IDEMPOTENT=PASS`;
- `HEARTBEAT_LOSS=PASS`;
- `SERVER_DURATION=PASS`.

Внешний `GET /health` вернул HTTP 200. Тестовые операторы удалены.

SHA-256 локального и deployed `masha_auth.py`:

`4042072291E9846638BF5F4C4D643A0E9344084F84DEB1EA6DB662ED74405B4F`

Резервная копия VPS:

`/opt/masha-auth/backups/stage-1.4-before-20260828-131937`
