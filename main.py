import asyncio
import logging
import time
import requests
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient, events, Button, functions, types
from pymongo import MongoClient

# ================== CONFIG ==================

API_ID = 29568441
API_HASH = "b32ec0fb66d22da6f77d355fbace4f2a"
BOT_TOKEN = "8302453295:AAFLJEUx-JAa75jbtIDLw-JelzKOPWhPs-8"

SUPPORT_CHAT_LINK = "https://t.me/kustbotschat"
UPDATES_CHANNEL_LINK = "https://t.me/kustbots"
UPI_DM_LINK = "https://t.me/KustXoffical"

# --- Chat Farmer Assets ---
FARMER_FORWARD_1 = ("kustvault", 3)
FARMER_FORWARD_2 = ("kustvault", 2)
FARMER_FORWARD_3 = ("kustvault", 4)
FARMER_API_URL = "https://chat-auth11-bad82326a8c1.herokuapp.com"

# --- Code Claimer Assets ---
CLAIMER_FORWARD_1 = ("kustvault", 7)
CLAIMER_FORWARD_2 = ("kustvault", 6)
CLAIMER_FORWARD_3 = ("kustvault", 5)
CLAIMER_API_URL = "https://code-auth-0cd38a139230.herokuapp.com"

# Start image
START_IMAGE_URL = "https://filehosting.kustbotsweb.workers.dev/-p_.jpg"

# MongoDB
MONGO_URL = "mongodb+srv://kustbotsweb_db_user:z7YqNFmFOvVHKl4B@kust-payments.hiin3lu.mongodb.net/?appName=kust-payments"
mongo = MongoClient(MONGO_URL)
db = mongo["kustfarm"]
users_col = db["users"]
demos_col = db["demos"]

# Bot owner
BOT_OWNER_ID = 7618467489

# OxaPay API
OXAPAY_API_KEY = "SNJEE3-MOEI0B-ZR0FW4-UWSLXH"
OXAPAY_API_BASE = "https://api.oxapay.com"

# Active users checker settings (Checks Farmer API by default)
ACTIVE_USERS_ENDPOINT = f"{FARMER_API_URL}/active_users"
RENAME_USER_ENDPOINT = f"{FARMER_API_URL}/rename_user"

# --- PRICING PLANS ---

# Farmer Plans
PLANS_FARMER = {
    "6h":  {"label": "6 Hours",   "amount": 1.0,  "hours": 6},
    "12h": {"label": "12 Hours",  "amount": 1.5,  "hours": 12},
    "1d":  {"label": "1 Day",     "amount": 2.3,  "hours": 24},
    "2d":  {"label": "2 Days",    "amount": 4.3,  "hours": 48},
    "4d":  {"label": "4 Days",    "amount": 7.8,  "hours": 96},
    "7d":  {"label": "7 Days",    "amount": 13.3, "hours": 168},
}

# Claimer Plans (Base + 0.2 USDT)
PLANS_CLAIMER = {
    "6h":  {"label": "6 Hours",   "amount": 0.1,  "hours": 6},
    "12h": {"label": "12 Hours",  "amount": 1.5,  "hours": 12},
    "1d":  {"label": "1 Day",     "amount": 2.5,  "hours": 24},
    "2d":  {"label": "2 Days",    "amount": 4.5,  "hours": 48},
    "4d":  {"label": "4 Days",    "amount": 8.0,  "hours": 96},
    "7d":  {"label": "7 Days",    "amount": 13.5, "hours": 168},
}

PAYMENT_TIMEOUT = 15 * 60
POLL_INTERVAL = 10

# Active users checker settings
ACTIVE_USERS_POLL_INTERVAL = 300  # seconds between active_users polls (5 minutes)
REMINDER_THRESHOLD_MINUTES = 60   # notify when <= 60 minutes remain

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("stake_payment_bot")

bot = TelegramClient("stake_farmer_payment_session", API_ID, API_HASH).start(bot_token=BOT_TOKEN)

user_sessions = {}
user_tasks = {}

# keep track of reminders sent to avoid duplicates: { username_lc: expires_iso }
_reminder_sent = {}

# ================== OXAPAY HELPERS ==================

