import os
import re
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import defaultdict

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties


# ============================
#        НАСТРОЙКИ
# ============================

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден!")

# ADMIN user id — только ему отправляются карточки
TARGET_USER_ID = 542345855

# Минск
TZ = ZoneInfo("Europe/Minsk")

# Базовые стоимости триггеров
TRIGGER_BASE_PLUS = 2.55   # простое "+"
MK_GENERIC = 2.39          # "мк"
GAB_VALUE = 2.89           # "габ"

# Цветные мк
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

# Маппинг чатов и тредов → названия магазинов
SHOP_NAMES = {
    (-1002079167705, 48): "A.",
    (-1002936236597, 3):  "B.",
    (-1002423500927, 2):  "E.",
    (-1003117964688, 5):  "F.",
    (-1002864795738, 3):  "G.",
    (-1002535060344, 5):  "H.",
    (-1002477650634, 3):  "I.",
    (-1003204457764, 4):  "J.",
    (-1002660511483, 3):  "K.",
    (-1002360529455, 3):  "333.",
    (-1002538985387, 3):  "L."
}

ALLOWED_THREADS = {chat: thread for (chat, thread) in SHOP_NAMES.keys()}


# ============================
#       ХРАНИЛИЩА
# ============================

pending = {}
user_ratings = {}
muted_users = set()

# Статистика — по дням
daily_stats = defaultdict(lambda: defaultdict(list))

# Глобальная сумма за текущий день
daily_trigger_sum = 0.0


# ============================
#          РЕЙТИНГ
# ============================

def get_rating(user_id: int) -> float:
    return user_ratings.get(user_id, 5.0)

def update_rating(user_id: int, delta: float):
    old = get_rating(user_id)
    new = max(0.0, min(5.0, old + delta))
    user_ratings[user_id] = new
    return old, new


# ============================
#          HELPERS
# ============================

def shop_name_for_message(msg: Message) -> str:
    return SHOP_NAMES.get((msg.chat.id, msg.message_thread_id), "Неизвестная точка")

def format_byn(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")

def escape_html(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ============================
#       ПАРСЕР ТРИГГЕРОВ
# ============================

COLOR_WORDS = [
    "синяя","красная","оранжевая","салатовая","коричневая",
    "светло-?серая","розовая","темно-?серая","голубая"
]
COLOR_RE = r"(?:%s)" % "|".join(COLOR_WORDS)

RE_MK_COLOR = re.compile(r"(\+)?\s*мк\.?\s*(" + COLOR_RE + r")\b", flags=re.IGNORECASE)
RE_MK       = re.compile(r"(\+)?\s*мк\.?\b", flags=re.IGNORECASE)
RE_GAB_MULT = re.compile(r"(?<!\d)(\d+)габ\b", flags=re.IGNORECASE)
RE_GAB      = re.compile(r"(?<!\d)габ\b", flags=re.IGNORECASE)
RE_PLUS     = re.compile(r"\+")

def parse_triggers_and_value(text: str):
    """Парсит текст и возвращает общую стоимость."""
    if not text:
        return 0.0, []

    stext = text.lower()
    used = []
    total = 0.0

    def overlap(s, e):
        for a, b in used:
            if not (e <= a or s >= b):
                return True
        return False

    # Цветные мк
    for m in RE_MK_COLOR.finditer(stext):
        s, e = m.span()
        if overlap(s, e):
            continue
        color_raw = m.group(2)
        key = color_raw.replace("-", "")
        val = MK_COLOR_VALUES.get(key, MK_GENERIC)
        used.append((s, e))
        total += val

    # Обычные мк
    for m in RE_MK.finditer(stext):
        s, e = m.span()
        if overlap(s, e):
            continue
        used.append((s, e))
        total += MK_GENERIC

    # gab с множителем
    for m in RE_GAB_MULT.finditer(stext):
        s, e = m.span()
        if overlap(s, e):
            continue
        mul = int(m.group(1))
        used.append((s, e))
        total += mul * GAB_VALUE

    # одиночный gab
    for m in RE_GAB.finditer(stext):
        s, e = m.span()
        if overlap(s, e):
            continue
        used.append((s, e))
        total += GAB_VALUE

    # оставшиеся "+"
    for m in RE_PLUS.finditer(stext):
        s, e = m.span()
        if overlap(s, e):
            continue
        used.append((s, e))
        total += TRIGGER_BASE_PLUS

    return total, []


# ============================
#         КАРТОЧКИ
# ============================

async def send_card_to_admin(bot: Bot, msg: Message, value: float):
    global daily_trigger_sum

    shop = shop_name_for_message(msg)
    uname = msg.from_user.username or "—"
    uid = msg.from_user.id
    text = msg.text or ""

    card = (
        f"{shop} +{format_byn(value)} BYN; S = {format_byn(daily_trigger_sum)} BYN\n"
        f"{uname} / id {uid}\n"
        f"{escape_html(text)}"
    )
    await bot.send_message(TARGET_USER_ID, card, parse_mode="HTML")

async def send_card_to_user(bot: Bot, msg: Message):
    uid = msg.from_user.id
    if uid == TARGET_USER_ID:
        return
    if uid in muted_users:
        return

    shop = shop_name_for_message(msg)
    text = msg.text or ""

    card = f"{shop} {escape_html(text)}"
    await bot.send_message(uid, card, parse_mode="HTML")


# ============================
#    ОБРАБОТКА СООБЩЕНИЙ
# ============================

async def handle_message(message: Message):
    global daily_trigger_sum

    # Личные команды
    if message.chat.type == "private":
        await handle_private_command(message)
        return

    # Чат → тред → магазин?
    if (message.chat.id, message.message_thread_id) not in SHOP_NAMES:
        return

    text = message.text or ""
    per_value, _ = parse_triggers_and_value(text)

    if per_value:
        daily_trigger_sum += per_value

    await send_card_to_admin(message.bot, message, per_value)
    await send_card_to_user(message.bot, message)


async def handle_edited_message(message: Message):
    global daily_trigger_sum

    if (message.chat.id, message.message_thread_id) not in SHOP_NAMES:
        return

    text = message.text or ""
    per_value, _ = parse_triggers_and_value(text)

    daily_trigger_sum += per_value

    await send_card_to_admin(message.bot, message, per_value)
    await send_card_to_user(message.bot, message)


# ============================
#         КОМАНДЫ
# ============================

async def handle_private_command(message: Message):
    text = (message.text or "").strip().lower()
    uid = message.from_user.id

    # Обнуление суммы — только администратор
    if text.startswith("/zero"):
        if uid != TARGET_USER_ID:
            await message.reply("Команда доступна только администратору.")
            return
        global daily_trigger_sum
        daily_trigger_sum = 0.0
        await message.reply("Сумма триггеров обнулена.")
        return

    if text.startswith("/mute"):
        muted_users.add(uid)
        await message.reply("Личные карточки отключены. Команда /unmute включает обратно.")
        return

    if text.startswith("/unmute"):
        muted_users.discard(uid)
        await message.reply("Личные карточки включены.")
        return

    if text.startswith("/report"):
        await message.reply("Команда /report пока отключена.")
        return


# ============================
#          ЗАПУСК
# ============================

async def main():
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher()

    dp.message.register(handle_message)
    dp.edited_message.register(handle_edited_message)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
