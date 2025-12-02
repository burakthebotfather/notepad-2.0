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

# Уникальный админ (ему не приходят личные карточки)
TARGET_USER_ID = 542345855

TZ = ZoneInfo("Europe/Minsk")
TRIGGER = "+"

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

# ALLOWED_THREADS для совместимости со старой логикой
ALLOWED_THREADS = {chat: thread for (chat, thread) in SHOP_NAMES.keys()}

# ------------------ ХРАНИЛИЩА В ПАМЯТИ ------------------

# pending = { msg_id: { "message": Message, "reply": Message, "corrected": bool } }
pending = {}

# user_ratings = { user_id: float }
user_ratings = {}

# user_mute_set: пользователи, у которых выключены личные карточки
muted_users = set()

# daily_stats = { user_id: { (chat_id, thread_id): [ { 'text':..., 'ts':datetime, 'has_plus':bool } , ... ] } }
daily_stats = defaultdict(lambda: defaultdict(list))

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
    # формат: 21.11.25 11:46:25 (двухзначный год для карточки водителю)
    return dt.astimezone(TZ).strftime("%d.%m.%y %H:%M:%S")

def format_date_long(dt: datetime) -> str:
    # формат: 21.11.2025
    return dt.astimezone(TZ).strftime("%d.%m.%Y")

# ------------------ ОТПРАВКА КАРТОЧКИ АДМИНУ ------------------

async def send_card_to_admin(bot, user: Message, tag: str, rating_before=None, rating_after=None):
    tz_now = datetime.now(TZ).strftime("%d.%m.%y %H:%M:%S")
    username = f"@{user.from_user.username}" if user.from_user.username else "—"
    text = user.text or ""

    shop = shop_name_for_message(user)

    rating_block = ""
    if rating_before is not None:
        rating_block = (
            f"\n⭐ <b>Рейтинг:</b>\n"
            f"до: <b>{rating_before:.2f}</b>\n"
            f"после: <b>{rating_after:.2f}</b>\n"
        )

    card = (
        f"📌 <b>{tag}</b>\n\n"
        f"🏪 <b>Организация:</b> {shop}\n\n"
        f"👤 <b>Пользователь:</b> {user.from_user.full_name}\n"
        f"🆔 <b>ID:</b> <code>{user.from_user.id}</code>\n"
        f"🔗 <b>Username:</b> {username}\n\n"
        f"🗨 <b>Текст сообщения:</b>\n<pre>{escape_html(text)}</pre>\n\n"
        f"📅 <b>Время отметки:</b> {tz_now}\n"
        f"{rating_block}"
    )

    await bot.send_message(TARGET_USER_ID, card, parse_mode="HTML")

# ------------------ ОТПРАВКА ЛИЧНОЙ КАРТОЧКИ ВОДИТЕЛЮ ------------------

async def send_card_to_user(bot, original_msg: Message):
    user_id = original_msg.from_user.id
    if user_id == TARGET_USER_ID:
        return  # админу не шлем
    if user_id in muted_users:
        return  # пользователь заглушил карточки

    # Время отправления карточки — время написания оригинального сообщения (в thread)
    ts = original_msg.date  # aiogram message.date — UTC datetime
    ts_local = ts.astimezone(TZ)
    time_str = ts_local.strftime("%d.%m.%y %H:%M:%S")

    rating = get_rating(user_id)
    shop = shop_name_for_message(original_msg)
    text = original_msg.text or ""

    # Формируем карточку в том виде, что вы просили:
    # «21.11.25 11:46:25 [время написания сообщения в thread_id], рейтинг: 4.90 [текущий рейтинг пользователя]
    #  A. Mousse Art Bakery - Белинского, 23
    #
    #  Богдановича, 123 +»
    card = (
        f"{time_str}, рейтинг: {rating:.2f}\n"
        f"{shop}\n\n"
        f"<b>Адрес / текст:</b>\n<pre>{escape_html(text)}</pre>"
    )

    try:
        await bot.send_message(user_id, card, parse_mode="HTML")
    except Exception:
        # Игнорируем ошибки доставки (например, пользователь заблокировал бота)
        pass

