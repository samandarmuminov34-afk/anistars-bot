import os
import random
import string
import time
from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
    MenuButtonCommands,
    helpers,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi. FPS.ms dagi .env faylini tekshiring.")

users = {}
waiting = {}
games = {}
states = {}

anon_codes = {}
anon_links = {}
anon_sessions = {}
quick_queue = {"guess": [], "rps": [], "xo": []}


# RAM cleanup: faqat vaqtinchalik/tashlab ketilgan holatlarni tozalaydi.
CLEANUP_INTERVAL_SECONDS = 600
STALE_WAIT_SECONDS = 1800
STALE_STATE_SECONDS = 3600
STALE_ANON_SECONDS = 86400
_last_cleanup = 0.0
_state_times = {}
_queue_times = {}
_anon_times = {}

def touch_state(uid):
    _state_times[uid] = time.time()

def touch_queue(game_type, uid):
    _queue_times[(game_type, uid)] = time.time()

def touch_anon(session_code):
    _anon_times[session_code] = time.time()

def cleanup_memory(force=False):
    global _last_cleanup
    now = time.time()
    if not force and now - _last_cleanup < CLEANUP_INTERVAL_SECONDS:
        return
    _last_cleanup = now

    # Tezkor navbatdagi 30 daqiqadan eski foydalanuvchilarni chiqarish.
    for game_type, queue in quick_queue.items():
        fresh = []
        for uid in queue:
            t = _queue_times.get((game_type, uid), now)
            if now - t <= STALE_WAIT_SECONDS:
                fresh.append(uid)
            else:
                if states.get(uid, {}).get("type") == "quick_wait":
                    states.pop(uid, None)
                _queue_times.pop((game_type, uid), None)
        quick_queue[game_type] = fresh

    # Kutayotgan, lekin raqib qo‘shilmagan o‘yinlarni 30 daqiqadan keyin tozalash.
    for uid, code in list(waiting.items()):
        game = games.get(code)
        if not game or now - _state_times.get(uid, now) > STALE_WAIT_SECONDS:
            waiting.pop(uid, None)
            games.pop(code, None)
            states.pop(uid, None)
            _state_times.pop(uid, None)

    # State'larni tozalash. Faol o‘yinlarga tegmaymiz.
    for uid, state in list(states.items()):
        code = state.get("code")
        if code and code in games and games[code].get("started"):
            touch_state(uid)
            continue
        if state.get("type") in ("anonymous_send", "anonymous_reply"):
            session_code = state.get("session")
            if session_code in anon_sessions:
                touch_anon(session_code)
                continue
        if now - _state_times.get(uid, now) > STALE_STATE_SECONDS:
            states.pop(uid, None)
            _state_times.pop(uid, None)

    # Faol bo‘lmagan anonim suhbatlarni 24 soatdan keyin o‘chirish.
    for session_code, session in list(anon_sessions.items()):
        if not session.get("active") or now - _anon_times.get(session_code, now) > STALE_ANON_SECONDS:
            anon_sessions.pop(session_code, None)
            _anon_times.pop(session_code, None)

    # Yordamchi timestamp lug‘atlarini ham kichraytirish.
    for uid in list(_state_times):
        if uid not in states:
            _state_times.pop(uid, None)
    for key in list(_queue_times):
        game_type, uid = key
        if uid not in quick_queue.get(game_type, []):
            _queue_times.pop(key, None)



def get_user(user):
    uid = user.id
    if uid not in users:
        users[uid] = {
            "name": user.first_name or "Foydalanuvchi",
            "games": 0,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "rating": 1000,
        }
    else:
        users[uid]["name"] = user.first_name or users[uid]["name"]
    return users[uid]


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 O‘yinlar", callback_data="games")],
        [InlineKeyboardButton("👤 Profil", callback_data="profile")],
        [InlineKeyboardButton("🏆 Reyting", callback_data="rating")],
        [InlineKeyboardButton("🕵️ Anonim xabar", callback_data="anonymous")],
    ])


def games_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Tezkor raqib", callback_data="quick_menu")],
        [InlineKeyboardButton("🎯 Sonni top", callback_data="game_guess")],
        [InlineKeyboardButton("🪨✂️📄 Tosh-Qaychi-Qog‘oz", callback_data="game_rps")],
        [InlineKeyboardButton("❌⭕ X va O", callback_data="game_xo")],
        [InlineKeyboardButton("🏠 Bosh sahifa", callback_data="home")],
    ])


