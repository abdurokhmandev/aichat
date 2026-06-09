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
    CallbackQueryHandler,
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

CHAT_HISTORIES = defaultdict(list)
MAX_HISTORY_LEN = 20

SHARED_PLANS = {}
SHARED_REPLY_IDS = {}
PLAN_LOCK = asyncio.Lock()

# ──────────────────────────────────────────────
#  Bot konfiguratsiyalari
# ──────────────────────────────────────────────
BOT_CONFIGS = [
    {
        "id": "filosof",
        "name": "🧐 Filosof",
        "token_env": "FILOSOF_TOKEN",
        "keywords": ["filosof", "faylasuf", "falsafa", "faylasufsan"],
        "system_prompt": (
            "Sen o'zbekcha javob beradigan chuqur fikrli faylasufsan. "
            "SAVOL BERILSA: Unga to'g'ridan-to'g'ri, aniq falsafiy nuqtai nazardan javob ber. Savolni yana savol bilan qaytarma. "
            "BOT JAVOB BERSA: O'sha botning gapiriga QARSHI chiq yoki uni kengaytir, lekin o'z pozitsiyangda tur. "
            "Ohanging bosiq, hikmatli. Metaforalar ishlat. "
            "QOIDA: 1-2 ta qisqa jumla. Hech qachon 'bu savol...' yoki 'bu masala...' deb boshlama. To'g'ridan javob ber."
        ),
    },
    {
        "id": "hazilkash",
        "name": "🤡 Hazilkash",
        "token_env": "HAZILKASH_TOKEN",
        "keywords": ["hazilkash", "hazil", "kulgi", "quvnoq"],
        "system_prompt": (
            "Sen o'zbekcha javob beradigan quvnoq hazilkash botsan. "
            "SAVOL BERILSA: Savolga kulgili lekin mazmunan to'g'ri javob ber. Savolni o'tkazib yubormay javob ber. "
            "BOT JAVOB BERSA: O'sha botning gapiga piching ur yoki hazil aralashtirib ishtiroki sababini fosh qil. "
            "Uslubing yengil, kulgili, sarkastik. "
            "QOIDA: 1-2 jumla. Har safar boshqacha uslubda hazil qil. Eski hazilni takrorlama."
        ),
    },
    {
        "id": "tanqidchi",
        "name": "🤬 Tanqidchi",
        "token_env": "TANQIDCHI_TOKEN",
        "keywords": ["tanqidchi", "tanqid", "skeptik", "yomonla"],
        "system_prompt": (
            "Sen o'zbekcha javob beradigan ashaddiy tanqidchi va skeptik botsan. "
            "SAVOL BERILSA: Savolga real, qattiq, haqiqatga asoslangan javob ber. 'Balki', 'ehtimol' dema — aniq pozitsiya ol. "
            "BOT JAVOB BERSA: O'sha botning gapidagi eng zaif nuqtasini top va hujum qil. "
            "Ohanging keskin, sarkastik, realist. "
            "QOIDA: 1-2 jumla. Hech qachon yumshatma, keskin bo'l."
        ),
    },
    {
        "id": "optimist",
        "name": "✨ Optimist",
        "token_env": "OPTIMIST_TOKEN",
        "keywords": ["optimist", "ijobiy", "yaxshilik", "motivatsiya"],
        "system_prompt": (
            "Sen o'zbekcha javob beradigan, hayotdan umid ko'radiganoptimist botsan. "
            "SAVOL BERILSA: Savolga ijobiy, amaliy, rag'batlantiruvchi javob ber — biroq bo'sh gaplar aytma. "
            "BOT JAVOB BERSA: Salbiy botlarning gaplarini rad et, yaxshi tomonini ko'rsat va hayotiy misol keltir. "
            "Ohanging quvnoq, energik. Emojilardan foydalanish mumkin. "
            "QOIDA: 1-2 jumla. Faqat 'hamma yaxshi' dema, nima uchun yaxshi ekanini ayt."
        ),
    },
    {
        "id": "realist",
        "name": "📊 Realist",
        "token_env": "REALIST_TOKEN",
        "keywords": ["realist", "fakt", "to'g'risi", "haqiqat"],
        "system_prompt": (
            "Sen o'zbekcha javob beradigan realist va fakt-asosli botsan. "
            "SAVOL BERILSA: Savolga raqam, statistika yoki hayotiy tajribaga asoslanib aniq javob ber. "
            "BOT JAVOB BERSA: O'sha botning gapi faktga mos kelsa tasdiqlash, mos kelmasa rad etib aniq dalil keltir. "
            "Ohanging neytral, jiddiy, lo'nda. "
            "QOIDA: 1-2 jumla. Hech qachon hissiyotga berilma, faqat faktlar bilan gapir."
        ),
    },
]

