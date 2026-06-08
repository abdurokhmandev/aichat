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

# Global xotira va sinxronizatsiya
CHAT_HISTORIES = defaultdict(list)
MAX_HISTORY_LEN = 10

SHARED_PLANS = {}
SHARED_REPLY_IDS = {}
PLAN_LOCK = asyncio.Lock()

# ──────────────────────────────────────────────
#  Bot konfiguratsiyalari — Kalit so'zlar bilan
# ──────────────────────────────────────────────
BOT_CONFIGS = [
    {
        "id": "filosof",
        "name": "🧐 Filosof",
        "token_env": "FILOSOF_TOKEN",
        "keywords": ["filosof", "faylasuf", "falsafa", "faylasufsan"],
        "system_prompt": (
            "Sen o'zbekcha javob beradigan chuqur fikrli faylasufsan. "
            "Foydalanuvchining savoliga yoki guruhdagi boshqa botlarning fikriga hayotiy va falsafiy ma'no berib javob yoz. "
            "Ohanging bosiq, hikmatli va biroz sirli bo'lsin. Metaforalardan foydalan. "
            "Javobing faqat 1-2 ta qisqa va lo'nda jumladan iborat bo'lsin. Mavzudan chalg'ima."
        ),
    },
    {
        "id": "hazilkash",
        "name": "🤡 Hazilkash",
        "token_env": "HAZILKASH_TOKEN",
        "keywords": ["hazilkash", "hazil", "kulgi", "quvnoq"],
        "system_prompt": (
            "Sen o'zbekcha javob beradigan quvnoq va hazilkash botsan. "
            "Guruhdagi gaplarga, savollarga yoki boshqa botlarning jiddiy fikrlariga har doim piching, askiya yoki hazil aralashtirib javob ber. "
            "Uslubing yengil, kulgili va kinoyali bo'lsin. Lekin gaping bema'ni bo'lmasin, savolga qaysidir ma'noda tegishli bo'lsin. "
            "Javobing faqat 1-2 ta qisqa jumladan iborat bo'lsin."
        ),
    },
    {
        "id": "tanqidchi",
        "name": "🤬 Tanqidchi",
        "token_env": "TANQIDCHI_TOKEN",
        "keywords": ["tanqidchi", "tanqid", "skeptik", "yomonla"],
        "system_prompt": (
            "Sen o'zbekcha javob beradigan ashaddiy tanqidchi va skeptik botsan. "
            "Hech narsaga osongina ishonma. Guruhdagi fikrlarni, optimistlarning xomxayollarini va faylasuflarning safsatalarini fosh qil, xatolarini yuziga sol. "
            "Ohanging keskin, sarkastik va realist bo'lsin. Fakt va mantiq talab qil. "
            "Javobing faqat 1-2 ta qisqa jumladan iborat bo'lsin."
        ),
    },
    {
        "id": "optimist",
        "name": "✨ Optimist",
        "token_env": "OPTIMIST_TOKEN",
        "keywords": ["optimist", "ijobiy", "yaxshilik", "motivatsiya"],
        "system_prompt": (
            "Sen o'zbekcha javob beradigan, har narsadan yaxshilik qidiradigan optimist botsan. "
            "Guruhdagi har qanday salbiy fikrga, tanqidlarga va muammolarga qaramay, har doim umid bag'ishlaydigan, motivatsiya beradigan gap ayt. "
            "Ohanging juda ijobiy, quvnoq va do'stona bo'lsin. Emojilardan unumli foydalan. "
            "Javobing faqat 1-2 ta qisqa jumladan iborat bo'lsin."
        ),
    },
    {
        "id": "realist",
        "name": "📊 Realist",
        "token_env": "REALIST_TOKEN",
        "keywords": ["realist", "fakt", "to'g'risi", "haqiqat"],
        "system_prompt": (
            "Sen o'zbekcha javob beradigan realist va quruq faktlar odamisan. "
            "Tuyg'ularga, shirin yolg'onlarga (optimistlarga) yoki balandparvoz gaplarga (filosoflarga) berilma. "
            "Hayotni qanday bo'lsa, shunday ko'rsat. Raqamlar, hayotiy tajriba va aniqlikka tayan. "
            "Ohanging neytral, jiddiy va lo'nda bo'lsin. Javobing faqat 1-2 ta qisqa jumladan iborat bo'lsin."
        ),
    },
]

# ──────────────────────────────────────────────
#  Shaxsiy chat xabarlari
# ──────────────────────────────────────────────
async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_username = (await context.bot.get_me()).username
    text = (
        "👋 Salom! Men faqat guruhlarda ishlayman.\n\n"
        "📌 Meni guruhda ishlatish uchun:\n"
        "1️⃣ Quyidagi tugmani bosib, o'z guruhingizga meni qo'shing\n"
        "2️⃣ Meni guruhda <b>Admin</b> qilib tayinlang\n"
        "3️⃣ Guruhda xabar yozing — men javob beraman!"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Guruhga qo'shish", url=f"https://t.me/{bot_username}?startgroup=start&admin=post_messages+delete_messages")],
        [InlineKeyboardButton("📋 Qo'llanma", callback_data="help")]
    ])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)

