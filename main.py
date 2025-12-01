import os
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.types import Message

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
    """
    Универсальный обработчик всех текстовых сообщений.
    """
    # Защита: если нет текста — игнорируем
    text = message.text or ""
    chat = message.chat
    chat_id = chat.id
    thread_id = message.message_thread_id  # None если не тема

    # Только в разрешённых чатах и тредах
    if chat_id not in ALLOWED_THREADS:
        return
    if ALLOWED_THREADS[chat_id] != thread_id:
        return

    # Если есть триггер — пересылаем админу
    if TRIGGER in text:
        try:
            await message.bot.forward_message(
                chat_id=TARGET_USER_ID,
                from_chat_id=chat_id,
                message_id=message.message_id
            )
        except Exception as e:
            # Логируем ошибку в консоль
            print("Ошибка при forward_message (trigger):", e)
        return

    # Триггер не найден — отправляем ответ в тред
    try:
        await message.reply(
            "Отметка отклонена. Причина: не обнаружен основной триггер. "
            "Пожалуйста, отправьте отметку повторно новым сообщением."
        )
    except Exception as e:
        print("Ошибка при reply:", e)

    # Пересылаем админу сообщение + пометка «Отклонено»
    try:
        await message.bot.send_message(
            chat_id=TARGET_USER_ID,
            text="❌ *Отклонено*\nСообщение пользователя:",
            parse_mode="Markdown"
        )
        await message.bot.forward_message(
            chat_id=TARGET_USER_ID,
            from_chat_id=chat_id,
            message_id=message.message_id
        )
    except Exception as e:
        print("Ошибка при пересылке админу:", e)


async def main():
    bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
    dp = Dispatcher()

    # Регистрируем обработчик всех текстовых сообщений
    dp.message.register(handle_message)

    print("Бот запускается...")
    # Запускаем polling
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