def quick_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎯 Sonni top", callback_data="quick_guess")],
        [InlineKeyboardButton("🪨✂️📄 Tosh-Qaychi-Qog‘oz", callback_data="quick_rps")],
        [InlineKeyboardButton("❌⭕ X va O", callback_data="quick_xo")],
        [InlineKeyboardButton("🎮 O‘yinlarga qaytish", callback_data="games")],
    ])


def anonymous_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Mening anonim havolam", callback_data="anon_link")],
        [InlineKeyboardButton("🏠 Bosh sahifa", callback_data="home")],
    ])


async def anonymous_link_text(uid, context):
    bot = await context.bot.get_me()
    code = anon_codes.get(uid)
    if not code:
        code = "".join(random.choices(string.ascii_letters + string.digits, k=12))
        while code in anon_links:
            code = "".join(random.choices(string.ascii_letters + string.digits, k=12))
        anon_codes[uid] = code
        anon_links[code] = uid

    link = helpers.create_deep_linked_url(bot.username, f"anon_{code}")
    return (
        "🕵️ SIZNING ANONIM HAVOLANGIZ\n\n"
        "Quyidagi havolani do‘stlaringizga yuboring. "
        "Ular sizga kimligini ko‘rsatmasdan xabar yuborishi mumkin.\n\n"
        f"🔗 {link}\n\n"
        "⚠️ Qabul qiluvchi yuboruvchining ismi va Telegram ID'sini ko‘rmaydi."
    )


async def show_anonymous(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🕵️ ANONIM XABARLAR\n\n"
        "Siz uchun maxsus anonim havola yarating. "
        "Havolani boshqalarga yuboring — ular sizga anonim xabar yubora oladi.",
        reply_markup=anonymous_menu(),
    )


async def show_anonymous_link(update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    await query.edit_message_text(
        await anonymous_link_text(uid, context),
        reply_markup=anonymous_menu(),
    )


async def start_anonymous_message(update, context, code):
    uid = update.effective_user.id
    recipient_id = anon_links.get(code)

    if not recipient_id:
        await update.message.reply_text(
            "❌ Bu anonim havola yaroqsiz yoki mavjud emas.",
            reply_markup=main_menu(),
        )
        return

    if recipient_id == uid:
        await update.message.reply_text(
            "😄 O‘zingizga anonim xabar yubora olmaysiz.",
            reply_markup=main_menu(),
        )
        return

    session_code = "".join(random.choices(string.ascii_letters + string.digits, k=12))
    while session_code in anon_sessions:
        session_code = "".join(random.choices(string.ascii_letters + string.digits, k=12))
    anon_sessions[session_code] = {"a": recipient_id, "b": uid, "active": True}
    states[uid] = {"type": "anonymous_send", "recipient": recipient_id, "session": session_code}
    touch_state(uid)
    touch_anon(session_code)
    await update.message.reply_text(
        "🕵️ ANONIM XABAR\n\n"
        "Xabaringizni yozing va yuboring.\n"
        "Sizning ismingiz qabul qiluvchiga ko‘rsatilmaydi.\n\n"
        "❌ Bekor qilish uchun /start bosing."
    )


def back_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎮 O‘yinlar", callback_data="games")],
        [InlineKeyboardButton("🏠 Bosh sahifa", callback_data="home")],
    ])


def make_code():
    while True:
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if code not in games and code not in waiting:
            return code


async def setup_commands(app):
    await app.bot.set_my_commands([
        BotCommand("start", "🏠 Bosh sahifa"),
        BotCommand("games", "🎮 O‘yinlar"),
        BotCommand("profile", "👤 Profil"),
    ])
    await app.bot.set_chat_menu_button(menu_button=MenuButtonCommands())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    get_user(user)
    states.pop(user.id, None)

    if context.args:
        payload = context.args[0]
        if payload.startswith("anon_"):
            await start_anonymous_message(update, context, payload[5:])
            return

    await update.message.reply_text(
        f"⭐ Anistars'ga xush kelibsiz, {user.first_name or 'do‘st'}!\n\n"
        "Quyidagi menyudan kerakli bo‘limni tanlang.",
        reply_markup=main_menu(),
    )


async def games_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    get_user(update.effective_user)
    states.pop(update.effective_user.id, None)
    await update.message.reply_text(
        "🎮 O‘yinlar bo‘limi:",
        reply_markup=games_menu(),
    )


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user)
    await update.message.reply_text(
        f"👤 Profil\n\n"
        f"🧑 Ism: {user['name']}\n"
        f"🎮 O‘yinlar: {user['games']}\n"
        f"🏆 G‘alabalar: {user['wins']}\n"
        f"❌ Mag‘lubiyatlar: {user['losses']}\n"
        f"🤝 Duranglar: {user['draws']}\n"
        f"⭐ Reyting: {user.get('rating', 1000)}",
        reply_markup=back_menu(),
    )


