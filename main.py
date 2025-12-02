import os
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from aiogram.client.default import DefaultBotProperties

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не найден!")

TARGET_USER_ID = 542345855

ALLOWED_THREADS = {
    -1002360529455: 3
}

TRIGGER = "+"

# Храним статусы сообщений
pending_checks = {}
# Формат:
# pending_checks[(chat_id, message_id)] = {
#     "fixed": False,
#     "task": asyncio.Task,
#     "bot": Bot
# }


async def check_later(chat_id, message_id):
    """Проверка через 5 минут — было исправление или нет."""
    await asyncio.sleep(300)

    key = (chat_id, message_id)
    record = pending_checks.get(key)
    if not record:
        return

    if record["fixed"]:
        pending_checks.pop(key, None)
        return

    # Не исправлено — штраф
    try:
        bot: Bot = record["bot"]
        await bot.send_message(
            chat_id=TARGET_USER_ID,
            text="Действий не предпринято. Рейтинг понижен."
        )
    except Exception as e:
        print("Ошибка при штрафе:", e)

    pending_checks.pop(key, None)


# --- обработка новых сообщений ---
async def handle_message(message: Message):
    text = message.text or ""
    chat_id = message.chat.id
    thread_id = message.message_thread_id

    if chat_id not in ALLOWED_THREADS or ALLOWED_THREADS[chat_id] != thread_id:
        return

    # Если триггер есть — нормальное поведение
    if TRIGGER in text:
        await message.bot.forward_message(
            chat_id=TARGET_USER_ID,
            from_chat_id=chat_id,
            message_id=message.message_id
        )
        return

    # Если триггера нет — уведомление
    try:
        await message.reply(
            f"Отметка не принята, так как основной триггер не обнаружен. "
        f"Корректировка доступна в течение 5 (пяти) минут после отправки исходного сообщения. "
        f"Повторная проверка {formatted}."
            
        )
    except:
        pass

    # Пересылка админу
    try:
        await message.bot.send_message(
            chat_id=TARGET_USER_ID,
            text="⚠️ <b>Отклонено</b>\nСообщение пользователя:",
            parse_mode="HTML"
        )
        await message.bot.forward_message(
            chat_id=TARGET_USER_ID,
            from_chat_id=chat_id,
            message_id=message.message_id
        )
    except:
        pass

    # Создаём проверку
    key = (chat_id, message.message_id)

    if key in pending_checks:
        pending_checks[key]["task"].cancel()

    task = asyncio.create_task(check_later(chat_id, message.message_id))
    pending_checks[key] = {
        "fixed": False,
        "task": task,
        "bot": message.bot
    }


# --- обработка редактирования сообщений ---
async def handle_edit(message: Message):
    text = message.text or ""
    chat_id = message.chat.id
    message_id = message.message_id

    key = (chat_id, message_id)

    # Проверяем, есть ли это сообщение в списке "ожидающих исправления"
    if key not in pending_checks:
        return

    # Если в отредактированном сообщении появился TRIGGER — считаем исправленным
    if TRIGGER in text:
        pending_checks[key]["fixed"] = True

        try:
            await message.bot.forward_message(
                chat_id=TARGET_USER_ID,
                from_chat_id=chat_id,
                message_id=message_id
            )
        except:
            pass


async def main():
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode="HTML")
    )
    dp = Dispatcher()

    dp.message.register(handle_message, F.content_type == "text")
    dp.edited_message.register(handle_edit)

    print("Бот запущен...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
