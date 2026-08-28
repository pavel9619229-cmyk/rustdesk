# Masha Auth — этап 1.1

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
в `src/masha_ticket.rs`. Общий gate соединений, lease и heartbeat
относятся к следующим этапам 1.3–1.4.

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
## Управление операторами

На VPS из каталога `/opt/masha-auth`:

```bash
python3 masha_auth.py admin status
python3 masha_auth.py admin allow OPERATOR_ID
python3 masha_auth.py admin allow OPERATOR_ID --valid-until 2026-12-31T23:59:59Z
python3 masha_auth.py admin block OPERATOR_ID
python3 masha_auth.py admin expire OPERATOR_ID
python3 masha_auth.py admin remove OPERATOR_ID
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
