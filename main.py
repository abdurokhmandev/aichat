import asyncio
import logging
import httpx
import random
from dotenv import load_dotenv
import os
from collections import defaultdict

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
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
MAX_HISTORY_LEN = 6

SHARED_PLANS = {}       # {message_id: {bot_id: delay_seconds}}
SHARED_REPLY_IDS = {}   # {message_id: last_telegram_sent_msg_id} (Zanjirli reply uchun!)
PLAN_LOCK = asyncio.Lock()

# ──────────────────────────────────────────────
#  🔥 Mavzuga javob beradigan aniq xarakterlar
# ──────────────────────────────────────────────
BOT_CONFIGS = [
    {
        "id": "filosof",
        "token_env": "FILOSOF_TOKEN",
        "system_prompt": (
            "Sen o'zbekcha gapiradigan faylasufsan 🧠. Foydalanuvchi bergan mavzuni (masalan: ob-havo, hayot, pul va h.k.) "
            "falsafiy tomondan tahlil qilib, faqat 1 ta qisqa gap yoz. Savolga albatta mavzu doirasida javob qaytar! "
            "Matn boshiga yoki ichiga aslo '[filosof]:' yoki o'z ismingni yozma! "
            "Masalan, ob-havo so'ralsa: 'Ob-havoning o'zgarishi aslida inson qalbining o'zgaruvchan aksidir... 🤔' deb qisqa falsafa qil."
        ),
    },
    {
        "id": "hazilkash",
        "token_env": "HAZILKASH_TOKEN",
        "system_prompt": (
            "Sen o'zbekcha gapiradigan quvnoq ko'cha bolasisan 😂. Berilgan mavzu yoki guruhdagi oxirgi gap yuzasidan "
            "faqat 1 ta qisqa va o'tkir hazil yoki piching yoz. Savoldan butunlay qochma, mavzuga bog'la! Ismingni mutlaqo yozma. "
            "Masalan, ob-havo so'ralsa: 'Ob-havoni bilmadim-u, lekin hozir muzdek tarvuz bo'lsa daxshat ketardi-da! 🍉' deb mavzuga mos hazil qil."
        ),
    },
    {
        "id": "tanqidchi",
        "token_env": "TANQIDCHI_TOKEN",
        "system_prompt": (
            "Sen asabiy va o'ta aqlli tanqidchisan 🤬. Berilgan savol yoki boshqa botlarning gapi yuzasidan xato topib, "
            "faqat 1 ta qisqa jumlada tanqid qil. Matn boshiga aslo '[tanqidchi]:' yoki shunga o'xshash nom yozma! "
            "Masalan, ob-havo so'ralsa: 'Yonigizda smartfon turib, mendan ob-havoni so'rayapsizlarmi, mantiq qani?! 🤦‍♂️' deb mavzuni urishib tanqid qil."
        ),
    },
    {
        "id": "optimist",
        "token_env": "OPTIMIST_TOKEN",
        "system_prompt": (
            "Sen doim quvnoq optimistsan ✨. Berilgan mavzu yoki muammo yuzasidan doim yaxshilik ko'rib, 1 ta qisqa jumlada dalda ber. Ismingni yozma. "
            "Masalan, ob-havo yomon bo'lsa: 'Havo qanday bo'lishidan qat'iy nazar kayfiyatni ko'taramiz, asosiysi qalblarimiz issiq bo'lsin! 🚀' deb yoz."
        ),
    },
    {
        "id": "realist",
        "token_env": "REALIST_TOKEN",
        "system_prompt": (
            "Sen mantiqiy va faqat faktlarga tayanadigan realistsan 📊. Berilgan savolga o'ta aniq, quruq va lo'nda qilib 1 ta jumlada javob ber. Ismingni yozma. "
            "Masalan: 'Ob-havoni aniq bilish uchun sinoptiklar saytiga qarash kerak, biz faqat taxmin qila olamiz 🧐' deb aniq fakt yoz."
        ),
    },
]

# ──────────────────────────────────────────────
#  Groq API (Chalg'ituvchi teglarsiz toza format)
# ──────────────────────────────────────────────
async def ask_groq_with_context(chat_id: int, current_bot_id: str, system_prompt: str) -> str:
    # Model o'ziga ism to'qimasligi uchun qat'iy eslatma qo'shamiz
    full_system = system_prompt + "\nMUHIM: Hech qachon matningiz boshiga '[...] ' kabi nomlar yoki teglarni qo'shmang! To'g'ridan-to'g'ri xabarni o'zini yozing."
    messages = [{"role": "system", "content": full_system}]
    
    # Tariqdagi qavslarni olib tashladik, bu model chalg'ishini oldini oladi
    for msg in CHAT_HISTORIES[chat_id]:
        if msg["id"] == current_bot_id:
            messages.append({"role": "assistant", "content": msg["content"]})
        else:
            messages.append({"role": "user", "content": f"Suhbatdosh fikri: {msg['content']}"})

    payload = {
        "model": SHARED_MODEL,
        "messages": messages,
        "temperature": 0.85, # Javoblar mavzudan chiqib ketmasligi uchun biroz pasaytirildi
        "max_tokens": 70,
    }
    
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(GROQ_URL, headers=HEADERS, json=payload)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Groq API xatosi [{current_bot_id}]: {e}")
        return ""