def create_invoice(amount: float, currency: str = "USDT", lifetime: int = 60):
    """
    Synchronous network call to create invoice. Returns raw JSON.
    """
    url = f"{OXAPAY_API_BASE}/v1/payment/invoice"
    headers = {"merchant_api_key": OXAPAY_API_KEY, "Content-Type": "application/json"}
    body = {"amount": amount, "currency": currency, "lifetime": lifetime}
    r = requests.post(url, headers=headers, json=body, timeout=15)
    r.raise_for_status()
    return r.json()

def query_invoice(track_id: str):
    """
    Synchronous network call to query invoice. Returns raw JSON.
    """
    url = f"{OXAPAY_API_BASE}/merchants/inquiry"
    headers = {"Content-Type": "application/json"}
    body = {"merchant": OXAPAY_API_KEY, "trackId": track_id}
    r = requests.post(url, headers=headers, json=body, timeout=15)
    r.raise_for_status()
    return r.json()

def activate_subscription(username_with_at: str, hours: int, api_base: str):
    """
    Synchronous activation call. Uses the specific API base URL (Farmer or Claimer).
    """
    try:
        params = {
            "user": username_with_at,
            "admin": "admin1234",
            "duration": hours
        }
        url = f"{api_base}/auth" 
        # use GET as original code
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        logger.info(f"[ACTIVATE] Activated subscription for {username_with_at} on {api_base} for {hours} hours. Response: {r.text}")
        return True
    except Exception as e:
        logger.exception(f"[ACTIVATE] Failed activation API for {username_with_at} on {api_base}: {e}")
        return False

def extract_status_from_query_response(resp_json):
    """
    OxaPay responses may vary. Try multiple common paths to find a status string.
    """
    if not resp_json:
        return None
    # direct status field
    status = None
    if isinstance(resp_json, dict):
        status = resp_json.get("status")
        if status:
            return str(status).lower()
        # sometimes nested
        data = resp_json.get("data") or resp_json.get("result") or resp_json.get("response")
        if isinstance(data, dict):
            s = data.get("status") or data.get("payment_status") or data.get("state")
            if s:
                return str(s).lower()
        # sometimes a list
        if isinstance(resp_json.get("data"), list) and len(resp_json.get("data")) > 0:
            el = resp_json.get("data")[0]
            if isinstance(el, dict):
                s = el.get("status")
                if s:
                    return str(s).lower()
    return None

async def wait_for_payment(user_id: int, track_id: str, plan_key: str, product_type: str):
    session = user_sessions.get(user_id)
    if not session:
        logger.warning(f"wait_for_payment: no session for {user_id}")
        return False

    # Determine which plan dict and config to use
    if product_type == "claimer":
        plans_dict = PLANS_CLAIMER
        api_url = CLAIMER_API_URL
        forwards = [CLAIMER_FORWARD_1, CLAIMER_FORWARD_2, CLAIMER_FORWARD_3]
        product_name = "Code Claimer"
    else:
        plans_dict = PLANS_FARMER
        api_url = FARMER_API_URL
        forwards = [FARMER_FORWARD_1, FARMER_FORWARD_2, FARMER_FORWARD_3]
        product_name = "Chat Farmer"

    plan = plans_dict.get(plan_key)
    if not plan:
        logger.warning(f"wait_for_payment: invalid plan {plan_key} for {product_type}")
        return False

    label = plan["label"]
    hours = plan["hours"]

    start = time.time()
    while time.time() - start < PAYMENT_TIMEOUT:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            # run blocking network call in a thread
            data = await asyncio.to_thread(query_invoice, track_id)
            status = extract_status_from_query_response(data) or ""
            status = status.lower()

            logger.info(f"Invoice {track_id} ({product_type}) status check: {status}")

            if status == "paid":
                username_clean = session.get("username", "UNKNOWN")

                # call activation API in a background thread
                activation_ok = await asyncio.to_thread(activate_subscription, f"@{username_clean}", hours, api_url)

                # Persist username to DB if not present
                try:
                    users_col.update_one({"user_id": user_id}, {"$set": {"username": username_clean}}, upsert=True)
                except Exception:
                    logger.exception("Failed to update DB with username on payment.")

                # Notify user
                await bot.send_message(
                    user_id,
                    f"✅ Payment confirmed!\n\n"
                    f"Your <b>{product_name} - {label}</b> subscription is activated.\n"
                    f"Stake Username: <code>@{username_clean}</code>\n"
                    f"Duration: <b>{hours} hours</b>.",
                    parse_mode="html"
                )

                # FORWARD + PIN
                for chat, msg_id in forwards:
                    try:
                        try:
                            source_entity = await bot.get_entity(chat)
                        except Exception as e:
                            logger.error(f"Could not resolve source entity '{chat}': {e}")
                            source_entity = chat 

                        fwd = await bot.forward_messages(entity=user_id, messages=msg_id, from_peer=source_entity)
                        if isinstance(fwd, list):
                            fwd = fwd[0]
                        try:
                            await bot.pin_message(user_id, fwd.id, notify=True)
                        except Exception:
                            pass
                    except Exception as e:
                        logger.exception(f"Forward error for {chat} msg {msg_id}: {e}")

                if not activation_ok:
                    logger.warning(f"Activation API returned failure for @{username_clean} on {product_name} after payment {track_id}")

                return True

            if status in ("expired", "cancelled", "cancel", "failed"):
                await bot.send_message(user_id, f"❌ Invoice for {product_name} ({label}) expired or cancelled.")
                return False

        except Exception as e:
            logger.exception(f"Invoice query error for track {track_id}: {e}")

    # timeout reached
    await bot.send_message(user_id, "⏳ Payment not confirmed. Create a new invoice.")
    return False

