import os
import sqlite3
import logging
import random
import asyncio
from flask import Flask, request
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

# ================= Logging =================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ================= Config =================
TOKEN = os.getenv("BOT_TOKEN")            # Render → Environment → BOT_TOKEN
WEBHOOK_URL = os.getenv("WEBHOOK_URL")    # Render → Environment → WEBHOOK_URL (https://your-app.onrender.com)
PORT = int(os.getenv("PORT", 5000))

if not TOKEN:
    raise RuntimeError("BOT_TOKEN env var is required")
if not WEBHOOK_URL:
    raise RuntimeError("WEBHOOK_URL env var is required")

# ================= Flask (for Render) =================
flask_app = Flask(__name__)

# ================= DB =================
DB_FILE = "bot.db"

# --- CONVERSATION STATES ---
ADD_EN, ADD_UA = range(2)
QUIZ, QUIZ_INPUT = range(2)


def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Words table
    c.execute(
        """CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            en TEXT,
            ua TEXT
        )"""
    )
    # Stats table
    c.execute(
        """CREATE TABLE IF NOT EXISTS stats (
            key TEXT PRIMARY KEY,
            value INTEGER
        )"""
    )
    # Users table
    c.execute(
        """CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            direction TEXT DEFAULT 'EN-UA'
        )"""
    )
    # Init stats
    for key in ["words_added", "correct_answers", "total_answers"]:
        c.execute("INSERT OR IGNORE INTO stats (key, value) VALUES (?, ?)", (key, 0))
    conn.commit()
    conn.close()


def main_menu():
    return ReplyKeyboardMarkup(
        [
            ["➕ Додати слово", "➖ Видалити слово"],
            ["📚 Показати всі слова", "📝 Вікторина"],
            ["📊 Статистика", "⚙️ Налаштування"],
        ],
        resize_keyboard=True,
    )


# ================= Handlers =================
# --- Add word ---
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


# --- Delete word ---
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
        [InlineKeyboardButton(f"{en} → {ua}", callback_data=f"del_{word_id}")]
        for word_id, en, ua in words
    ]
    await update.message.reply_text(
        "Виберіть слово для видалення:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


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


# --- Show all words ---
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


# --- Quiz ---
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
    correct = context.user_data.get("quiz_answer")

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
    correct = context.user_data.get("quiz_answer", "")

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


# --- Settings ---
async def settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🗑 Очистити слова", callback_data="clear_words")],
        [InlineKeyboardButton("📉 Очистити статистику", callback_data="clear_stats")],
        [InlineKeyboardButton("🔄 Змінити напрямок вікторини", callback_data="change_direction")],
    ]
    await update.message.reply_text(
        "⚙️ Налаштування:", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def settings_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    if query.data == "clear_words":
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM words")
        conn.commit()
        conn.close()
        await query.edit_message_text("✅ Список слів очищено.")

    elif query.data == "clear_stats":
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        for key in ["correct_answers", "total_answers"]:
            c.execute("UPDATE stats SET value = 0 WHERE key=?", (key,))
        conn.commit()
        conn.close()
        await query.edit_message_text("✅ Статистика очищена.")

    elif query.data == "change_direction":
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT direction FROM users WHERE user_id=?", (user_id,))
        row = c.fetchone()
        new_dir = "UA-EN" if row and row[0] == "EN-UA" else "EN-UA"
        c.execute(
            "INSERT OR REPLACE INTO users (user_id, direction) VALUES (?, ?)",
            (user_id, new_dir),
        )
        conn.commit()
        conn.close()
        await query.edit_message_text(f"✅ Напрямок змінено на {new_dir}")


# --- Stats ---
async def show_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT value FROM stats WHERE key='words_added'")
    words_added = c.fetchone()[0]
    c.execute("SELECT value FROM stats WHERE key='correct_answers'")
    correct = c.fetchone()[0]
    c.execute("SELECT value FROM stats WHERE key='total_answers'")
    total = c.fetchone()[0]
    conn.close()

    percent = round((correct / total * 100), 2) if total > 0 else 0
    await update.message.reply_text(
        f"📊 Статистика:\n"
        f"Доданих слів: {words_added}\n"
        f"Правильних відповідей: {correct}\n"
        f"Всього відповідей: {total}\n"
        f"Відсоток правильних: {percent}%"
    )


# --- Start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

    await update.message.reply_text("👋 Вітаю! Оберіть дію:", reply_markup=main_menu())


# ================= Build Telegram Application =================
application = Application.builder().token(TOKEN).build()

# Conversations
conv_add = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^➕ Додати слово$"), add_word_start)],
    states={
        ADD_EN: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_word_en)],
        ADD_UA: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_word_ua)],
    },
    fallbacks=[],
)

conv_quiz = ConversationHandler(
    entry_points=[MessageHandler(filters.Regex("^📝 Вікторина$"), quiz_start)],
    states={
        QUIZ: [MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_question)],
        QUIZ_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, quiz_input)],
    },
    fallbacks=[],
)

# Register handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(conv_add)
application.add_handler(conv_quiz)
application.add_handler(MessageHandler(filters.Regex("^➖ Видалити слово$"), delete_word))
application.add_handler(CallbackQueryHandler(delete_word_confirm, pattern="^del_"))
application.add_handler(MessageHandler(filters.Regex("^📚 Показати всі слова$"), show_words))
application.add_handler(MessageHandler(filters.Regex("^📊 Статистика$"), show_stats))
application.add_handler(MessageHandler(filters.Regex("^⚙️ Налаштування$"), settings))
application.add_handler(CallbackQueryHandler(settings_handler, pattern="^(clear_words|clear_stats|change_direction)$"))
application.add_handler(CallbackQueryHandler(quiz_answer))
# Fallback: return to menu on any other text
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, start))


# ================= Flask Webhook =================
@flask_app.route("/webhook", methods=["POST"])
def webhook():
    update = Update.de_json(request.get_json(force=True), application.bot)
    asyncio.run(application.process_update(update))   # ← Виправлено
    return "ok", 200


if __name__ == "__main__":
    init_db()

    async def bootstrap():
        await application.initialize()
        await application.start()
        await application.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
        logger.info("Webhook set to %s/webhook", WEBHOOK_URL)

    asyncio.get_event_loop().run_until_complete(bootstrap())

    # Run Flask
    flask_app.run(host="0.0.0.0", port=PORT)

