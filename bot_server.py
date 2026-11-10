import os
import time
import logging
import random
import io
import string
import re
import hmac
import hashlib
import json
from datetime import datetime, timedelta, timezonea
from threading import Thread
from typing import Tuple, Optional
from contextlib import suppress

import requests
import telebot
from telebot.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeChat,
    Message,
    CallbackQuery
)
from telebot.apihelper import ApiTelegramException
from pymongo import MongoClient
from bson.objectid import ObjectId
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, request, jsonify, Response, render_template_string, stream_with_context, redirect
import asyncio
import queue
from threading import Event
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import PeerChannel, InputMessagesFilterPhotos

# --- LOGGING SETUP ---
logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.ERROR)
logging.getLogger('apscheduler').setLevel(logging.ERROR)
logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('telethon').setLevel(logging.ERROR)
logging.getLogger('urllib3').setLevel(logging.ERROR)
logging.getLogger('TeleBot').setLevel(logging.CRITICAL)
telebot.logger.setLevel(logging.CRITICAL)

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "8626442584:AAHlcMntaXMjOj8ftG0tTPrNlAMR1cWrF9o")
MONGO_URI = os.getenv("MONGO_URI", "mongodb+srv://sibaram2920_db_user:hunter123@cluster0.rurpj7u.mongodb.net/sub_management?appName=Cluster0")
ADMIN_ID = int(os.getenv("ADMIN_ID", 8307246037))
ADMIN_PHONE = os.getenv("ADMIN_PHONE", "7735021399")
CONTACT_URL = os.getenv("CONTACT_URL", "https://t.me/NovelXplainerGroup01/1761")
PORT = int(os.environ.get("PORT", 5000))
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "rzp_live_TxG7eNfaYfo2dv")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "X0AynKe7isrpUQMcheFfSh34")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

# --- TELETHON MTPROTO & WEBSITE CONFIGURATION ---
API_ID = int(os.getenv("API_ID", 32872867))
API_HASH = os.getenv("API_HASH", "96b1fe91330b790401f0beadf39bfbcc")
WEB_BASE_URL = os.getenv("WEB_BASE_URL", f"http://localhost:{PORT}").rstrip("/")

PUBLIC_CHANNEL_USERNAME = "@NovelXplainer01"
PUBLIC_CHANNEL_LINK = "https://t.me/NovelXplainer01"
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "-1003990684133"))
TRACKING_CHANNEL_ID = -1003906576102  # Fallback Tracking Channel
FORUM_GROUP_ID = int(os.getenv("FORUM_GROUP_ID", "-1004398695870"))  # Group for 1-to-1 User Topics
NOVEL_GROUP_ID = int(os.getenv("NOVEL_GROUP_ID", "-1004431739408"))  # Group where Audio/Novels are stored
IST = timezone(timedelta(hours=5, minutes=30), "IST")

# --- INITIALIZATION ---
INSTANCE_ID = f"{os.getpid()}_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
client = MongoClient(MONGO_URI)


class ConflictSafeExceptionHandler(telebot.ExceptionHandler):
    def handle(self, exception):
        err_str = str(exception)
        if "409" in err_str or "Conflict" in err_str:
            with suppress(Exception):
                lock_doc = client["sub_management"]["config"].find_one({"_id": "bot_poller_lock"}) or {}
                if lock_doc.get("instance_id") and lock_doc.get("instance_id") != INSTANCE_ID:
                    bot.stop_polling()
                    return True
            time.sleep(4)
            return True
        return True


bot = telebot.TeleBot(BOT_TOKEN, exception_handler=ConflictSafeExceptionHandler())
db = client["sub_management"]

collections = {
    "channels": db["channels"],
    "users": db["users"],
    "protected": db["protected_msgs"],
    "novels": db["novels"],
    "novel_audios": db["novel_audios"],
    "payments": db["payments"],
    "config": db["config"],
    "session": db["sessions"],
    "click_history": db["click_history"],
    "reviews": db["reviews"]
}

# --- SERVER & LIVE RAZORPAY WEBHOOK + WEBSITE & MTPROTO HYBRID STREAMING (v5) ---
app = Flask(__name__)

# --- GOOGLE OAUTH SETUP ---
GOOGLE_CLIENT_ID = "aapka_google_client_id_yahan_daalein"
GOOGLE_CLIENT_SECRET = "aapka_google_client_secret_yahan_daalein"
GOOGLE_REDIRECT_URI = "https://novelexplainer-audio.onrender.com/auth/google/callback"

@app.route('/auth/google')
def google_login():
    google_url = f"https://accounts.google.com/o/oauth2/v2/auth?client_id={GOOGLE_CLIENT_ID}&redirect_uri={GOOGLE_REDIRECT_URI}&response_type=code&scope=email profile"
    return redirect(google_url)

@app.route('/auth/google/callback')
def google_callback():
    import requests
    code = request.args.get('code')
    if not code:
        return redirect("/?error=google_auth_failed")
    
    token_resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": GOOGLE_REDIRECT_URI
    }).json()

    access_token = token_resp.get("access_token")
    if not access_token:
        return redirect("/?error=token_failed")

    user_info = requests.get(
        "https://www.googleapis.com/oauth2/v2/userinfo", 
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    real_email = user_info.get("email")
    real_name = user_info.get("name", "Google User")
    if not real_email:
        return redirect("/?error=no_email")

    generated_uid = abs(hash(real_email)) % (10**9)

    collections["users"].update_one(
        {"user_id": generated_uid},
        {"$set": {
            "phone": real_email,
            "account_name": real_name,
            "auth_method": "google",
            "last_seen": time.time()
        }},
        upsert=True
    )

    return redirect(f"/?login_success=1&method=google&email={real_email}&name={real_name}&uid={generated_uid}")

WEB_HTML_Candidates = ("index.html", "index (12).html", "index-v8_compact.html", "index-v7_pocketfm.html", "index-v6_pocketfm.html")

def load_external_web_html() -> str:
    """Loads the external index.html file dynamically so UI edits take effect immediately without restarting Python."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    for fname in WEB_HTML_Candidates:
        fpath = os.path.join(base_dir, fname)
        if os.path.exists(fpath):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception:
                pass
    try:
        html_files = [
            os.path.join(base_dir, f) for f in os.listdir(base_dir)
            if f.lower().startswith("index") and f.lower().endswith(".html")
        ]
        if html_files:
            newest_file = max(html_files, key=os.path.getmtime)
            with open(newest_file, "r", encoding="utf-8") as f:
                return f.read()
    except Exception:
        pass
    return "<h3>Place index.html in the same folder as bot_server.py</h3>"


telethon_loop = asyncio.new_event_loop()
tg_user_client: Optional[TelegramClient] = None
tg_bot_client: Optional[TelegramClient] = None
tg_auth_state = {"phone": None, "phone_code_hash": None, "user_authorized": False, "account_name": ""}
AUDIO_EXTS = (".mp3", ".m4a", ".ogg", ".wav", ".aac", ".flac", ".opus", ".mp4", ".webm", ".wma")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
ENTITY_CACHE = {}
MSG_CACHE = {}
COVER_BYTES_CACHE = {}
NO_COVER_TOPICS = set()
WEB_OTP_SESSIONS = {}
ACTIVE_USER_STREAMS = {}
AUDIO_RAM_CACHE = {}
MAX_RAM_CACHE_TRACKS = 6

def create_instant_channel_join_link(ch_id: int, u_id: int) -> str:
    """Unbans the user if previously expired and creates a direct instant-join link (no approval wait)."""
    with suppress(Exception):
        bot.unban_chat_member(int(ch_id), int(u_id), only_if_banned=True)
    try:
        link_obj = bot.create_chat_invite_link(int(ch_id), creates_join_request=False, member_limit=1)
        return link_obj.invite_link
    except Exception:
        with suppress(Exception):
            return bot.create_chat_invite_link(int(ch_id), creates_join_request=True).invite_link
    return ""

def resolve_telegram_profile_name(user_id: int) -> Tuple[str, str]:
    """Fetches and caches the user's real Telegram profile name and @username."""
    if not user_id or int(user_id) <= 0:
        return "", ""
    user_doc = collections["users"].find_one({"user_id": int(user_id)}) or {}
    full_name = user_doc.get("account_name", "")
    uname = user_doc.get("username", "")
    try:
        chat_info = bot.get_chat(int(user_id))
        first = (chat_info.first_name or "").strip()
        last = (chat_info.last_name or "").strip()
        combined = f"{first} {last}".strip()
        if combined:
            full_name = combined
        if getattr(chat_info, "username", None):
            uname = chat_info.username
        collections["users"].update_one(
            {"user_id": int(user_id)},
            {"$set": {"account_name": full_name, "username": uname}},
            upsert=True
        )
    except Exception:
        pass
    if not full_name or full_name == "User":
        full_name = "Admin" if int(user_id) == ADMIN_ID else f"User {user_id}"
    return full_name, uname

def normalize_phone_number(country_code: str, raw_phone: str) -> Tuple[str, str]:
    """Returns (full_phone_with_plus, digits_only) supporting any country phone format."""
    raw = (raw_phone or "").strip()
    cc = (country_code or "").strip()
    if raw.startswith("+"):
        digits = re.sub(r"\D", "", raw)
        return f"+{digits}", digits
    digits = re.sub(r"\D", "", raw)
    cc_digits = re.sub(r"\D", "", cc)
    if digits.startswith("0") and len(digits) > 10:
        digits = digits.lstrip("0")
    if cc_digits and not digits.startswith(cc_digits):
        full_digits = f"{cc_digits}{digits}"
    elif not cc_digits and len(digits) == 10:
        full_digits = f"91{digits}"
    else:
        full_digits = digits
    return f"+{full_digits}", full_digits

def is_admin_phone(phone_str: str) -> bool:
    digits = re.sub(r"\D", "", phone_str or "")
    admin_digits = re.sub(r"\D", "", ADMIN_PHONE)
    return bool(digits and admin_digits and (digits == admin_digits or digits.endswith(admin_digits)))

def is_image_telethon_message(msg) -> bool:
    if not msg or not getattr(msg, "media", None):
        return False
    if getattr(msg, "photo", None):
        return True
    doc = getattr(msg, "document", None) or getattr(getattr(msg, "media", None), "document", None)
    if doc:
        mime = (getattr(doc, "mime_type", "") or "").lower()
        if mime.startswith("image/"):
            return True
        fname = ""
        if getattr(msg, "file", None) and getattr(msg.file, "name", None):
            fname = msg.file.name.lower()
        if fname.endswith(IMAGE_EXTS):
            return True
    return False

def save_topic_cover_msg_id(topic_id: int, msg_id: int, msg_obj=None) -> None:
    if not topic_id or not msg_id:
        return
    if msg_obj is not None:
        MSG_CACHE[int(msg_id)] = msg_obj
    collections["novels"].update_many(
        {"link": {"$regex": f"/{int(topic_id)}$"}},
        {"$set": {"cover_msg_id": int(msg_id)}}
    )

def format_remaining_time_str(rem_seconds: int) -> str:
    """Formats remaining seconds into Months, Days, Hours, Minutes, Seconds, hiding any unit that is 0."""
    rem = max(0, int(rem_seconds))
    months, rem = divmod(rem, 2592000)  # 30 days
    days, rem = divmod(rem, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)

    parts = []
    if months > 0:
        parts.append(f"{months}mo")
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if seconds > 0 or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)

def get_topic_free_audio_limit(topic_id: int) -> int:
    """Returns the configured free preview audio count for a novel topic (default 10)."""
    if not topic_id:
        return 10
    novel_doc = collections["novels"].find_one({"link": {"$regex": f"/{int(topic_id)}$"}})
    if novel_doc and novel_doc.get("free_audios") is not None:
        try:
            return max(0, int(novel_doc["free_audios"]))
        except Exception:
            return 10
    return 10

STREAM_AUTH_CACHE = {}

def can_user_stream_audio(user_id: int, track_msg_id: int) -> bool:
    """Returns True in 0ms from cache if user can stream the audio track."""
    if not user_id or int(user_id) <= 0:
        return False
    uid, mid = int(user_id), int(track_msg_id)
    if uid == ADMIN_ID:
        return True
    now = time.time()
    cached = STREAM_AUTH_CACHE.get((uid, mid))
    if cached and cached[1] > now:
        return cached[0]

    allowed = False
    if user_has_novel_access(uid):
        allowed = True
    elif collections["users"].find_one({"user_id": uid, "status": "approved_pending_join"}):
        allowed = True
    else:
        audio_doc = collections["novel_audios"].find_one({"message_id": mid})
        if audio_doc and audio_doc.get("topic_id"):
            t_id = int(audio_doc["topic_id"])
            free_limit = get_topic_free_audio_limit(t_id)
            if free_limit > 0:
                topic_audios = list(collections["novel_audios"].find({"topic_id": t_id}, {"message_id": 1, "chapter_num": 1}))
                topic_audios.sort(key=lambda x: (x.get("chapter_num", x["message_id"]), x["message_id"]))
                free_ids = {int(a["message_id"]) for a in topic_audios[:free_limit]}
                allowed = mid in free_ids

    STREAM_AUTH_CACHE[(uid, mid)] = (allowed, now + (120 if allowed else 15))
    return allowed

def get_active_tg_client() -> Optional[TelegramClient]:
    """Prefers the User MTProto client (which can read all old Oct 26-27 group messages), falls back to Bot client."""
    if tg_user_client and tg_auth_state.get("user_authorized"):
        return tg_user_client
    return tg_bot_client

def extract_topic_id_from_link(link: str) -> Optional[int]:
    if not link:
        return None
    parts = [p for p in link.rstrip("/").split("/") if p.isdigit()]
    if len(parts) >= 2:
        return int(parts[1])
    if parts:
        return int(parts[-1])
    return None

def clean_novel_keywords(novel_name: str) -> Tuple[str, str]:
    if not novel_name:
        return "", ""
    cleaned = re.sub(r'\(.*?\)', '', novel_name)
    cleaned = re.sub(r'\[.*?\]', '', cleaned)
    cleaned = re.sub(r'(?i)\bch(?:apter)?\s*\d+[-–~to\s]*\d*\b', '', cleaned)
    words = [w.lower() for w in re.split(r'[\s_\-]+', cleaned) if w.strip()]
    if not words:
        return "", ""
    return " ".join(words), "_".join(words)

def extract_chapter_number(text: str, fallback_id: int = 0) -> float:
    if not text:
        return float(fallback_id)
    m = re.search(r'(?:chapter|chap|ch|episode|ep|part|pt)[\s._:-]*(\d+)', text, re.IGNORECASE)
    if m:
        return float(int(m.group(1)))
    m2 = re.search(r'(\d+)\s*(?:to|-|~)\s*\d+', text, re.IGNORECASE)
    if m2:
        return float(int(m2.group(1)))
    m3 = re.match(r'^\s*(\d+)', text)
    if m3:
        return float(int(m3.group(1)))
    return float(fallback_id)

def is_audio_telethon_message(msg) -> bool:
    if not msg or not getattr(msg, "media", None):
        return False
    if getattr(msg, "audio", None) or getattr(msg, "voice", None):
        return True
    doc = getattr(msg, "document", None) or getattr(getattr(msg, "media", None), "document", None)
    if doc:
        mime = (getattr(doc, "mime_type", "") or "").lower()
        if mime.startswith("audio/"):
            return True
        fname = ""
        if getattr(msg, "file", None) and getattr(msg.file, "name", None):
            fname = msg.file.name.lower()
        if fname.endswith(AUDIO_EXTS):
            return True
        for attr in getattr(doc, "attributes", []) or []:
            if type(attr).__name__ == "DocumentAttributeAudio":
                return True
            afname = (getattr(attr, "file_name", "") or "").lower()
            if afname.endswith(AUDIO_EXTS):
                return True
            if getattr(attr, "title", None):
                return True
    return False

def parse_telethon_audio_doc(msg, resolved_topic_id: int) -> dict:
    file_name = ""
    audio_title = ""
    duration = 0
    file_size = getattr(msg.file, "size", 0) or 0

    if getattr(msg, "file", None) and getattr(msg.file, "name", None):
        file_name = os.path.splitext(msg.file.name)[0].replace("_", " ").strip()

    doc = getattr(msg, "document", None) or getattr(getattr(msg, "media", None), "document", None)
    if doc:
        if not file_size:
            file_size = getattr(doc, "size", 0) or 0
        for attr in getattr(doc, "attributes", []) or []:
            if getattr(attr, "title", None):
                audio_title = attr.title.strip()
            if getattr(attr, "file_name", None) and not file_name:
                file_name = os.path.splitext(attr.file_name)[0].replace("_", " ").strip()
            if getattr(attr, "duration", None):
                duration = int(attr.duration)

    caption_text = (getattr(msg, "message", "") or "").strip()
    caption_clean = ""
    for line in caption_text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("/"):
            caption_clean = line[:120]
            break

    if audio_title and not audio_title.startswith("#"):
        title = audio_title
    elif file_name and not file_name.startswith("#"):
        title = file_name
    elif caption_clean:
        title = caption_clean
    elif caption_text:
        title = caption_text.splitlines()[0][:120]
    else:
        title = f"Chapter Audio #{msg.id}"

    chapter_num = extract_chapter_number(title, msg.id)

    return {
        "topic_id": int(resolved_topic_id),
        "message_id": int(msg.id),
        "title": title,
        "caption": caption_text[:120],
        "chapter_num": chapter_num,
        "duration": duration,
        "file_size": file_size,
        "timestamp": msg.date.timestamp() if getattr(msg, "date", None) else time.time()
    }

async def _resolve_group_entity(client_obj: TelegramClient):
    key = id(client_obj)
    if key in ENTITY_CACHE:
        return ENTITY_CACHE[key]
    try:
        ent = await client_obj.get_input_entity(NOVEL_GROUP_ID)
        ENTITY_CACHE[key] = ent
        return ent
    except Exception:
        try:
            raw_id = int(str(NOVEL_GROUP_ID).replace("-100", ""))
            ent = await client_obj.get_input_entity(PeerChannel(raw_id))
            ENTITY_CACHE[key] = ent
            return ent
        except Exception:
            await client_obj.get_dialogs(limit=50)
            ent = await client_obj.get_input_entity(NOVEL_GROUP_ID)
            ENTITY_CACHE[key] = ent
            return ent

async def _scan_topic_audios(topic_id: int, novel_name: str = "") -> Tuple[list, str]:
    active_client = get_active_tg_client()
    if not active_client:
        return [], "No Telethon client initialized."

    if not novel_name:
        novel_doc = collections["novels"].find_one({"link": {"$regex": f"/{topic_id}$"}})
        if novel_doc:
            novel_name = novel_doc.get("name", "")

    space_key, underscore_key = clean_novel_keywords(novel_name)
    short_space_key = " ".join(space_key.split()[:3]) if space_key else ""
    short_under_key = "_".join(underscore_key.split("_")[:3]) if underscore_key else ""

    is_user_mode = bool(tg_user_client and tg_auth_state.get("user_authorized"))
    mode_name = "User-MTProto" if is_user_mode else "Bot-MTProto"
    debug_info = ""

    try:
        if not active_client.is_connected():
            await active_client.connect()

        entity = await _resolve_group_entity(active_client)
        real_topic_id = int(topic_id)

        # 1. Fast User-MTProto Topic Thread Fetch (Takes ~1 second, no batch loop needed!)
        if is_user_mode:
            count_replies = 0
            try:
                async for m in active_client.iter_messages(entity, reply_to=real_topic_id, limit=600):
                    if is_image_telethon_message(m):
                        save_topic_cover_msg_id(int(topic_id), m.id, m)
                    if is_audio_telethon_message(m):
                        parsed = parse_telethon_audio_doc(m, int(topic_id))
                        collections["novel_audios"].update_one(
                            {"topic_id": int(topic_id), "message_id": m.id},
                            {"$set": parsed},
                            upsert=True
                        )
                        MSG_CACHE[m.id] = m
                        count_replies += 1
            except Exception as rep_err:
                logger.warning(f"[WEB SCAN] iter_messages(reply_to={real_topic_id}) note: {rep_err}")

            # If topic_id was actually a message inside the topic rather than the root topic ID, check anchor
            if count_replies == 0:
                try:
                    anchor = await active_client.get_messages(entity, ids=real_topic_id)
                    if anchor and getattr(anchor, "reply_to", None):
                        top_id = getattr(anchor.reply_to, "reply_to_top_id", None) or getattr(anchor.reply_to, "reply_to_msg_id", None)
                        if top_id and int(top_id) != real_topic_id:
                            real_topic_id = int(top_id)
                            async for m in active_client.iter_messages(entity, reply_to=real_topic_id, limit=600):
                                if is_audio_telethon_message(m):
                                    parsed = parse_telethon_audio_doc(m, int(topic_id))
                                    collections["novel_audios"].update_one(
                                        {"topic_id": int(topic_id), "message_id": m.id},
                                        {"$set": parsed},
                                        upsert=True
                                    )
                                    MSG_CACHE[m.id] = m
                                    count_replies += 1
                except Exception:
                    pass

            # Also try fast keyword search if count_replies is still 0
            if count_replies == 0 and short_space_key and len(short_space_key) >= 4:
                try:
                    async for m in active_client.iter_messages(entity, search=short_space_key, limit=300):
                        if is_audio_telethon_message(m):
                            parsed = parse_telethon_audio_doc(m, int(topic_id))
                            collections["novel_audios"].update_one(
                                {"topic_id": int(topic_id), "message_id": m.id},
                                {"$set": parsed},
                                upsert=True
                            )
                            MSG_CACHE[m.id] = m
                            count_replies += 1
                except Exception:
                    pass

            if count_replies > 0:
                logger.info(f"[WEB SCAN] Fast User-MTProto indexed {count_replies} audios for Topic #{topic_id}!")
                results = list(collections["novel_audios"].find({"topic_id": int(topic_id)}, {"_id": 0}))
                results.sort(key=lambda x: (x.get("chapter_num", x["message_id"]), x["message_id"]))
                return results, f"Indexed {count_replies} chapters via User-MTProto"

        # 2. Fallback 100-ID batch scan ONLY if count_replies == 0
        batch_size = 100
        start_id = real_topic_id + 1
        topic_msg_ids = {real_topic_id, int(topic_id)}
        in_sequential_block = True
        total_found = 0
        empty_count = 0

        for b in range(3):
            b_start = start_id + (b * batch_size)
            batch_ids = list(range(b_start, b_start + batch_size))
            msgs = await active_client.get_messages(entity, ids=batch_ids)
            if not msgs:
                break

            non_none = [m for m in msgs if m is not None]
            if not non_none:
                break

            msg_types = {}
            batch_found = 0
            for m in non_none:
                t_name = type(m).__name__
                msg_types[t_name] = msg_types.get(t_name, 0) + 1
                if t_name == "MessageEmpty":
                    continue

                action_name = type(getattr(m, "action", None)).__name__
                if action_name == "MessageActionTopicCreate" and m.id not in topic_msg_ids:
                    in_sequential_block = False

                rep = getattr(m, "reply_to", None)
                rep_top = None
                if rep:
                    rep_top = getattr(rep, "reply_to_top_id", None) or getattr(rep, "reply_to_msg_id", None)

                is_reply_match = (rep_top is not None and int(rep_top) in topic_msg_ids)
                if is_reply_match:
                    topic_msg_ids.add(m.id)

                if is_image_telethon_message(m) and (is_reply_match or in_sequential_block):
                    save_topic_cover_msg_id(int(topic_id), m.id, m)

                if not is_audio_telethon_message(m):
                    continue

                parsed = parse_telethon_audio_doc(m, int(topic_id))
                combined_text = f"{parsed['title'].lower()} {parsed.get('caption', '').lower()}"

                is_name_match = False
                if short_space_key and len(short_space_key) >= 4 and short_space_key in combined_text:
                    is_name_match = True
                elif short_under_key and len(short_under_key) >= 4 and short_under_key in combined_text:
                    is_name_match = True

                has_other_hashtag = ("#" in parsed.get("caption", "") and short_under_key and short_under_key not in parsed.get("caption", "").lower())
                if has_other_hashtag:
                    in_sequential_block = False

                is_seq_match = (in_sequential_block and not has_other_hashtag and (rep_top is None or int(rep_top) in topic_msg_ids))

                if is_reply_match or is_name_match or is_seq_match:
                    collections["novel_audios"].update_one(
                        {"topic_id": int(topic_id), "message_id": m.id},
                        {"$set": parsed},
                        upsert=True
                    )
                    MSG_CACHE[m.id] = m
                    batch_found += 1
                    total_found += 1

            if b == 0:
                debug_info = f"Mode={mode_name} | Batch {b_start}-{b_start+99} returned {msg_types}"

            if batch_found == 0:
                empty_count += 1
                if empty_count >= 2:
                    break
            else:
                empty_count = 0

    except Exception as e:
        debug_info = f"Scan note: {type(e).__name__}: {e}"
        logger.error(f"[WEB SCAN] Error scanning topic {topic_id}: {e}")

    results = list(collections["novel_audios"].find({"topic_id": int(topic_id)}, {"_id": 0}))
    results.sort(key=lambda x: (x.get("chapter_num", x["message_id"]), x["message_id"]))
    return results, debug_info

async def _auto_preindex_unindexed_novels():
    """Gently pre-indexes any novel topic with 0 cached audios when User MTProto is connected."""
    await asyncio.sleep(2)
    if not (tg_user_client and tg_auth_state.get("user_authorized")):
        return
    try:
        novels = list(collections["novels"].find({"status": {"$in": ["ongoing", "completed", "adult"]}}))
        for n in novels:
            t_id = extract_topic_id_from_link(n.get("link", ""))
            if not t_id:
                continue
            cnt = collections["novel_audios"].count_documents({"topic_id": t_id})
            if cnt == 0:
                res, _ = await _scan_topic_audios(t_id, novel_name=n.get("name", ""))
                logger.info(f"[AUTO-SYNC] Indexed {len(res)} chapters for '{n.get('name')}' (Topic #{t_id})")
                await asyncio.sleep(1.2)
    except Exception as e:
        logger.warning(f"[AUTO-SYNC] Note: {e}")

def start_telethon_background() -> None:
    global tg_user_client, tg_bot_client
    asyncio.set_event_loop(telethon_loop)
    tg_user_client = TelegramClient("user_mtproto_session", API_ID, API_HASH, loop=telethon_loop, receive_updates=False)
    tg_bot_client = TelegramClient("bot_mtproto_session", API_ID, API_HASH, loop=telethon_loop, receive_updates=False)

    async def _init():
        await tg_user_client.connect()
        if await tg_user_client.is_user_authorized():
            me = await tg_user_client.get_me()
            tg_auth_state["user_authorized"] = True
            tg_auth_state["account_name"] = getattr(me, "first_name", "Connected")
            logger.info(f"Telethon USER Session Active ({tg_auth_state['account_name']}) - 100% Old & New Topic Audios Unlocked!")
            asyncio.create_task(_auto_preindex_unindexed_novels())
        else:
            logger.info("Telethon User Session not logged in yet. Starting Bot fallback session.")

        await tg_bot_client.start(bot_token=BOT_TOKEN)
        logger.info("Telethon Bot fallback client ready.")

    try:
        telethon_loop.run_until_complete(_init())
        telethon_loop.run_forever()
    except Exception as e:
        logger.error(f"Telethon startup error: {e}")

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization,Range"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    response.headers["Access-Control-Expose-Headers"] = "Content-Range,Content-Length,Accept-Ranges"
    return response

@app.route("/")
@app.route("/app")
@app.route("/player")
def serve_website():
    return Response(load_external_web_html(), mimetype="text/html")

@app.route("/sw.js")
def serve_service_worker():
    from flask import send_file
    # Serves the sw.js file from the root directory to satisfy Monetag's bot
    return send_file("sw.js", mimetype="application/javascript")

@app.route("/health")
def health() -> str:
    return "OK"

@app.route("/api/tg_auth/status")
def api_tg_auth_status():
    return jsonify({
        "user_authorized": tg_auth_state.get("user_authorized", False),
        "account_name": tg_auth_state.get("account_name", "")
    })

@app.route("/api/tg_auth/send_code", methods=["POST"])
def api_tg_auth_send_code():
    data = request.get_json(silent=True) or {}
    phone = (data.get("phone") or "").strip()
    if not phone:
        return jsonify({"error": "Phone number required"}), 400

    async def _send():
        if not tg_user_client.is_connected():
            await tg_user_client.connect()
        sent = await tg_user_client.send_code_request(phone)
        tg_auth_state["phone"] = phone
        tg_auth_state["phone_code_hash"] = sent.phone_code_hash
        return True

    try:
        asyncio.run_coroutine_threadsafe(_send(), telethon_loop).result(timeout=20)
        return jsonify({"status": "code_sent"})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/tg_auth/verify_code", methods=["POST"])