# ================== RENAME / ACTIVE USERS API HELPERS ==================

def get_active_users():
    """
    Call GET /active_users on the FARMER API.
    (Currently only tracking Farmer active users for reminders)
    """
    try:
        r = requests.get(ACTIVE_USERS_ENDPOINT, timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.exception(f"Failed to fetch active users: {e}")
        return None

def rename_user_api(old_username: str, new_username: str):
    """
    POST /rename_user with form fields old_username, new_username and admin.
    Defaulting to Farmer API for rename logic.
    """
    try:
        if old_username and not old_username.startswith("@"):
            old_username = f"@{old_username}"
        if new_username and not new_username.startswith("@"):
            new_username = f"@{new_username}"
            
        data = {"old_username": old_username, "new_username": new_username, "admin": "admin1234"}
        r = requests.post(RENAME_USER_ENDPOINT, data=data, timeout=20)

        if r.status_code >= 200 and r.status_code < 300:
            try:
                return r.json()
            except Exception:
                return {"ok": True, "status_code": r.status_code, "response_text": r.text}
        else:
            try:
                err = r.json()
            except Exception:
                err = r.text
            logger.warning(f"rename_user_api returned error {r.status_code}: {err}")
            return {"ok": False, "status_code": r.status_code, "response": err}
    except Exception as e:
        logger.exception(f"rename_user_api error: {e}")
        return {"ok": False, "error": str(e)}

# ================== ACTIVE USERS CHECKER (background) ==================

def _parse_iso_datetime(s: str):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

async def check_active_users_loop():
    await asyncio.sleep(5)
    logger.info("Active users reminder loop started (Farmer).")
    while True:
        try:
            data = await asyncio.to_thread(get_active_users)
            if not data:
                logger.debug("No active users data returned.")
            else:
                users = data.get("active_users") if isinstance(data, dict) else None
                if isinstance(users, list):
                    now = datetime.now(timezone.utc)
                    for entry in users:
                        try:
                            expires_raw = entry.get("expires")
                            username = entry.get("username") 
                            if not expires_raw or not username:
                                continue
                            expires_dt = _parse_iso_datetime(expires_raw)
                            if not expires_dt:
                                continue

                            if expires_dt.tzinfo is None:
                                expires_dt = expires_dt.replace(tzinfo=timezone.utc)

                            time_left = expires_dt - now
                            minutes_left = time_left.total_seconds() / 60

                            username_clean = username.lstrip("@").strip()
                            if minutes_left <= REMINDER_THRESHOLD_MINUTES and minutes_left > 0:
                                previous = _reminder_sent.get(username_clean.lower())
                                expires_iso = expires_dt.isoformat()
                                if previous == expires_iso:
                                    continue

                                rec = users_col.find_one({"username": username_clean})
                                if not rec:
                                    rec = demos_col.find_one({"username": username_clean})
                                if not rec:
                                    continue

                                user_id = rec.get("user_id")
                                if not user_id:
                                    continue

                                try:
                                    rem_text = (
                                        f"⏳ <b>Subscription ending soon</b>\n\n"
                                        f"Your Stake username <code>@{username_clean}</code> subscription expires at {expires_dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}.\n"
                                        f"Time left: approximately {int(minutes_left)} minutes.\n\n"
                                        "Renew now to avoid interruption."
                                    )
                                    buttons = [
                                        [Button.inline("💳 Renew Now", b"buy_sub")],
                                        [Button.url("🛠 Support", SUPPORT_CHAT_LINK)],
                                    ]
                                    await bot.send_message(user_id, rem_text, parse_mode="html", buttons=buttons)
                                    logger.info(f"Sent reminder to @{username_clean} (uid={user_id})")
                                    _reminder_sent[username_clean.lower()] = expires_iso
                                except Exception as e:
                                    logger.exception(f"Failed to send reminder to {username_clean}: {e}")
                        except Exception as ee:
                            logger.exception(f"Error processing active user entry: {ee}")
        except Exception as e:
            logger.exception(f"check_active_users_loop error: {e}")

        await asyncio.sleep(ACTIVE_USERS_POLL_INTERVAL)

# ================== HANDLERS ==================

@bot.on(events.NewMessage(pattern=r"^/start$"))
async def start_handler(event):
    user_id = event.sender_id
    try:
        await bot(functions.messages.SendReactionRequest(
            peer=event.chat_id,
            msg_id=event.message.id,
            reaction=[types.ReactionEmoji(emoticon='👍')],
            add_to_recent=False
        ))
    except:
        pass

    try:
        existing = users_col.find_one({"user_id": user_id})
    except:
        existing = None

    first_time = existing is None

    users_col.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "first_seen": datetime.now(timezone.utc)}},
        upsert=True
    )

    caption_text = (
        "<b>🚀 Kust Bots — Premium Tools</b>\n\n"
        "<b>1️⃣ Chat Farmer:</b> Automated Chat Farming & AI-Driven Replies\n"
        "<b>2️⃣ Code Claimer:</b> High-speed code claiming system\n\n"
        "Tap a button below to continue."
    )

    buttons = []
    # Purchase buttons
    buttons.append([Button.inline("🤖 Buy Chat Farmer", b"buy_product_farmer")])
    buttons.append([Button.inline("⚡ Buy Code Claimer", b"buy_product_claimer")])

    if first_time:
        buttons.append([Button.inline("🎁 Get Free Demo (Farmer)", b"get_demo")])
    else:
        buttons.append([Button.inline("✏️ Edit username", b"edit_username")])
    
    buttons.append([
        Button.url("🛠 Support", SUPPORT_CHAT_LINK),
        Button.url("📢 Updates", UPDATES_CHANNEL_LINK),
    ])

    user_sessions.setdefault(user_id, {"expecting_username": False})

    try:
        await bot.send_file(user_id, START_IMAGE_URL, caption=caption_text, parse_mode="html", buttons=buttons)
    except Exception as e:
        logger.error(f"send_file failed: {e}")
        # Fallback to text if image fails
        await event.respond(caption_text, parse_mode="html", buttons=buttons)

