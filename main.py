import asyncio
import logging
import httpx
import random
from dotenv import load_dotenv
import os
from collections import defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

HEADERS = {
    "Authorization": f"Bearer {GROQ_API_KEY}",
    "Content-Type": "application/json",
}

SHARED_MODEL = "llama-3.3-70b-versatile"

# Global xotira va sinxronizatsiya
CHAT_HISTORIES = defaultdict(list)
MAX_HISTORY_LEN = 10

SHARED_PLANS = {}
SHARED_REPLY_IDS = {}
PLAN_LOCK = asyncio.Lock()

# Bot username'larini saqlash uchun (run_all da to'ldiriladi)
BOT_USERNAMES = {}  # {bot_id: "@username"}

# ──────────────────────────────────────────────
#  Bot konfiguratsiyalari — aniq va professional
# ──────────────────────────────────────────────
BOT_CONFIGS = [
    {
        "id": "filosof",
        "token_env": "FILOSOF_TOKEN",
        "system_prompt": (
            "Sen o'zbekcha javob beradigan faylasufsan. "
            "Foydalanuvchi bergan savolga ANIQ va QISQA falsafiy nuqtai nazardan javob ber. "
            "Faqat bitta gap yoz. Hech qanday kirish so'zi, ism, teg yoki belgisiz — to'g'ridan-to'g'ri javobni yoz. "
            "Javob 1-2 jumladan oshmasin. Mavzudan chalg'ima."
        ),
    },
    {
        "id": "hazilkash",
        "token_env": "HAZILKASH_TOKEN",
        "system_prompt": (
            "Sen o'zbekcha javob beradigan hazilkash botsan. "
            "Foydalanuvchi bergan savolga ANIQ javob ber, lekin yengil va qiziqarli uslubda. "
            "Faqat bitta qisqa gap. Ism, teg yoki kirish so'zi yozma — bevosita javobni yoz. "
            "Savoldan qochma, to'g'ri javob ber."
        ),
    },
    {
        "id": "tanqidchi",
        "token_env": "TANQIDCHI_TOKEN",
        "system_prompt": (
            "Sen o'zbekcha javob beradigan tanqidchi botsan. "
            "Foydalanuvchi bergan savolga ANIQ va FAKTGA asoslangan javob ber, tanqidiy nuqtai nazardan. "
            "Faqat bitta qisqa gap. Ism, teg yoki kirish so'zi yozma — bevosita javobni yoz. "
            "Savoldan chalg'ima."
        ),
    },
    {
        "id": "optimist",
        "token_env": "OPTIMIST_TOKEN",
        "system_prompt": (
            "Sen o'zbekcha javob beradigan optimist botsan. "
            "Foydalanuvchi bergan savolga ANIQ va ijobiy uslubda javob ber. "
            "Faqat bitta qisqa gap. Ism, teg yoki kirish so'zi yozma — bevosita javobni yoz. "
            "Savoldan chalg'ima, aniq ma'lumot ber."
        ),
    },
    {
        "id": "realist",
        "token_env": "REALIST_TOKEN",
        "system_prompt": (
            "Sen o'zbekcha javob beradigan realist botsan. "
            "Foydalanuvchi bergan savolga FAQAT FAKTLARGA asoslanib, lo'nda va aniq javob ber. "
            "Faqat bitta qisqa gap. Ism, teg yoki kirish so'zi yozma — bevosita javobni yoz. "
            "Taxmin yoki fikr emas — faqat aniq ma'lumot."
        ),
    },
]