def api_tg_auth_verify_code():
    data = request.get_json(silent=True) or {}
    code = (data.get("code") or "").strip()
    password = data.get("password") or ""
    phone = tg_auth_state.get("phone")
    phone_code_hash = tg_auth_state.get("phone_code_hash")

    if not phone or not phone_code_hash:
        return jsonify({"error": "Please request OTP code first."}), 400

    async def _verify():
        try:
            await tg_user_client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            if not password:
                raise ValueError("Two-Step Verification (2FA) password is enabled on your account. Please enter your 2FA password.")
            await tg_user_client.sign_in(password=password)
        me = await tg_user_client.get_me()
        tg_auth_state["user_authorized"] = True
        tg_auth_state["account_name"] = getattr(me, "first_name", "Connected")
        asyncio.create_task(_auto_preindex_unindexed_novels())
        return tg_auth_state["account_name"]

    try:
        acc_name = asyncio.run_coroutine_threadsafe(_verify(), telethon_loop).result(timeout=25)
        return jsonify({"status": "connected", "account_name": acc_name})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/api/auth/send_otp", methods=["POST"])
def api_auth_send_otp():
    data = request.get_json(silent=True) or {}
    country_code = (data.get("country_code") or "").strip()
    raw_phone = (data.get("phone") or "").strip()
    explicit_user_id = int(data.get("user_id") or 0)

    if not raw_phone and explicit_user_id <= 0:
        return jsonify({"error": "Phone number or Telegram User ID is required."}), 400

    if raw_phone:
        full_phone, digits = normalize_phone_number(country_code, raw_phone)
        if len(digits) < 7 and explicit_user_id <= 0:
            return jsonify({"error": "Please enter a valid phone number."}), 400
    else:
        full_phone, digits = "", ""

    admin_match = is_admin_phone(digits) or (explicit_user_id == ADMIN_ID)
    matched_user_id = ADMIN_ID if admin_match else (explicit_user_id if explicit_user_id > 0 else None)

    if not matched_user_id and digits:
        user_doc = collections["users"].find_one({
            "$or": [
                {"phone": full_phone},
                {"phone_digits": digits},
                {"phone_digits": {"$regex": f"{digits[-10:]}$"}} if len(digits) >= 10 else {"phone_digits": digits}
            ]
        })
        if user_doc and user_doc.get("user_id"):
            matched_user_id = int(user_doc["user_id"])

    if matched_user_id and full_phone:
        collections["users"].update_one(
            {"user_id": matched_user_id},
            {"$set": {"phone": full_phone, "phone_digits": digits}},
            upsert=True
        )
    elif matched_user_id and not full_phone:
        u_doc = collections["users"].find_one({"user_id": matched_user_id}) or {}
        full_phone = u_doc.get("phone") or f"ID_{matched_user_id}"
        digits = u_doc.get("phone_digits") or str(matched_user_id)

    bot_otp = str(random.randint(10000, 99999))
    bot_dm_sent = False
    real_name = ""

    if matched_user_id:
        real_name, _ = resolve_telegram_profile_name(matched_user_id)
        try:
            role_tag = "👑 Admin" if matched_user_id == ADMIN_ID else "👤 User"
            bot.send_message(
                matched_user_id,
                f"🔐 *NovelXplainer Website Login OTP*\n\n"
                f"{role_tag}: *{real_name}*\n"
                f"🆔 Your User ID: `{matched_user_id}`\n"
                f"📱 Phone: `{full_phone}`\n"
                f"🔑 Your OTP Code: `{bot_otp}`\n\n"
                f"_Enter this 5-digit code on the website to activate your session._",
                parse_mode="Markdown"
            )
            bot_dm_sent = True
        except Exception as e:
            logger.warning(f"Bot DM OTP note for {matched_user_id}: {e}")

    telethon_sent = False
    phone_code_hash = None
    temp_session_str = None
    used_main_user_client = False

    async def _send_telethon_otp():
        nonlocal phone_code_hash, temp_session_str, used_main_user_client
        if not full_phone.startswith("+"):
            return False
        if admin_match and not tg_auth_state.get("user_authorized"):
            if not tg_user_client.is_connected():
                await tg_user_client.connect()
            sent = await tg_user_client.send_code_request(full_phone)
            phone_code_hash = sent.phone_code_hash
            used_main_user_client = True
            return True
        elif not bot_dm_sent:
            temp_client = TelegramClient(StringSession(), API_ID, API_HASH, loop=telethon_loop)
            await temp_client.connect()
            sent = await temp_client.send_code_request(full_phone)
            phone_code_hash = sent.phone_code_hash
            temp_session_str = temp_client.session.save()
            await temp_client.disconnect()
            return True
        return False

    try:
        telethon_sent = asyncio.run_coroutine_threadsafe(_send_telethon_otp(), telethon_loop).result(timeout=20)
    except Exception as e:
        logger.warning(f"Telethon OTP request note for {full_phone}: {e}")
        if not bot_dm_sent:
            return jsonify({"error": f"Telegram could not send OTP to {full_phone}. Click '🆔 User ID' in our Telegram Bot and enter your User ID above! ({e})"}), 400

    session_data = {
        "full_phone": full_phone,
        "digits": digits,
        "bot_otp": bot_otp if bot_dm_sent else None,
        "matched_user_id": matched_user_id,
        "account_name": real_name,
        "phone_code_hash": phone_code_hash,
        "temp_session_str": temp_session_str,
        "used_main_user_client": used_main_user_client,
        "timestamp": time.time()
    }
    WEB_OTP_SESSIONS[full_phone] = session_data
    if matched_user_id:
        WEB_OTP_SESSIONS[f"UID_{matched_user_id}"] = session_data

    msg_note = "✅ OTP sent to your Telegram Bot chat & Telegram app!" if (bot_dm_sent and telethon_sent) else (
        "✅ OTP sent directly to your Telegram Bot chat!" if bot_dm_sent else "✅ OTP sent to your Telegram app service notifications!"
    )
    return jsonify({
        "status": "otp_sent",
        "phone": full_phone,
        "user_id": explicit_user_id if explicit_user_id > 0 else None,
        "account_name": real_name if explicit_user_id > 0 else "",
        "is_admin_phone": False,
        "message": msg_note
    })

@app.route("/api/auth/verify_otp", methods=["POST"])
def api_auth_verify_otp():
    data = request.get_json(silent=True) or {}
    country_code = (data.get("country_code") or "").strip()
    raw_phone = (data.get("phone") or "").strip()
    explicit_user_id = int(data.get("user_id") or 0)
    code = (data.get("code") or "").strip()
    password = data.get("password") or ""

    if (not raw_phone and explicit_user_id <= 0) or not code:
        return jsonify({"error": "Phone number (or User ID) and OTP code are required."}), 400

    full_phone, digits = normalize_phone_number(country_code, raw_phone) if raw_phone else (f"ID_{explicit_user_id}", str(explicit_user_id))
    sess = WEB_OTP_SESSIONS.get(full_phone) or (WEB_OTP_SESSIONS.get(f"UID_{explicit_user_id}") if explicit_user_id > 0 else None)
    if not sess:
        return jsonify({"error": "OTP session expired or not found. Please click Send OTP again."}), 400

    verified_user_id = None
    account_name = sess.get("account_name") or ""
    admin_match = is_admin_phone(digits) or (explicit_user_id == ADMIN_ID)

    # 1. Check instant Bot DM OTP match
    if sess.get("bot_otp") and code == str(sess["bot_otp"]):
        verified_user_id = int(sess["matched_user_id"] or explicit_user_id or ADMIN_ID)
        real_name, _ = resolve_telegram_profile_name(verified_user_id)
        if real_name:
            account_name = real_name

    # 2. Otherwise verify Telethon Telegram OTP code
    if not verified_user_id and sess.get("phone_code_hash"):
        async def _verify_telethon():
            if sess.get("used_main_user_client"):
                if not tg_user_client.is_connected():
                    await tg_user_client.connect()
                try:
                    await tg_user_client.sign_in(phone=full_phone, code=code, phone_code_hash=sess["phone_code_hash"])
                except SessionPasswordNeededError:
                    if not password:
                        raise ValueError("2-Step Verification password required. Please enter your Telegram 2FA password.")
                    await tg_user_client.sign_in(password=password)
                me = await tg_user_client.get_me()
                tg_auth_state["user_authorized"] = True
                f_name = f"{getattr(me, 'first_name', '') or ''} {getattr(me, 'last_name', '') or ''}".strip() or "Connected"
                tg_auth_state["account_name"] = f_name
                asyncio.create_task(_auto_preindex_unindexed_novels())
                return int(me.id), f_name
            else:
                temp_client = TelegramClient(StringSession(sess.get("temp_session_str") or ""), API_ID, API_HASH, loop=telethon_loop)
                await temp_client.connect()
                try:
                    try:
                        await temp_client.sign_in(phone=full_phone, code=code, phone_code_hash=sess["phone_code_hash"])
                    except SessionPasswordNeededError:
                        if not password:
                            raise ValueError("2-Step Verification password required. Please enter your Telegram 2FA password.")
                        await temp_client.sign_in(password=password)
                    me = await temp_client.get_me()
                    u_id = int(me.id)
                    f_name = f"{getattr(me, 'first_name', '') or ''} {getattr(me, 'last_name', '') or ''}".strip() or "User"
                    return u_id, f_name
                finally:
                    await temp_client.disconnect()

        try:
            verified_user_id, account_name = asyncio.run_coroutine_threadsafe(_verify_telethon(), telethon_loop).result(timeout=25)
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    if not verified_user_id:
        return jsonify({"error": "Invalid OTP code. Please check your Telegram and try again."}), 400

    real_name, uname = resolve_telegram_profile_name(verified_user_id)
    if real_name and (not account_name or account_name in ["User", "Connected"]):
        account_name = real_name

    if admin_match or verified_user_id == ADMIN_ID:
        verified_user_id = ADMIN_ID
        is_admin = True
        if not account_name or account_name == "User":
            account_name = "Admin"
    else:
        is_admin = False

    collections["users"].update_one(
        {"user_id": verified_user_id},
        {"$set": {
            "phone": full_phone,
            "phone_digits": digits,
            "account_name": account_name,
            "username": uname,
            "last_seen": time.time()
        }},
        upsert=True
    )
    WEB_OTP_SESSIONS.pop(full_phone, None)
    WEB_OTP_SESSIONS.pop(f"UID_{verified_user_id}", None)

    now_ts = datetime.now(IST).timestamp()
    expiry_val = None
    if is_admin:
        has_access = True
        label = "Admin (Unlimited)"
        days_left = 999
    else:
        sub = collections["users"].find_one({"user_id": verified_user_id, "status": "active", "expiry": {"$gt": now_ts}})
        pending_sub = collections["users"].find_one({"user_id": verified_user_id, "status": "approved_pending_join"})
        if sub:
            expiry_val = float(sub["expiry"])
            rem = int(expiry_val - now_ts)
            d = rem // 86400
            has_access = True
            label = f"Active ({format_remaining_time_str(rem)} left)"
            days_left = d
        elif pending_sub:
            has_access = True
            label = "Approved (Click Request to Join Channel)"
            days_left = 1
        else:
            has_access = False
            label = "Inactive (Free Preview)"
            days_left = None

    return jsonify({
        "status": "authenticated",
        "user_id": verified_user_id,
        "phone": full_phone,
        "account_name": account_name,
        "is_admin": is_admin,
        "has_access": has_access,
        "label": label,
        "expiry": expiry_val,
        "days_left": days_left
    })

@app.route("/api/user_status")
def api_user_status():
    user_id = request.args.get("user_id", type=int, default=0)
    phone = (request.args.get("phone", default="") or "").strip()
    if user_id <= 0 and not phone:
        return jsonify({
            "has_access": False,
            "is_admin": False,
            "user_id": 0,
            "phone": "",
            "account_name": "",
            "label": "Login Required",
            "days_left": None
        })
    if user_id == ADMIN_ID or is_admin_phone(phone):
        real_name, uname = resolve_telegram_profile_name(ADMIN_ID)
        return jsonify({
            "has_access": True,
            "is_admin": True,
            "user_id": ADMIN_ID,
            "phone": phone or f"+91{ADMIN_PHONE}",
            "account_name": real_name,
            "username": uname,
            "label": "Admin (Unlimited)",
            "days_left": 999
        })
    now_ts = datetime.now(IST).timestamp()
    real_name, uname = resolve_telegram_profile_name(user_id)

    expired_active = list(collections["users"].find({"user_id": user_id, "status": "active", "expiry": {"$ne": None, "$lte": now_ts}}))
    if expired_active:
        kick_expired_users()

    sub = collections["users"].find_one({"user_id": user_id, "status": "active", "expiry": {"$gt": now_ts}})
    pending_sub = collections["users"].find_one({"user_id": user_id, "status": "approved_pending_join"})
    feedback_doc = collections["users"].find_one({"user_id": user_id, "status": "pending_feedback"})
    user_doc = sub or pending_sub or feedback_doc or collections["users"].find_one({"user_id": user_id}) or {}
    saved_phone = user_doc.get("phone", phone)

    if sub:
        expiry_val = float(sub["expiry"])
        rem = int(expiry_val - now_ts)
        d = rem // 86400
        return jsonify({
            "has_access": True,
            "is_admin": False,
            "pending_feedback": False,
            "user_id": user_id,
            "phone": saved_phone,
            "account_name": real_name,
            "username": uname,
            "label": f"Active ({format_remaining_time_str(rem)} left)",
            "expiry": expiry_val,
            "days_left": d
        })

    if pending_sub:
        dur_mins = int(pending_sub.get("duration_mins", 1440))
        new_expiry = now_ts + (dur_mins * 60)
        collections["users"].update_one(
            {"_id": pending_sub["_id"]},
            {"$set": {"status": "active", "expiry": new_expiry}}
        )
        rem = int(new_expiry - now_ts)
        return jsonify({
            "has_access": True,
            "is_admin": False,
            "pending_feedback": False,
            "user_id": user_id,
            "phone": saved_phone,
            "account_name": real_name,
            "username": uname,
            "label": f"Active ({format_remaining_time_str(rem)} left)",
            "expiry": new_expiry,
            "days_left": rem // 86400
        })

    return jsonify({
        "has_access": False,
        "is_admin": False,
        "pending_feedback": bool(feedback_doc),
        "feedback_channel_id": int(feedback_doc.get("channel_id", 0)) if feedback_doc else 0,
        "user_id": user_id,
        "phone": saved_phone,
        "account_name": real_name,
        "username": uname,
        "label": "Inactive (Free Preview)",
        "days_left": None
    })

@app.route("/api/reviews")
def api_get_reviews():
    topic_id = request.args.get("topic_id", type=int, default=0)
    query = {"topic_id": topic_id} if topic_id > 0 else {}
    docs = list(collections["reviews"].find(query, {"_id": 0}).sort("timestamp", -1).limit(60))
    return jsonify({"reviews": docs, "topic_id": topic_id})

@app.route("/api/review", methods=["POST"])
def api_submit_review():
    data = request.get_json(silent=True) or {}
    user_id = int(data.get("user_id") or 0)
    rating = max(1, min(5, int(data.get("rating") or 5)))
    comment = (data.get("comment") or "").strip()
    user_name = (data.get("user_name") or "").strip()
    novel_topic_id = int(data.get("topic_id") or 0)
    novel_name = (data.get("novel_name") or "").strip()

    fb_doc = (collections["users"].find_one({"user_id": user_id, "status": "pending_feedback"}) or collections["users"].find_one({"user_id": user_id}) or {}) if user_id else {}
    if not user_name:
        user_name = fb_doc.get("account_name") or (f"User {user_id}" if user_id else "Verified Listener")

    now_ist = datetime.now(IST)
    rev_doc = {
        "user_id": user_id,
        "user_name": user_name,
        "rating": rating,
        "comment": comment or f"Rated {rating} stars!",
        "topic_id": novel_topic_id,
        "novel_name": novel_name,
        "time_str": now_ist.strftime("%d %b %Y"),
        "timestamp": now_ist.timestamp()
    }
    with suppress(Exception):
        collections["reviews"].insert_one(rev_doc)

    if user_id:
        ch_id = int(fb_doc.get("channel_id") or 0)
        ch_name = (collections["channels"].find_one({"channel_id": ch_id}) or {}).get("name", "NovelXplainer Audio")
        collections["users"].update_many(
            {"user_id": user_id, "status": "pending_feedback"},
            {"$set": {"status": "expired", "last_rating": rating, "last_review_comment": comment}}
        )
        forum_topic_id = fb_doc.get("topic_id")
        comment_line = f"\n💬 Comment: {comment}" if comment else ""
        novel_line = f"\n📖 Novel: {novel_name}" if novel_name else ""
        log_text = (
            f"⭐️ *New Website User Review*\n\n"
            f"👤 User: {get_user_display(user_id)}\n"
            f"ID: `{user_id}`\n"
            f"📺 Channel: {ch_name}{novel_line}\n"
            f"⭐ Rating: {rating}/5 Stars{comment_line}"
        )
        with suppress(ApiTelegramException):
            if forum_topic_id:
                bot.send_message(FORUM_GROUP_ID, log_text, parse_mode="Markdown", message_thread_id=forum_topic_id)
            else:
                bot.send_message(LOG_CHANNEL_ID, log_text, parse_mode="Markdown")

    return jsonify({
        "submitted": True,
        "topic_id": novel_topic_id,
        "message": "Thank you for sharing your review!"
    })

@app.route("/api/view/novel", methods=["POST"])
def api_increment_novel_view():
    data = request.get_json(silent=True) or {}
    topic_id = int(data.get("topic_id") or 0)
    if topic_id <= 0:
        return jsonify({"error": "Invalid topic_id"}), 400

    collections["novels"].update_many(
        {"link": {"$regex": f"/{topic_id}$"}},
        {"$inc": {"views": 1}}
    )
    novel_doc = collections["novels"].find_one({"link": {"$regex": f"/{topic_id}$"}}) or {}
    return jsonify({
        "topic_id": topic_id,
        "novel_views": int(novel_doc.get("views", 0)),
        "novel_plays": int(novel_doc.get("plays", 0))
    })

@app.route("/api/view/audio", methods=["POST"])
def api_increment_audio_view():
    data = request.get_json(silent=True) or {}
    topic_id = int(data.get("topic_id") or 0)
    message_id = int(data.get("message_id") or 0)
    if message_id <= 0:
        return jsonify({"error": "Invalid message_id"}), 400

    collections["novel_audios"].update_one(
        {"message_id": message_id},
        {"$inc": {"views": 1}}
    )
    audio_doc = collections["novel_audios"].find_one({"message_id": message_id}) or {}
    resolved_topic = topic_id or int(audio_doc.get("topic_id") or 0)

    novel_views = 0
    novel_plays = 0
    if resolved_topic > 0:
        collections["novels"].update_many(
            {"link": {"$regex": f"/{resolved_topic}$"}},
            {"$inc": {"views": 1, "plays": 1}}
        )
        novel_doc = collections["novels"].find_one({"link": {"$regex": f"/{resolved_topic}$"}}) or {}
        novel_views = int(novel_doc.get("views", 0))
        novel_plays = int(novel_doc.get("plays", 0))

    return jsonify({
        "topic_id": resolved_topic,
        "message_id": message_id,
        "audio_views": int(audio_doc.get("views", 0)),
        "novel_views": novel_views,
        "novel_plays": novel_plays
    })

@app.route("/api/novel_meta", methods=["GET", "POST"])
def api_novel_custom_meta():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        user_id = int(data.get("user_id") or 0)
        phone = str(data.get("phone") or "")
        if user_id != ADMIN_ID and not is_admin_phone(phone):
            return jsonify({"error": "Admin only"}), 403

        if "sub_modal_note" in data:
            note_val = str(data.get("sub_modal_note") or "").strip()
            collections["config"].update_one(
                {"_id": "settings"},
                {"$set": {"sub_modal_note": note_val}},
                upsert=True
            )
            return jsonify({"saved": True, "sub_modal_note": note_val})

        topic_id = int(data.get("topic_id") or 0)
        if topic_id <= 0:
            return jsonify({"error": "Invalid topic_id"}), 400

        desc = str(data.get("description") or "").strip()
        author = str(data.get("author") or "").strip()
        narrator = str(data.get("narrator") or "").strip()

        collections["novels"].update_many(
            {"link": {"$regex": f"/{topic_id}$"}},
            {"$set": {"description": desc, "author": author, "narrator": narrator}}
        )
        return jsonify({
            "saved": True,
            "topic_id": topic_id,
            "description": desc,
            "author": author,
            "narrator": narrator
        })

    cfg = collections["config"].find_one({"_id": "settings"}) or {}
    return jsonify({"sub_modal_note": cfg.get("sub_modal_note", "")})

@app.route("/api/theme", methods=["GET", "POST"])
def api_website_theme():
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        theme = data.get("theme") or {}
        if theme and isinstance(theme, dict):
            collections["config"].update_one(
                {"_id": "web_theme"},
                {"$set": {"theme": theme, "updated_at": time.time()}},
                upsert=True
            )
            return jsonify({"saved": True, "theme": theme})
        return jsonify({"error": "Invalid theme data"}), 400

    doc = collections["config"].find_one({"_id": "web_theme"}) or {}
    return jsonify({"theme": doc.get("theme") or {}})

@app.route("/api/plans")
def api_plans():
    user_id = request.args.get("user_id", type=int, default=0)
    req_voucher = (request.args.get("voucher", default="") or "").strip().upper()
    cleanup_expired_discounts()

    ch_data = collections["channels"].find_one()
    if not ch_data:
        return jsonify({"error": "No subscription plans available."}), 404

    now_ts = datetime.now(IST).timestamp()
    active_discounts = (collections["config"].find_one({"_id": "discounts"}) or {}).get("plans", {})
    vouch_doc = collections["config"].find_one({"_id": "vouchers"}) or {}
    codes_map = vouch_doc.get("codes", {})

    available_vouchers = []
    for code, v_data in codes_map.items():
        if float(v_data.get("expires_at", 0)) > now_ts and len(v_data.get("used_by", [])) < v_data.get("limit", 9999999) and user_id not in v_data.get("used_by", []):
            available_vouchers.append({"code": code, "discount": float(v_data["discount"])})

    voucher_applied = None
    voucher_discount = 0.0
    voucher_error = None

    if req_voucher:
        if req_voucher in codes_map:
            v_data = codes_map[req_voucher]
            if float(v_data.get("expires_at", 0)) <= now_ts:
                voucher_error = "This promo code has expired."
            elif user_id in v_data.get("used_by", []):
                voucher_error = "You have already used this promo code."
            elif len(v_data.get("used_by", [])) >= v_data.get("limit", 9999999):
                voucher_error = "This promo code has reached its usage limit."
            else:
                voucher_applied = req_voucher
                voucher_discount = float(v_data["discount"])
                if user_id:
                    update_user_session(user_id, {"$set": {"applied_voucher": req_voucher}})
        else:
            voucher_error = "Invalid promo code."

    plans_list = []
    for p_time, p_price in sorted(ch_data.get("plans", {}).items(), key=lambda x: parse_time_str(x[0])[2]):
        num, unit, calc_mins = parse_time_str(p_time)
        base_price = float(p_price)
        discount_perc = 0.0
        if voucher_discount > 0:
            discount_perc = voucher_discount
        else:
            all_exp = float(active_discounts.get("all", {}).get("expires_at", 0))
            p_exp = float(active_discounts.get(p_time, {}).get("expires_at", 0))
            if "all" in active_discounts and all_exp > now_ts:
                discount_perc = float(active_discounts["all"]["percentage"])
            if p_time in active_discounts and p_exp > now_ts:
                discount_perc = max(discount_perc, float(active_discounts[p_time]["percentage"]))

        final_p = base_price if discount_perc == 0 else round(base_price * (1 - discount_perc / 100.0), 2)
        plans_list.append({
            "p_time": p_time,
            "title": f"{num} {unit}",
            "duration_mins": calc_mins,
            "base_price": base_price,
            "discount_perc": discount_perc,
            "final_price": final_p
        })

    config = collections["config"].find_one({"_id": "settings"}) or {}
    return jsonify({
        "channel_id": ch_data["channel_id"],
        "channel_name": ch_data.get("name", "Premium Audio"),
        "free_trial_enabled": bool(config.get("free_trial_enabled", False)),
        "free_trial_text": config.get("free_trial_text", "24 Hours"),
        "voucher_applied": voucher_applied,
        "voucher_discount": voucher_discount,
        "voucher_error": voucher_error,
        "available_vouchers": available_vouchers,
        "plans": plans_list,
        "razorpay_key_id": RAZORPAY_KEY_ID
    })

@app.route("/api/pay/create", methods=["POST"])
def api_pay_create():
    data = request.get_json(silent=True) or {}
    user_id = int(data.get("user_id") or 0)
    ch_id = int(data.get("ch_id") or 0)
    p_time = str(data.get("p_time") or "").strip()
    req_voucher = (data.get("voucher") or "").strip().upper()

    if not user_id or not p_time:
        return jsonify({"error": "user_id and p_time are required."}), 400

    if is_user_locked(user_id):
        return jsonify({"error": "Your account is temporarily locked. Please wait 5 minutes or contact Admin."}), 403

    cleanup_expired_discounts()
    ch_data = collections["channels"].find_one({"channel_id": ch_id}) if ch_id else collections["channels"].find_one()
    if not ch_data or p_time not in ch_data.get("plans", {}):
        return jsonify({"error": "Selected plan is unavailable."}), 404

    ch_id = int(ch_data["channel_id"])
    base_price = float(ch_data["plans"][p_time])
    now_ts = datetime.now(IST).timestamp()
    active_discounts = (collections["config"].find_one({"_id": "discounts"}) or {}).get("plans", {})

    voucher_perc = 0.0
    if req_voucher:
        vouch_doc = collections["config"].find_one({"_id": "vouchers"}) or {}
        if req_voucher in vouch_doc.get("codes", {}):
            v_data = vouch_doc["codes"][req_voucher]
            if float(v_data.get("expires_at", 0)) > now_ts and len(v_data.get("used_by", [])) < v_data.get("limit", 9999999) and user_id not in v_data.get("used_by", []):
                voucher_perc = float(v_data["discount"])
                update_user_session(user_id, {"$set": {"applied_voucher": req_voucher}})

    if voucher_perc > 0:
        discount_perc = voucher_perc
    else:
        discount_perc = max([
            float(active_discounts.get("all", {}).get("percentage", 0)) if float(active_discounts.get("all", {}).get("expires_at", 0)) > now_ts else 0,
            float(active_discounts.get(p_time, {}).get("percentage", 0)) if float(active_discounts.get(p_time, {}).get("expires_at", 0)) > now_ts else 0
        ])

    final_price = round(base_price * (1 - discount_perc / 100.0), 2) if discount_perc > 0 else round(base_price, 2)
    amount_paise = max(100, int(round(final_price * 100)))
    num, unit, _ = parse_time_str(p_time)

    # 1. Create Razorpay Payment Link (used by webhook, polling, and direct browser checkout)
    plink = create_razorpay_payment_link(user_id, ch_id, p_time, final_price)
    plink_id = plink.get("id") if plink else None
    short_url = plink.get("short_url") if plink else None

    # 2. Create Razorpay Order for embedded on-website payment methods (UPI, Card, NetBanking, Wallet)
    order_id = None
    try:
        order_payload = {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": f"web_{user_id}_{int(time.time())}",
            "notes": {
                "user_id": str(user_id),
                "ch_id": str(ch_id),
                "p_time": str(p_time),
                "amount": f"{final_price:.2f}"
            }
        }
        order_resp = requests.post(
            "https://api.razorpay.com/v1/orders",
            auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
            json=order_payload,
            timeout=12
        )
        if order_resp.status_code in [200, 201]:
            order_id = order_resp.json().get("id")
    except Exception as e:
        logger.warning(f"Razorpay order creation note: {e}")

    if not plink_id and not order_id:
        return jsonify({"error": "Unable to generate Razorpay checkout right now. Please try again."}), 500

    update_user_session(
        user_id,
        {
            "$set": {
                "selected_plan": {
                    "ch_id": ch_id,
                    "p_time": p_time,
                    "price": final_price,
                    "plink_id": plink_id or order_id,
                    "short_url": short_url or "",
                    "used_discount": discount_perc > 0
                }
            }
        }
    )

    return jsonify({
        "key_id": RAZORPAY_KEY_ID,
        "user_id": user_id,
        "ch_id": ch_id,
        "p_time": p_time,
        "plan_title": f"{num} {unit}",
        "amount": final_price,
        "amount_paise": amount_paise,
        "plink_id": plink_id,
        "short_url": short_url,
        "order_id": order_id
    })

