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

# --- Code Claimer Assets ---
CLAIMER_API_URL = "https://code-auth11-4cc0b14f630c.herokuapp.com"
# Forwards for Claimer (Proof channels)
CLAIMER_FORWARD_1 = ("kustvault", 5)
CLAIMER_FORWARD_2 = ("kustvault", 6)
CLAIMER_FORWARD_3 = ("kustvault", 7)

# --- Chat Farmer Assets ---
FARMER_API_URL = "https://farmer-auth1-a6807b536c38.herokuapp.com"
# Forwards for Farmer (Using same vault for now, change if needed)
FARMER_FORWARD_1 = ("kustvault", 2)
FARMER_FORWARD_2 = ("kustvault", 3)
FARMER_FORWARD_3 = ("kustvault", 4)

# Start image
START_IMAGE_URL = "https://filehosting.kustbotsweb.workers.dev/f/3e5a6eb1e2444c14bc87a40b4b6a9973"

# MongoDB
MONGO_URL = "mongodb+srv://kustbotsweb_db_user:z7YqNFmFOvVHKl4B@kust-payments.hiin3lu.mongodb.net/?appName=kust-payments"
mongo = MongoClient(MONGO_URL)
db = mongo["kustfarm"]
users_col = db["users"]

# Bot owner
BOT_OWNER_ID = 7618467489

# OxaPay API
OXAPAY_API_KEY = "SNJEE3-MOEI0B-ZR0FW4-UWSLXH"
OXAPAY_API_BASE = "https://api.oxapay.com"

# Active users checker settings
ACTIVE_USERS_POLL_INTERVAL = 300  # seconds between active_users polls (5 minutes)
REMINDER_THRESHOLD_MINUTES = 60   # notify when <= 60 minutes remain

# --- PRICING PLANS ---

# Plans (Shared pricing for 2d+)
PLANS_LONG_TERM = {
    "2d":  {"label": "2 Days",      "amount": 4.5,  "hours": 48},
    "4d":  {"label": "4 Days",      "amount": 8.0,  "hours": 96},
    "7d":  {"label": "7 Days",      "amount": 12.5, "hours": 168},
}

# 1 Day definitions (Product dependent)
# Code Claimer "1 Day" is now 12 Hours
PLAN_1D_CLAIMER = {"label": "12 Hours", "amount": 2.5, "hours": 12}
# Chat Farmer "1 Day" remains 24 Hours
PLAN_1D_FARMER  = {"label": "1 Day",    "amount": 2.5, "hours": 24}

# Plans (Exclusive to Chat Farmer Short Term)
PLANS_FARMER_SHORT = {
    "3h":  {"label": "3 Hours",     "amount": 0.5,  "hours": 3},
    "6h":  {"label": "6 Hours",     "amount": 0.9,  "hours": 6},
    "12h": {"label": "12 Hours",    "amount": 1.5,  "hours": 12},
}

# --- REFUND CONFIGURATION ---
# Refund amounts per hour remaining. 
# Set conservatively to prevent "Plan Arbitrage" (buying cheap long plans and refunding at expensive short rates).
REFUND_RATE_CLAIMER_PER_HOUR = 0.15 # Approx $0.15 per hour
REFUND_RATE_FARMER_PER_HOUR = 0.08  # Approx $0.08 per hour

PAYMENT_TIMEOUT = 15 * 60
POLL_INTERVAL = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("stake_payment_bot")

bot = TelegramClient("stake_farmer_payment_session", API_ID, API_HASH).start(bot_token=BOT_TOKEN)

user_sessions = {}
user_tasks = {}
bot_username = None # Will be set on startup

# keep track of reminders sent to avoid duplicates: { username_lc: expires_iso }
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
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        logger.info(f"[ACTIVATE] Activated for {username_with_at} on {api_url}. Response: {r.text}")
        return True
    except Exception as e:
        logger.exception(f"[ACTIVATE] Failed activation API for {username_with_at} on {api_url}: {e}")
        return False

def extract_status_from_query_response(resp_json):
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

