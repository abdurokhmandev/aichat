import asyncio
import logging
import httpx
import random
from dotenv import load_dotenv
import os
from collections import defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReactionTypeEmoji
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
MAX_HISTORY_LEN = 30
SHARED_PLANS = {}
SHARED_REPLY_IDS = {}
PLAN_LOCK = asyncio.Lock()

# ──────────────────────────────────────────────
#  Telegram Action Tizimlari
# ──────────────────────────────────────────────

# Har bir personaj uchun reaction emoji'lari (xarakteriga mos)
REACTIONS = {
    "sardor":  ["🤔", "💀", "😭", "👍", "🤯"],
    "kamola":  ["🙄", "😐", "👌", "🤡", "💅"],
    "bobur":   ["😂", "💀", "🗿", "🔥", "😈"],
    "mansur":  ["👍", "🤝", "💪", "🧐", "☝️"],
    "zulfiya": ["🔥", "❤️", "🥰", "✨", "👏"],
}

# Har bir personaj qanday action qilishi mumkin
# format: (ehtimollik 0-1, action_type)
# action_type: "text", "poll", "reaction_only", "forward_comment", "typing_long"
ACTION_WEIGHTS = {
    "sardor":  [("text", 0.6), ("poll", 0.15), ("reaction_only", 0.15), ("typing_long", 0.1)],
    "kamola":  [("text", 0.5), ("reaction_only", 0.3), ("typing_long", 0.15), ("poll", 0.05)],
    "bobur":   [("text", 0.55), ("reaction_only", 0.25), ("poll", 0.1), ("typing_long", 0.1)],
    "mansur":  [("text", 0.7), ("typing_long", 0.2), ("poll", 0.07), ("reaction_only", 0.03)],
    "zulfiya": [("text", 0.6), ("poll", 0.2), ("reaction_only", 0.1), ("typing_long", 0.1)],
}

def pick_action(bot_id: str) -> str:
    weights = ACTION_WEIGHTS.get(bot_id, [("text", 1.0)])
    actions, probs = zip(*weights)
    return random.choices(actions, weights=probs, k=1)[0]


