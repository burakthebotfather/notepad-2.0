import os
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

TARGET_USER_ID = 542345855

allowed_locations = {
    -1002360529455: 3
}

TRIGGER = "+"

async def process_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    message = update.effective_message
    chat_id = message.chat_id
    thread_id = message.message_thread_id

    if chat_id not in allowed_locations:
        return
    if allowed_locations[chat_id] != thread_id:
        return

    text = message.text or ""

    if TRIGGER in text:
        await context.bot.forward_message(
            chat_id=TARGET_USER_ID,
            from_chat_id=chat_id,
            message_id=message.message_id
        )
        return

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
    """Главная точка входа."""
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Безопасный запуск polling (лучший вариант для Railway)
    await application.initialize()
    await application.start()
    await application.updater.start_polling()
    await application.updater.idle()


if __name__ == "__main__":
    asyncio.run(main())