# ------------------ ЭКРАНИРОВАНИЕ HTML ------------------

def escape_html(text: str) -> str:
    # простая функция для безопасного вывода в <pre> / HTML
    # заменяет & < > символы
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# ------------------ ДЕЙСТВИЯ СО СТАТИСТИКОЙ ------------------

def record_message_for_daily_stats(msg: Message):
    """Сохраняем сообщение в daily_stats по варианту B (всегда)."""
    user_id = msg.from_user.id
    key = (msg.chat.id, msg.message_thread_id)
    entry = {
        "text": msg.text or "",
        "ts": msg.date.astimezone(TZ),
        "has_plus": TRIGGER in (msg.text or "")
    }
    daily_stats[user_id][key].append(entry)

# ------------------ ПЕРЕПИСКА: ФОРМАТ ОТЧЕТА ------------------

def build_report_for_user(user_id: int) -> str:
    """Собирает и возвращает текст отчёта для /report по текущим daily_stats."""
    now = datetime.now(TZ)
    date_str = format_date_long(now)

    user_data = daily_stats.get(user_id, {})
    if not user_data:
        return f"{date_str}\n\nОтчётов за сегодня не найдено."

    lines = [f"{date_str}\n"]
    # Перебираем организации, которые есть в user_data
    report_index = 0
    for (chat_id, thread_id), entries in user_data.items():
        report_index += 1
        shop = SHOP_NAMES.get((chat_id, thread_id), "Неизвестная точка")
        total = len(entries)
        lines.append(f"Отчет {report_index}:\n{shop}\nВыполнено доставок: {total}\nСписок адресов:")
        for i, e in enumerate(entries, start=1):
            # показываем текст и добавляем '+' если в тексте был
            plus_mark = " +" if e["has_plus"] else ""
            # текст будет коротким (в <pre> внутри сообщения) — но тут мы собираем единый текст сообщения
            addr = e["text"]
            lines.append(f"{i}) {addr}{plus_mark}")
        lines.append("")  # пустая строка между отчетами

    return "\n".join(lines)

# ------------------ ТАЙМЕР ДЛЯ СБРОСА СЧЁТЧИКОВ В 23:59 ------------------

async def schedule_daily_reset():
    """Запускает цикл, который очищает daily_stats ежедневно в 23:59 Europe/Minsk."""
    while True:
        now = datetime.now(TZ)
        # следующий момент: сегодня в 23:59:00
        target = datetime.combine(now.date(), dt_time(23, 59, 0), TZ)
        if now >= target:
            # если уже после 23:59, берем завтрашний
            target = target + timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        await asyncio.sleep(wait_seconds)
        # Обновление в 23:59: очищаем daily_stats (не трогая рейтинги)
        daily_stats.clear()
        # Переходим на следующий цикл (спим снова до следующего 23:59)
        # loop continues

# ------------------ ПОВТОРНАЯ ПРОВЕРКА ЧЕРЕЗ 5 МИНУТ ------------------

async def schedule_check(message_id: int):
    await asyncio.sleep(300)

    context = pending.get(message_id)
    if not context:
        return

    msg: Message = context["message"]
    reply_msg = context["reply"]

    if not context["corrected"]:
        old, new = update_rating(msg.from_user.id, -0.1)

        try:
            await msg.reply("Действий не предпринято. Рейтинг понижен на 0.1!")
        except:
            pass

        await send_card_to_admin(
            msg.bot, msg,
            "требование об исправлении проигнорировано",
            rating_before=old,
            rating_after=new
        )

    # После 5-минутной проверки — отправляем ЛИЧНУЮ карточку водителю (всегда)
    # Отправляем даже если пользователь — админ? Нет, админу не шлём (по требованиям)
    try:
        await send_card_to_user(msg.bot, msg)
    except:
        pass

    pending.pop(message_id, None)

    await asyncio.sleep(300)
    try:
        await reply_msg.delete()
    except:
        pass

