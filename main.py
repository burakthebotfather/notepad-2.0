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

# ------------------ ХРАНИЛИЩА ------------------

pending = {}
user_ratings = {}
muted_users = set()

daily_stats = defaultdict(lambda: defaultdict(list))
daily_trigger_sum = 0.0
income_reset_at = datetime.now(TZ)

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
    return SHOP_NAMES.get((msg.chat.id, msg.message_thread_id), "Неизвестная точка")

def format_byn(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")

def escape_html(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ------------------ ПАРСЕР ------------------

COLOR_RE = r"(синяя|красная|оранжевая|салатовая|коричневая|светло-?серая|розовая|темно-?серая|голубая)"
RE_MK_COLOR = re.compile(r"\+?\s*мк\.?\s*" + COLOR_RE, re.I)
RE_MK = re.compile(r"\+?\s*мк\.?\b", re.I)
RE_GAB_MULT = re.compile(r"(\d+)габ\b", re.I)
RE_GAB = re.compile(r"\bгаб\b", re.I)
RE_PLUS = re.compile(r"\+")

def parse_triggers_and_value(text: str):
    """
    Парсер триггеров:
    - считается только часть СПРАВА от первого '+'
    - триггер 'и' дает +0.93 BYN
    - 'н' после '+' дает множитель x1.5
    - габариты и километры учитываются
    """

    raw = (text or "").lower()
    total = 0.0
    used = []
    triggers = []

    # --- ищем первый '+' ---
    idx = raw.find("+")
    if idx == -1:
        return 0.0, []

    # --- текст справа от '+', только здесь ищем триггеры ---
    work_text = raw[idx + 1 :]

    # ====================================================
    # 1) Базовый плюс
    # ====================================================
    total += TRIGGER_BASE_PLUS
    triggers.append({"type": "plus", "value": TRIGGER_BASE_PLUS})
    used.append((idx, idx + 1))

    # ====================================================
    # 2) Цветные MK (если есть)
    # ====================================================
    for m in RE_MK_COLOR.finditer(work_text):
        s, e = m.span()
        # скорректированные позиции относительно общего текста
        rs, re_ = idx + 1 + s, idx + 1 + e
        if any(not (re_ <= a or rs >= b) for a, b in used):
            continue
        color = m.group(1).replace("-", "")
        val = MK_COLOR_VALUES.get(color, MK_GENERIC)
        total += val
        used.append((rs, re_))
        triggers.append({"type": "mk_color", "value": val})

    # ====================================================
    # 3) MK без цвета
    # ====================================================
    for m in RE_MK.finditer(work_text):
        s, e = m.span()
        rs, re_ = idx + 1 + s, idx + 1 + e
        if any(not (re_ <= a or rs >= b) for a, b in used):
            continue
        total += MK_GENERIC
        used.append((rs, re_))
        triggers.append({"type": "mk", "value": MK_GENERIC})

    # ====================================================
    # 4) Габариты
    # ====================================================
    for m in RE_GAB_MULT.finditer(work_text):
        s, e = m.span()
        rs, re_ = idx + 1 + s, idx + 1 + e
        if any(not (re_ <= a or rs >= b) for a, b in used):
            continue
        mul = int(m.group(1))
        val = mul * GAB_VALUE
        total += val
        used.append((rs, re_))
        triggers.append({"type": "gab_mult", "value": val})

    for m in RE_GAB.finditer(work_text):
        s, e = m.span()
        rs, re_ = idx + 1 + s, idx + 1 + e
        if any(not (re_ <= a or rs >= b) for a, b in used):
            continue
        total += GAB_VALUE
        used.append((rs, re_))
        triggers.append({"type": "gab", "value": GAB_VALUE})

    # ====================================================
    # 5) "и" — уточнение адреса
    # ====================================================
    for m in re.finditer(r"\bи\b", work_text):
        s, e = m.span()
        rs, re_ = idx + 1 + s, idx + 1 + e
        if any(not (re_ <= a or rs >= b) for a, b in used):
            continue
        # триггер "и" стоит в work_text, учитываем
        total += 0.93
        used.append((rs, re_))
        triggers.append({"type": "info_i", "value": 0.93})

    # ====================================================
    # 6) Километры :K — целое число
    # ====================================================
    for m in re.finditer(r":\s*(\d+)", work_text):
        s, e = m.span()
        rs, re_ = idx + 1 + s, idx + 1 + e
        if any(not (re_ <= a or rs >= b) for a, b in used):
            continue
        try:
            k = int(m.group(1))
            total += float(k)
            used.append((rs, re_))
            triggers.append({"type": "kilometers", "value": float(k)})
        except:
            continue

    if re.search(r"\bн\b", work_text):
        total = total * 1.5
        triggers.append({"type": "night_multiplier", "value": 1.5})

    return total, triggers

# ------------------ КАРТОЧКИ ------------------

async def send_card_to_admin(bot, msg: Message, value: float):
    if msg.from_user.id == TARGET_USER_ID:
        return

    shop = shop_name_for_message(msg)
    username = msg.from_user.username or "—"
    uid = msg.from_user.id
    text = escape_html(msg.text or "")

    card = (
        f"{shop} +{format_byn(value)} BYN\n"
        f"{username} / id {uid}\n"
        f"<code>{text}</code>"
    )

    await bot.send_message(TARGET_USER_ID, card, parse_mode="HTML")

async def send_card_to_user(bot, msg: Message):
    uid = msg.from_user.id
    if uid == TARGET_USER_ID or uid in muted_users:
        return

    shop = shop_name_for_message(msg)
    text = escape_html(msg.text or "")
    await bot.send_message(uid, f"{shop} {text}", parse_mode="HTML")

# ------------------ DAILY RESET ------------------

async def schedule_daily_reset():
    global daily_trigger_sum, income_reset_at
    while True:
        now = datetime.now(TZ)
        target = datetime.combine(now.date(), dt_time(23, 59), TZ)
        if now >= target:
            target += timedelta(days=1)
        await asyncio.sleep((target - now).total_seconds())
        daily_stats.clear()
        daily_trigger_sum = 0.0
        income_reset_at = datetime.now(TZ)

# ------------------ SCHEDULE CHECK (5 минут) ------------------

async def schedule_check(message_id: int):
    await asyncio.sleep(300)

    ctx = pending.get(message_id)
    if not ctx:
        return

    msg = ctx["message"]
    corrected = ctx.get("corrected", False)
    value = ctx.get("value", 0.0)

    if not corrected:
        update_rating(msg.from_user.id, -0.1)
        try:
            await msg.reply("Действий не предпринято. Рейтинг понижен на 0.1!")
        except:
            pass

    await send_card_to_admin(msg.bot, msg, value)
    await send_card_to_user(msg.bot, msg)

    pending.pop(message_id, None)
# ------------------ DAILY STATS ------------------

def record_message_for_daily_stats(msg: Message, value: float, triggers: list):
    user_id = msg.from_user.id
    key = (msg.chat.id, msg.message_thread_id)
    entry = {
        "message_id": msg.message_id,
        "text": msg.text or "",
        "ts": msg.date.astimezone(TZ),
        "value": value,
        "triggers": triggers
    }
    daily_stats[user_id][key].append(entry)

def adjust_daily_stats_on_edit(msg: Message, old_value: float):
    global daily_trigger_sum
    user_id = msg.from_user.id
    key = (msg.chat.id, msg.message_thread_id)
    entries = daily_stats.get(user_id, {}).get(key, [])

    for e in entries:
        if e["message_id"] == msg.message_id:
            new_value, new_triggers = parse_triggers_and_value(msg.text or "")
            diff = new_value - e["value"]
            if abs(diff) > 1e-9:
                daily_trigger_sum += diff
            e["text"] = msg.text or ""
            e["ts"] = msg.date.astimezone(TZ)
            e["value"] = new_value
            e["triggers"] = new_triggers
            return

    new_value, new_triggers = parse_triggers_and_value(msg.text or "")
    record_message_for_daily_stats(msg, new_value, new_triggers)
    daily_trigger_sum += new_value

# ------------------ EDIT HANDLER ------------------

async def handle_edited_message(message: Message):
    msg_id = message.message_id
    new_value, new_triggers = parse_triggers_and_value(message.text or "")

    if msg_id not in pending:
        adjust_daily_stats_on_edit(message, 0.0)
        return

    ctx = pending[msg_id]
    old_value = ctx.get("value", 0.0)

    if abs(new_value - old_value) > 1e-9:
        global daily_trigger_sum
        daily_trigger_sum += (new_value - old_value)
        ctx["value"] = new_value
        adjust_daily_stats_on_edit(message, old_value)

    if "+" in (message.text or ""):
        ctx["corrected"] = True
        update_rating(message.from_user.id, +0.05)
        await send_card_to_admin(message.bot, message, ctx["value"])
        await send_card_to_user(message.bot, message)

# ------------------ PRIVATE COMMANDS ------------------

async def handle_private_command(message: Message):
    global daily_trigger_sum, income_reset_at
    text = (message.text or "").strip().lower()
    uid = message.from_user.id

    if text == "/mute":
        muted_users.add(uid)
        await message.reply("Личные карточки отключены.")
        return

    if text == "/unmute":
        muted_users.discard(uid)
        await message.reply("Личные карточки включены.")
        return

    if text == "/report":
        data = daily_stats.get(uid)
        if not data:
            await message.reply("Отчётов за сегодня нет.")
            return

        lines = []
        for (chat_id, thread_id), entries in data.items():
            shop = SHOP_NAMES.get((chat_id, thread_id), "Неизвестная точка")
            lines.append(f"{shop}: {len(entries)} доставок")
            for i, e in enumerate(entries, 1):
                lines.append(f"{i}) {e['text']} +{format_byn(e['value'])} BYN")
            lines.append("")

        await message.reply("\n".join(lines))
        return

    if text == "/income" and uid == TARGET_USER_ID:
        await message.reply(
            f"{format_byn(daily_trigger_sum)} BYN — накопленный доход.\n"
            f"Последнее обновление {income_reset_at.strftime('%d.%m.%Y в %H:%M:%S')}."
        )
        return

    if text == "/zero":
        if uid != TARGET_USER_ID:
            await message.reply("Команда доступна только администратору.")
            return
        daily_trigger_sum = 0.0
        income_reset_at = datetime.now(TZ)
        await message.reply("Счётчик дохода обнулён.")
        return

# ------------------ MAIN MESSAGE HANDLER ------------------

async def handle_message(message: Message):
    global daily_trigger_sum

    if message.chat.type == "private":
        if message.text and message.text.startswith("/"):
            await handle_private_command(message)
        return

    key = (message.chat.id, message.message_thread_id)
    if key not in SHOP_NAMES:
        return

    text = message.text or ""
    value, triggers = parse_triggers_and_value(text)

    record_message_for_daily_stats(message, value, triggers)

    if value > 0:
        daily_trigger_sum += value

    if "+" in text:
        update_rating(message.from_user.id, +0.02)
        pending[message.message_id] = {
            "message": message,
            "corrected": True,
            "value": value
        }
        asyncio.create_task(schedule_check(message.message_id))
        return

    reply = await message.reply(
        "Отметка не принята. Основной триггер не обнаружен. "
        "Отредактируйте сообщение в течение 5 минут."
    )

    pending[message.message_id] = {
        "message": message,
        "reply": reply,
        "corrected": False,
        "value": value
    }

    asyncio.create_task(schedule_check(message.message_id))

# ------------------ START ------------------

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
