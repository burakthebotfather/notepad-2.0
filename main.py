# main.py
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

TARGET_USER_ID = int(os.getenv("TARGET_USER_ID", "542345855"))

TZ = ZoneInfo("Europe/Minsk")

# Значения для триггеров
TRIGGER_BASE_PLUS = 2.55  # BYN for single '+'
MK_GENERIC = 2.39  # "+ мк"
GAB_VALUE = 2.89   # "габ"
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

# ------------------ ХРАНИЛИЩА ------------------

pending = {}
user_ratings = {}
muted_users = set()
daily_stats = defaultdict(lambda: defaultdict(list))
daily_trigger_sum = 0.0
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

def format_byn(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")

def escape_html(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ------------------ ТРИГГЕР-ПАРСЕР ------------------

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
    if not text:
        return 0.0, []

    used_spans = []
    triggers = []
    total = 0.0

    def span_overlaps(s, e):
        for a, b in used_spans:
            if not (e <= a or s >= b):
                return True
        return False

    # цветные мк
    for m in RE_MK_COLOR.finditer(text):
        s, e = m.span()
        if span_overlaps(s, e):
            continue
        color_raw = m.group(2).lower().replace("-", "")
        val = MK_COLOR_VALUES.get(color_raw, MK_GENERIC)
        raw_slice = text[s:e].strip()
        triggers.append({"value": val, "raw": raw_slice})
        used_spans.append((s, e))
        total += val

    # mk generic
    for m in RE_MK.finditer(text):
        s, e = m.span()
        if span_overlaps(s, e):
            continue
        raw_slice = text[s:e].strip()
        triggers.append({"value": MK_GENERIC, "raw": raw_slice})
        used_spans.append((s, e))
        total += MK_GENERIC

    # gab with multiplier
    for m in RE_GAB_MULT.finditer(text):
        s, e = m.span()
        if span_overlaps(s, e):
            continue
        mul = int(m.group(1))
        val = mul * GAB_VALUE
        raw_slice = text[s:e].strip()
        triggers.append({"value": val, "raw": raw_slice})
        used_spans.append((s, e))
        total += val

    # standalone gab
    for m in RE_GAB.finditer(text):
        s, e = m.span()
        if span_overlaps(s, e):
            continue
        raw_slice = text[s:e].strip()
        triggers.append({"value": GAB_VALUE, "raw": raw_slice})
        used_spans.append((s, e))
        total += GAB_VALUE

    # plus
    for m in RE_PLUS.finditer(text):
        s, e = m.span()
        if span_overlaps(s, e):
            continue
        raw_slice = text[s:e].strip()
        triggers.append({"value": TRIGGER_BASE_PLUS, "raw": raw_slice})
        used_spans.append((s, e))
        total += TRIGGER_BASE_PLUS

    return total, triggers

def triggers_to_strings(triggers: list) -> list[str]:
    return [ " ".join(t["raw"].split()) for t in triggers ]

# ------------------ ОТПРАВКА КАРТОЧЕК ------------------

async def send_card_to_user(bot: Bot, message: Message, per_msg_value: float, user_total: float, triggers: list):
    user_id = message.from_user.id
    if user_id == TARGET_USER_ID:
        return
    if user_id in muted_users:
        return

    shop = shop_name_for_message(message)
    text = message.text or ""

    per_msg_str = format_byn(per_msg_value)
    total_str = format_byn(user_total)
    triggers_line = ", ".join(triggers_to_strings(triggers)) if triggers else "—"

    card = (
        f"{shop} {escape_html(text)}\n"
        f"<i>{escape_html(text)}</i>\n"
        f"<b>+{per_msg_str} BYN</b> | <b>{total_str} BYN</b>\n"
        f"Обнаружены триггеры: {escape_html(triggers_line)}"
    )
    try:
        await bot.send_message(user_id, card, parse_mode="HTML")
    except Exception:
        pass

# ------------------ ОБРАБОТКА ЛИЧНЫХ СООБЩЕНИЙ ------------------

async def handle_private_message(message: Message):
    global daily_trigger_sum, driver_income

    # команды
    if message.text and message.text.startswith("/"):
        await handle_private_command(message)
        return

    user_id = message.from_user.id
    if user_id in muted_users:
        return

    text = message.text or ""
    if not text.strip():
        return

    per_msg_value, triggers = parse_triggers_and_value(text)
    if per_msg_value <= 0 or not triggers:
        return

    daily_trigger_sum += per_msg_value
    driver_income[user_id] += per_msg_value

    record_message_for_daily_stats(message, per_msg_value, triggers)

    user_total = driver_income.get(user_id, 0.0)
    await send_card_to_user(message.bot, message, per_msg_value, user_total, triggers)

# ------------------ HANDLER для /reset, /mute и др. ------------------

async def handle_private_command(message: Message):
    global daily_trigger_sum, driver_income

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
        await message.reply(report)
        return

    if text.startswith("/reset"):
        # сбрасываем только для пользователя
        user_entries = daily_stats.get(user_id, {})
        to_subtract = 0.0
        for key, entries in user_entries.items():
            for e in entries:
                to_subtract += e.get("value", 0.0)
        daily_trigger_sum = max(0.0, daily_trigger_sum - to_subtract)
        daily_stats.pop(user_id, None)
        driver_income[user_id] = 0.0
        await message.reply("Ваши счётчики успешно обнулены.")
        return

    if text.startswith("/zero"):
        if user_id != TARGET_USER_ID:
            await message.reply("Команда доступна только администратору.")
            return
        daily_trigger_sum = 0.0
        driver_income.clear()
        daily_stats.clear()
        await message.reply("Глобальные счётчики обнулены.")
        return

# ------------------ СТАТИСТИКА ------------------

def record_message_for_daily_stats(msg: Message, per_msg_value: float, triggers: list):
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

def build_report_for_user(user_id: int) -> str:
    now = datetime.now(TZ)
    date_str = now.astimezone(TZ).strftime("%d.%m.%Y")
    user_data = daily_stats.get(user_id, {})
    if not user_data:
        return f"{date_str}\n\nОтчётов за сегодня не найдено."

    lines = [f"{date_str}\n"]
    for (chat_id, thread_id), entries in user_data.items():
        shop = SHOP_NAMES.get((chat_id, thread_id), "Неизвестная точка")
        lines.append(f"{shop}:")
        for i, e in enumerate(entries, start=1):
            val = e.get("value", 0.0)
            val_str = format_byn(val)
            addr = e.get("text", "")
            lines.append(f"{i}) {addr} +{val_str} BYN")
        lines.append("")
    return "\n".join(lines)

# ------------------ ЕЖЕДНЕВНЫЙ СБРОС ------------------

async def schedule_daily_reset():
    global daily_trigger_sum, driver_income, daily_stats
    while True:
        now = datetime.now(TZ)
        target = datetime.combine(now.date(), dt_time(23, 59, 0), TZ)
        if now >= target:
            target = target + timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        daily_stats.clear()
        daily_trigger_sum = 0.0
        driver_income = defaultdict(float)

# ------------------ ЭДИТ СОБЩЕНИЙ ------------------

async def handle_edited_message(message: Message):
    global daily_trigger_sum, driver_income

    msg_id = message.message_id
    new_value, new_triggers = parse_triggers_and_value(message.text or "")

    if msg_id not in pending:
        # корректировка старых записей
        adjust_daily_stats_on_edit(message, 0.0)
        return

    context = pending[msg_id]
    old_value = context.get("value", 0.0)

    diff = new_value - old_value
    if abs(diff) > 1e-9:
        daily_trigger_sum += diff
        uid = message.from_user.id
        driver_income[uid] += diff
        context["value"] = new_value
        adjust_daily_stats_on_edit(message, old_value)

    if "+" in (message.text or ""):
        context["corrected"] = True
        if context.get("reply"):
            try:
                await context["reply"].delete()
            except:
                pass

        old, new = update_rating(message.from_user.id, +0.05)
        await send_card_to_admin(message.bot, message, context.get("value", 0.0), daily_trigger_sum)
        context["admin_sent"] = True

        try:
            ok = await message.reply("Проверка прошла успешно. Отметка принята, рейтинг повышен на 0.05.")
            await asyncio.sleep(300)
            try:
                await ok.delete()
            except:
                pass
        except:
            pass

    uid = message.from_user.id
    user_total = driver_income.get(uid, 0.0)
    await send_card_to_user(message.bot, message, new_value, user_total, new_triggers)

# ------------------ ОБРАБОТЧИКИ ДЛЯ ТРЕДОВ (MAGAZINE CHAT) ------------------

async def handle_in_thread_message(message: Message):
    global daily_trigger_sum

    text = message.text or ""
    chat_id = message.chat.id
    thread_id = message.message_thread_id

    if (chat_id, thread_id) not in SHOP_NAMES:
        return

    per_msg_value, triggers = parse_triggers_and_value(text)
    if per_msg_value:
        daily_trigger_sum += per_msg_value
    record_message_for_daily_stats(message, per_msg_value, triggers)

    if "+" in text:
        old, new = update_rating(message.from_user.id, +0.02)
        pending[message.message_id] = {
            "message": message,
            "reply": None,
            "corrected": True,
            "admin_sent": False,
            "value": per_msg_value,
            "triggers": triggers
        }
        asyncio.create_task(schedule_check(message.message_id))
        return

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

# ------------------ ФУНКЦИИ SCHEDULE_CHECK, DELETE, И СБРОС СТОРОК ------------------

async def schedule_check(message_id: int):
    await asyncio.sleep(300)

    context = pending.get(message_id)
    if not context:
        return

    msg: Message = context["message"]
    reply_msg = context.get("reply")
    corrected = context.get("corrected", False)
    admin_sent = context.get("admin_sent", False)
    value = context.get("value", 0.0)
    triggers = context.get("triggers", [])

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

    try:
        user_id = msg.from_user.id
        user_total = driver_income.get(user_id, 0.0)
        await send_card_to_user(msg.bot, msg, value, user_total, triggers)
    except:
        pass

    pending.pop(message_id, None)

    if reply_msg:
        await asyncio.sleep(300)
        try:
            await reply_msg.delete()
        except:
            pass

async def delete_messages_later(chat_id: int, message_ids: list[int], delay: int = 300):
    await asyncio.sleep(delay)
    for m_id in message_ids:
        try:
            await Bot(token=BOT_TOKEN).delete_message(chat_id=chat_id, message_id=m_id)
        except Exception:
            pass

# ------------------ ИНИЦИАЛИЗАЦИЯ И START ------------------

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

dp.message.register(handle_in_thread_message)
dp.edited_message.register(handle_edited_message)
dp.message.register(handle_private_message, lambda m: m.chat.type == "private")

async def main():
    asyncio.create_task(schedule_daily_reset())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
