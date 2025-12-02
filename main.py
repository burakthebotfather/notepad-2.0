import os
import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties


# ------------------ НАСТРОЙКИ ------------------

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден!")

TARGET_USER_ID = 542345855

ALLOWED_THREADS = {
    -1002360529455: 3,
    -1002079167705: 48,
    -1002936236597: 3,
    -1002423500927: 2,
    -1003117964688: 5,
    -1002864795738: 3,
    -1002535060344: 5,
    -1002477650634: 3,
    -1003204457764: 4,
    -1002660511483: 3,
    -1002538985387: 3
}

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

TRIGGER = "+"
TZ = ZoneInfo("Europe/Minsk")

# pending = { msg_id: { "message": Message, "reply": Message, "corrected": bool } }
pending = {}

# user_ratings = { user_id: float }
user_ratings = {}

# ------------------ Рейтинг ------------------

def get_rating(user_id: int) -> float:
    return user_ratings.get(user_id, 5.0)

def update_rating(user_id: int, delta: float):
    old = get_rating(user_id)
    new = max(0.0, min(5.0, old + delta))
    user_ratings[user_id] = new
    return old, new


# -------------------------------------------------
#        ФУНКЦИЯ: ПЕРЕСЫЛКА-КАРТОЧКА АДМИНУ
# -------------------------------------------------

async def send_card_to_admin(bot, user: Message, tag: str, rating_before=None, rating_after=None):
    tz_now = datetime.now(TZ).strftime("%d.%m.%y %H:%M:%S")
    username = f"@{user.from_user.username}" if user.from_user.username else "—"
    text = user.text or ""

    shop_name = SHOP_NAMES.get((user.chat.id, user.message_thread_id), "Неизвестная точка")

    rating_block = ""
    if rating_before is not None:
        rating_block = (
            f"\n⭐ <b>Рейтинг:</b>\n"
            f"до: <b>{rating_before:.2f}</b>\n"
            f"после: <b>{rating_after:.2f}</b>\n"
        )

    card = (
        f"📌 <b>{tag}</b>\n\n"
        f"🏪 <b>Точка:</b> {shop_name}\n\n"
        f"👤 <b>Пользователь:</b> {user.from_user.full_name}\n"
        f"🆔 <b>ID:</b> <code>{user.from_user.id}</code>\n"
        f"🔗 <b>Username:</b> {username}\n\n"
        f"🗨 <b>Текст сообщения:</b>\n<code>{text}</code>\n\n"
        f"📅 <b>Время сообщения:</b> {tz_now}\n"
        f"💬 <b>chat_id:</b> {user.chat.id}\n"
        f"🧵 <b>thread_id:</b> {user.message_thread_id}"
        f"{rating_block}"
    )

    await bot.send_message(TARGET_USER_ID, card)


# -------------------------------------------------
#        ПОВТОРНАЯ ПРОВЕРКА ЧЕРЕЗ 5 МИНУТ
# -------------------------------------------------

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
            await msg.reply("Действий не предпринято. Рейтинг понижен!")
        except:
            pass

        await send_card_to_admin(
            msg.bot, msg,
            "требование об исправлении проигнорировано",
            rating_before=old,
            rating_after=new
        )

    pending.pop(message_id, None)

    await asyncio.sleep(300)
    try:
        await reply_msg.delete()
    except:
        pass


# -------------------------------------------------
#        ОБРАБОТКА ИЗМЕНЁННЫХ СООБЩЕНИЙ
# -------------------------------------------------

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
            "Проверка прошла успешно. Отметка принята, рейтинг повышен."
        )

        await send_card_to_admin(
            message.bot, message,
            "корректировка произведена вовремя",
            rating_before=old,
            rating_after=new
        )

        await asyncio.sleep(300)
        try:
            await ok.delete()
        except:
            pass


# -------------------------------------------------
#        ОСНОВНОЙ ОБРАБОТЧИК СООБЩЕНИЙ
# -------------------------------------------------

async def handle_message(message: Message):
    text = message.text or ""
    chat_id = message.chat.id
    thread_id = message.message_thread_id

    if chat_id not in ALLOWED_THREADS:
        return
    if ALLOWED_THREADS[chat_id] != thread_id:
        return

    # ----- Если отметка сразу корректна -----
    if TRIGGER in text:
        old, new = update_rating(message.from_user.id, +0.02)

        await asyncio.sleep(300)
        await send_card_to_admin(
            message.bot, message,
            "ошибка исключена, отметка принята",
            rating_before=old,
            rating_after=new
        )
        return

    # ----- Триггера нет — даем 5 минут -----

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

    asyncio.create_task(schedule_check(message.message_id))


# -------------------------------------------------
#                      ЗАПУСК БОТА
# -------------------------------------------------

async def main():
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML")
    )
    dp = Dispatcher()

    dp.message.register(handle_message)
    dp.edited_message.register(handle_edited_message)

    print("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