async def rating_text():
    if not users:
        return "🏆 REYTING\n\nHozircha o‘yinchilar yo‘q."

    ranked = sorted(
        users.items(),
        key=lambda item: (item[1].get("rating", 1000), item[1].get("wins", 0)),
        reverse=True,
    )

    lines = ["🏆 ANISTARS REYTINGI", ""]
    medals = ["🥇", "🥈", "🥉"]

    for i, (uid, user) in enumerate(ranked[:10], start=1):
        prefix = medals[i - 1] if i <= 3 else f"{i}."
        lines.append(
            f"{prefix} {user.get('name', 'Foydalanuvchi')} — "
            f"⭐ {user.get('rating', 1000)}"
        )

    return "\n".join(lines)


async def show_rating(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        await rating_text(),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Yangilash", callback_data="rating")],
            [InlineKeyboardButton("🏠 Bosh sahifa", callback_data="home")],
        ]),
    )


async def show_game_info(update: Update, context: ContextTypes.DEFAULT_TYPE, game_type):
    query = update.callback_query
    await query.answer()

    descriptions = {
        "guess": (
            "🎯 SONNI TOP\n\n"
            "2 kishi o‘ynaydi.\n"
            "1. Har ikkala o‘yinchi 1–100 oralig‘ida yashirin son tanlaydi.\n"
            "2. Navbat bilan raqib sonini topasiz.\n"
            "3. Birinchi bo‘lib to‘g‘ri topgan o‘yinchi g‘olib.\n\n"
            "O‘yin yaratish uchun tugmani bosing."
        ),
        "rps": (
            "🪨✂️📄 TOSH-QAYCHI-QOG‘OZ\n\n"
            "2 kishi o‘ynaydi.\n"
            "Tosh > Qaychi\n"
            "Qaychi > Qog‘oz\n"
            "Qog‘oz > Tosh\n\n"
            "O‘yin yaratish uchun tugmani bosing."
        ),
        "xo": (
            "❌⭕ X VA O\n\n"
            "2 kishi o‘ynaydi.\n"
            "❌ birinchi yuradi.\n"
            "3×3 maydonda ketma-ket 3 ta belgi qo‘ygan o‘yinchi g‘olib.\n\n"
            "O‘yin yaratish uchun tugmani bosing."
        ),
    }

    key = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ O‘yin yaratish", callback_data=f"create_{game_type}")],
        [InlineKeyboardButton("🔑 Kod bilan kirish", callback_data=f"join_{game_type}")],
        [InlineKeyboardButton("🎮 O‘yinlarga qaytish", callback_data="games")],
    ])

    await query.edit_message_text(descriptions[game_type], reply_markup=key)


def game_buttons(game_type):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Yangi o‘yin", callback_data=f"create_{game_type}")],
        [InlineKeyboardButton("🎮 O‘yinlar", callback_data="games")],
    ])


async def create_game(update: Update, context: ContextTypes.DEFAULT_TYPE, game_type):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    code = make_code()
    games[code] = {
        "type": game_type,
        "host": uid,
        "guest": None,
        "started": False,
        "data": {},
    }
    waiting[uid] = code
    states[uid] = {"type": "waiting_join", "code": code, "game_type": game_type}
    touch_state(uid)

    await query.edit_message_text(
        f"✅ O‘yin yaratildi!\n\n"
        f"🔑 O‘yin kodi: `{code}`\n\n"
        "Raqibingizga shu kodni yuboring.\n"
        "Raqib kod orqali kirgach, o‘yin boshlanadi.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ O‘yinni bekor qilish", callback_data="cancel_game")],
            [InlineKeyboardButton("🎮 O‘yinlar", callback_data="games")],
        ]),
    )


async def request_join(update: Update, context: ContextTypes.DEFAULT_TYPE, game_type):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    states[uid] = {"type": "enter_code", "game_type": game_type}

    await query.edit_message_text(
        "🔑 O‘yin kodini yuboring.\n\n"
        "Masalan: `A1B2C3`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🎮 O‘yinlarga qaytish", callback_data="games")]
        ]),
    )


