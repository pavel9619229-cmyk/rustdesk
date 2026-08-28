# Masha Auth — этапы 1.1–1.5

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
heartbeat. Списание `time_credit` выполняется на этапе 1.6.

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
python3 masha_auth.py admin grant OPERATOR_ID --source ad_reward --grant-kind time_credit --quota-seconds 1800
python3 masha_auth.py admin revoke-grant GRANT_ID
python3 masha_auth.py admin billing OPERATOR_ID --billing-status overdue
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
