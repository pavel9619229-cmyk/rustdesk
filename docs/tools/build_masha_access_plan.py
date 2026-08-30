from pathlib import Path
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Table, TableStyle


OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("masha-server-access-plan-restructured.pdf")
PAGE_W, PAGE_H = A4
TOC_H = 445

NAVY = colors.HexColor("#123F5B")
BLUE = colors.HexColor("#1976C9")
PALE_BLUE = colors.HexColor("#EAF4FB")
GREEN = colors.HexColor("#138A56")
PALE_GREEN = colors.HexColor("#E8F5EE")
ORANGE = colors.HexColor("#D97300")
PALE_ORANGE = colors.HexColor("#FFF3DC")
RED = colors.HexColor("#C92727")
PALE_RED = colors.HexColor("#FFF0F0")
TEXT = colors.HexColor("#172333")
MUTED = colors.HexColor("#617180")
LINE = colors.HexColor("#C9D6DE")
ROW = colors.HexColor("#F4F7F8")


def register_fonts():
    candidates = [
        (Path(r"C:\Windows\Fonts\arial.ttf"), Path(r"C:\Windows\Fonts\arialbd.ttf"), Path(r"C:\Windows\Fonts\consola.ttf")),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")),
    ]
    for regular, bold, mono in candidates:
        if regular.exists() and bold.exists():
            pdfmetrics.registerFont(TTFont("Body", str(regular)))
            pdfmetrics.registerFont(TTFont("BodyBold", str(bold)))
            pdfmetrics.registerFont(TTFont("Mono", str(mono if mono.exists() else regular)))
            return
    raise RuntimeError("Suitable fonts were not found")


register_fonts()

styles = {
    "body": ParagraphStyle("body", fontName="Body", fontSize=9.2, leading=12, textColor=TEXT),
    "small": ParagraphStyle("small", fontName="Body", fontSize=8, leading=10, textColor=TEXT),
    "muted": ParagraphStyle("muted", fontName="Body", fontSize=7.5, leading=9, textColor=MUTED),
    "bold": ParagraphStyle("bold", fontName="BodyBold", fontSize=9.2, leading=12, textColor=TEXT),
    "table": ParagraphStyle("table", fontName="Body", fontSize=7.7, leading=9, textColor=TEXT),
    "table_bold": ParagraphStyle("table_bold", fontName="BodyBold", fontSize=7.7, leading=9, textColor=TEXT),
    "center": ParagraphStyle("center", fontName="BodyBold", fontSize=8, leading=9, alignment=TA_CENTER, textColor=TEXT),
}


def esc(text):
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def para(text, style="body"):
    return Paragraph(esc(text), styles[style])


def draw_wrapped(c, text, x, y, width, style="body"):
    p = para(text, style)
    _, h = p.wrap(width, PAGE_H)
    p.drawOn(c, x, y - h)
    return y - h


def draw_heading(c, text, x, y, size=13, color=NAVY):
    c.setFont("BodyBold", size)
    c.setFillColor(color)
    c.drawString(x, y, text)
    return y - size - 7


def draw_bullets(c, items, x, y, width, size=8.6, leading=12, color=TEXT):
    style = ParagraphStyle("bullet", fontName="Body", fontSize=size, leading=leading, textColor=color, leftIndent=13, firstLineIndent=-9)
    for item in items:
        p = Paragraph("• " + esc(item), style)
        _, h = p.wrap(width, PAGE_H)
        p.drawOn(c, x, y - h)
        y -= h + 2
    return y


def draw_box(c, x, top, width, height, fill, stroke, radius=8):
    c.setFillColor(fill)
    c.setStrokeColor(stroke)
    c.setLineWidth(1)
    c.roundRect(x, top - height, width, height, radius, fill=1, stroke=1)