# ──────────────────────────────────────────────
#  Shaxsiy chat uchun "Guruhga qo'shing" xabari
# ──────────────────────────────────────────────
async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shaxsiy chatda guruhga qo'shish yo'riqnomasini yuboradi."""
    bot_username = (await context.bot.get_me()).username

    text = (
        "👋 Salom! Men faqat guruhlarda ishlaymen.\n\n"
        "📌 Meni guruhda ishlatish uchun:\n"
        "1️⃣ Quyidagi tugmani bosib, o'z guruhingizga meni qo'shing\n"
        "2️⃣ Meni guruhda <b>Admin</b> qilib tayinlang\n"
        "   (Xabarlarni o'qish va yuborish huquqi kerak)\n"
        "3️⃣ Guruhda xabar yozing — men javob beraman!\n\n"
        "⚠️ <b>Admin huquqi bo'lmasa ishlamayman!</b>"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "➕ Guruhga qo'shish",
            url=f"https://t.me/{bot_username}?startgroup=start&admin=post_messages+delete_messages+restrict_members"
        )],
        [InlineKeyboardButton(
            "📋 Qo'llanma ko'rish",
            callback_data="help"
        )]
    ])

    await update.message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=keyboard
    )


# ──────────────────────────────────────────────
#  Groq API — toza va aniq javob olish
# ──────────────────────────────────────────────
async def ask_groq_with_context(chat_id: int, current_bot_id: str, system_prompt: str, question: str) -> str:
    full_system = (
        system_prompt
        + "\n\nQATTIQ QOIDA: Javobingiz boshiga hech qachon '[bot_nomi]:', '**', yoki biror teg qo'ymang. "
        "Faqat javobning o'zini yozing. Javob 1-2 jumladan oshmasin."
    )

    messages = [{"role": "system", "content": full_system}]

    # Oxirgi N ta tarix qo'shish
    for msg in CHAT_HISTORIES[chat_id][-MAX_HISTORY_LEN:]:
        if msg["id"] == current_bot_id:
            messages.append({"role": "assistant", "content": msg["content"]})
        else:
            messages.append({"role": "user", "content": msg["content"]})

    # Joriy savol
    messages.append({"role": "user", "content": question})

    payload = {
        "model": SHARED_MODEL,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 100,
    }

    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(GROQ_URL, headers=HEADERS, json=payload)
            response.raise_for_status()
            data = response.json()
            answer = data["choices"][0]["message"]["content"].strip()

            # Keraksiz teglarni tozalash
            for bot_cfg in BOT_CONFIGS:
                answer = answer.replace(f"[{bot_cfg['id']}]:", "").replace(f"[{bot_cfg['id']}] ", "")
            answer = answer.strip(": \n")

            return answer
    except Exception as e:
        logger.error(f"Groq API xatosi [{current_bot_id}]: {e}")
        return ""


# ──────────────────────────────────────────────
#  Guruh xabarini qayta ishlash (asosiy handler)
# ──────────────────────────────────────────────
async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Guruhda kelgan har qanday xabarga javob beradi."""
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    msg_id = update.message.message_id
    question = update.message.text.strip()
    user_name = update.effective_user.first_name if update.effective_user else "Foydalanuvchi"

    # Qaysi bot bu ekanligini aniqlash
    current_cfg = None
    for cfg in BOT_CONFIGS:
        token = os.getenv(cfg["token_env"])
        if token and token == context.bot.token:
            current_cfg = cfg
            break

    if not current_cfg:
        return

    # Juda qisqa xabarlarni o'tkazib yuborish (1-2 ta harf)
    if len(question) < 3:
        return

    # ─── REJA TUZISH (faqat birinchi bot tuzadi) ───
    async with PLAN_LOCK:
        if msg_id not in SHARED_PLANS:
            # Tarixga savol qo'shish
            CHAT_HISTORIES[chat_id].append({"id": user_name, "content": question})

            # 1 yoki 2 ta bot tanlash
            num_bots = random.randint(1, 2)
            selected = random.sample(BOT_CONFIGS, num_bots)

            plan = {}
            plan[selected[0]["id"]] = random.uniform(2, 5)
            if num_bots > 1:
                plan[selected[1]["id"]] = random.uniform(20, 35)

            SHARED_PLANS[msg_id] = plan
            SHARED_REPLY_IDS[msg_id] = msg_id
            logger.info(f"Yangi reja: {list(plan.keys())} — savol: '{question[:40]}'")

    master_plan = SHARED_PLANS.get(msg_id, {})
    my_id = current_cfg["id"]

    # Bu bot rejada yo'q bo'lsa — jim turadi
    if my_id not in master_plan:
        return

    # Belgilangan vaqtni kutish
    allocated_delay = master_plan[my_id]
    await asyncio.sleep(allocated_delay)

    # "Yozmoqda..." ko'rsatish
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass
    await asyncio.sleep(random.uniform(1.5, 3.0))

    # Javob olish
    answer = await ask_groq_with_context(chat_id, my_id, current_cfg["system_prompt"], question)
    if not answer:
        logger.warning(f"[{my_id}] bo'sh javob qaytdi, o'tkazib yuborildi.")
        return

    # Zanjirli reply
    reply_target_id = SHARED_REPLY_IDS.get(msg_id, msg_id)

    try:
        sent_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=answer,
            reply_to_message_id=reply_target_id
        )

        # Keyingi bot shu xabarga reply qilsin
        SHARED_REPLY_IDS[msg_id] = sent_msg.message_id

        # Tarixga qo'shish
        CHAT_HISTORIES[chat_id].append({"id": my_id, "content": answer})
        if len(CHAT_HISTORIES[chat_id]) > MAX_HISTORY_LEN * 2:
            CHAT_HISTORIES[chat_id] = CHAT_HISTORIES[chat_id][-MAX_HISTORY_LEN:]

        logger.info(f"✅ [{my_id}] → msg#{reply_target_id} ga reply: '{answer[:50]}'")

    except Exception as e:
        logger.error(f"[{my_id}] xabar yuborishda xato: {e}")


