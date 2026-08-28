# Masha Auth — этап 1.1

Минимальный серверный endpoint авторизации сеанса:

`POST /v1/session/authorize`

Сервис выдаёт короткоживущий Ed25519-signed ticket только оператору
со статусом `active`. Статусы перечитываются из JSON перед каждым
решением, поэтому блокировка применяется без перезапуска сервиса.

## Граница этапа

В 1.1 входят endpoint, серверное решение `active/blocked/expired`
и выпуск подписанного ticket. Проверка ticket получателем, replay-защита,
общий gate P2P/relay/Direct IP, lease и heartbeat относятся к 1.2–1.4.

## Требования

- Node.js 20 или новее;
- приватный Ed25519-ключ вне репозитория;
- файл операторов вне репозитория;
- TLS reverse proxy перед сервисом в production.

Сервис слушает `127.0.0.1:8443` по умолчанию. Публиковать этот HTTP-порт
напрямую нельзя: внешний адрес должен использовать HTTPS.
## Запрос

```json
{
  "operator_id": "operator-01",
  "target_id": "target-01",
  "connection_type": "remote",
  "client_version": "1.4.9"
}
```

Для `connection_type=direct-ip` обязательно поле `target_nonce`.

Успешный ответ содержит `allowed=true`, `ticket` и `expires_at`.
Ticket имеет формат `base64url(payload).base64url(signature)`.
Подпись вычисляется Ed25519 по исходным байтам JSON payload.

Машинные причины отказа:

- `operator_unknown`;
- `operator_blocked`;
- `operator_expired`;
- `operator_inactive`;
- `invalid_request`;
- `target_nonce_required`.
## Локальная проверка

Из каталога `services/masha-auth`:

```powershell
node --check server.mjs
node --test test/authorize.test.mjs
```

Тесты создают временный ключ и временный реестр операторов. Секреты
и тестовые данные после завершения удаляются.

## Подготовка production-конфигурации

На сервере:

```bash
sudo install -d -m 700 -o masha-auth -g masha-auth /etc/masha-auth
node /opt/masha-auth/generate-keys.mjs /etc/masha-auth
sudo cp /opt/masha-auth/operators.example.json /etc/masha-auth/operators.json
sudo chown masha-auth:masha-auth /etc/masha-auth/operators.json
sudo chmod 600 /etc/masha-auth/operators.json
```

Затем скопировать `deploy/masha-auth.env.example` в
`/etc/masha-auth/masha-auth.env` и установить systemd unit.
## Запуск через systemd

```bash
sudo cp /opt/masha-auth/deploy/masha-auth.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now masha-auth
curl --fail http://127.0.0.1:8443/healthz
```

HTTPS reverse proxy должен передавать только
`/v1/session/authorize` и при необходимости `/healthz` на
`http://127.0.0.1:8443`.

Приватный ключ, рабочий `operators.json` и environment-файл
не добавляются в Git.