@bot.on(events.NewMessage(pattern=r"^/(help|support)$"))
async def help_handler(event):
    await event.respond(
        "For issues, join support chat:",
        buttons=[[Button.url("🛠 Support Chat", SUPPORT_CHAT_LINK)]]
    )

# --- PRODUCT SELECTION HANDLERS ---

@bot.on(events.CallbackQuery(data=b"buy_sub"))
async def buy_sub_menu_handler(event):
    # If user clicks "Renew" from reminder, show product choice
    await event.answer()
    buttons = [
        [Button.inline("🤖 Buy Chat Farmer", b"buy_product_farmer")],
        [Button.inline("⚡ Buy Code Claimer", b"buy_product_claimer")],
    ]
    await event.edit("<b>Select Product to Renew:</b>", parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(pattern=b"buy_product_"))
async def buy_product_handler(event):
    await event.answer()
    user_id = event.sender_id
    product_type = event.data.decode().split("_")[-1]  # "farmer" or "claimer"

    session = user_sessions.setdefault(user_id, {})
    session["expecting_username"] = True
    session["product"] = product_type  # Store selected product
    session.pop("demo_request", None)

    prod_name = "Chat Farmer" if product_type == "farmer" else "Code Claimer"

    text = (
        f"<b>Buy {prod_name} — Step 1: Provide your Stake username</b>\n\n"
        "Send only the username. Examples:\n"
        "• <code>alice123</code>\n"
        "• <code>@alice123</code>\n\n"
        "Do NOT send profile links or screenshots. After you send the username you'll be asked to confirm it."
    )
    try:
        await event.edit(text, parse_mode="html")
    except:
        await event.respond(text, parse_mode="html")