async def quick_match(update: Update, context: ContextTypes.DEFAULT_TYPE, game_type):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    queue = quick_queue[game_type]

    # Foydalanuvchi boshqa tezkor navbatda bo‘lsa, uni olib tashlaymiz.
    for queued_type in quick_queue:
        quick_queue[queued_type] = [x for x in quick_queue[queued_type] if x != uid]

    # Navbatda raqib bor bo‘lsa, darhol juftlaymiz.
    opponent = queue.pop(0) if queue else None
    if opponent is not None and opponent != uid:
        code = make_code()
        games[code] = {
            "type": game_type,
            "host": opponent,
            "guest": uid,
            "started": False,
            "data": {},
        }
        states.pop(opponent, None)
        states.pop(uid, None)

        await context.bot.send_message(opponent, "⚡ Raqib topildi! O‘yin boshlanmoqda...")
        await context.bot.send_message(uid, "⚡ Raqib topildi! O‘yin boshlanmoqda...")
        await start_game(code, context)
        return

    queue.append(uid)
    states[uid] = {"type": "quick_wait", "game_type": game_type}
    touch_state(uid)
    touch_queue(game_type, uid)

    names = {
        "guess": "🎯 Sonni top",
        "rps": "🪨✂️📄 Tosh-Qaychi-Qog‘oz",
        "xo": "❌⭕ X va O",
    }
    await query.edit_message_text(
        f"⚡ TEZKOR RAQIB\n\n"
        f"🎮 O‘yin: {names[game_type]}\n\n"
        "🔎 Raqib qidirilmoqda...\n"
        "Raqib topilishi bilan o‘yin avtomatik boshlanadi.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Qidiruvni bekor qilish", callback_data="quick_cancel")],
            [InlineKeyboardButton("🎮 O‘yinlarga qaytish", callback_data="games")],
        ]),
    )


async def show_quick_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "⚡ TEZKOR RAQIB\n\n"
        "O‘yin turini tanlang. Bot sizga avtomatik ravishda raqib topadi.",
        reply_markup=quick_menu_keyboard(),
    )


async def cancel_quick_match(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    removed = False
    for game_type in quick_queue:
        if uid in quick_queue[game_type]:
            quick_queue[game_type] = [x for x in quick_queue[game_type] if x != uid]
            removed = True

    states.pop(uid, None)
    await query.edit_message_text(
        "❌ Raqib qidiruvi bekor qilindi." if removed else "❌ Raqib qidiruvi faol emas.",
        reply_markup=games_menu(),
    )


async def start_game(code, context):
    game = games.get(code)
    if not game or not game["guest"]:
        return

    game["started"] = True
    game_type = game["type"]
    host = game["host"]
    guest = game["guest"]

    get_user(context.bot_data.get("_dummy_user", type("U", (), {"id": host, "first_name": "Player"})()))

    if game_type == "guess":
        game["data"] = {
            "secrets": {},
            "turn": host,
            "guesses": {},
        }
        states[host] = {"type": "guess_secret", "code": code}
        states[guest] = {"type": "guess_secret", "code": code}
        touch_state(host)
        touch_state(guest)

        text = (
            "🎯 O‘YIN BOSHLANDI!\n\n"
            "Ikkalangiz ham 1–100 oralig‘ida yashirin son tanlang.\n"
            "Sonni oddiy xabar sifatida yuboring."
        )
        await context.bot.send_message(host, text)
        await context.bot.send_message(guest, text)

    elif game_type == "rps":
        game["data"] = {"choices": {}}
        states[host] = {"type": "rps", "code": code}
        states[guest] = {"type": "rps", "code": code}
        touch_state(host)
        touch_state(guest)

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🪨 Tosh", callback_data=f"rps_{code}_rock"),
                InlineKeyboardButton("✂️ Qaychi", callback_data=f"rps_{code}_scissors"),
            ],
            [InlineKeyboardButton("📄 Qog‘oz", callback_data=f"rps_{code}_paper")],
        ])
        text = "🪨✂️📄 O‘yin boshlandi!\n\nTanlovingizni yashirincha tanlang:"
        await context.bot.send_message(host, text, reply_markup=keyboard)
        await context.bot.send_message(guest, text, reply_markup=keyboard)

    elif game_type == "xo":
        game["data"] = {
            "board": [" "] * 9,
            "turn": host,
            "marks": {host: "❌", guest: "⭕"},
            "messages": {},
        }
        states[host] = {"type": "xo", "code": code}
        states[guest] = {"type": "xo", "code": code}
        touch_state(host)
        touch_state(guest)
        await send_xo_board(code, context)


