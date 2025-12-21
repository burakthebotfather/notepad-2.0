import os
import re
import asyncio
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo
from collections import defaultdict

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties

# ================== НАСТРОЙКИ ==================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден!")

TARGET_USER_ID = 542345855
TZ = ZoneInfo("Europe/Minsk")

TRIGGER_BASE_PLUS = 2.55
MK_GENERIC = 2.39
GAB_VALUE = 2.89

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

# ================== ХРАНИЛИЩА ==================

pending = {}
user_ratings = {}
muted_users = set()

daily_stats = defaultdict(lambda: defaultdict(list))
daily_trigger_sum = 0.0
income_reset_at = datetime.now(TZ)

# ================== ВСПОМОГАТЕЛЬНОЕ ==================

def format_byn(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")

def escape_html(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def shop_name_for_message(msg: Message) -> str:
    return SHOP_NAMES.get((msg.chat.id, msg.message_thread_id), "Неизвестная точка")

def get_rating(uid: int) -> float:
    return user_ratings.get(uid, 5.0)

def update_rating(uid: int, delta: float):
    user_ratings[uid] = max(0.0, min(5.0, get_rating(uid) + delta))

# ================== ПАРСЕР ==================

COLOR_RE = r"(синяя|красная|оранжевая|салатовая|коричневая|светло-?серая|розовая|темно-?серая|голубая)"
RE_MK_COLOR = re.compile(r"\+?\s*мк\.?\s*" + COLOR_RE, re.I)
RE_MK = re.compile(r"\+?\s*мк\.?\b", re.I)
RE_GAB_MULT = re.compile(r"(\d+)габ\b", re.I)
RE_GAB = re.compile(r"\bгаб\b", re.I)
RE_PLUS = re.compile(r"\+")

def parse_triggers_and_value(text: str):
    text = (text or "").lower()
    total = 0.0
    used = []

    def overlaps(s, e):
        return any(not (e <= a or s >= b) for a, b in used)

    for m in RE_MK_COLOR.finditer(text):
        s, e = m.span()
        if overlaps(s, e): continue
        color = m.group(1).replace("-", "")
        total += MK_COLOR_VALUES.get(color, MK_GENERIC)
        used.append((s, e))

    for m in RE_MK.finditer(text):
        s, e = m.span()
        if overlaps(s, e): continue
        total += MK_GENERIC
        used.append((s, e))

    for m in RE_GAB_MULT.finditer(text):
        s, e = m.span()
        if overlaps(s, e): continue
        total += int(m.group(1)) * GAB_VALUE
        used.append((s, e))

    for m in RE_GAB.finditer(text):
        s, e = m.span()
        if overlaps(s, e): continue
        total += GAB_VALUE
        used.append((s, e))

    for m in RE_PLUS.finditer(text):
        s, e = m.span()
        if overlaps(s, e): continue
        total += TRIGGER_BASE_PLUS
        used.append((s, e))

    return total

# ================== КАРТОЧКИ ==================

async def send_card_to_admin(bot, msg: Message, value: float):
    if msg.from_user.id == TARGET_USER_ID:
        return

    card = (
        f"{shop_name_for_message(msg)} +{format_byn(value)} BYN\n"
        f"{msg.from_user.username or '—'} / id {msg.from_user.id}\n"
        f"<code>{escape_html(msg.text)}</code>"
    )
    await bot.send_message(TARGET_USER_ID, card)

async def send_card_to_user(bot, msg: Message):
    uid = msg.from_user.id
    if uid == TARGET_USER_ID or uid in muted_users:
        return
    await bot.send_message(uid, f"{shop_name_for_message(msg)} <code>{escape_html(msg.text)}</code>")

# ================== ОТЛОЖЕННАЯ ПРОВЕРКА ==================

async def schedule_check(msg_id: int):
    await asyncio.sleep(300)

    ctx = pending.get(msg_id)
    if not ctx:
        return

    msg = ctx["message"]
    value = ctx["value"]

    if not ctx["corrected"]:
        update_rating(msg.from_user.id, -0.1)
        await msg.reply("Отметка не исправлена. Рейтинг понижен.")

    await send_card_to_admin(msg.bot, msg, value)
    await send_card_to_user(msg.bot, msg)

    pending.pop(msg_id, None)

# ================== HANDLERS ==================

async def handle_message(msg: Message):
    global daily_trigger_sum

    if msg.chat.type == "private":
        t = (msg.text or "").lower()
        if t == "/income" and msg.from_user.id == TARGET_USER_ID:
            await msg.reply(
                f"{format_byn(daily_trigger_sum)} BYN — накопленный доход.\n"
                f"Последнее обновление {income_reset_at.strftime('%d.%m.%Y в %H:%M:%S')}."
            )
        if t == "/zero" and msg.from_user.id == TARGET_USER_ID:
            daily_trigger_sum = 0.0
            income_reset_at = datetime.now(TZ)
            await msg.reply("Счетчик дохода обнулён.")
        return

    if (msg.chat.id, msg.message_thread_id) not in SHOP_NAMES:
        return

    value = parse_triggers_and_value(msg.text)
    daily_trigger_sum += value

    pending[msg.message_id] = {
        "message": msg,
        "value": value,
        "corrected": "+" in (msg.text or "")
    }

    asyncio.create_task(schedule_check(msg.message_id))

async def handle_edited_message(msg: Message):
    ctx = pending.get(msg.message_id)
    if not ctx:
        return

    new_value = parse_triggers_and_value(msg.text)
    diff = new_value - ctx["value"]

    global daily_trigger_sum
    daily_trigger_sum += diff

    ctx["value"] = new_value
    ctx["corrected"] = "+" in (msg.text or "")

# ================== DAILY RESET ==================

async def schedule_daily_reset():
    global daily_trigger_sum, income_reset_at
    while True:
        now = datetime.now(TZ)
        target = datetime.combine(now.date(), dt_time(23, 59), TZ)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        daily_trigger_sum = 0.0
        income_reset_at = datetime.now(TZ)

# ================== START ==================

async def main():
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()

    dp.message.register(handle_message)
    dp.edited_message.register(handle_edited_message)

    asyncio.create_task(schedule_daily_reset())

    print("Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