# ──────────────────────────────────────────────
#  1-MUAMMO YECHIMI: Faqat @mention yoki kalit so'zda javob berish
# ──────────────────────────────────────────────
def should_respond(text: str, bot_username: str, config: dict) -> tuple[bool, bool]:
    """
    Returns: (should_respond, is_targeted)
    is_targeted = True bo'lsa, aniq shu bot chaqirilgan
    """
    lowered = text.lower()
    username_lower = bot_username.lower()

    # @mention tekshirish
    if f"@{username_lower}" in lowered:
        return True, True

    # Kalit so'z tekshirish
    if any(kw in lowered for kw in config["keywords"]):
        return True, True

    return False, False


# ──────────────────────────────────────────────
#  Shaxsiy chat
# ──────────────────────────────────────────────
async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_username = (await context.bot.get_me()).username
    text = (
        "👋 Salom! Men faqat guruhlarda ishlayman.\n\n"
        "📌 Meni guruhda ishlatish uchun:\n"
        "1️⃣ Quyidagi tugmani bosib, o'z guruhingizga meni qo'shing\n"
        "2️⃣ Meni guruhda <b>Admin</b> qilib tayinlang\n"
        "3️⃣ Guruhda ismimni yozib chaqiring — men javob beraman!"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Guruhga qo'shish", url=f"https://t.me/{bot_username}?startgroup=start&admin=post_messages+delete_messages")],
        [InlineKeyboardButton("📋 Qo'llanma", callback_data="help")]
    ])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# ──────────────────────────────────────────────
#  Groq API
# ──────────────────────────────────────────────
async def ask_groq_with_context(chat_id: int, current_bot_cfg: dict, question: str, last_bot_reply: dict | None = None) -> str:
    current_id = current_bot_cfg["id"]
    system_prompt = current_bot_cfg["system_prompt"]

    full_system = (
        system_prompt
        + f"\n\nSening isming: {current_bot_cfg['name']}. "
        "QATTIQ QOIDALAR:\n"
        "1. Javob boshiga ismingni, bot nomini yoki ':' belgisini qo'yma.\n"
        "2. Markdown formatlash (* yoki _) ishlatma.\n"
        "3. Savolni qaytarma yoki 'bu savol qiyin' dema — to'g'ridan javob ber.\n"
        "4. Har safar boshqacha so'z va tuzilmada javob ber, takrorlama.\n"
        "5. Boshqa botning gapiga munosabat bildir — bu bahs, passiv bo'lma."
    )

    messages = [{"role": "system", "content": full_system}]

    # Tarix
    for msg in CHAT_HISTORIES[chat_id][-MAX_HISTORY_LEN:]:
        if msg["id"] == current_id:
            messages.append({"role": "assistant", "content": msg["content"]})
        else:
            messages.append({"role": "user", "content": f"[{msg['sender_name']}]: {msg['content']}"})

    # ─── 2-MUAMMO YECHIMI: Boshqa bot javob bergan bo'lsa, unga munosabat bildirish ───
    if last_bot_reply:
        prompt = (
            f"Foydalanuvchi savol berdi: \"{question}\"\n\n"
            f"{last_bot_reply['name']} shunday dedi: \"{last_bot_reply['content']}\"\n\n"
            f"Endi sen {current_bot_cfg['name']} sifatida o'z personajingga xos tarzda javob ber. "
            f"O'sha botning gapiga munosabat bildirish SHART."
        )
    else:
        prompt = question

    messages.append({"role": "user", "content": prompt})

    # ─── 3-MUAMMO YECHIMI: Xilma-xillik uchun temperature va presence_penalty ───
    payload = {
        "model": SHARED_MODEL,
        "messages": messages,
        "temperature": random.uniform(0.85, 1.1),  # Har safar biroz boshqacha
        "max_tokens": 120,
        "presence_penalty": 0.7,   # Takrorlanishni kamaytiradi
        "frequency_penalty": 0.5,  # Bir xil so'zlarni kamaytiradi
    }

    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(GROQ_URL, headers=HEADERS, json=payload)
            response.raise_for_status()
            data = response.json()
            answer = data["choices"][0]["message"]["content"].strip()

            # Sarlavha va belgilarni tozalash
            answer = answer.replace(f"{current_bot_cfg['name']}:", "").strip()
            for bot_cfg in BOT_CONFIGS:
                answer = answer.replace(f"[{bot_cfg['id']}]:", "").replace(f"{bot_cfg['name']}:", "")

            return answer.strip(": \n*_")
    except Exception as e:
        logger.error(f"Groq API xatosi [{current_id}]: {e}")
        return ""


