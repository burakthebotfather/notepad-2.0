import os
import asyncio
from datetime import datetime, timedelta
import pytz

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

TRIGGER = "+"
TZ = pytz.timezone("Europe/Minsk")

# pending = { msg_id: { "message": Message, "reply": Message, "corrected": bool } }
pending = {}

# -------------------------------------------------
#        ФУНКЦИЯ: ПЕРЕСЫЛКА-КАРТОЧКА АДМИНУ
# -------------------------------------------------

async def send_card_to_admin(bot, user: Message, tag: str):
    tz_now = datetime.now(TZ).strftime("%d.%m.%y %H:%M:%S")
    username = f"@{user.from_user.username}" if user.from_user.username else "—"
    text = user.text or ""

    card = (
        f"📌 <b>{tag}</b>\n\n"
        f"👤 <b>Пользователь:</b> {user.from_user.full_name}\n"
        f"🆔 <b>ID:</b> <code>{user.from_user.id}</code>\n"
        f"🔗 <b>Username:</b> {username}\n\n"
        f"🗨 <b>Текст сообщения:</b>\n<code>{text}</code>\n\n"
        f"📅 <b>Время сообщения:</b> {tz_now}\n"
        f"💬 <b>chat_id:</b> {user.chat.id}\n"
        f"🧵 <b>thread_id:</b> {user.message_thread_id}"
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

    # пользователь НИЧЕГО не исправил
    if not context["corrected"]:
        try:
            await msg.reply("Действий не предпринято. Рейтинг понижен!")
        except:
            pass

        await send_card_to_admin(msg.bot, msg, "требование об исправлении проигнорировано")

    pending.pop(message_id, None)

    # удаляем предупреждение бота через 5 минут
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

    # Пользователь исправил и добавил '+'
    if TRIGGER in (message.text or ""):
        context["corrected"] = True

        # удаляем старое предупреждение
        try:
            await context["reply"].delete()
        except:
            pass

        ok = await message.reply(
            "Проверка прошла успешно. Отметка принята, а рейтинг сохранен."
        )

        await send_card_to_admin(message.bot, message, "корректировка произведена вовремя")

        # удаляем через 5 минут
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

    # ------------------ ЕСЛИ ОТМЕТКА СРАЗУ КОРРЕКТНА ------------------
    if TRIGGER in text:
        await asyncio.sleep(300)
        await send_card_to_admin(message.bot, message, "ошибка исключена, отметка принята")
        return

    # ------------------ ТРИГГЕРА НЕТ — ДАЁМ 5 МИНУТ ------------------

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
