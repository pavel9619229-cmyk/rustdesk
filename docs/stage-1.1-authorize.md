# Этап 1.1 — сервер authorize

Дата проверки: 2026-08-28

Статус: завершён

## Результат

На VPS UDU `77.222.38.70` развёрнут production endpoint:

`POST https://77.222.38.70:8443/v1/session/authorize`

Сервис выдаёт короткоживущий Ed25519-signed ticket только оператору
с действующим правом. Реализованы отдельные машинные причины отказа:

- `operator_unknown`;
- `operator_blocked`;
- `operator_expired`.
В SQLite добавлено nullable-поле `operators.valid_until`.
Существующая база мигрирована на месте без потери записей.
Прежний signing key и public key сохранены.

## Автоматические тесты

Изолированные тесты на VPS:

- tests: 6;
- passed: 6;
- failed: 0.

Проверены active ticket/signature, blocked, expired, unknown,
обязательный nonce Direct IP и миграция legacy-базы.

## Production-проверка

Живые HTTPS-тесты:

- `ACTIVE_SIGNED_TICKET=PASS`;
- `BLOCKED=PASS`;
- `EXPIRED=PASS`;
- `UNKNOWN=PASS`;
- `TEST_DATA_CLEANUP=PASS`.
Тестовые операторы после проверки удалены.
Сервис `masha-auth.service` имеет состояние `active`.
Внешний `GET /health` без отключения TLS-проверки вернул HTTP 200.

SHA-256 локального и deployed `masha_auth.py` совпадает:

`804a95f91f9ffd225b40c129a30167ede36b703ed10967e24e803d3c391dc34d`

## Контрольные точки

Git implementation checkpoint:

`0a56549dc336f2abf35ed4554fcc75375ffd101f`

Резервная копия состояния VPS до обновления:

`/opt/masha-auth/backups/stage-1.1-before-20260828-1047`

Следующий этап: 1.2 «Проверка ticket получателем».