# ------------------ ОБРАБОТКА ИЗМЕНЁННЫХ СООБЩЕНИЙ ------------------

async def handle_edited_message(message: Message):
    msg_id = message.message_id

    if msg_id not in pending:
        return

    context = pending[msg_id]

    if TRIGGER in (message.text or ""):
        context["corrected"] = True

        try:
            await context["reply"].delete()
        except:
            pass

        old, new = update_rating(message.from_user.id, +0.05)

        ok = await message.reply(
            "Проверка прошла успешно. Отметка принята, рейтинг повышен на 0.05."
        )

        await send_card_to_admin(
            message.bot, message,
            "корректировка произведена вовремя",
            rating_before=old,
            rating_after=new
        )

        # Личную карточку мы уже запланировали в schedule_check — она будет отправлена после 5 минут
        await asyncio.sleep(300)
        try:
            await ok.delete()
        except:
            pass

# ------------------ ОБРАБОТЧИК КОМАНД И СООБЩЕНИЙ ОТ ПОЛЬЗОВАТЕЛЯ (ЛИЧНО) ------------------

async def handle_private_command(message: Message):
    text = (message.text or "").strip().lower()
    user_id = message.from_user.id

    if text.startswith("/mute"):
        muted_users.add(user_id)
        await message.reply("Личные карточки отключены. Чтобы вновь включить — отправьте /unmute.")
        return

    if text.startswith("/unmute"):
        if user_id in muted_users:
            muted_users.discard(user_id)
        await message.reply("Личные карточки включены.")
        return

    if text.startswith("/report"):
        report = build_report_for_user(user_id)
        # Отправляем отчёт
        try:
            await message.reply(report)
        except:
            # Иногда текст может быть большой — всё равно отправляем
            await message.reply("Не удалось отправить отчёт.")
        return

# ------------------ ОСНОВНОЙ ОБРАБОТЧИК СООБЩЕНИЙ В ТРЕДАХ ------------------

async def handle_message(message: Message):
    # Если сообщение пришло в приват — обрабатываем команды
    if message.chat.type == "private":
        # Это личка с ботом — команды /mute /unmute /report
        if message.text and message.text.startswith("/"):
            await handle_private_command(message)
        return

    text = message.text or ""
    chat_id = message.chat.id
    thread_id = message.message_thread_id

    # фильтр разрешённых тредов
    if chat_id not in ALLOWED_THREADS:
        return
    if ALLOWED_THREADS[chat_id] != thread_id:
        return

    # Сохраняем сообщение в суточную статистику всегда (вариант B)
    record_message_for_daily_stats(message)

    # Если отметка сразу корректна
    if TRIGGER in text:
        old, new = update_rating(message.from_user.id, +0.02)

        # отправляем карточку админу через 5 минут (как и раньше)
        await asyncio.sleep(300)
        await send_card_to_admin(
            message.bot, message,
            "ошибка исключена, отметка принята",
            rating_before=old,
            rating_after=new
        )

        # Личную карточку водителю тоже отправит schedule_check ниже, поэтому
        # всё же создаём тут pending-заметку чтобы schedule_check выполнил отправку ЛК.
        # Создаём фиктивный reply, чтобы код удаления работал корректно.
        reply = await message.reply("Отметка принята. Инфо отправлено админу.")
        pending[message.message_id] = {
            "message": message,
            "reply": reply,
            "corrected": True  # уже корректно
        }

        # Запускаем таймер, который позже пришлёт личную карточку и удалит reply
        asyncio.create_task(schedule_check(message.message_id))
        return

    # Если триггера нет — выдаём предупреждение и ждем 5 минут
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
        "corrected": False
    }

    # Запуск задач:
    # 1) проверка через 5 минут (schedule_check) — она также отправит личную карточку водителю
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