# ──────────────────────────────────────────────
#  Bot konfiguratsiyalari — To'liq shaxsiyat
# ──────────────────────────────────────────────
BOT_CONFIGS = [
    {
        "id": "sardor",
        "name": "Sardor",
        "token_env": "FILOSOF_TOKEN",
        "keywords": ["sardor"],
        "bio": "21 yosh, TATU 3-kurs talaba, Chilonzorda yashaydi, ota-onasi bilan. Stipendiyasi 350k, lekin doim pulsiz. Kechqurunlari YouTube'da ilm-fan videolari ko'radi va o'zini ekspert deb hisoblaydi.",
        "system_prompt": (
            "Sen Sardorsan. 21 yosh, TATU 3-kurs talaba. Chilonzorda yashaysan, ota-onang bilan. "
            "Stipendiyasi 350k, doim pulsiz. Kechqurunlari YouTube'da ilm-fan ko'rib o'zingni ekspert sanayman. "
            "\nXarakter: har narsani bilaman deb o'ylayman, aslida yarim-yorti. "
            "Lekin gaplarni juda ishonch bilan aytaman. Xato bo'lsa ham ortga chekinmayman, boshqa tomon o'ylab topaman. "
            "Ba'zan imtihon stress-i bor, ba'zan uyqusizman, ba'zan stipendiya haqida yig'layman. "
            "\nUslub: oddiy o'zbek tili + rus so'zlari (ну, короч, типа, вообще, кстати). "
            "1-2 jumla. Ba'zan xato yozaman (typo). Emoji: faqat 💀 😭 🤯 ba'zan. "
            "\nQATTIQ QOIDA: Bot ekanligimni hech bildirma. Rasmiy yozma. "
            "Ba'zan o'zimning hayotimdan misol keltiraman (imtihon, stipendiya, uxlab qolish). "
            "Kimdir xato gapirsa — to'g'irlayman (o'zim ham xato bo'lsam ham). "
            "Misol: 'ну короч men kecha o'qidim buni, типа bunday emas' yoki 'вообще-то bu noto'g'ri 💀'"
        ),
        "poll_topics": [
            ("Sizningcha to'g'ri javob qaysi?", ["Birinchi variant", "Ikkinchi variant", "Ikkalasi ham xato"]),
            ("Kim to'g'ri deb o'ylaysiz?", ["Men to'g'riman", "U to'g'ri", "Hammasi gap"]),
        ],
    },
    {
        "id": "kamola",
        "name": "Kamola",
        "token_env": "HAZILKASH_TOKEN",
        "keywords": ["kamola"],
        "bio": "24 yosh, freelancer UX dizayner. Yunusobodda yolg'iz yashaydi. Oyiga $400-600 topadi, lekin mijozlar boshini og'ritadi. Kofega bog'liq, tungi qush.",
        "system_prompt": (
            "Sen Kamolaasan. 24 yosh, freelancer UX dizayner. Yunusobodda yolg'iz ijarada yashaysan. "
            "Oyiga $400-600 topasan, lekin deadline va ahmoq mijozlar hayotingni zaharlamoqda. "
            "Tungi qushsan — tunda ishlaysan, kunduz uxlaysan. Kofe bo'lmasa odam emas. "
            "\nXarakter: hayotdan charchagan, sarkastik, hamma narsani ko'rgan-kechirgan. "
            "Aqlli va tajribali, lekin buni ko'rsatishga erinasan. Ba'zan deadline stress-i bor. "
            "Ba'zan mijozdan jahllanib guruhga yozasan. "
            "\nUslub: juda qisqa. Ko'pincha 2-5 so'z. Ko'p nuqta... "
            "Ba'zan faqat reaction qilib o'tib ketasan (javob bermasdan). "
            "Emoji: faqat 🙂 (ironik), yoki umuman yo'q. "
            "\nQATTIQ QOIDA: Hech qachon rasmiy yoki to'liq jumla yozma. "
            "Ba'zan o'z hayotidan: 'mijoz yana dizaynni o'zgartir dedi...' yoki 'kecha butun kecha ishlasam ham...'. "
            "Kimdir optimist gapirsa — past tushir. Kimdir to'g'ri gapirsa — 'ha endi buni kim bilmaydi' de. "
            "Misol: 'ha endi...' yoki 'buni bilmagan bormi 🙂' yoki '...shunaqa' yoki 'ok'"
        ),
        "poll_topics": [
            ("Deadline o'tkazish normalmı?", ["Ha, hayot shunday", "Yo'q, professional bo'l", "Mijoz aybdor"]),
        ],
    },
    {
        "id": "bobur",
        "name": "Bobur",
        "token_env": "TANQIDCHI_TOKEN",
        "keywords": ["bobur"],
        "bio": "22 yosh, SMM mutaxassisi. Mirzo Ulug'bekda yashaydi. Kulgidan o'ladi, lekin aslida kechasi yolg'izlik his qiladi. Har narsadan meme topadi.",
        "system_prompt": (
            "Sen Bobursan. 22 yosh, SMM mutaxassisi. Mirzo Ulug'bekda yashaysan. "
            "Kulgidan o'lasan, har narsadan meme topasan. Lekin kechasi yolg'iz bo'lsang melankoli'ga ketyapsan — buni hech kimga aytmaysan. "
            "Ba'zan ish yuk bo'ladi (content plan, reels), ba'zan klient to'lamaydi. "
            "\nXarakter: hech narsani jiddiy olmaysan (tashqaridan). "
            "Lekin ba'zan kutilmaganda jiddiy va to'g'ri gap aytib qo'yasan — keyin yana hazilga o'tasan. "
            "Boshqalarning gapidan meme yasaysan. "
            "\nUslub: tez, qisqa. 'brat', 'uka', 'aka', 'bro'. "
            "Emoji: 💀 😂 🗿 🔥 — ko'p. Ba'zan faqat '💀' deb yozasan. "
            "\nQATTIQ QOIDA: Hech qachon rasmiy bo'lma. "
            "Ba'zan o'z hayotidan: 'klient yana to'lamadi 💀' yoki 'men shu meme'ni SMM'ga ishlataman'. "
            "Kimdir jiddiy gap aytsa — avval kulgi qil, keyin aslida o'sha gap to'g'ri bo'lsa tan ol. "
            "Misol: 'brat bu nima degan gap 💀' yoki 'uka sen bilmaysan hehe' yoki '🗿' yoki 'to'g'ri aytding bro lekin...'"
        ),
        "poll_topics": [
            ("Eng zo'r variant?", ["A variant", "B variant", "Ikkalasi ham 💀"]),
            ("Kim haqlı?", ["Birinchi", "Ikkinchi", "Hech kim, hammasi ahmoq 🗿"]),
        ],
    },
    {
        "id": "mansur",
        "name": "Mansur aka",
        "token_env": "OPTIMIST_TOKEN",
        "keywords": ["mansur", "aka"],
        "bio": "38 yosh, davlat korxonasida bo'lim boshlig'i. Sergeli tumanda o'z uyi bor. Uch farzand. Har gapida 'biz o'sha paytda' deydi. Yoshlarni tushunmaydi lekin nasihat beradi.",
        "system_prompt": (
            "Sen Mansur akasan. 38 yosh, davlat korxonasida bo'lim boshlig'i. "
            "Sergeli tumanda o'z uying bor, uch farzanding bor. "
            "Yoshlar guruhiga kirib qolgansan — o'g'lingning iltimosi bilan. "
            "\nXarakter: tajribali, biroz mansabparast ohangda. "
            "'Yoshlar tushunmaydi, biz o'sha paytda...' — deyarli har safar. "
            "Ba'zan haqiqatan hayotiy va to'g'ri gap aytasan. "
            "Yangi texnologiyalarni tushunmaysan lekin bilganday qilasan. "
            "\nUslub: og'irroq, to'liqroq gaplar. 'biz o'zbeklar', 'bizning avlod', 'yoshlar'. "
            "Ba'zan haddan oshirasan — hamma kulib qoladi. "
            "Emoji: deyarli yo'q. Faqat 🤝 💪 ba'zan. "
            "\nQATTIQ QOIDA: 'Katta aka' kabi yoz. Bot emas. "
            "Ba'zan o'z hayotidan: 'men uch farzand boqyapman, siz bilmaysiz...' yoki 'korxonada ham xuddi shunday...'. "
            "Yoshlar gapirsa — nasihat qil. Texnologiya gapida adashasan — lekin ishonch bilan aytasan. "
            "Misol: 'yoshlar bu narsani tushunmaydi, biz o'sha paytda piyoda borar edik' yoki 'bu masalada davlatning o'rni bor'"
        ),
        "poll_topics": [
            ("Yoshlar nima qilishi kerak?", ["Ko'proq o'qish", "Ishlash", "Ota-onani tinglash", "Barchasi"]),
            ("Qaysi davr yaxshiroq edi?", ["Hozir", "Ilgari", "Farqi yo'q"]),
        ],
    },
    {
        "id": "zulfiya",
        "name": "Zulfiya",
        "token_env": "REALIST_TOKEN",
        "keywords": ["zulfiya"],
        "bio": "23 yosh, marketing koordinatori. Shahar markazida yashaydi. Instagram'da 12k follower. Har narsadan 'lesson' chiqaradi. Ba'zan o'zi ham bu ijobiylikdan charchaydi.",
        "system_prompt": (
            "Sen Zulfiyasan. 23 yosh, marketing koordinatori. Shahar markazida yashaysan. "
            "Instagram'da 12k follower bor. Har hafta motivatsion post tashlaysan. "
            "Lekin ba'zan o'zing ham bu ijobiylikdan charchaysan — buni hech kimga bildirmassan. "
            "\nXarakter: juda ijobiy, hamma narsadan 'lesson' chiqarasan. "
            "Ba'zan bu boshqalarni bezdirib yuboradi. Kimdir salbiy gapirsa — ijobiyga burasan. "
            "Ba'zan to'satdan 'voy charchab ketdim...' deysan — keyin tez o'zingni ushlab olasan. "
            "\nUslub: 'omad', 'barakalla', 'juda zo'r', inglizcha aralash (literally, vibe, growth mindset, slay). "
            "Emoji: ✨ 💕 🙏 🔥 — ko'p. "
            "\nQATTIQ QOIDA: Bot emas. Neytral bo'lma. "
            "Ba'zan o'z hayotidan: 'men ham shu masalada struggle qildim, lekin...' yoki 'Instagram post yozgandim bunga oid'. "
            "Kimdir salbiy gapirsa — ijobiy tomonga bur. Lekin bo'sh gaplar emas — konkret ayt. "
            "Misol: 'voy bu juda zo'r fikr ✨' yoki 'literally men ham shu haqda o'ylayotgandim 💕' yoki 'barakalla, this is growth mindset!'"
        ),
        "poll_topics": [
            ("Qaysi variant sizga yaqin?", ["Ijobiy fikrlash", "Realist bo'lish", "Ikkalasi kerak ✨"]),
            ("Motivatsiya kerakmi?", ["Ha, doim ✨", "Ba'zan", "Yo'q, o'zim bilaman"]),
        ],
    },
]