def _build_post_payment_response(user_id: int, ch_id: int, p_time: str, pay_id: str) -> dict:
    num, unit, _ = parse_time_str(p_time)
    u_doc = collections["users"].find_one({"user_id": user_id, "channel_id": ch_id}) or {}
    join_link = u_doc.get("direct_join_link", "")
    return {
        "paid": True,
        "auto_joined": True,
        "payment_id": pay_id,
        "join_link": join_link,
        "duration_text": f"{num} {unit}"
    }

@app.route("/api/pay/verify", methods=["POST"])
def api_pay_verify():
    data = request.get_json(silent=True) or {}
    user_id = int(data.get("user_id") or 0)
    ch_id = int(data.get("ch_id") or 0)
    p_time = str(data.get("p_time") or "1m")
    amount = float(data.get("amount") or 0.0)
    plink_id = data.get("plink_id")
    rzp_payment_id = data.get("razorpay_payment_id")
    rzp_order_id = data.get("razorpay_order_id")
    rzp_signature = data.get("razorpay_signature")

    if not user_id:
        return jsonify({"paid": False, "message": "Missing user_id"}), 400

    if rzp_payment_id and rzp_order_id and rzp_signature:
        expected = hmac.new(
            RAZORPAY_KEY_SECRET.encode("utf-8"),
            f"{rzp_order_id}|{rzp_payment_id}".encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        if hmac.compare_digest(expected, rzp_signature):
            activate_subscription_after_payment(user_id, ch_id, p_time, amount, rzp_payment_id)
            return jsonify(_build_post_payment_response(user_id, ch_id, p_time, rzp_payment_id))

    if plink_id and str(plink_id).startswith("plink_"):
        plink_data = fetch_razorpay_payment_link(str(plink_id))
        if plink_data and plink_data.get("status") == "paid":
            notes = plink_data.get("notes") or {}
            u_id = int(notes.get("user_id", user_id))
            c_id = int(notes.get("ch_id", ch_id))
            pt = str(notes.get("p_time", p_time))
            paid_amt = float(notes.get("amount", amount or (float(plink_data.get("amount", 0)) / 100.0)))
            payments_list = plink_data.get("payments") or []
            pay_id = payments_list[0].get("payment_id", plink_id) if payments_list else str(plink_id)
            activate_subscription_after_payment(u_id, c_id, pt, paid_amt, pay_id)
            return jsonify(_build_post_payment_response(u_id, c_id, pt, pay_id))

    order_id = data.get("order_id")
    if order_id and str(order_id).startswith("order_"):
        try:
            resp = requests.get(
                f"https://api.razorpay.com/v1/orders/{order_id}/payments",
                auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
                timeout=10
            )
            if resp.status_code == 200:
                items = resp.json().get("items") or []
                for item in items:
                    if item.get("status") in ["captured", "authorized"]:
                        pay_id = item.get("id")
                        activate_subscription_after_payment(user_id, ch_id, p_time, amount, pay_id)
                        return jsonify(_build_post_payment_response(user_id, ch_id, p_time, pay_id))
        except Exception as e:
            logger.warning(f"Order verify check note: {e}")

    return jsonify({"paid": False, "message": "Payment not captured yet. Please complete payment using any method above."})

@app.route("/api/pay/free_trial", methods=["POST"])
def api_pay_free_trial():
    data = request.get_json(silent=True) or {}
    user_id = int(data.get("user_id") or 0)
    if not user_id:
        return jsonify({"error": "Missing user_id"}), 400
    config = collections["config"].find_one({"_id": "settings"}) or {}
    if not config.get("free_trial_enabled", False):
        return jsonify({"error": "Free trial is currently disabled."}), 400
    ch_data = collections["channels"].find_one()
    if not ch_data:
        return jsonify({"error": "No channel configured."}), 404
    ch_id = int(ch_data["channel_id"])
    now_ts = datetime.now(IST).timestamp()
    existing = collections["users"].find_one({"user_id": user_id, "channel_id": ch_id})
    if existing and existing.get("status") == "active" and float(existing.get("expiry") or 0) > now_ts:
        return jsonify({"error": "You already have an active subscription/trial!"}), 400

    process_free_trial_logic(user_id, ch_id, user_id)
    updated_doc = collections["users"].find_one({"user_id": user_id, "channel_id": ch_id}) or {}
    join_link = updated_doc.get("direct_join_link", "")
    trial_text = config.get("free_trial_text", "24 Hours")
    return jsonify({
        "activated": True,
        "auto_joined": True,
        "join_link": join_link,
        "duration_text": trial_text,
        "message": f"🎉 Your {trial_text} Free Trial is now active! All chapters are unlocked."
    })

@app.route("/api/sync_state")
def api_sync_state():
    """Returns a fast signature of novels, audio counts, cover images, views, reviews, theme, and user access for real-time website auto-sync."""
    user_id = request.args.get("user_id", type=int, default=0)
    try:
        novels_docs = list(collections["novels"].find(
            {"status": {"$in": ["ongoing", "completed", "adult"]}},
            {"name": 1, "status": 1, "link": 1, "cover_msg_id": 1, "free_audios": 1, "views": 1, "plays": 1, "description": 1, "author": 1, "narrator": 1}
        ).sort("name", 1))
        audio_total = collections["novel_audios"].count_documents({})
        rev_total = collections["reviews"].count_documents({})
        ch_doc = collections["channels"].find_one({}, {"plans": 1, "name": 1}) or {}
        disc_doc = collections["config"].find_one({"_id": "discounts"}) or {}
        vouch_doc = collections["config"].find_one({"_id": "vouchers"}) or {}
        set_doc = collections["config"].find_one({"_id": "settings"}) or {}
        theme_doc = collections["config"].find_one({"_id": "web_theme"}) or {}
        user_doc = collections["users"].find_one({"user_id": user_id}, {"status": 1, "expiry": 1}) or {}

        raw_state = json.dumps({
            "n": [(
                str(x.get("_id")), x.get("name"), x.get("status"), x.get("link"),
                x.get("cover_msg_id"), x.get("free_audios", 10),
                x.get("views", 0), x.get("plays", 0),
                x.get("description", ""), x.get("author", ""), x.get("narrator", "")
            ) for x in novels_docs],
            "a": audio_total,
            "rv": rev_total,
            "p": ch_doc.get("plans", {}),
            "d": str(disc_doc.get("plans", {})),
            "v": list((vouch_doc.get("codes") or {}).keys()),
            "ft": set_doc.get("free_trial_enabled", False),
            "sn": set_doc.get("sub_modal_note", ""),
            "th": theme_doc.get("theme", {}),
            "u": (user_doc.get("status"), user_doc.get("expiry"), user_id == ADMIN_ID)
        }, sort_keys=True)
        sig = hashlib.md5(raw_state.encode("utf-8")).hexdigest()
        return jsonify({"signature": sig})
    except Exception as e:
        return jsonify({"signature": str(int(time.time() // 30)), "note": str(e)})

@app.route("/api/novel_cover/<int:topic_id>")
def api_novel_cover(topic_id: int):
    """Serves cached topic cover image without triggering FloodWait or 404 terminal spam."""
    if topic_id in COVER_BYTES_CACHE:
        img_bytes, mime = COVER_BYTES_CACHE[topic_id]
        return Response(img_bytes, mimetype=mime, headers={"Cache-Control": "public, max-age=3600"})
    if topic_id in NO_COVER_TOPICS:
        return Response(status=204)

    active_client = get_active_tg_client()
    if not active_client:
        return Response(status=204)

    novel_doc = collections["novels"].find_one({"link": {"$regex": f"/{topic_id}$"}}) or {}
    cover_msg_id = novel_doc.get("cover_msg_id")
    if not cover_msg_id:
        NO_COVER_TOPICS.add(topic_id)
        return Response(status=204)

    async def _fetch_cover():
        if not active_client.is_connected():
            await active_client.connect()
        entity = await _resolve_group_entity(active_client)
        m = MSG_CACHE.get(int(cover_msg_id)) or await active_client.get_messages(entity, ids=int(cover_msg_id))
        if not m or not is_image_telethon_message(m):
            return None, None
        MSG_CACHE[int(cover_msg_id)] = m
        data_bytes = await active_client.download_media(m.media, file=bytes)
        mime_type = getattr(getattr(m, "file", None), "mime_type", None) or "image/jpeg"
        return data_bytes, mime_type

    try:
        img_bytes, mime = asyncio.run_coroutine_threadsafe(_fetch_cover(), telethon_loop).result(timeout=10)
        if img_bytes:
            COVER_BYTES_CACHE[topic_id] = (img_bytes, mime)
            return Response(img_bytes, mimetype=mime, headers={"Cache-Control": "public, max-age=3600"})
    except Exception:
        pass

    NO_COVER_TOPICS.add(topic_id)
    return Response(status=204)

@app.route("/api/novels")
def api_novels():
    user_id = request.args.get("user_id", type=int, default=0)
    has_access = user_has_novel_access(user_id)

    all_revs = list(collections["reviews"].find({"topic_id": {"$gt": 0}}, {"topic_id": 1, "rating": 1}))
    rev_map = {}
    for r in all_revs:
        tid = int(r.get("topic_id") or 0)
        if tid > 0:
            rev_map.setdefault(tid, []).append(int(r.get("rating") or 5))

    novels_cursor = collections["novels"].find({"status": {"$in": ["ongoing", "completed", "adult"]}}).sort("name", 1)
    novels_list = []
    for n in novels_cursor:
        t_id = extract_topic_id_from_link(n.get("link", ""))
        audio_count = collections["novel_audios"].count_documents({"topic_id": t_id}) if t_id else 0
        ratings_for_novel = rev_map.get(int(t_id or 0), [])
        rev_count = len(ratings_for_novel)
        avg_rating = round(sum(ratings_for_novel) / rev_count, 1) if rev_count > 0 else 5.0

        novels_list.append({
            "id": str(n["_id"]),
            "name": n.get("name", "Untitled"),
            "status": n.get("status", "ongoing"),
            "link": n.get("link", ""),
            "topic_id": t_id,
            "cover_msg_id": n.get("cover_msg_id", 0),
            "free_audios": int(n.get("free_audios", 10)) if n.get("free_audios") is not None else 10,
            "audio_count": audio_count,
            "views": int(n.get("views", 0)),
            "plays": int(n.get("plays", 0)),
            "review_count": rev_count,
            "avg_rating": avg_rating,
            "description": n.get("description", ""),
            "author": n.get("author", ""),
            "narrator": n.get("narrator", "")
        })
    return jsonify({"novels": novels_list, "has_access": has_access})

@app.route("/api/topic/<int:topic_id>/audios")
def api_topic_audios(topic_id: int):
    user_id = request.args.get("user_id", type=int, default=0)
    novel_name = request.args.get("novel_name", default="")
    refresh = request.args.get("refresh", default="0") == "1"
    has_access = user_has_novel_access(user_id)

    audios = list(collections["novel_audios"].find({"topic_id": topic_id}, {"_id": 0}))
    audios.sort(key=lambda x: (x.get("chapter_num", x["message_id"]), x["message_id"]))

    debug_note = ""
    mode_label = "User-MTProto" if tg_auth_state.get("user_authorized") else "Bot-Fallback"

    if not audios or refresh:
        try:
            future = asyncio.run_coroutine_threadsafe(
                _scan_topic_audios(topic_id, novel_name=novel_name),
                telethon_loop
            )
            scanned, debug_note = future.result(timeout=25)
            if scanned:
                audios = scanned
        except Exception as e:
            debug_note = f"Scan note: {type(e).__name__}: {e}"
            logger.warning(f"Live topic scan note: {type(e).__name__}: {e}")
            audios = list(collections["novel_audios"].find({"topic_id": topic_id}, {"_id": 0}))
            audios.sort(key=lambda x: (x.get("chapter_num", x["message_id"]), x["message_id"]))

    free_limit = get_topic_free_audio_limit(topic_id)
    for idx, ep in enumerate(audios):
        ep["free_rank"] = idx + 1
        ep["locked"] = False if (has_access or idx < free_limit) else True
        ep["views"] = int(ep.get("views", 0))

    novel_doc = collections["novels"].find_one({"link": {"$regex": f"/{topic_id}$"}}) or {}

    return jsonify({
        "audios": audios,
        "has_access": has_access,
        "free_limit": free_limit,
        "engine": mode_label,
        "debug_note": debug_note,
        "novel_views": int(novel_doc.get("views", 0)),
        "novel_plays": int(novel_doc.get("plays", 0)),
        "description": novel_doc.get("description", ""),
        "author": novel_doc.get("author", ""),
        "narrator": novel_doc.get("narrator", "")
    })

STREAM_BLOCK_SIZE = 131072  # 128 KB aligned blocks (divides 1 MB evenly for ultra-fast MTProto seeking)

def _get_or_create_ram_cache_entry(track_msg_id: int, msg, file_size: int, mime_type: str) -> dict:
    now = time.time()
    if track_msg_id in AUDIO_RAM_CACHE:
        AUDIO_RAM_CACHE[track_msg_id]["last_access"] = now
        return AUDIO_RAM_CACHE[track_msg_id]

    while len(AUDIO_RAM_CACHE) >= MAX_RAM_CACHE_TRACKS:
        oldest_id = min(AUDIO_RAM_CACHE.keys(), key=lambda k: AUDIO_RAM_CACHE[k]["last_access"])
        AUDIO_RAM_CACHE.pop(oldest_id, None)

    entry = {
        "blocks": {},
        "file_size": file_size or 0,
        "mime_type": mime_type,
        "last_access": now
    }
    AUDIO_RAM_CACHE[track_msg_id] = entry
    return entry

@app.route("/stream/<int:user_id>/<int:track_msg_id>", methods=["GET", "HEAD", "OPTIONS"])
def stream_large_audio(user_id: int, track_msg_id: int):
    if request.method == "OPTIONS":
        return Response(status=200)
    """
    Ultra-high-speed instant-seeking audio stream with 128 KB Block RAM Cache.
    Zero background download contention: 100% of MTProto bandwidth goes to the exact seek position.
    """
    if not can_user_stream_audio(user_id, track_msg_id):
        return "Unauthorized: Subscription required.", 403
    active_client = get_active_tg_client()
    if not active_client:
        return "Telethon client initializing.", 503

    # Fast synchronous lookup in MSG_CACHE (0ms latency on seek)
    msg = MSG_CACHE.get(track_msg_id)
    if not msg or not getattr(msg, "media", None):
        async def _get_msg():
            if not active_client.is_connected():
                await active_client.connect()
            entity = await _resolve_group_entity(active_client)
            m = await active_client.get_messages(entity, ids=track_msg_id)
            if m and getattr(m, "media", None):
                MSG_CACHE[track_msg_id] = m
            return m

        try:
            msg = asyncio.run_coroutine_threadsafe(_get_msg(), telethon_loop).result(timeout=15)
        except Exception as e:
            return f"Error fetching audio track: {e}", 500

    if not msg or not getattr(msg, "media", None):
        return "Audio media not found.", 404

    file_size = getattr(msg.file, "size", None)
    if not file_size:
        doc = getattr(msg, "document", None) or getattr(getattr(msg, "media", None), "document", None)
        file_size = getattr(doc, "size", None) if doc else None

    mime_type = getattr(msg.file, "mime_type", None) or "audio/mpeg"
    if mime_type in ("application/octet-stream", "audio/mp3"):
        mime_type = "audio/mpeg"

    range_header = request.headers.get("Range", None)
    offset = 0
    end = (file_size - 1) if file_size else None
    status_code = 200

    if range_header and file_size:
        match = re.search(r"bytes=(\d+)-(\d*)", range_header)
        if match:
            offset = min(int(match.group(1)), file_size - 1)
            if match.group(2):
                end = min(int(match.group(2)), file_size - 1)
            status_code = 206

    length = (end - offset + 1) if (file_size and end is not None) else None
    is_small_prewarm = bool(length is not None and length <= (STREAM_BLOCK_SIZE * 2) and offset == 0)

    # Cancel previous active streams for this user ONLY when starting/seeking a main playback stream
    if not is_small_prewarm:
        prev_streams = ACTIVE_USER_STREAMS.pop(user_id, [])
        for _, old_stop, old_fut, old_q in prev_streams:
            old_stop.set()
            with suppress(Exception):
                old_q.put_nowait(None)
            old_fut.cancel()

    ram_entry = _get_or_create_ram_cache_entry(track_msg_id, msg, file_size, mime_type)
    blocks_map = ram_entry["blocks"]

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Type": mime_type,
        "Content-Disposition": "inline",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=3600"
    }
    if length is not None and file_size:
        headers["Content-Length"] = str(length)
        if status_code == 206:
            headers["Content-Range"] = f"bytes {offset}-{end}/{file_size}"

    if request.method == "HEAD":
        return Response(status=status_code, headers=headers)

    def generate():
        cur_pos = offset
        target_end = (end + 1) if end is not None else file_size

        # 1. Serve any consecutive 128 KB blocks already cached in RAM in 0ms
        while target_end is None or cur_pos < target_end:
            blk_idx = cur_pos // STREAM_BLOCK_SIZE
            blk_data = blocks_map.get(blk_idx)
            if blk_data is None:
                break
            blk_offset = cur_pos - (blk_idx * STREAM_BLOCK_SIZE)
            slice_data = blk_data[blk_offset:]
            if target_end is not None and cur_pos + len(slice_data) > target_end:
                slice_data = slice_data[:target_end - cur_pos]
            if not slice_data:
                break
            yield bytes(slice_data)
            cur_pos += len(slice_data)

        if target_end is not None and cur_pos >= target_end:
            return

        # 2. Stream directly from the aligned 128 KB seek block via Telethon MTProto
        aligned_offset = (cur_pos // STREAM_BLOCK_SIZE) * STREAM_BLOCK_SIZE
        skip_bytes = cur_pos - aligned_offset
        rem_length = (target_end - cur_pos) if target_end is not None else None

        q = queue.Queue(maxsize=32)
        stop_event = Event()

        async def _producer():
            try:
                bytes_sent = 0
                cur_blk_idx = aligned_offset // STREAM_BLOCK_SIZE
                first_chunk = True
                async for raw_chunk in active_client.iter_download(
                    msg.media,
                    offset=aligned_offset,
                    request_size=STREAM_BLOCK_SIZE
                ):
                    if stop_event.is_set():
                        break
                    if not raw_chunk:
                        break
                    # Convert bytearray/memoryview to bytes immediately so Werkzeug WSGI never fails assertion
                    raw_bytes = bytes(raw_chunk)

                    # Cache aligned 128 KB block in RAM for instant backward/forward re-seeks
                    if len(blocks_map) < 240:
                        blocks_map[cur_blk_idx] = raw_bytes
                    cur_blk_idx += 1

                    out_chunk = raw_bytes
                    if first_chunk and skip_bytes > 0:
                        out_chunk = out_chunk[skip_bytes:]
                        first_chunk = False
                    else:
                        first_chunk = False

                    if not out_chunk:
                        continue
                    if rem_length is not None and bytes_sent + len(out_chunk) > rem_length:
                        out_chunk = out_chunk[:rem_length - bytes_sent]

                    while not stop_event.is_set():
                        try:
                            q.put(out_chunk, timeout=0.1)
                            break
                        except queue.Full:
                            await asyncio.sleep(0.02)

                    bytes_sent += len(out_chunk)
                    if rem_length is not None and bytes_sent >= rem_length:
                        break
            except Exception:
                pass
            finally:
                with suppress(Exception):
                    q.put_nowait(None)

        fut = asyncio.run_coroutine_threadsafe(_producer(), telethon_loop)
        if not is_small_prewarm:
            ACTIVE_USER_STREAMS.setdefault(user_id, []).append((track_msg_id, stop_event, fut, q))

        idle_seconds = 0.0
        try:
            while not stop_event.is_set():
                try:
                    chunk = q.get(timeout=1.0)
                    idle_seconds = 0.0
                except queue.Empty:
                    idle_seconds += 1.0
                    if stop_event.is_set() or fut.done() or idle_seconds >= 25.0:
                        break
                    continue
                if chunk is None:
                    break
                yield bytes(chunk)
        except GeneratorExit:
            stop_event.set()
        finally:
            stop_event.set()
            fut.cancel()

    return Response(stream_with_context(generate()), status=status_code, headers=headers)

@app.route("/razorpay-webhook", methods=["POST"])
@app.route("/webhook", methods=["POST"])
def razorpay_webhook():
    payload_body = request.get_data(as_text=True)
    signature = request.headers.get("X-Razorpay-Signature", "")

    if RAZORPAY_WEBHOOK_SECRET:
        expected_sig = hmac.new(
            RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
            payload_body.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected_sig, signature):
            logger.warning("Invalid Razorpay Webhook Signature!")
            return jsonify({"status": "invalid_signature"}), 400

    try:
        data = json.loads(payload_body)
        event = data.get("event", "")
        payload = data.get("payload", {})

        notes = {}
        payment_id = None
        amount_paid = 0.0

        if event == "payment_link.paid":
            plink_entity = payload.get("payment_link", {}).get("entity", {})
            pay_entity = payload.get("payment", {}).get("entity", {})
            notes = plink_entity.get("notes") or pay_entity.get("notes") or {}
            payment_id = pay_entity.get("id") or plink_entity.get("id")
            amount_paid = float(plink_entity.get("amount", 0)) / 100.0

        elif event in ["payment.captured", "order.paid"]:
            pay_entity = payload.get("payment", {}).get("entity", {})
            order_entity = payload.get("order", {}).get("entity", {})
            notes = pay_entity.get("notes") or order_entity.get("notes") or {}
            payment_id = pay_entity.get("id") or order_entity.get("id")
            amount_paid = float(pay_entity.get("amount", 0)) / 100.0

        if notes and "user_id" in notes and "ch_id" in notes and "p_time" in notes:
            u_id = int(notes["user_id"])
            ch_id = int(notes["ch_id"])
            p_time = str(notes["p_time"])
            paid_val = float(notes.get("amount", amount_paid))
            Thread(
                target=activate_subscription_after_payment,
                args=(u_id, ch_id, p_time, paid_val, payment_id or f"rzp_{int(time.time())}"),
                daemon=True
            ).start()

        return jsonify({"status": "ok"}), 200
    except Exception as e:
        logger.error(f"Error processing Razorpay webhook: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

def keep_alive() -> None:
    Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, threaded=True), daemon=True).start()

# --- RAZORPAY API HELPERS ---
def create_razorpay_payment_link(user_id: int, ch_id: int, p_time: str, amount_inr: float) -> Optional[dict]:
    """Creates a unique Razorpay Payment Link with metadata notes for automated webhook capture."""
    amount_paise = max(100, int(round(amount_inr * 100)))
    num, unit, _ = parse_time_str(p_time)
    ref_id = f"sub_{user_id}_{int(time.time())}_{random.randint(100, 999)}"
    expire_by = int(time.time()) + 1200  # 20 minutes from now

    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "accept_partial": False,
        "expire_by": expire_by,
        "reference_id": ref_id,
        "description": f"Audio Subscription Plan: {num} {unit}",
        "notes": {
            "user_id": str(user_id),
            "ch_id": str(ch_id),
            "p_time": str(p_time),
            "amount": f"{amount_inr:.2f}"
        },
        "reminder_enable": False
    }

    try:
        resp = requests.post(
            "https://api.razorpay.com/v1/payment_links",
            auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
            json=payload,
            timeout=15
        )
        if resp.status_code in [200, 201]:
            return resp.json()
        logger.error(f"Razorpay Link Creation Failed ({resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Exception creating Razorpay payment link: {e}")
    return None

def fetch_razorpay_payment_link(plink_id: str) -> Optional[dict]:
    """Fetches the live status of a Razorpay Payment Link."""
    try:
        resp = requests.get(
            f"https://api.razorpay.com/v1/payment_links/{plink_id}",
            auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        logger.error(f"Error fetching Razorpay link {plink_id}: {e}")
    return None

def activate_subscription_after_payment(u_id: int, ch_id: int, p_time: str, paid_amount: float, payment_id: str) -> bool:
    """Automatically activates or extends a user's subscription once Razorpay payment is confirmed."""
    # Idempotency check to ensure webhook + button click never double-activate
    if payment_id and collections["payments"].find_one({"payment_id": payment_id}):
        return False

    num, unit, calc_mins = parse_time_str(p_time)
    ch_data = collections["channels"].find_one({"channel_id": ch_id}) or {}
    user_display = get_user_display(u_id)

    sess = get_user_session(u_id)
    applied_voucher = sess.get("applied_voucher")
    menu_msg_id = sess.get("menu_msg_id")

    if menu_msg_id:
        with suppress(ApiTelegramException):
            bot.delete_message(u_id, menu_msg_id)

    update_user_session(
        u_id,
        {"$unset": {"pending_approval": "", "selected_plan": "", f"price_cache.{ch_id}_{p_time}": "", "applied_voucher": "", "menu_msg_id": ""}}
    )

    try:
        if applied_voucher:
            collections["config"].update_one({"_id": "vouchers"}, {"$push": {f"codes.{applied_voucher}.used_by": u_id}})

        collections["payments"].insert_one({
            "user_id": u_id,
            "amount": paid_amount,
            "payment_id": payment_id,
            "timestamp": datetime.now(IST).timestamp()
        })
        collections["config"].update_one({"_id": "bot_stats"}, {"$inc": {"lifetime_total": paid_amount}}, upsert=True)

        doc = io.BytesIO(
            f"===========================\n"
            f"PAYMENT TRANSACTION RECEIPT\n"
            f"===========================\n"
            f"Transaction ID: {payment_id}\n"
            f"User ID: {u_id}\n"
            f"User Name: {user_display}\n"
            f"Channel: {ch_data.get('name', str(ch_id))}\n"
            f"Plan: {num} {unit}\n"
            f"Amount Paid: INR {paid_amount:.2f}\n"
            f"Date & Time: {datetime.now(IST).strftime('%Y-%m-%d %I:%M:%S %p IST')}\n"
            f"Status: PAID (RAZORPAY VERIFIED)\n"
            f"===========================".encode('utf-8')
        )
        doc.name = f"Receipt_{u_id}_{int(time.time())}.txt"
        doc.seek(0)

        is_already_in = False
        with suppress(ApiTelegramException):
            is_already_in = bot.get_chat_member(ch_id, u_id).status in ["member", "administrator", "creator"]

        if is_already_in:
            current_user = collections["users"].find_one({"user_id": u_id, "channel_id": ch_id, "status": "active"})
            new_expiry = (float(current_user["expiry"]) if current_user and current_user.get("expiry") else datetime.now(IST).timestamp()) + (calc_mins * 60)

            collections["users"].update_one(
                {"user_id": u_id, "channel_id": ch_id},
                {
                    "$set": {"status": "active", "duration_mins": calc_mins, "expiry": new_expiry, "pending_attempts": 0},
                    "$unset": {"locked_at": ""},
                    "$inc": {"total_purchases": 1, "total_mins": calc_mins},
                    "$push": {"history": {"joined_at": datetime.now(IST).timestamp(), "expires_at": new_expiry, "duration_mins": calc_mins, "note": "razorpay_auto_renewed"}}
                },
                upsert=True
            )

            bot.send_message(u_id, f"🥳 *Payment Successful & Verified!*\n\nYour subscription for {num} {unit} has been extended automatically since you are already in the channel!", parse_mode="Markdown")
            with suppress(ApiTelegramException):
                bot.send_document(u_id, doc, caption="📄 Your Official Razorpay Payment Receipt")
            with suppress(ApiTelegramException):
                bot.send_message(ADMIN_ID, f"✅ Razorpay Payment Captured (`{payment_id}`).\nUser `{u_id}` ({user_display}) renewed for {num} {unit} (₹{paid_amount:.2f}).", parse_mode="Markdown")

            user_doc = collections["users"].find_one({"user_id": u_id})
            topic_id = user_doc.get("topic_id") if user_doc else None
            log_text = (
                f"🟢 *Subscription Renewed (Razorpay)*\n\n"
                f"👤 User: {user_display}\n"
                f"ID: `{u_id}`\n"
                f"💳 Payment ID: `{payment_id}`\n"
                f"📺 Channel: {ch_data.get('name', 'Unknown')}\n"
                f"⏳ Added: {num} {unit}\n"
                f"⏰ New Expiry: {datetime.fromtimestamp(new_expiry, IST).strftime('%Y-%m-%d %I:%M %p IST')}\n"
                f"📊 Total Times Subscribed: {(collections['users'].find_one({'user_id': u_id, 'channel_id': ch_id}) or {}).get('total_purchases', 1)}\n"
                f"⏳ Total Time Subscribed: {(collections['users'].find_one({'user_id': u_id, 'channel_id': ch_id}) or {}).get('total_mins', calc_mins)} mins"
            )
            with suppress(ApiTelegramException):
                if topic_id:
                    bot.send_message(FORUM_GROUP_ID, log_text, parse_mode="Markdown", message_thread_id=topic_id)
                else:
                    bot.send_message(LOG_CHANNEL_ID, log_text, parse_mode="Markdown")

            novel_text = ""
            for status, title in [("completed", "Completed"), ("ongoing", "Ongoing")]:
                novels = list(collections["novels"].find({"status": status}))
                if novels:
                    novel_text += f"{'📚' if status == 'completed' else '🔄'} <b>{title} Novels:</b>\n" + "".join(
                        f"🔹 <a href='{n.get('link', '#')}'>{n['name']}</a>\n" if n.get("link") else f"🔹 {n['name']}\n" for n in novels
                    ) + "\n"
            if novel_text.strip():
                with suppress(ApiTelegramException):
                    bot.send_message(u_id, f"📚 <b>Available Novels:</b>\n\n{novel_text.strip()}", parse_mode="HTML", disable_web_page_preview=True)

        else:
            direct_link = create_instant_channel_join_link(ch_id, u_id)
            new_expiry = datetime.now(IST).timestamp() + (calc_mins * 60)
            collections["users"].update_one(
                {"user_id": u_id, "channel_id": ch_id},
                {
                    "$set": {
                        "status": "active",
                        "duration_mins": calc_mins,
                        "expiry": new_expiry,
                        "pending_attempts": 0,
                        "direct_join_link": direct_link
                    },
                    "$unset": {"locked_at": "", "pending_join_link": ""},
                    "$inc": {"total_purchases": 1, "total_mins": calc_mins},
                    "$push": {"history": {"joined_at": datetime.now(IST).timestamp(), "expires_at": new_expiry, "duration_mins": calc_mins, "note": "razorpay_auto_activated"}}
                },
                upsert=True
            )

            join_markup = InlineKeyboardMarkup(row_width=1).add(InlineKeyboardButton("🚀 Quick Join Channel", url=direct_link)) if direct_link else None
            bot.send_message(
                u_id,
                f"🥳 *Payment Successful & Activated!*\n\nSubscription: *{num} {unit}*\n\nYour plan is now active immediately! Tap below to join the channel directly:",
                parse_mode="Markdown",
                protect_content=True,
                reply_markup=join_markup
            )
            with suppress(ApiTelegramException):
                bot.send_document(u_id, doc, caption="📄 Your Official Razorpay Payment Receipt")
            with suppress(ApiTelegramException):
                bot.send_message(ADMIN_ID, f"✅ Razorpay Payment Captured (`{payment_id}`).\nUser `{u_id}` ({user_display}) approved for {num} {unit} (₹{paid_amount:.2f}) and sent join link.", parse_mode="Markdown")

            user_doc = collections["users"].find_one({"user_id": u_id})
            topic_id = user_doc.get("topic_id") if user_doc else None
            log_text = (
                f"🟢 *User Approved via Razorpay (Pending Join)*\n\n"
                f"👤 User: {user_display}\n"
                f"ID: `{u_id}`\n"
                f"💳 Payment ID: `{payment_id}`\n"
                f"📺 Channel: {ch_data.get('name', str(ch_id))}\n"
                f"Plan: {num} {unit}"
            )
            with suppress(ApiTelegramException):
                if topic_id:
                    bot.send_message(FORUM_GROUP_ID, log_text, parse_mode="Markdown", message_thread_id=topic_id)
                else:
                    bot.send_message(LOG_CHANNEL_ID, log_text, parse_mode="Markdown")

        return True
    except Exception as e:
        logger.error(f"Error activating subscription after Razorpay payment: {e}")
        return False

def check_pending_razorpay_links() -> None:
    """Background task that checks pending Razorpay payment links and auto-captures paid ones."""
    try:
        pending_sessions = list(collections["session"].find({"selected_plan.plink_id": {"$exists": True}}))
        for sess in pending_sessions:
            u_id = sess["_id"]
            plan = sess.get("selected_plan", {})
            plink_id = plan.get("plink_id")
            if not plink_id:
                continue
            plink_data = fetch_razorpay_payment_link(plink_id)
            if plink_data and plink_data.get("status") == "paid":
                payments_list = plink_data.get("payments") or []
                pay_id = payments_list[0].get("payment_id", plink_id) if payments_list else plink_id
                activate_subscription_after_payment(
                    u_id,
                    int(plan["ch_id"]),
                    str(plan["p_time"]),
                    float(plan["price"]),
                    pay_id
                )
    except Exception as e:
        logger.error(f"Error in check_pending_razorpay_links: {e}")

# --- FORUM TOPIC AUTOMATION ---
def ensure_user_forum_topic(user_id: int, username: str) -> int:
    """Creates a topic in the private group for the user if it doesn't exist."""
    user_doc = collections["users"].find_one({"user_id": user_id})
    if user_doc and user_doc.get("topic_id"):
        return user_doc["topic_id"]

    try:
        topic_name = f"User {username or user_id} ({user_id})"
        topic = bot.create_forum_topic(chat_id=FORUM_GROUP_ID, name=topic_name)
        collections["users"].update_one({"user_id": user_id}, {"$set": {"topic_id": topic.message_thread_id}}, upsert=True)
        bot.send_message(
            FORUM_GROUP_ID,
            f"✅ Topic automatically created for {topic_name}.\n\n💬 Any messages sent by the user will appear here, and any replies you send in this topic will automatically go to their DM.",
            message_thread_id=topic.message_thread_id
        )
        return topic.message_thread_id
    except Exception as e:
        logger.error(f"Failed to create forum topic for {user_id}: {e}")
        return None

# --- USER ACTIVITY & CLICK TRACKING & FORWARDING ---
def log_activity(user_id: int, action_data: Optional[str] = None, message: Optional[Message] = None) -> None:
    now = time.time()
    user_doc = collections["users"].find_one({"user_id": user_id}) or {}

    collections["users"].update_one(
        {"user_id": user_id},
        {"$set": {"last_seen": now}},
        upsert=True
    )

    stop_log = user_doc.get("stop_log", False)
    topic_id = user_doc.get("topic_id")

    if action_data:
        collections["click_history"].update_one(
            {"user_id": user_id, "button": action_data},
            {"$set": {"timestamp": now}},
            upsert=True
        )
        if not action_data.startswith("/") and user_id != ADMIN_ID and not stop_log:
            try:
                chat_info = bot.get_chat(user_id)
                raw_name = f"{chat_info.first_name or 'Unknown'} {f'(@{chat_info.username})' if chat_info.username else ''}".strip()
                safe_name = raw_name.replace('<', '&lt;').replace('>', '&gt;').replace('&', '&amp;')
                safe_button = str(action_data).replace('<', '&lt;').replace('>', '&gt;')
                log_msg = f"🖱 <b>Live Activity Log</b>\n\n👤 User Name: <a href='tg://user?id={user_id}'>{safe_name}</a>\n🆔 User ID: <code>{user_id}</code>\n🔘 Action: <code>{safe_button}</code>"

                bot.send_message(TRACKING_CHANNEL_ID, log_msg, parse_mode="HTML")
            except Exception:
                pass

    if message and message.chat.id not in [ADMIN_ID, FORUM_GROUP_ID] and not stop_log:
        try:
            if topic_id:
                bot.forward_message(FORUM_GROUP_ID, message.chat.id, message.message_id, message_thread_id=topic_id)
            else:
                bot.forward_message(TRACKING_CHANNEL_ID, message.chat.id, message.message_id)
        except Exception:
            pass

def cleanup_expired_clicks() -> None:
    cutoff = time.time() - (10 * 86400)
    try:
        collections["click_history"].delete_many({"timestamp": {"$lt": cutoff}})
    except Exception as e:
        logger.error(f"Error cleaning up click history: {e}")

# --- SESSION MANAGEMENT & ONE-TIME CLICK LOCK ---
CLICK_CACHE = {}

def check_and_lock_click(chat_id: int, message_id: int) -> bool:
    cache_key = f"{chat_id}_{message_id}"
    now = time.time()

    expired = [k for k, v in CLICK_CACHE.items() if isinstance(v, (int, float)) and now - v > 3600]
    for k in expired:
        del CLICK_CACHE[k]

    if cache_key in CLICK_CACHE:
        if isinstance(CLICK_CACHE[cache_key], (int, float)):
            with suppress(ApiTelegramException):
                bot.edit_message_reply_markup(chat_id, message_id, reply_markup=None)
            return True

    CLICK_CACHE[cache_key] = now
    return False

def unlock_click(chat_id: int, message_id: int) -> None:
    CLICK_CACHE.pop(f"{chat_id}_{message_id}", None)

def get_user_session(user_id: int) -> dict:
    return collections["session"].find_one({"_id": user_id}) or {"_id": user_id}

def update_user_session(user_id: int, update_data: dict) -> None:
    collections["session"].update_one({"_id": user_id}, update_data, upsert=True)

def clear_previous_admin_menu(chat_id: int) -> None:
    session = get_user_session(chat_id)
    if session.get("last_admin_menu_id"):
        with suppress(ApiTelegramException):
            bot.delete_message(chat_id, session["last_admin_menu_id"])
        update_user_session(chat_id, {"$unset": {"last_admin_menu_id": ""}})

def set_admin_menu(chat_id: int, msg_id: int) -> None:
    update_user_session(chat_id, {"$set": {"last_admin_menu_id": msg_id}})


# --- ANTI-SPAM LOCK & AUTO-UNLOCK (5 MINS) ---
def is_user_locked(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return False
    user_doc = collections["users"].find_one({"user_id": user_id})
    if user_doc and user_doc.get("pending_attempts", 0) >= 1:
        lock_time = user_doc.get("locked_at", 0)
        if time.time() - lock_time > 300:
            collections["users"].update_one({"user_id": user_id}, {"$set": {"pending_attempts": 0}, "$unset": {"locked_at": ""}})
            return False
        return True
    return False

def send_locked_message(chat_id: int, user_id: int) -> None:
    user_doc = collections["users"].find_one({"user_id": user_id})
    locked_at = user_doc.get("locked_at", 0) if user_doc else 0
    remaining = int(300 - (time.time() - locked_at))
    if remaining < 0:
        remaining = 0

    mins, secs = divmod(remaining, 60)
    time_str = f"{mins}m {secs}s"

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("📞 Contact Admin", url=CONTACT_URL))

    bot.send_message(
        chat_id,
        f"⚠️ *Account Locked*\n\n⏳ *Your account will auto-unlock in: {time_str}*\n\nIf you need assistance, please contact the Admin below.",
        reply_markup=markup,
        parse_mode="Markdown"
    )

# --- UTILITIES ---
def generate_voucher_code() -> str:
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))

def cleanup_expired_discounts() -> None:
    try:
        now_ts = datetime.now(IST).timestamp()
        disc_doc = collections["config"].find_one({"_id": "discounts"})

        if disc_doc and "plans" in disc_doc:
            expired_keys = {f"plans.{tgt}": "" for tgt, data in disc_doc["plans"].items() if float(data.get("expires_at", 0)) <= now_ts}
            if expired_keys:
                collections["config"].update_one({"_id": "discounts"}, {"$unset": expired_keys})

        vouch_doc = collections["config"].find_one({"_id": "vouchers"})
        if vouch_doc and "codes" in vouch_doc:
            expired_v = {f"codes.{code}": "" for code, data in vouch_doc["codes"].items() if float(data.get("expires_at", 0)) <= now_ts}
            if expired_v:
                collections["config"].update_one({"_id": "vouchers"}, {"$unset": expired_v})
    except Exception:
        pass

def trigger_global_restart(b_msg: Optional[str] = None) -> int:
    active_pending = {s["_id"] for s in collections["session"].find({"pending_approval": {"$exists": True}})}
    active_users = list(collections["users"].find({"status": "active"}).distinct("user_id"))
    other_users = list(collections["users"].find({"status": {"$ne": "active"}}).distinct("user_id"))

    seen = set()
    all_users = []
    for u in active_users + other_users:
        if u not in seen:
            seen.add(u)
            all_users.append(u)

    count = 0
    for u in all_users:
        if u == ADMIN_ID or u in active_pending:
            continue

        session = get_user_session(u)
        if session.get("menu_msg_id"):
            with suppress(ApiTelegramException):
                bot.delete_message(u, session["menu_msg_id"])
            update_user_session(u, {"$unset": {"menu_msg_id": ""}})

        if b_msg:
            try:
                bot.send_message(u, b_msg, parse_mode="Markdown")
                count += 1
                time.sleep(0.04)
            except Exception:
                pass

        with suppress(Exception):
            _handle_restart(u, u)
            time.sleep(0.05)

    return count

def parse_time_str(time_str: str) -> Tuple[int, str, int]:
    time_str = time_str.strip().lower()
    try:
        if time_str.endswith("mi"):
            return int(time_str[:-2]), "Minutes", int(time_str[:-2])
        if time_str.endswith("m"):
            return int(time_str[:-1]), "Months", int(time_str[:-1]) * 43200
        if time_str.endswith("h"):
            return int(time_str[:-1]), "Hours", int(time_str[:-1]) * 60
        if time_str.endswith("d"):
            return int(time_str[:-1]), "Days", int(time_str[:-1]) * 1440
        return int(time_str), "Minutes", int(time_str)
    except ValueError:
        return 0, "Unknown", 0

def parse_discount_time(t_str: str) -> int:
    t_str = t_str.lower()
    try:
        if t_str.endswith('mi'):
            return int(t_str[:-2])
        if t_str.endswith('d'):
            return int(t_str[:-1]) * 1440
        if t_str.endswith('h'):
            return int(t_str[:-1]) * 60
        if t_str.endswith('m'):
            return int(t_str[:-1])
        return int(t_str) * 60
    except ValueError:
        return 60

def get_user_display(u_id: int) -> str:
    try:
        chat_info = bot.get_chat(u_id)
        display_name = f"{chat_info.first_name or 'Unknown'} {f'(@{chat_info.username})' if chat_info.username else ''}".strip()
        for char in ['_', '*', '`', '[']:
            display_name = display_name.replace(char, f"\\{char}")
        return display_name
    except ApiTelegramException:
        return "Unknown\\_User"

def is_pending_feedback(user_id: int) -> bool:
    has_pending = collections["users"].find_one({"user_id": user_id, "status": "pending_feedback"}) is not None
    has_active = collections["users"].find_one({"user_id": user_id, "status": {"$in": ["active", "approved_pending_join"]}}) is not None
    return has_pending and not has_active

def is_user_member(user_id: int) -> bool:
    try:
        status = bot.get_chat_member(PUBLIC_CHANNEL_USERNAME, user_id).status
        return status in ["member", "administrator", "creator"]
    except ApiTelegramException:
        return False

# --- DYNAMIC ADMIN AUTHENTICATION ---
def require_dynamic_auth(message: Message, target_func, *args) -> None:
    dyn_pass = str(random.randint(1000, 9999))
    CLICK_CACHE[f'auth_{message.chat.id}'] = {'pass': dyn_pass, 'func': target_func, 'args': args}

    try:
        log_text = f"🔐 *Admin Action Auth*\n\nDynamic 4-digit Password requested by Admin for Protected Action: `{dyn_pass}`"
        bot.send_message(TRACKING_CHANNEL_ID, log_text, parse_mode="Markdown")

        msg = bot.send_message(message.chat.id, "🔐 A dynamic 4-digit password was sent to the tracking channel. Please enter it here:")
        bot.register_next_step_handler(message=msg, callback=verify_dynamic_auth)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error sending auth to tracking channel: {e}")

def verify_dynamic_auth(message: Message):
    auth_data = CLICK_CACHE.get(f'auth_{message.chat.id}')
    if not auth_data:
        return bot.send_message(message.chat.id, "❌ Authentication session expired. Please click the menu button again.")

    if message.text and message.text.strip() == auth_data['pass']:
        with suppress(ApiTelegramException):
            bot.delete_message(message.chat.id, message.message_id)
        with suppress(ApiTelegramException):
            bot.delete_message(message.chat.id, message.message_id - 1)

        func = auth_data['func']
        args = auth_data['args']
        func(message, *args)
        del CLICK_CACHE[f'auth_{message.chat.id}']
    else:
        bot.send_message(message.chat.id, "❌ Incorrect password. Access denied.")

# --- COMMANDS & PERSISTENT MENU ---
def setup_commands() -> None:
    try:
        bot.delete_my_commands(scope=BotCommandScopeDefault())

        bot.set_my_commands([
            BotCommand("restart", "Restart / Change Plan"),
            BotCommand("subscription", "Check active subscriptions"),
            BotCommand("casual", "Protected Admin Setup Menu"),
            BotCommand("advance", "Advanced Admin Tools")
        ], scope=BotCommandScopeChat(ADMIN_ID))
        logger.info("Bot commands successfully updated.")
    except Exception as e:
        logger.error(f"Error setting bot commands: {e}")

def get_persistent_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("/casual"), KeyboardButton("💳 Subscription"))
        markup.add(KeyboardButton("/advance"), KeyboardButton("📚 Novel"))
        markup.add(KeyboardButton("🆔 User ID"))
    else:
        markup.add(KeyboardButton("🔄 Restart"), KeyboardButton("💳 Subscription"))
        markup.add(KeyboardButton("📚 Novel"), KeyboardButton("🆔 User ID"))
        markup.add(KeyboardButton("🧹 Clear"))

        config = collections["config"].find_one({"_id": "settings"}) or {}
        if config.get("free_trial_enabled", False):
            markup.add(KeyboardButton("🎁 Free Trial"))
    return markup

@bot.message_handler(commands=["userid", "id"])
def show_user_id_command(message: Message):
    u_id = message.from_user.id
    log_activity(u_id, "/userid", message)
    real_name, uname = resolve_telegram_profile_name(u_id)
    user_doc = collections["users"].find_one({"user_id": u_id}) or {}
    saved_phone = user_doc.get("phone", "Not linked yet")

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(f"🆔 User ID: {u_id}", callback_data="copy_userid_info"))

    bot.send_message(
        message.chat.id,
        f"👤 <b>Your Telegram Profile & User ID</b>\n\n"
        f"🆔 <b>User ID:</b> <code>{u_id}</code> <i>(Tap to copy)</i>\n"
        f"🏷️ <b>Telegram Name:</b> {real_name}\n"
        f"📱 <b>Linked Phone:</b> <code>{saved_phone}</code>\n\n"
        f"💡 <i>Enter this User ID with your phone number on the website login page to receive instant Bot OTP codes!</i>",
        parse_mode="HTML",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data == "copy_userid_info")