async def wait_for_payment(user_id: int, track_id: str, plan_label: str, hours: int, plan_amount: float):
    session = user_sessions.get(user_id)
    if not session:
        logger.warning(f"wait_for_payment: no session for {user_id}")
        return False

    product_type = session.get("product", "claimer")
    
    # Configure variables based on product
    if product_type == "farmer":
        api_url = FARMER_API_URL
        product_name = "Chat Farmer"
        forwards = [FARMER_FORWARD_1, FARMER_FORWARD_2, FARMER_FORWARD_3]
    else:
        api_url = CLAIMER_API_URL
        product_name = "Code Claimer"
        forwards = [CLAIMER_FORWARD_1, CLAIMER_FORWARD_2, CLAIMER_FORWARD_3]

    start = time.time()
    while time.time() - start < PAYMENT_TIMEOUT:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            # run blocking network call in a thread
            data = await asyncio.to_thread(query_invoice, track_id)
            status = extract_status_from_query_response(data) or ""
            status = status.lower()

            logger.info(f"Invoice {track_id} status check: {status}")

            if status == "paid":
                username_clean = session.get("username", "UNKNOWN")

                # call activation API in a background thread
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
                            # Emoji: 🎉
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
                # Emoji: ✅
                await bot.send_message(
                    user_id,
                    f"<tg-emoji emoji-id='5039793437776282663'>✅</tg-emoji> Payment confirmed!\n\n"
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
                # Emoji: ❌
                await bot.send_message(user_id, f"<tg-emoji emoji-id='5273914604752216432'>❌</tg-emoji> Invoice for {product_name} ({plan_label}) expired or cancelled.", parse_mode="html")
                return False

        except Exception as e:
            logger.exception(f"Invoice query error for track {track_id}: {e}")

    # timeout reached
    # Emoji: ⏳
    await bot.send_message(user_id, "<tg-emoji emoji-id='4954254104604967967'>⏳</tg-emoji> Payment not confirmed. Create a new invoice.", parse_mode="html")
    return False

# ================== API MANAGEMENT HELPERS ==================

def get_active_users(api_url):
    """
    Call GET /active_users on the specific API.
    """
    try:
        r = requests.get(f"{api_url}/active_users", timeout=20)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.error(f"Failed to fetch active users from {api_url}: {e}")
        return None

def rename_user_api(old_username: str, new_username: str):
    """
    Attempts to rename user on BOTH Claimer and Farmer APIs to ensure sync.
    """
    results = []
    
    if old_username and not old_username.startswith("@"):
        old_username = f"@{old_username}"
    if new_username and not new_username.startswith("@"):
        new_username = f"@{new_username}"
        
    data = {"old_username": old_username, "new_username": new_username, "admin": "admin1234"}

    # List of endpoints to update
    endpoints = [
        f"{CLAIMER_API_URL}/rename_user",
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
    """
    Deletes the user from the specified API using GET or POST /delete_user.
    """
    if username and not username.startswith("@"):
        username = f"@{username}"
    
    params = {"user": username, "admin": "admin1234"}
    
    try:
        # Trying POST
        r = requests.post(f"{api_url}/delete_user", params=params, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"Failed delete_user POST on {api_url}: {e}")
        try:
            # Fallback to GET
            r = requests.get(f"{api_url}/delete_user", params=params, timeout=10)
            r.raise_for_status()
            return True
        except Exception as e2:
             logger.error(f"Failed delete_user GET on {api_url}: {e2}")
    return False

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
    logger.info("Active users reminder loop started (Dual API).")
    
    api_sources = [
        {"name": "Code Claimer", "url": CLAIMER_API_URL},
        {"name": "Chat Farmer", "url": FARMER_API_URL}
    ]

    while True:
        try:
            for source in api_sources:
                api_name = source["name"]
                api_url = source["url"]
                
                data = await asyncio.to_thread(get_active_users, api_url)
                
                if not data:
                    continue

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
                            
                            # Unique key for reminder: username + product + expire_time
                            reminder_key = f"{username_clean.lower()}_{api_name}_{expires_dt.isoformat()}"

                            if minutes_left <= REMINDER_THRESHOLD_MINUTES and minutes_left > 0:
                                previous = _reminder_sent.get(reminder_key)
                                if previous:
                                    continue

                                rec = users_col.find_one({"username": username_clean})
                                if not rec:
                                    continue

                                user_id = rec.get("user_id")
                                if not user_id:
                                    continue

                                try:
                                    # Emoji: ⏳
                                    rem_text = (
                                        f"<tg-emoji emoji-id='4954254104604967967'>⏳</tg-emoji> <b>Subscription ending soon</b>\n\n"
                                        f"Product: <b>{api_name}</b>\n"
                                        f"User: <code>@{username_clean}</code>\n"
                                        f"Expires: {expires_dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                                        f"Time left: ~{int(minutes_left)} mins.\n\n"
                                        f"Renew now to avoid interruption."
                                    )
                                    buttons = [
                                        [Button.inline("💳 Renew Now", b"buy_sub")],
                                        [Button.url("🛠 Support", SUPPORT_CHAT_LINK)],
                                    ]
                                    await bot.send_message(user_id, rem_text, parse_mode="html", buttons=buttons)
                                    logger.info(f"Sent reminder to @{username_clean} for {api_name}")
                                    _reminder_sent[reminder_key] = True
                                except Exception as e:
                                    logger.exception(f"Failed to send reminder to {username_clean}: {e}")
                        except Exception as ee:
                            logger.exception(f"Error processing active user entry: {ee}")
        
        except Exception as e:
            logger.exception(f"check_active_users_loop error: {e}")

        await asyncio.sleep(ACTIVE_USERS_POLL_INTERVAL)


# ================== ADD POINTS COMMAND ==================

@bot.on(events.NewMessage(pattern=r"^/add\s"))
async def add_points_handler(event):
    # 1. Security Check: Only allow BOT_OWNER_ID
    if event.sender_id != BOT_OWNER_ID:
        return

    # 2. Parse Arguments: /add <target> <amount>
    args = event.message.message.split()
    if len(args) != 3:
        # Markdown Emoji for ❌: ![❌](tg://emoji?id=5273914604752216432)
        return await event.reply("![❌](tg://emoji?id=5273914604752216432) Usage: `/add <@username/userid> <amount>`", parse_mode="markdown")

    target_arg = args[1]
    amount_arg = args[2]

    # 3. Validate Amount
    try:
        amount = float(amount_arg)
    except ValueError:
        # Markdown Emoji for ❌
        return await event.reply("![❌](tg://emoji?id=5273914604752216432) Invalid amount. Please enter a number.", parse_mode="markdown")

    # 4. Find Target User in DB
    target_user_id = None
    user_record = None

    # Check if input is User ID (digits)
    if target_arg.isdigit():
        target_user_id = int(target_arg)
        user_record = users_col.find_one({"user_id": target_user_id})
    else:
        # Check if input is Username (remove @ if present)
        clean_username = target_arg.lstrip("@")
        # Try to find by username
        user_record = users_col.find_one({"username": clean_username})
        if user_record:
            target_user_id = user_record.get("user_id")

    if not user_record or not target_user_id:
        # Markdown Emoji for ❌
        return await event.reply(f"![❌](tg://emoji?id=5273914604752216432) User `{target_arg}` not found in the database.", parse_mode="markdown")

    # 5. Update Database
    try:
        # Add points
        users_col.update_one({"user_id": target_user_id}, {"$inc": {"points": amount}})
        
        # Fetch new balance for confirmation
        updated_user = users_col.find_one({"user_id": target_user_id})
        new_balance = updated_user.get("points", 0.0)

        # 6. Notify Owner (Admin) - Markdown
        # Emoji ✅: 5039793437776282663
        await event.reply(
            f"![✅](tg://emoji?id=5039793437776282663) **Success!**\n\n"
            f"User: `{target_arg}`\n"
            f"Added: `{amount}` points\n"
            f"New Balance: `{new_balance:.2f}`",
            parse_mode="markdown"
        )

        # 7. Notify the User - HTML
        # Emoji 🎉: 5208541126583136130
        # Emoji 💰: 5375312095346704820 (Money Bag)
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
        # Markdown Emoji for ❌
        await event.reply(f"![❌](tg://emoji?id=5273914604752216432) Database error: {e}", parse_mode="markdown")
        
# ================== START / MENU HANDLERS ==================

@bot.on(events.NewMessage(pattern=r"^/start"))
async def start_handler(event):
    user_id = event.sender_id
    
    # Reaction
    try:
        await bot(functions.messages.SendReactionRequest(
            peer=event.chat_id,
            msg_id=event.message.id,
            reaction=[types.ReactionEmoji(emoticon='👍')],
            add_to_recent=False
        ))
    except:
        pass

    # Fetch user from DB or create new
    try:
        existing = users_col.find_one({"user_id": user_id})
    except:
        existing = None

    first_time = existing is None
    
    # Parse Arguments (Referral)
    args = event.message.message.split()
    referrer_id = None
    if len(args) > 1:
        try:
            possible_referrer = int(args[1])
            if possible_referrer != user_id:
                referrer_id = possible_referrer
        except ValueError:
            pass

    # Update or Insert DB
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
                    # Emoji: 🎉 (Replaced 🥳 with custom 🎉 as user didn't provide 🥳 ID)
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

    # Emoji: 🚀 -> 5445284980978621387
    caption_text = (
        "<b><tg-emoji emoji-id='5445284980978621387'>🚀</tg-emoji> Kust Bots — Premium Tools</b>\n\n"
        "<b>Available Products:</b>\n"
        "• Code Claimer (High-speed claiming)\n"
        "• Chat Farmer (Automated chat farming)\n\n"
        "Select a product to purchase or manage your account."
    )

    buttons = []
    # Purchase buttons (Product Selection)
    buttons.append([Button.inline("⚡ Buy Code Claimer", b"buy_product_claimer")])
    buttons.append([Button.inline("👨‍🌾 Buy Chat Farmer", b"buy_product_farmer")])

    # Account Actions Row
    account_row = []
    if not first_time:
        account_row.append(Button.inline("✏️ Edit Username", b"edit_username"))
    account_row.append(Button.inline("🎁 Refer & Earn", b"menu_referral"))
    buttons.append(account_row)
    
    # Termination Row
    if not first_time:
        buttons.append([Button.inline("🗑 Terminate Sub", b"terminate_sub_menu")])

    # Info Row
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
    
    if not bot_username:
         me = await bot.get_me()
         globals()['bot_username'] = me.username
         
    ref_link = f"https://t.me/{bot_username}?start={user_id}"
    
    # Emoji 🎁: 5411271889421086677
    # Emoji 💰: 5375312095346704820 (Money Bag)
    # Emoji 👥: 5453957997418004470
    # Emoji 👇: 5442744585132464157
    text = (
        "<b><tg-emoji emoji-id='5411271889421086677'>🎁</tg-emoji> Refer & Earn Program</b>\n\n"
        "Invite friends and earn <b>10%</b> of their spendings as points!\n"
        "1 Point = 1 USDT value.\n\n"
        f"<tg-emoji emoji-id='5375312095346704820'>💰</tg-emoji> <b>Your Balance:</b> {points:.2f} Points\n"
        f"<tg-emoji emoji-id='5453957997418004470'>👥</tg-emoji> <b>Total Referrals:</b> {ref_count}\n\n"
        "<tg-emoji emoji-id='5442744585132464157'>👇</tg-emoji> <b>Your Referral Link:</b>\n"
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
    # Purchase buttons
    buttons.append([Button.inline("⚡ Buy Code Claimer", b"buy_product_claimer")])
    buttons.append([Button.inline("👨‍🌾 Buy Chat Farmer", b"buy_product_farmer")])

    # Account Actions Row
    account_row = []
    if existing:
        account_row.append(Button.inline("✏️ Edit Username", b"edit_username"))
    account_row.append(Button.inline("🎁 Refer & Earn", b"menu_referral"))
    buttons.append(account_row)
    
    # Termination Row
    if existing:
        buttons.append([Button.inline("🗑 Terminate Sub", b"terminate_sub_menu")])

    # Info Row
    buttons.append([
        Button.url("🛠 Support", SUPPORT_CHAT_LINK),
        Button.url("📢 Updates", UPDATES_CHANNEL_LINK),
    ])

    # Emoji: 🚀 -> 5445284980978621387
    caption_text = (
        "<b><tg-emoji emoji-id='5445284980978621387'>🚀</tg-emoji> Kust Bots — Premium Tools</b>\n\n"
        "<b>Available Products:</b>\n"
        "• Code Claimer (High-speed claiming)\n"
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
        [Button.inline("⚡ Buy Code Claimer", b"buy_product_claimer")],
        [Button.inline("👨‍🌾 Buy Chat Farmer", b"buy_product_farmer")],
    ]
    await event.edit("<b>Select Product to Renew:</b>", parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(pattern=b"buy_product_"))
async def buy_product_handler(event):
    await event.answer()
    user_id = event.sender_id
    
    # Determine product from callback data
    data_str = event.data.decode()
    product_type = "claimer"
    product_display = "Code Claimer"
    
    if "farmer" in data_str:
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
    
    # Emoji ✏️: 5395444784611480792
    text = (
        "<b><tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Edit Username — Step 1</b>\n\n"
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

# ================== TERMINATE SUBSCRIPTION HANDLER ==================

@bot.on(events.CallbackQuery(data=b"terminate_sub_menu"))
async def terminate_sub_menu_handler(event):
    await event.answer()
    user_id = event.sender_id
    
    user_doc = users_col.find_one({"user_id": user_id})
    if not user_doc or not user_doc.get("username"):
        await event.edit("❌ No username linked to your account. Cannot terminate.", buttons=[[Button.inline("🔙 Back", b"back_to_start")]])
        return

    username = user_doc.get("username").lstrip("@")
    
    # Search for active subscription in both APIs
    active_details = None # (api_url, expires_dt, product_type)
    
    # Check Claimer
    data_claimer = await asyncio.to_thread(get_active_users, CLAIMER_API_URL)
    if data_claimer:
        users = data_claimer.get("active_users", [])
        if isinstance(users, list):
            for u in users:
                if u.get("username", "").lower().lstrip("@") == username.lower():
                    expires = _parse_iso_datetime(u.get("expires"))
                    if expires:
                        active_details = (CLAIMER_API_URL, expires, "Code Claimer")
                        break

    # Check Farmer (if not found in Claimer)
    if not active_details:
        data_farmer = await asyncio.to_thread(get_active_users, FARMER_API_URL)
        if data_farmer:
            users = data_farmer.get("active_users", [])
            if isinstance(users, list):
                for u in users:
                    if u.get("username", "").lower().lstrip("@") == username.lower():
                        expires = _parse_iso_datetime(u.get("expires"))
                        if expires:
                            active_details = (FARMER_API_URL, expires, "Chat Farmer")
                            break
    
    if not active_details:
        # Enable manual input for old username
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
            [Button.inline("🔙 Back", b"back_to_start")]
        ]
        await event.edit(text, parse_mode="html", buttons=buttons)
        return

    # User is active -> Calculate Refund
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
        if product_name == "Code Claimer":
            refund_amount = remaining_hours * REFUND_RATE_CLAIMER_PER_HOUR
        else:
            refund_amount = remaining_hours * REFUND_RATE_FARMER_PER_HOUR
            
    # Format
    refund_amount = round(refund_amount, 2)
    
    # Store in session for execution
    session = user_sessions.setdefault(user_id, {})
    session["term_username"] = username
    session["term_api"] = api_url
    session["term_refund"] = refund_amount
    session["term_product"] = product_name
    
    # Emoji ⚠️: 5240241223632984914
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
        [Button.inline("❌ Cancel", b"back_to_start")]
    ]
    await event.edit(text, parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(data=b"terminate_sub_force_db"))
async def terminate_force_db_handler(event):
    await event.answer()
    user_id = event.sender_id
    
    # Just remove from DB
    users_col.update_one({"user_id": user_id}, {"$unset": {"username": ""}})
    
    await event.edit("✅ Username removed from database.", buttons=[[Button.inline("🔙 Back", b"back_to_start")]])

@bot.on(events.CallbackQuery(data=b"terminate_sub_execute"))
async def terminate_execute_handler(event):
    await event.answer()
    user_id = event.sender_id
    session = user_sessions.get(user_id, {})
    
    username = session.get("term_username")
    api_url = session.get("term_api")
    refund = session.get("term_refund", 0.0)
    
    if not username or not api_url:
        await event.edit("Session expired. Please try again.", buttons=[[Button.inline("🔙 Back", b"back_to_start")]])
        return
        
    # Execute Delete on API
    await event.edit("⏳ Deleting user from server...", parse_mode="html")
    
    success = await asyncio.to_thread(delete_user_api, username, api_url)
    
    if success:
        # Check if the terminated username matches the user's current DB username
        user_doc = users_col.find_one({"user_id": user_id})
        current_db_user = user_doc.get("username", "").lstrip("@") if user_doc else ""
        
        update_query = {"$inc": {"points": refund}}
        
        # Only remove username from DB if it matches the one we just terminated
        if current_db_user.lower() == username.lower().lstrip("@"):
             update_query["$unset"] = {"username": ""}
             
        users_col.update_one({"user_id": user_id}, update_query)
        
        # Emoji ✅
        await event.edit(
            f"<tg-emoji emoji-id='5039793437776282663'>✅</tg-emoji> <b>Subscription Terminated</b>\n\n"
            f"User <code>@{username}</code> deleted.\n"
            f"Refunded: <b>{refund} Points</b>.",
            parse_mode="html",
            buttons=[[Button.inline("🔙 Main Menu", b"back_to_start")]]
        )
    else:
        # Failed
        await event.edit(
            "❌ Failed to delete user from server. Please contact support.",
            buttons=[[Button.url("🛠 Support", SUPPORT_CHAT_LINK)]]
        )
        
    # Clean session
    session.pop("term_username", None)
    session.pop("term_api", None)
    session.pop("term_refund", None)

# ================== TEXT INPUT HANDLER ==================

@bot.on(events.NewMessage(pattern=r"^[A-Za-z0-9_@]{3,51}$"))
async def username_handler(event):
    user_id = event.sender_id
    session = user_sessions.setdefault(user_id, {})

    raw_username = event.raw_text.strip()
    username_clean = raw_username.lstrip('@')

    # --- TERMINATE OLD USERNAME FLOW ---
    if session.get("expecting_term_username"):
        session["expecting_term_username"] = False
        target_username = username_clean
        
        # Search for active subscription in both APIs (Manual Search)
        active_details = None
        
        # Check Claimer
        data_claimer = await asyncio.to_thread(get_active_users, CLAIMER_API_URL)
        if data_claimer:
            users = data_claimer.get("active_users", [])
            if isinstance(users, list):
                for u in users:
                    if u.get("username", "").lower().lstrip("@") == target_username.lower():
                        expires = _parse_iso_datetime(u.get("expires"))
                        if expires:
                            active_details = (CLAIMER_API_URL, expires, "Code Claimer")
                            break

        # Check Farmer
        if not active_details:
            data_farmer = await asyncio.to_thread(get_active_users, FARMER_API_URL)
            if data_farmer:
                users = data_farmer.get("active_users", [])
                if isinstance(users, list):
                    for u in users:
                        if u.get("username", "").lower().lstrip("@") == target_username.lower():
                            expires = _parse_iso_datetime(u.get("expires"))
                            if expires:
                                active_details = (FARMER_API_URL, expires, "Chat Farmer")
                                break
        
        if not active_details:
            await event.respond(
                f"❌ No active subscription found for <code>@{target_username}</code> either.",
                parse_mode="html",
                buttons=[[Button.inline("🔙 Back", b"back_to_start")]]
            )
            return

        # Calculate Refund
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
            if product_name == "Code Claimer":
                refund_amount = remaining_hours * REFUND_RATE_CLAIMER_PER_HOUR
            else:
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
            [Button.inline("❌ Cancel", b"back_to_start")]
        ]
        await event.respond(text, parse_mode="html", buttons=buttons)
        return

    # --- RENAME FLOW STEP 1: CAPTURE OLD USERNAME ---
    if session.get("expecting_rename_old"):
        session["expecting_rename_old"] = False
        session["expecting_rename_new"] = True
        session["rename_old_value"] = username_clean
        
        # Emoji ✅: 5039793437776282663
        await event.respond(
            f"<tg-emoji emoji-id='5039793437776282663'>✅</tg-emoji> Old Username identified: <code>@{username_clean}</code>\n\n"
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

        # Emoji 🔄: 5375338737028841420
        await event.respond(f"<tg-emoji emoji-id='5375338737028841420'>🔄</tg-emoji> Processing change from <code>@{old_username}</code> to <code>@{new_username}</code>...", parse_mode="html")

        # Call API (Tries both servers)
        try:
            resp = await asyncio.to_thread(rename_user_api, old_username, new_username)
            
            if isinstance(resp, dict) and resp.get("ok") is False:
                # Emoji ❌: 5273914604752216432
                await event.respond(f"<tg-emoji emoji-id='5273914604752216432'>❌</tg-emoji> Rename API reported failure: {resp}\n\nLocal username not changed.", parse_mode="html")
                return

            # Update DB references locally
            try:
                # 1. Update where it matches exactly as a string
                users_col.update_many({"username": old_username}, {"$set": {"username": new_username}})
                
                # 2. Update inside arrays
                users_col.update_many(
                    {"username": old_username}, 
                    {"$set": {"username.$": new_username}}
                )
                
                session["username"] = new_username
                
            except Exception as e:
                logger.exception("Failed to update DB entries after rename API success.")
            
            # Emoji ✅: 5039793437776282663
            await event.respond(f"<tg-emoji emoji-id='5039793437776282663'>✅</tg-emoji> Success! Username changed from <code>@{old_username}</code> to <code>@{new_username}</code>.", parse_mode="html")
            
        except Exception as e:
            logger.exception("Rename process failed.")
            # Emoji ❌: 5273914604752216432
            await event.respond(f"<tg-emoji emoji-id='5273914604752216432'>❌</tg-emoji> Error during rename: {e}", parse_mode="html")
        
        return

    # --- STANDARD PURCHASE FLOW ---
    if not session.get("expecting_username"):
        # If not expecting username, this handler shouldn't have been triggered by regex if we want to show start menu
        # But regex handlers fire before generic ones. 
        # We'll just call start_handler here if not expecting input.
        await start_handler(event)
        return

    # Save as pending until user confirms
    session["pending_username"] = username_clean
    session["expecting_username"] = False
    
    # Determine product display name
    prod = session.get("product", "claimer")
    prod_name = "Code Claimer" if prod == "claimer" else "Chat Farmer"

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

    prod = session.get("product", "claimer")
    prod_name = "Code Claimer" if prod == "claimer" else "Chat Farmer"

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
    # Emoji 💵: 6325705628291436771
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

    prod = session.get("product", "claimer")
    prod_name = "Code Claimer" if prod == "claimer" else "Chat Farmer"

    header = f"⚡ <b>{prod_name} Plans</b>"
    
    # Determine if it is Weekend (Sat=5, Sun=6)
    now = datetime.now(timezone.utc)
    is_weekend = now.weekday() in [5, 6]

    # Emoji 💳: 6129870117619634982
    text = (
        f"{header}\n"
        "<tg-emoji emoji-id='6129870117619634982'>💳</tg-emoji> <b>Select a Plan</b>\n\n"
        "Plans:\n"
    )
    
    buttons = []
    
    if prod == "farmer":
        # --- CHAT FARMER PLANS ---
        # 3h, 6h, 12h, 1d(24h), 2d, 4d, 7d
        # Layout: Symmetrical
        # Row 1: 3h, 6h
        # Row 2: 12h, 1d
        # Row 3: 2d, 4d
        # Row 4: 7d
        
        p3h = PLANS_FARMER_SHORT["3h"]
        p6h = PLANS_FARMER_SHORT["6h"]
        p12h = PLANS_FARMER_SHORT["12h"]
        p1d = PLAN_1D_FARMER
        p2d = PLANS_LONG_TERM["2d"]
        p4d = PLANS_LONG_TERM["4d"]
        p7d = PLANS_LONG_TERM["7d"]

        # Text listing
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
        
    else:
        # --- CODE CLAIMER PLANS ---
        # 12h(1d modified), 2d(48h), 4d, 7d (+ Weekend Pass if weekend)
        
        p1d = PLAN_1D_CLAIMER # 12 Hours
        p2d = PLANS_LONG_TERM["2d"]
        p4d = PLANS_LONG_TERM["4d"]
        p7d = PLANS_LONG_TERM["7d"]
        
        # Override label for 2d to be explicitly "2 Days (48h)"
        p2d_label_display = "2 Days (48h)"

        if is_weekend:
            text += f"• {'Wknd Pass':<8} — 5.0 USDT (Till Sun Night)\n"
            # Row 1: Weekend Pass
            buttons.append([Button.inline("Weekend Pass — 5.0 $", b"plan_weekend")])

        # Standard listings
        text += f"• {p1d['label']:<8} — {p1d['amount']} USDT\n"
        text += f"• {p2d_label_display:<8} — {p2d['amount']} USDT\n"
        text += f"• {p4d['label']:<8} — {p4d['amount']} USDT\n"
        text += f"• {p7d['label']:<8} — {p7d['amount']} USDT\n"

        # Row: 12h, 2d (48h)
        buttons.append([
            Button.inline(f"{p1d['label']} — {p1d['amount']} $", b"plan_1d"),
            Button.inline(f"2d (48h) — {p2d['amount']} $", b"plan_2d"),
        ])
        # Row: 4d, 7d
        buttons.append([
            Button.inline(f"4d — {p4d['amount']} $", b"plan_4d"),
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

    plan_key = event.data.decode().split("_", 1)[1]
    
    # Check Product
    prod = session.get("product", "claimer")

    # Handle Special Weekend Plan
    if plan_key == "weekend":
        now = datetime.now(timezone.utc)
        weekday = now.weekday()
        days_until_monday = 7 - weekday
        next_monday = (now + timedelta(days=days_until_monday)).replace(hour=0, minute=0, second=0, microsecond=0)
        
        remaining = next_monday - now
        hours = int(remaining.total_seconds() / 3600)
        
        if hours < 1: 
            hours = 1
            
        amount = 5.0
        label = "Weekend Pass"
        
    elif plan_key == "1d":
        # Handle 1d Plan (Split logic)
        if prod == "farmer":
            plan = PLAN_1D_FARMER
        else:
            plan = PLAN_1D_CLAIMER
        amount = plan["amount"]
        label = plan["label"]
        hours = plan["hours"]
        
    elif plan_key in PLANS_FARMER_SHORT:
        # Farmer specific short plans
        plan = PLANS_FARMER_SHORT[plan_key]
        amount = plan["amount"]
        label = plan["label"]
        hours = plan["hours"]
        
    else:
        # Standard Long Term Plans (2d, 4d, 7d)
        plan = PLANS_LONG_TERM.get(plan_key)
        if not plan:
            return await event.respond("Invalid plan. Try again.")
        amount = plan["amount"]
        label = plan["label"]
        if prod == "claimer" and plan_key == "2d":
            label = "2 Days (48h)"
        hours = plan["hours"]

    # Save selection to session
    session["selected_plan_key"] = plan_key
    session["selected_amount"] = amount
    session["selected_label"] = label
    session["selected_hours"] = hours

    # === PAYMENT METHOD SELECTION SCREEN ===
    
    user_data = users_col.find_one({"user_id": user_id})
    user_points = user_data.get("points", 0.0) if user_data else 0.0
    
    prod_name = "Code Claimer" if prod == "claimer" else "Chat Farmer"

    # Emoji 🛒: 5226656353744862682
    # Emoji 💰 (Alt/Gold): 6325444137797554944
    text = (
        f"<tg-emoji emoji-id='5226656353744862682'>🛒</tg-emoji> <b>Checkout: {prod_name}</b>\n\n"
        f"Plan: <b>{label}</b>\n"
        f"Cost: <b>{amount} USDT</b> (or Points)\n"
        f"Duration: <b>{hours} Hours</b>\n\n"
        f"<tg-emoji emoji-id='6325444137797554944'>💰</tg-emoji> Your Points: <b>{user_points:.2f}</b>\n\n"
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
    
    prod = session.get("product", "claimer")
    if prod == "farmer":
        api_url = FARMER_API_URL
        prod_name = "Chat Farmer"
        forwards = [FARMER_FORWARD_1, FARMER_FORWARD_2, FARMER_FORWARD_3]
    else:
        api_url = CLAIMER_API_URL
        prod_name = "Code Claimer"
        forwards = [CLAIMER_FORWARD_1, CLAIMER_FORWARD_2, CLAIMER_FORWARD_3]
    
    # Check Balance
    user_data = users_col.find_one({"user_id": user_id})
    user_points = user_data.get("points", 0.0) if user_data else 0.0
    
    if user_points < amount:
        await event.answer(f"❌ Insufficient Points! You need {amount} points.", alert=True)
        return
        
    # Deduct Points
    users_col.update_one({"user_id": user_id}, {"$inc": {"points": -amount}})
    
    # Emoji 🔄: 5375338737028841420
    await event.edit(f"<tg-emoji emoji-id='5375338737028841420'>🔄</tg-emoji> Activating {prod_name} subscription...", parse_mode="html")
    
    # Activate
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
            
        # Emoji ✅: 5039793437776282663
        await event.edit(
            f"<tg-emoji emoji-id='5039793437776282663'>✅</tg-emoji> <b>Paid with Points!</b>\n\n"
            f"Your <b>{prod_name} - {label}</b> subscription is activated.\n"
            f"Deducted: <b>{amount} Points</b>\n"
            f"Remaining: <b>{user_points - amount:.2f} Points</b>\n"
            f"Duration: <b>{hours} hours</b>.",
            parse_mode="html"
        )
    else:
        # Refund on failure
        users_col.update_one({"user_id": user_id}, {"$inc": {"points": amount}})
        # Emoji ❌: 5273914604752216432
        await event.edit(f"<tg-emoji emoji-id='5273914604752216432'>❌</tg-emoji> Activation failed. Points refunded. Contact support.", parse_mode="html")

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
    
    prod = session.get("product", "claimer")
    prod_name = "Code Claimer" if prod == "claimer" else "Chat Farmer"

    if user_id in user_tasks:
        old = user_tasks[user_id]
        if not old.done():
            old.cancel()

    # Emoji 🔄: 5375338737028841420
    await event.edit(f"<tg-emoji emoji-id='5375338737028841420'>🔄</tg-emoji> Creating Invoice...", parse_mode="html")

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
    
    # Emoji ✅: 5039793437776282663
    text = (
        f"<tg-emoji emoji-id='5039793437776282663'>✅</tg-emoji> Product: <b>{prod_name}</b>\n"
        f"<tg-emoji emoji-id='5039793437776282663'>✅</tg-emoji> Plan: <b>{label}</b>\n"
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

    # Pass amount to wait_for_payment to calculate rewards later
    task = asyncio.create_task(wait_for_payment(user_id, track_id, label, hours, amount))
    user_tasks[user_id] = task

# ================== BROADCAST ==================

@bot.on(events.NewMessage(pattern=r"^/broadcast$"))
async def broadcast_handler(event):
    if event.sender_id != BOT_OWNER_ID:
        # Markdown Emoji ❌
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

    # Emoji ✅: 5039793437776282663 (HTML by default if no parse_mode specified, but works best with explicit tag)
    await event.reply(f"<tg-emoji emoji-id='5039793437776282663'>✅</tg-emoji> Broadcast sent to {total} users.", parse_mode="html")

# ================== MAIN ==================

def main():
    logger.info("Stake Payment Bot (Multi-Product) is running...")
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(check_active_users_loop())
    except Exception as e:
        logger.exception(f"Failed to schedule active users checker: {e}")

    bot.run_until_disconnected()

if __name__ == "__main__":
    main()