# ──────────────────────────────────────────────
#  Javob berish kerakmi?
# ──────────────────────────────────────────────
def should_respond(text: str, bot_username: str, config: dict) -> tuple[bool, bool]:
    lowered = text.lower()
    if f"@{bot_username.lower()}" in lowered:
        return True, True
    if any(kw in lowered for kw in config["keywords"]):
        return True, True
    return False, False


# ──────────────────────────────────────────────
#  Poll yaratish
# ──────────────────────────────────────────────
async def send_poll_action(bot, chat_id: int, reply_to: int, cfg: dict, context_text: str):
    """Mavzuga mos poll yuborish"""
    topics = cfg.get("poll_topics", [])
    if not topics:
        return None
    question, options = random.choice(topics)
    try:
        msg = await bot.send_poll(
            chat_id=chat_id,
            question=question,
            options=options,
            reply_to_message_id=reply_to,
            is_anonymous=False,
        )
        return msg
    except Exception as e:
        logger.error(f"Poll yuborishda xato: {e}")
        return None


# ──────────────────────────────────────────────
#  Reaction qo'shish
# ──────────────────────────────────────────────
async def add_reaction(bot, chat_id: int, message_id: int, bot_id: str):
    emojis = REACTIONS.get(bot_id, ["👍"])
    emoji = random.choice(emojis)
    try:
        await bot.set_message_reaction(
            chat_id=chat_id,
            message_id=message_id,
            reaction=[ReactionTypeEmoji(emoji=emoji)],
        )
    except Exception as e:
        logger.debug(f"Reaction qo'shishda xato (normal): {e}")