@bot.on(events.CallbackQuery(data=b"get_demo"))
async def get_demo_handler(event):
    await event.answer()
    user_id = event.sender_id

    session = user_sessions.setdefault(user_id, {})
    session["expecting_username"] = True
    session["demo_request"] = True
    session["product"] = "farmer" # Demos are for farmer by default

    text = (
        "🎁 <b>Free Demo (3 hours) — Step 1: Provide your Stake username</b>\n\n"
        "Send only the username. Examples:\n"
        "• <code>alice123</code>\n"
        "• <code>@alice123</code>\n\n"
        "One demo per Telegram account + Stake username. After you send the username you'll be asked to confirm it."
    )
    try:
        await event.edit(text, parse_mode="html")
    except:
        await event.respond(text, parse_mode="html")

# ================== EDITED USERNAME HANDLER LOGIC ==================

@bot.on(events.CallbackQuery(data=b"edit_username"))
async def edit_username_handler(event):
    await event.answer()
    user_id = event.sender_id
    session = user_sessions.setdefault(user_id, {})
    
    # Reset other states
    session.pop("expecting_username", None)
    session.pop("demo_request", None)
    
    # Set state to expect OLD username
    session["expecting_rename_old"] = True
    session["expecting_rename_new"] = False
    
    text = (
        "<b>✏️ Edit Username — Step 1</b>\n\n"
        "Please enter the <b>OLD</b> Stake username (the one you want to replace).\n\n"
        "Example: <code>alice123</code>"
    )
    try:
        await event.edit(text, parse_mode="html")
    except:
        await event.respond(text, parse_mode="html")

@bot.on(events.CallbackQuery(data=b"edit_cancel"))
async def edit_cancel_handler(event):
    await event.answer()
    user_id = event.sender_id
    session = user_sessions.setdefault(user_id, {})
    session.pop("expecting_rename_old", None)
    session.pop("expecting_rename_new", None)
    session.pop("rename_old_value", None)
    await event.edit("Edit cancelled.", parse_mode="html")

# ================== TEXT INPUT HANDLER ==================

