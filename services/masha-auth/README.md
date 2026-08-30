# Masha Auth — этапы 1.1–2.0

Production-совместимый Python-сервис авторизации сеанса:

`POST /v1/session/authorize`

Сервис работает на VPS UDU, использует SQLite и подписывает короткоживущие
ticket ключом Ed25519. Приватный ключ, база и TLS-файлы не хранятся в Git.

## Критерий 1.1

- действующий оператор получает подписанный ticket;
- неизвестный оператор получает `operator_unknown`;
- заблокированный оператор получает `operator_blocked`;
- оператор с истёкшим `valid_until` получает `operator_expired`;
- изменение статуса применяется без пересборки клиента.

Проверка ticket получателем и replay-защита реализованы на этапе 1.2
в `src/masha_ticket.rs`. Общий gate всех путей соединений реализован
на этапе 1.3, lease и heartbeat — на этапе 1.4.

## Запрос

```json
{
  "operator_id": "operator-01",
  "target_id": "target-01",
  "session_id": "session-01",
  "connection_type": "remote",
  "client_version": "1.4.9"
}
```

Для `connection_type=direct-ip` обязательно поле `target_nonce`.

Успешный ответ содержит `allowed=true`, `ticket`, `expires_at`
и `ticket_version=1`. Ticket имеет формат:

`base64url(payload).base64url(ed25519_signature)`

TTL настраивается в SQLite и ограничен диапазоном 30–600 секунд.
## Lease и heartbeat

После локального принятия соединения получатель создаёт lease:

- `POST /v1/session/lease/start`;
- `POST /v1/session/lease/heartbeat`;
- `POST /v1/session/lease/finish`.

Heartbeat отправляется каждые 10 секунд. Grace period при недоступности
сервера — 30 секунд. Блокировка или истечение права немедленно отклоняют
следующий heartbeat и завершают сеанс. Пропажа heartbeat переводит lease
в `heartbeat_lost`. Сервер сохраняет `started_at`, `finished_at`,
причину завершения и `duration_seconds`. Повторный finish идемпотентен.

## Entitlement Engine (этап 1.5)

Authorize и каждый heartbeat используют единый расчёт действующего права.
Поддерживаются источники `payment`, `ad_reward`, `trial`, `promo`, `admin`
и виды grant `unlimited_period`, `time_credit`. Истёкшие и отозванные grant
не дают доступ. Статус оплаты `payment_due`, `overdue` или `blocked` приводит
к `payment_required` только когда нет другого действующего grant. Поэтому
действующий ad reward, trial, promo или admin grant не блокируется долгом.

Выбранные `grant_id` и `grant_source` подписываются внутри session ticket.
Отзыв последнего действующего grant прекращает активный lease на следующем
heartbeat. Повторное подтверждённое событие с теми же `source_type` и
`source_id` возвращает существующий grant и не начисляет право повторно.

## Usage accounting (этап 1.6)

Каждый lease создаёт одну запись `usage_sessions`, связанную с `ticket_jti`,
`session_id` и выбранным grant. Heartbeat и finish учитывают только разницу
между уже записанной и текущей серверной длительностью. Накопительный расход
хранится в `grant_consumption` с уникальными `lease_id` и idempotency key.

Повторный start блокируется по ticket или session binding, а повторные
heartbeat/finish возвращают уже сохранённый результат без повторного списания.
Для `time_credit` остаток уменьшается атомарно; при нуле grant получает статус
`consumed`, а сеанс завершается с `quota_exhausted`. Heartbeat loss также
фиксирует серверную длительность и расход до конца grace period.

Число активных lease одного оператора ограничивается настройкой
`max_concurrent_sessions` (по умолчанию 1). Проверка и создание lease
выполняются в одной блокирующей транзакции.

## Серверная постоплата (этап 2.0)

Постоплата включается оператору отдельной политикой и grant
`postpaid_account`. Существующие операторы и grant этапа 1 при обновлении
не меняются и автоматически на постоплату не переводятся.

Базовая политика `postpaid-default` задаёт:

- тариф `100` копеек за `3600` секунд подтверждённой активности — ровно
  `1 ₽/час` без вычислений с плавающей точкой;