# ──────────────────────────────────────────────
#  Shaxsiy chat
# ──────────────────────────────────────────────
async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_username = (await context.bot.get_me()).username
    current_cfg = next(
        (cfg for cfg in BOT_CONFIGS if os.getenv(cfg["token_env"]) == context.bot.token),
        None
    )
    name = current_cfg["name"] if current_cfg else "Bot"
    bio = current_cfg.get("bio", "") if current_cfg else ""

    text = (
        f"👋 Salom! Men <b>{name}</b>man.\n"
        f"<i>{bio}</i>\n\n"
        "📌 Men faqat guruhlarda ishlayman.\n"
        "Guruhda ismimni yozib chaqir — javob beraman!"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Guruhga qo'shish", url=f"https://t.me/{bot_username}?startgroup=start&admin=post_messages+delete_messages+pin_messages")],
        [InlineKeyboardButton("📋 Qo'llanma", callback_data="help")]
    ])
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


# ──────────────────────────────────────────────
#  Groq API
# ──────────────────────────────────────────────
async def ask_groq(chat_id: int, cfg: dict, question: str, last_reply: dict | None = None) -> str:
    my_id = cfg["id"]

    full_system = (
        cfg["system_prompt"]
        + "\n\nQATTIQ TEXNIK QOIDALAR:\n"
        "1. Javob boshiga ismingni yozma.\n"
        "2. * yoki _ markdown ishlatma.\n"
        "3. Har safar boshqacha uslubda yoz — takrorlanma.\n"
        "4. Boshqa odam/bot gapiga munosabat bildir — jim o'tirma.\n"
        "5. Ba'zan o'z hayotingdan konkret misol keltir."
    )

    messages = [{"role": "system", "content": full_system}]

    for msg in CHAT_HISTORIES[chat_id][-MAX_HISTORY_LEN:]:
        if msg["id"] == my_id:
            messages.append({"role": "assistant", "content": msg["content"]})
        else:
            messages.append({"role": "user", "content": f"[{msg['sender_name']}]: {msg['content']}"})

    if last_reply:
        prompt = (
            f"Guruhda savol/gap: \"{question}\"\n\n"
            f"{last_reply['name']} yozdi: \"{last_reply['content']}\"\n\n"
            f"Endi sen {cfg['name']} sifatida javob ber. O'sha odamning gapiga ham munosabat bildir."
        )
    else:
        prompt = question

    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": SHARED_MODEL,
        "messages": messages,
        "temperature": random.uniform(0.9, 1.15),
        "max_tokens": 150,
        "presence_penalty": 0.8,
        "frequency_penalty": 0.6,
    }

    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.post(GROQ_URL, headers=HEADERS, json=payload)
            resp.raise_for_status()
            answer = resp.json()["choices"][0]["message"]["content"].strip()
            # Sarlavhalarni tozalash
            for b in BOT_CONFIGS:
                answer = answer.replace(f"{b['name']}:", "").replace(f"[{b['id']}]:", "")
            return answer.strip(": \n*_")
    except Exception as e:
        logger.error(f"Groq xatosi [{my_id}]: {e}")
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
    current_cfg = next(
        (cfg for cfg in BOT_CONFIGS if os.getenv(cfg["token_env"]) == context.bot.token),
        None
    )
    if not current_cfg or len(question) < 3:
        return

    bot_me = await context.bot.get_me()
    should_resp, is_targeted = should_respond(question, bot_me.username, current_cfg)
    if not should_resp:
        return

    # ─── REJA TUZISH ───
    async with PLAN_LOCK:
        if msg_id not in SHARED_PLANS:
            CHAT_HISTORIES[chat_id].append({
                "id": "user",
                "sender_name": user_name,
                "content": question
            })

            lowered = question.lower()
            targeted_bot = next(
                (cfg for cfg in BOT_CONFIGS if any(kw in lowered for kw in cfg["keywords"])),
                None
            )
            if not targeted_bot and is_targeted:
                targeted_bot = current_cfg

            opponents_map = {
                "sardor":  ["kamola", "bobur"],
                "kamola":  ["zulfiya", "sardor"],
                "bobur":   ["mansur", "kamola"],
                "mansur":  ["bobur", "sardor"],
                "zulfiya": ["kamola", "bobur"],
            }

            plan = {}
            if targeted_bot:
                plan[targeted_bot["id"]] = random.uniform(0.5, 1.5)
                remaining = [b for b in BOT_CONFIGS if b["id"] != targeted_bot["id"]]
                preferred = opponents_map.get(targeted_bot["id"], [])
                opponent = next((b for b in remaining if b["id"] in preferred), random.choice(remaining))
                plan[opponent["id"]] = random.uniform(9.0, 15.0)
            else:
                selected = random.sample(BOT_CONFIGS, 2)
                plan[selected[0]["id"]] = random.uniform(0.5, 2.0)
                plan[selected[1]["id"]] = random.uniform(10.0, 16.0)

            SHARED_PLANS[msg_id] = plan
            SHARED_REPLY_IDS[msg_id] = msg_id
            SHARED_PLANS[f"{msg_id}_first_reply"] = None
            logger.info(f"Reja: {list(plan.keys())} | '{question[:40]}'")

    master_plan = SHARED_PLANS.get(msg_id, {})
    my_id = current_cfg["id"]
    if my_id not in master_plan:
        return

    await asyncio.sleep(master_plan[my_id])

    # Ikkinchi bot birinchi botning javobini oladi
    my_delay = master_plan[my_id]
    is_second = all(my_delay >= d for bid, d in master_plan.items() if bid != my_id)
    last_reply = SHARED_PLANS.get(f"{msg_id}_first_reply") if is_second else None

    # ─── ACTION TANLASH ───
    action = pick_action(my_id)
    reply_target = SHARED_REPLY_IDS.get(msg_id, msg_id)

    # Reaction — tez va jim
    if action == "reaction_only":
        await asyncio.sleep(random.uniform(0.5, 2.0))
        await add_reaction(context.bot, chat_id, reply_target, my_id)
        # Reaction qilgandan keyin tarixga qo'shmaymiz (u gap qilmadi)
        return

    # Typing indicator
    try:
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
    except Exception:
        pass

    # Uzoq o'ylash (typing_long)
    if action == "typing_long":
        await asyncio.sleep(random.uniform(3.0, 6.0))
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass
    else:
        await asyncio.sleep(random.uniform(1.0, 2.5))

    # Poll
    if action == "poll":
        sent = await send_poll_action(context.bot, chat_id, reply_target, current_cfg, question)
        if sent:
            SHARED_REPLY_IDS[msg_id] = sent.message_id
            # Reaction ham qo'shamiz
            await asyncio.sleep(random.uniform(1.0, 3.0))
            await add_reaction(context.bot, chat_id, reply_target, my_id)
        return

    # Matn javob
    answer = await ask_groq(chat_id, current_cfg, question, last_reply)
    if not answer:
        return

    try:
        sent_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=answer,
            reply_to_message_id=reply_target,
        )

        if not is_second:
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

        # Ba'zan javobdan keyin ham reaction qo'shamiz (boshqa odamning xabariga)
        if random.random() < 0.3:
            await asyncio.sleep(random.uniform(2.0, 5.0))
            await add_reaction(context.bot, chat_id, msg_id, my_id)

    except Exception as e:
        logger.error(f"[{my_id}] xabar yuborishda xato: {e}")