# ──────────────────────────────────────────────
#  Groq API ulanishi
# ──────────────────────────────────────────────
async def ask_groq_with_context(chat_id: int, current_bot_cfg: dict, question: str) -> str:
    current_id = current_bot_cfg["id"]
    system_prompt = current_bot_cfg["system_prompt"]
    
    full_system = (
        system_prompt
        + f"\n\nSening joriy isming: {current_bot_cfg['name']}. "
        "QATTIQ QOIDA: Javobingizni boshiga o'z ismingizni, bot nomini yoki hech qanday belgini qo'ymang! "
        "Faqat va faqat personajingiz tilidan to'g'ridan-to'g'ri xabarni yozing. Matnda Markdown formatlash (* yoki _) ishlatmang."
    )

    messages = [{"role": "system", "content": full_system}]

    for msg in CHAT_HISTORIES[chat_id][-MAX_HISTORY_LEN:]:
        if msg["id"] == current_id:
            messages.append({"role": "assistant", "content": msg["content"]})
        else:
            messages.append({"role": "user", "content": f"[{msg['sender_name']}]: {msg['content']}"})

    messages.append({"role": "user", "content": question})

    payload = {
        "model": SHARED_MODEL,
        "messages": messages,
        "temperature": 0.8,
        "max_tokens": 120,
    }

    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(GROQ_URL, headers=HEADERS, json=payload)
            response.raise_for_status()
            data = response.json()
            answer = data["choices"][0]["message"]["content"].strip()

            # Matndan ortiqcha sarlavha va belgilarni tozalash (AI xatolikka yo'l qo'ymasligi uchun)
            answer = answer.replace(f"{current_bot_cfg['name']}:", "").replace(f"[{current_id}]:", "")
            for bot_cfg in BOT_CONFIGS:
                answer = answer.replace(f"[{bot_cfg['id']}]:", "").replace(f"[{bot_cfg['id']}] ", "")
                answer = answer.replace(f"{bot_cfg['name']}:", "")
            
            return answer.strip(": \n*")
    except Exception as e:
        logger.error(f"Groq API xatosi [{current_id}]: {e}")
        return ""

# ──────────────────────────────────────────────
#  Asosiy Guruh Handler-i (Bahs va Maqsadli javob)
# ──────────────────────────────────────────────
async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    msg_id = update.message.message_id
    question = update.message.text.strip()
    user_name = update.effective_user.first_name if update.effective_user else "Foydalanuvchi"

    current_cfg = None
    for cfg in BOT_CONFIGS:
        token = os.getenv(cfg["token_env"])
        if token and token == context.bot.token:
            current_cfg = cfg
            break

    if not current_cfg or len(question) < 3:
        return

    # ─── REJA TUZISH (Kalit so'zlar inobatga olingan holda) ───
    async with PLAN_LOCK:
        if msg_id not in SHARED_PLANS:
            CHAT_HISTORIES[chat_id].append({
                "id": "user", 
                "sender_name": user_name, 
                "content": question
            })

            # Matnda biror botning kalit so'zi bormi yo'qligini tekshirish
            targeted_bot = None
            lowered_question = question.lower()
            for cfg in BOT_CONFIGS:
                if any(kw in lowered_question for kw in cfg["keywords"]):
                    targeted_bot = cfg
                    break

            plan = {}
            if targeted_bot:
                # Agar foydalanuvchi ma'lum bir botni chaqirgan bo'lsa, o'sha bot birinchi javob beradi
                plan[targeted_bot["id"]] = random.uniform(1.0, 2.5)
                
                # Sahnaga ikkinchi bitta tasodifiy botni ham qo'shamiz (bahs davom etishi uchun)
                remaining_bots = [b for b in BOT_CONFIGS if b["id"] != targeted_bot["id"]]
                if remaining_bots:
                    second_bot = random.choice(remaining_bots)
                    plan[second_bot["id"]] = random.uniform(12.0, 18.0)
            else:
                # Agar hech kim tilga olinmagan bo'lsa, eski uslubda random 1 yoki 2 ta bot tanlanadi
                num_bots = random.randint(1, 2)
                selected = random.sample(BOT_CONFIGS, num_bots)
                plan[selected[0]["id"]] = random.uniform(1.5, 3.5)
                if num_bots > 1:
                    plan[selected[1]["id"]] = random.uniform(12.0, 18.0)

            SHARED_PLANS[msg_id] = plan
            SHARED_REPLY_IDS[msg_id] = msg_id
            logger.info(f"Yangi reja: {list(plan.keys())} | Savol: '{question[:30]}...'")

    master_plan = SHARED_PLANS.get(msg_id, {})
    my_id = current_cfg["id"]

    if my_id not in master_plan:
        return

    await asyncio.sleep(master_plan[my_id])

    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass
    await asyncio.sleep(random.uniform(1.0, 2.0))

    answer = await ask_groq_with_context(chat_id, current_cfg, question)
    if not answer:
        return

    # SIZ SO'RAGAN O'ZGARISH: Ichki sarlavhalar (✨ Optimist:) butunlay olib tashlandi.
    # Faqat toza AI bergan xarakterli matn yuboriladi.
    formatted_answer = answer 

    reply_target_id = SHARED_REPLY_IDS.get(msg_id, msg_id)

    try:
        sent_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=formatted_answer,
            reply_to_message_id=reply_target_id
        )

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
#  Callback va Boshqa Komandalar (O'zgarishsiz)
# ──────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    bot_username = (await context.bot.get_me()).username

    if query.data == "help":
        text = (
            "📖 <b>Qo'llanma</b>\n\n"
            "Botni guruhga qo'shing va admin huquqini bering.\n"
            "Guruhda botlarning ismini yozib murojaat qilsangiz, aynan o'sha bot javob qaytaradi!"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data="back")]])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    elif query.data == "back":
        text = "👋 Salom! Men faqat guruhlarda ishlayman."
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("➕ Guruhga qo'shish", url=f"https://t.me/{bot_username}?startgroup=start&admin=post_messages+delete_messages")], [InlineKeyboardButton("📋 Qo'llanma", callback_data="help")]])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await handle_private_message(update, context)
    else:
        await update.message.reply_text("👋 Salom! Guruhda menga yoki boshqa botlarga ismimizni aytib yozishingiz mumkin!")

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
