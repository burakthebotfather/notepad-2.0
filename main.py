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
TARGET_USER_ID = 542345855

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

# daily_trigger_sum: сумма в BYN за текущие сутки (всех триггеров)
daily_trigger_sum = 0.0

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

def format_dt_for_card(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%d.%m.%y %H:%M:%S")

def format_date_long(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%d.%m.%Y")

def format_byn(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")

def escape_html(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ------------------ ТРИГГЕР-ПАРСЕР ------------------
# Алгоритм:
# 1) ищем цветные "мк" (с возможным ведущим '+')
# 2) ищем "мк" без цвета
# 3) ищем слитные множители для "габ" (\d+габ) и затем одиночные "габ"
# 4) учитываем не попавшие в предыдущие элементы одиночные символы '+'
# При всех совпадениях: суммируем значение в BYN и возвращаем список найденных триггеров для записи.

# Компилируем регулярки:
COLOR_WORDS = [
    "синяя","красная","оранжевая","салатовая","коричневая",
    "светло-?серая","розовая","темно-?серая","голубая"
]
COLOR_RE = r"(?:%s)" % "|".join(COLOR_WORDS)
# ищем варианты типа "+ мк синяя", "+мк синяя", "мк синяя" (предпочитаем наличие 'мк')
RE_MK_COLOR = re.compile(r"(\+)?\s*мк\.?\s*(" + COLOR_RE + r")\b", flags=re.IGNORECASE)
# общие "мк" без цвета: "+ мк", "+мк", "мк", "мк."
RE_MK = re.compile(r"(\+)?\s*мк\.?\b", flags=re.IGNORECASE)
# множитель для габ: "3габ" (только слипшийся), либо "+3габ" или "3габ"
RE_GAB_MULT = re.compile(r"(?<!\d)(\d+)габ\b", flags=re.IGNORECASE)
# одиночный "габ"
RE_GAB = re.compile(r"(?<!\d)габ\b", flags=re.IGNORECASE)
# одиночные плюсы
RE_PLUS = re.compile(r"\+")

def parse_triggers_and_value(text: str):
    """
    Возвращает tuple (total_value_byn: float, triggers_list: list of dict)
    triggers_list содержит элементы вида:
      {"type": "mk_color", "color": "синяя", "span": (s,e), "value": 4.05}
    """
    global MK_COLOR_VALUES, MK_GENERIC, GAB_VALUE, TRIGGER_BASE_PLUS

    if not text:
        return 0.0, []

    stext = text.lower()
    used_spans = []
    triggers = []
    total = 0.0

    def span_overlaps(s, e):
        for a, b in used_spans:
            if not (e <= a or s >= b):
                return True
        return False

    # 1) цветные мк
    for m in RE_MK_COLOR.finditer(stext):
        s, e = m.span()
        if span_overlaps(s, e):
            continue
        color_raw = m.group(2)
        # normalize color (remove dash)
        color_norm = color_raw.replace("-", "")
        color_norm = color_norm.replace("́", "")  # in case
        # match key in mapping: allow both with and without hyphen forms
        # try direct, then replace hyphen
        val = None
        # try exact raw
        key = color_raw.replace("-", "")
        # find mapping by iterating keys lowercased and removing hyphens
        for k, v in MK_COLOR_VALUES.items():
            if k.replace("-", "") == key:
                val = v
                break
            if k == color_raw:
                val = v
                break
        if val is None:
            # fallback to generic mk
            val = MK_GENERIC
        triggers.append({"type": "mk_color", "color": color_raw, "span": (s, e), "value": val})
        used_spans.append((s, e))
        total += val

    # 2) mk generic (non-colored)
    for m in RE_MK.finditer(stext):
        s, e = m.span()
        # skip if overlaps colored matches
        if span_overlaps(s, e):
            continue
        # ensure it's not part of other word (regex already uses boundary)
        triggers.append({"type": "mk", "span": (s, e), "value": MK_GENERIC})
        used_spans.append((s, e))
        total += MK_GENERIC

    # 3) gab with multiplier (only concatenated, like "3габ")
    for m in RE_GAB_MULT.finditer(stext):
        s, e = m.span()
        if span_overlaps(s, e):
            continue
        mul = int(m.group(1))
        val = mul * GAB_VALUE
        triggers.append({"type": "gab_mult", "mult": mul, "span": (s, e), "value": val})
        used_spans.append((s, e))
        total += val

    # 4) standalone "габ"
    for m in RE_GAB.finditer(stext):
        s, e = m.span()
        if span_overlaps(s, e):
            continue
        triggers.append({"type": "gab", "span": (s, e), "value": GAB_VALUE})
        used_spans.append((s, e))
        total += GAB_VALUE

    # 5) remaining plus signs not covered by above spans
    for m in RE_PLUS.finditer(stext):
        s, e = m.span()
        if span_overlaps(s, e):
            continue
        # count this plus as base
        triggers.append({"type": "plus", "span": (s, e), "value": TRIGGER_BASE_PLUS})
        used_spans.append((s, e))
        total += TRIGGER_BASE_PLUS

    return total, triggers

# ------------------ СТРОКИ КАРТОЧЕК ------------------

async def send_card_to_admin(bot, original_msg: Message, per_msg_value: float, s_total: float):
    """
    Отправляет специальную карточку ТОЛЬКО TARGET_USER_ID в требуемом формате (без смайликов):
    A. [Название точки] +2,55 BYN; S = 65,50 BYN
    username [ник]/id [ID]
    ул. Богдановича, 123 + [полный текст сообщения]
    """
    if TARGET_USER_ID is None:
        return

    shop = shop_name_for_message(original_msg)
    username = original_msg.from_user.username or "—"
    uid = original_msg.from_user.id
    text = original_msg.text or ""

    per_msg_str = format_byn(per_msg_value)
    s_total_str = format_byn(s_total)

    card_lines = [
        f"{shop} +{per_msg_str} BYN; S = {s_total_str} BYN",
        f"{username} / id {uid}",
        f"{escape_html(text)}"
    ]
    card = "\n".join(card_lines)

    try:
        await bot.send_message(TARGET_USER_ID, card, parse_mode="HTML")
    except Exception:
        pass

async def send_card_to_user(bot, original_msg: Message):
    """
    Отправляет карточку обычному пользователю:
    "A.[Название точки] [полный текст сообщения]"
    Не отправляет TARGET_USER_ID и пользователей из muted_users.
    """
    user_id = original_msg.from_user.id
    if user_id == TARGET_USER_ID:
        return
    if user_id in muted_users:
        return

    shop = shop_name_for_message(original_msg)
    text = original_msg.text or ""

    card = f"{shop} {escape_html(text)}"
    try:
        await bot.send_message(user_id, card, parse_mode="HTML")
    except Exception:
        pass

# ------------------ СТАТИСТИКА / ЗАПИСЬ СООБЩЕНИЯ ------------------

def record_message_for_daily_stats(msg: Message, per_msg_value: float, triggers: list):
    """
    Сохраняем сообщение в daily_stats (вариант B - всегда).
    Сохраняем value (BYN) и список триггеров для возможной отладки/отчёта.
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
    old_value — предыдущее BYN значение для этого сообщения (если известно).
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
            # show text, and also append a short marker with BYN for that message
            val = e.get("value", 0.0)
            val_str = format_byn(val)
            addr = e.get("text", "")
            lines.append(f"{i}) {addr} +{val_str} BYN")
        lines.append("")
    return "\n".join(lines)

# ------------------ ПЕРИОДИЧЕСКИЙ СБРОС DAILY STATS В 23:59 ------------------

async def schedule_daily_reset():
    """
    Сбрасывает daily_stats и суммарную daily_trigger_sum в 23:59 Europe/Minsk.
    """
    global daily_trigger_sum
    while True:
        now = datetime.now(TZ)
        target = datetime.combine(now.date(), dt_time(23, 59, 0), TZ)
        if now >= target:
            target = target + timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        # Очищаем статистику и сумму (рейтинги при этом не трогаем)
        daily_stats.clear()
        daily_trigger_sum = 0.0
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
        await send_card_to_user(msg.bot, msg)
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
    # recompute triggers and value
    new_value, new_triggers = parse_triggers_and_value(message.text or "")

    if msg_id not in pending:
        # скорректируем daily_stats (если запись есть)
        adjust_daily_stats_on_edit(message, 0.0)
        return

    context = pending[msg_id]
    old_value = context.get("value", 0.0)
    if abs(new_value - old_value) > 1e-9:
        # обновляем глобальную сумму
        global daily_trigger_sum
        daily_trigger_sum += (new_value - old_value)
        context["value"] = new_value
        # и обновим запись в daily_stats
        adjust_daily_stats_on_edit(message, old_value)

    # если теперь в тексте есть '+', считаем как исправление
    if "+" in (message.text or ""):
        context["corrected"] = True
        # если был reply — удалим
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

# ------------------ ОСНОВНОЙ ХЕНДЛЕР СООБЩЕНИЙ (треды и личка) ------------------

async def handle_message(message: Message):
    # Если в личке — команды
    if message.chat.type == "private":
        if message.text and message.text.startswith("/"):
            await handle_private_command(message)
        return

    # В треде
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
    record_message_for_daily_stats(message, per_msg_value, triggers)

    # Если сообщение сразу корректно (есть хотя бы один '+', включая в составе триггеров)
    # определяем как наличие символа '+' в тексте
    if "+" in text:
        # повышаем рейтинг
        old, new = update_rating(message.from_user.id, +0.02)

        # НЕ отправляем reply в тред (по требованию)
        # создаём pending с reply = None, corrected = True, admin_sent False
        pending[message.message_id] = {
            "message": message,
            "reply": None,
            "corrected": True,
            "admin_sent": False,
            "value": per_msg_value
        }

        # Запускаем таймер, который через 5 минут отправит личную карточку водителю и админ-карточку
        asyncio.create_task(schedule_check(message.message_id))
        return

    # Если триггера нет — выдаём предупреждение и ждём 5 минут
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
        "value": per_msg_value
    }

    asyncio.create_task(schedule_check(message.message_id))

# ------------------ ЗАПУСК БОТА ------------------

async def main():
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML")
    )
    dp = Dispatcher()

    dp.message.register(handle_message)
    dp.edited_message.register(handle_edited_message)

    # запуск фоновой задачи для ежедневного сброса статистики
    asyncio.create_task(schedule_daily_reset())

    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
