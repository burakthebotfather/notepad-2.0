import os
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties

# --- Настройки ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден в переменных окружения!")

TARGET_USER_ID = 542345855

# Формат: {chat_id: thread_id}
ALLOWED_THREADS = {
    -1002360529455: 3
}

TRIGGER = "+"


async def handle_message(message: Message):
    text = message.text or ""
    chat_id = message.chat.id
    thread_id = message.message_thread_id

    # Только в разрешённых чатах и тредах
    if chat_id not in ALLOWED_THREADS:
        return

    if ALLOWED_THREADS[chat_id] != thread_id:
        return

    # Если есть триггер
    if TRIGGER in text:
        try:
            # ЗАДЕРЖКА 5 МИНУТ
            await asyncio.sleep(300)

            await message.bot.forward_message(
                chat_id=TARGET_USER_ID,
                from_chat_id=chat_id,
                message_id=message.message_id
            )
        except Exception as e:
            print("Ошибка при пересылке триггерного сообщения:", e)
        return

    # Если триггера нет — ответ пользователю
    try:
        await message.reply(
            "Отметка отклонена: не обнаружен основной триггер. "
            "Корректировка доступна в течение 5 минут после отправки."
        )
    except Exception as e:
        print("Ошибка при reply:", e)

    # Пересылка админу с пометкой
    try:
        await message.bot.send_message(
            chat_id=TARGET_USER_ID,
            text="❌ <b>Отклонено</b>\nСообщение пользователя:",
            parse_mode="HTML"
        )
        await message.bot.forward_message(
            chat_id=TARGET_USER_ID,
            from_chat_id=chat_id,
            message_id=message.message_id
        )
    except Exception as e:
        print("Ошибка при отправке админу:", e)


async def main():
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML")
    )

    dp = Dispatcher()
    dp.message.register(handle_message)

    print("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