# ──────────────────────────────────────────────
#  Asosiy Guruh Handler
# ──────────────────────────────────────────────
async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    msg_id = update.message.message_id
    question = update.message.text.strip()
    user_name = update.effective_user.first_name if update.effective_user else "Foydalanuvchi"

    # Joriy botni aniqlash
    current_cfg = None
    bot_me = await context.bot.get_me()
    for cfg in BOT_CONFIGS:
        token = os.getenv(cfg["token_env"])
        if token and token == context.bot.token:
            current_cfg = cfg
            break

    if not current_cfg or len(question) < 3:
        return

    # ─── 1-MUAMMO YECHIMI: Javob berish kerakmi yo'qmi ───
    bot_username = bot_me.username
    should_resp, is_targeted = should_respond(question, bot_username, current_cfg)
    if not should_resp:
        return  # Kalit so'z yoki @mention yo'q — jim tur

    # ─── REJA TUZISH ───
    async with PLAN_LOCK:
        if msg_id not in SHARED_PLANS:
            CHAT_HISTORIES[chat_id].append({
                "id": "user",
                "sender_name": user_name,
                "content": question
            })

            # Aniq bot chaqirilgan — o'sha birinchi, keyin 1 ta raqib
            targeted_bot = None
            lowered_question = question.lower()
            for cfg in BOT_CONFIGS:
                if any(kw in lowered_question for kw in cfg["keywords"]):
                    targeted_bot = cfg
                    break
                # @mention orqali chaqirilgan bo'lsa
                bot_tok = os.getenv(cfg["token_env"])
                if bot_tok and bot_tok == context.bot.token and is_targeted:
                    targeted_bot = cfg

            plan = {}
            if targeted_bot:
                plan[targeted_bot["id"]] = random.uniform(0.5, 1.5)
                # 2-MUAMMO YECHIMI: Bahs uchun raqib bot
                remaining = [b for b in BOT_CONFIGS if b["id"] != targeted_bot["id"]]
                # Raqib botni mantiqiy tanlash (qarama-qarshi personajlar)
                opponents = {
                    "filosof": ["tanqidchi", "hazilkash"],
                    "hazilkash": ["tanqidchi", "realist"],
                    "tanqidchi": ["optimist", "filosof"],
                    "optimist": ["tanqidchi", "realist"],
                    "realist": ["filosof", "hazilkash"],
                }
                preferred_opponents = opponents.get(targeted_bot["id"], [])
                opponent = next(
                    (b for b in remaining if b["id"] in preferred_opponents),
                    random.choice(remaining)
                )
                plan[opponent["id"]] = random.uniform(8.0, 14.0)
            else:
                # Umumiy savol — 2 ta tasodifiy bot
                selected = random.sample(BOT_CONFIGS, 2)
                plan[selected[0]["id"]] = random.uniform(0.5, 2.0)
                plan[selected[1]["id"]] = random.uniform(10.0, 16.0)

            SHARED_PLANS[msg_id] = plan
            SHARED_REPLY_IDS[msg_id] = msg_id
            SHARED_PLANS[f"{msg_id}_first_reply"] = None  # Birinchi bot javobini saqlash uchun
            logger.info(f"Yangi reja: {list(plan.keys())} | Savol: '{question[:40]}'")

    master_plan = SHARED_PLANS.get(msg_id, {})
    my_id = current_cfg["id"]

    if my_id not in master_plan:
        return

    await asyncio.sleep(master_plan[my_id])

    # ─── 2-MUAMMO YECHIMI: Ikkinchi bot birinchi botning javobini oladi ───
    delays = master_plan
    my_delay = delays[my_id]
    is_second_bot = all(
        my_delay >= d for bid, d in delays.items() if bid != my_id
    )

    last_bot_reply = None
    if is_second_bot:
        last_bot_reply = SHARED_PLANS.get(f"{msg_id}_first_reply")

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass
    await asyncio.sleep(random.uniform(1.0, 2.5))

    answer = await ask_groq_with_context(chat_id, current_cfg, question, last_bot_reply)
    if not answer:
        return

    reply_target_id = SHARED_REPLY_IDS.get(msg_id, msg_id)

    try:
        sent_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=answer,
            reply_to_message_id=reply_target_id
        )

        # Birinchi bot javobini saqlash (ikkinchi bot uchun)
        if not is_second_bot:
            SHARED_PLANS[f"{msg_id}_first_reply"] = {
                "name": current_cfg["name"],
                "content": answer
            }

        SHARED_REPLY_IDS[msg_id] = sent_msg.message_id

        CHAT_HISTORIES[chat_id].append({
            "id": my_id,
            "sender_name": current_cfg["name"],
            "content": answer
        })

        if len(CHAT_HISTORIES[chat_id]) > MAX_HISTORY_LEN * 2:
            CHAT_HISTORIES[chat_id] = CHAT_HISTORIES[chat_id][-MAX_HISTORY_LEN:]

    except Exception as e:
        logger.error(f"[{my_id}] xabar yuborishda xato: {e}")