async def join_game(uid, code, game_type, context):
    code = code.strip().upper()
    game = games.get(code)

    if not game:
        return False, "❌ Bunday o‘yin kodi topilmadi."

    if game["type"] != game_type:
        return False, "❌ Bu kod boshqa turdagi o‘yinga tegishli."

    if game["host"] == uid:
        return False, "❌ Siz o‘zingiz yaratgan o‘yinga kira olmaysiz."

    if game["guest"]:
        return False, "❌ Bu o‘yinda allaqachon 2 ta o‘yinchi bor."

    game["guest"] = uid
    waiting.pop(game["host"], None)
    states.pop(uid, None)
    states.pop(game["host"], None)

    await context.bot.send_message(
        game["host"],
        "🎉 Raqib o‘yinga qo‘shildi! O‘yin boshlanmoqda..."
    )
    await context.bot.send_message(
        uid,
        "🎉 Siz o‘yinga qo‘shildingiz!"
    )
    await start_game(code, context)

    return True, "OK"


def check_xo(board):
    lines = [
        (0, 1, 2), (3, 4, 5), (6, 7, 8),
        (0, 3, 6), (1, 4, 7), (2, 5, 8),
        (0, 4, 8), (2, 4, 6),
    ]

    for a, b, c in lines:
        if board[a] != " " and board[a] == board[b] == board[c]:
            return board[a]

    if " " not in board:
        return "draw"

    return None


def xo_keyboard(board):
    buttons = []
    for row in range(3):
        line = []
        for col in range(3):
            index = row * 3 + col
            value = board[index]
            text = value if value != " " else str(index + 1)
            line.append(InlineKeyboardButton(text, callback_data=f"xo_move_{index}"))
        buttons.append(line)

    return InlineKeyboardMarkup(buttons)


async def send_xo_board(code, context):
    game = games.get(code)
    if not game:
        return

    board = game["data"]["board"]
    turn = game["data"]["turn"]
    mark = game["data"]["marks"][turn]

    text = (
        "❌⭕ X VA O\n\n"
        "Navbat: " + mark + "\n\n"
        "Maydon raqamlari:\n"
        "1️⃣ 2️⃣ 3️⃣\n"
        "4️⃣ 5️⃣ 6️⃣\n"
        "7️⃣ 8️⃣ 9️⃣"
    )

    messages = game["data"].setdefault("messages", {})

    for uid in (game["host"], game["guest"]):
        if uid in messages:
            try:
                await context.bot.edit_message_text(
                    chat_id=uid,
                    message_id=messages[uid],
                    text=text,
                    reply_markup=xo_keyboard(board),
                )
            except Exception as e:
                if "Message is not modified" not in str(e):
                    print(f"XO edit error: {e}")
        else:
            message = await context.bot.send_message(
                uid,
                text,
                reply_markup=xo_keyboard(board),
            )
            messages[uid] = message.message_id


async def finish_game(code, winner_id, loser_id, context, draw=False):
    game = games.get(code)
    if not game:
        return

    players = [game["host"], game["guest"]]

    for uid in players:
        if uid is not None:
            users.setdefault(uid, {
                "name": "Foydalanuvchi",
                "games": 0,
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "rating": 1000,
            })
            users[uid]["games"] += 1

    if draw:
        for uid in players:
            users[uid]["draws"] += 1
            users[uid]["rating"] = users[uid].get("rating", 1000) + 5
        result = "🤝 O‘yin durang bilan tugadi!"
    else:
        users[winner_id]["wins"] += 1
        users[loser_id]["losses"] += 1
        users[winner_id]["rating"] = users[winner_id].get("rating", 1000) + 20
        users[loser_id]["rating"] = max(0, users[loser_id].get("rating", 1000) - 10)
        result = "🏆 G‘olib: siz!"

    for uid in players:
        if uid is None:
            continue
        if draw:
            personal = "🤝 Siz bilan raqib durang qildingiz!"
        elif uid == winner_id:
            personal = result
        else:
            personal = "❌ Siz yutqazdingiz."

        if game["type"] == "xo" and uid in game["data"].get("messages", {}):
            board = game["data"]["board"]
            if draw:
                final_text = "❌⭕ X VA O\n\n🤝 DURANG!\n\nO‘yin tugadi."
            else:
                mark = game["data"]["marks"][winner_id]
                if uid == winner_id:
                    final_text = f"❌⭕ X VA O\n\n🏆 SIZ G‘OLIB BO‘LDINGIZ! {mark}\n\nO‘yin tugadi."
                else:
                    final_text = f"❌⭕ X VA O\n\n❌ SIZ YUTQAZDINGIZ.\n\nG‘olib: {mark}\nO‘yin tugadi."
            try:
                await context.bot.edit_message_text(
                    chat_id=uid,
                    message_id=game["data"]["messages"][uid],
                    text=final_text,
                    reply_markup=game_buttons("xo"),
                )
            except Exception as e:
                if "Message is not modified" not in str(e):
                    print(f"XO finish edit error: {e}")
        else:
            await context.bot.send_message(
                uid,
                f"{personal}\n\n🎮 O‘yin tugadi.",
                reply_markup=game_buttons(game["type"]),
            )
        states.pop(uid, None)

    games.pop(code, None)


