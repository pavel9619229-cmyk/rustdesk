# Этап 1.6 — Usage accounting

Дата: 2026-08-28

## Результат

Для каждого принятого ticket сервер создаёт одну запись `usage_sessions`,
связанную с lease, `session_id`, `ticket_jti` и выбранным grant.

Heartbeat и finish рассчитывают длительность по серверному времени и
записывают только разницу относительно `accounted_seconds`. Накопительный
расход хранится в `grant_consumption`: одна строка и один idempotency key
на lease. Все операции выполняются в одной SQLite-транзакции с блокировкой
записи, поэтому параллельные повторы не удваивают расход.

Повторный start с тем же ticket отклоняется как `ticket_replayed`; новый
ticket с повторным operator/session binding — как `session_replayed`.
Повторные heartbeat и finish возвращают уже учтённую длительность.

Для `time_credit` quota уменьшается на фактически учтённые секунды.
Остаток не может стать отрицательным. При исчерпании grant получает статус
`consumed`, а lease завершается с `quota_exhausted`. Потеря heartbeat
учитывает время до окончания grace period.

Существующие lease мигрируются в `usage_sessions` без повторного списания.
История usage не удаляется командой удаления оператора и остаётся для аудита.

## Проверка

Локально: 20 тестов, 0 ошибок. Покрыты повторные start/heartbeat/finish,
пошаговое списание, quota exhaustion, heartbeat loss и миграция старой БД.

Production после резервного копирования: replay start/session — PASS;
heartbeat + повторный finish — PASS, ровно 40 секунд; quota exhaustion — PASS,
ровно 15 секунд; heartbeat loss — PASS, ровно 40 секунд; тестовые записи
удалены. Сервис и SQLite integrity check находятся в состоянии `ok`.
