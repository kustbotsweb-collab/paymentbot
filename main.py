import asyncio
import logging
import time
import requests
import random
import string
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

# --- Code Claimer Assets ---
CLAIMER_API_URL = "https://code-auth-432b14a97f21.herokuapp.com"
# Forwards for Claimer (Proof channels)
CLAIMER_FORWARD_1 = ("kustvault", 5)
CLAIMER_FORWARD_2 = ("kustvault", 6)
CLAIMER_FORWARD_3 = ("kustvault", 7)

# --- Chat Farmer Assets ---
FARMER_API_URL = "https://farmer-auth-5ff624c7d5bb.herokuapp.com"
# Forwards for Farmer (Using same vault for now, change if needed)
FARMER_FORWARD_1 = ("kustvault", 2)
FARMER_FORWARD_2 = ("kustvault", 3)
FARMER_FORWARD_3 = ("kustvault", 4)

# --- API Claimer Assets ---
API_CLAIMER_AUTH_URL = "https://code-auth-432b14a97f21.herokuapp.com"  # Same as Code Claimer auth
API_CLAIMER_DEPLOY_URL = "https://claimer-deploy-da60199619e8.herokuapp.com"  # CHANGE THIS to your deploy server URL
API_CLAIMER_AUTH_TOKEN = "fuck1234"  # CHANGE THIS to your deploy API auth token
API_CLAIMER_REGION = "eu"  # Deploy region
# Forwards for API Claimer (Using same vault, change if needed)
API_CLAIMER_FORWARD_1 = ("kustvault", 8)
API_CLAIMER_FORWARD_2 = ("kustvault", 9)
API_CLAIMER_FORWARD_3 = ("kustvault", 10)

# Start image
START_IMAGE_URL = "https://filehosting.kustbotsweb.workers.dev/f/3e5a6eb1e2444c14bc87a40b4b6a9973"

# MongoDB
MONGO_URL = "mongodb+srv://kustbotsweb_db_user:z7YqNFmFOvVHKl4B@kust-payments.hiin3lu.mongodb.net/?appName=kust-payments"
mongo = MongoClient(MONGO_URL)
db = mongo["kustfarm"]
users_col = db["users"]

# Track deployed app names to avoid conflicts
deployed_apps_col = db["deployed_apps"]

# Track API subscriptions for better management
api_subscriptions_col = db["api_subscriptions"]

# Bot owner
BOT_OWNER_ID = 7618467489

# OxaPay API
OXAPAY_API_KEY = "SNJEE3-MOEI0B-ZR0FW4-UWSLXH"
OXAPAY_API_BASE = "https://api.oxapay.com"

# Active users checker settings
ACTIVE_USERS_POLL_INTERVAL = 60   # seconds between polls (1 minute)
REMINDER_THRESHOLD_MINUTES = 10   # notify when <= 10 minutes remain

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
# API Claimer "1 Day" is 24 Hours
PLAN_1D_API_CLAIMER = {"label": "1 Day", "amount": 3.0, "hours": 24}

# Plans (Exclusive to Chat Farmer Short Term)
PLANS_FARMER_SHORT = {
    "3h":  {"label": "3 Hours",     "amount": 0.5,  "hours": 3},
    "6h":  {"label": "6 Hours",     "amount": 0.9,  "hours": 6},
    "12h": {"label": "12 Hours",    "amount": 1.5,  "hours": 12},
}