async def handle_rps_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    parts = query.data.split("_", 2)
    if len(parts) != 3:
        return

    code = parts[1]
    choice = parts[2]
    uid = query.from_user.id
    game = games.get(code)

    if not game or not game["started"] or game["type"] != "rps":
        await query.edit_message_text("❌ Bu o‘yin mavjud emas.")
        return

    if uid not in (game["host"], game["guest"]):
        return

    choices = game["data"]["choices"]

    if uid in choices:
        await query.answer("Siz allaqachon tanlagansiz.", show_alert=True)
        return

    choices[uid] = choice
    await query.edit_message_text("✅ Tanlovingiz qabul qilindi. Raqibingizni kuting...")

    if len(choices) != 2:
        return

    host = game["host"]
    guest = game["guest"]
    a = choices[host]
    b = choices[guest]

    if a == b:
        await finish_game(code, None, None, context, draw=True)
        return

    wins = {
        "rock": "scissors",
        "scissors": "paper",
        "paper": "rock",
    }

    winner_id = host if wins[a] == b else guest
    loser_id = guest if winner_id == host else host

    await finish_game(code, winner_id, loser_id, context)


async def handle_xo_move(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        index = int(query.data.split("_")[-1])
    except (ValueError, IndexError):
        return

    uid = query.from_user.id
    state = states.get(uid)

    if not state or state.get("type") != "xo":
        await query.answer("Faol X va O o‘yini topilmadi.", show_alert=True)
        return

    code = state["code"]
    game = games.get(code)

    if not game or not game["started"]:
        await query.answer("O‘yin tugagan.", show_alert=True)
        return

    if game["data"]["turn"] != uid:
        await query.answer("Hozir navbat sizniki emas.", show_alert=True)
        return

    board = game["data"]["board"]

    if board[index] != " ":
        await query.answer("Bu joy band.", show_alert=True)
        return

    mark = game["data"]["marks"][uid]
    board[index] = mark

    result = check_xo(board)

    if result:
        if result == "draw":
            await finish_game(code, None, None, context, draw=True)
        else:
            winner_id = uid
            loser_id = game["guest"] if uid == game["host"] else game["host"]
            await finish_game(code, winner_id, loser_id, context)
        return

    game["data"]["turn"] = (
        game["guest"] if uid == game["host"] else game["host"]
    )

    await send_xo_board(code, context)


async def cancel_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    for game_type in quick_queue:
        quick_queue[game_type] = [x for x in quick_queue[game_type] if x != uid]

    code = waiting.pop(uid, None)
    if code:
        games.pop(code, None)

    state = states.pop(uid, None)
    if not code and state:
        code = state.get("code")
        if code:
            games.pop(code, None)

    await query.edit_message_text(
        "❌ O‘yin bekor qilindi.",
        reply_markup=games_menu(),
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleanup_memory()
    query = update.callback_query
    data = query.data
    uid = query.from_user.id
    get_user(query.from_user)

    if data == "home":
        states.pop(uid, None)
        await query.answer()
        await query.edit_message_text(
            "🏠 Anistars bosh sahifasi",
            reply_markup=main_menu(),
        )
        return

    if data == "quick_menu":
        states.pop(uid, None)
        await show_quick_menu(update, context)
        return

    if data.startswith("quick_") and data != "quick_menu" and data != "quick_cancel":
        game_type = data[len("quick_"):]
        if game_type in quick_queue:
            await quick_match(update, context, game_type)
        return

    if data == "quick_cancel":
        await cancel_quick_match(update, context)
        return

    if data == "games":
        states.pop(uid, None)
        await query.answer()
        await query.edit_message_text(
            "🎮 O‘yinlar bo‘limi:",
            reply_markup=games_menu(),
        )
        return

    if data == "profile":
        await query.answer()
        user = get_user(query.from_user)
        await query.edit_message_text(
            f"👤 Profil\n\n"
            f"🧑 Ism: {user['name']}\n"
            f"🎮 O‘yinlar: {user['games']}\n"
            f"🏆 G‘alabalar: {user['wins']}\n"
            f"❌ Mag‘lubiyatlar: {user['losses']}\n"
            f"🤝 Duranglar: {user['draws']}",
            reply_markup=back_menu(),
        )
        return

    if data == "rating":
        await show_rating(update, context)
        return

    if data == "anonymous":
        await show_anonymous(update, context)
        return

    if data == "anon_link":
        await show_anonymous_link(update, context)
        return

    if data.startswith("anon_reply_"):
        session_code = data[len("anon_reply_"):]
        session = anon_sessions.get(session_code)
        if not session or not session.get("active") or uid not in (session["a"], session["b"]):
            await query.answer("❌ Bu suhbat tugagan yoki mavjud emas.", show_alert=True)
            return
        states[uid] = {"type": "anonymous_reply", "session": session_code}
        touch_state(uid)
        touch_anon(session_code)
        await query.answer()
        await query.message.reply_text(
            "↩️ ANONIM JAVOB\n\n✍️ Javobingizni yozing.\n\n❌ Bekor qilish uchun /start bosing."
        )
        return

    if data.startswith("anon_close_"):
        session_code = data[len("anon_close_"):]
        session = anon_sessions.get(session_code)
        if not session or uid not in (session["a"], session["b"]):
            await query.answer("❌ Suhbat topilmadi.", show_alert=True)
            return
        session["active"] = False
        states.pop(session["a"], None)
        states.pop(session["b"], None)
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("🚫 Anonim suhbat tugatildi.", reply_markup=main_menu())
        return

    if data == "cancel_game":
        await cancel_game(update, context)
        return

    if data == "game_guess":
        await show_game_info(update, context, "guess")
        return

    if data == "game_rps":
        await show_game_info(update, context, "rps")
        return

    if data == "game_xo":
        await show_game_info(update, context, "xo")
        return

    if data.startswith("create_"):
        game_type = data.split("_", 1)[1]
        await create_game(update, context, game_type)
        return

    if data.startswith("join_"):
        game_type = data.split("_", 1)[1]
        await request_join(update, context, game_type)
        return

    if data.startswith("rps_"):
        await handle_rps_choice(update, context)
        return

    if data.startswith("xo_move_"):
        await handle_xo_move(update, context)
        return


async def handle_guess_message(uid, text, context):
    state = states.get(uid)
    if not state or state.get("type") != "guess_secret":
        return False

    code = state["code"]
    game = games.get(code)

    if not game or game["type"] != "guess":
        return False

    try:
        number = int(text.strip())
    except ValueError:
        await context.bot.send_message(uid, "❌ Faqat 1–100 oralig‘idagi son yuboring.")
        return True

    if not 1 <= number <= 100:
        await context.bot.send_message(uid, "❌ Son 1–100 oralig‘ida bo‘lishi kerak.")
        return True

    secrets = game["data"]["secrets"]
    if uid in secrets:
        await context.bot.send_message(uid, "❌ Siz sonni allaqachon tanlagansiz.")
        return True

    secrets[uid] = number
    await context.bot.send_message(uid, "✅ Yashirin soningiz qabul qilindi.")

    if len(secrets) < 2:
        return True

    host = game["host"]
    guest = game["guest"]
    states[host] = {"type": "guess_turn", "code": code}
    states[guest] = {"type": "guess_wait", "code": code}
    touch_state(host)
    touch_state(guest)
    game["data"]["turn"] = host

    await context.bot.send_message(
        host,
        "🎯 Birinchi navbat sizniki.\n"
        "Raqibingizning yashirin sonini topish uchun 1–100 oralig‘idagi sonni yuboring."
    )
    await context.bot.send_message(
        guest,
        "⏳ Raqibingizning navbatini kuting."
    )
    return True


async def handle_guess_turn(uid, text, context):
    state = states.get(uid)
    if not state or state.get("type") not in ("guess_turn", "guess_wait"):
        return False

    code = state["code"]
    game = games.get(code)

    if not game or game["type"] != "guess":
        return False

    if game["data"]["turn"] != uid:
        await context.bot.send_message(uid, "⏳ Hozir navbat sizniki emas.")
        return True

    try:
        guess = int(text.strip())
    except ValueError:
        await context.bot.send_message(uid, "❌ 1–100 oralig‘idagi son yuboring.")
        return True

    if not 1 <= guess <= 100:
        await context.bot.send_message(uid, "❌ Son 1–100 oralig‘ida bo‘lishi kerak.")
        return True

    opponent = game["guest"] if uid == game["host"] else game["host"]
    secret = game["data"]["secrets"][opponent]

    if guess == secret:
        await finish_game(code, uid, opponent, context)
        return True

    if guess < secret:
        hint = "⬆️ Yuqoriroq son."
    else:
        hint = "⬇️ Pastroq son."

    await context.bot.send_message(uid, f"❌ Topilmadi. {hint}")

    states[uid] = {"type": "guess_wait", "code": code}
    states[opponent] = {"type": "guess_turn", "code": code}
    touch_state(uid)
    touch_state(opponent)
    game["data"]["turn"] = opponent

    await context.bot.send_message(
        opponent,
        "🎯 Navbat sizniki!\n"
        "Raqibingizning yashirin sonini topish uchun 1–100 oralig‘ida son yuboring."
    )
    return True


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cleanup_memory()
    if not update.message or not update.message.text:
        return

    uid = update.effective_user.id
    get_user(update.effective_user)
    text = update.message.text.strip()

    state = states.get(uid)

    if state and state.get("type") == "anonymous_reply":
        session_code = state.get("session")
        session = anon_sessions.get(session_code)
        if not session or not session.get("active") or uid not in (session["a"], session["b"]):
            states.pop(uid, None)
            await update.message.reply_text("❌ Bu anonim suhbat tugatilgan.", reply_markup=main_menu())
            return

        opponent = session["b"] if uid == session["a"] else session["a"]
        try:
            await context.bot.send_message(
                opponent,
                "🕵️ Sizga anonim javob keldi!\n\n"
                f"💬 {text}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ Javob berish", callback_data=f"anon_reply_{session_code}")],
                    [InlineKeyboardButton("🚫 Suhbatni tugatish", callback_data=f"anon_close_{session_code}")],
                ]),
            )
            touch_anon(session_code)
            await update.message.reply_text("✅ Anonim javob yuborildi! 🕵️")
        except Exception:
            await update.message.reply_text("❌ Javobni yuborib bo‘lmadi.", reply_markup=main_menu())
        states.pop(uid, None)
        return

    if state and state.get("type") == "anonymous_send":
        recipient_id = state.get("recipient")
        if not recipient_id:
            states.pop(uid, None)
            await update.message.reply_text("❌ Anonim xabar sessiyasi tugagan.", reply_markup=main_menu())
            return

        session_code = state.get("session")
        session = anon_sessions.get(session_code)
        if not session or not session.get("active"):
            states.pop(uid, None)
            await update.message.reply_text("❌ Bu anonim suhbat tugatilgan.", reply_markup=main_menu())
            return

        try:
            await context.bot.send_message(
                recipient_id,
                "🕵️ Sizga yangi anonim xabar keldi!\n\n"
                f"💬 {text}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("↩️ Javob berish", callback_data=f"anon_reply_{session_code}")],
                    [InlineKeyboardButton("🚫 Suhbatni tugatish", callback_data=f"anon_close_{session_code}")],
                ]),
            )
            touch_anon(session_code)
            await update.message.reply_text(
                "✅ Xabaringiz anonim tarzda yuborildi! 🕵️"
            )
        except Exception:
            await update.message.reply_text(
                "❌ Xabarni yuborib bo‘lmadi. Qabul qiluvchi botni bloklagan bo‘lishi mumkin.",
                reply_markup=main_menu(),
            )
        states.pop(uid, None)
        return

    if not state:
        await update.message.reply_text(
            "🏠 Menyudan bo‘lim tanlang:",
            reply_markup=main_menu(),
        )
        return

    state_type = state.get("type")

    if state_type == "enter_code":
        game_type = state["game_type"]
        ok, message = await join_game(uid, text, game_type, context)
        if not ok:
            await update.message.reply_text(message)
        return

    if state_type == "guess_secret":
        await handle_guess_message(uid, text, context)
        return

    if state_type in ("guess_turn", "guess_wait"):
        await handle_guess_turn(uid, text, context)
        return

    if state_type == "waiting_join":
        await update.message.reply_text(
            "⏳ Raqibingizni kuting.\n"
            "O‘yin kodini unga yuboring."
        )
        return

    if state_type == "rps":
        await update.message.reply_text(
            "🪨✂️📄 Tanlovni tugmalar orqali bosing."
        )
        return

    if state_type == "xo":
        await update.message.reply_text(
            "❌⭕ X va O o‘yinida yurish uchun maydondagi tugmani bosing."
        )
        return


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"BOT ERROR: {context.error}")


def main():
    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(setup_commands)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("games", games_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))
    application.add_error_handler(error_handler)

    print("Anistars bot is running...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
