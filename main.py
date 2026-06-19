import asyncio
import logging
import time
import requests
import json
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient, events, Button, functions, types
from pymongo import MongoClient

# ================== CONFIG ==================
API_ID = 29568441
API_HASH = "b32ec0fb66d22da6f77d355fbace4f2a"
BOT_TOKEN = "8302453295:AAFLJEUx-JAa75jbtIDLw-JelzKOPWhPs-8"
SUPPORT_CHAT_LINK = "https://t.me/KustXoffical"
UPDATES_CHANNEL_LINK = "https://t.me/kustbots"
UPI_DM_LINK = "https://t.me/KustXoffical"

# --- Chat Farmer Assets ---
FARMER_API_URL = "https://free-gwendolyn-frozenbots-28495340.koyeb.app"
# Forwards for Farmer
FARMER_FORWARD_1 = ("kustvault", 2)
FARMER_FORWARD_2 = ("kustvault", 3)
FARMER_FORWARD_3 = ("kustvault", 4)

# Start image
START_IMAGE_URL = "https://filehosting.kustbotsweb.workers.dev/f/3e5a6eb1e2444c14bc87a40b4b6a9973"

# MongoDB
MONGO_URL = "mongodb+srv://kustbotsweb_db_user:z7YqNFmFOvVHKl4B@kust-payments.hiin3lu.mongodb.net/?appName=kust-payments"
mongo = MongoClient(MONGO_URL)
db = mongo["kustchatbot"]
users_col = db["users"]

# Bot owner
BOT_OWNER_ID = 7618467489

# OxaPay API
OXAPAY_API_KEY = "EZJYLC-3A6TFB-RFFXOR-WTTDY3"
OXAPAY_API_BASE = "https://api.oxapay.com"

# Active users checker settings
ACTIVE_USERS_POLL_INTERVAL = 60   # seconds between polls (1 minute)
REMINDER_THRESHOLD_MINUTES = 10   # notify when <= 10 minutes remain

# --- PRICING PLANS (50% OFF) ---
# Plans (Shared pricing for 2d+)
PLANS_LONG_TERM = {
    "2d":  {"label": "2 Days",      "amount": 2.25,  "hours": 48},
    "4d":  {"label": "4 Days",      "amount": 4.0,   "hours": 96},
    "7d":  {"label": "7 Days",      "amount": 4.75,  "hours": 168},
}

# 1 Day definitions
PLAN_1D_FARMER  = {"label": "1 Day",    "amount": 1.0, "hours": 24}

# Plans (Exclusive to Chat Farmer Short Term)
PLANS_FARMER_SHORT = {
    "3h":  {"label": "3 Hours",     "amount": 0.25,  "hours": 3},
    "6h":  {"label": "6 Hours",     "amount": 0.45,  "hours": 6},
    "12h": {"label": "12 Hours",    "amount": 0.75,  "hours": 12},
}

# --- BULK POINTS PACKAGES ---
BULK_POINTS_PACKAGES = {
    "5":   {"label": "5 Points",    "amount": 5.0,  "points": 5},
    "10":  {"label": "10 Points",   "amount": 9.5,  "points": 10},   # 5% bonus
    "25":  {"label": "25 Points",   "amount": 22.5, "points": 25},   # 10% bonus
    "50":  {"label": "50 Points",   "amount": 42.5, "points": 50},   # 15% bonus
    "100": {"label": "100 Points",  "amount": 80.0, "points": 100},  # 20% bonus
}

# --- REFUND CONFIGURATION ---
REFUND_RATE_FARMER_PER_HOUR = 0.04  # Reduced 50% to match new pricing

PAYMENT_TIMEOUT = 15 * 60
POLL_INTERVAL = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("stake_payment_bot")

bot = TelegramClient("stake_farmer_payment_session", API_ID, API_HASH).start(bot_token=BOT_TOKEN)

user_sessions = {}
user_tasks = {}
bot_username = None # Will be set on startup

# keep track of reminders sent to avoid duplicates: { key: True }
_reminder_sent = {}


# ================== OXAPAY HELPERS ==================
def create_invoice(amount: float, currency: str = "USDT", lifetime: int = 60):
    url = f"{OXAPAY_API_BASE}/v1/payment/invoice"
    headers = {"merchant_api_key": OXAPAY_API_KEY, "Content-Type": "application/json"}
    body = {"amount": amount, "currency": currency, "lifetime": lifetime}
    r = requests.post(url, headers=headers, json=body, timeout=15)
    r.raise_for_status()
    return r.json()

def query_invoice(track_id: str):
    url = f"{OXAPAY_API_BASE}/merchants/inquiry"
    headers = {"Content-Type": "application/json"}
    body = {"merchant": OXAPAY_API_KEY, "trackId": track_id}
    r = requests.post(url, headers=headers, json=body, timeout=15)
    r.raise_for_status()
    return r.json()

def activate_subscription(username_with_at: str, hours: int, api_url: str):
    """
    Synchronous activation call to the specific product API.
    """
    try:
        params = {
            "user": username_with_at,
            "admin": "admin1234",
            "duration": hours
        }
        url = f"{api_url}/auth" 
        r = requests.get(url, params=params, timeout=120)
        r.raise_for_status()
        logger.info(f"[ACTIVATE] Activated for {username_with_at} on {api_url}. Response: {r.text}")
        return True
    except Exception as e:
        logger.exception(f"[ACTIVATE] Failed activation API for {username_with_at} on {api_url}: {e}")
        return False

def extract_status_from_query_response(resp_json):
    if not resp_json:
        return None
    status = None
    if isinstance(resp_json, dict):
        status = resp_json.get("status")
        if status:
            return str(status).lower()
        data = resp_json.get("data") or resp_json.get("result") or resp_json.get("response")
        if isinstance(data, dict):
            s = data.get("status") or data.get("payment_status") or data.get("state")
            if s:
                return str(s).lower()
        if isinstance(resp_json.get("data"), list) and len(resp_json.get("data")) > 0:
            el = resp_json.get("data")[0]
            if isinstance(el, dict):
                s = el.get("status")
                if s:
                    return str(s).lower()
    return None