@bot.on(events.NewMessage(pattern=r"^[A-Za-z0-9_@]{3,51}$"))
async def username_handler(event):
    user_id = event.sender_id
    session = user_sessions.setdefault(user_id, {})

    raw_username = event.raw_text.strip()
    username_clean = raw_username.lstrip('@')

    # --- RENAME FLOW STEP 1: CAPTURE OLD USERNAME ---
    if session.get("expecting_rename_old"):
        session["expecting_rename_old"] = False
        session["expecting_rename_new"] = True
        session["rename_old_value"] = username_clean
        
        await event.respond(
            f"✅ Old Username identified: <code>@{username_clean}</code>\n\n"
            "<b>Step 2:</b> Now send the <b>NEW</b> Stake username.",
            parse_mode="html"
        )
        return

    # --- RENAME FLOW STEP 2: CAPTURE NEW USERNAME AND EXECUTE ---
    if session.get("expecting_rename_new"):
        old_username = session.get("rename_old_value")
        new_username = username_clean
        
        # Clear states immediately to prevent loops
        session["expecting_rename_new"] = False
        session.pop("rename_old_value", None)
        
        if not old_username:
             await event.respond("❌ Session expired or invalid state. Please try again from the menu.", parse_mode="html")
             return

        await event.respond(f"🔄 Processing change from <code>@{old_username}</code> to <code>@{new_username}</code>...", parse_mode="html")

        # Call API
        try:
            resp = await asyncio.to_thread(rename_user_api, old_username, new_username)
            
            # If API fails
            if isinstance(resp, dict) and resp.get("ok") is False:
                await event.respond(f"❌ Rename API reported failure: {resp}\n\nLocal username not changed.", parse_mode="html")
                return

            # Update DB references locally so reminders work for the new username
            try:
                # Update users collection
                # 1. Update where it matches exactly as a string
                users_col.update_many({"username": old_username}, {"$set": {"username": new_username}})
                
                # 2. Update inside arrays
                users_col.update_many(
                    {"username": old_username}, 
                    {"$set": {"username.$": new_username}}
                )
                
                # Update demos collection
                demos_col.update_many({"username": old_username}, {"$set": {"username": new_username}})
                
                # Update current session
                session["username"] = new_username
                
            except Exception as e:
                logger.exception("Failed to update DB entries after rename API success.")
            
            await event.respond(f"✅ Success! Username changed from <code>@{old_username}</code> to <code>@{new_username}</code>.", parse_mode="html")
            
        except Exception as e:
            logger.exception("Rename process failed.")
            await event.respond(f"❌ Error during rename: {e}", parse_mode="html")
        
        return

    # --- STANDARD PURCHASE / DEMO FLOW ---
    if not session.get("expecting_username"):
        return  # Ignore unrelated text

    # Save as pending until user confirms
    session["pending_username"] = username_clean
    session["expecting_username"] = False

    text = (
        "You entered the Stake username:\n\n"
        f"<b>@{username_clean}</b>\n\n"
        "Is this correct?"
    )
    buttons = [
        [Button.inline("✅ Yes, this is my username", b"confirm_username_yes")],
        [Button.inline("✏️ No — Edit username", b"confirm_username_no")],
    ]

    await event.respond(text, parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(data=b"confirm_username_no"))
async def confirm_no_handler(event):
    await event.answer()
    user_id = event.sender_id
    session = user_sessions.setdefault(user_id, {})

    # Allow user to send username again
    session["expecting_username"] = True
    session.pop("pending_username", None)

    text = (
        "Okay — please send your Stake username again.\n\n"
        "Examples:\n"
        "• <code>alice123</code>\n"
        "• <code>@alice123</code>\n\n"
        "Send only the username (no links)."
    )
    try:
        await event.edit(text, parse_mode="html")
    except:
        await event.respond(text, parse_mode="html")

@bot.on(events.CallbackQuery(data=b"confirm_username_yes"))
async def confirm_yes_handler(event):
    await event.answer()
    user_id = event.sender_id
    session = user_sessions.get(user_id)

    if not session:
        return await event.respond("Session expired. Restart with /start.")

    pending = session.get("pending_username")
    if not pending:
        return await event.respond("No username pending. Please restart with /start and try again.")

    username_clean = pending
    session.pop("pending_username", None)
    session["username"] = username_clean
    session["expecting_username"] = False

    # Persist username to DB
    try:
        users_col.update_one({"user_id": user_id}, {"$set": {"username": username_clean}}, upsert=True)
    except Exception:
        logger.exception("Failed to persist username to DB on confirm.")

    # If this was a demo request (Only for Farmer)
    if session.get("demo_request"):
        already = demos_col.find_one({"$or": [{"user_id": user_id}, {"username": username_clean}]})
        if already:
            session.pop("demo_request", None)
            return await event.respond("❌ Demo already used by this username or Telegram account.")

        expires_at = datetime.now(timezone.utc) + timedelta(hours=3)
        demos_col.insert_one({"user_id": user_id, "username": username_clean, "expires_at": expires_at})
        users_col.update_one(
            {"user_id": user_id},
            {"$set": {"demo_used": True, "demo_expires": expires_at, "demo_username": username_clean}},
            upsert=True
        )

        # Activate Farmer API
        await asyncio.to_thread(activate_subscription, f"@{username_clean}", 3, FARMER_API_URL)

        text = (
            f"✅ Demo activated for <code>@{username_clean}</code>\n"
            f"Duration: 3 hours."
        )
        try:
            await event.edit(text, parse_mode="html")
        except:
            await event.respond(text, parse_mode="html")

        # FORWARD THREE messages (Farmer Assets)
        for chat, msg_id in [FARMER_FORWARD_1, FARMER_FORWARD_2, FARMER_FORWARD_3]:
            try:
                try:
                    source_entity = await bot.get_entity(chat)
                except Exception as e:
                    logger.error(f"Could not resolve demo forward source '{chat}': {e}")
                    source_entity = chat

                fwd = await bot.forward_messages(entity=user_id, messages=msg_id, from_peer=source_entity)
                if isinstance(fwd, list):
                    fwd = fwd[0]
                try:
                    await bot.pin_message(user_id, fwd.id, notify=True)
                except Exception:
                    pass
            except Exception as e:
                logger.exception(f"Demo forward error: {e}")

        session.pop("demo_request", None)
        return

    # Purchase flow
    product_type = session.get("product", "farmer")
    prod_name = "Chat Farmer" if product_type == "farmer" else "Code Claimer"

    text = (
        f"Stake username saved: <code>@{username_clean}</code>\n"
        f"Product: <b>{prod_name}</b>\n\n"
        "Choose payment method:"
    )
    buttons = [
        [Button.inline("💳 Buy with Crypto", b"buy_crypto")],
        [Button.inline("💵 Buy with UPI", b"buy_upi")],
        [
            Button.url("🛠 Support", SUPPORT_CHAT_LINK),
            Button.url("📢 Updates", UPDATES_CHANNEL_LINK),
        ],
    ]

    try:
        await event.edit(text, parse_mode="html", buttons=buttons)
    except:
        await event.respond(text, parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(data=b"buy_upi"))