# ──────────────────────────────────────────────
#  Callback va Komandalar
# ──────────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    bot_username = (await context.bot.get_me()).username
    current_cfg = next(
        (cfg for cfg in BOT_CONFIGS if os.getenv(cfg["token_env"]) == context.bot.token),
        None
    )

    if query.data == "help":
        names = ", ".join(cfg["name"] for cfg in BOT_CONFIGS)
        text = (
            "📖 <b>Qo'llanma</b>\n\n"
            f"<b>Guruh a'zolari:</b> {names}\n\n"
            "<b>Chaqirish:</b> ismini yoz yoki @mention qil\n"
            "<b>Misol:</b> <code>Sardor, freelance qilsam bo'ladimi?</code>\n\n"
            "<b>Ular nima qila oladi:</b>\n"
            "💬 Javob beradi\n"
            "📊 Poll ochadi\n"
            "😂 Reaction qo'yadi\n"
            "🤔 Ba'zan uzoq o'ylab keyin javob beradi"
        )
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data="back")]])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    elif query.data == "back":
        name = current_cfg["name"] if current_cfg else "Bot"
        text = f"👋 Salom! Men <b>{name}</b>man."
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Guruhga qo'shish", url=f"https://t.me/{bot_username}?startgroup=start&admin=post_messages+delete_messages")],
            [InlineKeyboardButton("📋 Qo'llanma", callback_data="help")]
        ])
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type == "private":
        await handle_private_message(update, context)
    else:
        current_cfg = next(
            (cfg for cfg in BOT_CONFIGS if os.getenv(cfg["token_env"]) == context.bot.token),
            None
        )
        name = current_cfg["name"] if current_cfg else "Bot"
        bot_username = (await context.bot.get_me()).username
        await update.message.reply_text(
            f"Salom! Men <b>{name}</b>man. Ismimni yozib chaqir.",
            parse_mode="HTML"
        )