def draw_table(c, data, widths, x, top, row_heights=None, header=True, font_size=7.7):
    cells = []
    for r, row in enumerate(data):
        cells.append([para(v, "table_bold" if header and r == 0 else "table") for v in row])
    t = Table(cells, colWidths=widths, rowHeights=row_heights, hAlign="LEFT")
    style = [
        ("GRID", (0, 0), (-1, -1), 0.45, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("BACKGROUND", (0, 0), (-1, 0), colors.white),
    ]
    for r in range(1 if header else 0, len(data)):
        style.append(("BACKGROUND", (0, r), (-1, r), ROW if r % 2 else colors.white))
    t.setStyle(TableStyle(style))
    _, h = t.wrap(sum(widths), PAGE_H)
    t.drawOn(c, x, top - h)
    return top - h, h


def header(c, section, title, dest, page_num, status="актуально на 30.08.2026", outline=None, level=1):
    c.bookmarkPage(dest)
    if outline:
        c.addOutlineEntry(outline, dest, level=level, closed=False)
    c.setFillColor(NAVY)
    c.rect(0, PAGE_H - 72, PAGE_W, 72, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#AFC7D6"))
    c.setFont("BodyBold", 8.5)
    c.drawString(38, PAGE_H - 28, section)
    c.setFillColor(colors.white)
    c.setFont("BodyBold", 20)
    c.drawString(38, PAGE_H - 52, title)
    c.setFont("Body", 7.5)
    c.drawRightString(PAGE_W - 38, PAGE_H - 27, status)
    bx, by, bw, bh = PAGE_W - 133, PAGE_H - 64, 95, 22
    c.setFillColor(colors.white)
    c.setStrokeColor(colors.white)
    c.rect(bx, by, bw, bh, fill=1, stroke=0)
    c.setFillColor(BLUE)
    c.setFont("Body", 8)
    c.drawCentredString(bx + bw / 2, by + 7, "В оглавление")
    c.linkRect("", "toc", (bx, by, bx + bw, by + bh), relative=0, thickness=0)
    c.setStrokeColor(LINE)
    c.line(38, 27, PAGE_W - 38, 27)
    c.setFillColor(MUTED)
    c.setFont("Body", 7)
    c.drawString(38, 15, "Маша · UDU · технический план")
    c.drawRightString(PAGE_W - 38, 15, f"{page_num}/7")


def toc_subrow(c, x, y, width, label, page, dest):
    c.setFillColor(colors.white)
    c.setStrokeColor(LINE)
    c.roundRect(x, y - 27, width, 27, 5, fill=1, stroke=1)
    c.setFillColor(TEXT)
    c.setFont("Body", 8.3)
    c.drawString(x + 12, y - 17, label)
    c.setFillColor(MUTED)
    c.setFont("BodyBold", 7.4)
    c.drawRightString(x + width - 12, y - 17, f"стр. {page}  ›")
    c.linkRect("", dest, (x, y - 27, x + width, y), relative=0, thickness=0)


def page_toc(c):
    c.setPageSize((PAGE_W, TOC_H))
    c.bookmarkPage("toc")
    c.addOutlineEntry("1. Оглавление", "toc", level=0, closed=False)
    c.setFillColor(NAVY)
    c.rect(0, TOC_H - 75, PAGE_W, 75, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("BodyBold", 8)
    c.drawString(24, TOC_H - 24, "МАША · UDU · ТЕХНИЧЕСКИЙ ПЛАН V10")
    c.setFont("BodyBold", 20)
    c.drawString(24, TOC_H - 54, "1. ОГЛАВЛЕНИЕ")
    c.setFont("Body", 7.2)
    c.drawRightString(PAGE_W - 24, TOC_H - 24, "актуально на 30.08.2026")

    x, width = 24, PAGE_W - 48
    top = TOC_H - 94
    c.setFillColor(PALE_BLUE)
    c.setStrokeColor(BLUE)
    c.roundRect(x, top - 128, width, 128, 8, fill=1, stroke=1)
    c.setFillColor(NAVY)
    c.setFont("BodyBold", 11)
    c.drawString(x + 12, top - 20, "2. СТРУКТУРА И ТЕРМИНЫ")
    c.setFillColor(MUTED)
    c.setFont("Body", 7.5)
    c.drawRightString(x + width - 12, top - 20, "стр. 2-4")
    toc_subrow(c, x + 10, top - 34, width - 20, "Состав системы и основные термины", 2, "structure")
    toc_subrow(c, x + 10, top - 64, width - 20, "Авторизация и управление сеансом", 3, "authorization")
    toc_subrow(c, x + 10, top - 94, width - 20, "Данные, события, API и учёт времени", 4, "data_api")

    top2 = top - 142
    c.setFillColor(PALE_GREEN)
    c.setStrokeColor(GREEN)
    c.roundRect(x, top2 - 128, width, 128, 8, fill=1, stroke=1)
    c.setFillColor(GREEN)
    c.setFont("BodyBold", 11)
    c.drawString(x + 12, top2 - 20, "3. ЭТАПЫ ВЫПОЛНЕНИЯ")
    c.setFillColor(MUTED)
    c.setFont("Body", 7.5)
    c.drawRightString(x + width - 12, top2 - 20, "стр. 5-7")
    toc_subrow(c, x + 10, top2 - 34, width - 20, "Этап 1: выполненные пункты 1.0-1.9", 5, "stage1")
    toc_subrow(c, x + 10, top2 - 64, width - 20, "Этап 1.9: приёмочные тесты и доказательства", 6, "stage19")
    toc_subrow(c, x + 10, top2 - 94, width - 20, "Этап 2: коммерциализация, пункты 2.0-2.6", 7, "stage2")
    c.showPage()


def page_structure(c):
    c.setPageSize(A4)
    header(c, "РАЗДЕЛ 2 · СТРУКТУРА И ТЕРМИНЫ", "Состав системы и основные термины", "structure", 2,
           outline="2. Структура и термины", level=0)
    c.addOutlineEntry("Состав системы и основные термины", "structure", level=1, closed=False)
    x, width, y = 38, PAGE_W - 76, PAGE_H - 92

    draw_box(c, x, y, width, 58, PALE_GREEN, GREEN)
    c.setFillColor(GREEN)
    c.setFont("BodyBold", 10)
    c.drawString(x + 14, y - 19, "ГЛАВНЫЙ ПРИНЦИП")
    draw_wrapped(c, "Без подтверждения сервера нельзя начать ни обычный, ни Direct IP-сеанс. Клиент бесплатный. Право оператора определяет единый Access / Entitlement Engine.", x + 14, y - 31, width - 28, "small")
    y -= 73

    y = draw_heading(c, "Участники системы", x, y, 12)
    participants = [
        ["Участник", "Роль"],
        ["Клиент", "Публикует ID и heartbeat; предоставляет удалённый доступ к устройству."],
        ["Оператор", "Передаёт Device Key и ID клиента; запрашивает право на соединение."],
        ["Сервер Маши", "Проверяет право, лимиты и блокировки; выдаёт session ticket и lease."],
        ["Принимающая Маша", "Проверяет ticket до P2P, relay или Direct IP-соединения."],
    ]
    y, _ = draw_table(c, participants, [92, width - 92], x, y, row_heights=[20, 30, 30, 30, 30])
    y -= 14

    y = draw_heading(c, "Решение о соединении", x, y, 12)
    draw_box(c, x, y, width, 42, PALE_GREEN, GREEN, 5)
    c.setFillColor(GREEN); c.setFont("BodyBold", 8.5); c.drawString(x + 12, y - 16, "РАЗРЕШЕНО")
    draw_wrapped(c, "Сервер выдаёт короткоживущий подписанный ticket; активный сеанс поддерживается короткой lease.", x + 100, y - 11, width - 112, "small")
    y -= 48
    draw_box(c, x, y, width, 42, PALE_RED, RED, 5)
    c.setFillColor(RED); c.setFont("BodyBold", 8.5); c.drawString(x + 12, y - 16, "ЗАПРЕЩЕНО")
    draw_wrapped(c, "Новый сеанс не начинается; при непродлении lease активный сеанс завершается после grace period.", x + 100, y - 11, width - 112, "small")
    y -= 57

    y = draw_heading(c, "Основные термины", x, y, 12)
    terms = [
        ["Session ticket", "Подписанное разрешение на один конкретный сеанс.", "Lease", "Короткое право продолжать уже начатый сеанс."],
        ["Heartbeat", "Периодический запрос продления lease.", "Fail-closed", "При ошибке проверки новый сеанс запрещается."],
        ["P2P / relay", "Прямой путь или передача трафика через relay.", "Direct IP", "Прямое соединение по IP, но с той же авторизацией."],
        ["Access grant", "Серверная запись о праве доступа.", "Device Key", "Идентификатор экземпляра операторской Маши."],
        ["Nonce / jti", "Одноразовость ticket и защита от повтора.", "Grace period", "Короткий срок перед принудительным завершением."],
    ]
    y, _ = draw_table(c, terms, [73, 187, 73, 186], x, y, row_heights=[31] * 5, header=False)
    y -= 12
    y = draw_heading(c, "Источники права", x, y, 11)
    y = draw_wrapped(c, "payment, ad_reward, trial, promo и admin создают унифицированные access_grants. Состояние оплаты не равно итоговому состоянию доступа.", x, y, width, "small")
    y -= 5
    draw_wrapped(c, "Рекламное право создаётся только после server-to-server webhook рекламной сети либо серверной проверки receipt/token; приложение не может само заявить ad_watched=true.", x, y, width, "small")
    c.showPage()


def page_authorization(c):
    c.setPageSize(A4)
    header(c, "РАЗДЕЛ 2 · СТРУКТУРА И ТЕРМИНЫ", "Авторизация и управление сеансом", "authorization", 3,
           outline="Авторизация и управление сеансом", level=1)
    x, width, y = 38, PAGE_W - 76, PAGE_H - 98
    y = draw_heading(c, "Решение сервера и fail-closed", x, y, 13)
    y = draw_bullets(c, [
        "Любой новый сеанс вызывает POST /v1/session/authorize.",
        "Если сервер авторизации недоступен или ответ нельзя проверить, новый сеанс запрещён.",
        "Путь трафика не меняет правило: P2P, relay и Direct IP используют одну авторизацию.",
        "Принимающее приложение проверяет подпись, срок и привязку права к конкретному сеансу.",
    ], x, y, width)
    y -= 10
    y = draw_heading(c, "Обязательные поля session ticket", x, y, 13)
    ticket = [
        ["Поле", "Назначение"],
        ["operator_device_id", "Разрешённый экземпляр операторской Маши"],
        ["client_id", "Конкретное принимающее устройство"],
        ["session_id", "Конкретный сеанс"],
        ["nonce, jti", "Одноразовость и защита от повторного использования"],
        ["iat, nbf, exp", "Время выдачи, начала действия и окончания"],
        ["kid", "Ключ подписи и его ротация"],
        ["constraints", "Режим, лимиты, версия протокола и иные ограничения"],
    ]
    y, _ = draw_table(c, ticket, [112, width - 112], x, y, row_heights=[20] + [25] * 7)
    y -= 18
    y = draw_heading(c, "Защита от повторного использования", x, y, 13)
    y = draw_wrapped(c, "jti регистрируется сервером и принимающей стороной. Ticket действует только для пары operator-device/client, конкретного session_id и nonce рукопожатия. Повторный ticket, другая цель или изменённые параметры отклоняются.", x, y, width, "body")
    y -= 18
    y = draw_heading(c, "Lease активного сеанса", x, y, 13)
    draw_bullets(c, [
        "После старта сервер создаёт короткую lease; приложение продлевает её heartbeat-запросами.",
        "Блокировка, отзыв права или исчерпание лимита запрещают продление.",
        "После пропуска heartbeat действует короткий серверный grace period, затем сеанс завершается.",
        "Интервалы heartbeat, TTL ticket и grace period являются серверными параметрами.",
    ], x, y, width)
    c.showPage()


def page_data(c):
    c.setPageSize(A4)
    header(c, "РАЗДЕЛ 2 · СТРУКТУРА И ТЕРМИНЫ", "Данные, события, API и учёт времени", "data_api", 4,
           outline="Данные, события, API и учёт времени", level=1)
    x, width, y = 38, PAGE_W - 76, PAGE_H - 98
    y = draw_heading(c, "Основные таблицы", x, y, 13)
    tables = [
        ["Таблица", "Содержание"],
        ["operators, devices", "Device Key, состояние экземпляра, версия, блокировка"],
        ["access_policies", "Правила тарифа, группы и оператора"],
        ["access_grants", "Источник, тип, объём, valid_from, valid_until, статус"],
        ["grant_consumption", "Резервирование и списание права по сеансам"],
        ["usage_sessions", "authorize, start, heartbeat, finish, серверная длительность"],
        ["session_tickets", "jti, session_id, bindings, срок, состояние использования"],
        ["billing_accounts", "Расчётный статус отдельно от права доступа"],
        ["payment_events", "Исходные webhook-события и результат обработки"],
        ["ad_reward_events", "Provider, offer/nonce, receipt, награда, уникальность"],
        ["audit_log", "Решения доступа, отказы, блокировки и административные действия"],
    ]
    y, _ = draw_table(c, tables, [118, width - 118], x, y, row_heights=[20] + [23] * 10)
    y -= 15
    y = draw_heading(c, "Идемпотентность и доверие к событиям", x, y, 12)
    y = draw_bullets(c, [
        "Каждый платёжный и рекламный webhook имеет уникальный provider event ID.",
        "Подпись провайдера проверяется до изменения состояния.",
        "Повторная доставка не создаёт повторный grant или начисление.",
        "Сохраняются исходное событие, время получения, результат проверки и связь с grant.",
    ], x, y, width, size=8.2, leading=10.5)
    y -= 8
    y = draw_heading(c, "Основные API", x, y, 12)
    y = draw_bullets(c, [
        "POST /v1/session/authorize - решение и выдача ticket.",
        "POST /v1/sessions/start, /heartbeat, /finish - lease, время и списание.",
        "GET /v1/access/status - итоговый доступ, причина и варианты восстановления.",
        "POST /v1/payments/create; POST /v1/webhooks/yookassa.",
        "GET /v1/ad-rewards/offers; POST /v1/ad-rewards/prepare.",
        "POST /v1/webhooks/ad/{provider}; POST /v1/ad-rewards/claim.",
    ], x, y, width, size=8.2, leading=10.5)
    y -= 8
    y = draw_heading(c, "Учёт времени", x, y, 12)
    draw_wrapped(c, "Время услуги - период установленного сеанса удалённого управления от подтверждённого start до finish либо серверного закрытия после пропажи heartbeat. Сервер задаёт округление, минимальную единицу, правила аварийного завершения и одновременных сеансов. Повторные запросы не увеличивают время и списание.", x, y, width, "small")
    c.showPage()


def page_stage1(c):
    c.setPageSize(A4)
    header(c, "РАЗДЕЛ 3 · ЭТАПЫ ВЫПОЛНЕНИЯ", "Этап 1: завершённые работы", "stage1", 5,
           outline="3. Этапы выполнения", level=0)
    c.addOutlineEntry("Этап 1: выполненные пункты 1.0-1.9", "stage1", level=1, closed=False)
    x, width, y = 38, PAGE_W - 76, PAGE_H - 96
    draw_box(c, x, y, width, 54, PALE_GREEN, GREEN)
    c.setFillColor(GREEN); c.setFont("BodyBold", 13); c.drawString(x + 16, y - 22, "ЭТАП 1 ЗАВЕРШЁН")
    c.setFillColor(TEXT); c.setFont("Body", 8.5); c.drawString(x + 170, y - 22, "desktop -> local-server подтверждено; пункты 1.0-1.9 закрыты.")
    y -= 73
    y = draw_heading(c, "Подтверждённые результаты", x, y, 12)
    y = draw_bullets(c, [
        "Git: 7c9bda7d0; Windows x64 и CI проходят; 12 из 12 серверных проверок успешны.",
        "Сеанс через rendezvous 77.222.38.70:21116 и relay :21117; H264, стабильные 30 FPS.",
    ], x, y, width, size=8.4, leading=11)
    y -= 9
    y = draw_heading(c, "Пункты этапа 1", x, y, 12)
    stages = [
        ["1.0", "CI и Windows-сборка", "ЗАВЕРШЁН"],
        ["1.1", "Базовое серверное управление", "ЗАВЕРШЁН"],
        ["1.2", "Внешний deny до relay", "ЗАВЕРШЁН"],
        ["1.3", "Подпись, nonce и защита от повторов", "ЗАВЕРШЁН"],
        ["1.4", "Fail-closed и совместимость протокола", "ЗАВЕРШЁН"],
        ["1.5", "Управление доступом оператора", "ЗАВЕРШЁН"],
        ["1.6", "Стабильность rendezvous/relay", "ЗАВЕРШЁН"],
        ["1.7", "Подготовка рабочего контура", "ЗАВЕРШЁН"],
        ["1.8", "Реальное desktop -> local-server", "ЗАВЕРШЁН"],
        ["1.9", "Приёмочные тесты и доказательства результата   Подробнее ->", "ЗАВЕРШЁН"],
    ]
    table_top = y
    y, _ = draw_table(c, stages, [45, width - 145, 100], x, y, row_heights=[23] * 10, header=False)
    c.linkRect("", "stage19", (x, table_top - 230, x + width, table_top - 207), relative=0, thickness=0)
    y -= 18
    draw_box(c, x, y, width, 55, PALE_GREEN, GREEN)
    c.setFillColor(GREEN); c.setFont("BodyBold", 10); c.drawString(x + 16, y - 20, "ПРИЁМКА ЭТАПА 1")
    c.setFillColor(TEXT); c.setFont("Body", 8.5); c.drawString(x + 150, y - 20, "Пункт 1.9: 12 из 12 обязательных проверок пройдены.")
    c.setFillColor(BLUE); c.setFont("BodyBold", 8); c.drawRightString(x + width - 16, y - 39, "Открыть подробности  ›")
    c.linkRect("", "stage19", (x, y - 55, x + width, y), relative=0, thickness=0)
    c.showPage()


def page_stage19(c):
    c.setPageSize(A4)
    header(c, "РАЗДЕЛ 3 · ЭТАПЫ ВЫПОЛНЕНИЯ", "Этап 1.9: приёмочные тесты", "stage19", 6,
           outline="Этап 1.9: приёмочные тесты и доказательства", level=1)
    x, width, y = 38, PAGE_W - 76, PAGE_H - 96
    draw_box(c, x, y, width, 48, PALE_GREEN, GREEN)
    c.setFillColor(GREEN); c.setFont("BodyBold", 11); c.drawString(x + 16, y - 20, "ЭТАП 1.9 ЗАВЕРШЁН")
    c.setFillColor(TEXT); c.setFont("Body", 8.5); c.drawString(x + 185, y - 20, "12 обязательных серверных проверок пройдены.")
    y -= 67
    y = draw_heading(c, "Обязательные приёмочные тесты", x, y, 12)
    tests = [
        "Active: действующее право - сеанс разрешён.",
        "Blocked / expired: новое соединение запрещено.",
        "Fail-closed: сервер авторизации недоступен - новое соединение запрещено.",
        "Direct IP: без действующего ticket соединение запрещено.",
        "Replay: повторное использование ticket или jti запрещено.",
        "Wrong binding: ticket другого клиента, оператора или session_id запрещён.",
        "Tamper: изменение полей или подписи приводит к отказу.",
        "Lease revoke: отзыв права прекращает активный сеанс после grace period.",
        "Heartbeat loss: пропажа heartbeat закрывает сеанс и фиксирует серверную длительность.",
        "Idempotency: повторные start/finish/webhook не создают двойного списания или grant.",
        "Alternative grant: payment overdue не блокирует действующий ad_reward, promo или admin grant.",
        "Concurrent sessions: сервер применяет заданный лимит одновременных сеансов.",
    ]
    num_style = ParagraphStyle("num", fontName="Body", fontSize=7.8, leading=10.2, textColor=TEXT)
    for i, item in enumerate(tests, 1):
        p = Paragraph(f"<b>{i}.</b>&nbsp;&nbsp;{esc(item)}", num_style)
        _, h = p.wrap(width, PAGE_H)
        p.drawOn(c, x + 12, y - h)
        y -= h + 2
    y -= 9
    y = draw_heading(c, "Доказательства результата", x, y, 11.5)
    y = draw_bullets(c, [
        "Коммит и точный SHA в репозитории на server.",
        "Лог автоматических тестов и успешная Windows x64 сборка.",
        "Протокол ручного теста на операторском и клиентском устройствах.",
    ], x, y, width, size=8, leading=10)
    y -= 8
    draw_box(c, x, y, width, 67, colors.HexColor("#F4F7F8"), LINE)
    c.setFillColor(TEXT); c.setFont("BodyBold", 9.5); c.drawString(x + 14, y - 18, "Не входит в этап 1")
    draw_wrapped(c, "Редизайн интерфейса, installer/auto-update, YooKassa UI, рекламный UI, защита бинарника и сайт не задерживают подтверждение серверного контроля. Эти работы входят в этап 2.", x + 14, y - 29, width - 28, "small")
    c.showPage()


def roadmap_row(c, x, y, width, num, title, status, detail, fill=colors.white, stroke=LINE, accent=NAVY):
    h = 54
    draw_box(c, x, y, width, h, fill, stroke, 7)
    c.setFillColor(accent); c.circle(x + 23, y - h / 2, 15, fill=1, stroke=0)
    c.setFillColor(colors.white); c.setFont("BodyBold", 8.5); c.drawCentredString(x + 23, y - h / 2 - 3, num)
    c.setFillColor(NAVY); c.setFont("BodyBold", 10.5); c.drawString(x + 50, y - 19, title)
    c.setFillColor(MUTED); c.setFont("Body", 7.7); c.drawString(x + 50, y - 38, detail)
    tag_w = max(63, pdfmetrics.stringWidth(status, "BodyBold", 7) + 18)
    c.setFillColor(colors.HexColor("#64717B")); c.roundRect(x + width - tag_w - 11, y - 25, tag_w, 17, 8, fill=1, stroke=0)
    c.setFillColor(colors.white); c.setFont("BodyBold", 7); c.drawCentredString(x + width - tag_w / 2 - 11, y - 19, status)
    return y - h - 8


def page_stage2(c):
    c.setPageSize(A4)
    header(c, "РАЗДЕЛ 3 · ЭТАПЫ ВЫПОЛНЕНИЯ", "Этап 2: коммерциализация", "stage2", 7,
           outline="Этап 2: коммерциализация, пункты 2.0-2.6", level=1)
    x, width, y = 38, PAGE_W - 76, PAGE_H - 92
    draw_box(c, x, y, width, 43, PALE_BLUE, BLUE)
    c.setFillColor(BLUE); c.setFont("BodyBold", 9); c.drawString(x + 14, y - 17, "ПРИНЦИП")
    draw_wrapped(c, "Сначала серверный учёт права доступа и долга; затем платёжный провайдер и альтернативные способы доступа.", x + 90, y - 11, width - 104, "small")
    y -= 57

    draw_box(c, x, y, width, 118, PALE_ORANGE, ORANGE)
    c.setFillColor(ORANGE); c.setFont("BodyBold", 12); c.drawString(x + 16, y - 22, "2.0  СЕРВЕРНАЯ ПОСТОПЛАТА")
    c.setFillColor(ORANGE); c.setFont("BodyBold", 8); c.drawRightString(x + width - 16, y - 22, "ТЕКУЩИЙ ЭТАП")
    c.setFillColor(TEXT); c.setFont("Body", 8.2)
    c.drawString(x + 16, y - 42, "Тариф 1 ₽/час; источник истины - сервер: duration, debt, due/grace, T-10, /v1/access/status, идемпотентность.")
    c.setFont("BodyBold", 8.2); c.drawString(x + 16, y - 63, "Критерии готовности:")
    c.setFont("Body", 7.7)
    c.drawString(x + 28, y - 79, "• 1 час подтверждённой активности = 1 ₽; повторный finish не создаёт долг;")
    c.drawString(x + 28, y - 92, "• просрочка без grant блокирует новый сеанс; ad_reward, promo и admin сохраняют доступ;")
    c.drawString(x + 28, y - 105, "• предупреждение формируется за 10 минут до блокировки.")
    y -= 132

    y = roadmap_row(c, x, y, width, "2.1", "ЮKassa", "СЛЕДУЮЩИЙ", "Создание платежа, webhook, сверка статуса и закрытие задолженности.", PALE_GREEN, LINE)
    y = roadmap_row(c, x, y, width, "2.2", "Rewarded-реклама", "ПОСЛЕ 2.1", "Grant ad_reward с серверным сроком действия; без обхода платёжных правил.")
    y = roadmap_row(c, x, y, width, "2.3", "Установщик и автообновление", "ПОСЛЕ БИЛЛИНГА", "Подписанный установщик, проверяемое обновление, контролируемый откат.")
    y = roadmap_row(c, x, y, width, "2.4", "Защита бинарей", "ПЕРЕД ПУБЛИКАЦИЕЙ", "Подпись, контроль целостности и базовое усложнение подмены клиента.")
    y = roadmap_row(c, x, y, width, "2.5", "Интерфейс и бренд", "ПЕРЕД ПИЛОТОМ", "Понятные статусы доступа, долга, предупреждения и оплаты.")
    y = roadmap_row(c, x, y, width, "2.6", "Сайт и выпуск", "ФИНАЛ", "Тарифы, документы, загрузка, поддержка и публичный контур.")
    draw_box(c, x, y + 2, width, 46, PALE_GREEN, GREEN)
    c.setFillColor(GREEN); c.setFont("BodyBold", 8.5); c.drawString(x + 14, y - 15, "БЛИЖАЙШАЯ КОНТРОЛЬНАЯ ТОЧКА")
    c.setFillColor(TEXT); c.setFont("Body", 7.8); c.drawString(x + 14, y - 32, "Этап 2.0 принят после автоматических тестов правил начисления и блокировки.")
    c.showPage()


def build():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(OUT), pagesize=A4, pageCompression=1)
    c.setTitle("Маша: серверный доступ, оплата и реклама")
    c.setAuthor("UDU")
    c.setSubject("Технический план проекта")
    page_toc(c)
    page_structure(c)
    page_authorization(c)
    page_data(c)
    page_stage1(c)
    page_stage19(c)
    page_stage2(c)
    c.save()
    print(OUT)


if __name__ == "__main__":
    build()
