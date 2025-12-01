import os
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

# ID пользователя, которому пересылаем сообщения
TARGET_USER_ID = 542345855

# Разрешённые чаты и треды
allowed_locations = {
    -1002360529455: 3
}

TRIGGER = "+"


async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка входящих сообщений."""

    message = update.effective_message
    if not message:
        return

    chat_id = message.chat_id
    thread_id = message.message_thread_id

    if chat_id not in allowed_locations or allowed_locations[chat_id] != thread_id:
        return

    text = message.text or ""

    if TRIGGER in text:
        await context.bot.forward_message(
            chat_id=TARGET_USER_ID,
            from_chat_id=chat_id,
            message_id=message.message_id
        )
        return

    # Если триггер не найден
    await message.reply_text(
        "Отметка отклонена. Причина: не обнаружен основной триггер. "
        "Пожалуйста, отправьте отметку повторно новым сообщением."
    )

    await context.bot.send_message(
        chat_id=TARGET_USER_ID,
        text="❌ *Отклонено*\nСообщение пользователя:",
        parse_mode="Markdown"
    )

    await context.bot.forward_message(
        chat_id=TARGET_USER_ID,
        from_chat_id=chat_id,
        message_id=message.message_id
    )


async def main():
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN не найден в переменных окружения Railway!")

    app = ApplicationBuilder().token(bot_token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, process_message))

    # Безопасный запуск polling
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    await app.updater.idle()


if __name__ == "__main__":
    asyncio.run(main())