# ──────────────────────────────────────────────
#  Sinxronlashtirilgan Zanjirli Reply Handler
# ──────────────────────────────────────────────
async def handle_ask_chain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    msg_id = update.message.message_id
    
    current_cfg = None
    for cfg in BOT_CONFIGS:
        if os.getenv(cfg["token_env"]) == context.bot.token:
            current_cfg = cfg
            break
            
    if not current_cfg or not context.args:
        return
        
    question = " ".join(context.args)
    user_name = update.effective_user.first_name if update.effective_user else "User"

    # 🛑 1. BOSH PLAN TUZISH
    async with PLAN_LOCK:
        if msg_id not in SHARED_PLANS:
            CHAT_HISTORIES[chat_id].append({"id": user_name, "content": question})
            
            # Guruh toza bo'lishi uchun 1 tadan 2 tagacha bot tasodifiy tanlanadi
            num_bots_to_pick = random.randint(1, 2)
            selected_bots = random.sample(BOT_CONFIGS, num_bots_to_pick)
            
            plan = {}
            # Birinchi bot tezda chiqadi (3-6 soniya)
            plan[selected_bots[0]["id"]] = random.uniform(3, 6)
            
            # Ikkinchi bot ancha kech chiqadi (25-40 soniya)
            if num_bots_to_pick > 1:
                plan[selected_bots[1]["id"]] = random.uniform(25, 40)
                
            SHARED_PLANS[msg_id] = plan
            # Zanjirli reply targetini birinchi bo'lib foydalanuvchining xabariga sozlaymiz
            SHARED_REPLY_IDS[msg_id] = msg_id

    # 📑 2. REJADA BORLIGINI TEKSHIRISH
    master_plan = SHARED_PLANS[msg_id]
    my_id = current_cfg["id"]
    
    if my_id not in master_plan:
        return # Rejada yo'q bot mutlaqo jim turadi!

    # ⏳ 3. REJADAGI VAQTNI KUTISH
    allocated_delay = master_plan[my_id]
    await asyncio.sleep(allocated_delay)
    
    # "Yozmoqda..." statusini yoqish
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    await asyncio.sleep(random.uniform(2, 4))

    # Groq'dan toza javobni olish
    answer = await ask_groq_with_context(chat_id, my_id, current_cfg["system_prompt"])
    if not answer:
        return

    # 🔗 4. HAQIQIY ZANJIRLI REPLY (Eng muhim qismi)
    # Ushbu zanjir uchun eng oxirgi yuborilgan xabar ID sini olamiz
    reply_target_id = SHARED_REPLY_IDS.get(msg_id, msg_id)

    try:
        # Xabarni foydalanuvchiga emas, aynan o'zidan oldingi xabarga REPLI qilib yuboradi!
        sent_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=answer,
            reply_to_message_id=reply_target_id
        )
        
        # 🔥 Navbatdagi bot endi hozirgina yuborilgan ushbu xabarga reply qilishi uchun ID ni yangilaymiz!
        SHARED_REPLY_IDS[msg_id] = sent_msg.message_id
        
        # Tarixni yangilash
        CHAT_HISTORIES[chat_id].append({"id": my_id, "content": answer})
        if len(CHAT_HISTORIES[chat_id]) > MAX_HISTORY_LEN:
            CHAT_HISTORIES[chat_id].pop(0)
            
        logger.info(f"[{my_id}] {reply_target_id} xabarga reply qilib muvaffaqiyatli yubordi.")
    except Exception as e:
        logger.error(f"Xabar yuborishda xato: {e}")

# ──────────────────────────────────────────────
#  Application Builder va Start
# ──────────────────────────────────────────────
def build_application(config: dict):
    token = os.getenv(config["token_env"])
    if not token:
        raise ValueError(f"Token topilmadi: {config['token_env']}")

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("ask", handle_ask_chain))
    return app

async def run_all():
    if not GROQ_API_KEY:
        logger.error("❌ .env faylida GROQ_API_KEY topilmadi!")
        return

    apps = [build_application(cfg) for cfg in BOT_CONFIGS]

    for app in apps:
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

    logger.info("✅ ZANJIRLI REPLY va TOZA PROMPT rejimi muvaffaqiyatli ishga tushdi!")

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