async def buy_upi_handler(event):
    await event.answer()
    text = (
        "💵 <b>Buy with UPI</b>\n\n"
        "DM admin and mention your Stake username:\n"
        f"👉 <a href=\"{UPI_DM_LINK}\">@KustXoffical</a>"
    )
    buttons = [[Button.url("DM for UPI Payment", UPI_DM_LINK)]]
    try:
        await event.edit(text, parse_mode="html", buttons=buttons)
    except:
        await event.respond(text, parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(data=b"buy_crypto"))
async def buy_crypto_handler(event):
    await event.answer()
    user_id = event.sender_id
    session = user_sessions.get(user_id)

    if not session or "username" not in session:
        return await event.respond("Restart with /start and send your username.")

    product_type = session.get("product", "farmer")
    
    if product_type == "claimer":
        plans = PLANS_CLAIMER
        header = "⚡ <b>Code Claimer Plans</b>"
    else:
        plans = PLANS_FARMER
        header = "🤖 <b>Chat Farmer Plans</b>"

    text = (
        f"{header}\n"
        "💳 <b>Buy with Crypto (OxaPay)</b>\n\n"
        "Plans:\n"
    )
    
    # Sort keys to ensure order: 6h, 12h, 1d, 2d, 4d, 7d
    ordered_keys = ["6h", "12h", "1d", "2d", "4d", "7d"]
    
    for key in ordered_keys:
        p = plans[key]
        text += f"• {p['label']:<8} — {p['amount']} USDT\n"

    text += "\nSelect your plan:"

    # Generate buttons dynamically based on the plan set
    buttons = [
        [
            Button.inline(f"6h — {plans['6h']['amount']} USDT", b"plan_6h"),
            Button.inline(f"12h — {plans['12h']['amount']} USDT", b"plan_12h"),
        ],
        [
            Button.inline(f"1d — {plans['1d']['amount']} USDT", b"plan_1d"),
            Button.inline(f"2d — {plans['2d']['amount']} USDT", b"plan_2d"),
        ],
        [
            Button.inline(f"4d — {plans['4d']['amount']} USDT", b"plan_4d"),
            Button.inline(f"7d — {plans['7d']['amount']} USDT", b"plan_7d"),
        ],
    ]

    try:
        await event.edit(text, parse_mode="html", buttons=buttons)
    except:
        await event.respond(text, parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(pattern=b"plan_"))
