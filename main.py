import os
import re
import asyncio
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo
from collections import defaultdict

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties

# ------------------ НАСТРОЙКИ ------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден!")

# Уникальный админ (ему не приходят личные карточки, /zero только ему доступна)
TARGET_USER_ID = int(os.getenv("TARGET_USER_ID", "542345855"))

TZ = ZoneInfo("Europe/Minsk")

# Базовые значения
TRIGGER_BASE_PLUS = 2.55  # BYN for single '+'
MK_GENERIC = 2.39  # "+ мк"
GAB_VALUE = 2.89   # "габ"
# Цветные "мк" значения
MK_COLOR_VALUES = {
    "синяя": 4.05,
    "красная": 5.71,
    "оранжевая": 6.37,
    "салатовая": 8.03,
    "коричневая": 8.69,
    "светло-серая": 10.35,
    "светлосерая": 10.35,
    "розовая": 11.01,
    "темно-серая": 12.67,
    "темносерая": 12.67,
    "голубая": 13.33
}

# Разрешённые треды / соответствия названий
SHOP_NAMES = {
    (-1002079167705, 48): "A.",
    (-1002936236597, 3): "B.",
    (-1002423500927, 2): "E.",
    (-1003117964688, 5): "F.",
    (-1002864795738, 3): "G.",
    (-1002535060344, 5): "H.",
    (-1002477650634, 3): "I.",
    (-1003204457764, 4): "J.",
    (-1002660511483, 3): "K.",
    (-1002360529455, 3): "333.",
    (-1002538985387, 3): "L."
}

ALLOWED_THREADS = {chat: thread for (chat, thread) in SHOP_NAMES.keys()}

# ------------------ ХРАНИЛИЩА В ПАМЯТИ ------------------

# pending = { msg_id: { "message": Message, "reply": Message or None, "corrected": bool, "admin_sent": bool, "value": float } }
pending = {}

# user_ratings = { user_id: float }
user_ratings = {}

# user_mute_set: пользователи, у которых выключены личные карточки
muted_users = set()

# daily_stats = { user_id: { (chat_id, thread_id): [ { 'message_id':..., 'text':..., 'ts':datetime, 'value':float, 'triggers': [..] } , ... ] } }
daily_stats = defaultdict(lambda: defaultdict(list))

# daily_trigger_sum: глобальная сумма BYN за текущие сутки (всех триггеров)
daily_trigger_sum = 0.0

# driver_income: { user_id: float } — накопленный доход водителя за период от обновления до обновления счётчика
driver_income = defaultdict(float)

# ------------------ РЕЙТИНГ ------------------

def get_rating(user_id: int) -> float:
    return user_ratings.get(user_id, 5.0)

def update_rating(user_id: int, delta: float):
    old = get_rating(user_id)
    new = max(0.0, min(5.0, old + delta))
    user_ratings[user_id] = new
    return old, new

# ------------------ HELPERS ------------------

def shop_name_for_message(msg: Message) -> str:
    return SHOP_NAMES.get((msg.chat.id, msg.message_thread_id), "[индивидуальный отчёт]")