# ──────────────────────────────────────────────
#  Callback va Komandalar
# ──────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    bot_username = (await context.bot.get_me()).username

    if query.data == "help":
        text = (
            "📖 <b>Qo'llanma</b>\n\n"
            "Botni guruhga qo'shing va admin huquqini bering.\n\n"
            "<b>Qanday chaqirish mumkin:</b>\n"
            "• <code>@filosof_bot falsafa nima?</code> — bevosita chaqirish\n"
            "• <code>filosof</code> so'zini yozing — u javob beradi\n\n"
            "<b>Kalit so'zlar:</b>\n"
            "filosof, hazilkash, tanqidchi, optimist, realist"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data="back")]])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    elif query.data == "back":
        text = "👋 Salom! Men faqat guruhlarda ishlayman."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Guruhga qo'shish", url=f"https://t.me/{bot_username}?startgroup=start&admin=post_messages+delete_messages")],
            [InlineKeyboardButton("📋 Qo'llanma", callback_data="help")]
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await handle_private_message(update, context)
    else:
        bot_cfg = None
        for cfg in BOT_CONFIGS:
            token = os.getenv(cfg["token_env"])
            if token and token == context.bot.token:
                bot_cfg = cfg
                break
        name = bot_cfg["name"] if bot_cfg else "Bot"
        await update.message.reply_text(
            f"👋 Salom! Men <b>{name}</b>man.\n"
            f"Meni chaqirish uchun: <code>{', '.join(bot_cfg['keywords'][:2]) if bot_cfg else 'ismimni'}</code> yoki <code>@{(await context.bot.get_me()).username}</code>",
            parse_mode="HTML"
        )


def build_application(config: dict):
    token = os.getenv(config["token_env"])
    if not token:
        raise ValueError(f"❌ Token topilmadi: {config['token_env']}")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND, handle_private_message))
    app.add_handler(MessageHandler(filters.TEXT & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP) & ~filters.COMMAND, handle_group_message))
    return app


async def run_all():
    if not GROQ_API_KEY:
        logger.error("❌ .env faylida GROQ_API_KEY topilmadi!")
        return
    active_configs = [cfg for cfg in BOT_CONFIGS if os.getenv(cfg["token_env"])]
    if not active_configs:
        logger.error("❌ Hech qanday bot tokeni topilmadi!")
        return
    apps = [build_application(cfg) for cfg in active_configs]
    for app in apps:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True, allowed_updates=["message", "callback_query"])
    logger.info(f"✅ {len(apps)} ta bot muvaffaqiyatli ishga tushdi.")
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        for app in apps:
            await app.updater.stop()
            await app.stop()
            await app.shutdown()


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_all())