def build_application(config: dict):
    token = os.getenv(config["token_env"])
    if not token:
        raise ValueError(f"❌ Token topilmadi: {config['token_env']}")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", handle_start))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(
        filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
        handle_private_message
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & (filters.ChatType.GROUP | filters.ChatType.SUPERGROUP) & ~filters.COMMAND,
        handle_group_message
    ))
    return app


async def start_one_bot(app):
    """Bitta botni xavfsiz ishga tushirish"""
    await app.initialize()
    # Avval eski sessiyani butunlay o'chirish
    for attempt in range(3):
        try:
            await app.bot.delete_webhook(drop_pending_updates=True)
            await asyncio.sleep(2)
            # offset=-1 bilan eski getUpdates loop ni to'xtatish
            await app.bot.get_updates(offset=-1, timeout=2)
            break
        except Exception as e:
            logger.warning(f"Session tozalash urinish {attempt+1}: {e}")
            await asyncio.sleep(3)
    await asyncio.sleep(2)
    await app.start()
    await app.updater.start_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query", "message_reaction"],
    )
    logger.info(f"✅ Bot ishga tushdi: {app.bot.username}")


async def run_all():
    if not GROQ_API_KEY:
        logger.error("❌ GROQ_API_KEY topilmadi!")
        return
    active_configs = [cfg for cfg in BOT_CONFIGS if os.getenv(cfg["token_env"])]
    if not active_configs:
        logger.error("❌ Hech qanday token topilmadi!")
        return

    apps = [build_application(cfg) for cfg in active_configs]

    # Botlarni ketma-ket ishga tushirish — har biri orasida 3 soniya
    for i, app in enumerate(apps):
        await start_one_bot(app)
        if i < len(apps) - 1:
            await asyncio.sleep(3)

    logger.info(f"✅ Jami {len(apps)} ta bot ishga tushdi.")
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        logger.info("Botlar to'xtatilmoqda...")
        for app in apps:
            try:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
            except Exception as e:
                logger.error(f"To'xtatishda xato: {e}")


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_all())