def copy_userid_info_callback(call: CallbackQuery):
    bot.answer_callback_query(call.id, f"Your Telegram User ID is: {call.from_user.id}", show_alert=True)

@bot.message_handler(func=lambda m: m.text in ["🔄 Restart", "💳 Subscription", "📚 Novel", "🧹 Clear", "🎁 Free Trial", "🆔 User ID"])
def handle_persistent_menu(message: Message):
    if message.text == "🔄 Restart":
        _handle_restart(message.from_user.id, message.chat.id)
    elif message.text == "💳 Subscription":
        check_subscription_status(message)
    elif message.text == "📚 Novel":
        novel_command_handler(message)
    elif message.text == "🆔 User ID":
        show_user_id_command(message)
    elif message.text == "🧹 Clear":
        clear_user_chat(message)
    elif message.text == "🎁 Free Trial":
        ch_data = collections["channels"].find_one()
        if ch_data:
            process_free_trial_logic(message.from_user.id, ch_data["channel_id"], message.chat.id)
        else:
            bot.send_message(message.chat.id, "No channels available for free trial.")

@bot.message_handler(func=lambda m: m.from_user.id == ADMIN_ID and (m.forward_from_chat is not None or m.forward_from is not None), content_types=["text", "photo", "video", "document", "audio", "voice"])
def handle_admin_forwards(message: Message) -> None:
    collections["protected"].update_one(
        {"chat_id": message.chat.id, "msg_id": message.message_id, "is_photo": False},
        {"$set": {"chat_id": message.chat.id, "msg_id": message.message_id, "is_photo": False}},
        upsert=True
    )

# --- ADMIN /CASUAL & /ADVANCE MENU ---
@bot.message_handler(commands=["casual"], func=lambda m: m.from_user.id == ADMIN_ID)
def casual_cmd(message: Message) -> None:
    clear_previous_admin_menu(message.chat.id)
    log_activity(message.from_user.id, "/casual", message)
    markup = InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("💰 Edit Prices", callback_data="casual_price"),
        InlineKeyboardButton("🎧 Manage Audio Groups", callback_data="casual_channels"),
        InlineKeyboardButton("➕ Add Audio Group", callback_data="casual_add"),
        InlineKeyboardButton("🧹 Clear9", callback_data="casual_clear9"),
        InlineKeyboardButton("✏️ Edit User", callback_data="casual_edit"),
        InlineKeyboardButton("🎁 Toggle Free Trial", callback_data="casual_freetrial")
    )
    msg = bot.send_message(message.chat.id, "🛡️ *Protected Admin Actions*\n\nSelect a setup command (4-digit password required):", reply_markup=markup, parse_mode="Markdown")
    set_admin_menu(message.chat.id, msg.message_id)

@bot.message_handler(commands=["advance", "advanced"], func=lambda m: m.from_user.id == ADMIN_ID)
def advance_cmd(message: Message) -> None:
    clear_previous_admin_menu(message.chat.id)
    log_activity(message.from_user.id, "/advance", message)
    markup = InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("🔄 Sync Razorpay", callback_data="adv_approve"),
        InlineKeyboardButton("🔒 Lock Users", callback_data="adv_lock"),
        InlineKeyboardButton("📜 History", callback_data="adv_history"),
        InlineKeyboardButton("🏷️ Discount", callback_data="adv_discount"),
        InlineKeyboardButton("📈 Calculate", callback_data="adv_calculate"),
        InlineKeyboardButton("🤖 Hack", callback_data="adv_hack"),
        InlineKeyboardButton("📚 Manage Novels", callback_data="adv_novel")
    )
    msg = bot.send_message(message.chat.id, "🎛️ *Advanced Tools Manager*\n\nSelect an option to proceed (No Password Required):", reply_markup=markup, parse_mode="Markdown")
    set_admin_menu(message.chat.id, msg.message_id)