- срок оплаты 24 часа после появления первого начисления;
- grace period 1 час после срока оплаты;
- серверное предупреждение за 600 секунд до блокировки;
- один одновременный сеанс.

Все параметры политики хранятся на сервере и меняются административной
командой. Длительность берётся только из `usage_sessions`: от серверного
`start` до `finish` либо до серверного закрытия после потери heartbeat.
Повторные heartbeat и finish учитывают лишь ещё не записанную разницу и не
создают повторный долг.

Состояния расчёта: `current`, `payment_due`, `overdue`, `blocked`. После
окончания grace period новый сеанс по postpaid grant отклоняется с
`payment_required`. Действующие `ad_reward`, `promo` и `admin` grant имеют
приоритет и сохраняют доступ независимо от долга.

Текущее серверное решение доступно через:

```text
GET /v1/access/status?operator_id=OPERATOR_ID
```

Ответ содержит итоговый `allowed`, причину, выбранный grant, сумму долга в
копейках, срок оплаты, конец grace period, момент предупреждения,
`warning_10_minutes` и число секунд до блокировки.

## Автоматическая приёмка (этап 1.7)

Сценарии Active, blocked/expired, fail-closed, Direct IP, replay, wrong binding,
tamper, lease revoke, heartbeat loss, idempotency, alternative grant и
concurrent sessions запускаются одной командой:

```powershell
powershell -ExecutionPolicy Bypass -File .\test-stage-1.7.ps1
```

Скрипт запускает Python service tests и Rust release tests настоящего UDU crate,
проверяет наличие свидетельства каждого из 12 сценариев и завершает работу
с `STAGE_1_7=PASS` только после успешного прохождения всех проверок.

Для этапа 2.0 отдельный скрипт проверяет точный тариф, идемпотентность,
серверное закрытие по heartbeat, предупреждение T-10, блокировку,
альтернативные grant, погашение долга, HTTP status API и безопасную миграцию:

```powershell
powershell -ExecutionPolicy Bypass -File .\test-stage-2.0.ps1
```

Успешная приёмка завершается строкой `STAGE_2_0=PASS`.

## Управление операторами

На VPS из каталога `/opt/masha-auth`:

```bash
python3 masha_auth.py admin status
python3 masha_auth.py admin allow OPERATOR_ID
python3 masha_auth.py admin allow OPERATOR_ID --valid-until 2026-12-31T23:59:59Z
python3 masha_auth.py admin block OPERATOR_ID
python3 masha_auth.py admin expire OPERATOR_ID
python3 masha_auth.py admin remove OPERATOR_ID
python3 masha_auth.py admin grant OPERATOR_ID --source promo --expires-at 2026-12-31T23:59:59Z
python3 masha_auth.py admin grant OPERATOR_ID --source ad_reward --grant-kind time_credit --quota-seconds 1800 --source-id EVENT_ID
python3 masha_auth.py admin revoke-grant GRANT_ID
python3 masha_auth.py admin billing OPERATOR_ID --billing-status overdue
python3 masha_auth.py admin postpaid OPERATOR_ID
python3 masha_auth.py admin access-status OPERATOR_ID
python3 masha_auth.py admin settle OPERATOR_ID
python3 masha_auth.py admin policy postpaid-default --rate-minor-per-hour 100 --payment-due-seconds 86400 --grace-seconds 3600 --warning-seconds 600 --max-sessions 1
python3 masha_auth.py admin usage OPERATOR_ID
python3 masha_auth.py admin concurrency 2
```

`allow` без `--valid-until` создаёт бессрочное право. Значение
`--valid-until` может быть Unix timestamp либо датой ISO-8601.
## Тесты

```bash
python3 -m unittest discover -s test -p 'test_*.py' -v
```

Тесты используют отдельный временный каталог и не изменяют production-базу.

## Production

- код: `/opt/masha-auth/masha_auth.py`;
- база: `/opt/masha-auth/data/auth.db`;
- приватный ключ: `/opt/masha-auth/secrets/signing_key.pem`;
- systemd unit: `masha-auth.service`;
- HTTPS: `77.222.38.70:8443`;
- health check: `GET /health`.

Перед каждым обновлением создаётся резервная копия кода, базы и unit-файла.