async def wait_for_payment(user_id: int, track_id: str, plan_label: str, hours: int, plan_amount: float, is_bulk_points: bool = False, points_amount: int = 0):
    session = user_sessions.get(user_id)
    if not session:
        logger.warning(f"wait_for_payment: no session for {user_id}")
        return False

    product_type = session.get("product", "farmer")
    
    # Handle bulk points purchase
    if is_bulk_points:
        product_name = "Points Purchase"
        forwards = []
        api_url = None
    else:
        # Configure variables for farmer
        api_url = FARMER_API_URL
        product_name = "Chat Farmer"
        forwards = [FARMER_FORWARD_1, FARMER_FORWARD_2, FARMER_FORWARD_3]

    start = time.time()
    while time.time() - start < PAYMENT_TIMEOUT:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            data = await asyncio.to_thread(query_invoice, track_id)
            status = extract_status_from_query_response(data) or ""
            status = status.lower()

            logger.info(f"Invoice {track_id} status check: {status}")

            if status == "paid":
                
                # === BULK POINTS PURCHASE ===
                if is_bulk_points:
                    users_col.update_one(
                        {"user_id": user_id},
                        {"$inc": {"points": points_amount}},
                        upsert=True
                    )
                    
                    user_record = users_col.find_one({"user_id": user_id})
                    new_balance = user_record.get("points", 0.0) if user_record else points_amount
                    
                    await bot.send_message(
                        user_id,
                        f"✅ <b>Payment Confirmed!</b>\n\n"
                        f"Points Purchased: <b>{points_amount}</b>\n"
                        f"New Balance: <b>{new_balance:.2f} Points</b>\n\n"
                        f"Use your points to purchase subscriptions!",
                        parse_mode="html"
                    )
                    return True
                
                username_clean = session.get("username", "UNKNOWN")

                activation_ok = await asyncio.to_thread(activate_subscription, f"@{username_clean}", hours, api_url)

                # Persist username to DB if not present
                user_record = users_col.find_one({"user_id": user_id})
                if user_record:
                    users_col.update_one({"user_id": user_id}, {"$set": {"username": username_clean}})
                else:
                    users_col.insert_one({"user_id": user_id, "username": username_clean, "points": 0.0})
                    user_record = {"user_id": user_id}

                # === REFERRAL REWARD SYSTEM ===
                referrer_id = user_record.get("referrer_id")
                if referrer_id:
                    try:
                        reward_points = plan_amount * 0.10
                        users_col.update_one(
                            {"user_id": referrer_id},
                            {"$inc": {"points": reward_points}}
                        )
                        try:
                            await bot.send_message(
                                referrer_id,
                                f"<tg-emoji emoji-id='5208541126583136130'>🎉</tg-emoji> <b>Referral Bonus!</b>\n\n"
                                f"Your referred user just purchased a plan.\n"
                                f"You earned <b>{reward_points:.2f} points</b> (USDT value)."
                                , parse_mode="html"
                            )
                        except Exception as e:
                            logger.error(f"Failed to notify referrer {referrer_id}: {e}")
                    except Exception as e:
                        logger.error(f"Error processing referral reward: {e}")

                # Notify user
                await bot.send_message(
                    user_id,
                    f"✅ Payment confirmed!\n\n"
                    f"Your <b>{product_name} - {plan_label}</b> subscription is activated.\n"
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
                await bot.send_message(user_id, f"❌ Invoice for {product_name} ({plan_label}) expired or cancelled.", parse_mode="html")
                return False

        except Exception as e:
            logger.exception(f"Invoice query error for track {track_id}: {e}")

    await bot.send_message(user_id, "⏳ Payment not confirmed. Create a new invoice.", parse_mode="html")
    return False

# ================== API MANAGEMENT HELPERS ==================
def get_active_users(api_url):
    try:
        r = requests.get(f"{api_url}/active_users", timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Failed to fetch active users from {api_url}: {e}")
        return None

def rename_user_api(old_username: str, new_username: str):
    if old_username and not old_username.startswith("@"):
        old_username = f"@{old_username}"
    if new_username and not new_username.startswith("@"):
        new_username = f"@{new_username}"
        
    data = {"old_username": old_username, "new_username": new_username, "admin": "admin1234"}
    
    endpoints = [
        f"{FARMER_API_URL}/rename_user"
    ]
    
    success = False
    details = ""
    for ep in endpoints:
        try:
            r = requests.post(ep, data=data, timeout=10)
            if r.status_code >= 200 and r.status_code < 300:
                success = True
            else:
                details += f"Fail {ep}: {r.status_code} "
        except Exception as e:
            details += f"Err {ep}: {str(e)} "
            
    if success:
        return {"ok": True}
    else:
        return {"ok": False, "error": details}

def delete_user_api(username: str, api_url: str):
    if username and not username.startswith("@"):
        username = f"@{username}"
        
    params = {"username": username, "admin": "admin1234"}
    
    try:
        r = requests.post(f"{api_url}/delete_user", params=params, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed delete_user POST on {api_url}: {e}")
        try:
            r = requests.get(f"{api_url}/delete_user", params=params, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e2: 
            logger.error(f"Failed delete_user GET on {api_url}: {e2}")
            
    return False

# ================== ACTIVE USERS CHECKER ==================
def _parse_iso_datetime(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    logger.warning(f"[PARSE_DT] Could not parse datetime string: {s!r}")
    return None

def _extract_active_users_list(data):
    if not data:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("active_users", "users", "data", "result", "results"):
            val = data.get(key)
            if isinstance(val, list):
                return val
    return []

async def check_active_users_loop():
    await asyncio.sleep(5)
    logger.info("Active users reminder loop started (60s interval, 10min reminder).")
    
    api_sources = [
        {"name": "Chat Farmer",  "url": FARMER_API_URL},
    ]

    while True:
        try:
            now = datetime.now(timezone.utc)

            for source in api_sources:
                api_name = source["name"]
                api_url  = source["url"]

                try:
                    data = await asyncio.to_thread(get_active_users, api_url)
                except Exception as e:
                    logger.error(f"[REMINDER] get_active_users failed for {api_name}: {e}")
                    continue

                if not data:
                    continue

                users = _extract_active_users_list(data)
                if not users:
                    continue

                for entry in users:
                    try:
                        expires_raw = entry.get("expires") or entry.get("expiry") or entry.get("expire_at") or entry.get("expires_at")
                        username    = entry.get("username") or entry.get("user") or entry.get("name")

                        if not expires_raw or not username:
                            continue

                        expires_dt = _parse_iso_datetime(str(expires_raw))
                        if not expires_dt:
                            continue

                        if expires_dt.tzinfo is None:
                            expires_dt = expires_dt.replace(tzinfo=timezone.utc)

                        time_left    = expires_dt - now
                        seconds_left = time_left.total_seconds()
                        minutes_left = seconds_left / 60.0

                        username_clean = username.lstrip("@").strip()

                        rec     = users_col.find_one({"username": username_clean})
                        user_id = rec.get("user_id") if rec else None

                        if 0 < seconds_left <= (REMINDER_THRESHOLD_MINUTES * 60):
                            reminder_key = (
                                f"reminder_{username_clean.lower()}"
                                f"_{api_name}"
                                f"_{expires_dt.strftime('%Y%m%d%H%M')}"
                            )

                            if not _reminder_sent.get(reminder_key):
                                if user_id:
                                    try:
                                        rem_text = (
                                            f"⏳ <b>Subscription ending soon!</b>\n\n"
                                            f"Product: <b>{api_name}</b>\n"
                                            f"User: <code>@{username_clean}</code>\n"
                                            f"Expires: {expires_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                                            f"Time left: ~{int(minutes_left)} min(s).\n\n"
                                            f"Renew now to avoid any interruption!"
                                        )
                                        buttons = [
                                            [Button.inline("💳 Renew Now", b"buy_sub")],
                                            [Button.url("🛠 Support", SUPPORT_CHAT_LINK)],
                                        ]
                                        await bot.send_message(
                                            user_id,
                                            rem_text,
                                            parse_mode="html",
                                            buttons=buttons
                                        )
                                    except Exception as e:
                                        logger.exception(f"[REMINDER] ❌ Failed to send DM to {username_clean}: {e}")

                                _reminder_sent[reminder_key] = True

                    except Exception as ee:
                        logger.exception(f"[REMINDER] Error processing active user entry: {ee}")

        except Exception as e:
            logger.exception(f"check_active_users_loop top-level error: {e}")

        await asyncio.sleep(ACTIVE_USERS_POLL_INTERVAL)


# ================== ADD POINTS COMMAND ==================
@bot.on(events.NewMessage(pattern=r"^/add\s"))
async def add_points_handler(event):
    if event.sender_id != BOT_OWNER_ID:
        return

    args = event.message.message.split()
    if len(args) != 3:
        return await event.reply("![❌](tg://emoji?id=5273914604752216432) Usage: `/add <@username/userid> <amount>`", parse_mode="markdown")

    target_arg = args[1]
    amount_arg = args[2]

    try:
        amount = float(amount_arg)
    except ValueError:
        return await event.reply("![❌](tg://emoji?id=5273914604752216432) Invalid amount. Please enter a number.", parse_mode="markdown")

    target_user_id = None
    user_record = None

    if target_arg.isdigit():
        target_user_id = int(target_arg)
        user_record = users_col.find_one({"user_id": target_user_id})
    else:
        clean_username = target_arg.lstrip("@")
        user_record = users_col.find_one({"username": clean_username})
        if user_record:
            target_user_id = user_record.get("user_id")

    if not user_record or not target_user_id:
        return await event.reply(f"![❌](tg://emoji?id=5273914604752216432) User `{target_arg}` not found in the database.", parse_mode="markdown")

    try:
        users_col.update_one({"user_id": target_user_id}, {"$inc": {"points": amount}})
        
        updated_user = users_col.find_one({"user_id": target_user_id})
        new_balance = updated_user.get("points", 0.0)

        await event.reply(
            f"![✅](tg://emoji?id=5039793437776282663) **Success!**\n\n"
            f"User: `{target_arg}`\n"
            f"Added: `{amount}` points\n"
            f"New Balance: `{new_balance:.2f}`",
            parse_mode="markdown"
        )
        try:
            await bot.send_message(
                target_user_id,
                f"<tg-emoji emoji-id='5208541126583136130'>🎉</tg-emoji> <b>Balance Update!</b>\n\n"
                f"Admin has added <b>{amount} points</b> to your account.\n"
                f"<tg-emoji emoji-id='5375312095346704820'>💰</tg-emoji> Total Balance: <b>{new_balance:.2f} Points</b>",
                parse_mode="html"
            )
        except Exception as e:
            await event.reply(f"⚠️ Points added, but failed to DM user (User might have blocked bot): {e}")

    except Exception as e:
        logger.error(f"Error adding points: {e}")
        await event.reply(f"![❌](tg://emoji?id=5273914604752216432) Database error: {e}", parse_mode="markdown")

# ================== EXTEND TIME COMMAND (OWNER ONLY) ==================
@bot.on(events.NewMessage(pattern=r"^/extend\s"))
async def extend_time_handler(event):
    if event.sender_id != BOT_OWNER_ID:
        return

    args = event.message.message.split()
    
    if len(args) < 3:
        return await event.reply(
            "❌ <b>Usage:</b> <code>/extend &lt;username/userid&gt; &lt;hours&gt;</code>\n\n"
            "<b>Example:</b>\n"
            "<code>/extend @alice123 24</code>\n",
            parse_mode="html"
        )
        
    target_arg = args[1]
    hours_arg = args[2]
    
    try:
        hours_to_add = int(hours_arg)
        if hours_to_add <= 0:
            raise ValueError("Hours must be positive")
    except ValueError:
        return await event.reply("❌ Invalid hours. Please enter a positive number.", parse_mode="html")
        
    target_user_id = None
    target_username = None
    
    if target_arg.isdigit():
        target_user_id = int(target_arg)
        user_record = users_col.find_one({"user_id": target_user_id})
        if user_record:
            target_username = user_record.get("username")
        if not target_username:
            return await event.reply(f"❌ User ID `{target_user_id}` found but no username set in database. Please use username instead.", parse_mode="html")
    else:
        target_username = target_arg.lstrip("@")
        user_record = users_col.find_one({"username": target_username})
        if user_record:
            target_user_id = user_record.get("user_id")
            
    status_msg = await event.reply(f"🔄 Extending subscription for <code>@{target_username}</code>...", parse_mode="html")
    
    results = []
    product_name = "Chat Farmer"
    api_url = FARMER_API_URL
        
    try:
        success = await asyncio.to_thread(activate_subscription, f"@{target_username}", hours_to_add, api_url)
        
        if success:
            results.append(f"✅ <b>{product_name}</b>: Extended by {hours_to_add} hours")
        else:
            results.append(f"❌ <b>{product_name}</b>: Failed to extend")
            
    except Exception as e:
        logger.exception(f"Error extending {product_name} for {target_username}: {e}")
        results.append(f"❌ <b>{product_name}</b>: Error - {str(e)[:50]}")
            
    result_text = (
        f"⏰ <b>Subscription Extension Result</b>\n\n"
        f"User: <code>@{target_username}</code>\n"
        f"Hours Added: <b>{hours_to_add}</b>\n\n"
    )
    result_text += "\n".join(results)
    
    if target_user_id:
        try:
            api_data = await asyncio.to_thread(get_active_users, FARMER_API_URL)
            new_expiry_str = "N/A"
            if api_data and isinstance(api_data, dict):
                for u in _extract_active_users_list(api_data):
                    if u.get("username", "").lower().lstrip("@") == target_username.lower():
                        new_expiry_str = u.get("expires", "N/A")
                        break
                        
            await bot.send_message(
                target_user_id,
                f"🎉 <b>Subscription Extended!</b>\n\n"
                f"Admin has extended your subscription.\n"
                f"Hours Added: <b>{hours_to_add}</b>\n"
                f"New Expiry: <code>{new_expiry_str}</code>",
                parse_mode="html"
            )
            result_text += "\n\n✅ User notified successfully."
        except Exception as e:
            result_text += f"\n\n⚠️ Could not notify user: {str(e)[:50]}"
    else:
        result_text += "\n\n<i>User not in bot database - notification skipped.</i>"
        
    await status_msg.edit(result_text, parse_mode="html")


# ================== BULK POINTS PURCHASE COMMAND ==================
@bot.on(events.NewMessage(pattern=r"^/buypoints$"))
async def buypoints_handler(event):
    user_id = event.sender_id
    
    try:
        await bot(functions.messages.SendReactionRequest(
            peer=event.chat_id,
            msg_id=event.message.id,
            reaction=[types.ReactionEmoji(emoticon='💰')],
            add_to_recent=False
        ))
    except:
        pass
        
    user_data = users_col.find_one({"user_id": user_id})
    current_points = user_data.get("points", 0.0) if user_data else 0.0
    
    text = (
        f"💰 <b>Buy Points in Bulk</b>\n\n"
        f"Current Balance: <b>{current_points:.2f} Points</b>\n\n"
        f"<b>Available Packages:</b>\n"
        f"• 5 Points — 5.0 USDT\n"
        f"• 10 Points — 9.5 USDT (5% discount)\n"
        f"• 25 Points — 22.5 USDT (10% discount)\n"
        f"• 50 Points — 42.5 USDT (15% discount)\n"
        f"• 100 Points — 80.0 USDT (20% discount)\n\n"
        f"<i>1 Point = 1 USDT value</i>\n\n"
        f"Select a package:"
    )
    
    buttons = [
        [
            Button.inline("5 Pts — 5 $", b"bulk_5"),
            Button.inline("10 Pts — 9.5 $", b"bulk_10"),
        ],
        [
            Button.inline("25 Pts — 22.5 $", b"bulk_25"),
            Button.inline("50 Pts — 42.5 $", b"bulk_50"),
        ],
        [
            Button.inline("100 Pts — 80 $", b"bulk_100"),
        ],
        [Button.inline("🔙 Main Menu", b"back_to_start")]
    ]
    
    try:
        await event.respond(text, parse_mode="html", buttons=buttons)
    except:
        await event.reply(text, parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(pattern=b"bulk_"))
async def bulk_points_handler(event):
    await event.answer()
    user_id = event.sender_id
    
    data_str = event.data.decode()
    package_key = data_str.replace("bulk_", "")
    
    package = BULK_POINTS_PACKAGES.get(package_key)
    if not package:
        return await event.respond("Invalid package. Try again.")
        
    amount = package["amount"]
    points = package["points"]
    label = package["label"]
    
    session = user_sessions.setdefault(user_id, {})
    session["bulk_points"] = points
    session["bulk_amount"] = amount
    session["bulk_label"] = label
    session["product"] = "bulk_points"
    
    user_data = users_col.find_one({"user_id": user_id})
    current_points = user_data.get("points", 0.0) if user_data else 0.0
    
    text = (
        f"💰 <b>Bulk Points Purchase</b>\n\n"
        f"Package: <b>{label}</b>\n"
        f"Cost: <b>{amount} USDT</b>\n"
        f"Points to receive: <b>{points}</b>\n\n"
        f"Current Balance: <b>{current_points:.2f} Points</b>\n"
        f"After Purchase: <b>{current_points + points:.2f} Points</b>\n\n"
        f"Proceed to payment?"
    )
    
    buttons = [
        [Button.inline(f"💳 Pay {amount} USDT", b"bulk_pay_crypto")],
        [Button.inline("🔙 Back to Packages", b"back_buypoints")]
    ]
    
    try:
        await event.edit(text, parse_mode="html", buttons=buttons)
    except:
        await event.respond(text, parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(data=b"menu_buypoints"))
@bot.on(events.CallbackQuery(data=b"back_buypoints"))
async def back_buypoints_handler(event):
    await event.answer()
    user_id = event.sender_id
    
    user_data = users_col.find_one({"user_id": user_id})
    current_points = user_data.get("points", 0.0) if user_data else 0.0
    
    text = (
        f"💰 <b>Buy Points in Bulk</b>\n\n"
        f"Current Balance: <b>{current_points:.2f} Points</b>\n\n"
        f"<b>Available Packages:</b>\n"
        f"• 5 Points — 5.0 USDT\n"
        f"• 10 Points — 9.5 USDT (5% discount)\n"
        f"• 25 Points — 22.5 USDT (10% discount)\n"
        f"• 50 Points — 42.5 USDT (15% discount)\n"
        f"• 100 Points — 80.0 USDT (20% discount)\n\n"
        f"<i>1 Point = 1 USDT value</i>\n\n"
        f"Select a package:"
    )
    
    buttons = [
        [
            Button.inline("5 Pts — 5 $", b"bulk_5"),
            Button.inline("10 Pts — 9.5 $", b"bulk_10"),
        ],
        [
            Button.inline("25 Pts — 22.5 $", b"bulk_25"),
            Button.inline("50 Pts — 42.5 $", b"bulk_50"),
        ],
        [
            Button.inline("100 Pts — 80 $", b"bulk_100"),
        ],
        [Button.inline("🔙 Main Menu", b"back_to_start")]
    ]
    
    try:
        await event.edit(text, parse_mode="html", buttons=buttons)
    except:
        await event.respond(text, parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(data=b"bulk_pay_crypto"))
async def bulk_pay_crypto_handler(event):
    await event.answer()
    user_id = event.sender_id
    session = user_sessions.get(user_id)
    
    if not session or "bulk_amount" not in session:
        return await event.respond("Session expired. Please use /buypoints again.")
        
    amount = session["bulk_amount"]
    points = session["bulk_points"]
    label = session["bulk_label"]
    
    await event.edit("🔄 Creating invoice...", parse_mode="html")
    
    try:
        resp = await asyncio.to_thread(create_invoice, amount)
    except Exception as e:
        logger.exception(f"Invoice error: {e}")
        return await event.edit("Failed to create invoice.")
        
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
        return await event.edit("Payment gateway error.")
        
    session["track_id"] = track_id
    
    text = (
        f"✅ <b>Bulk Points Purchase</b>\n"
        f"Package: <b>{label}</b>\n"
        f"Amount: <b>{amount} USDT</b>\n"
        f"Points: <b>{points}</b>\n\n"
        f"Click <b>Pay</b> to open OxaPay.\n"
        f"Payment window: 15 minutes."
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
        
    task = asyncio.create_task(
        wait_for_payment(
            user_id, 
            track_id, 
            label, 
            0,
            amount, 
            is_bulk_points=True, 
            points_amount=points
        )
    )
    user_tasks[user_id] = task

# ================== START / MENU HANDLERS ==================
@bot.on(events.NewMessage(pattern=r"^/start"))
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
    
    args = event.message.message.split()
    referrer_id = None
    if len(args) > 1:
        try:
            possible_referrer = int(args[1])
            if possible_referrer != user_id:
                referrer_id = possible_referrer
        except ValueError:
            pass

    if first_time:
        new_user_doc = {
            "user_id": user_id,
            "first_seen": datetime.now(timezone.utc),
            "points": 0.0
        }
        
        if referrer_id:
            ref_user = users_col.find_one({"user_id": referrer_id})
            if ref_user:
                new_user_doc["referrer_id"] = referrer_id
                try:
                    await bot.send_message(
                        referrer_id, 
                        f"<tg-emoji emoji-id='5208541126583136130'>🎉</tg-emoji> <b>New Referral!</b>\n\n"
                        f"A new user joined via your link.\n"
                        f"You will earn <b>10%</b> in points when they make a purchase.",
                        parse_mode="html"
                    )
                except Exception as e:
                    logger.error(f"Failed to notify referrer {referrer_id}: {e}")
                    
        users_col.insert_one(new_user_doc)
    else:
        users_col.update_one(
            {"user_id": user_id},
            {"$set": {"last_seen": datetime.now(timezone.utc)}}
        )

    caption_text = (
        "<b><tg-emoji emoji-id='5445284980978621387'>🚀</tg-emoji> Kust Bots — Premium Tools</b>\n\n"
        "<b>Available Products:</b>\n"
        "• Chat Farmer (Automated chat farming)\n\n"
        "Select a product to purchase or manage your account."
    )

    buttons = []
    buttons.append([Button.inline("👨‍🌾 Buy Chat Farmer", b"buy_product_farmer")])

    if existing:
        buttons.append([Button.inline("⚙️ Manage Subscriptions", b"manage_subs_menu")])

    buttons.append([
        Button.inline("🎁 Refer & Earn", b"menu_referral"),
        Button.inline("💰 Buy Points", b"menu_buypoints")
    ])

    buttons.append([
        Button.url("🛠 Support", SUPPORT_CHAT_LINK),
        Button.url("📢 Updates", UPDATES_CHANNEL_LINK),
    ])

    user_sessions.setdefault(user_id, {"expecting_username": False})

    try:
        await bot.send_file(user_id, START_IMAGE_URL, caption=caption_text, parse_mode="html", buttons=buttons)
    except Exception as e:
        logger.error(f"send_file failed: {e}")
        await event.respond(caption_text, parse_mode="html", buttons=buttons)

@bot.on(events.NewMessage(pattern=r"^/(help|support)$"))
async def help_handler(event):
    await event.respond(
        "For issues, join support chat:",
        buttons=[[Button.url("🛠 Support Chat", SUPPORT_CHAT_LINK)]]
    )

# --- MANAGE SUBSCRIPTIONS & PASSWORD HANDLERS ---
@bot.on(events.CallbackQuery(data=b"manage_subs_menu"))
async def manage_subs_menu_handler(event):
    await event.answer()
    user_id = event.sender_id
    await show_management_menu(event, user_id)

async def show_management_menu(event_or_msg, user_id):
    text = "⚙️ **Manage Subscriptions**\n\nSelect an option below:"
    buttons = [
        [Button.inline("✏️ Edit Username", b"edit_username")],
        [Button.inline("🗑 Terminate Sub", b"terminate_sub_menu")],
        [Button.inline("🔙 Main Menu", b"back_to_start")]
    ]
    if hasattr(event_or_msg, 'edit'):
        await event_or_msg.edit(text, parse_mode="markdown", buttons=buttons)
    else:
        await bot.send_message(user_id, text, parse_mode="markdown", buttons=buttons)

# --- REFERRAL MENU HANDLER ---
@bot.on(events.CallbackQuery(data=b"menu_referral"))
async def referral_menu_handler(event):
    await event.answer()
    user_id = event.sender_id
    
    user_data = users_col.find_one({"user_id": user_id})
    points = user_data.get("points", 0.0) if user_data else 0.0
    
    try:
        ref_count = users_col.count_documents({"referrer_id": user_id})
    except:
        ref_count = 0
        
    global bot_username
    if not bot_username: 
        me = await bot.get_me() 
        globals()['bot_username'] = me.username 
        
    ref_link = f"https://t.me/{bot_username}?start={user_id}"

    text = (
        "<b><tg-emoji emoji-id='5411271889421086677'>🎁</tg-emoji> Refer & Earn Program</b>\n\n"
        "Invite friends and earn <b>10%</b> of their spendings as points!\n"
        "1 Point = 1 USDT value.\n\n"
        f"<tg-emoji emoji-id='5375312095346704820'>💰</tg-emoji> <b>Your Balance:</b> {points:.2f} Points\n"
        f"<tg-emoji emoji-id='5453957997418004470'>👥</tg-emoji> <b>Total Referrals:</b> {ref_count}\n\n"
        "<b><tg-emoji emoji-id='5442744585132464157'>👇</tg-emoji> Your Referral Link:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        "Share this link. You get notified instantly when someone joins."
    )
    
    buttons = [[Button.inline("🔙 Back", b"back_to_start")]]
    
    try:
        await event.edit(text, parse_mode="html", buttons=buttons)
    except:
        await event.respond(text, parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(data=b"back_to_start"))
async def back_start_handler(event):
    await event.answer()
    user_id = event.sender_id

    try:
        existing = users_col.find_one({"user_id": user_id})
    except:
        existing = None

    buttons = []
    buttons.append([Button.inline("👨‍🌾 Buy Chat Farmer", b"buy_product_farmer")])

    if existing:
        buttons.append([Button.inline("⚙️ Manage Subscriptions", b"manage_subs_menu")])

    buttons.append([
        Button.inline("🎁 Refer & Earn", b"menu_referral"),
        Button.inline("💰 Buy Points", b"menu_buypoints")
    ])

    buttons.append([
        Button.url("🛠 Support", SUPPORT_CHAT_LINK),
        Button.url("📢 Updates", UPDATES_CHANNEL_LINK),
    ])

    caption_text = (
        "<b><tg-emoji emoji-id='5445284980978621387'>🚀</tg-emoji> Kust Bots — Premium Tools</b>\n\n"
        "<b>Available Products:</b>\n"
        "• Chat Farmer (Automated chat farming)\n\n"
        "Select a product to purchase or manage your account."
    )
    
    try:
        await event.delete() 
    except:
        pass
    try:
        await bot.send_file(user_id, START_IMAGE_URL, caption=caption_text, parse_mode="html", buttons=buttons)
    except Exception as e:
        await bot.send_message(user_id, caption_text, parse_mode="html", buttons=buttons)

# --- PRODUCT SELECTION HANDLERS ---
@bot.on(events.CallbackQuery(data=b"buy_sub"))
async def buy_sub_menu_handler(event):
    await event.answer()
    buttons = [
        [Button.inline("👨‍🌾 Buy Chat Farmer", b"buy_product_farmer")],
    ]
    await event.edit("<b>Select Product to Renew:</b>", parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(pattern=b"buy_product_"))
async def buy_product_handler(event):
    await event.answer()
    user_id = event.sender_id
    
    product_type = "farmer"
    product_display = "Chat Farmer"
        
    session = user_sessions.setdefault(user_id, {})
    session["expecting_username"] = True
    session["product"] = product_type

    text = (
        f"<b>Buy {product_display} — Step 1: Provide your Stake username</b>\n\n"
        "Send only the username. Examples:\n"
        "• <code>alice123</code>\n"
        "• <code>@alice123</code>\n\n"
        "Do NOT send profile links or screenshots. After you send the username you'll be asked to confirm it."
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
    
    session.pop("expecting_username", None)
    
    session["expecting_rename_old"] = True
    session["expecting_rename_new"] = False
    
    text = (
        "<b><tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Edit Username — Step 1</b>\n\n"
        "Please enter the <b>OLD</b> Stake username (the one you want to replace).\n\n"
        "Example: <code>alice123</code>"
    )
    buttons = [[Button.inline("❌ Cancel", b"manage_subs_menu")]]
    try:
        await event.edit(text, parse_mode="html", buttons=buttons)
    except:
        await event.respond(text, parse_mode="html", buttons=buttons)

# ================== TERMINATE SUBSCRIPTION HANDLER ==================
@bot.on(events.CallbackQuery(data=b"terminate_sub_menu"))
async def terminate_sub_menu_handler(event):
    await event.answer()
    user_id = event.sender_id
    
    user_doc = users_col.find_one({"user_id": user_id})
    if not user_doc or not user_doc.get("username"):
        await event.edit(
            "❌ No username linked to your account. Cannot terminate.",
            buttons=[[Button.inline("🔙 Back", b"manage_subs_menu")]]
        )
        return

    username = user_doc.get("username").lstrip("@")
    
    active_details = None
    
    data_farmer = await asyncio.to_thread(get_active_users, FARMER_API_URL)
    if data_farmer:
        users = _extract_active_users_list(data_farmer)
        for u in users:
            if u.get("username", "").lower().lstrip("@") == username.lower():
                expires = _parse_iso_datetime(str(u.get("expires", "")))
                if expires:
                    active_details = (FARMER_API_URL, expires, "Chat Farmer")
                    break

    if not active_details:
        session = user_sessions.setdefault(user_id, {})
        session["expecting_term_username"] = True
        
        text = (
            f"User: <code>@{username}</code>\n\n"
            "❌ <b>No active subscription found for this username.</b>\n\n"
            "If you have a subscription under an <b>OLD username</b>, please <b>send it now</b> to terminate it and get a refund.\n\n"
            "Or click below to just remove the current username from the database."
        )
        buttons = [
            [Button.inline("🗑 Remove Username Only", b"terminate_sub_force_db")],
            [Button.inline("🔙 Back", b"manage_subs_menu")]
        ]
        await event.edit(text, parse_mode="html", buttons=buttons)
        return

    api_url, expires_dt, product_name = active_details

    now = datetime.now(timezone.utc)
    if expires_dt.tzinfo is None:
        expires_dt = expires_dt.replace(tzinfo=timezone.utc)
        
    if expires_dt > now:
        remaining_secs = (expires_dt - now).total_seconds()
        remaining_hours = remaining_secs / 3600.0
    else:
        remaining_hours = 0

    refund_amount = 0.0
    if remaining_hours > 0:
        refund_amount = remaining_hours * REFUND_RATE_FARMER_PER_HOUR
            
    refund_amount = round(refund_amount, 2)

    session = user_sessions.setdefault(user_id, {})
    session["term_username"] = username
    session["term_api"] = api_url
    session["term_refund"] = refund_amount
    session["term_product"] = product_name

    text = (
        f"<b>🗑 Terminate Subscription</b>\n\n"
        f"Product: <b>{product_name}</b>\n"
        f"User: <code>@{username}</code>\n"
        f"Time Left: <b>{remaining_hours:.1f} Hours</b>\n\n"
        f"<b>Refund Estimate:</b> {refund_amount} Points\n"
        "<i>(Based on remaining time)</i>\n\n"
        "Are you sure? This will instantly stop the bot and remove your username."
    )
    
    buttons = [
        [Button.inline(f"✅ Yes, Refund {refund_amount} Pts", b"terminate_sub_execute")],
        [Button.inline("❌ Cancel", b"manage_subs_menu")]
    ]
    await event.edit(text, parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(data=b"terminate_sub_force_db"))
async def terminate_force_db_handler(event):
    await event.answer()
    user_id = event.sender_id
    
    users_col.update_one({"user_id": user_id}, {"$unset": {"username": ""}})
    
    await event.edit("✅ Username removed from database.", buttons=[[Button.inline("🔙 Manage Menu", b"manage_subs_menu")]])

@bot.on(events.CallbackQuery(data=b"terminate_sub_execute"))
async def terminate_execute_handler(event):
    await event.answer()
    user_id = event.sender_id
    session = user_sessions.get(user_id, {})
    
    username = session.get("term_username")
    api_url = session.get("term_api")
    refund = session.get("term_refund", 0.0)
    product_name = session.get("term_product", "Unknown")
    
    if not username or not api_url:
        await event.edit("Session expired. Please try again.", buttons=[[Button.inline("🔙 Manage Menu", b"manage_subs_menu")]])
        return
        
    await event.edit("⏳ Terminating subscription on all services...", parse_mode="html")

    # ===== CALL PRODUCT APIs TO DELETE USER =====
    all_api_urls = [FARMER_API_URL]
    
    seen_urls = set()
    unique_api_urls = []
    for u in all_api_urls:
        if u not in seen_urls:
            seen_urls.add(u)
            unique_api_urls.append(u)

    any_success = False
    for del_url in unique_api_urls:
        ok = await asyncio.to_thread(delete_user_api, username, del_url)
        if ok:
            any_success = True
        logger.info(f"[TERMINATE] delete_user_api on {del_url} for @{username}: {'ok' if ok else 'failed'}")

    # ===== UPDATE DB: REFUND POINTS AND REMOVE USERNAME =====
    user_doc = users_col.find_one({"user_id": user_id})
    current_db_user = user_doc.get("username", "").lstrip("@") if user_doc else ""
    
    update_query = {"$inc": {"points": refund}}
    if current_db_user.lower() == username.lower().lstrip("@"):
        update_query["$unset"] = {"username": ""}
        
    users_col.update_one({"user_id": user_id}, update_query)

    # ===== BUILD RESULT MESSAGE =====
    if any_success:
        await event.edit(
            f"✅ <b>Subscription Terminated</b>\n\n"
            f"User <code>@{username}</code> removed from all services.\n"
            f"Refunded: <b>{refund} Points</b>.",
            parse_mode="html",
            buttons=[[Button.inline("🔙 Manage Menu", b"manage_subs_menu")]]
        )
    else:
        await event.edit(
            "❌ Failed to delete user from servers. Please contact support.",
            buttons=[[Button.url("🛠 Support", SUPPORT_CHAT_LINK)]]
        )
        
    session.pop("term_username", None)
    session.pop("term_api", None)
    session.pop("term_refund", None)
    session.pop("term_product", None)

# ================== TEXT INPUT HANDLER ==================
@bot.on(events.NewMessage)
async def text_input_handler(event):
    user_id = event.sender_id
    session = user_sessions.setdefault(user_id, {})
    
    raw_text = event.raw_text.strip()
    if raw_text.startswith("/"):
        return

    import re
    username_pattern = r"^[A-Za-z0-9_@]{3,51}$"
    if not re.match(username_pattern, raw_text):
        return

    username_clean = raw_text.lstrip('@')

    # --- TERMINATE OLD USERNAME FLOW ---
    if session.get("expecting_term_username"):
        session["expecting_term_username"] = False
        target_username = username_clean
        
        active_details = None
        
        data_farmer = await asyncio.to_thread(get_active_users, FARMER_API_URL)
        if data_farmer:
            users = _extract_active_users_list(data_farmer)
            for u in users:
                if u.get("username", "").lower().lstrip("@") == target_username.lower():
                    expires = _parse_iso_datetime(str(u.get("expires", "")))
                    if expires:
                        active_details = (FARMER_API_URL, expires, "Chat Farmer")
                        break
                            
        if not active_details:
            await event.respond(
                f"❌ No active subscription found for <code>@{target_username}</code> either.",
                parse_mode="html",
                buttons=[[Button.inline("🔙 Manage Menu", b"manage_subs_menu")]]
            )
            return

        api_url, expires_dt, product_name = active_details

        now = datetime.now(timezone.utc)
        if expires_dt.tzinfo is None:
            expires_dt = expires_dt.replace(tzinfo=timezone.utc)
            
        if expires_dt > now:
            remaining_secs = (expires_dt - now).total_seconds()
            remaining_hours = remaining_secs / 3600.0
        else:
            remaining_hours = 0

        refund_amount = 0.0
        if remaining_hours > 0:
            refund_amount = remaining_hours * REFUND_RATE_FARMER_PER_HOUR
                
        refund_amount = round(refund_amount, 2)
        
        session["term_username"] = target_username
        session["term_api"] = api_url
        session["term_refund"] = refund_amount
        session["term_product"] = product_name
        
        text = (
            f"<b>🗑 Terminate Subscription (Old Username)</b>\n\n"
            f"Product: <b>{product_name}</b>\n"
            f"User: <code>@{target_username}</code>\n"
            f"Time Left: <b>{remaining_hours:.1f} Hours</b>\n\n"
            f"<b>Refund Estimate:</b> {refund_amount} Points\n"
            "<i>(Based on remaining time)</i>\n\n"
            "Are you sure? This will instantly stop the bot."
        )
        
        buttons = [
            [Button.inline(f"✅ Yes, Refund {refund_amount} Pts", b"terminate_sub_execute")],
            [Button.inline("❌ Cancel", b"manage_subs_menu")]
        ]
        await event.respond(text, parse_mode="html", buttons=buttons)
        return

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
        
        session["expecting_rename_new"] = False
        session.pop("rename_old_value", None)
        
        if not old_username: 
            await event.respond("❌ Session expired or invalid state. Please try again from the menu.", parse_mode="html") 
            return

        await event.respond(f"🔄 Processing change from <code>@{old_username}</code> to <code>@{new_username}</code>...", parse_mode="html")

        try:
            resp = await asyncio.to_thread(rename_user_api, old_username, new_username)
            
            if isinstance(resp, dict) and resp.get("ok") is False:
                await event.respond(f"❌ Rename API reported failure: {resp}\n\nLocal username not changed.", parse_mode="html")
                return

            try:
                users_col.update_many({"username": old_username}, {"$set": {"username": new_username}})
                users_col.update_many(
                    {"username": old_username}, 
                    {"$set": {"username.$": new_username}}
                )
                session["username"] = new_username
                
            except Exception as e:
                logger.exception("Failed to update DB entries after rename API success.")
            
            await event.respond(
                f"✅ Success! Username changed from <code>@{old_username}</code> to <code>@{new_username}</code>.",
                parse_mode="html",
                buttons=[[Button.inline("🔙 Manage Menu", b"manage_subs_menu")]]
            )
            
        except Exception as e:
            logger.exception("Rename process failed.")
            await event.respond(f"❌ Error during rename: {e}", parse_mode="html")
            
        return

    # --- STANDARD PURCHASE FLOW ---
    if not session.get("expecting_username"):
        return

    session["pending_username"] = username_clean
    session["expecting_username"] = False
    
    prod_name = "Chat Farmer"

    text = (
        f"Product: <b>{prod_name}</b>\n\n"
        "You entered the Stake username:\n"
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

    try:
        users_col.update_one({"user_id": user_id}, {"$set": {"username": username_clean}}, upsert=True)
    except Exception:
        logger.exception("Failed to persist username to DB on confirm.")

    prod_name = "Chat Farmer"

    # Standard flow for other products
    text = (
        f"Stake username saved: <code>@{username_clean}</code>\n"
        f"Product: <b>{prod_name}</b>\n\n"
        "Choose payment method:"
    )
    buttons = [
        [Button.inline("💳 Buy with Crypto / Points", b"buy_crypto")],
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
        "<tg-emoji emoji-id='6325705628291436771'>💵</tg-emoji> <b>Buy with UPI</b>\n\n"
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

    prod_name = "Chat Farmer"
    header = f"⚡ <b>{prod_name} Plans</b>"

    text = (
        f"{header}\n"
        "💳 <b>Select a Plan</b>\n\n"
        "Plans:\n"
    )
    
    buttons = []
    
    p3h = PLANS_FARMER_SHORT["3h"]
    p6h = PLANS_FARMER_SHORT["6h"]
    p12h = PLANS_FARMER_SHORT["12h"]
    p1d = PLAN_1D_FARMER
    p2d = PLANS_LONG_TERM["2d"]
    p4d = PLANS_LONG_TERM["4d"]
    p7d = PLANS_LONG_TERM["7d"]

    text += f"• {p3h['label']:<8} — {p3h['amount']} USDT\n"
    text += f"• {p6h['label']:<8} — {p6h['amount']} USDT\n"
    text += f"• {p12h['label']:<8} — {p12h['amount']} USDT\n"
    text += f"• {p1d['label']:<8} — {p1d['amount']} USDT\n"
    text += f"• {p2d['label']:<8} — {p2d['amount']} USDT\n"
    text += f"• {p4d['label']:<8} — {p4d['amount']} USDT\n"
    text += f"• {p7d['label']:<8} — {p7d['amount']} USDT\n"

    buttons.append([
        Button.inline(f"3h — {p3h['amount']} $", b"plan_3h"),
        Button.inline(f"6h — {p6h['amount']} $", b"plan_6h"),
    ])
    buttons.append([
        Button.inline(f"12h — {p12h['amount']} $", b"plan_12h"),
        Button.inline(f"1d — {p1d['amount']} $", b"plan_1d"),
    ])
    buttons.append([
        Button.inline(f"2d — {p2d['amount']} $", b"plan_2d"),
        Button.inline(f"4d — {p4d['amount']} $", b"plan_4d"),
    ])
    buttons.append([
        Button.inline(f"7d — {p7d['amount']} $", b"plan_7d"),
    ])

    text += "\nSelect your plan:"

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

    plan_key_raw = event.data.decode().split("_", 1)[1]
    
    if plan_key_raw == "1d":
        plan = PLAN_1D_FARMER
        amount = plan["amount"]
        label = plan["label"]
        hours = plan["hours"]
        plan_key = "1d"
        
    elif plan_key_raw in PLANS_FARMER_SHORT:
        plan = PLANS_FARMER_SHORT[plan_key_raw]
        amount = plan["amount"]
        label = plan["label"]
        hours = plan["hours"]
        plan_key = plan_key_raw
        
    else:
        plan = PLANS_LONG_TERM.get(plan_key_raw)
        if not plan:
            return await event.respond("Invalid plan. Try again.")
        amount = plan["amount"]
        label = plan["label"]
        hours = plan["hours"]
        plan_key = plan_key_raw

    session["selected_plan_key"] = plan_key
    session["selected_amount"] = amount
    session["selected_label"] = label
    session["selected_hours"] = hours

    user_data = users_col.find_one({"user_id": user_id})
    user_points = user_data.get("points", 0.0) if user_data else 0.0
    
    prod_name = "Chat Farmer"

    text = (
        f"🛒 <b>Checkout: {prod_name}</b>\n\n"
        f"Plan: <b>{label}</b>\n"
        f"Cost: <b>{amount} USDT</b> (or Points)\n"
        f"Duration: <b>{hours} Hours</b>\n\n"
        f"💰 Your Points: <b>{user_points:.2f}</b>\n\n"
        "Select payment method:"
    )
    
    buttons = [
        [Button.inline(f"Pay with Crypto ({amount} USDT)", b"pay_method_crypto")],
        [Button.inline(f"Pay with Points ({amount} Pts)", b"pay_method_points")],
        [Button.inline("🔙 Back", b"buy_crypto")]
    ]
    
    try:
        await event.edit(text, parse_mode="html", buttons=buttons)
    except:
        await event.respond(text, parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(data=b"pay_method_points"))
async def pay_points_handler(event):
    await event.answer()
    user_id = event.sender_id
    session = user_sessions.get(user_id)
    
    if not session or "selected_amount" not in session:
        return await event.respond("Session expired. Please restart.")
        
    amount = session["selected_amount"]
    label = session["selected_label"]
    hours = session["selected_hours"]
    username_clean = session["username"]
    
    api_url = FARMER_API_URL
    prod_name = "Chat Farmer"
    forwards = [FARMER_FORWARD_1, FARMER_FORWARD_2, FARMER_FORWARD_3]
        
    user_data = users_col.find_one({"user_id": user_id})
    user_points = user_data.get("points", 0.0) if user_data else 0.0
    
    if user_points < amount:
        await event.answer(f"❌ Insufficient Points! You need {amount} points.", alert=True)
        return
        
    users_col.update_one({"user_id": user_id}, {"$inc": {"points": -amount}})
    
    await event.edit(f"🔄 Activating {prod_name} subscription...", parse_mode="html")
    
    activation_ok = await asyncio.to_thread(activate_subscription, f"@{username_clean}", hours, api_url)
    
    if activation_ok:
        # FORWARD + PIN
        for chat, msg_id in forwards:
            try:
                try:
                    source_entity = await bot.get_entity(chat)
                except:
                    source_entity = chat 
                fwd = await bot.forward_messages(entity=user_id, messages=msg_id, from_peer=source_entity)
                if isinstance(fwd, list): fwd = fwd[0]
                try: await bot.pin_message(user_id, fwd.id, notify=True)
                except: pass
            except: pass
            
        await event.edit(
            f"✅ <b>Paid with Points!</b>\n\n"
            f"Your <b>{prod_name} - {label}</b> subscription is activated.\n"
            f"Deducted: <b>{amount} Points</b>\n"
            f"Remaining: <b>{user_points - amount:.2f} Points</b>\n"
            f"Duration: <b>{hours} hours</b>.",
            parse_mode="html",
            buttons=[[Button.url("🛠 Support", SUPPORT_CHAT_LINK)]]
        )
    else:
        users_col.update_one({"user_id": user_id}, {"$inc": {"points": amount}})
        await event.edit("❌ Activation failed. Points refunded. Contact support.", parse_mode="html")

@bot.on(events.CallbackQuery(data=b"pay_method_crypto"))
async def pay_crypto_inv_handler(event):
    await event.answer()
    user_id = event.sender_id
    session = user_sessions.get(user_id)
    
    if not session or "selected_amount" not in session:
        return await event.respond("Session expired.")
        
    amount = session["selected_amount"]
    label = session["selected_label"]
    hours = session["selected_hours"]
    
    prod_name = "Chat Farmer"

    if user_id in user_tasks:
        old = user_tasks[user_id]
        if not old.done():
            old.cancel()

    await event.edit("🔄 Creating Invoice...", parse_mode="html")

    try:
        resp = await asyncio.to_thread(create_invoice, amount)
    except Exception as e:
        logger.exception(f"Invoice error: {e}")
        return await event.edit("Failed to create invoice.")

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
        return await event.edit("Payment gateway error.")

    session["track_id"] = track_id
    
    text = (
        f"✅ Product: <b>{prod_name}</b>\n"
        f"✅ Plan: <b>{label}</b>\n"
        f"Amount: <b>{amount} USDT</b>\n"
        f"Duration: <b>{hours} Hours</b>\n\n"
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

    task = asyncio.create_task(wait_for_payment(user_id, track_id, label, hours, amount))
    user_tasks[user_id] = task

# ================== BROADCAST ==================
@bot.on(events.NewMessage(pattern=r"^/broadcast$"))
async def broadcast_handler(event):
    if event.sender_id != BOT_OWNER_ID:
        return await event.reply("![❌](tg://emoji?id=5273914604752216432) Unauthorized.", parse_mode="markdown")
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

    await event.reply(f"✅ Broadcast sent to {total} users.", parse_mode="html")

# ================== MAIN ==================
def main():
    logger.info("Stake Payment Bot (Chat Farmer) is running...")
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(check_active_users_loop())
    except Exception as e:
        logger.exception(f"Failed to schedule background tasks: {e}")

    bot.run_until_disconnected()

if __name__ == "__main__":
    main()
