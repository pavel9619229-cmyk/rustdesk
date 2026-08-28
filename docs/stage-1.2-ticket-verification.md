# Этап 1.2 — проверка ticket получателем

Дата проверки: 2026-08-28

Статус: завершён

## Результат

В принимающую сторону UDU добавлен модуль `src/masha_ticket.rs`.
Он проверяет ticket до передачи решения будущему gate соединения.

Проверки выполняются fail-closed:

- Ed25519-подпись по закреплённому публичному ключу Masha Auth;
- версия `v=1` и издатель `iss=masha-auth`;
- время выдачи `iat`, срок `exp` и максимальный TTL 600 секунд;
- одноразовый идентификатор `jti` и повторное использование;
- привязка к `operator_id`, `target_id` и `session_id`;
- привязка к `connection_type` и `target_nonce`.

После успешной проверки `jti` атомарно помещается в локальный
replay-cache до окончания срока ticket. Просроченные записи удаляются.
## Контракт authorize

`POST /v1/session/authorize` теперь требует `session_id`.
Сервер подписывает его вместе с остальными claims.

Обязательные поля запроса:

- `operator_id`;
- `target_id`;
- `session_id`;
- `connection_type`;
- `client_version`;
- `target_nonce` для `connection_type=direct-ip`.

Запрос без `session_id` отклоняется:

`{"allowed":false,"reason":"invalid_request"}`

## Автоматические тесты

Rust 1.75.0, настоящий crate UDU:

`cargo test --release --lib masha_ticket::tests`

Результат: 7 passed, 0 failed.

Проверены действительный ticket, неверная подпись, истёкший ticket,
ticket из будущего, неправильные bindings, nonce и replay `jti`.
Python authorize в изолированной папке VPS:

`python3 -m unittest discover -s test -p 'test_*.py' -v`

Результат: 7 passed, 0 failed.

Production live-check:

- `LIVE_SIGNED_SESSION_BINDING=PASS`;
- `LIVE_MISSING_SESSION_DENIED=PASS`;
- тестовый оператор удалён;
- временные файлы удалены.

## Production

VPS UDU: `77.222.38.70`.

Сервис `masha-auth.service` активен, внешний `GET /health`
возвращает `{"status":"ok","service":"masha-auth","version":1}`.

SHA-256 развёрнутого `masha_auth.py`:

`8589fe812ded4ab490389fc452796cdccefa4e3414f8e8b1065d4d66b9f20be8`

Публичный Ed25519-ключ не изменён и совпадает с закреплённым в клиенте.
Backup перед развёртыванием:

`/opt/masha-auth/backups/stage-1.2-before-20260828-083828`

Checkpoint реализации:

`5b71d7a1132879a51db41f59dab414aba4a7fdb9`

Ветка: `masha/server-control-01`.

## Граница этапа

Этап 1.2 реализует и тестирует проверку ticket получателем.
Он пока не подключает эту проверку ко всем входящим путям соединения.

Следующий этап 1.3 — общий gate: обязательный вызов проверки перед
допуском relay и Direct IP соединений. Этап 1.3 ещё не начат.
