# Этап 1.5 — Entitlement Engine

Дата: 2026-08-28

## Результат

Masha Auth принимает единое решение о доступе на основании активных grant:
`payment`, `ad_reward`, `trial`, `promo`, `admin`.

Поддержаны бессрочные/срочные права и временной кредит. Истёкшие grant
автоматически переводятся в `expired`, отозванные не участвуют в решении.
Выбранные `grant_id` и `grant_source` подписываются в session ticket.

Статус оплаты `payment_due`, `overdue` или `blocked` не блокирует
действующий grant другого источника. Без альтернативного права ответ:
`payment_required`.

Authorize и lease heartbeat используют один и тот же Entitlement Engine.
Отзыв последнего действующего grant завершает активный сеанс на следующем
heartbeat. Списание временного кредита относится к этапу 1.6.

## Совместимость

При миграции существующие активные записи `operators` получают legacy
admin grant. Старые команды allow/block/expire остаются рабочими.

## Проверка

Локальный набор: 17 тестов, включая все пять источников, overdue с
альтернативными grant, отсутствие grant, expiry/revoke, остановку lease и
регрессионную проверку повторной миграции.

Production после резервного копирования: overdue + promo — PASS; отзыв grant
закрывает lease — PASS; overdue без grant — PASS; time credit ad_reward — PASS;
временные операторы и активные grant теста удалены.