@bot.callback_query_handler(func=lambda call: (call.data.startswith("casual_") or call.data.startswith("adv_")) and not call.data.startswith("casual_edit_") and not call.data.startswith("casual_remusr_"))
def protected_menu_callbacks(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    action = call.data.split("_")[1]
    bot.answer_callback_query(call.id)

    if call.data.startswith("casual_") or call.data.startswith("adv_"):
        with suppress(ApiTelegramException):
            bot.delete_message(call.message.chat.id, call.message.message_id)

    action_map = {
        "price": trigger_price_cmd,
        "channels": trigger_channels_cmd,
        "add": trigger_add_cmd,
        "clear9": trigger_clear9_cmd,
        "freetrial": toggle_free_trial,
        "approve": approve_manager_cmd,
        "lock": lock_manager_cmd,
        "history": history_cmd,
        "discount": lambda m: show_discount_targets(m.chat.id),
        "calculate": calculate_earnings_cmd,
        "hack": hack_cmd,
        "edit": trigger_edit_ask_id,
        "novel": trigger_novel_admin_menu
    }

    if action in action_map:
        if call.data.startswith("casual_"):
            require_dynamic_auth(call.message, action_map[action])
        else:
            action_map[action](call.message)

def toggle_free_trial(message: Message) -> None:
    config = collections["config"].find_one({"_id": "settings"}) or {}
    current_state = config.get("free_trial_enabled", False)
    if current_state:
        collections["config"].update_one({"_id": "settings"}, {"$set": {"free_trial_enabled": False}}, upsert=True)
        bot.send_message(ADMIN_ID, "🎁 *Free Trial is now 🔴 DISABLED.*", parse_mode="Markdown")
        Thread(target=trigger_global_restart, daemon=True).start()
    else:
        msg = bot.send_message(ADMIN_ID, "🎁 Please enter the duration for the Free Trial (e.g., `1h` for 1 hour or `30mi` for 30 minutes):", parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_enable_free_trial)

def process_enable_free_trial(message: Message) -> None:
    t_str = message.text.strip().lower()
    num, unit, calc_mins = parse_time_str(t_str)
    if calc_mins <= 0:
        return bot.send_message(ADMIN_ID, "❌ Invalid duration format. Action cancelled.")

    collections["config"].update_one({"_id": "settings"}, {"$set": {"free_trial_enabled": True, "free_trial_duration": calc_mins, "free_trial_text": f"{num} {unit}"}}, upsert=True)
    bot.send_message(ADMIN_ID, f"🎁 *Free Trial ({num} {unit}) is now 🟢 ENABLED.*", parse_mode="Markdown")
    Thread(target=trigger_global_restart, daemon=True).start()

def trigger_price_cmd(message: Message):
    bot.send_message(ADMIN_ID, "Select a channel to manage prices:", reply_markup=InlineKeyboardMarkup(row_width=1).add(*(InlineKeyboardButton(f"{ch.get('name', 'Unknown')}", callback_data=f"price_ch_{ch['channel_id']}") for ch in collections["channels"].find({"admin_id": ADMIN_ID}))))

@bot.callback_query_handler(func=lambda call: call.data.startswith("price_"))
def price_actions(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    parts = call.data.split("_")
    bot.answer_callback_query(call.id)
    if parts[1] == "ch":
        ch_id = int(parts[2])
        ch_data = collections["channels"].find_one({"channel_id": ch_id})
        markup = InlineKeyboardMarkup().add(InlineKeyboardButton("➕ Add Prices", callback_data=f"price_add_{ch_id}"), InlineKeyboardButton("✏️ Edit Prices", callback_data=f"price_edit_{ch_id}")).add(InlineKeyboardButton("🔄 Reset Prices", callback_data=f"price_reset_{ch_id}"))
        bot.edit_message_text(f"Channel: *{ch_data.get('name')}*\nCurrent Plans: `{', '.join(f'{k}:{v}' for k, v in sorted(ch_data.get('plans', {}).items(), key=lambda x: parse_time_str(x[0])[2])) or 'None'}`\n\nChoose an option:", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    else:
        ch_id, reset = int(parts[2]), parts[1] == "reset"
        bot.register_next_step_handler(bot.send_message(ADMIN_ID, f"{'🔄 *Resetting' if reset else ('➕ *Adding' if parts[1]=='add' else '✏️ *Editing')} prices*\n\nEnter plans (Time:Price): `1d:50, 1m:150`", parse_mode="Markdown"), lambda m: process_price_update(m, ch_id, reset))

def process_price_update(message: Message, ch_id: int, reset: bool) -> None:
    log_activity(message.from_user.id, "Updating Prices", message)
    try:
        new_plans = {t.strip(): pr.strip() for p in message.text.split(",") for t, pr in [p.split(":")]}
        final_plans = new_plans if reset else {**(collections["channels"].find_one({"channel_id": ch_id}).get("plans", {})), **new_plans}
        collections["channels"].update_one({"channel_id": ch_id}, {"$set": {"plans": final_plans}})
        bot.send_message(ADMIN_ID, f"✅ Prices updated successfully!\n\nCurrent Active Plans:\n`{', '.join(f'{k}:{v}' for k, v in sorted(final_plans.items(), key=lambda x: parse_time_str(x[0])[2]))}`", parse_mode="Markdown")
        Thread(target=trigger_global_restart, daemon=True).start()
    except Exception:
        bot.send_message(ADMIN_ID, "❌ Invalid format. Please use `Time:Price`.")

def trigger_channels_cmd(message: Message):
    markup = InlineKeyboardMarkup(row_width=1)
    for ch in collections["channels"].find():
        markup.add(
            InlineKeyboardButton(f"💰 Edit Prices: {ch.get('name', 'Unknown')}", callback_data=f"price_ch_{ch['channel_id']}"),
            InlineKeyboardButton(f"🗑️ Delete: {ch.get('name', 'Unknown')}", callback_data=f"delch_{ch['channel_id']}")
        )
    bot.send_message(ADMIN_ID, "Your Managed Audio Groups:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("delch_"))
def delete_channel_callback(call: CallbackQuery):
    log_activity(call.from_user.id, call.data)
    ch_id = int(call.data.split("_")[1])
    collections["channels"].delete_one({"channel_id": ch_id})
    bot.answer_callback_query(call.id, "✅ Audio Group deleted successfully!", show_alert=True)
    with suppress(ApiTelegramException):
        bot.delete_message(call.message.chat.id, call.message.message_id)
    trigger_channels_cmd(call.message)

def trigger_add_cmd(message: Message):
    msg = bot.send_message(ADMIN_ID, "Please send the **Group/Channel ID** (e.g., `-1003916102941`) OR forward any message from it here:", parse_mode="Markdown")
    bot.register_next_step_handler(message=msg, callback=process_add_new_group)

def process_add_new_group(message: Message):
    log_activity(message.from_user.id, "Add New Group", message)
    try:
        if message.forward_from_chat:
            chat_id = message.forward_from_chat.id
        else:
            chat_id = int(message.text.strip())

        try:
            chat_info = bot.get_chat(chat_id)
            chat_name = chat_info.title or chat_info.first_name or str(chat_id)
        except ApiTelegramException as e:
            return bot.send_message(ADMIN_ID, f"❌ Cannot find chat `{chat_id}`. Make sure you have added the bot to that group as an Admin first!\n\nError: {e}", parse_mode="Markdown")

        if collections["channels"].find_one({"channel_id": chat_id}):
            return bot.send_message(ADMIN_ID, "⚠️ This Audio Group is already added to your managed list.")

        collections["channels"].insert_one({
            "channel_id": chat_id,
            "name": chat_name,
            "admin_id": ADMIN_ID,
            "plans": {"1m": "99", "3m": "249"}
        })

        bot.send_message(ADMIN_ID, f"✅ Successfully added **{chat_name}** (`{chat_id}`) to your Managed Audio Groups!\n\nYou can now change its prices from the /casual menu.", parse_mode="Markdown")

    except ValueError:
        bot.send_message(ADMIN_ID, "❌ Invalid ID format. Please send a valid numeric ID (like `-1003916102941`) or forward a message.")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ An error occurred: {e}")

def trigger_clear9_cmd(message: Message):
    markup = InlineKeyboardMarkup(row_width=1).add(
        InlineKeyboardButton("🧹 Clear All (Keep Forwards)", callback_data="c9_norm"),
        InlineKeyboardButton("📝 Clear Text Only (Keep Photos)", callback_data="c9_img")
    )
    bot.send_message(message.chat.id, "Select Clear Mode:", reply_markup=markup)

def trigger_edit_ask_id(message: Message):
    markup = InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("➕ Add User", callback_data="casual_edit_add"),
        InlineKeyboardButton("➖ Remove User", callback_data="casual_edit_rem")
    )
    bot.send_message(message.chat.id, "🛠 *User Management*\n\nChoose an action:", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("casual_edit_"))
def casual_edit_callbacks(call: CallbackQuery):
    log_activity(call.from_user.id, call.data)
    action = call.data.split("_")[2]
    bot.answer_callback_query(call.id)
    with suppress(ApiTelegramException):
        bot.delete_message(call.message.chat.id, call.message.message_id)

    if action == "add":
        msg = bot.send_message(ADMIN_ID, "👤 Please send the **Telegram User ID** to manually add/renew and send join link:", parse_mode="Markdown")
        bot.register_next_step_handler(message=msg, callback=edit_ask_plan)
    elif action == "rem":
        active_users = list(collections["users"].find({"status": {"$in": ["active", "approved_pending_join"]}}))
        if not active_users:
            return bot.send_message(ADMIN_ID, "❌ No active users found to remove.")

        markup = InlineKeyboardMarkup(row_width=1)
        for u in active_users:
            markup.add(InlineKeyboardButton(f"👤 {get_user_display(u['user_id'])} ({u['user_id']})", callback_data=f"casual_remusr_{u['user_id']}"))

        bot.send_message(ADMIN_ID, "Select a user to immediately remove and expire:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("casual_remusr_"))
def casual_remusr_callback(call: CallbackQuery):
    u_id = int(call.data.split("_")[2])
    bot.answer_callback_query(call.id)
    with suppress(ApiTelegramException):
        bot.delete_message(call.message.chat.id, call.message.message_id)
    require_dynamic_auth(call.message, process_user_removal_by_id, u_id)

def process_user_removal_by_id(message: Message, target_id: int) -> None:
    active_records = list(collections["users"].find({"user_id": target_id, "status": {"$in": ["active", "approved_pending_join"]}}))
    if not active_records:
        return bot.send_message(ADMIN_ID, f"⚠️ No active or pending subscriptions found for User ID `{target_id}`.", parse_mode="Markdown")
    now_ts = datetime.now(IST).timestamp()
    user_display = get_user_display(target_id)
    removed_channels = []

    for user in active_records:
        ch_id = user["channel_id"]
        try:
            bot.ban_chat_member(ch_id, target_id)
            bot.unban_chat_member(ch_id, target_id)
        except Exception as e:
            logger.error(f"Error kicking user {target_id}: {e}")

        collections["users"].update_one({"_id": user["_id"]}, {"$set": {"status": "pending_feedback", "expiry": now_ts - 1}, "$push": {"history": {"exited_at": now_ts, "note": "manually_expired_by_admin"}}})
        ch_name = (collections["channels"].find_one({"channel_id": ch_id}) or {}).get("name", str(ch_id))
        removed_channels.append(ch_name)

        markup = InlineKeyboardMarkup(row_width=5).row(*(InlineKeyboardButton(f"{i}⭐", callback_data=f"fb_{ch_id}_{i}") for i in range(1, 6)))
        with suppress(ApiTelegramException):
            bot.send_message(target_id, "⏰ *Your subscription has ended.*\n\nThank you for subscribing! Now that your plan is complete, please take a moment to rate your experience below to help us improve:", reply_markup=markup, parse_mode="Markdown")

        log_text = f"🔴 *User Subscription Manually Expired*\n\n👤 User: {user_display}\nID: `{target_id}`\n📺 Channel: {ch_name}\nStatus: Expired & Removed by Admin."
        with suppress(ApiTelegramException):
            bot.send_message(LOG_CHANNEL_ID, log_text, parse_mode="Markdown")

    bot.send_message(ADMIN_ID, f"✅ Successfully expired and removed `{target_id}` from Telegram channel(s): *{', '.join(removed_channels)}*.", parse_mode="Markdown")

# --- ADMIN /HACK MENU (BROADCAST & IMPERSONATE) ---
def hack_cmd(message: Message) -> None:
    cutoff = time.time() - 900
    online_users = list(collections["users"].find({"last_seen": {"$gt": cutoff}}))
    if not online_users:
        return bot.send_message(ADMIN_ID, "🔴 No users have been online in the last 15 minutes.")
    markup = InlineKeyboardMarkup(row_width=1)
    for u in online_users:
        markup.add(InlineKeyboardButton(f"🟢 {get_user_display(u['user_id'])}", callback_data=f"hack_user_{u['user_id']}"))
    bot.send_message(ADMIN_ID, f"🟢 *Live Online Users ({len(online_users)})*\n\nClick an active user below:", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("hack_"))
def hack_callbacks(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    bot.answer_callback_query(call.id)

    parts = call.data.split("_")
    action = parts[1]

    if action == "user":
        if len(parts) > 2:
            u_id = int(parts[2])
            u_doc = collections["users"].find_one({"user_id": u_id}) or {}
            is_stopped = u_doc.get("stop_log", False)

            markup = InlineKeyboardMarkup(row_width=1).add(
                InlineKeyboardButton("💬 Open Chat with User", url=f"tg://user?id={u_id}"),
                InlineKeyboardButton("🔍 View History", callback_data=f"hack_hist_{u_id}"),
                InlineKeyboardButton("🤖 Force User Action", callback_data=f"hack_force_{u_id}"),
                InlineKeyboardButton("📢 Broadcast to User", callback_data=f"hack_bcast_{u_id}"),
                InlineKeyboardButton("🔊 Resume Logging" if is_stopped else "🔇 Stop Logging", callback_data=f"hack_toglog_{u_id}")
            )
            bot.edit_message_text(f"Selected User: `{u_id}`\n\nChoose an action:", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
            unlock_click(call.message.chat.id, call.message.message_id)

    elif action == "toglog":
        if len(parts) > 2:
            u_id = int(parts[2])
            u_doc = collections["users"].find_one({"user_id": u_id}) or {}
            new_state = not u_doc.get("stop_log", False)
            collections["users"].update_one({"user_id": u_id}, {"$set": {"stop_log": new_state}}, upsert=True)

            bot.answer_callback_query(call.id, f"Logging {'Stopped' if new_state else 'Resumed'} for {u_id}")
            unlock_click(call.message.chat.id, call.message.message_id)
            call.data = f"hack_user_{u_id}"
            hack_callbacks(call)

    elif action == "bcast":
        if len(parts) > 2:
            u_id = int(parts[2])
            msg = bot.send_message(ADMIN_ID, f"📢 Send the message to broadcast specifically to user `{u_id}`:")
            bot.register_next_step_handler(message=msg, callback=lambda m: process_hack_user_broadcast(m, u_id))

    elif action == "hist":
        if len(parts) > 2:
            show_user_click_history(call.message.chat.id, int(parts[2]))

    elif action == "force":
        if len(parts) > 2:
            u_id = int(parts[2])
            process_hack_force_id(call.message, u_id)
            unlock_click(call.message.chat.id, call.message.message_id)

def process_hack_user_broadcast(message: Message, u_id: int) -> None:
    log_activity(message.from_user.id, f"Broadcast to {u_id}", message)
    try:
        bot.copy_message(u_id, message.chat.id, message.message_id)
        bot.send_message(ADMIN_ID, f"✅ Broadcast successfully sent to `{u_id}`.", parse_mode="Markdown")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Broadcast failed: {e}")

def process_hack_force_id(message: Message, u_id: int) -> None:
    ch_data = collections["channels"].find_one()
    if not ch_data:
        return bot.send_message(ADMIN_ID, "No channels.")

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("🔄 Force /restart for User", callback_data=f"hforce_restart_{u_id}"))

    for p_time, p_price in sorted(ch_data.get("plans", {}).items(), key=lambda x: parse_time_str(x[0])[2]):
        num, unit, _ = parse_time_str(p_time)
        markup.add(InlineKeyboardButton(f"💳 Auto-Activate {num} {unit}", callback_data=f"hfplan_{u_id}_{ch_data['channel_id']}_{p_time}"))
        markup.add(InlineKeyboardButton(f"🔗 Send Razorpay Link for {num} {unit}", callback_data=f"hfqr_{u_id}_{ch_data['channel_id']}_{p_time}"))

    bot.send_message(ADMIN_ID, f"🤖 Select action/plan to force for User `{u_id}`:", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("hforce_"))
def execute_force_action(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    bot.answer_callback_query(call.id, "Forcing action...")
    action = call.data.split("_")[1]
    u_id = int(call.data.split("_")[2])
    if action == "restart":
        _handle_restart(u_id, u_id)
        bot.send_message(ADMIN_ID, f"✅ Executed /restart on behalf of user {u_id}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("hfqr_"))
def hfqr_callback(call: CallbackQuery):
    log_activity(call.from_user.id, call.data)
    bot.answer_callback_query(call.id, "Generating Razorpay Payment Link for user...")
    _, u_id, ch_id, p_time = call.data.split("_")
    u_id, ch_id = int(u_id), int(ch_id)

    ch_data = collections["channels"].find_one({"channel_id": ch_id})
    final_price = round(float(ch_data["plans"][p_time]), 2)
    num, unit, _ = parse_time_str(p_time)

    plink = create_razorpay_payment_link(u_id, ch_id, p_time, final_price)
    if not plink or "short_url" not in plink:
        return bot.send_message(ADMIN_ID, "❌ Failed to create Razorpay Payment Link.")

    plink_id = plink["id"]
    short_url = plink["short_url"]

    update_user_session(
        u_id,
        {"$set": {"selected_plan": {"ch_id": ch_id, "p_time": p_time, "price": final_price, "plink_id": plink_id, "short_url": short_url, "used_discount": False}}}
    )

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(f"💳 Pay Now (₹{final_price:.2f})", url=short_url))
    markup.add(InlineKeyboardButton("✅ Verify Payment Status", callback_data=f"rzpchk_{plink_id}"))
    markup.add(InlineKeyboardButton("🔄 Change Plan / Restart", callback_data="restart_flow"))
    markup.add(InlineKeyboardButton("📞 Contact Admin", url=CONTACT_URL))

    try:
        sent_msg = bot.send_message(
            u_id,
            f"⚠️ *Admin has sent you a direct Razorpay payment link!*\n\n"
            f"📦 *Plan:* {num} {unit}\n"
            f"💰 *Amount:* ₹{final_price:.2f}\n\n"
            f"Click **Pay Now** below to complete your payment securely via Razorpay. Your subscription will activate automatically as soon as payment is captured!",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        update_user_session(u_id, {"$set": {"menu_msg_id": sent_msg.message_id}})
        bot.send_message(ADMIN_ID, f"✅ Razorpay Payment Link (`{plink_id}`) for {p_time} sent directly to user `{u_id}`.", parse_mode="Markdown")
    except Exception as e:
        bot.send_message(ADMIN_ID, f"❌ Failed to send payment link to user: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith("hfplan_"))
def hfplan_callback(call: CallbackQuery):
    log_activity(call.from_user.id, call.data)
    bot.answer_callback_query(call.id, "Activating plan for user...")
    with suppress(ApiTelegramException):
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)

    _, u_id, ch_id, p_time = call.data.split("_")
    u_id = int(u_id)
    ch_id = int(ch_id)

    ch_data = collections["channels"].find_one({"channel_id": ch_id})
    base_price = float(ch_data["plans"][p_time])
    num, unit, calc_mins = parse_time_str(p_time)

    collections["payments"].insert_one({"user_id": u_id, "amount": base_price, "timestamp": datetime.now(IST).timestamp()})
    collections["config"].update_one({"_id": "bot_stats"}, {"$inc": {"lifetime_total": base_price}}, upsert=True)

    is_already_in = False
    with suppress(ApiTelegramException):
        is_already_in = bot.get_chat_member(ch_id, u_id).status in ["member", "administrator", "creator"]

    if is_already_in:
        current_user = collections["users"].find_one({"user_id": u_id, "channel_id": ch_id, "status": "active"})
        new_expiry = (float(current_user["expiry"]) if current_user and current_user.get("expiry") else datetime.now(IST).timestamp()) + (calc_mins * 60)
        collections["users"].update_one(
            {"user_id": u_id, "channel_id": ch_id},
            {"$set": {"status": "active", "duration_mins": calc_mins, "expiry": new_expiry, "pending_attempts": 0}, "$inc": {"total_purchases": 1, "total_mins": calc_mins}, "$push": {"history": {"joined_at": datetime.now(IST).timestamp(), "expires_at": new_expiry, "duration_mins": calc_mins, "note": "hack_force_activate"}}},
            upsert=True
        )
        bot.send_message(ADMIN_ID, f"✅ Manually forced/renewed User `{u_id}` for {num} {unit}.")
        with suppress(ApiTelegramException):
            bot.send_message(u_id, f"🥳 *Subscription Activated by Admin!*\n\nYour subscription for {num} {unit} has been extended.", parse_mode="Markdown")
    else:
        link = bot.create_chat_invite_link(ch_id, creates_join_request=True)
        collections["users"].update_one(
            {"user_id": u_id, "channel_id": ch_id},
            {"$set": {"status": "approved_pending_join", "duration_mins": calc_mins, "expiry": None, "pending_attempts": 0}, "$inc": {"total_purchases": 1, "total_mins": calc_mins}},
            upsert=True
        )
        bot.send_message(ADMIN_ID, f"✅ Manually forced/approved User `{u_id}` for {num} {unit}.")
        with suppress(ApiTelegramException):
            bot.send_message(u_id, f"🥳 *Subscription Approved by Admin!*\n\nSubscription: {num} {unit}\n\n⚠️ Click the button below to send a Join Request. The bot will automatically accept you and start your timer!", parse_mode="Markdown", protect_content=True, reply_markup=InlineKeyboardMarkup(row_width=1).add(InlineKeyboardButton("👉 Request to Join Channel", url=link.invite_link)))

def show_user_click_history(chat_id: int, u_id: int) -> None:
    history = list(collections["click_history"].find({"user_id": u_id}).sort("timestamp", -1).limit(40))
    if not history:
        return bot.send_message(chat_id, f"📭 No recent button clicks found for User ID `{u_id}`.", parse_mode="Markdown")

    text = f"🔍 Button Click History for {get_user_display(u_id)} ({u_id})\n\n"
    for h in history:
        text += f"🔘 {h['button']}\n🕒 {datetime.fromtimestamp(h['timestamp'], IST).strftime('%Y-%m-%d %I:%M:%S %p')}\n\n"
    if len(text) > 4000:
        doc = io.BytesIO(text.encode('utf-8'))
        doc.name = f"ClickHistory_{u_id}.txt"
        bot.send_document(chat_id, doc, caption=f"📄 Full Click History for {u_id}")
    else:
        bot.send_message(chat_id, text)

# --- ADMIN ENDPOINTS (/edit, /history) ---
def edit_ask_plan(message: Message) -> None:
    log_activity(message.from_user.id, "Input Add ID", message)
    if not message.text.strip().isdigit():
        return bot.send_message(ADMIN_ID, "❌ Invalid User ID.")
    u_id = int(message.text.strip())

    ch_data = collections["channels"].find_one()
    if not ch_data:
        return bot.send_message(ADMIN_ID, "❌ No channels configured.")

    markup = InlineKeyboardMarkup(row_width=1)
    for p_time, p_price in sorted(ch_data.get("plans", {}).items(), key=lambda x: parse_time_str(x[0])[2]):
        markup.add(InlineKeyboardButton(f"💳 {p_time} (₹{p_price})", callback_data=f"editx_{u_id}_{ch_data['channel_id']}_{p_time}"))

    bot.send_message(ADMIN_ID, f"👤 User ID: `{u_id}`\n\nSelect a membership plan to apply:", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("editx_"))
def editx_callback(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    bot.answer_callback_query(call.id, "Applying plan...")
    with suppress(ApiTelegramException):
        bot.delete_message(call.message.chat.id, call.message.message_id)

    parts = call.data.split("_")
    u_id, ch_id, p_time = int(parts[1]), int(parts[2]), parts[3]
    ch_data = collections["channels"].find_one({"channel_id": ch_id})
    price = float(ch_data["plans"][p_time])

    num, unit, calc_mins = parse_time_str(p_time)
    if calc_mins == 0:
        return bot.send_message(ADMIN_ID, "❌ Invalid duration.")

    collections["payments"].insert_one({"user_id": u_id, "amount": price, "timestamp": datetime.now(IST).timestamp()})
    collections["config"].update_one({"_id": "bot_stats"}, {"$inc": {"lifetime_total": price}}, upsert=True)

    is_already_in = False
    with suppress(ApiTelegramException):
        is_already_in = bot.get_chat_member(ch_id, u_id).status in ["member", "administrator", "creator"]

    if is_already_in:
        current_user = collections["users"].find_one({"user_id": u_id, "channel_id": ch_id, "status": "active"})
        new_expiry = (float(current_user["expiry"]) if current_user and current_user.get("expiry") else datetime.now(IST).timestamp()) + (calc_mins * 60)
        collections["users"].update_one(
            {"user_id": u_id, "channel_id": ch_id},
            {"$set": {"status": "active", "duration_mins": calc_mins, "expiry": new_expiry, "pending_attempts": 0}, "$inc": {"total_purchases": 1, "total_mins": calc_mins}, "$push": {"history": {"joined_at": datetime.now(IST).timestamp(), "expires_at": new_expiry, "duration_mins": calc_mins, "note": "manual_add_by_admin"}}},
            upsert=True
        )
        bot.send_message(ADMIN_ID, f"✅ Manually renewed User `{u_id}` for {num} {unit} at ₹{price}.")
        with suppress(ApiTelegramException):
            bot.send_message(u_id, f"🥳 *Subscription Renewed by Admin!*\n\nYour subscription for {num} {unit} has been extended.", parse_mode="Markdown")
    else:
        collections["users"].update_one(
            {"user_id": u_id, "channel_id": ch_id},
            {"$set": {"status": "approved_pending_join", "duration_mins": calc_mins, "expiry": None, "pending_attempts": 0}, "$inc": {"total_purchases": 1, "total_mins": calc_mins}},
            upsert=True
        )
        bot.send_message(ADMIN_ID, f"✅ Manually approved User `{u_id}` for {num} {unit} at ₹{price}.")
        link = bot.create_chat_invite_link(ch_id, creates_join_request=True)
        markup = InlineKeyboardMarkup(row_width=1).add(InlineKeyboardButton("👉 Join Channel Now", url=link.invite_link))
        with suppress(ApiTelegramException):
            bot.send_message(u_id, f"🥳 *Subscription Approved by Admin!*\n\nSubscription: {num} {unit}\n\n⚠️ Click the button below to join the channel. The bot will automatically accept your request and start your timer!", parse_mode="Markdown", protect_content=True, reply_markup=markup)

def history_cmd(message: Message) -> None:
    users = list(collections["users"].find({"status": {"$in": ["active", "approved_pending_join"]}}))
    text = "📜 *Active Users History*\n\nSelect a user to send their detailed history to tracking channel:\n"
    markup = InlineKeyboardMarkup(row_width=1)
    for u in users:
        markup.add(InlineKeyboardButton(f"👤 {get_user_display(u['user_id'])} ({u['user_id']})", callback_data=f"histsend_{u['user_id']}"))
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("histsend_"))
def send_hist_tracking(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)

    with suppress(ApiTelegramException):
        bot.delete_message(call.message.chat.id, call.message.message_id)

    u_id = int(call.data.split("_")[1])
    bot.answer_callback_query(call.id, f"Sending detailed history for {u_id} to tracking channel...")

    user_record = collections["users"].find_one({"user_id": u_id})
    if not user_record:
        return bot.send_message(call.message.chat.id, f"❌ No data found for {u_id}")

    payments = list(collections["payments"].find({"user_id": u_id}))
    total_paid = sum(p.get("amount", 0) for p in payments)
    total_purchases = user_record.get('total_purchases', len(payments))

    text = f"📜 *Full Detailed History*\n\n👤 User: {get_user_display(u_id)}\n🆔 ID: `{u_id}`\n💳 Total Spent: *₹{total_paid:.2f}*\n📊 Total Times Subscribed: *{total_purchases}*\n\n"

    text += "💸 *Payment Log (One by One):*\n"
    for idx, p in enumerate(payments, 1):
        text += f"{idx}. ₹{p.get('amount', 0)} on {datetime.fromtimestamp(p['timestamp'], IST).strftime('%Y-%m-%d %I:%M %p')}\n"

    text += "\n⏳ *Access Log:*\n"
    for hist in user_record.get("history", []):
        j_at = datetime.fromtimestamp(hist.get("joined_at", 0), IST).strftime('%Y-%m-%d %H:%M') if hist.get("joined_at") else "N/A"
        e_at = datetime.fromtimestamp(hist.get("expires_at", 0), IST).strftime('%Y-%m-%d %H:%M') if hist.get("expires_at") else "N/A"
        ex_at = datetime.fromtimestamp(hist.get("exited_at", 0), IST).strftime('%Y-%m-%d %H:%M') if hist.get("exited_at") else "N/A"
        text += f"🔹 Note: {hist.get('note', 'N/A')}\nJoined: {j_at}\nExpires: {e_at}\nExited: {ex_at}\nDuration: {hist.get('duration_mins', 0)} mins\n\n"

    bot.send_message(TRACKING_CHANNEL_ID, text, parse_mode="Markdown")

    session = get_user_session(call.from_user.id)
    if session.get("last_hist_msg_id"):
        with suppress(ApiTelegramException):
            bot.delete_message(call.message.chat.id, session["last_hist_msg_id"])

    sent_msg = bot.send_message(call.message.chat.id, f"✅ Detailed history for `{u_id}` sent to Tracking Channel", parse_mode="Markdown")
    update_user_session(call.from_user.id, {"$set": {"last_hist_msg_id": sent_msg.message_id}})

def approve_manager_cmd(message: Message) -> None:
    """Syncs all active Razorpay payment links with Razorpay API and auto-activates any paid links."""
    pending_sessions = list(collections["session"].find({"selected_plan.plink_id": {"$exists": True}}))
    if not pending_sessions:
        return bot.send_message(message.chat.id, "✅ *No pending Razorpay payment links found.*", parse_mode="Markdown")

    bot.send_message(message.chat.id, f"🔄 *Checking {len(pending_sessions)} active Razorpay Payment Link(s)...*", parse_mode="Markdown")
    activated_count = 0
    for sess in pending_sessions:
        u_id = sess["_id"]
        sel_plan = sess.get("selected_plan", {})
        plink_id = sel_plan.get("plink_id")
        if not plink_id:
            continue
        plink_data = fetch_razorpay_payment_link(plink_id)
        if plink_data and plink_data.get("status") == "paid":
            payments_list = plink_data.get("payments") or []
            pay_id = payments_list[0].get("payment_id", plink_id) if payments_list else plink_id
            if activate_subscription_after_payment(u_id, int(sel_plan["ch_id"]), str(sel_plan["p_time"]), float(sel_plan["price"]), pay_id):
                activated_count += 1

    bot.send_message(message.chat.id, f"✅ *Razorpay Sync Complete!* Auto-activated `{activated_count}` newly paid subscription(s).", parse_mode="Markdown")

def lock_manager_cmd(message: Message) -> None:
    locked_users = list(collections["users"].find({"pending_attempts": {"$gte": 1}}))
    if not locked_users:
        return bot.send_message(message.chat.id, "✅ *No users are currently locked.*", parse_mode="Markdown")

    markup = InlineKeyboardMarkup(row_width=1)
    for u in locked_users:
        markup.add(InlineKeyboardButton(f"🔓 Unlock {get_user_display(u['user_id'])} ({u['user_id']})", callback_data=f"unlockusr_{u['user_id']}"))
    markup.add(InlineKeyboardButton("🔓 Unlock ALL Users", callback_data="unlockall"), InlineKeyboardButton("❌ Close", callback_data="close_lock_menu"))
    bot.send_message(message.chat.id, "🔒 *Locked Users Management*\n\nClick a button below to unblock a user's account:", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("unlockusr_"))
def unlock_user_callback(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    u_id = int(call.data.split("_")[1])
    collections["users"].update_one({"user_id": u_id}, {"$set": {"pending_attempts": 0}, "$unset": {"locked_at": ""}})
    update_user_session(u_id, {"$unset": {"pending_approval": "", "selected_plan": ""}})
    bot.answer_callback_query(call.id, f"User {u_id} unlocked successfully!", show_alert=True)
    with suppress(ApiTelegramException):
        bot.send_message(u_id, "🔓 *Your account has been unlocked by the Admin.*\n\nYou can now use /start or /restart to try again.", parse_mode="Markdown")
    lock_manager_cmd(call.message)

@bot.callback_query_handler(func=lambda call: call.data == "unlockall")
def unlock_all_users(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    collections["users"].update_many({"pending_attempts": {"$gte": 1}}, {"$set": {"pending_attempts": 0}, "$unset": {"locked_at": ""}})
    collections["session"].update_many({"pending_approval": {"$exists": True}}, {"$unset": {"pending_approval": "", "selected_plan": ""}})
    bot.answer_callback_query(call.id, "✅ All users have been unlocked!", show_alert=True)
    with suppress(ApiTelegramException):
        bot.edit_message_text("✅ *All locked users have been successfully unlocked.*", call.message.chat.id, call.message.message_id, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "close_lock_menu")
def close_lock_menu(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    with suppress(ApiTelegramException):
        bot.delete_message(call.message.chat.id, call.message.message_id)

# --- DISCOUNT & VOUCHER MANAGEMENT (/d) ---
def show_discount_targets(chat_id: int, message_id: int = None) -> None:
    cleanup_expired_discounts()
    ch_data = collections["channels"].find_one() or {}
    markup = InlineKeyboardMarkup(row_width=1).add(InlineKeyboardButton("🌐 Global Discount (All Plans)", callback_data="d_tgt_all"))
    for p_time, _ in sorted(ch_data.get("plans", {}).items(), key=lambda x: parse_time_str(x[0])[2]):
        markup.add(InlineKeyboardButton(f"📦 Plan Discount: {parse_time_str(p_time)[0]} {parse_time_str(p_time)[1]}", callback_data=f"d_tgt_{p_time}"))
    markup.add(InlineKeyboardButton("🎟️ Create Promo Voucher", callback_data="vouch_create"), InlineKeyboardButton("❌ Close Menu", callback_data="d_close"))
    text = "🛠 *Interactive Discount & Voucher Management*\n\nSelect a plan to manage discounts or create a Promo Voucher:"
    if message_id:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "vouch_create")
def vouch_create_start(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("⏳ Time-Based Voucher", callback_data="vc_type_time"))
    markup.add(InlineKeyboardButton("👥 Limit-Based Voucher", callback_data="vc_type_limit"))
    markup.add(InlineKeyboardButton("🔙 Back", callback_data="d_back"))

    now_ts = datetime.now(IST).timestamp()
    vouch_doc = collections["config"].find_one({"_id": "vouchers"})
    active_text = ""

    if vouch_doc and "codes" in vouch_doc:
        for code, data in vouch_doc["codes"].items():
            exp = float(data.get("expires_at", 0))
            limit = data.get("limit", 9999999)
            used_count = len(data.get("used_by", []))

            if exp > now_ts and used_count < limit:
                if limit == 9999999:
                    hours, rem_m = divmod(int(exp - now_ts), 3600)
                    time_left = f"{hours}h {rem_m // 60}m"
                    active_text += f"\n🎟️ `{code}` | {data['discount']}% OFF | ⏳ Left: {time_left} | 👥 Used: {used_count}"
                else:
                    active_text += f"\n🎟️ `{code}` | {data['discount']}% OFF | 👥 Uses: {used_count}/{limit}"

    if not active_text:
        active_text = "\n\n_🔴 No active vouchers found. Users will not see the Promo Code button._"
    else:
        active_text = "\n\n📜 *Currently Active Vouchers:*" + active_text

    msg_text = f"🎟️ *Select Voucher Type to Create:*{active_text}"

    bot.edit_message_text(msg_text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("vc_type_"))
def vc_type_selected(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    v_type = call.data.split("_")[2]
    msg = bot.send_message(ADMIN_ID, "🎟️ Enter discount % for this Voucher (e.g. 50):")
    bot.register_next_step_handler(msg, lambda m: vouch_ask_next(m, v_type))

def vouch_ask_next(message: Message, v_type: str) -> None:
    try:
        perc = float(message.text.strip().replace("%", ""))
    except Exception:
        return bot.send_message(ADMIN_ID, "❌ Invalid percentage.")

    if v_type == "time":
        msg = bot.send_message(ADMIN_ID, "⏳ Enter duration for Voucher (e.g. 1d, 12h, 30mi):")
        bot.register_next_step_handler(msg, lambda m: vouch_finalize_time(m, perc))
    else:
        msg = bot.send_message(ADMIN_ID, "👥 Enter max number of users who can use this voucher (e.g. 10):")
        bot.register_next_step_handler(msg, lambda m: vouch_finalize_limit(m, perc))

def vouch_finalize_time(message: Message, perc: float) -> None:
    t_str = message.text.strip().lower()
    mins = parse_discount_time(t_str)
    if mins <= 0:
        return bot.send_message(ADMIN_ID, "❌ Invalid time format.")
    code = generate_voucher_code()
    collections["config"].update_one({"_id": "vouchers"}, {"$set": {f"codes.{code}": {"discount": perc, "expires_at": datetime.now(IST).timestamp() + (mins * 60), "limit": 9999999, "used_by": []}}}, upsert=True)
    b_msg = f"🎉 *Special Time-Based Promo!* 🎉\n\nUse Code: `{code}` to get *{perc}% OFF*!\n⏳ Valid for only: {t_str}\n\nClick below to claim your discounted plan before it expires!"
    bot.send_message(ADMIN_ID, f"✅ *Time-Based Voucher Created!*\n\nCode: `{code}`\nDiscount: {perc}%\nDuration: {t_str}\nUses: Unlimited\n\nBroadcasting to users...", parse_mode="Markdown")
    Thread(target=lambda: [bot.send_message(ADMIN_ID, f"✅ Broadcast delivered to {trigger_global_restart(b_msg)} active users.")], daemon=True).start()

def vouch_finalize_limit(message: Message, perc: float) -> None:
    if not message.text.strip().isdigit():
        return bot.send_message(ADMIN_ID, "❌ Invalid limit.")
    limit = int(message.text.strip())
    code = generate_voucher_code()
    collections["config"].update_one({"_id": "vouchers"}, {"$set": {f"codes.{code}": {"discount": perc, "expires_at": datetime.now(IST).timestamp() + 315360000, "limit": limit, "used_by": []}}}, upsert=True)
    b_msg = f"🎉 *Limited Usage Promo Code!* 🎉\n\nUse Code: `{code}` to get *{perc}% OFF*!\n👥 Hurry! Only available for the first {limit} users.\n\nClick below to claim your discounted plan!"
    bot.send_message(ADMIN_ID, f"✅ *Limit-Based Voucher Created!*\n\nCode: `{code}`\nDiscount: {perc}%\nDuration: No Expiry\nUses: {limit}\n\nBroadcasting to users...", parse_mode="Markdown")
    Thread(target=lambda: [bot.send_message(ADMIN_ID, f"✅ Broadcast delivered to {trigger_global_restart(b_msg)} active users.")], daemon=True).start()

@bot.callback_query_handler(func=lambda call: call.data.startswith("d_tgt_"))
def d_target_selected(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    target = call.data.split("_")[2]
    cleanup_expired_discounts()
    current_disc = (collections["config"].find_one({"_id": "discounts"}) or {}).get("plans", {}).get(target)
    now_ts = datetime.now(IST).timestamp()

    status_text = "🔴 *No active discount*"
    if current_disc and float(current_disc["expires_at"]) > now_ts:
        hours, rem_m = divmod(int(float(current_disc["expires_at"]) - now_ts), 3600)
        status_text = f"🟢 *Active:* {current_disc['percentage']}% OFF\n⏳ *Expires in:* {hours}h {rem_m // 60}m"

    markup = InlineKeyboardMarkup(row_width=1).add(InlineKeyboardButton("➕ Add / Edit Discount", callback_data=f"d_set_{target}"))
    if current_disc:
        markup.add(InlineKeyboardButton("🗑️ Remove Discount", callback_data=f"d_rm_{target}"))
    markup.add(InlineKeyboardButton("🔙 Back to Plan List", callback_data="d_back"))
    bot.edit_message_text(f"🛠 *Managing Discount for:* `{'Global' if target == 'all' else target.upper()}`\n\nStatus: {status_text}\n\nWhat would you like to do?", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    unlock_click(call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("d_set_"))
def d_set_perc(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    target = call.data.split("_")[2]
    markup = InlineKeyboardMarkup(row_width=3).add(*(InlineKeyboardButton(f"{p}%", callback_data=f"d_perc_{target}_{p}.0") for p in [5, 10, 15, 25, 50, 75])).add(InlineKeyboardButton("✏️ Custom %", callback_data=f"d_cperc_{target}"), InlineKeyboardButton("🔙 Back", callback_data=f"d_tgt_{target}"))
    bot.edit_message_text(f"Select discount percentage for `{'Global' if target == 'all' else target.upper()}`:", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    unlock_click(call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("d_cperc_"))
def d_custom_perc_prompt(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    target = call.data.split("_")[2]
    msg = bot.send_message(call.message.chat.id, f"Send custom discount percentage for {target} (e.g. 12.5):")
    bot.register_next_step_handler(message=msg, callback=lambda m: show_time_selection(m.chat.id, target, float(m.text.strip().replace("%", ""))) if m.text.strip().replace("%", "").replace(".", "").isdigit() else bot.send_message(m.chat.id, "❌ Invalid percentage."))

def show_time_selection(chat_id: int, target: str, perc: float, message_id: int = None) -> None:
    markup = InlineKeyboardMarkup(row_width=3).add(
        InlineKeyboardButton("1 Hour", callback_data=f"d_time_{target}_{perc}_1h"), InlineKeyboardButton("5 Hours", callback_data=f"d_time_{target}_{perc}_5h"), InlineKeyboardButton("10 Hours", callback_data=f"d_time_{target}_{perc}_10h"), InlineKeyboardButton("1 Day", callback_data=f"d_time_{target}_{perc}_1d"), InlineKeyboardButton("3 Days", callback_data=f"d_time_{target}_{perc}_3d"), InlineKeyboardButton("7 Days", callback_data=f"d_time_{target}_{perc}_7d")
    ).add(InlineKeyboardButton("✏️ Custom Time", callback_data=f"d_ctime_{target}_{perc}"), InlineKeyboardButton("🔙 Cancel", callback_data=f"d_tgt_{target}"))
    text = f"Select duration for `{'Global' if target == 'all' else target.upper()}` ({perc}% OFF):"
    if message_id:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode="Markdown")
        unlock_click(chat_id, message_id)
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("d_perc_"))
def d_set_time(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    parts = call.data.split("_")
    show_time_selection(call.message.chat.id, parts[2], float(parts[3]), call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("d_ctime_"))
def d_custom_time_prompt(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    parts = call.data.split("_")
    msg = bot.send_message(call.message.chat.id, f"Send custom duration for {parts[2]} at {parts[3]}% OFF (e.g. 45mi, 12h, 2d):")
    bot.register_next_step_handler(message=msg, callback=lambda m: execute_discount(m.chat.id, parts[2], float(parts[3]), m.text.strip().lower(), parse_discount_time(m.text.strip().lower())) if parse_discount_time(m.text.strip().lower()) > 0 else bot.send_message(m.chat.id, "❌ Invalid format."))

@bot.callback_query_handler(func=lambda call: call.data.startswith("d_time_"))
def d_finalize(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    parts = call.data.split("_")
    execute_discount(call.message.chat.id, parts[2], float(parts[3]), parts[4], parse_discount_time(parts[4]), call.message.message_id)
    unlock_click(call.message.chat.id, call.message.message_id)

def execute_discount(chat_id: int, target: str, perc: float, t_str: str, add_mins: int, message_id: int = None) -> None:
    collections["config"].update_one({"_id": "discounts"}, {"$set": {f"plans.{target}": {"percentage": perc, "expires_at": datetime.now(IST).timestamp() + (add_mins * 60)}}}, upsert=True)
    text = f"✅ *{perc}% Discount Activated!* (Valid for {t_str})\nTarget: `{'Global (All Plans)' if target == 'all' else target.upper()}`\n\nBroadcasting offer..."
    if message_id:
        bot.edit_message_text(text, chat_id, message_id, parse_mode="Markdown")
    else:
        bot.send_message(chat_id, text, parse_mode="Markdown")

    b_msg = f"🎉 *Limited Time Offer!* 🎉\n\nAdmin has activated a special *{perc}% DISCOUNT* on {'all subscription plans' if target == 'all' else f'the {parse_time_str(target)[0]} {parse_time_str(target)[1]} plan'} for the next *{t_str}*!\n\nClick below to grab your discounted plan before it expires!"
    Thread(target=lambda: [bot.send_message(chat_id, f"✅ Broadcast delivered to {trigger_global_restart(b_msg)} users.")], daemon=True).start()

@bot.callback_query_handler(func=lambda call: call.data.startswith("d_rm_"))
def d_remove(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    collections["config"].update_one({"_id": "discounts"}, {"$unset": {f"plans.{call.data.split('_')[2]}": ""}})
    bot.answer_callback_query(call.id, "Discount removed!")
    d_target_selected(call)

@bot.callback_query_handler(func=lambda call: call.data == "d_back")
def d_back(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    show_discount_targets(call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data == "d_close")
def d_close(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    with suppress(ApiTelegramException):
        bot.delete_message(call.message.chat.id, call.message.message_id)

# --- NOVELS (/novel) ---
NOVELS_PER_PAGE = 8

def user_has_novel_access(user_id: int) -> bool:
    if user_id == ADMIN_ID:
        return True
    now_ts = datetime.now(IST).timestamp()
    return collections["users"].find_one({"user_id": user_id, "status": "active", "expiry": {"$gt": now_ts}}) is not None

def get_novel_categories_markup() -> InlineKeyboardMarkup:
    markup = InlineKeyboardMarkup(row_width=2)
    markup.row(
        InlineKeyboardButton("🔄 Ongoing Novels", callback_data="novcat_ongoing_0"),
        InlineKeyboardButton("📚 Completed Novels", callback_data="novcat_completed_0")
    )
    markup.row(InlineKeyboardButton("🔞 Adult Novels", callback_data="novcat_adult_0"))
    markup.row(
        InlineKeyboardButton("🔍 Search Novel", callback_data="usrnov_search"),
        InlineKeyboardButton("📩 Request Novel", callback_data="usrnov_request")
    )
    return markup

def process_bulk_novel_add(message: Message):
    text = message.text or ""
    blocks = text.split("\n\n")
    count = 0
    for block in blocks:
        if not block.strip():
            continue
        urls = re.findall(r'(https?://[^\s]+)', block)
        tags = re.findall(r'#([^\n]+)', block)
        if tags:
            name = tags[0].strip()
            link = urls[0] if urls else ""
            if not collections["novels"].find_one({"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}}):
                collections["novels"].insert_one({"name": name, "link": link, "status": "draft"})
                count += 1
        else:
            name = block.strip()
            if name and not name.startswith("http"):
                if not collections["novels"].find_one({"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}}):
                    collections["novels"].insert_one({"name": name, "link": "", "status": "draft"})
                    count += 1

    if count > 0:
        bot.send_message(message.chat.id, f"✅ Added {count} novels as drafts.")
        show_drafts(message.chat.id)
    else:
        bot.send_message(message.chat.id, "❌ No new novels added (duplicates or invalid format).")

def show_drafts(chat_id):
    drafts = list(collections["novels"].find({"status": "draft"}))
    if not drafts:
        return bot.send_message(chat_id, "✅ All pending novels have been categorized!")

    d = drafts[0]
    markup = InlineKeyboardMarkup(row_width=2).row(
        InlineKeyboardButton("✅ Completed", callback_data=f"n_st_completed_{d['_id']}"),
        InlineKeyboardButton("🔄 Ongoing", callback_data=f"n_st_ongoing_{d['_id']}")
    ).add(InlineKeyboardButton("🗑️ Delete", callback_data=f"n_del_{d['_id']}"))
    bot.send_message(chat_id, f"Novel: *{d['name']}*\nLink: {d.get('link', 'None')}\n\nSelect Status:", reply_markup=markup, parse_mode="Markdown")

def trigger_novel_admin_menu(message: Message) -> None:
    markup = InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("➕ Add Novel", callback_data="novel_add"),
        InlineKeyboardButton("✏️ Edit Novel", callback_data="novel_edit"),
        InlineKeyboardButton("❌ Delete Novel", callback_data="novel_delete"),
        InlineKeyboardButton("🔍 Find & Replace", callback_data="novel_replace")
    )
    msg = bot.send_message(message.chat.id, "🛠 *Novel Management Menu*\n\nNote: You can also use `/t`, `/tc`, or `/ta` inside a topic to auto-add novels.", reply_markup=markup, parse_mode="Markdown")
    set_admin_menu(message.chat.id, msg.message_id)

# --- AUTO-ADD NOVEL FROM TOPIC (/t, /tc, /ta) ---
def get_topic_novel_markup(novel_id):
    novel_doc = collections["novels"].find_one({"_id": ObjectId(novel_id)}) or {}
    free_cnt = int(novel_doc.get("free_audios", 10)) if novel_doc.get("free_audios") is not None else 10
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✏️ Edit Name", callback_data=f"tnov_name_{novel_id}"),
        InlineKeyboardButton("🔄 Change Category", callback_data=f"tnov_cat_{novel_id}")
    )
    markup.add(
        InlineKeyboardButton(f"🎧 Free Audios ({free_cnt})", callback_data=f"tnov_free_{novel_id}"),
        InlineKeyboardButton("🗑️ Delete Novel", callback_data=f"tnov_del_{novel_id}")
    )
    return markup

@bot.message_handler(commands=['t', 'tc', 'ta'], func=lambda m: m.chat.id == NOVEL_GROUP_ID and m.from_user.id == ADMIN_ID)
def auto_add_novel_from_topic(message: Message):
    if not message.is_topic_message or not message.message_thread_id:
        return bot.reply_to(message, "❌ Please use this command inside a specific Topic.")

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        return bot.reply_to(message, "⚠️ Format:\n`/t Name` (Ongoing)\n`/tc Name` (Completed)\n`/ta Name` (Adult)")

    novel_name = args[1].strip()
    clean_group_id = str(message.chat.id).replace("-100", "")
    topic_url = f"https://t.me/c/{clean_group_id}/{message.message_thread_id}"

    cmd = message.text.split()[0].split('@')[0].lower()
    if cmd == '/tc':
        status = 'completed'
    elif cmd == '/ta':
        status = 'adult'
    else:
        status = 'ongoing'

    status_display = {"ongoing": "🔄 Ongoing", "completed": "📚 Completed", "adult": "🔞 Adult"}

    existing_link = collections["novels"].find_one({"link": topic_url})
    existing_name = collections["novels"].find_one({"name": {"$regex": f"^{re.escape(novel_name)}$", "$options": "i"}})

    if existing_link:
        old_name = existing_link.get("name", "Unknown")
        free_cnt = int(existing_link.get("free_audios", 10)) if existing_link.get("free_audios") is not None else 10
        collections["novels"].update_one({"_id": existing_link["_id"]}, {"$set": {"name": novel_name, "status": status}})
        markup = get_topic_novel_markup(existing_link["_id"])
        return bot.reply_to(
            message,
            f"✅ <b>Novel Updated!</b>\n\nOld Name: <s>{old_name}</s>\nNew Name: <b>{novel_name}</b>\nCategory: {status_display[status]}\nFree Audios: <b>🎧 {free_cnt} Audios</b>\n\n<i>The novel has been successfully renamed and updated.</i>",
            parse_mode="HTML",
            reply_markup=markup
        )

    if existing_name:
        return bot.reply_to(message, f"⚠️ Novel '{novel_name}' already exists in a different topic.")

    new_novel = {"name": novel_name, "link": topic_url, "status": status, "free_audios": 10}
    res = collections["novels"].insert_one(new_novel)
    markup = get_topic_novel_markup(res.inserted_id)

    bot.reply_to(
        message,
        f"✅ <b>Novel Added!</b>\n\nName: <b>{novel_name}</b>\nCategory: {status_display[status]}\nFree Audios: <b>🎧 10 Audios (Default)</b>\nLink: {topic_url}\n\n<i>It will now appear as a button in /novel menu & website.</i>",
        parse_mode="HTML",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("tnov_"))
def topic_novel_callbacks(call: CallbackQuery):
    parts = call.data.split("_")
    action = parts[1]
    novel_id = parts[-1]

    if call.from_user.id != ADMIN_ID:
        return bot.answer_callback_query(call.id, "Admin only.", show_alert=True)

    novel = collections["novels"].find_one({"_id": ObjectId(novel_id)})
    if not novel and action != "cancel":
        return bot.answer_callback_query(call.id, "Novel not found or already deleted.", show_alert=True)

    if action == "name":
        msg = bot.reply_to(call.message, f"✏️ Enter the new name for **{novel['name']}**:\n\n_(Reply to this message with the new name)_", parse_mode="Markdown")
        bot.register_next_step_handler(msg, lambda m: process_tnov_rename(m, novel_id, call.message.message_id))
        bot.answer_callback_query(call.id)
    elif action == "free":
        cur_free = int(novel.get("free_audios", 10)) if novel.get("free_audios") is not None else 10
        msg = bot.reply_to(
            call.message,
            f"🎧 How many free audios do you want to show for **{novel['name']}**?\n"
            f"Current: `{cur_free}` (Default: `10`)\n\n"
            f"_(Reply with a number, e.g. `5` to unlock 5 audios and lock the rest):_",
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(msg, lambda m: process_tnov_free_audios(m, novel_id, call.message.message_id))
        bot.answer_callback_query(call.id)
    elif action == "cat":
        markup = InlineKeyboardMarkup(row_width=3)
        markup.add(
            InlineKeyboardButton("🔄 Ongoing", callback_data=f"tnov_setc_ongoing_{novel_id}"),
            InlineKeyboardButton("📚 Completed", callback_data=f"tnov_setc_completed_{novel_id}"),
            InlineKeyboardButton("🔞 Adult", callback_data=f"tnov_setc_adult_{novel_id}")
        )
        markup.add(InlineKeyboardButton("🔙 Cancel", callback_data=f"tnov_cancel_{novel_id}"))
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
        bot.answer_callback_query(call.id)
    elif action == "cancel":
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=get_topic_novel_markup(novel_id))
        bot.answer_callback_query(call.id)
    elif action == "setc":
        new_cat = parts[2]
        collections["novels"].update_one({"_id": ObjectId(novel_id)}, {"$set": {"status": new_cat}})
        status_display = {"ongoing": "🔄 Ongoing", "completed": "📚 Completed", "adult": "🔞 Adult"}
        free_cnt = int(novel.get("free_audios", 10)) if novel.get("free_audios") is not None else 10

        bot.answer_callback_query(call.id, f"Category updated to {status_display[new_cat]}!")
        bot.edit_message_text(
            f"✅ <b>Novel Updated!</b>\n\nName: <b>{novel['name']}</b>\nCategory: {status_display[new_cat]}\nFree Audios: <b>🎧 {free_cnt} Audios</b>\n\n<i>Category has been successfully updated.</i>",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=get_topic_novel_markup(novel_id)
        )
    elif action == "del":
        collections["novels"].delete_one({"_id": ObjectId(novel_id)})
        bot.answer_callback_query(call.id, "Novel deleted from database!", show_alert=True)
        bot.edit_message_text(f"🗑️ <b>Novel Deleted!</b>\n\n<s>{novel['name']}</s> has been removed from the database.", call.message.chat.id, call.message.message_id, parse_mode="HTML")

def process_tnov_rename(message: Message, novel_id: str, original_msg_id: int):
    new_name = (message.text or "").strip()
    if not new_name:
        return
    novel = collections["novels"].find_one({"_id": ObjectId(novel_id)})
    if not novel:
        return

    old_name = novel.get("name", "Unknown")
    status = novel.get("status", "ongoing")
    free_cnt = int(novel.get("free_audios", 10)) if novel.get("free_audios") is not None else 10
    status_display = {"ongoing": "🔄 Ongoing", "completed": "📚 Completed", "adult": "🔞 Adult"}

    collections["novels"].update_one({"_id": ObjectId(novel_id)}, {"$set": {"name": new_name}})

    with suppress(Exception):
        bot.edit_message_text(
            f"✅ <b>Novel Name Updated!</b>\n\nOld Name: <s>{old_name}</s>\nNew Name: <b>{new_name}</b>\nCategory: {status_display.get(status, status)}\nFree Audios: <b>🎧 {free_cnt} Audios</b>\n\n<i>Name successfully updated.</i>",
            message.chat.id,
            original_msg_id,
            parse_mode="HTML",
            reply_markup=get_topic_novel_markup(novel_id)
        )

    bot.reply_to(message, f"✅ Novel name successfully changed from <s>{old_name}</s> to <b>{new_name}</b>!", parse_mode="HTML")

def process_tnov_free_audios(message: Message, novel_id: str, original_msg_id: int):
    raw_val = (message.text or "").strip()
    m = re.search(r"\d+", raw_val)
    if not m:
        return bot.reply_to(message, "❌ Invalid number. Please click the 🎧 Free Audios button again and send a valid number (e.g. `5`).", parse_mode="Markdown")

    new_free_cnt = max(0, int(m.group(0)))
    novel = collections["novels"].find_one({"_id": ObjectId(novel_id)})
    if not novel:
        return

    status = novel.get("status", "ongoing")
    status_display = {"ongoing": "🔄 Ongoing", "completed": "📚 Completed", "adult": "🔞 Adult"}

    collections["novels"].update_one({"_id": ObjectId(novel_id)}, {"$set": {"free_audios": new_free_cnt}})

    with suppress(Exception):
        bot.edit_message_text(
            f"✅ <b>Novel Updated!</b>\n\nName: <b>{novel['name']}</b>\nCategory: {status_display.get(status, status)}\nFree Audios: <b>🎧 {new_free_cnt} Audios</b>\n\n<i>Free preview audio count has been updated to {new_free_cnt}.</i>",
            message.chat.id,
            original_msg_id,
            parse_mode="HTML",
            reply_markup=get_topic_novel_markup(novel_id)
        )

    bot.reply_to(
        message,
        f"✅ Free audios for <b>{novel['name']}</b> set to <b>{new_free_cnt}</b>!\n<i>Users without an active subscription will now see {new_free_cnt} unlocked audio(s) and the rest locked.</i>",
        parse_mode="HTML"
    )

# --- NOVEL USER MENU (PAGINATION, SEARCH & REQUEST NOVEL) ---
@bot.message_handler(commands=["novel"])
def novel_command_handler(message: Message) -> None:
    user_id = message.from_user.id
    log_activity(user_id, "/novel", message)
    bot.send_message(
        message.chat.id,
        "🌟 <b>Dynamic Novel Directory!</b> 🌟\n\n<i>Select a category, search for a novel, or request a new novel below:</i>",
        reply_markup=get_novel_categories_markup(),
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("novcat_"))
def show_category_novels(call: CallbackQuery):
    log_activity(call.from_user.id, call.data)
    parts = call.data.split("_")
    category = parts[1]
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    user_id = call.from_user.id

    has_access = user_has_novel_access(user_id)

    cat_details = {
        "ongoing": {"name": "Ongoing Novels", "emoji": "🔄"},
        "completed": {"name": "Completed Novels", "emoji": "📚"},
        "adult": {"name": "Adult Novels", "emoji": "🔞"}
    }

    details = cat_details.get(category, {"name": "Novels", "emoji": "📖"})
    novels = list(collections["novels"].find({"status": category}).sort("name", 1))

    if not novels:
        return bot.answer_callback_query(call.id, f"No {details['name']} found.", show_alert=True)

    total_novels = len(novels)
    total_pages = max(1, (total_novels + NOVELS_PER_PAGE - 1) // NOVELS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start_idx = page * NOVELS_PER_PAGE
    page_novels = novels[start_idx:start_idx + NOVELS_PER_PAGE]

    markup = InlineKeyboardMarkup(row_width=1)
    for n in page_novels:
        if not has_access:
            markup.add(InlineKeyboardButton(f"{details['emoji']} {n['name']} (🔒 Locked)", callback_data="req_sub_novel"))
        elif n.get("link"):
            markup.add(InlineKeyboardButton(f"{details['emoji']} {n['name']}", url=n["link"]))
        else:
            markup.add(InlineKeyboardButton(f"⚠️ {n['name']} (No Link Yet)", callback_data="novel_nolink"))

    if total_pages > 1:
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"novcat_{category}_{page - 1}"))
        nav_buttons.append(InlineKeyboardButton(f"📄 {page + 1}/{total_pages}", callback_data="novel_page_info"))
        if page < total_pages - 1:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"novcat_{category}_{page + 1}"))
        markup.row(*nav_buttons)

    markup.row(
        InlineKeyboardButton("🔍 Search", callback_data="usrnov_search"),
        InlineKeyboardButton("📩 Request Novel", callback_data="usrnov_request")
    )
    markup.add(InlineKeyboardButton("🔙 Back to Categories", callback_data="novel_back_to_cats"))

    msg_text = f"🌟 <b>{details['name']}</b> (Total: {total_novels} | Page {page + 1}/{total_pages}) 🌟\n\n"
    if not has_access:
        msg_text += "⚠️ <i>You must purchase a membership to read these novels. Click any novel below to view plans:</i>"
    else:
        msg_text += "<i>Click any novel button below to jump directly to its private topic:</i>"

    with suppress(ApiTelegramException):
        bot.edit_message_text(msg_text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="HTML")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "novel_page_info")
def novel_page_info_alert(call: CallbackQuery):
    bot.answer_callback_query(call.id, "Use ⬅️ Prev and Next ➡️ to browse pages.")

@bot.callback_query_handler(func=lambda call: call.data == "novel_back_to_cats")
def back_to_novel_categories(call: CallbackQuery):
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    with suppress(ApiTelegramException):
        bot.edit_message_text(
            "🌟 <b>Dynamic Novel Directory!</b> 🌟\n\n<i>Select a category, search for a novel, or request a new novel below:</i>",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=get_novel_categories_markup(),
            parse_mode="HTML"
        )
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("usrnov_"))
def user_novel_extra_actions(call: CallbackQuery):
    log_activity(call.from_user.id, call.data)
    action = call.data.split("_")[1]
    bot.answer_callback_query(call.id)

    if action == "search":
        msg = bot.send_message(
            call.message.chat.id,
            "🔍 <b>Search Novel</b>\n\nSend the name (or part of the name) of the novel you are looking for:",
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, process_user_novel_search)
    elif action == "request":
        msg = bot.send_message(
            call.message.chat.id,
            "📩 <b>Request a Novel</b>\n\nSend the exact name of the novel you want to request:",
            parse_mode="HTML"
        )
        bot.register_next_step_handler(msg, process_user_novel_request)

def process_user_novel_search(message: Message):
    log_activity(message.from_user.id, "Novel Search", message)
    if not message.text or message.text.startswith("/"):
        return
    query = message.text.strip()
    user_id = message.from_user.id
    has_access = user_has_novel_access(user_id)

    results = list(collections["novels"].find({
        "name": {"$regex": re.escape(query), "$options": "i"},
        "status": {"$in": ["ongoing", "completed", "adult"]}
    }).sort("name", 1))

    markup = InlineKeyboardMarkup(row_width=1)
    if not results:
        markup.add(InlineKeyboardButton("📩 Request This Novel", callback_data="usrnov_request"))
        markup.add(InlineKeyboardButton("🔙 Back to Novel Menu", callback_data="novel_back_to_cats"))
        return bot.send_message(
            message.chat.id,
            f"❌ No novels found matching <b>{query}</b>.\n\nWould you like to request this novel?",
            reply_markup=markup,
            parse_mode="HTML"
        )

    emoji_map = {"ongoing": "🔄", "completed": "📚", "adult": "🔞"}
    for n in results[:20]:
        em = emoji_map.get(n.get("status"), "📖")
        if not has_access:
            markup.add(InlineKeyboardButton(f"{em} {n['name']} (🔒 Locked)", callback_data="req_sub_novel"))
        elif n.get("link"):
            markup.add(InlineKeyboardButton(f"{em} {n['name']}", url=n["link"]))
        else:
            markup.add(InlineKeyboardButton(f"⚠️ {n['name']} (No Link Yet)", callback_data="novel_nolink"))

    markup.row(
        InlineKeyboardButton("🔍 Search Again", callback_data="usrnov_search"),
        InlineKeyboardButton("📩 Request Novel", callback_data="usrnov_request")
    )
    markup.add(InlineKeyboardButton("🔙 Back to Categories", callback_data="novel_back_to_cats"))

    bot.send_message(
        message.chat.id,
        f"🔍 <b>Search Results for '{query}'</b> ({len(results)} found):",
        reply_markup=markup,
        parse_mode="HTML"
    )

def process_user_novel_request(message: Message):
    log_activity(message.from_user.id, "Novel Request", message)
    if not message.text or message.text.startswith("/"):
        return
    req_name = message.text.strip()
    user_id = message.from_user.id

    existing = collections["novels"].find_one({
        "name": {"$regex": f"^{re.escape(req_name)}$", "$options": "i"},
        "status": {"$in": ["ongoing", "completed", "adult"]}
    })
    if existing:
        has_access = user_has_novel_access(user_id)
        markup = InlineKeyboardMarkup(row_width=1)
        if not has_access:
            markup.add(InlineKeyboardButton(f"📖 {existing['name']} (🔒 Locked)", callback_data="req_sub_novel"))
        elif existing.get("link"):
            markup.add(InlineKeyboardButton(f"📖 {existing['name']}", url=existing["link"]))
        markup.add(InlineKeyboardButton("🔙 Back to Categories", callback_data="novel_back_to_cats"))
        return bot.send_message(
            message.chat.id,
            f"✅ <b>{existing['name']}</b> is already available in our directory!",
            reply_markup=markup,
            parse_mode="HTML"
        )

    user_display = get_user_display(user_id)
    user_doc = collections["users"].find_one({"user_id": user_id}) or {}
    topic_id = user_doc.get("topic_id")

    admin_markup = InlineKeyboardMarkup(row_width=2).add(
        InlineKeyboardButton("✅ Notify User (Uploaded)", callback_data=f"reqdone_{user_id}"),
        InlineKeyboardButton("❌ Reject Request", callback_data=f"reqrej_{user_id}")
    )
    req_log = (
        f"📩 *New Novel Request*\n\n"
        f"👤 User: {user_display}\n"
        f"🆔 ID: `{user_id}`\n"
        f"📖 Requested Novel: *{req_name}*"
    )
    with suppress(ApiTelegramException):
        bot.send_message(ADMIN_ID, req_log, parse_mode="Markdown", reply_markup=admin_markup)
        if topic_id:
            bot.send_message(FORUM_GROUP_ID, req_log, parse_mode="Markdown", message_thread_id=topic_id)

    markup = InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 Back to Novel Menu", callback_data="novel_back_to_cats"))
    bot.send_message(
        message.chat.id,
        f"✅ Your request for <b>{req_name}</b> has been sent to the Admin!\n\nYou will be notified once it is uploaded.",
        reply_markup=markup,
        parse_mode="HTML"
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith(("reqdone_", "reqrej_")))
def handle_novel_request_admin_action(call: CallbackQuery):
    u_id = int(call.data.split("_")[1])
    if call.data.startswith("reqdone_"):
        with suppress(ApiTelegramException):
            bot.send_message(u_id, "🎉 *Good News!* The novel you requested has been added to the `/novel` directory!", parse_mode="Markdown")
        bot.answer_callback_query(call.id, "User notified!")
        bot.edit_message_text(call.message.text + "\n\n✅ Status: Fulfilled & User Notified", call.message.chat.id, call.message.message_id)
    else:
        with suppress(ApiTelegramException):
            bot.send_message(u_id, "⚠️ Sorry, the novel you requested is currently unavailable.", parse_mode="Markdown")
        bot.answer_callback_query(call.id, "Request rejected.")
        bot.edit_message_text(call.message.text + "\n\n❌ Status: Rejected", call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data == "novel_nolink")
def novel_no_link_alert(call: CallbackQuery):
    bot.answer_callback_query(call.id, "⏳ Admin has not added the link for this novel yet.", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data == "req_sub_novel")
def req_sub_novel_callback(call: CallbackQuery):
    bot.answer_callback_query(call.id, "⚠️ You must purchase a membership first!", show_alert=True)
    with suppress(ApiTelegramException):
        bot.delete_message(call.message.chat.id, call.message.message_id)
    _handle_restart(call.from_user.id, call.message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("novel_"))
def novel_admin_actions(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    action = call.data.split("_")[1]
    bot.answer_callback_query(call.id)
    if action == "add":
        with suppress(ApiTelegramException):
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        msg = bot.send_message(ADMIN_ID, "Send the novel details (You can send multiple blocks separated by double newlines, e.g. #Tag and link):")
        bot.register_next_step_handler(message=msg, callback=process_bulk_novel_add)
    elif action == "replace":
        with suppress(ApiTelegramException):
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        msg = bot.send_message(
            ADMIN_ID,
            "🔍 *Find & Replace in Novels*\n\nSend the text to find and replace in this format:\n`FindText | ReplaceText`\n\n_(Updates matching novel names and links)_",
            parse_mode="Markdown"
        )
        bot.register_next_step_handler(message=msg, callback=process_novel_find_replace)
    elif action in ["edit", "delete"]:
        novels = list(collections["novels"].find({}))
        if not novels:
            return bot.send_message(ADMIN_ID, f"❌ No novels found to {action}.")
        markup = InlineKeyboardMarkup(row_width=1).add(*(InlineKeyboardButton(f"{'✅' if n.get('status') == 'completed' else '🔄'} {n['name']}", callback_data=f"n_{'edit' if action == 'edit' else 'del'}_{n['_id']}") for n in novels))
        bot.edit_message_text(f"Select a novel to {action}:", call.message.chat.id, call.message.message_id, reply_markup=markup)
        unlock_click(call.message.chat.id, call.message.message_id)

def process_novel_find_replace(message: Message) -> None:
    if not message.text or "|" not in message.text:
        return bot.send_message(ADMIN_ID, "❌ Invalid format. Use: `FindText | ReplaceText`", parse_mode="Markdown")
    find_str, replace_str = [part.strip() for part in message.text.split("|", 1)]
    if not find_str:
        return bot.send_message(ADMIN_ID, "❌ Find text cannot be empty.")

    updated_count = 0
    for n in collections["novels"].find({}):
        new_name = n.get("name", "").replace(find_str, replace_str)
        new_link = n.get("link", "").replace(find_str, replace_str)
        if new_name != n.get("name", "") or new_link != n.get("link", ""):
            collections["novels"].update_one({"_id": n["_id"]}, {"$set": {"name": new_name, "link": new_link}})
            updated_count += 1

    bot.send_message(ADMIN_ID, f"✅ Find & Replace complete! Updated *{updated_count}* novel(s).", parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("n_st_"))
def set_novel_status(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    parts = call.data.split("_")
    collections["novels"].update_one({"_id": ObjectId(parts[3])}, {"$set": {"status": parts[2]}})
    bot.edit_message_text(f"✅ Novel saved successfully as *{parts[2].capitalize()}*!", call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    unlock_click(call.message.chat.id, call.message.message_id)
    show_drafts(call.message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("n_edit_"))
def edit_novel_select(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    with suppress(ApiTelegramException):
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    novel_id = call.data.split("_", 2)[2]
    msg = bot.send_message(ADMIN_ID, "Send the new name/details for this novel:")
    bot.register_next_step_handler(message=msg, callback=lambda m: [collections["novels"].update_one({"_id": ObjectId(novel_id)}, {"$set": {"name": m.text.strip()}}), bot.send_message(ADMIN_ID, f"✅ Novel successfully updated to: *{m.text.strip()}*", parse_mode="Markdown")])
    unlock_click(call.message.chat.id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("n_del_"))
def delete_novel(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    deleted = collections["novels"].find_one_and_delete({"_id": ObjectId(call.data.split("_", 2)[2])})
    bot.answer_callback_query(call.id, "✅ Novel deleted!")
    bot.edit_message_text("✅ Novel has been deleted.", call.message.chat.id, call.message.message_id)
    unlock_click(call.message.chat.id, call.message.message_id)
    if deleted and deleted.get("status") == "draft":
        show_drafts(call.message.chat.id)

# --- CLEAR CHATS (/clear, /clear9) ---
def fast_clear(chat_id: int, is_admin: bool = False, keep_photos: bool = False) -> None:
    try:
        temp_msg = bot.send_message(chat_id, "⏳ Initializing fast clear process...")

        protected_set = {doc["msg_id"] for doc in collections["protected"].find({"chat_id": chat_id, "is_photo": False})} if is_admin else set()
        photo_set = {doc["msg_id"] for doc in collections["protected"].find({"chat_id": chat_id, "is_photo": True})} if is_admin else set()

        if not is_admin:
            collections["protected"].delete_many({"chat_id": chat_id})

        for msg_id in range(temp_msg.message_id, 0, -1):
            if is_admin:
                if msg_id in protected_set:
                    continue
                if keep_photos and msg_id in photo_set:
                    continue

            try:
                bot.delete_message(chat_id, msg_id)
                time.sleep(0.04)
            except ApiTelegramException as e:
                if e.error_code == 429:
                    time.sleep(e.result_json.get('parameters', {}).get('retry_after', 1))
                    with suppress(ApiTelegramException):
                        bot.delete_message(chat_id, msg_id)
            except Exception:
                pass

        if is_admin:
            bot.send_message(chat_id, "✅ Cleared successfully!")
        else:
            _handle_restart(chat_id, chat_id)
    except Exception as e:
        logger.error(f"Error during clear: {e}")

@bot.message_handler(commands=["clear"])
def clear_user_chat(message: Message) -> None:
    log_activity(message.from_user.id, "/clear", message)
    if is_user_locked(message.from_user.id):
        return send_locked_message(message.chat.id, message.from_user.id)
    Thread(target=fast_clear, args=(message.chat.id, False), daemon=True).start()

@bot.callback_query_handler(func=lambda call: call.data.startswith("c9_"))
def c9_callback(call: CallbackQuery):
    log_activity(call.from_user.id, call.data)
    with suppress(ApiTelegramException):
        bot.delete_message(call.message.chat.id, call.message.message_id)
    keep_img = (call.data == "c9_img")
    Thread(target=fast_clear, args=(call.message.chat.id, True, keep_img), daemon=True).start()

# --- CALCULATE (/calculate) ---
def calculate_earnings_cmd(message: Message) -> None:
    clear_previous_admin_menu(message.chat.id)
    now_ts = datetime.now(IST).timestamp()
    collections["payments"].delete_many({"timestamp": {"$lt": now_ts - 31536000}})

    totals = {k: sum(p.get("amount", 0) for p in collections["payments"].find({"timestamp": {"$gte": now_ts - ts}})) for k, ts in [("day", 86400), ("week", 604800), ("month", 2592000)]}
    lifetime = (collections["config"].find_one({"_id": "bot_stats"}) or {}).get("lifetime_total", 0)

    if lifetime == 0:
        lifetime = sum(p.get("amount", 0) for p in collections["payments"].find())
        collections["config"].update_one({"_id": "bot_stats"}, {"$set": {"lifetime_total": lifetime}}, upsert=True)

    markup = InlineKeyboardMarkup(row_width=1).add(InlineKeyboardButton("📄 Generate Document (.txt)", callback_data="calc_doc")).add(
        InlineKeyboardButton("🗑️ Remove Last Transactions", callback_data="calc_rem")).add(
        InlineKeyboardButton("👀 Show Recent Data", callback_data="calc_show")).add(
        InlineKeyboardButton("⚠️ Reset Lifetime Total", callback_data="calc_reset_life"))

    msg = bot.send_message(message.chat.id, f"📈 *Revenue Calculator*\n\n🔹 *Today (24h):* ₹{totals['day']:.2f}\n🔹 *Last 7 Days:* ₹{totals['week']:.2f}\n🔹 *Last 30 Days:* ₹{totals['month']:.2f}\n🌟 *Lifetime Total:* ₹{lifetime:.2f}\n\n_Note: Individual records older than 365 days auto-delete to save space._", reply_markup=markup, parse_mode="Markdown")
    set_admin_menu(message.chat.id, msg.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("calc_"))
def calc_callbacks(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    action = call.data.split("_")[1]
    bot.answer_callback_query(call.id)

    if action == "doc":
        records = list(collections["payments"].find().sort("timestamp", -1))
        text = "=== ALL TRANSACTIONS RECORD ===\n\n" + ("No transactions found." if not records else "".join(f"User: {r['user_id']} | Amount: INR {r.get('amount', 0)} | Date: {datetime.fromtimestamp(r['timestamp'], IST).strftime('%Y-%m-%d %I:%M:%S %p')}\n" for r in records))
        doc = io.BytesIO(text.encode('utf-8'))
        doc.name = f"Transactions_{int(time.time())}.txt"
        with suppress(ApiTelegramException):
            bot.send_document(call.message.chat.id, doc, caption="📄 *Complete Transaction History*\n(Last 365 days)", parse_mode="Markdown")
    elif action == "rem":
        msg = bot.send_message(call.message.chat.id, "How many recent days of data do you want to remove? (e.g. 2)")
        bot.register_next_step_handler(message=msg, callback=lambda m: bot.register_next_step_handler(message=bot.send_message(m.chat.id, f"🔐 You selected **{int(m.text.strip())} days**. Please enter the high-level password:", parse_mode="Markdown"), callback=lambda m2: calc_rem_execute(m2, int(m.text.strip()))) if m.text.strip().isdigit() else bot.send_message(m.chat.id, "❌ Invalid number."))
    elif action == "show":
        msg = bot.send_message(call.message.chat.id, "How many days of recent data do you want to show? (e.g. 5)")
        bot.register_next_step_handler(message=msg, callback=lambda m: calc_show_execute(m) if m.text.strip().isdigit() else bot.send_message(m.chat.id, "❌ Invalid number."))
    elif action == "reset":
        msg = bot.send_message(call.message.chat.id, "⚠️ *DANGER:* You selected to reset Lifetime Earnings.\n\nPlease enter the lifetime reset password:", parse_mode="Markdown")
        bot.register_next_step_handler(message=msg, callback=lambda m: calc_reset_life_execute(m))

def calc_rem_execute(message: Message, days: int) -> None:
    with suppress(ApiTelegramException):
        [bot.delete_message(message.chat.id, message.message_id - i) for i in (0, 1)]
    if message.text.strip() == "12345678i":
        res = collections["payments"].delete_many({"timestamp": {"$gte": datetime.now(IST).timestamp() - (days * 86400)}})
        bot.send_message(message.chat.id, f"✅ Successfully removed **{res.deleted_count}** transactions.", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "❌ Incorrect high-level password.")

def calc_show_execute(message: Message) -> None:
    records = list(collections["payments"].find({"timestamp": {"$gte": datetime.now(IST).timestamp() - (int(message.text.strip()) * 86400)}}).sort("timestamp", -1))
    if not records:
        return bot.send_message(message.chat.id, "📭 No transactions found.")
    bot.send_message(message.chat.id, "📋 *Transactions*\n\n" + "".join(f"👤 `{r['user_id']}` | ₹{r.get('amount', 0)} | 🕒 {datetime.fromtimestamp(r['timestamp'], IST).strftime('%Y-%m-%d %I:%M %p')}\n" for r in records[:50]) + (f"\n...and {len(records) - 50} more." if len(records) > 50 else ""), parse_mode="Markdown")

def calc_reset_life_execute(message: Message) -> None:
    with suppress(ApiTelegramException):
        [bot.delete_message(message.chat.id, message.message_id - i) for i in (0, 1)]
    if message.text.strip() == "123456789i":
        collections["config"].update_one({"_id": "bot_stats"}, {"$set": {"lifetime_total": 0}}, upsert=True)
        collections["payments"].delete_many({})
        bot.send_message(message.chat.id, "✅ Lifetime earnings and history reset.", parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, "❌ Incorrect password.")

# --- SUBSCRIPTION ROUTING & VOUCHERS ---
def send_subscription_plans(chat_id: int, user_id: int, target_ch_id: Optional[str] = None) -> None:
    ch_data = (collections["channels"].find_one({"channel_id": int(target_ch_id)}) if target_ch_id and target_ch_id.isdigit() else None) or collections["channels"].find_one()
    if not ch_data:
        return bot.send_message(chat_id, "No subscription plans are currently available.")

    ch_id = ch_data["channel_id"]
    now_ts = datetime.now(IST).timestamp()

    active_sub = collections["users"].find_one({"user_id": user_id, "channel_id": ch_id, "status": "active", "expiry": {"$gt": now_ts}})
    if active_sub:
        markup = InlineKeyboardMarkup(row_width=1).add(InlineKeyboardButton("👉 Request to Re-Join", url=bot.create_chat_invite_link(ch_id, creates_join_request=True).invite_link))
        novel_text = ""
        for status, title in [("completed", "Completed"), ("ongoing", "Ongoing")]:
            novels = list(collections["novels"].find({"status": status}))
            if novels:
                novel_text += f"{'📚' if status == 'completed' else '🔄'} <b>{title} Novels:</b>\n" + "".join(f"🔹 <a href='{n.get('link', '#')}'>{n['name']}</a>\n" if n.get("link") else f"🔹 {n['name']}\n" for n in novels) + "\n"

        msg_text = f"✅ *Active Subscription Found!*\n\nExpires on: `{datetime.fromtimestamp(float(active_sub['expiry']), IST).strftime('%Y-%m-%d %I:%M %p IST')}`\nClick below to re-join."
        if novel_text.strip():
            msg_text += f"\n\n{novel_text.strip()}"

        return bot.send_message(chat_id, msg_text, reply_markup=markup, parse_mode="HTML" if novel_text.strip() else "Markdown", disable_web_page_preview=True)

    if collections["users"].find_one({"user_id": user_id, "channel_id": ch_id, "status": "approved_pending_join"}):
        markup = InlineKeyboardMarkup(row_width=1).add(InlineKeyboardButton("👉 Request to Join Channel", url=bot.create_chat_invite_link(ch_id, creates_join_request=True).invite_link))
        return bot.send_message(chat_id, "⚠️ *You have an approved subscription pending!*\n\nClick below to send a Join Request.", reply_markup=markup, parse_mode="Markdown")

    session = get_user_session(chat_id)
    if session.get("menu_msg_id"):
        with suppress(ApiTelegramException):
            bot.delete_message(chat_id, session["menu_msg_id"])

    cleanup_expired_discounts()
    active_discounts = (collections["config"].find_one({"_id": "discounts"}) or {}).get("plans", {})

    applied_voucher = session.get("applied_voucher")
    voucher_perc = 0

    has_active_vouchers = False
    available_codes_text = ""
    vouch_doc = collections["config"].find_one({"_id": "vouchers"})
    if vouch_doc and "codes" in vouch_doc:
        for code, v_data in vouch_doc["codes"].items():
            if float(v_data.get("expires_at", 0)) > now_ts and len(v_data.get("used_by", [])) < v_data.get("limit", 9999999) and user_id not in v_data.get("used_by", []):
                has_active_vouchers = True
                available_codes_text += f"\n🎁 Code: <code>{code}</code> ({v_data['discount']}% OFF)"

    if applied_voucher:
        if vouch_doc and "codes" in vouch_doc and applied_voucher in vouch_doc["codes"]:
            v_data = vouch_doc["codes"][applied_voucher]
            if float(v_data["expires_at"]) > now_ts and len(v_data.get("used_by", [])) < v_data.get("limit", 9999999) and user_id not in v_data.get("used_by", []):
                voucher_perc = float(v_data["discount"])
            else:
                update_user_session(user_id, {"$unset": {"applied_voucher": ""}})
                applied_voucher = None
        else:
            update_user_session(user_id, {"$unset": {"applied_voucher": ""}})
            applied_voucher = None

    markup = InlineKeyboardMarkup(row_width=1)
    max_exp = 0.0

    config = collections["config"].find_one({"_id": "settings"}) or {}
    if config.get("free_trial_enabled", False):
        trial_text = config.get("free_trial_text", "24 Hours")
        markup.add(InlineKeyboardButton(f"🎁 Claim {trial_text} Free Trial", callback_data=f"freetrial_{ch_data['channel_id']}"))

    for p_time, p_price in sorted(ch_data.get("plans", {}).items(), key=lambda x: parse_time_str(x[0])[2]):
        base_price, discount_perc, exp_at = float(p_price), 0.0, 0.0
        if voucher_perc > 0:
            discount_perc = voucher_perc
        else:
            all_exp = float(active_discounts.get("all", {}).get("expires_at", 0))
            p_exp = float(active_discounts.get(p_time, {}).get("expires_at", 0))
            if "all" in active_discounts and all_exp > now_ts:
                discount_perc, exp_at = float(active_discounts["all"]["percentage"]), all_exp
            if p_time in active_discounts and p_exp > now_ts:
                discount_perc, exp_at = float(active_discounts[p_time]["percentage"]), p_exp

        final_p = base_price if discount_perc == 0 else round(base_price * (1 - discount_perc / 100), 2)
        btn_text = f"💳 {parse_time_str(p_time)[0]} {parse_time_str(p_time)[1]} ₹{final_p}"
        max_exp = max(max_exp, exp_at)
        markup.add(InlineKeyboardButton(btn_text, callback_data=f"select_{ch_data['channel_id']}_{p_time}"))

    if applied_voucher:
        markup.add(InlineKeyboardButton(f"🎟️ Active Promo Code ({voucher_perc}% OFF)", callback_data="active_voucher_info"))
    elif has_active_vouchers:
        markup.add(InlineKeyboardButton("🎟️ Have a Promo Code?", callback_data="enter_voucher"))
    markup.add(InlineKeyboardButton("📞 Contact Admin", url=CONTACT_URL))

    intro_text = f"Welcome!\n\nYou are joining the Private Audio Group: <b>{ch_data.get('name', 'Premium Audio')}</b>.\n\nPlease select a subscription plan below to access the files:"
    if voucher_perc > 0:
        intro_text = f"🎟️ <b>Promo Code Applied! ({voucher_perc}% OFF)</b> 🎟️\n\nYou are joining: <b>{ch_data.get('name', 'Premium Audio')}</b>.\n\nPlease select a discounted plan below:"
    elif max_exp > 0:
        hours, rem_m = divmod(int(max_exp - now_ts), 3600)
        intro_text = f"🎉 <b>Special Discounts Applied!</b> 🎉\n⏳ <b>Offer ends in: {f'{hours}h {rem_m // 60}m' if hours else f'{rem_m // 60}m'}</b>\n\nYou are joining: <b>{ch_data.get('name', 'Premium Audio')}</b>.\n\nPlease select a discounted subscription plan below:"

    if available_codes_text and voucher_perc == 0:
        intro_text += f"\n\n<b>🔥 Active Promo Codes (Click to copy):</b>{available_codes_text}"

    update_user_session(chat_id, {"$set": {"menu_msg_id": bot.send_message(chat_id, intro_text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True).message_id}})

@bot.callback_query_handler(func=lambda call: call.data == "active_voucher_info")
def active_voucher_info_callback(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    session = get_user_session(call.from_user.id)
    code = session.get("applied_voucher")

    if not code:
        bot.answer_callback_query(call.id, "No active promo code found.")
        return

    vouch_doc = collections["config"].find_one({"_id": "vouchers"})
    v_data = vouch_doc.get("codes", {}).get(code, {}) if vouch_doc else {}
    perc = v_data.get("discount", "Unknown")

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🗑️ Remove Promo Code", callback_data="remove_voucher"))
    markup.add(InlineKeyboardButton("🔙 Back", callback_data="restart_flow"))

    bot.edit_message_text(
        f"🎟️ *Active Promo Code*\n\nCode: `{code}`\nDiscount: *{perc}% OFF*\n\nThis voucher is currently applied to your plans. Do you want to remove it to enter a different one?",
        call.message.chat.id,
        call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data == "remove_voucher")
def remove_voucher_callback(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    update_user_session(call.from_user.id, {"$unset": {"applied_voucher": ""}})
    bot.answer_callback_query(call.id, "Promo code removed successfully!", show_alert=True)
    _handle_restart(call.from_user.id, call.message.chat.id)

def process_free_trial_logic(u_id: int, ch_id: int, chat_id: int, msg_id_to_delete: Optional[int] = None, call_id: Optional[str] = None):
    config = collections["config"].find_one({"_id": "settings"}) or {}

    if not config.get("free_trial_enabled", False):
        if call_id:
            bot.answer_callback_query(call_id, "❌ Free trial is currently disabled.", show_alert=True)
        else:
            bot.send_message(chat_id, "❌ Free trial is currently disabled.")
        return

    now_ts = datetime.now(IST).timestamp()
    user_record = collections["users"].find_one({"user_id": u_id, "channel_id": ch_id})

    if user_record:
        status = user_record.get("status")
        expiry = user_record.get("expiry")

        if status == "active" and expiry and float(expiry) > now_ts:
            rem = int(float(expiry) - now_ts)
            h, rem_s = divmod(rem, 3600)
            m, s = divmod(rem_s, 60)
            msg = f"⏳ You already have an active Free Trial!\n\nTime remaining: {h}h {m}m {s}s"
            if call_id:
                bot.answer_callback_query(call_id, msg, show_alert=True)
            else:
                bot.send_message(chat_id, msg)
            return
        elif status == "approved_pending_join":
            msg = "⏳ Your Free Trial is pending! Please join the channel to start the timer."
            if call_id:
                bot.answer_callback_query(call_id, msg, show_alert=True)
            else:
                bot.send_message(chat_id, msg)
            return

    if call_id:
        bot.answer_callback_query(call_id, "Activating free trial...")
    if msg_id_to_delete:
        with suppress(ApiTelegramException):
            bot.delete_message(chat_id, msg_id_to_delete)

    calc_mins = config.get("free_trial_duration", 1440)
    trial_text = config.get("free_trial_text", "24 Hours")

    is_already_in = False
    with suppress(ApiTelegramException):
        is_already_in = bot.get_chat_member(ch_id, u_id).status in ["member", "administrator", "creator"]

    if is_already_in:
        current_user = collections["users"].find_one({"user_id": u_id, "channel_id": ch_id, "status": "active"})
        new_expiry = (float(current_user["expiry"]) if current_user and current_user.get("expiry") else datetime.now(IST).timestamp()) + (calc_mins * 60)
        collections["users"].update_one(
            {"user_id": u_id, "channel_id": ch_id},
            {"$set": {"status": "active", "duration_mins": calc_mins, "expiry": new_expiry, "pending_attempts": 0, "used_free_trial": True}, "$inc": {"total_purchases": 1, "total_mins": calc_mins}, "$push": {"history": {"joined_at": datetime.now(IST).timestamp(), "expires_at": new_expiry, "duration_mins": calc_mins, "note": "free_trial_claim"}}},
            upsert=True
        )
        bot.send_message(u_id, f"🥳 *Free Trial Activated!*\n\nYou have been granted {trial_text} of free access. Enjoy!", parse_mode="Markdown")
    else:
        direct_link = create_instant_channel_join_link(ch_id, u_id)
        new_expiry = datetime.now(IST).timestamp() + (calc_mins * 60)
        collections["users"].update_one(
            {"user_id": u_id, "channel_id": ch_id},
            {
                "$set": {
                    "status": "active",
                    "duration_mins": calc_mins,
                    "expiry": new_expiry,
                    "pending_attempts": 0,
                    "used_free_trial": True,
                    "direct_join_link": direct_link
                },
                "$unset": {"pending_join_link": ""},
                "$inc": {"total_purchases": 1, "total_mins": calc_mins},
                "$push": {"history": {"joined_at": datetime.now(IST).timestamp(), "expires_at": new_expiry, "duration_mins": calc_mins, "note": "free_trial_claim"}}
            },
            upsert=True
        )
        markup = InlineKeyboardMarkup(row_width=1).add(InlineKeyboardButton("🚀 Quick Join Channel", url=direct_link)) if direct_link else None
        with suppress(ApiTelegramException):
            bot.send_message(u_id, f"🥳 *Free Trial Activated!*\n\nYour *{trial_text}* free trial has started immediately! Tap below to join the channel directly:", parse_mode="Markdown", protect_content=True, reply_markup=markup)

    with suppress(ApiTelegramException):
        bot.send_message(chat_id, "🔄 Menu updated.", reply_markup=get_persistent_menu(u_id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("freetrial_"))
def free_trial_callback(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    if check_and_lock_click(call.message.chat.id, call.message.message_id):
        return bot.answer_callback_query(call.id, "Processing...")

    u_id = call.from_user.id
    ch_id = int(call.data.split("_")[1])
    process_free_trial_logic(u_id, ch_id, call.message.chat.id, call.message.message_id, call.id)

@bot.callback_query_handler(func=lambda call: call.data == "enter_voucher")
def enter_voucher_start(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    if check_and_lock_click(call.message.chat.id, call.message.message_id):
        return
    bot.answer_callback_query(call.id)
    msg = bot.send_message(call.message.chat.id, "🎟️ Please send your 10-digit Promo/Voucher code:")
    bot.register_next_step_handler(message=msg, callback=process_voucher)

def process_voucher(message: Message) -> None:
    log_activity(message.from_user.id, "Input Voucher", message)
    code = message.text.strip().upper()
    now_ts = datetime.now(IST).timestamp()
    vouch_doc = collections["config"].find_one({"_id": "vouchers"})

    if vouch_doc and "codes" in vouch_doc and code in vouch_doc["codes"]:
        v_data = vouch_doc["codes"][code]
        if float(v_data["expires_at"]) > now_ts:
            if message.from_user.id in v_data.get("used_by", []):
                temp = bot.send_message(message.chat.id, "❌ You have already used this promo code.")
            elif len(v_data.get("used_by", [])) >= v_data.get("limit", 9999999):
                temp = bot.send_message(message.chat.id, "❌ This promo code has reached its usage limit.")
            else:
                update_user_session(message.from_user.id, {"$set": {"applied_voucher": code}})
                temp = bot.send_message(message.chat.id, f"✅ Promo code applied successfully! ({v_data['discount']}% OFF)")
        else:
            temp = bot.send_message(message.chat.id, "❌ This promo code has expired.")
    else:
        temp = bot.send_message(message.chat.id, "❌ Invalid promo code.")

    time.sleep(1.5)
    with suppress(ApiTelegramException):
        bot.delete_message(message.chat.id, message.message_id)
    with suppress(ApiTelegramException):
        bot.delete_message(message.chat.id, temp.message_id)

    update_user_session(message.from_user.id, {"$set": {"last_restart": 0}})
    _handle_restart(message.from_user.id, message.chat.id)

# --- RAZORPAY AUTOMATED CHECKOUT & VERIFICATION ---
@bot.callback_query_handler(func=lambda call: call.data.startswith("select_"))
def user_pays(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    if is_user_locked(call.from_user.id):
        return bot.answer_callback_query(call.id, "Locked", show_alert=True) or send_locked_message(call.message.chat.id, call.from_user.id)
    if check_and_lock_click(call.message.chat.id, call.message.message_id):
        return bot.answer_callback_query(call.id, "Generating payment link...")
    bot.answer_callback_query(call.id, "Generating Razorpay checkout link...")

    with suppress(ApiTelegramException):
        bot.delete_message(call.message.chat.id, call.message.message_id)

    cleanup_expired_discounts()
    _, ch_id, p_time = call.data.split("_")
    ch_id = int(ch_id)
    ch_data = collections["channels"].find_one({"channel_id": ch_id})

    if not ch_data or p_time not in ch_data.get("plans", {}):
        return bot.send_message(call.message.chat.id, "❌ Selected plan is unavailable.")

    base_price = float(ch_data["plans"][p_time])
    active_discounts = (collections["config"].find_one({"_id": "discounts"}) or {}).get("plans", {})
    now_ts = datetime.now(IST).timestamp()

    session = get_user_session(call.from_user.id)
    applied_voucher = session.get("applied_voucher")
    voucher_perc = 0
    if applied_voucher:
        vouch_doc = collections["config"].find_one({"_id": "vouchers"})
        if vouch_doc and "codes" in vouch_doc and applied_voucher in vouch_doc["codes"]:
            v_data = vouch_doc["codes"][applied_voucher]
            if float(v_data["expires_at"]) > now_ts and len(v_data.get("used_by", [])) < v_data.get("limit", 9999999) and call.from_user.id not in v_data.get("used_by", []):
                voucher_perc = float(v_data["discount"])

    if voucher_perc > 0:
        discount_perc = voucher_perc
    else:
        discount_perc = max([
            float(active_discounts.get("all", {}).get("percentage", 0)) if float(active_discounts.get("all", {}).get("expires_at", 0)) > now_ts else 0,
            float(active_discounts.get(p_time, {}).get("percentage", 0)) if float(active_discounts.get(p_time, {}).get("expires_at", 0)) > now_ts else 0
        ])

    final_price = round(base_price * (1 - discount_perc / 100), 2) if discount_perc > 0 else round(base_price, 2)
    num, unit, _ = parse_time_str(p_time)

    plink = create_razorpay_payment_link(call.from_user.id, ch_id, p_time, final_price)
    if not plink or "short_url" not in plink:
        return bot.send_message(
            call.message.chat.id,
            "❌ *Unable to generate Razorpay Payment Link right now.* Please try again using /restart or contact Admin.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🔄 Try Again", callback_data="restart_flow"))
        )

    plink_id = plink["id"]
    short_url = plink["short_url"]

    update_user_session(
        call.from_user.id,
        {
            "$set": {
                "selected_plan": {
                    "ch_id": ch_id,
                    "p_time": p_time,
                    "price": final_price,
                    "plink_id": plink_id,
                    "short_url": short_url,
                    "used_discount": discount_perc > 0
                }
            }
        }
    )

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton(f"💳 Pay Now (₹{final_price:.2f})", url=short_url))
    markup.add(InlineKeyboardButton("✅ Verify Payment Status", callback_data=f"rzpchk_{plink_id}"))
    markup.add(InlineKeyboardButton("🔄 Change Plan / Restart", callback_data="restart_flow"))
    markup.add(InlineKeyboardButton("📞 Contact Admin", url=CONTACT_URL))

    msg_text = (
        f"💳 *Razorpay Automated Checkout*\n\n"
        f"📺 *Group:* {ch_data.get('name', 'Premium Audio')}\n"
        f"⏳ *Plan:* {num} {unit}\n"
        f"💰 *Payable Amount:* *₹{final_price:.2f}*\n"
        f"🆔 *Order Ref:* `{plink_id}`\n\n"
        f"1️⃣ Click **Pay Now** below to pay via UPI, Card, or NetBanking.\n"
        f"2️⃣ Your membership will activate **automatically** once payment is completed (or click *Verify Payment Status* anytime)."
    )

    sent_msg = bot.send_message(call.message.chat.id, msg_text, reply_markup=markup, parse_mode="Markdown")
    update_user_session(call.from_user.id, {"$set": {"menu_msg_id": sent_msg.message_id}})

@bot.callback_query_handler(func=lambda call: call.data.startswith("rzpchk_"))
def verify_razorpay_status_callback(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    plink_id = call.data.split("_", 1)[1]

    plink_data = fetch_razorpay_payment_link(plink_id)
    if not plink_data:
        return bot.answer_callback_query(call.id, "⚠️ Unable to reach Razorpay at the moment. Please try again in a few seconds.", show_alert=True)

    status = plink_data.get("status", "")
    if status == "paid":
        bot.answer_callback_query(call.id, "✅ Payment Verified! Activating your subscription...", show_alert=True)
        notes = plink_data.get("notes") or {}
        sess = get_user_session(call.from_user.id)
        sel_plan = sess.get("selected_plan", {})

        u_id = int(notes.get("user_id", call.from_user.id))
        ch_id = int(notes.get("ch_id", sel_plan.get("ch_id", 0)))
        p_time = str(notes.get("p_time", sel_plan.get("p_time", "1m")))
        paid_amount = float(notes.get("amount", sel_plan.get("price", float(plink_data.get("amount", 0)) / 100.0)))

        payments_list = plink_data.get("payments") or []
        pay_id = payments_list[0].get("payment_id", plink_id) if payments_list else plink_id

        activate_subscription_after_payment(u_id, ch_id, p_time, paid_amount, pay_id)
    elif status in ["cancelled", "expired"]:
        bot.answer_callback_query(call.id, f"❌ This payment link has {status}. Please click Restart to generate a new link.", show_alert=True)
    else:
        bot.answer_callback_query(call.id, "⏳ Payment not captured yet! Please complete the payment using the 'Pay Now' button first.", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data in ["restart_flow"] or call.data.startswith("checkjoin_"))
def flow_callbacks(call: CallbackQuery) -> None:
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    log_activity(call.from_user.id, call.data)

    if is_user_locked(call.from_user.id):
        return bot.answer_callback_query(call.id, "Locked", show_alert=True) or send_locked_message(call.message.chat.id, call.from_user.id)
    if check_and_lock_click(call.message.chat.id, call.message.message_id):
        return bot.answer_callback_query(call.id, "Processing...")

    bot.answer_callback_query(call.id, "Processing...")
    if call.data == "restart_flow":
        with suppress(ApiTelegramException):
            bot.delete_message(call.message.chat.id, call.message.message_id)
        _handle_restart(call.from_user.id, call.message.chat.id)
    else:
        if is_user_member(call.from_user.id):
            with suppress(ApiTelegramException):
                bot.delete_message(call.message.chat.id, call.message.message_id)
            target = call.data.split("_")[1]
            send_subscription_plans(call.message.chat.id, call.from_user.id, target if target != "none" else None)
        else:
            bot.answer_callback_query(call.id, "❌ You haven't joined the channel yet! Please join first.", show_alert=True)
        unlock_click(call.message.chat.id, call.message.message_id)

def _handle_restart(user_id: int, chat_id: int) -> None:
    session = get_user_session(user_id)
    now = time.time()

    update_user_session(user_id, {"$unset": {"selected_plan": "", "pending_approval": ""}, "$set": {"last_restart": now}})
    if session.get("menu_msg_id"):
        with suppress(ApiTelegramException):
            bot.delete_message(chat_id, session["menu_msg_id"])
        update_user_session(chat_id, {"$unset": {"menu_msg_id": ""}})

    bot.send_message(chat_id, "🔄 *Menu loaded.*", parse_mode="Markdown", reply_markup=get_persistent_menu(user_id))
    send_subscription_plans(chat_id, user_id)

@bot.message_handler(commands=["start", "restart"])
def start_handler(message: Message) -> None:
    bot.clear_step_handler_by_chat_id(message.chat.id)
    user_id = message.from_user.id
    log_activity(user_id, message.text, message)

    if message.text.startswith("/start"):
        with suppress(ApiTelegramException):
            bot.send_message(message.chat.id, "🔄 System persistent menu deployed below.", reply_markup=get_persistent_menu(user_id))

    if user_id != ADMIN_ID:
        ensure_user_forum_topic(user_id, message.from_user.username or message.from_user.first_name)

    if is_user_locked(user_id):
        return send_locked_message(message.chat.id, user_id)
    if user_id != ADMIN_ID and is_pending_feedback(user_id):
        return bot.send_message(message.chat.id, "⭐ *We'd love your feedback!* Your subscription has ended—please select a star rating on the review prompt above to continue.", parse_mode="Markdown")

    if message.text.startswith("/restart"):
        return _handle_restart(user_id, message.chat.id)

    session = get_user_session(user_id)
    if session.get("selected_plan"):
        return bot.send_message(message.chat.id, "⚠️ You have an active Razorpay payment link. Please complete it or use /restart to choose a different plan.")

    if user_id == ADMIN_ID and len(message.text.split()) == 1:
        return bot.send_message(message.chat.id, "✅ Admin Panel Active!\n\n/casual - Protected Setup Commands\n/advance - Advanced Tools")

    target_ch = message.text.split()[1] if len(message.text.split()) > 1 else None

    if not is_user_member(user_id):
        markup = InlineKeyboardMarkup(row_width=1).add(InlineKeyboardButton("📢 Join Channel", url=PUBLIC_CHANNEL_LINK)).add(InlineKeyboardButton("✅ I've Joined", callback_data=f"checkjoin_{target_ch or 'none'}"))
        return bot.send_message(message.chat.id, f"⚠️ *To use this bot, you must join our public channel first!*\n\nPlease join {PUBLIC_CHANNEL_USERNAME} and click the button below.", reply_markup=markup, parse_mode="Markdown")

    send_subscription_plans(message.chat.id, user_id, target_ch)

@bot.message_handler(commands=["subscription"])
def check_subscription_status(message: Message) -> None:
    user_id = message.from_user.id
    log_activity(user_id, "/subscription", message)
    if user_id != ADMIN_ID and is_pending_feedback(user_id):
        return bot.send_message(message.chat.id, "⭐ *We'd love your feedback!* Your subscription has ended—please select a star rating on the review prompt above to continue.", parse_mode="Markdown")

    now_ts = datetime.now(IST).timestamp()

    if user_id == ADMIN_ID:
        active_subs = list(collections["users"].find({"status": "active", "expiry": {"$gt": now_ts}}))
        if not active_subs:
            return bot.send_message(message.chat.id, "❌ No users have active subscriptions right now.")

        msg_text = "📜 *All Active Subscriptions (Admin View):*\n\n"
        for s in active_subs:
            ch_name = (collections['channels'].find_one({'channel_id': s['channel_id']}) or {}).get('name', 'Unknown')
            exp_date = datetime.fromtimestamp(float(s['expiry']), IST).strftime('%Y-%m-%d %I:%M %p IST')
            rem = int(float(s['expiry']) - now_ts)
            d, r1 = divmod(rem, 86400)
            h = divmod(r1, 3600)[0]

            user_detail = f"👤 User: {get_user_display(s['user_id'])}\nID: `{s['user_id']}`\n📺 {ch_name}\n⏰ Exp: `{exp_date}`\n⏳ Left: `{d}d {h}h`\n📊 Renewals: {s.get('total_purchases', 1)}\n\n"

            if len(msg_text) + len(user_detail) > 3900:
                bot.send_message(message.chat.id, msg_text, parse_mode="Markdown")
                msg_text = ""
            msg_text += user_detail

        msg_text += f"\n👥 *Total Active Users:* {len(active_subs)}"
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton("➕ Add Missing Users", callback_data="sub_add_missing"))
        return bot.send_message(message.chat.id, msg_text, reply_markup=markup, parse_mode="Markdown")

    valid_subs = [s for s in collections["users"].find({"user_id": user_id, "status": {"$in": ["active", "approved_pending_join"]}}) if s["status"] == "approved_pending_join" or float(s.get("expiry", 0)) > now_ts]
    if not valid_subs:
        return bot.send_message(message.chat.id, "❌ You do not have any active subscriptions right now.")

    msg_text = "📜 *Your Active Subscriptions:*\n\n"
    markup = InlineKeyboardMarkup(row_width=1)
    has_pending = False

    for sub in valid_subs:
        ch_name = (collections["channels"].find_one({"channel_id": sub["channel_id"]}) or {}).get("name", "Premium Audio Group")
        link = bot.create_chat_invite_link(sub["channel_id"], creates_join_request=True).invite_link

        if sub["status"] == "approved_pending_join":
            msg_text += f"📺 *{ch_name}*\n⏳ Status: `Pending Join` (Timer starts when you join)\n\n"
            markup.add(InlineKeyboardButton(f"👉 Request to Join {ch_name}", url=link))
            has_pending = True
        else:
            d, r1 = divmod(int(float(sub["expiry"]) - now_ts), 86400)
            h, r2 = divmod(r1, 3600)
            msg_text += f"📺 *{ch_name}*\n⏰ Expires on: `{datetime.fromtimestamp(float(sub['expiry']), IST).strftime('%Y-%m-%d %I:%M %p IST')}`\n⏳ Time Left: `{d}d {h}h {divmod(r2, 60)[0]}m {divmod(r2, 60)[1]}s`\n\n"
            markup.add(InlineKeyboardButton(f"👉 Enter / Re-Join {ch_name}", url=link))
            has_pending = True

    if has_pending:
        bot.send_message(message.chat.id, msg_text, reply_markup=markup, parse_mode="Markdown")
    else:
        bot.send_message(message.chat.id, msg_text, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "sub_add_missing")
def sub_add_missing_callback(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    bot.answer_callback_query(call.id, "Scanning for missing users... This may take a moment.")

    now_ts = datetime.now(IST).timestamp()
    active_subs = list(collections["users"].find({"status": "active", "expiry": {"$gt": now_ts}}))
    missing_users = []

    for sub in active_subs:
        try:
            member = bot.get_chat_member(sub["channel_id"], sub["user_id"])
            if member.status in ["left", "kicked", "restricted"]:
                missing_users.append(sub)
        except ApiTelegramException:
            missing_users.append(sub)

    if not missing_users:
        bot.send_message(call.message.chat.id, "✅ All active users are currently in their channels.")
        return

    markup = InlineKeyboardMarkup(row_width=1)
    for u in missing_users:
        markup.add(InlineKeyboardButton(f"➕ Add {get_user_display(u['user_id'])} ({u['user_id']})", callback_data=f"sub_fadd_{u['user_id']}_{u['channel_id']}"))

    bot.send_message(call.message.chat.id, "⚠️ *Users with active subscriptions but NOT in the channel:*\n\nClick a user to send them an invite link:", reply_markup=markup, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("sub_fadd_"))
def sub_fadd_callback(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    with suppress(ApiTelegramException):
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    parts = call.data.split("_")
    u_id, ch_id = int(parts[2]), int(parts[3])

    try:
        bot.unban_chat_member(ch_id, u_id)
    except Exception:
        pass

    try:
        link = bot.create_chat_invite_link(ch_id, creates_join_request=True)
        collections["users"].update_one(
            {"user_id": u_id, "channel_id": ch_id},
            {"$set": {"status": "approved_pending_join"}}
        )

        bot.send_message(u_id, "🥳 *Admin has granted you access!*\n\n⚠️ Click the button below to send a Join Request. The bot will automatically accept you!", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("👉 Request to Join Channel", url=link.invite_link)))
        bot.answer_callback_query(call.id, f"Invite link sent to {u_id}!", show_alert=True)
    except Exception:
        fallback_url = "https://t.me/+9ziR29BtQAUyNzNl"
        try:
            bot.send_message(u_id, "🥳 *Admin has granted you access!*\n\n⚠️ Click the button below to Join the Channel!", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("👉 Join Channel", url=fallback_url)))
            bot.answer_callback_query(call.id, f"Fallback invite link sent to {u_id}!", show_alert=True)
        except Exception as e2:
            bot.answer_callback_query(call.id, f"Failed: {e2}", show_alert=True)

@bot.chat_join_request_handler()
def handle_join_request(message) -> None:
    u_id, ch_id, now_ts = message.from_user.id, message.chat.id, datetime.now(IST).timestamp()

    if collections["users"].find_one({"user_id": u_id, "channel_id": ch_id, "status": "active", "expiry": {"$gt": now_ts}}):
        try:
            bot.approve_chat_join_request(ch_id, u_id)
            bot.send_message(u_id, "✅ Your re-join request was approved! Welcome back to the channel.")
            return logger.info(f"Active User {u_id} re-joined channel {ch_id}.")
        except Exception as e:
            return logger.error(f"Error re-joining active user: {e}")

    pending_user = collections["users"].find_one({"user_id": u_id, "channel_id": ch_id, "status": "approved_pending_join"})

    if pending_user:
        try:
            bot.approve_chat_join_request(ch_id, u_id)
            duration_mins = pending_user.get("duration_mins", 0)
            expiry_datetime = datetime.now(IST) + timedelta(minutes=duration_mins)

            collections["users"].update_one(
                {"_id": pending_user["_id"]},
                {"$set": {"status": "active", "expiry": expiry_datetime.timestamp()}, "$push": {"history": {"joined_at": datetime.now(IST).timestamp(), "expires_at": expiry_datetime.timestamp(), "duration_mins": duration_mins}}}
            )

            bot.send_message(u_id, f"✅ Your join request was approved! Your timer has officially started for {duration_mins} minutes.")

            novel_text = ""
            for status, title in [("completed", "Completed"), ("ongoing", "Ongoing")]:
                novels = list(collections["novels"].find({"status": status}))
                if novels:
                    novel_text += f"{'📚' if status == 'completed' else '🔄'} <b>{title} Novels:</b>\n" + "".join(f"🔹 <a href='{n.get('link', '#')}'>{n['name']}</a>\n" if n.get("link") else f"🔹 {n['name']}\n" for n in novels) + "\n"
            if novel_text.strip():
                with suppress(ApiTelegramException):
                    bot.send_message(u_id, f"📚 <b>Available Novels:</b>\n\n{novel_text.strip()}", parse_mode="HTML", disable_web_page_preview=True)

            user_doc = collections["users"].find_one({"user_id": u_id})
            topic_id = user_doc.get("topic_id") if user_doc else None
            log_text = f"🟢 *New Timer Started*\n\n👤 User: {get_user_display(u_id)}\nID: `{u_id}`\n📺 Channel: {(collections['channels'].find_one({'channel_id': ch_id}) or {}).get('name', str(ch_id))}\n⏳ Duration: {duration_mins} mins\n⏰ Expires: {expiry_datetime.strftime('%Y-%m-%d %I:%M %p IST')}\n📊 Total Times Subscribed: {pending_user.get('total_purchases', 1)}\n⏳ Total Time Subscribed: {pending_user.get('total_mins', duration_mins)} mins"
            with suppress(ApiTelegramException):
                if topic_id:
                    bot.send_message(FORUM_GROUP_ID, log_text, parse_mode="Markdown", message_thread_id=topic_id)
                else:
                    bot.send_message(LOG_CHANNEL_ID, log_text, parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Error accepting join request: {e}")
    else:
        with suppress(ApiTelegramException):
            bot.decline_chat_join_request(ch_id, u_id)
            bot.send_message(u_id, "❌ Your join request was declined because you do not have an active payment approval or your subscription has expired.")

def kick_expired_users() -> None:
    now = datetime.now(IST).timestamp()
    for user in list(collections["users"].find({"status": "active", "expiry": {"$ne": None, "$lte": now}})):
        try:
            bot.ban_chat_member(user["channel_id"], user["user_id"])
            bot.unban_chat_member(user["channel_id"], user["user_id"])

            markup = InlineKeyboardMarkup(row_width=5).row(*(InlineKeyboardButton(f"{i}⭐", callback_data=f"fb_{user['channel_id']}_{i}") for i in range(1, 6)))
            bot.send_message(user["user_id"], "⏰ *Your subscription has ended.*\n\nThank you for subscribing! Now that your plan is complete, please take a moment to rate your experience below to help us improve:", reply_markup=markup, parse_mode="Markdown")

            user_doc = collections["users"].find_one({"user_id": user["user_id"]})
            topic_id = user_doc.get("topic_id") if user_doc else None
            log_text = f"🔴 *Subscription Expired*\n\n👤 User: {get_user_display(user['user_id'])}\nID: `{user['user_id']}`\n📺 Channel: {(collections['channels'].find_one({'channel_id': user['channel_id']}) or {}).get('name', str(user['channel_id']))}\n📊 Total Times Subscribed: {user.get('total_purchases', 1)}\n⏳ Total Time Subscribed: {user.get('total_mins', 0)} mins\nStatus: Removed & Pending Feedback."
            with suppress(ApiTelegramException):
                if topic_id:
                    bot.send_message(FORUM_GROUP_ID, log_text, parse_mode="Markdown", message_thread_id=topic_id)
                else:
                    bot.send_message(LOG_CHANNEL_ID, log_text, parse_mode="Markdown")

        except Exception as e:
            logger.error(f"Error kicking user {user.get('user_id')}: {e}")
        finally:
            collections["users"].update_one({"_id": user["_id"]}, {"$set": {"status": "pending_feedback"}, "$push": {"history": {"exited_at": now, "note": "expired_kicked"}}})

@bot.callback_query_handler(func=lambda call: call.data.startswith("fb_"))
def handle_feedback(call: CallbackQuery) -> None:
    log_activity(call.from_user.id, call.data)
    if check_and_lock_click(call.message.chat.id, call.message.message_id):
        return
    bot.answer_callback_query(call.id, "Feedback submitted!")
    _, ch_id, rating = call.data.split("_")

    user_doc = collections["users"].find_one({"user_id": call.from_user.id})
    topic_id = user_doc.get("topic_id") if user_doc else None
    log_text = f"⭐️ *New Feedback*\n\n👤 User: {get_user_display(call.from_user.id)}\nID: `{call.from_user.id}`\n📺 Channel: {(collections['channels'].find_one({'channel_id': int(ch_id)}) or {}).get('name', str(ch_id))}\nRating: {rating} Stars"
    with suppress(ApiTelegramException):
        if topic_id:
            bot.send_message(FORUM_GROUP_ID, log_text, parse_mode="Markdown", message_thread_id=topic_id)
        else:
            bot.send_message(LOG_CHANNEL_ID, log_text, parse_mode="Markdown")

    collections["users"].update_one({"user_id": call.from_user.id, "channel_id": int(ch_id), "status": "pending_feedback"}, {"$set": {"status": "expired"}})
    bot.edit_message_text(f"✅ Thank you for your {rating}⭐ feedback!\n\nLoading main menu...", call.message.chat.id, call.message.message_id, reply_markup=None)
    _handle_restart(call.from_user.id, call.message.chat.id)

@bot.message_handler(func=lambda m: m.chat.id == FORUM_GROUP_ID and m.message_thread_id is not None, content_types=['text', 'photo', 'video', 'document', 'audio', 'voice', 'sticker'])
def admin_reply_to_user_topic(message: Message):
    if message.from_user.id == bot.get_me().id:
        return
    user_doc = collections["users"].find_one({"topic_id": message.message_thread_id})
    if user_doc:
        try:
            bot.copy_message(user_doc["user_id"], FORUM_GROUP_ID, message.message_id)
        except Exception as e:
            logger.error(f"Failed to copy message to user {user_doc['user_id']}: {e}")

@bot.message_handler(
    func=lambda m: m.chat.id == NOVEL_GROUP_ID,
    content_types=["photo", "audio", "voice", "document"]
)
def index_novel_group_audio(message: Message) -> None:
    try:
        topic_id = message.message_thread_id or 1
        if message.photo:
            save_topic_cover_msg_id(int(topic_id), int(message.message_id))
            COVER_BYTES_CACHE.pop(int(topic_id), None)
            return
        title = ""
        duration = 0
        file_size = 0
        if message.audio:
            title = (message.audio.title or message.audio.file_name or "").strip()
            duration = message.audio.duration or 0
            file_size = message.audio.file_size or 0
        elif message.voice:
            duration = message.voice.duration or 0
            file_size = message.voice.file_size or 0
        elif message.document:
            fname = (message.document.file_name or "").strip()
            mime = (message.document.mime_type or "").lower()
            if not mime.startswith("audio/") and not fname.lower().endswith(AUDIO_EXTS):
                return
            title = os.path.splitext(fname)[0].replace("_", " ").strip()
            file_size = message.document.file_size or 0
        if not title and message.caption:
            for line in message.caption.splitlines():
                if line.strip() and not line.strip().startswith("#"):
                    title = line.strip()[:120]
                    break
        if not title:
            title = f"Chapter Audio #{message.message_id}"
        chapter_num = extract_chapter_number(title, message.message_id)
        collections["novel_audios"].update_one(
            {"topic_id": topic_id, "message_id": message.message_id},
            {"$set": {
                "topic_id": topic_id,
                "message_id": message.message_id,
                "title": title,
                "chapter_num": chapter_num,
                "duration": duration,
                "file_size": file_size,
                "timestamp": time.time()
            }},
            upsert=True
        )
    except Exception as e:
        logger.error(f"Error indexing audio: {e}")

@bot.message_handler(func=lambda message: True, content_types=['text', 'photo', 'video', 'document', 'audio', 'voice'])
def fallback_message_handler(message: Message):
    log_activity(message.from_user.id, None, message)

if __name__ == "__main__":
    Thread(target=start_telethon_background, daemon=True).start()
    keep_alive()
    setup_commands()

    scheduler = BackgroundScheduler()
    scheduler.add_job(kick_expired_users, "interval", seconds=30, max_instances=1, coalesce=True)
    scheduler.add_job(cleanup_expired_discounts, "interval", seconds=10, max_instances=1, coalesce=True)
    scheduler.add_job(check_pending_razorpay_links, "interval", seconds=20, max_instances=1, coalesce=True)
    scheduler.add_job(cleanup_expired_clicks, "interval", hours=1, max_instances=1, coalesce=True)
    scheduler.start()

    with suppress(Exception):
        collections["config"].update_one(
            {"_id": "bot_poller_lock"},
            {"$set": {"instance_id": INSTANCE_ID, "started_at": time.time()}},
            upsert=True
        )
    time.sleep(1.5)
    with suppress(Exception):
        bot.remove_webhook()
    logger.info("Bot is running...")
    bot.infinity_polling(timeout=20, long_polling_timeout=10, allowed_updates=telebot.util.update_types, logger_level=logging.CRITICAL)