# Plans (Exclusive to API Claimer)
PLANS_API_CLAIMER = {
    "1d":  {"label": "1 Day",    "amount": 5.0,  "hours": 12},
    "3d":  {"label": "3 Days",   "amount": 7.5,  "hours": 72},
    "7d":  {"label": "7 Days",   "amount": 10.0, "hours": 168},
    "14d": {"label": "14 Days",  "amount": 18.0, "hours": 336},
    "30d": {"label": "30 Days",  "amount": 30.0, "hours": 720},
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
# Refund amounts per hour remaining. 
# Set conservatively to prevent "Plan Arbitrage" (buying cheap long plans and refunding at expensive short rates).
REFUND_RATE_CLAIMER_PER_HOUR = 0.15 # Approx $0.15 per hour
REFUND_RATE_FARMER_PER_HOUR = 0.08  # Approx $0.08 per hour
REFUND_RATE_API_CLAIMER_PER_HOUR = 0.10  # Approx $0.10 per hour

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

# Track expired cleanup to avoid duplicates: { key: True }
_expired_cleanup_sent = {}

# Track pre-expiry container deletion to avoid duplicates: { key: True }
_pre_expiry_deletion_sent = {}

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

def delete_deployed_app(app_name: str):
    """
    Delete a deployed Heroku app/container via the deploy API.
    """
    try:
        url = f"{API_CLAIMER_DEPLOY_URL}/apps/{app_name}"
        headers = {
            "Authorization": f"Bearer {API_CLAIMER_AUTH_TOKEN}"
        }
        
        logger.info(f"[DELETE_APP] Deleting container: {app_name}")
        
        r = requests.delete(url, headers=headers, timeout=30)
        
        if r.status_code == 200:
            logger.info(f"[DELETE_APP] Successfully deleted app: {app_name}")
            return True, r.json()
        else:
            # Check if the app is already deleted (404 not found)
            # The API may return 500 with embedded 404 error
            response_text = r.text.lower()
            if "not_found" in response_text or "\"id\":\"not_found\"" in response_text or "couldn't find that app" in response_text:
                logger.info(f"[DELETE_APP] App {app_name} already deleted (not found), treating as success")
                return True, {"status": "already_deleted", "message": "App was already deleted"}
            
            logger.error(f"[DELETE_APP] Failed to delete app {app_name}: {r.status_code} - {r.text}")
            return False, {"error": f"HTTP {r.status_code}: {r.text}"}
            
    except Exception as e:
        logger.exception(f"[DELETE_APP] Exception deleting app {app_name}: {e}")
        return False, {"error": str(e)}

def generate_unique_app_name(username: str):
    """
    Generate a unique app name based on username.
    Format: api-cl-{username}
    If taken, append random alphanumeric character.
    """
    # Clean username - remove @ and special characters
    clean_user = username.lstrip("@").lower()
    clean_user = ''.join(c for c in clean_user if c.isalnum() or c == '_')
    
    # Limit length
    if len(clean_user) > 20:
        clean_user = clean_user[:20]
    
    base_name = f"api-cl-{clean_user}"
    
    # Check if base name exists
    existing = deployed_apps_col.find_one({"app_name": base_name})
    if not existing:
        return base_name
    
    # If exists, append random character until unique
    for _ in range(100):  # Max 100 attempts
        suffix = random.choice(string.ascii_lowercase + string.digits)
        candidate = f"{base_name}-{suffix}"
        existing = deployed_apps_col.find_one({"app_name": candidate})
        if not existing:
            return candidate
    
    # Fallback with random string
    random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"{base_name}-{random_suffix}"

def deploy_api_container(session_token: str, app_name: str):
    """
    Deploy API container for the user.
    Calls the deploy API with the session token and app name.
    Returns SSE streaming response and parses the final status.
    """
    try:
        url = f"{API_CLAIMER_DEPLOY_URL}/deploy"
        headers = {
            "Authorization": f"Bearer {API_CLAIMER_AUTH_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream"
        }
        payload = {
            "session_token": session_token,
            "app_name": app_name
        }
        
        logger.info(f"[DEPLOY] Deploying container: {app_name}")
        
        # Use stream=True to handle SSE response
        r = requests.post(url, headers=headers, json=payload, stream=True, timeout=600)
        
        if r.status_code >= 400:
            error_text = r.text
            logger.error(f"[DEPLOY] Deploy failed with status {r.status_code}: {error_text}")
            return False, {"error": f"HTTP {r.status_code}: {error_text}"}
        
        # Parse SSE stream
        final_result = None
        last_status = None
        
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            
            line = line.strip()
            
            # SSE data lines start with "data: "
            if line.startswith("data: "):
                data_str = line[6:]  # Remove "data: " prefix
                try:
                    data = json.loads(data_str)
                    status = data.get("status", "")
                    message = data.get("message", "")
                    progress = data.get("progress", 0)
                    
                    logger.info(f"[DEPLOY] Status update: {status} - {message} ({progress}%)")
                    
                    # Track the last status
                    last_status = data
                    
                    # Check for final states
                    if status == "completed":
                        final_result = data
                        logger.info(f"[DEPLOY] Deploy completed successfully for {app_name}")
                        return True, data
                    elif status == "error" or status == "build_failed" or status == "build_timeout":
                        logger.error(f"[DEPLOY] Deploy failed: {message}")
                        return False, data
                        
                except json.JSONDecodeError as e:
                    logger.warning(f"[DEPLOY] Failed to parse SSE data: {data_str}")
                    continue
        
        # If we got here, check the last status
        if last_status:
            if last_status.get("status") == "completed":
                return True, last_status
            elif last_status.get("success") is True:
                return True, last_status
        
        # No clear result
        logger.warning(f"[DEPLOY] Stream ended without clear result for {app_name}")
        return False, {"error": "Stream ended without completion status", "last_status": last_status}
        
    except requests.exceptions.Timeout:
        logger.error(f"[DEPLOY] Deploy timeout for {app_name}")
        return False, {"error": "Deployment timed out"}
    except Exception as e:
        logger.exception(f"[DEPLOY] Failed to deploy container {app_name}: {e}")
        return False, {"error": str(e)}

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

# ================== PROGRESS ANIMATION HELPER ==================

async def animate_deploy_progress(user_id: int, app_name: str, session_token: str):
    """
    Deploy container with animated progress messages.
    Returns (success, result) tuple.
    """
    # Animation frames
    frames = [
        "⏳ Initializing deployment...",
        "⏳ Fetching container image...",
        "⏳ Setting up environment...",
        "⏳ Configuring network...",
        "⏳ Starting container...",
        "⏳ Running health checks...",
        "⏳ Finalizing deployment...",
    ]
    
    # Send initial message
    progress_msg = await bot.send_message(
        user_id,
        f"🚀 <b>Deploying your API Claimer container...</b>\n\n"
        f"App Name: <code>{app_name}</code>\n"
        f"Region: <b>{API_CLAIMER_REGION.upper()}</b>\n\n"
        f"{frames[0]}",
        parse_mode="html"
    )
    
    frame_idx = 0
    dots = ""
    
    async def update_progress():
        nonlocal frame_idx, dots
        try:
            # Cycle through dots
            dots = "." * ((len(dots) % 3) + 1)
            current_frame = frames[frame_idx % len(frames)]
            
            await bot.edit_message(
                user_id,
                progress_msg.id,
                f"🚀 <b>Deploying your API Claimer container...</b>\n\n"
                f"App Name: <code>{app_name}</code>\n"
                f"Region: <b>{API_CLAIMER_REGION.upper()}</b>\n\n"
                f"{current_frame}{dots}",
                parse_mode="html"
            )
            frame_idx += 1
        except Exception as e:
            logger.warning(f"Failed to update progress message: {e}")
    
    # Start deployment in background
    deploy_task = asyncio.create_task(
        asyncio.to_thread(deploy_api_container, session_token, app_name)
    )
    
    # Animate while waiting
    while not deploy_task.done():
        await update_progress()
        await asyncio.sleep(2)  # Update every 2 seconds
    
    # Get result
    try:
        success, result = deploy_task.result()
    except Exception as e:
        logger.exception(f"Deploy task raised exception: {e}")
        success = False
        result = {"error": str(e)}
    
    # Update final message
    if success:
        web_url = result.get("web_url", f"https://{app_name}.herokuapp.com")
        await bot.edit_message(
            user_id,
            progress_msg.id,
            f"✅ <b>Container Deployed Successfully!</b>\n\n"
            f"App Name: <code>{app_name}</code>\n"
            f"Region: <b>{API_CLAIMER_REGION.upper()}</b>\n"
            f"URL: <code>{web_url}</code>\n\n"
            f"🎉 Your API Claimer is now live!",
            parse_mode="html"
        )
    else:
        error_msg = result.get("message", result.get("error", "Unknown error"))
        await bot.edit_message(
            user_id,
            progress_msg.id,
            f"❌ <b>Container Deployment Failed</b>\n\n"
            f"App Name: <code>{app_name}</code>\n"
            f"Error: {error_msg}\n\n"
            f"Please contact support for assistance.",
            parse_mode="html"
        )
    
    return success, result

async def wait_for_payment(user_id: int, track_id: str, plan_label: str, hours: int, plan_amount: float, is_bulk_points: bool = False, points_amount: int = 0):
    session = user_sessions.get(user_id)
    if not session:
        logger.warning(f"wait_for_payment: no session for {user_id}")
        return False

    product_type = session.get("product", "claimer")
    
    # Handle bulk points purchase
    if is_bulk_points:
        product_name = "Points Purchase"
        forwards = []
        api_url = None
    else:
        # Configure variables based on product
        if product_type == "farmer":
            api_url = FARMER_API_URL
            product_name = "Chat Farmer"
            forwards = [FARMER_FORWARD_1, FARMER_FORWARD_2, FARMER_FORWARD_3]
        elif product_type == "api_claimer":
            api_url = API_CLAIMER_AUTH_URL
            product_name = "API Claimer"
            forwards = [API_CLAIMER_FORWARD_1, API_CLAIMER_FORWARD_2, API_CLAIMER_FORWARD_3]
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
                
                # === BULK POINTS PURCHASE ===
                if is_bulk_points:
                    # Add points to user account
                    users_col.update_one(
                        {"user_id": user_id},
                        {"$inc": {"points": points_amount}},
                        upsert=True
                    )
                    
                    # Get updated balance
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

                # === API CLAIMER SPECIFIC: DEPLOY CONTAINER WITH ANIMATION ===
                deploy_message = ""
                if product_type == "api_claimer":
                    session_token = session.get("session_token")
                    if session_token:
                        # Generate unique app name
                        app_name = generate_unique_app_name(username_clean)
                        
                        # Deploy container with animated progress
                        deploy_ok, deploy_resp = await animate_deploy_progress(user_id, app_name, session_token)
                        
                        if deploy_ok:
                            expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
                            web_url = deploy_resp.get("web_url", f"https://{app_name}.herokuapp.com")
                            
                            # Save deployed app info to DB with comprehensive tracking
                            deployed_apps_col.insert_one({
                                "user_id": user_id,
                                "username": username_clean,
                                "app_name": app_name,
                                "session_token": session_token,
                                "deployed_at": datetime.now(timezone.utc),
                                "expires_at": expires_at,
                                "status": "active",
                                "web_url": web_url,
                                "region": API_CLAIMER_REGION,
                                "product_type": "api_claimer"
                            })
                            
                            # Also save to api_subscriptions collection for comprehensive tracking
                            api_subscriptions_col.update_one(
                                {"user_id": user_id, "username": username_clean, "product_type": "api_claimer"},
                                {
                                    "$set": {
                                        "user_id": user_id,
                                        "username": username_clean,
                                        "product_type": "api_claimer",
                                        "app_name": app_name,
                                        "api_url": api_url,
                                        "expires_at": expires_at,
                                        "status": "active",
                                        "session_token": session_token,
                                        "web_url": web_url,
                                        "updated_at": datetime.now(timezone.utc)
                                    }
                                },
                                upsert=True
                            )
                            
                            deploy_message = (
                                f"\n\n✅ <b>Container Deployed!</b>\n"
                                f"App Name: <code>{app_name}</code>\n"
                                f"Region: <b>{API_CLAIMER_REGION.upper()}</b>\n"
                                f"URL: <code>{web_url}</code>"
                            )
                        else:
                            error_msg = deploy_resp.get("message", deploy_resp.get("error", "Unknown error"))
                            deploy_message = (
                                f"\n\n⚠️ <b>Container deployment failed.</b>\n"
                                f"Error: {error_msg}\n"
                                f"Your subscription is active but container was not deployed.\n"
                                f"Please contact support with your API key."
                            )
                            logger.error(f"Deploy failed for user {user_id}: {deploy_resp}")
                    else:
                        deploy_message = (
                            f"\n\n⚠️ <b>Session token not found.</b>\n"
                            f"Please contact support to deploy your container manually."
                        )

                # Notify user
                # Emoji: ✅
                await bot.send_message(
                    user_id,
                    f"✅ Payment confirmed!\n\n"
                    f"Your <b>{product_name} - {plan_label}</b> subscription is activated.\n"
                    f"Stake Username: <code>@{username_clean}</code>\n"
                    f"Duration: <b>{hours} hours</b>."
                    f"{deploy_message}",
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
                await bot.send_message(user_id, f"❌ Invoice for {product_name} ({plan_label}) expired or cancelled.", parse_mode="html")
                return False

        except Exception as e:
            logger.exception(f"Invoice query error for track {track_id}: {e}")

    # timeout reached
    # Emoji: ⏳
    await bot.send_message(user_id, "⏳ Payment not confirmed. Create a new invoice.", parse_mode="html")
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
    
    params = {"username": username, "admin": "admin1234"}
    
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
    logger.info("Active users reminder loop started (60s interval, 10min reminder, auto-delete containers).")
    
    api_sources = [
        {"name": "Code Claimer", "url": CLAIMER_API_URL},
        {"name": "Chat Farmer", "url": FARMER_API_URL},
        {"name": "API Claimer", "url": API_CLAIMER_AUTH_URL}
    ]

    while True:
        try:
            now = datetime.now(timezone.utc)
            
            # === PHASE 1: CHECK ACTIVE USERS FROM API FOR REMINDERS AND PRE-EXPIRY CLEANUP ===
            for source in api_sources:
                api_name = source["name"]
                api_url = source["url"]
                
                data = await asyncio.to_thread(get_active_users, api_url)
                
                if not data:
                    continue

                users = data.get("active_users") if isinstance(data, dict) else None
                if isinstance(users, list):
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
                            seconds_left = time_left.total_seconds()
                            minutes_left = seconds_left / 60

                            username_clean = username.lstrip("@").strip()
                            
                            # Get user info from DB
                            rec = users_col.find_one({"username": username_clean})
                            user_id = rec.get("user_id") if rec else None

                            # === REMINDER: 10 minutes left ===
                            if minutes_left <= REMINDER_THRESHOLD_MINUTES and minutes_left > 1:
                                # Unique key for reminder based on username + product + expire_time
                                reminder_key = f"reminder_{username_clean.lower()}_{api_name}_{expires_dt.isoformat()}"
                                
                                if not _reminder_sent.get(reminder_key):
                                    if user_id:
                                        try:
                                            rem_text = (
                                                f"⏳ <b>Subscription ending soon</b>\n\n"
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
                                            logger.info(f"[REMINDER] Sent to @{username_clean} for {api_name} ({int(minutes_left)} mins left)")
                                            _reminder_sent[reminder_key] = True
                                        except Exception as e:
                                            logger.exception(f"Failed to send reminder to {username_clean}: {e}")

                            # === PRE-EXPIRY CONTAINER DELETION: < 60 seconds left ===
                            # Delete container just before expiry so user gets the notification
                            if api_name == "API Claimer" and seconds_left <= 60 and seconds_left > -300:
                                # Find the deployed container for this user
                                deployed_app = deployed_apps_col.find_one({
                                    "username": username_clean,
                                    "status": "active"
                                })
                                
                                if deployed_app:
                                    app_name = deployed_app.get("app_name")
                                    container_user_id = deployed_app.get("user_id")
                                    
                                    if app_name:
                                        # Unique key for this deletion
                                        deletion_key = f"pre_expiry_{app_name}"
                                        
                                        if not _pre_expiry_deletion_sent.get(deletion_key):
                                            logger.info(f"[PRE_EXPIRY_DELETE] Subscription about to expire for @{username_clean}, deleting container: {app_name}")
                                            
                                            # Delete the container
                                            delete_ok, delete_resp = await asyncio.to_thread(delete_deployed_app, app_name)
                                            
                                            if delete_ok:
                                                # Update deployed_apps_col
                                                deployed_apps_col.update_one(
                                                    {"app_name": app_name},
                                                    {"$set": {
                                                        "status": "expired_deleted",
                                                        "deleted_at": now,
                                                        "deletion_reason": "subscription_expired"
                                                    }}
                                                )
                                                
                                                # Update api_subscriptions_col
                                                api_subscriptions_col.update_one(
                                                    {"app_name": app_name},
                                                    {"$set": {
                                                        "status": "expired_deleted",
                                                        "deleted_at": now,
                                                        "deletion_reason": "subscription_expired"
                                                    }}
                                                )
                                                
                                                # Notify user
                                                notify_user_id = container_user_id or user_id
                                                if notify_user_id:
                                                    try:
                                                        await bot.send_message(
                                                            notify_user_id,
                                                            f"⏰ <b>Subscription Expired</b>\n\n"
                                                            f"Your API Claimer subscription has expired.\n"
                                                            f"Container <code>{app_name}</code> has been automatically stopped.\n\n"
                                                            f"Renew to get a new container deployed.",
                                                            parse_mode="html",
                                                            buttons=[
                                                                [Button.inline("💳 Renew Now", b"buy_product_api_claimer")],
                                                                [Button.url("🛠 Support", SUPPORT_CHAT_LINK)]
                                                            ]
                                                        )
                                                    except Exception as e:
                                                        logger.error(f"Failed to notify user {notify_user_id} about expired container: {e}")
                                                
                                                logger.info(f"[PRE_EXPIRY_DELETE] Successfully deleted container: {app_name}")
                                            else:
                                                logger.error(f"[PRE_EXPIRY_DELETE] Failed to delete container {app_name}: {delete_resp}")
                                            
                                            # Mark as processed to avoid duplicates
                                            _pre_expiry_deletion_sent[deletion_key] = True
                                            
                        except Exception as ee:
                            logger.exception(f"Error processing active user entry: {ee}")
            
            # === PHASE 2: CHECK DEPLOYED CONTAINERS FROM DATABASE (FALLBACK) ===
            # This catches any containers that might have been missed by Phase 1
            try:
                # Find all active deployed containers
                active_containers = deployed_apps_col.find({"status": "active"})
                
                for container in active_containers:
                    try:
                        app_name = container.get("app_name")
                        username = container.get("username")
                        container_user_id = container.get("user_id")
                        expires_at = container.get("expires_at")
                        
                        if not app_name or not expires_at:
                            continue
                        
                        # Parse expires_at if it's a string
                        if isinstance(expires_at, str):
                            expires_at = _parse_iso_datetime(expires_at)
                        
                        if not expires_at:
                            continue

                        # Ensure expires_at has timezone info
                        if expires_at.tzinfo is None:
                            expires_at = expires_at.replace(tzinfo=timezone.utc)

                        time_left = expires_at - now
                        seconds_left = time_left.total_seconds()
                        
                        # Delete if expired or about to expire (< 60 seconds)
                        if seconds_left <= 60:
                            # Unique key for this deletion
                            deletion_key = f"db_cleanup_{app_name}"
                            
                            if not _expired_cleanup_sent.get(deletion_key):
                                logger.info(f"[DB_CLEANUP] Found expired/soon-to-expire container: {app_name} for @{username}")
                                
                                # Delete the container
                                delete_ok, delete_resp = await asyncio.to_thread(delete_deployed_app, app_name)
                                
                                if delete_ok:
                                    # Update deployed_apps_col
                                    deployed_apps_col.update_one(
                                        {"app_name": app_name},
                                        {"$set": {
                                            "status": "expired_deleted",
                                            "deleted_at": now,
                                            "deletion_reason": "subscription_expired"
                                        }}
                                    )
                                    
                                    # Update api_subscriptions_col
                                    api_subscriptions_col.update_one(
                                        {"app_name": app_name},
                                        {"$set": {
                                            "status": "expired_deleted",
                                            "deleted_at": now,
                                            "deletion_reason": "subscription_expired"
                                        }}
                                    )
                                    
                                    # Notify user
                                    if container_user_id:
                                        try:
                                            await bot.send_message(
                                                container_user_id,
                                                f"⏰ <b>Subscription Expired</b>\n\n"
                                                f"Your API Claimer subscription has expired.\n"
                                                f"Container <code>{app_name}</code> has been automatically stopped.\n\n"
                                                f"Renew to get a new container deployed.",
                                                parse_mode="html",
                                                buttons=[
                                                    [Button.inline("💳 Renew Now", b"buy_product_api_claimer")],
                                                    [Button.url("🛠 Support", SUPPORT_CHAT_LINK)]
                                                ]
                                            )
                                        except Exception as e:
                                            logger.error(f"Failed to notify user {container_user_id} about expired container: {e}")
                                    
                                    logger.info(f"[DB_CLEANUP] Successfully deleted container: {app_name}")
                                else:
                                    logger.error(f"[DB_CLEANUP] Failed to delete container {app_name}: {delete_resp}")
                                
                                # Mark as processed
                                _expired_cleanup_sent[deletion_key] = True
                                    
                    except Exception as e:
                        logger.exception(f"Error processing container from DB: {e}")
                        
            except Exception as e:
                logger.exception(f"Error in DB container cleanup phase: {e}")
        
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

# ================== API CLAIMER STATS COMMAND (OWNER ONLY) ==================

@bot.on(events.NewMessage(pattern=r"^/apicstats$"))
async def apicstats_handler(event):
    """
    Owner-only command to view all API Claimer users stats.
    Shows: Active users, their remaining time, container status, etc.
    """
    if event.sender_id != BOT_OWNER_ID:
        return

    await event.reply("📊 <b>Fetching API Claimer Stats...</b>", parse_mode="html")

    now = datetime.now(timezone.utc)
    
    # === 1. FETCH FROM API ===
    api_data = await asyncio.to_thread(get_active_users, API_CLAIMER_AUTH_URL)
    
    api_users = []
    if api_data and isinstance(api_data, dict):
        api_users = api_data.get("active_users", [])
    
    # === 2. FETCH FROM DATABASE (Deployed Containers) ===
    db_containers = list(deployed_apps_col.find({"product_type": "api_claimer"}))
    
    # === 3. BUILD STATS REPORT ===
    
    # Count totals
    total_api_users = len(api_users)
    total_db_containers = len(db_containers)
    active_containers = len([c for c in db_containers if c.get("status") == "active"])
    expired_containers = len([c for c in db_containers if c.get("status") in ["expired_deleted", "terminated"]])
    
    # Build user details
    report_lines = []
    report_lines.append("<b>📊 API CLAIMER STATS</b>")
    report_lines.append(f"📅 <i>Generated: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}</i>")
    report_lines.append("")
    report_lines.append(f"<b>📈 Summary:</b>")
    report_lines.append(f"• Active on API: <b>{total_api_users}</b>")
    report_lines.append(f"• Total Containers in DB: <b>{total_db_containers}</b>")
    report_lines.append(f"• Active Containers: <b>{active_containers}</b>")
    report_lines.append(f"• Expired/Terminated: <b>{expired_containers}</b>")
    report_lines.append("")
    report_lines.append("<b>👥 Active Users (from API):</b>")
    report_lines.append("━━━━━━━━━━━━━━━━━━━━")
    
    if not api_users:
        report_lines.append("<i>No active users found on API.</i>")
    else:
        for idx, user in enumerate(api_users, 1):
            username = user.get("username", "Unknown")
            expires_raw = user.get("expires")
            
            if expires_raw:
                expires_dt = _parse_iso_datetime(expires_raw)
                if expires_dt:
                    if expires_dt.tzinfo is None:
                        expires_dt = expires_dt.replace(tzinfo=timezone.utc)
                    
                    time_left = expires_dt - now
                    total_seconds = time_left.total_seconds()
                    
                    if total_seconds > 0:
                        hours_left = total_seconds / 3600
                        if hours_left >= 24:
                            time_str = f"{hours_left/24:.1f} days"
                        elif hours_left >= 1:
                            time_str = f"{hours_left:.1f} hours"
                        else:
                            time_str = f"{total_seconds/60:.0f} mins"
                        
                        status_emoji = "🟢"
                    else:
                        time_str = "EXPIRED"
                        status_emoji = "🔴"
                    
                    expires_str = expires_dt.strftime('%Y-%m-%d %H:%M UTC')
                else:
                    time_str = "N/A"
                    expires_str = "Invalid date"
                    status_emoji = "⚪"
            else:
                time_str = "N/A"
                expires_str = "No expiry"
                status_emoji = "⚪"
            
            # Check for container in DB
            container_info = ""
            clean_username = username.lstrip("@") if username else ""
            container = deployed_apps_col.find_one({"username": clean_username, "product_type": "api_claimer"})
            if container:
                container_status = container.get("status", "unknown")
                app_name = container.get("app_name", "N/A")
                if container_status == "active":
                    container_info = f" | 📦 {app_name}"
                else:
                    container_info = f" | 📦 {app_name} ({container_status})"
            
            report_lines.append(f"{status_emoji} <code>@{username.lstrip('@')}</code>")
            report_lines.append(f"   ⏱ {time_str} left | Expires: {expires_str}{container_info}")
    
    # === 4. ADD DB-ONLY CONTAINERS (not in API but in DB) ===
    api_usernames = set(u.get("username", "").lower().lstrip("@") for u in api_users)
    
    db_only_containers = []
    for container in db_containers:
        container_username = container.get("username", "").lower()
        if container_username not in api_usernames and container.get("status") == "active":
            db_only_containers.append(container)
    
    if db_only_containers:
        report_lines.append("")
        report_lines.append("<b>📦 DB Containers (Not in API):</b>")
        report_lines.append("━━━━━━━━━━━━━━━━━━━━")
        for container in db_only_containers:
            app_name = container.get("app_name", "N/A")
            username = container.get("username", "Unknown")
            status = container.get("status", "unknown")
            deployed_at = container.get("deployed_at")
            expires_at = container.get("expires_at")
            
            deployed_str = deployed_at.strftime('%Y-%m-%d') if deployed_at else "N/A"
            expires_str = expires_at.strftime('%Y-%m-%d %H:%M') if expires_at else "N/A"
            
            if expires_at:
                if isinstance(expires_at, str):
                    expires_at = _parse_iso_datetime(expires_at)
                if expires_at:
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=timezone.utc)
                    time_left = expires_at - now
                    total_seconds = time_left.total_seconds()
                    if total_seconds > 0:
                        status_emoji = "🟡"
                    else:
                        status_emoji = "🔴"
                else:
                    status_emoji = "⚪"
            else:
                status_emoji = "⚪"
            
            report_lines.append(f"{status_emoji} <code>@{username}</code>")
            report_lines.append(f"   📦 {app_name} | Status: {status}")
            report_lines.append(f"   Deployed: {deployed_str} | Expires: {expires_str}")
    
    # Send report (split if too long)
    full_report = "\n".join(report_lines)
    
    # Telegram has a 4096 char limit
    if len(full_report) <= 4096:
        await event.reply(full_report, parse_mode="html")
    else:
        # Split into chunks
        chunks = []
        current_chunk = ""
        for line in report_lines:
            if len(current_chunk) + len(line) + 1 > 4000:
                chunks.append(current_chunk)
                current_chunk = line
            else:
                if current_chunk:
                    current_chunk += "\n" + line
                else:
                    current_chunk = line
        if current_chunk:
            chunks.append(current_chunk)
        
        for i, chunk in enumerate(chunks):
            if i == 0:
                await event.reply(chunk, parse_mode="html")
            else:
                await event.reply(f"<b>📊 API Claimer Stats (continued)</b>\n\n{chunk}", parse_mode="html")

# ================== EXTEND TIME COMMAND (OWNER ONLY) ==================

@bot.on(events.NewMessage(pattern=r"^/extend\s"))
async def extend_time_handler(event):
    """
    Owner-only command to extend subscription time for users.
    Usage: /extend <username/userid> <hours> [product]
    
    Examples:
    /extend @alice123 24 claimer
    /extend @alice123 48 farmer
    /extend @alice123 72 api_claimer
    /extend 123456789 24 claimer
    
    If product not specified, extends on all products where user is active.
    """
    if event.sender_id != BOT_OWNER_ID:
        return

    # Parse arguments
    args = event.message.message.split()
    
    if len(args) < 3:
        return await event.reply(
            "❌ <b>Usage:</b> <code>/extend &lt;username/userid&gt; &lt;hours&gt; [product]</code>\n\n"
            "<b>Products:</b> <code>claimer</code>, <code>farmer</code>, <code>api_claimer</code>\n\n"
            "<b>Examples:</b>\n"
            "<code>/extend @alice123 24 claimer</code>\n"
            "<code>/extend @alice123 48 farmer</code>\n"
            "<code>/extend @alice123 72 api_claimer</code>\n"
            "<code>/extend 123456789 24 claimer</code>\n\n"
            "<i>If product not specified, extends on all active products.</i>",
            parse_mode="html"
        )
    
    target_arg = args[1]
    hours_arg = args[2]
    product_arg = args[3].lower() if len(args) > 3 else None
    
    # Validate hours
    try:
        hours_to_add = int(hours_arg)
        if hours_to_add <= 0:
            raise ValueError("Hours must be positive")
    except ValueError:
        return await event.reply("❌ Invalid hours. Please enter a positive number.", parse_mode="html")
    
    # Validate product
    valid_products = ["claimer", "farmer", "api_claimer"]
    if product_arg and product_arg not in valid_products:
        return await event.reply(f"❌ Invalid product. Valid options: {', '.join(valid_products)}", parse_mode="html")
    
    # Find user
    target_user_id = None
    target_username = None
    
    if target_arg.isdigit():
        target_user_id = int(target_arg)
        user_record = users_col.find_one({"user_id": target_user_id})
        if user_record:
            target_username = user_record.get("username")
    else:
        clean_username = target_arg.lstrip("@")
        user_record = users_col.find_one({"username": clean_username})
        if user_record:
            target_user_id = user_record.get("user_id")
            target_username = clean_username
    
    if not target_username:
        return await event.reply(f"❌ User `{target_arg}` not found or has no username set.", parse_mode="html")
    
    # Show processing message
    status_msg = await event.reply(f"🔄 Extending subscription for <code>@{target_username}</code>...", parse_mode="html")
    
    # Prepare results
    results = []
    
    # Determine which products to extend
    products_to_extend = [product_arg] if product_arg else valid_products
    
    for product in products_to_extend:
        if product == "claimer":
            api_url = CLAIMER_API_URL
            product_name = "Code Claimer"
        elif product == "farmer":
            api_url = FARMER_API_URL
            product_name = "Chat Farmer"
        elif product == "api_claimer":
            api_url = API_CLAIMER_AUTH_URL
            product_name = "API Claimer"
        else:
            continue
        
        # Call activation API to extend
        try:
            success = await asyncio.to_thread(activate_subscription, f"@{target_username}", hours_to_add, api_url)
            
            if success:
                results.append(f"✅ <b>{product_name}</b>: Extended by {hours_to_add} hours")
                
                # If API Claimer, also update the container expiry in DB
                if product == "api_claimer":
                    # Find active container and update expiry
                    container = deployed_apps_col.find_one({"username": target_username, "status": "active"})
                    if container:
                        current_expires = container.get("expires_at")
                        if current_expires:
                            if isinstance(current_expires, str):
                                current_expires = _parse_iso_datetime(current_expires)
                            if current_expires:
                                if current_expires.tzinfo is None:
                                    current_expires = current_expires.replace(tzinfo=timezone.utc)
                                # Extend from current expiry or now, whichever is later
                                now = datetime.now(timezone.utc)
                                if current_expires > now:
                                    new_expires = current_expires + timedelta(hours=hours_to_add)
                                else:
                                    new_expires = now + timedelta(hours=hours_to_add)
                                
                                # Update both collections
                                deployed_apps_col.update_one(
                                    {"app_name": container.get("app_name")},
                                    {"$set": {"expires_at": new_expires}}
                                )
                                api_subscriptions_col.update_one(
                                    {"app_name": container.get("app_name")},
                                    {"$set": {"expires_at": new_expires}}
                                )
                                results.append(f"   📦 Container expiry updated to: {new_expires.strftime('%Y-%m-%d %H:%M UTC')}")
            else:
                results.append(f"❌ <b>{product_name}</b>: Failed to extend")
                
        except Exception as e:
            logger.exception(f"Error extending {product_name} for {target_username}: {e}")
            results.append(f"❌ <b>{product_name}</b>: Error - {str(e)[:50]}")
    
    # Build result message
    result_text = (
        f"⏰ <b>Subscription Extension Result</b>\n\n"
        f"User: <code>@{target_username}</code>\n"
        f"Hours Added: <b>{hours_to_add}</b>\n\n"
    )
    result_text += "\n".join(results)
    
    # Try to notify the user
    if target_user_id:
        try:
            # Get new expiry from API for notification
            api_data = await asyncio.to_thread(get_active_users, API_CLAIMER_AUTH_URL if product_arg == "api_claimer" else 
                                                (FARMER_API_URL if product_arg == "farmer" else CLAIMER_API_URL))
            new_expiry_str = "N/A"
            if api_data and isinstance(api_data, dict):
                for u in api_data.get("active_users", []):
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
    
    await status_msg.edit(result_text, parse_mode="html")

# ================== BULK POINTS PURCHASE COMMAND ==================

@bot.on(events.NewMessage(pattern=r"^/buypoints$"))
async def buypoints_handler(event):
    user_id = event.sender_id
    
    # Reaction
    try:
        await bot(functions.messages.SendReactionRequest(
            peer=event.chat_id,
            msg_id=event.message.id,
            reaction=[types.ReactionEmoji(emoticon='💰')],
            add_to_recent=False
        ))
    except:
        pass
    
    # Get current balance
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
    
    # Extract package key
    data_str = event.data.decode()
    package_key = data_str.replace("bulk_", "")
    
    package = BULK_POINTS_PACKAGES.get(package_key)
    if not package:
        return await event.respond("Invalid package. Try again.")
    
    amount = package["amount"]
    points = package["points"]
    label = package["label"]
    
    # Save to session
    session = user_sessions.setdefault(user_id, {})
    session["bulk_points"] = points
    session["bulk_amount"] = amount
    session["bulk_label"] = label
    session["product"] = "bulk_points"  # Mark as bulk points purchase
    
    # Get current balance
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


@bot.on(events.CallbackQuery(data=b"back_buypoints"))
async def back_buypoints_handler(event):
    await event.answer()
    user_id = event.sender_id
    
    # Get current balance
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
    
    # Create invoice
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
    
    # Start payment wait task
    task = asyncio.create_task(
        wait_for_payment(
            user_id, 
            track_id, 
            label, 
            0,  # No hours for bulk points
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
        "• Chat Farmer (Automated chat farming)\n"
        "• API Claimer (Dedicated API container)\n\n"
        "Select a product to purchase or manage your account."
    )

    buttons = []
    # Purchase buttons (Product Selection)
    buttons.append([Button.inline("⚡ Buy Code Claimer", b"buy_product_claimer")])
    buttons.append([Button.inline("👨‍🌾 Buy Chat Farmer", b"buy_product_farmer")])
    buttons.append([Button.inline("🔌 Buy API Claimer", b"buy_product_api_claimer")])

    # Account Actions Row
    account_row = []
    if not first_time:
        account_row.append(Button.inline("✏️ Edit Username", b"edit_username"))
    account_row.append(Button.inline("🎁 Refer & Earn", b"menu_referral"))
    buttons.append(account_row)
    
    # Bulk Points Row
    buttons.append([Button.inline("💰 Buy Points", b"menu_buypoints")])
    
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

# --- BUY POINTS MENU HANDLER ---

@bot.on(events.CallbackQuery(data=b"menu_buypoints"))
async def menu_buypoints_handler(event):
    await event.answer()
    user_id = event.sender_id
    
    # Get current balance
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
        [Button.inline("🔙 Back", b"back_to_start")]
    ]
    
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
    buttons.append([Button.inline("🔌 Buy API Claimer", b"buy_product_api_claimer")])

    # Account Actions Row
    account_row = []
    if existing:
        account_row.append(Button.inline("✏️ Edit Username", b"edit_username"))
    account_row.append(Button.inline("🎁 Refer & Earn", b"menu_referral"))
    buttons.append(account_row)
    
    # Bulk Points Row
    buttons.append([Button.inline("💰 Buy Points", b"menu_buypoints")])
    
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
        "• Chat Farmer (Automated chat farming)\n"
        "• API Claimer (Dedicated API container)\n\n"
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
        [Button.inline("🔌 Buy API Claimer", b"buy_product_api_claimer")],
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
    elif "api_claimer" in data_str:
        product_type = "api_claimer"
        product_display = "API Claimer"
    
    session = user_sessions.setdefault(user_id, {})
    session["expecting_username"] = True
    session["product"] = product_type

    # API Claimer has different flow - needs both username AND session token
    if product_type == "api_claimer":
        text = (
            f"<b>Buy {product_display} — Step 1: Provide your Stake username</b>\n\n"
            "Send only the username. Examples:\n"
            "• <code>alice123</code>\n"
            "• <code>@alice123</code>\n\n"
            "Do NOT send profile links or screenshots.\n\n"
            "<i>After username confirmation, you'll need to provide your Stake API key.</i>"
        )
    else:
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
    
    # Search for active subscription in all APIs
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

    # Check API Claimer (if not found in others)
    if not active_details:
        data_api_claimer = await asyncio.to_thread(get_active_users, API_CLAIMER_AUTH_URL)
        if data_api_claimer:
            users = data_api_claimer.get("active_users", [])
            if isinstance(users, list):
                for u in users:
                    if u.get("username", "").lower().lstrip("@") == username.lower():
                        expires = _parse_iso_datetime(u.get("expires"))
                        if expires:
                            active_details = (API_CLAIMER_AUTH_URL, expires, "API Claimer")
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
        elif product_name == "API Claimer":
            refund_amount = remaining_hours * REFUND_RATE_API_CLAIMER_PER_HOUR
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
    product_name = session.get("term_product", "Unknown")
    
    if not username or not api_url:
        await event.edit("Session expired. Please try again.", buttons=[[Button.inline("🔙 Back", b"back_to_start")]])
        return
        
    # Execute Delete on API
    await event.edit("⏳ Deleting user from server...", parse_mode="html")
    
    success = await asyncio.to_thread(delete_user_api, username, api_url)
    
    if success:
        # If API Claimer, also stop the deployed container
        if product_name == "API Claimer":
            try:
                deployed_app = deployed_apps_col.find_one({"username": username, "status": "active"})
                if deployed_app:
                    app_name = deployed_app.get("app_name")
                    if app_name:
                        # Delete the container via API
                        delete_ok, delete_resp = await asyncio.to_thread(delete_deployed_app, app_name)
                        
                        # Mark as terminated in DB
                        deployed_apps_col.update_one(
                            {"app_name": app_name},
                            {"$set": {
                                "status": "terminated",
                                "terminated_at": datetime.now(timezone.utc),
                                "deletion_reason": "user_terminated"
                            }}
                        )
                        
                        # Also update api_subscriptions_col
                        api_subscriptions_col.update_one(
                            {"app_name": app_name},
                            {"$set": {
                                "status": "terminated",
                                "terminated_at": datetime.now(timezone.utc),
                                "deletion_reason": "user_terminated"
                            }}
                        )
            except Exception as e:
                logger.error(f"Failed to delete deployed app during termination: {e}")
        
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
            f"✅ <b>Subscription Terminated</b>\n\n"
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
    session.pop("term_product", None)

# ================== TEXT INPUT HANDLER ==================

@bot.on(events.NewMessage)
async def text_input_handler(event):
    user_id = event.sender_id
    session = user_sessions.setdefault(user_id, {})
    
    raw_text = event.raw_text.strip()

    # --- HANDLE SESSION TOKEN INPUT FOR API CLAIMER ---
    if session.get("expecting_session_token"):
        session["expecting_session_token"] = False
        
        # Validate session token (basic check - should be a long string)
        if len(raw_text) < 20:
            await event.respond(
                "❌ Invalid API key. Your Stake API key should be a long string.\n\n"
                "Please send a valid API key or click below to skip.",
                buttons=[[Button.inline("⏭️ Skip for now", b"skip_session_token")]]
            )
            return
        
        session["session_token"] = raw_text
        
        # Proceed to payment selection
        username_clean = session.get("username", "UNKNOWN")
        prod_name = "API Claimer"
        
        text = (
            f"<b>✅ API Key Saved!</b>\n\n"
            f"Username: <code>@{username_clean}</code>\n"
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
        
        await event.respond(text, parse_mode="html", buttons=buttons)
        return

    # Skip pattern matching for commands and short text
    if raw_text.startswith("/"):
        return
    
    # Username pattern check
    import re
    username_pattern = r"^[A-Za-z0-9_@]{3,51}$"
    if not re.match(username_pattern, raw_text):
        return

    username_clean = raw_text.lstrip('@')

    # --- TERMINATE OLD USERNAME FLOW ---
    if session.get("expecting_term_username"):
        session["expecting_term_username"] = False
        target_username = username_clean
        
        # Search for active subscription in all APIs (Manual Search)
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

        # Check API Claimer
        if not active_details:
            data_api_claimer = await asyncio.to_thread(get_active_users, API_CLAIMER_AUTH_URL)
            if data_api_claimer:
                users = data_api_claimer.get("active_users", [])
                if isinstance(users, list):
                    for u in users:
                        if u.get("username", "").lower().lstrip("@") == target_username.lower():
                            expires = _parse_iso_datetime(u.get("expires"))
                            if expires:
                                active_details = (API_CLAIMER_AUTH_URL, expires, "API Claimer")
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
            elif product_name == "API Claimer":
                refund_amount = remaining_hours * REFUND_RATE_API_CLAIMER_PER_HOUR
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

        # Emoji 🔄: 5375338737028841420
        await event.respond(f"🔄 Processing change from <code>@{old_username}</code> to <code>@{new_username}</code>...", parse_mode="html")

        # Call API (Tries both servers)
        try:
            resp = await asyncio.to_thread(rename_user_api, old_username, new_username)
            
            if isinstance(resp, dict) and resp.get("ok") is False:
                # Emoji ❌: 5273914604752216432
                await event.respond(f"❌ Rename API reported failure: {resp}\n\nLocal username not changed.", parse_mode="html")
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
            await event.respond(f"✅ Success! Username changed from <code>@{old_username}</code> to <code>@{new_username}</code>.", parse_mode="html")
            
        except Exception as e:
            logger.exception("Rename process failed.")
            # Emoji ❌: 5273914604752216432
            await event.respond(f"❌ Error during rename: {e}", parse_mode="html")
        
        return

    # --- STANDARD PURCHASE FLOW ---
    if not session.get("expecting_username"):
        return

    # Save as pending until user confirms
    session["pending_username"] = username_clean
    session["expecting_username"] = False
    
    # Determine product display name
    prod = session.get("product", "claimer")
    if prod == "api_claimer":
        prod_name = "API Claimer"
    elif prod == "farmer":
        prod_name = "Chat Farmer"
    else:
        prod_name = "Code Claimer"

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

@bot.on(events.CallbackQuery(data=b"skip_session_token"))
async def skip_session_token_handler(event):
    await event.answer()
    user_id = event.sender_id
    session = user_sessions.get(user_id)
    
    if not session:
        return await event.respond("Session expired. Restart with /start.")
    
    username_clean = session.get("username", "UNKNOWN")
    prod_name = "API Claimer"
    
    text = (
        f"<b>⚠️ API Key Skipped</b>\n\n"
        f"Username: <code>@{username_clean}</code>\n"
        f"Product: <b>{prod_name}</b>\n\n"
        f"<i>You can provide your API key later via support.</i>\n\n"
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
    
    await event.edit(text, parse_mode="html", buttons=buttons)

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
    if prod == "api_claimer":
        prod_name = "API Claimer"
    elif prod == "farmer":
        prod_name = "Chat Farmer"
    else:
        prod_name = "Code Claimer"

    # === API CLAIMER: ASK FOR SESSION TOKEN ===
    if prod == "api_claimer":
        text = (
            f"Stake username saved: <code>@{username_clean}</code>\n"
            f"Product: <b>{prod_name}</b>\n\n"
            "<b>Step 2: Provide your Stake API Key</b>\n\n"
            "To deploy your dedicated API container, we need your Stake API key.\n\n"
            "<i>Your API key is a long string from your Stake account settings.</i>\n\n"
            "Please paste your API key now:"
        )
        buttons = [
            [Button.inline("⏭️ Skip for now", b"skip_session_token")],
            [Button.inline("❌ Cancel", b"back_to_start")]
        ]
        session["expecting_session_token"] = True
        try:
            await event.edit(text, parse_mode="html", buttons=buttons)
        except:
            await event.respond(text, parse_mode="html", buttons=buttons)
        return

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
    if prod == "api_claimer":
        prod_name = "API Claimer"
    elif prod == "farmer":
        prod_name = "Chat Farmer"
    else:
        prod_name = "Code Claimer"

    header = f"⚡ <b>{prod_name} Plans</b>"
    
    # Determine if it is Weekend (Sat=5, Sun=6)
    now = datetime.now(timezone.utc)
    is_weekend = now.weekday() in [5, 6]

    # Emoji 💳: 6129870117619634982
    text = (
        f"{header}\n"
        "💳 <b>Select a Plan</b>\n\n"
        "Plans:\n"
    )
    
    buttons = []
    
    if prod == "api_claimer":
        # --- API CLAIMER PLANS ---
        p1d = PLANS_API_CLAIMER["1d"]
        p3d = PLANS_API_CLAIMER["3d"]
        p7d = PLANS_API_CLAIMER["7d"]
        p14d = PLANS_API_CLAIMER["14d"]
        p30d = PLANS_API_CLAIMER["30d"]

        # Text listing
        text += f"• {p1d['label']:<8} — {p1d['amount']} USDT\n"
        text += f"• {p3d['label']:<8} — {p3d['amount']} USDT\n"
        text += f"• {p7d['label']:<8} — {p7d['amount']} USDT\n"
        text += f"• {p14d['label']:<8} — {p14d['amount']} USDT\n"
        text += f"• {p30d['label']:<8} — {p30d['amount']} USDT\n"

        # API Claimer plan buttons
        buttons.append([
            Button.inline(f"1d — {p1d['amount']} $", b"plan_api_1d"),
            Button.inline(f"3d — {p3d['amount']} $", b"plan_api_3d"),
        ])
        buttons.append([
            Button.inline(f"7d — {p7d['amount']} $", b"plan_api_7d"),
            Button.inline(f"14d — {p14d['amount']} $", b"plan_api_14d"),
        ])
        buttons.append([
            Button.inline(f"30d — {p30d['amount']} $", b"plan_api_30d"),
        ])
        
    elif prod == "farmer":
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

    plan_key_raw = event.data.decode().split("_", 1)[1]
    
    # Check Product
    prod = session.get("product", "claimer")

    # Handle API Claimer Plans
    if plan_key_raw.startswith("api_"):
        plan_key = plan_key_raw.replace("api_", "")
        plan = PLANS_API_CLAIMER.get(plan_key)
        if not plan:
            return await event.respond("Invalid plan. Try again.")
        amount = plan["amount"]
        label = plan["label"]
        hours = plan["hours"]
        
    # Handle Special Weekend Plan
    elif plan_key_raw == "weekend":
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
        plan_key = "weekend"
        
    elif plan_key_raw == "1d":
        # Handle 1d Plan (Split logic)
        if prod == "farmer":
            plan = PLAN_1D_FARMER
        elif prod == "api_claimer":
            plan = PLANS_API_CLAIMER["1d"]
        else:
            plan = PLAN_1D_CLAIMER
        amount = plan["amount"]
        label = plan["label"]
        hours = plan["hours"]
        plan_key = "1d"
        
    elif plan_key_raw in PLANS_FARMER_SHORT:
        # Farmer specific short plans
        plan = PLANS_FARMER_SHORT[plan_key_raw]
        amount = plan["amount"]
        label = plan["label"]
        hours = plan["hours"]
        plan_key = plan_key_raw
        
    else:
        # Standard Long Term Plans (2d, 4d, 7d)
        plan = PLANS_LONG_TERM.get(plan_key_raw)
        if not plan:
            return await event.respond("Invalid plan. Try again.")
        amount = plan["amount"]
        label = plan["label"]
        if prod == "claimer" and plan_key_raw == "2d":
            label = "2 Days (48h)"
        hours = plan["hours"]
        plan_key = plan_key_raw

    # Save selection to session
    session["selected_plan_key"] = plan_key
    session["selected_amount"] = amount
    session["selected_label"] = label
    session["selected_hours"] = hours

    # === PAYMENT METHOD SELECTION SCREEN ===
    
    user_data = users_col.find_one({"user_id": user_id})
    user_points = user_data.get("points", 0.0) if user_data else 0.0
    
    if prod == "api_claimer":
        prod_name = "API Claimer"
    elif prod == "farmer":
        prod_name = "Chat Farmer"
    else:
        prod_name = "Code Claimer"

    # Emoji 🛒: 5226656353744862682
    # Emoji 💰 (Alt/Gold): 6325444137797554944
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
    
    prod = session.get("product", "claimer")
    if prod == "api_claimer":
        api_url = API_CLAIMER_AUTH_URL
        prod_name = "API Claimer"
        forwards = [API_CLAIMER_FORWARD_1, API_CLAIMER_FORWARD_2, API_CLAIMER_FORWARD_3]
    elif prod == "farmer":
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
    await event.edit(f"🔄 Activating {prod_name} subscription...", parse_mode="html")
    
    # Activate
    activation_ok = await asyncio.to_thread(activate_subscription, f"@{username_clean}", hours, api_url)
    
    if activation_ok:
        # === API CLAIMER: DEPLOY CONTAINER WITH ANIMATION ===
        deploy_message = ""
        if prod == "api_claimer":
            session_token = session.get("session_token")
            if session_token:
                # Deploy container with animated progress
                app_name = generate_unique_app_name(username_clean)
                deploy_ok, deploy_resp = await animate_deploy_progress(user_id, app_name, session_token)
                
                if deploy_ok:
                    expires_at = datetime.now(timezone.utc) + timedelta(hours=hours)
                    web_url = deploy_resp.get("web_url", f"https://{app_name}.herokuapp.com")
                    
                    # Save deployed app info to DB with comprehensive tracking
                    deployed_apps_col.insert_one({
                        "user_id": user_id,
                        "username": username_clean,
                        "app_name": app_name,
                        "session_token": session_token,
                        "deployed_at": datetime.now(timezone.utc),
                        "expires_at": expires_at,
                        "status": "active",
                        "web_url": web_url,
                        "region": API_CLAIMER_REGION,
                        "product_type": "api_claimer"
                    })
                    
                    # Also save to api_subscriptions collection for comprehensive tracking
                    api_subscriptions_col.update_one(
                        {"user_id": user_id, "username": username_clean, "product_type": "api_claimer"},
                        {
                            "$set": {
                                "user_id": user_id,
                                "username": username_clean,
                                "product_type": "api_claimer",
                                "app_name": app_name,
                                "api_url": api_url,
                                "expires_at": expires_at,
                                "status": "active",
                                "session_token": session_token,
                                "web_url": web_url,
                                "updated_at": datetime.now(timezone.utc)
                            }
                        },
                        upsert=True
                    )
                    
                    deploy_message = (
                        f"\n\n✅ <b>Container Deployed!</b>\n"
                        f"App Name: <code>{app_name}</code>\n"
                        f"Region: <b>{API_CLAIMER_REGION.upper()}</b>\n"
                        f"URL: <code>{web_url}</code>"
                    )
                else:
                    error_msg = deploy_resp.get("message", deploy_resp.get("error", "Unknown error"))
                    deploy_message = (
                        f"\n\n⚠️ <b>Container deployment failed.</b>\n"
                        f"Error: {error_msg}\n"
                        f"Your subscription is active but container was not deployed.\n"
                        f"Please contact support with your API key."
                    )
                    logger.error(f"Deploy failed for user {user_id}: {deploy_resp}")
            else:
                deploy_message = (
                    f"\n\n⚠️ <b>No API key provided.</b>\n"
                    f"Your subscription is active but container was not deployed.\n"
                    f"Please contact support with your API key to deploy manually."
                )
        
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
            f"✅ <b>Paid with Points!</b>\n\n"
            f"Your <b>{prod_name} - {label}</b> subscription is activated.\n"
            f"Deducted: <b>{amount} Points</b>\n"
            f"Remaining: <b>{user_points - amount:.2f} Points</b>\n"
            f"Duration: <b>{hours} hours</b>."
            f"{deploy_message}",
            parse_mode="html"
        )
    else:
        # Refund on failure
        users_col.update_one({"user_id": user_id}, {"$inc": {"points": amount}})
        # Emoji ❌: 5273914604752216432
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
    
    prod = session.get("product", "claimer")
    if prod == "api_claimer":
        prod_name = "API Claimer"
    elif prod == "farmer":
        prod_name = "Chat Farmer"
    else:
        prod_name = "Code Claimer"

    if user_id in user_tasks:
        old = user_tasks[user_id]
        if not old.done():
            old.cancel()

    # Emoji 🔄: 5375338737028841420
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
    
    # Emoji ✅: 5039793437776282663
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
    await event.reply(f"✅ Broadcast sent to {total} users.", parse_mode="html")

# ================== MAIN ==================

def main():
    logger.info("Stake Payment Bot (Multi-Product with API Claimer + Auto-Delete + Bulk Points + Stats + Extend) is running...")
    try:
        loop = asyncio.get_event_loop()
        # Only start the main loop - container cleanup is now integrated
        loop.create_task(check_active_users_loop())
    except Exception as e:
        logger.exception(f"Failed to schedule background tasks: {e}")

    bot.run_until_disconnected()

if __name__ == "__main__":
    main()
