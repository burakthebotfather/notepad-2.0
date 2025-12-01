import os
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

TARGET_USER_ID = 542345855
ALLOWED_THREADS = {-1002360529455: 3}
TRIGGER = "+"


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    if not message or not message.text:
        return

    chat_id = message.chat_id
    thread_id = message.message_thread_id

    # Проверяем правильный чат и thread
    if chat_id not in ALLOWED_THREADS or ALLOWED_THREADS[chat_id] != thread_id:
        return

    if TRIGGER in message.text:
        # Пересылаем пользователю с TARGET_USER_ID
        await context.bot.forward_message(
            chat_id=TARGET_USER_ID,
            from_chat_id=chat_id,
            message_id=message.message_id
        )
    else:
        # Отвечаем в чате
        await message.reply_text(
            "Отметка отклонена. Причина: не обнаружен основной триггер. "
            "Пожалуйста, отправьте отметку повторно новым сообщением."
        )
        # Отправляем пользователю сообщение о отклонении
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


def main():
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("BOT_TOKEN не найден!")

    # Создаём приложение
    app = ApplicationBuilder().token(bot_token).build()

    # Хэндлер для всех текстовых сообщений
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запуск бота
    print("Бот запущен...")
    app.run_polling()


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    asyncio.run(main())