async def plan_handler(event):
    await event.answer()
    user_id = event.sender_id
    session = user_sessions.get(user_id)

    if not session or "username" not in session:
        return await event.respond("Restart with /start and send your username first.")

    product_type = session.get("product", "farmer")
    
    # Select correct plan dictionary
    plans_dict = PLANS_CLAIMER if product_type == "claimer" else PLANS_FARMER

    plan_key = event.data.decode().split("_", 1)[1]
    plan = plans_dict.get(plan_key)
    
    if not plan:
        return await event.respond("Invalid plan. Try again.")

    amount = plan["amount"]
    label = plan["label"]

    if user_id in user_tasks:
        old = user_tasks[user_id]
        if not old.done():
            old.cancel()

    try:
        resp = await asyncio.to_thread(create_invoice, amount)
    except Exception as e:
        logger.exception(f"Invoice error: {e}")
        return await event.respond("Failed to create invoice.")

    data = resp if isinstance(resp, dict) else {}
    track_id = None
    pay_url = None
    if isinstance(data, dict):
        track_id = data.get("track_id") or data.get("trackId") or data.get("trackid")
        pay_url = data.get("payment_url") or data.get("paymentUrl") or data.get("url")
        nested = data.get("data") if isinstance(data.get("data"), dict) else None
        if nested:
            track_id = track_id or nested.get("track_id") or nested.get("trackId") or nested.get("trackid")
            pay_url = pay_url or nested.get("payment_url") or nested.get("paymentUrl") or nested.get("url")
        if not track_id and isinstance(data.get("data"), list) and len(data.get("data")) > 0:
            el = data.get("data")[0]
            if isinstance(el, dict):
                track_id = el.get("track_id") or el.get("trackId") or el.get("trackid")
                pay_url = el.get("payment_url") or el.get("paymentUrl") or el.get("url")

    if not track_id or not pay_url:
        logger.error(f"Payment gateway returned unexpected response: {resp}")
        return await event.respond("Payment gateway error.")

    session["track_id"] = track_id
    session["plan_key"] = plan_key
    
    prod_display = "Code Claimer" if product_type == "claimer" else "Chat Farmer"

    text = (
        f"✅ Product: <b>{prod_display}</b>\n"
        f"✅ Plan: <b>{label}</b>\n"
        f"Amount: <b>{amount} USDT</b>\n\n"
        "Click <b>Pay</b> to open OxaPay.\n"
        "Payment window: 15 minutes."
    )
    buttons = [
        [Button.url("🔗 Pay", pay_url)],
        [
            Button.url("🛠 Support", SUPPORT_CHAT_LINK),
            Button.url("📢 Updates", UPDATES_CHANNEL_LINK),
        ],
    ]
    try:
        await event.edit(text, parse_mode="html", buttons=buttons)
    except:
        await event.respond(text, parse_mode="html", buttons=buttons)

    # Pass product_type to wait_for_payment
    task = asyncio.create_task(wait_for_payment(user_id, track_id, plan_key, product_type))
    user_tasks[user_id] = task

# ================== BROADCAST ==================

@bot.on(events.NewMessage(pattern=r"^/broadcast$"))
async def broadcast_handler(event):
    if event.sender_id != BOT_OWNER_ID:
        return await event.reply("❌ Unauthorized.")

    if not event.is_reply:
        return await event.reply("Reply to a message with /broadcast.")

    orig = await event.get_reply_message()
    if not orig:
        return await event.reply("Message not found.")

    total = 0
    cursor = users_col.find({}, {"user_id": 1})

    for u in cursor:
        uid = u.get("user_id")
        if not uid:
            continue

        try:
            fwd = await bot.forward_messages(uid, orig.id, from_peer=event.chat_id)
            if isinstance(fwd, list):
                fwd = fwd[0]

            try:
                await bot.pin_message(uid, fwd.id, notify=True)
            except:
                pass

            total += 1
        except Exception as e:
            logger.error(f"Broadcast fail to {uid}: {e}")

    await event.reply(f"✅ Broadcast sent to {total} users.")

# ================== MAIN ==================

def main():
    logger.info("Stake Payment Bot (Farmer + Claimer) is running...")
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(check_active_users_loop())
    except Exception as e:
        logger.exception(f"Failed to schedule active users checker: {e}")

    bot.run_until_disconnected()

if __name__ == "__main__":
    main()
