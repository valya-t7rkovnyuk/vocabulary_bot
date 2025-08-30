import logging
import sqlite3
import random
from datetime import time
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

# --- ЛОГІ ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

DB_FILE = "bot.db"

# --- СТАНИ ДЛЯ КОНВЕРСАЦІЙ ---
ADD_EN, ADD_UA = range(2)
QUIZ, QUIZ_INPUT = range(2)
SET_TIME = 1

# --- ІНІЦІАЛІЗАЦІЯ БАЗИ ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Таблиця слів
    c.execute(
        """CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            en TEXT,
            ua TEXT
        )"""
    )

    # Таблиця статистики
    c.execute(
        """CREATE TABLE IF NOT EXISTS stats (
            key TEXT PRIMARY KEY,
            value INTEGER
        )"""
    )

    # Таблиця користувачів
    c.execute(
        """CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            direction TEXT DEFAULT 'EN-UA',
            daily_time TEXT DEFAULT '10:00'
        )"""
    )

    # Ініціалізація статистики
    for key in ["words_added", "correct_answers", "total_answers"]:
        c.execute("INSERT OR IGNORE INTO stats (key, value) VALUES (?, ?)", (key, 0))

    conn.commit()
    conn.close()


# --- ГОЛОВНЕ МЕНЮ ---
def main_menu():
    return ReplyKeyboardMarkup(
        [
            ["➕ Додати слово", "➖ Видалити слово"],
            ["📚 Показати всі слова", "📝 Вікторина"],
            ["📊 Статистика", "⚙️ Налаштування"],
        ],
        resize_keyboard=True,
    )


# --- ДОБАВИТИ СЛОВО ---
async def add_word_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Введіть слово англійською:")
    return ADD_EN


