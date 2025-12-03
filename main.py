import os
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
TRIGGER = "+"
TRIGGER_VALUE = 2.55  # BYN per '+'

# Разрешённые треды / соответствия названий
SHOP_NAMES = {
    (-1002079167705, 48): "A. Mousse Art Bakery - Белинского, 23",
    (-1002936236597, 3): "B. Millionroz.by - Тимирязева, 67",
    (-1002423500927, 2): "E. Flovi.Studio - Тимирязева, 65Б",
    (-1003117964688, 5): "F. Flowers Titan - Мележа, 1",
    (-1002864795738, 3): "G. Цветы Мира - Академическая, 6",
    (-1002535060344, 5): "H. Kudesnica.by - Старовиленский тракт, 10",
    (-1002477650634, 3): "I. Cvetok.by - Восточная, 41",
    (-1003204457764, 4): "J. Jungle.by - Неманская, 2",
    (-1002660511483, 3): "K. Pastel Flowers - Сурганова, 31",
    (-1002360529455, 3): "333. ТЕСТ БОТОВ - 1-й Нагатинский пр-д",
    (-1002538985387, 3): "L. Lamour.by - Кропоткина, 84"
}

ALLOWED_THREADS = {chat: thread for (chat, thread) in SHOP_NAMES.keys()}

# ------------------ ХРАНИЛИЩА В ПАМЯТИ ------------------

# pending = { msg_id: { "message": Message, "reply": Message or None, "corrected": bool, "admin_sent": bool, "plus_count": int } }
pending = {}

# user_ratings = { user_id: float }
user_ratings = {}

# user_mute_set: пользователи, у которых выключены личные карточки
muted_users = set()

# daily_stats = { user_id: { (chat_id, thread_id): [ { 'message_id':..., 'text':..., 'ts':datetime, 'plus_count':int } , ... ] } }
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

# ------------------ УТИЛИТЫ ------------------

def shop_name_for_message(msg: Message) -> str:
    return SHOP_NAMES.get((msg.chat.id, msg.message_thread_id), "Неизвестная точка")