def format_dt_for_card(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%d.%m.%y %H:%M:%S")

def format_date_long(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%d.%m.%Y")

def format_byn(value: float) -> str:
    # format with comma decimal separator
    return f"{value:.2f}".replace(".", ",")

def escape_html(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ------------------ ТРИГГЕР-ПАРСЕР ------------------
# Алгоритм и regex'ы взяты из исходного кода — адаптированы под регистронезависимость и аккуратный вывод.

COLOR_WORDS = [
    "синяя","красная","оранжевая","салатовая","коричневая",
    "светло-?серая","розовая","темно-?серая","голубая"
]
COLOR_RE = r"(?:%s)" % "|".join(COLOR_WORDS)
RE_MK_COLOR = re.compile(r"(\+)?\s*мк\.?\s*(" + COLOR_RE + r")\b", flags=re.IGNORECASE)
RE_MK = re.compile(r"(\+)?\s*мк\.?\b", flags=re.IGNORECASE)
RE_GAB_MULT = re.compile(r"(?<!\d)(\d+)габ\b", flags=re.IGNORECASE)
RE_GAB = re.compile(r"(?<!\d)габ\b", flags=re.IGNORECASE)
RE_PLUS = re.compile(r"\+")

def parse_triggers_and_value(text: str):
    """
    Возвращает tuple (total_value_byn: float, triggers_list: list of dict)
    triggers_list содержит элементы вида:
      {"type": "mk_color", "color": "синяя", "span": (s,e), "value": 4.05, "raw": "+ мк синяя"}
    raw — кусок текста, как он был в исходном сообщении (с сохранением регистра).
    """
    global MK_COLOR_VALUES, MK_GENERIC, GAB_VALUE, TRIGGER_BASE_PLUS

    if not text:
        return 0.0, []

    stext_lower = text.lower()
    used_spans = []
    triggers = []
    total = 0.0

    def span_overlaps(s, e):
        for a, b in used_spans:
            if not (e <= a or s >= b):
                return True
        return False

    # 1) цветные мк
    for m in RE_MK_COLOR.finditer(text):
        s, e = m.span()
        if span_overlaps(s, e):
            continue
        color_raw = m.group(2)
        color_norm = color_raw.lower().replace("-", "")
        val = MK_COLOR_VALUES.get(color_norm)
        if val is None:
            # try match keys ignoring hyphen
            for k, v in MK_COLOR_VALUES.items():
                if k.replace("-", "") == color_norm:
                    val = v
                    break
        if val is None:
            val = MK_GENERIC
        raw_slice = text[s:e].strip()
        triggers.append({"type": "mk_color", "color": color_raw, "span": (s, e), "value": val, "raw": raw_slice})
        used_spans.append((s, e))
        total += val

    # 2) mk generic (non-colored)
    for m in RE_MK.finditer(text):
        s, e = m.span()
        if span_overlaps(s, e):
            continue
        raw_slice = text[s:e].strip()
        triggers.append({"type": "mk", "span": (s, e), "value": MK_GENERIC, "raw": raw_slice})
        used_spans.append((s, e))
        total += MK_GENERIC

    # 3) gab with multiplier (only concatenated, like "3габ")
    for m in RE_GAB_MULT.finditer(text):
        s, e = m.span()
        if span_overlaps(s, e):
            continue
        mul = int(m.group(1))
        val = mul * GAB_VALUE
        raw_slice = text[s:e].strip()
        triggers.append({"type": "gab_mult", "mult": mul, "span": (s, e), "value": val, "raw": raw_slice})
        used_spans.append((s, e))
        total += val

    # 4) standalone "габ"
    for m in RE_GAB.finditer(text):
        s, e = m.span()
        if span_overlaps(s, e):
            continue
        raw_slice = text[s:e].strip()
        triggers.append({"type": "gab", "span": (s, e), "value": GAB_VALUE, "raw": raw_slice})
        used_spans.append((s, e))
        total += GAB_VALUE

    # 5) remaining plus signs not covered by above spans
    for m in RE_PLUS.finditer(text):
        s, e = m.span()
        if span_overlaps(s, e):
            continue
        raw_slice = text[s:e].strip()
        triggers.append({"type": "plus", "span": (s, e), "value": TRIGGER_BASE_PLUS, "raw": raw_slice})
        used_spans.append((s, e))
        total += TRIGGER_BASE_PLUS

    return total, triggers

def triggers_to_strings(triggers: list) -> list:
    """
    Возвращает список строк-описаний триггеров в том виде, как они были найдены в тексте.
    Сохраняется исходный фрагмент raw (с оригинальным регистром).
    Если raw отсутствует — генерируем читаемую метку.
    """
    out = []
    for t in triggers:
        raw = t.get("raw")
        if raw:
            # normalize spaces
            s = " ".join(raw.split())
            out.append(s)
        else:
            if t["type"] == "mk_color":
                out.append(f"+ мк {t.get('color', '')}".strip())
            elif t["type"] == "mk":
                out.append("+ мк")
            elif t["type"] == "gab":
                out.append("габ")
            elif t["type"] == "gab_mult":
                out.append(f"{t.get('mult',1)}габ")
            elif t["type"] == "plus":
                out.append("+")
            else:
                out.append(t["type"])
    return out

# ------------------ СТРОКИ КАРТОЧЕК ------------------

async def send_card_to_admin(bot, original_msg: Message, per_msg_value: float, s_total: float):
    """
    Отправляет специальную карточку ТОЛЬКО TARGET_USER_ID в требуемом формате (без смайликов):
    Формат:
    +{per_msg} BYN | {shop} | {s_total} BYN
    [полный текст сообщения от (исходного) пользователя]  <-- в виде цитаты (экранируем HTML)
    username / id ID
    T-Driver income: {driver_income for that user}
    Online: HH:MM:SS  (время с первого сообщения пользователя за период)
    """
    if TARGET_USER_ID is None:
        return

    shop = shop_name_for_message(original_msg)
    username = original_msg.from_user.username or "—"
    uid = original_msg.from_user.id
    text = original_msg.text or ""

    per_msg_str = format_byn(per_msg_value)
    s_total_str = format_byn(s_total)

    # driver income for this user (may be 0)
    drv_income = driver_income.get(uid, 0.0)
    drv_income_str = format_byn(drv_income)

    # "Online" - compute period since first message from this user in daily_stats (if any)
    online_str = "00:00:00"
    user_entries = daily_stats.get(uid, {})
    first_ts = None
    for key, entries in user_entries.items():
        if entries:
            candidate = entries[0].get("ts")
            if first_ts is None or candidate < first_ts:
                first_ts = candidate
    if first_ts:
        delta = datetime.now(TZ) - first_ts
        # format HH:MM:SS
        total_seconds = int(delta.total_seconds())
        hrs = total_seconds // 3600
        mins = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        online_str = f"{hrs:02d}:{mins:02d}:{secs:02d}"

    # Build card
    # First line: +per_msg BYN | Shop | s_total BYN
    line1 = f"+{per_msg_str} BYN | {shop} | {s_total_str} BYN"
    # Quote message text (escape html)
    quoted = escape_html(text)
    lines = [
        line1,
        f"<i>{quoted}</i>",
        f"{username} / id {uid}",
        f"T-Driver income: {drv_income_str} BYN",
        f"Online: {online_str}"
    ]
    card = "\n".join(lines)

    try:
        await bot.send_message(TARGET_USER_ID, card, parse_mode="HTML")
    except Exception:
        pass

async def send_card_to_user(bot, original_msg: Message, per_msg_value: float, user_total: float, triggers: list):
    """
    Отправляет карточку пользователю (водителю).
    Формат:
    [Shop placeholder] [первые слова если нужно] [полный текст сообщения]  (в виде цитаты)
    +{per_msg} BYN | {user_total} BYN
    Обнаружены триггеры: {comma separated}
    """
    user_id = original_msg.from_user.id
    if user_id == TARGET_USER_ID:
        return
    if user_id in muted_users:
        return

    shop = shop_name_for_message(original_msg)
    text = original_msg.text or ""

    per_msg_str = format_byn(per_msg_value)
    total_str = format_byn(user_total)

    triggers_list = triggers_to_strings(triggers)
    triggers_line = ", ".join(triggers_list) if triggers_list else "—"

    # Build card
    # First line: shop + original message (quoted)
    # Use HTML italics for quote
    lines = [
        f"{shop} {escape_html(text)}",  # header line, as requested
        f"<i>{escape_html(text)}</i>",
        f"<b>+{per_msg_str} BYN</b> | <b>{total_str} BYN</b>",
        f"Обнаружены триггеры: {escape_html(triggers_line)}"
    ]
    card = "\n".join(lines)

    try:
        await bot.send_message(user_id, card, parse_mode="HTML")
    except Exception:
        pass

# ------------------ СТАТИСТИКА / ЗАПИСЬ СООБЩЕНИЯ ------------------

def record_message_for_daily_stats(msg: Message, per_msg_value: float, triggers: list):
    """
    Сохраняем сообщение в daily_stats (по user_id).
    """
    user_id = msg.from_user.id
    key = (msg.chat.id, msg.message_thread_id)
    entry = {
        "message_id": msg.message_id,
        "text": msg.text or "",
        "ts": msg.date.astimezone(TZ),
        "value": per_msg_value,
        "triggers": triggers
    }
    daily_stats[user_id][key].append(entry)

def adjust_daily_stats_on_edit(msg: Message, old_value: float):
    """
    Если сообщение отредактировали, корректируем запись и суммарную S.
    """
    global daily_trigger_sum
    user_id = msg.from_user.id
    key = (msg.chat.id, msg.message_thread_id)
    entries = daily_stats.get(user_id, {}).get(key, [])
    for e in entries:
        if e.get("message_id") == msg.message_id:
            # recompute new value
            new_value, new_triggers = parse_triggers_and_value(msg.text or "")
            diff = new_value - e.get("value", 0.0)
            if abs(diff) > 1e-9:
                daily_trigger_sum += diff
                # also adjust driver_income for that user
                driver_income[user_id] += diff
            # update entry
            e["text"] = msg.text or ""
            e["ts"] = msg.date.astimezone(TZ)
            e["value"] = new_value
            e["triggers"] = new_triggers
            return
    # not found -> add new
    new_value, new_triggers = parse_triggers_and_value(msg.text or "")
    record_message_for_daily_stats(msg, new_value, new_triggers)
    daily_trigger_sum += new_value
    driver_income[user_id] += new_value

# ------------------ ОТЧЁТ / BUILD REPORT ------------------

def build_report_for_user(user_id: int) -> str:
    now = datetime.now(TZ)
    date_str = format_date_long(now)
    user_data = daily_stats.get(user_id, {})
    if not user_data:
        return f"{date_str}\n\nОтчётов за сегодня не найдено."

    lines = [f"{date_str}\n"]
    report_index = 0
    for (chat_id, thread_id), entries in user_data.items():
        report_index += 1
        shop = SHOP_NAMES.get((chat_id, thread_id), "Неизвестная точка")
        total = len(entries)
        lines.append(f"Отчет {report_index}:\n{shop}\nВыполнено доставок: {total}\nСписок адресов:")
        for i, e in enumerate(entries, start=1):
            val = e.get("value", 0.0)
            val_str = format_byn(val)
            addr = e.get("text", "")
            lines.append(f"{i}) {addr} +{val_str} BYN")
        lines.append("")
    return "\n".join(lines)

# ------------------ ПЕРИОДИЧЕСКИЙ СБРОС DAILY STATS В 23:59 ------------------

async def schedule_daily_reset():
    """
    Сбрасывает daily_stats, daily_trigger_sum и driver_income в 23:59 Europe/Minsk.
    """
    global daily_trigger_sum, driver_income, daily_stats
    while True:
        now = datetime.now(TZ)
        target = datetime.combine(now.date(), dt_time(23, 59, 0), TZ)
        if now >= target:
            target = target + timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        # Очищаем статистику и суммы
        daily_stats.clear()
        daily_trigger_sum = 0.0
        driver_income = defaultdict(float)
        # цикл продолжается

# ------------------ SCHEDULE_CHECK (проверка через 5 минут) ------------------

async def schedule_check(message_id: int):
    await asyncio.sleep(300)

    context = pending.get(message_id)
    if not context:
        return

    msg: Message = context["message"]
    reply_msg = context.get("reply")  # может быть None
    corrected = context.get("corrected", False)
    admin_sent = context.get("admin_sent", False)
    value = context.get("value", 0.0)

    # если не исправлено — понижаем рейтинг и отправляем админ-карточку (если ещё не отправляли)
    if not corrected:
        old, new = update_rating(msg.from_user.id, -0.1)
        try:
            await msg.reply("Действий не предпринято. Рейтинг понижен на 0.1!")
        except:
            pass
        if not admin_sent:
            await send_card_to_admin(msg.bot, msg, value, daily_trigger_sum)
            context["admin_sent"] = True
    else:
        if not admin_sent:
            await send_card_to_admin(msg.bot, msg, value, daily_trigger_sum)
            context["admin_sent"] = True

    # отправляем личную карточку пользователю (всегда, кроме админа и если muted)
    try:
        # При отправке личной карточки используем driver_income накопленный для этого user
        uid = msg.from_user.id
        user_total = driver_income.get(uid, 0.0)
        await send_card_to_user(msg.bot, msg, value, user_total, context.get("triggers", []))
    except:
        pass

    # удаляем pending
    pending.pop(message_id, None)

    # удаляем reply сообщение, если он был создан (через 5 минут)
    if reply_msg:
        await asyncio.sleep(300)
        try:
            await reply_msg.delete()
        except:
            pass

# ------------------ ОБРАБОТКА РЕДАКТИРОВАНИЙ ------------------

async def handle_edited_message(message: Message):
    msg_id = message.message_id

    # пересчитываем триггеры и значение по новому тексту
    new_value, new_triggers = parse_triggers_and_value(message.text or "")

    if msg_id not in pending:
        # скорректируем daily_stats, если запись есть
        adjust_daily_stats_on_edit(message, 0.0)
        return

    context = pending[msg_id]
    old_value = context.get("value", 0.0)

    if abs(new_value - old_value) > 1e-9:
        # обновляем глобальную сумму и driver income
        global daily_trigger_sum
        daily_trigger_sum += (new_value - old_value)
        context["value"] = new_value
        uid = message.from_user.id
        driver_income[uid] += (new_value - old_value)
        # обновляем запись в daily_stats
        adjust_daily_stats_on_edit(message, old_value)

    # если теперь в тексте есть '+', считаем как исправление
    if "+" in (message.text or ""):
        context["corrected"] = True
        # удаляем reply, если он был
        try:
            if context.get("reply"):
                await context["reply"].delete()
        except:
            pass

        # повышаем рейтинг за исправление
        old, new = update_rating(message.from_user.id, +0.05)

        # отправляем админ-карточку (сумма S актуальна)
        await send_card_to_admin(message.bot, message, context.get("value", 0.0), daily_trigger_sum)
        context["admin_sent"] = True

        # уведомление в тред (краткое)
        try:
            ok = await message.reply("Проверка прошла успешно. Отметка принята, рейтинг повышен на 0.05.")
            await asyncio.sleep(300)
            try:
                await ok.delete()
            except:
                pass
        except:
            pass

    # --- исправленная часть: отправляем пользователю **текущий текст** ---
    # При редактировании отправляем карточку пользователю с обновлёнными суммами
    uid = message.from_user.id
    user_total = driver_income.get(uid, 0.0)
    await send_card_to_user(message.bot, message, new_value, user_total, new_triggers)

# ------------------ PRIVATE COMMANDS (личные команды от пользователя) ------------------

async def handle_private_command(message: Message):
    text = (message.text or "").strip().lower()
    user_id = message.from_user.id

    if text.startswith("/mute"):
        muted_users.add(user_id)
        await message.reply("Личные карточки отключены. Чтобы вновь включить — отправьте /unmute.")
        return

    if text.startswith("/unmute"):
        muted_users.discard(user_id)
        await message.reply("Личные карточки включены.")
        return

    if text.startswith("/report"):
        report = build_report_for_user(user_id)
        try:
            await message.reply(report)
        except:
            await message.reply("Не удалось отправить отчёт.")
        return

    if text.startswith("/zero"):
        # доступна ТОЛЬКО TARGET_USER_ID
        if user_id != TARGET_USER_ID:
            await message.reply("Команда доступна только администратору.")
            return
        # обнуляем сумму S (daily_trigger_sum)
        global daily_trigger_sum
        daily_trigger_sum = 0.0
        await message.reply("Сумма накопленных триггеров обнулена.")
        return

    if text.startswith("/reset"):
        # локальная команда пользователя — сбрасывает только его значения
        # вычислим сумму, которую нужно вычесть из global daily_trigger_sum
        user_entries = daily_stats.get(user_id, {})
        to_subtract = 0.0
        for key, entries in user_entries.items():
            for e in entries:
                to_subtract += e.get("value", 0.0)
        # вычитаем из глобальной суммы
        global daily_trigger_sum
        daily_trigger_sum = max(0.0, daily_trigger_sum - to_subtract)
        # очищаем user stats и driver_income для этого user
        if user_id in daily_stats:
            daily_stats.pop(user_id, None)
        driver_income[user_id] = 0.0
        await message.reply("Ваши счётчики успешно обнулены.")
        return

# ------------------ ОСНОВНОЙ ХЕНДЛЕР СООБЩЕНИЙ (треды и личка) ------------------

# Создаём bot и dispatcher
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# Регистрация хендлеров ниже будет выполнена в main()

async def handle_in_thread_message(message: Message):
    # В треде (магазины)
    text = message.text or ""
    chat_id = message.chat.id
    thread_id = message.message_thread_id

    # фильтр разрешённых тредов
    if (chat_id, thread_id) not in SHOP_NAMES:
        return

    # Парсим триггеры и суммарную BYN-стоимость для этого сообщения
    per_msg_value, triggers = parse_triggers_and_value(text)

    # Обновляем глобальную сумму и daily_stats (вариант B - всегда)
    global daily_trigger_sum
    if per_msg_value:
        daily_trigger_sum += per_msg_value
        # Мы НЕ добавляем автоматически в driver_income, т.к. driver_income относится к водителям (private)
    record_message_for_daily_stats(message, per_msg_value, triggers)

    # Если сообщение сразу корректно (есть хотя бы один '+', включая в составе триггеров)
    if "+" in text:
        # повышаем рейтинг
        old, new = update_rating(message.from_user.id, +0.02)

        # создаём pending — corrected True (т.к. уже есть +)
        pending[message.message_id] = {
            "message": message,
            "reply": None,
            "corrected": True,
            "admin_sent": False,
            "value": per_msg_value,
            "triggers": triggers
        }

        # Запускаем таймер, который через 5 минут отправит личную карточку водителю и админ-карточку
        asyncio.create_task(schedule_check(message.message_id))
        return

    # Если триггера нет — даём предупреждение и ждём 5 минут
    check_time = datetime.now(TZ) + timedelta(minutes=5)
    formatted = check_time.strftime("%d.%m.%y в %H:%M")

    reply = await message.reply(
        "Отметка не принята, так как основной триггер не обнаружен. "
        "Рейтинг не изменится, если исходная отметка будет оперативно отредактирована. "
        f"Повторная проверка {formatted}."
    )

    pending[message.message_id] = {
        "message": message,
        "reply": reply,
        "corrected": False,
        "admin_sent": False,
        "value": per_msg_value,
        "triggers": triggers
    }

    asyncio.create_task(schedule_check(message.message_id))

async def handle_private_message(message: Message):
    """
    При личном сообщении (личка) — если это команда, обрабатываем в handle_private_command.
    Если это обычное сообщение от водителя — парсим триггеры; если триггеров нет — игнорируем.
    Если триггеры найдены — обновляем driver_income[user], daily_trigger_sum (global),
    записываем в daily_stats и отправляем пользователю форматированную карточку.
    """
    # команды
    if message.text and message.text.startswith("/"):
        await handle_private_command(message)
        return

    # если пользователь замьючен — не отвечаем
    user_id = message.from_user.id
    if user_id in muted_users:
        return

    text = message.text or ""
    if not text.strip():
        return

    # парсим триггеры
    per_msg_value, triggers = parse_triggers_and_value(text)
    # реагируем только если найдены триггеры (total>0)
    if per_msg_value <= 0 or not triggers:
        return

    # обновляем глобальную сумму и user income
    global daily_trigger_sum
    daily_trigger_sum += per_msg_value
    driver_income[user_id] += per_msg_value

    # сохраняем в daily_stats (заметим: для private сообщений у нас нет chat/thread; используем (0,0) ключ)
    key = (message.chat.id, message.message_thread_id)
    record_message_for_daily_stats(message, per_msg_value, triggers)

    # отправляем карточку только пользователю (в виде личного отчёта)
    user_total = driver_income.get(user_id, 0.0)
    await send_card_to_user(message.bot, message, per_msg_value, user_total, triggers)

# ------------------ РЕГИСТРАЦИЯ ХЕНДЛЕРОВ ------------------

# регистрируем отдельно: треды и личку/редактирование
dp.message.register(handle_in_thread_message)
dp.edited_message.register(handle_edited_message)
dp.message.register(handle_private_message, lambda m: m.chat.type == "private")

# ------------------ ЗАПУСК БОТА ------------------

async def main():
    # запускаем фоновую задачу для ежедневного сброса статистики
    asyncio.create_task(schedule_daily_reset())

    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