async def add_word_en(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["en"] = update.message.text.strip()
    await update.message.reply_text("Введіть переклад українською:")
    return ADD_UA


async def add_word_ua(update: Update, context: ContextTypes.DEFAULT_TYPE):
    en = context.user_data["en"]
    ua = update.message.text.strip()

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO words (en, ua) VALUES (?, ?)", (en, ua))
    c.execute("UPDATE stats SET value = value + 1 WHERE key='words_added'")
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ Слово додано: {en} → {ua}", reply_markup=main_menu())
    return ConversationHandler.END


# --- ВИДАЛЕННЯ ---
async def delete_word(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, en, ua FROM words")
    words = c.fetchall()
    conn.close()

    if not words:
        await update.message.reply_text("Список порожній.")
        return

    keyboard = [
        [InlineKeyboardButton(f"{en} → {ua}", callback_data=f"del_{word_id}")] for word_id, en, ua in words
    ]
    await update.message.reply_text("Виберіть слово для видалення:", reply_markup=InlineKeyboardMarkup(keyboard))


async def delete_word_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    word_id = int(query.data.split("_")[1])

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("DELETE FROM words WHERE id=?", (word_id,))
    conn.commit()
    conn.close()

    await query.edit_message_text("✅ Слово видалено.")


# --- ПОКАЗАТИ ВСІ СЛОВА ---
async def show_words(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT en, ua FROM words")
    words = c.fetchall()
    conn.close()

    if not words:
        await update.message.reply_text("Список слів порожній.")
        return

    text = "\n".join([f"{en} → {ua}" for en, ua in words])
    await update.message.reply_text("📚 Ваш словник:\n\n" + text)


# --- ВІКТОРИНА ---
async def quiz_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT en, ua FROM words")
    words = c.fetchall()
    conn.close()

    if len(words) < 2:
        await update.message.reply_text("Недостатньо слів для вікторини (мін. 2).")
        return ConversationHandler.END

    user_id = update.effective_user.id
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT direction FROM users WHERE user_id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    direction = row[0] if row else "EN-UA"

    context.user_data["quiz_direction"] = direction
    context.user_data["quiz_words"] = words

    await update.message.reply_text(
        "Виберіть рівень складності:\n1️⃣ Легкий (2 варіанти)\n2️⃣ Середній (4 варіанти)\n3️⃣ Важкий (введення вручну)"
    )
    return QUIZ


async def quiz_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    level = update.message.text.strip()
    if level not in ["1", "2", "3"]:
        await update.message.reply_text("Оберіть 1, 2 або 3.")
        return QUIZ

    context.user_data["quiz_level"] = int(level)

    words = context.user_data["quiz_words"]
    word = random.choice(words)
    context.user_data["quiz_word"] = word

    en, ua = word
    direction = context.user_data["quiz_direction"]

    if direction == "EN-UA":
        question, answer = en, ua
    else:
        question, answer = ua, en

    context.user_data["quiz_answer"] = answer

    if int(level) in [1, 2]:
        options = [answer]
        while len(options) < (2 if level == "1" else 4):
            opt = random.choice(words)
            opt = opt[1] if direction == "EN-UA" else opt[0]
            if opt not in options:
                options.append(opt)
        random.shuffle(options)

        keyboard = [[InlineKeyboardButton(opt, callback_data=opt)] for opt in options]
        await update.message.reply_text(
            f"🔎 Перекладіть: {question}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
    else:
        await update.message.reply_text(f"🔎 Перекладіть: {question}")
        return QUIZ_INPUT

    return ConversationHandler.END


async def quiz_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    answer = query.data
    correct = context.user_data["quiz_answer"]

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE stats SET value = value + 1 WHERE key='total_answers'")
    if answer == correct:
        c.execute("UPDATE stats SET value = value + 1 WHERE key='correct_answers'")
        conn.commit()
        conn.close()
        await query.edit_message_text(f"✅ Правильно! {correct}")
    else:
        conn.commit()
        conn.close()
        await query.edit_message_text(f"❌ Неправильно. Правильна відповідь: {correct}")


async def quiz_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = update.message.text.strip()
    correct = context.user_data["quiz_answer"]

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE stats SET value = value + 1 WHERE key='total_answers'")
    if answer.lower() == correct.lower():
        c.execute("UPDATE stats SET value = value + 1 WHERE key='correct_answers'")
        conn.commit()
        conn.close()
        await update.message.reply_text(f"✅ Правильно! {correct}", reply_markup=main_menu())
    else:
        conn.commit()
        conn.close()
        await update.message.reply_text(
            f"❌ Неправильно. Правильна відповідь: {correct}", reply_markup=main_menu()
        )
    return ConversationHandler.END


# --- СЛОВО ДНЯ ---
async def send_daily_word(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT user_id, daily_time FROM users")
    users = c.fetchall()
    conn.close()

    for user_id, daily_time in users:
        now = time.today()
        target_time = time.fromisoformat(daily_time)

        if now >= target_time:
            await context.bot.send_message(user_id, "🔔 Ваше слово дня!")

            # Відправити випадкове слово
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("SELECT en, ua FROM words ORDER BY RANDOM() LIMIT 1")
            word = c.fetchone()
            conn.close()

            if word:
                en, ua = word
                await context.bot.send_message(user_id, f"{en} → {ua}")


# --- ГОЛОВНА ФУНКЦІЯ ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Вітаю! Я ваш віртуальний помічник.", reply_markup=main_menu())


def main():
    # Ініціалізація БД
    init_db()

    application = Application.builder().token("8246569607:AAEaLgo6bLYTUV3oq98mRrXWn58XWKbJT48").build()

    # Старт /help команда
    application.add_handler(CommandHandler("start", start))

    # Додавання слова
    add_word_handler = ConversationHandler(
        entry_points=[CommandHandler("add", add_word_start)],
        states={
            ADD_EN: [MessageHandler(filters.TEXT, add_word_en)],
            ADD_UA: [MessageHandler(filters.TEXT, add_word_ua)],
        },
        fallbacks=[],
    )
    application.add_handler(add_word_handler)

    # Вікторина
    quiz_handler = ConversationHandler(
        entry_points=[CommandHandler("quiz", quiz_start)],
        states={
            QUIZ: [MessageHandler(filters.TEXT, quiz_question)],
            QUIZ_INPUT: [MessageHandler(filters.TEXT, quiz_input)],
        },
        fallbacks=[CallbackQueryHandler(quiz_answer)],
    )
    application.add_handler(quiz_handler)

    # Показати слова
    application.add_handler(CommandHandler("show", show_words))

    # Видалити слово
    application.add_handler(CommandHandler("delete", delete_word))

    # Статистика
    application.add_handler(CommandHandler("stats", show_stats))

    # Щоденне повідомлення
    application.job_queue.run_daily(send_daily_word, time=time(10, 0))

    application.run_polling()


if __name__ == "__main__":
    main()
