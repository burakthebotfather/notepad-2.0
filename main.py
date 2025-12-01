import os
import asyncio
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties

# --- Настройки ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден в переменных окружения!")

TARGET_USER_ID = 542345855

ALLOWED_THREADS = {
    -1002360529455: 3
}

TRIGGER = "+"

# Хранилище ожидающих сообщений:
pending = {}  # {message_id: {...}}


async def schedule_check(message_id: int):
    """Ожидание 5 минут и проверка статуса."""
    await asyncio.sleep(300)

    context = pending.get(message_id)
    if not context:
        return  # Уже обработано

    msg: Message = context["message"]
    replied_msg: Message = context["reply"]
    admin_note = context["admin_note"]
    initial_text = msg.text or ""

    # Если за время ожидания триггер так и не появился
    if TRIGGER not in initial_text:
        try:
            await msg.reply(
                "Действий не предпринято. Рейтинг понижен."
            )
        except:
            pass

        # Пересылка админу
        try:
            await msg.bot.send_message(
                chat_id=TARGET_USER_ID,
                text="⚠️ игнорирование требований оформления отметок"
            )
            await msg.bot.forward_message(
                chat_id=TARGET_USER_ID,
                from_chat_id=msg.chat.id,
                message_id=msg.message_id
            )
        except:
            pass

    # Удаляем данные
    pending.pop(message_id, None)

    # Удаляем ответы бота через 5 минут после проверки
    await asyncio.sleep(300)
    try:
        await replied_msg.delete()
    except:
        pass


async def handle_message(message: Message):
    text = message.text or ""
    chat_id = message.chat.id
    thread_id = message.message_thread_id

    # Только в разрешённых чатах и тредах
    if chat_id not in ALLOWED_THREADS:
        return

    if ALLOWED_THREADS[chat_id] != thread_id:
        return

    # Если есть триггер "+"
    if TRIGGER in text:
        # Проверка: это корректировка?
        if message.message_id in pending:
            context = pending.pop(message.message_id)
            bot_reply: Message = context["reply"]

            # Удаляем старый ответ
            try:
                await bot_reply.delete()
            except:
                pass

            # Новое уведомление пользователю
            notify = await message.reply("Изменения зафиксированы. Отметка принята!")

            # Пересылка админу
            try:
                await message.bot.send_message(
                    TARGET_USER_ID,
                    "корректировка произведена вовремя"
                )
                await message.bot.forward_message(
                    TARGET_USER_ID,
                    from_chat_id=chat_id,
                    message_id=message.message_id
                )
            except:
                pass

            # Удаление ответа бота через 5 минут
            await asyncio.sleep(300)
            try:
                await notify.delete()
            except:
                pass

        else:
            # Обычная корректная отметка — пересылка через 5 минут
            await asyncio.sleep(300)
            try:
                await message.bot.forward_message(
                    TARGET_USER_ID,
                    from_chat_id=chat_id,
                    message_id=message.message_id
                )
            except:
                pass

        return

    # === Если триггера НЕТ ===

    # Отвечаем пользователю в треде
    check_time = datetime.now() + timedelta(minutes=5)
    formatted = check_time.strftime("%d.%m.%y в %H:%M")

    reply = await message.reply(
        f"Отметка не принята, так как основной триггер не обнаружен. "
        f"Корректировка доступна в течение 5 (пяти) минут после отправки исходного сообщения. "
        f"Повторная проверка {formatted}."
    )

    # Сохраняем контекст
    pending[message.message_id] = {
        "message": message,
        "reply": reply,
        "admin_note": None
    }

    # Запускаем таймер
    asyncio.create_task(schedule_check(message.message_id))


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