def format_dt_for_card(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%d.%m.%y %H:%M:%S")

def format_date_long(dt: datetime) -> str:
    return dt.astimezone(TZ).strftime("%d.%m.%Y")

def format_byn(value: float) -> str:
    # Формат с двумя знаками и запятой в качестве разделителя, напр.: 2,55
    return f"{value:.2f}".replace(".", ",")

def count_pluses(text: str) -> int:
    if not text:
        return 0
    return text.count("+")

def escape_html(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

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

    # Собираем сообщение (без смайликов)
    # 1-я строка: "A. [Название точки] +2,55 BYN; S = 65,50 BYN"
    # 2-я: "username [ник]/id [ID]"
    # 3-я: full text
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

def record_message_for_daily_stats(msg: Message):
    """
    Сохраняем сообщение в daily_stats (вариант B - всегда),
    а также обновляем daily_trigger_sum.
    """
    global daily_trigger_sum
    user_id = msg.from_user.id
    key = (msg.chat.id, msg.message_thread_id)
    text = msg.text or ""
    plus_count = count_pluses(text)
    entry = {
        "message_id": msg.message_id,
        "text": text,
        "ts": msg.date.astimezone(TZ),
        "plus_count": plus_count
    }
    daily_stats[user_id][key].append(entry)

    # Обновляем глобальную сумму (в BYN)
    if plus_count:
        daily_trigger_sum += plus_count * TRIGGER_VALUE

def adjust_daily_stats_on_edit(msg: Message, old_plus_count: int):
    """
    Если сообщение отредактировали, корректируем запись и суммарную S.
    """
    global daily_trigger_sum
    user_id = msg.from_user.id
    key = (msg.chat.id, msg.message_thread_id)
    entries = daily_stats.get(user_id, {}).get(key, [])
    # Ищем запись по message_id
    for e in entries:
        if e.get("message_id") == msg.message_id:
            new_plus = count_pluses(msg.text or "")
            # скорректируем сумму
            diff = new_plus - e.get("plus_count", 0)
            if diff != 0:
                daily_trigger_sum += diff * TRIGGER_VALUE
            # обновим запись
            e["text"] = msg.text or ""
            e["ts"] = msg.date.astimezone(TZ)
            e["plus_count"] = new_plus
            return
    # если запись не найдена (маловероятно) — добавим новую
    record_message_for_daily_stats(msg)

# ------------------ ОТЧЁТ / BUILD REPORT ------------------

def build_report_for_user(user_id: int) -> str:
    """
    Формат отчёта:
    21.11.2025

    Отчет 1:
    A. Mousse Art Bakery - ...
    Выполнено доставок: 7
    Список адресов:
    1) Богдановича, 120 + [полный текст]
    ...
    """
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
            plus_mark = " +" * e.get("plus_count", 0) if e.get("plus_count", 0) else ""
            addr = e.get("text", "")
            # В отчёте показываем текст как есть (без HTML), плюс отмечаем плюсы
            lines.append(f"{i}) {addr}{plus_mark}")
        lines.append("")  # пустая строка между отчетами
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
    plus_count = context.get("plus_count", 0)

    # если не исправлено — понижаем рейтинг и отправляем админ-карточку (если ещё не отправляли)
    if not corrected:
        old, new = update_rating(msg.from_user.id, -0.1)
        try:
            await msg.reply("Действий не предпринято. Рейтинг понижен на 0.1!")
        except:
            pass
        # отправляем админ-карточку (если ещё не отправляли)
        if not admin_sent:
            per_msg_value = plus_count * TRIGGER_VALUE
            await send_card_to_admin(msg.bot, msg, per_msg_value, daily_trigger_sum)
            context["admin_sent"] = True

    else:
        # если исправлено или было корректно изначально, и админ ещё не получил карточку — отправляем
        if not admin_sent:
            per_msg_value = plus_count * TRIGGER_VALUE
            await send_card_to_admin(msg.bot, msg, per_msg_value, daily_trigger_sum)
            context["admin_sent"] = True

    # отправляем личную карточку пользователю (всегда, кроме админа и если muted)
    try:
        await send_card_to_user(msg.bot, msg)
    except:
        pass

    # удаляем pending
    pending.pop(message_id, None)

    # удаляем reply сообщение, если он был создан
    if reply_msg:
        await asyncio.sleep(300)
        try:
            await reply_msg.delete()
        except:
            pass

# ------------------ ОБРАБОТКА РЕДАКТИРОВАНИЙ ------------------

async def handle_edited_message(message: Message):
    msg_id = message.message_id
    if msg_id not in pending:
        # возможно пришло редактирование после удаления из pending — всё равно нужно скорректировать daily_stats
        # на всякий случай попытаемся подправить запись
        # стараемся найти старое значение (будем считать старым plus_count = 0 если нет записи)
        adjust_daily_stats_on_edit(message, 0)
        return

    context = pending[msg_id]
    old_plus = context.get("plus_count", 0)
    new_plus = count_pluses(message.text or "")
    if new_plus != old_plus:
        # скорректируем глобальную сумму и daily_stats запись
        adjust_daily_stats_on_edit(message, old_plus)
        context["plus_count"] = new_plus

    # если теперь в тексте есть '+', считаем как исправление
    if TRIGGER in (message.text or ""):
        # помечаем как исправленное
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
        # если админ уже получал карточку для этого сообщения — всё равно отправим новую карточку
        per_msg_value = new_plus * TRIGGER_VALUE
        await send_card_to_admin(message.bot, message, per_msg_value, daily_trigger_sum)
        context["admin_sent"] = True

        # уведомление в тред (краткое)
        try:
            ok = await message.reply("Проверка прошла успешно. Отметка принята, рейтинг повышен на 0.05.")
            # удалим уведомление через 5 минут
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

    # Сначала записываем сообщение в daily_stats (вариант B)
    record_message_for_daily_stats(message)
    # Получаем plus_count
    plus_count = count_pluses(text)

    # Если сообщение сразу корректно (есть '+'):
    if plus_count > 0:
        # Повышение рейтинга за корректную отметку
        old, new = update_rating(message.from_user.id, +0.02)

        # НЕ отправляем reply в тред (по требованию)
        # но создаём pending с reply = None, corrected = True, admin_sent False (карточка админу и личная карточка отправятся из schedule_check)
        pending[message.message_id] = {
            "message": message,
            "reply": None,
            "corrected": True,
            "admin_sent": False,
            "plus_count": plus_count
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
        "plus_count": plus_count
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