# ──────────────────────────────────────────────
#  /start komandasi
# ──────────────────────────────────────────────
async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_type = update.effective_chat.type
    if chat_type == "private":
        await handle_private_message(update, context)
    else:
        await update.message.reply_text(
            "👋 Salom! Men faolman. Guruhda xabar yozing — javob beraman! 🤖"
        )


# ──────────────────────────────────────────────
#  Application Builder
# ──────────────────────────────────────────────
def build_application(config: dict):
    token = os.getenv(config["token_env"])
    if not token:
        raise ValueError(f"❌ Token topilmadi: {config['token_env']}")

    app = ApplicationBuilder().token(token).build()

    # /start komandasi
    app.add_handler(CommandHandler("start", handle_start))

    # Shaxsiy chatdagi har qanday xabar
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
        handle_private_message
    ))

    # Guruh/supergroup xabarlari
    app.add_handler(MessageHandler(
        filters.TEXT & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP) & ~filters.COMMAND,
        handle_group_message
    ))

    return app


# ──────────────────────────────────────────────
#  Barcha botlarni ishga tushirish
# ──────────────────────────────────────────────
async def run_all():
    if not GROQ_API_KEY:
        logger.error("❌ .env faylida GROQ_API_KEY topilmadi!")
        return

    # Token mavjudligini tekshirish
    active_configs = []
    for cfg in BOT_CONFIGS:
        if os.getenv(cfg["token_env"]):
            active_configs.append(cfg)
        else:
            logger.warning(f"⚠️  Token yo'q, o'tkazib yuborildi: {cfg['token_env']}")

    if not active_configs:
        logger.error("❌ Hech qanday bot tokeni topilmadi!")
        return

    apps = [build_application(cfg) for cfg in active_configs]

    for app in apps:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"]
        )

    bot_names = [cfg["id"] for cfg in active_configs]
    logger.info(f"✅ {len(apps)} ta bot muvaffaqiyatli ishga tushdi: {bot_names}")
    logger.info("📌 Guruhda xabar yozilganda botlar avtomatik javob beradi.")

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("🛑 Botlar to'xtatilmoqda...")
    finally:
        for app in apps:
            try:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
            except Exception as e:
                logger.error(f"To'xtatishda xato: {e}")
        logger.info("✅ Barcha botlar to'xtatildi.")


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_all())
