import asyncio
import logging
import time
import requests
import random
import string
import json
import traceback
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient, events, Button, functions, types
from telethon.errors import MessageNotModifiedError, FloodWaitError
from pymongo import MongoClient

# ================== CONFIG ==================

API_ID = 29568441
API_HASH = "b32ec0fb66d22da6f77d355fbace4f2a"
BOT_TOKEN = "8573345038:AAEppbd1tFOP5NEq0AzJcjBM9moDkves5eQ"

SUPPORT_CHAT_LINK = "https://t.me/Rabit0505"
UPDATES_CHANNEL_LINK = "https://t.me/Rabit0505"
UPI_DM_LINK = "https://t.me/Rabit0505"

# --- Code Claimer Assets ---
CLAIMER_API_URL = "https://code-auth1-4df5f5b73886.herokuapp.com"
# Forwards for Claimer (Proof channels)
CLAIMER_FORWARD_1 = ("rebateautomations", 2)  # 1st: the claimer file
CLAIMER_FORWARD_2 = ("rebateautomations", 3)  # 2nd: setup video

# --- API Claimer Assets ---
API_CLAIMER_AUTH_URL = "https://code-auth1-4df5f5b73886.herokuapp.com"  # Same as Code Claimer auth

# DEPLOY URLS (Load Distribution Pool)
# The bot will deploy a single container using the first deployer that hasn't reached its limit.
API_CLAIMER_DEPLOYERS = [
    {
        "deploy_id": 1,
        "url": "https://api-claimer-1-7d4b61900826.herokuapp.com",
        "token": "fuck1234",
        "limit": 99
    },
    {
        "deploy_id": 2,
        "url": "https://api-claimer-2-cfed7420dc82.herokuapp.com",
        "token": "fuck1234",
        "limit": 99
    },
    {
        "deploy_id": 3,
        "url": "https://api-claimer-3-aeab2d378e5b.herokuapp.com",
        "token": "fuck1234",
        "limit": 0
    },
    {
        "deploy_id": 4,
        "url": "https://api-claimer-4-7b21e2a8515b.herokuapp.com",
        "token": "fuck1234",
        "limit": 0
    }
]

API_CLAIMER_REGION = "eu"  # Deploy region

# Mirror site
API_CLAIMER_MIRROR_SITE = "stake.bet"

# Start image
START_IMAGE_URL = "https://rebatestarting.vibeshiftbots.workers.dev/"

# MongoDB
MONGO_URL = "mongodb+srv://kustbotsweb_db_user:z7YqNFmFOvVHKl4B@kust-payments.hiin3lu.mongodb.net/?appName=kust-payments"
mongo = MongoClient(MONGO_URL)
db = mongo["kustfarm"]
users_col = db["users"]

# Track deployed apps to avoid conflicts
deployed_apps_col = db["deployed_apps"]

# Track API subscriptions for better management
api_subscriptions_col = db["api_subscriptions"]

# Bot owner
BOT_OWNER_ID = 7618467489

# Admin Debug Reporter ID
ADMIN_DEBUG_ID = 8673494392

# OxaPay API
OXAPAY_API_KEY = "VXJ5BQ-I9TNK9-RQUZWH-MWLSC4"
OXAPAY_API_BASE = "https://api.oxapay.com"

# Active users checker settings
ACTIVE_USERS_POLL_INTERVAL = 60   # seconds between polls (1 minute)
REMINDER_THRESHOLD_MINUTES = 10   # notify when <= 10 minutes remain

# --- PRICING PLANS ---

# Plans (Shared pricing for both Code Claimer and API Claimer)
PLANS = {
    "2d":   {"label": "2 Days Weekend",           "amount": 6.0,   "hours": 48},
    "7d":   {"label": "1 Week",                   "amount": 15.0,  "hours": 168},
    "30d":  {"label": "1 Month",                  "amount": 50.0,  "hours": 720},
    "120d": {"label": "3 Months (+1 Month Free)", "amount": 140.0, "hours": 2880},
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
REFUND_RATE_CLAIMER_PER_HOUR = 0.0 # Approx $0.15 per hour
REFUND_RATE_API_CLAIMER_PER_HOUR = 0.0  # Approx $0.10 per hour

PAYMENT_TIMEOUT = 15 * 60
POLL_INTERVAL = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("rebate_buy_bot")

bot = TelegramClient("rebate_buy_bot_session", API_ID, API_HASH).start(bot_token=BOT_TOKEN)

user_sessions = {}
user_tasks = {}
bot_username = None # Will be set on startup

# keep track of reminders sent to avoid duplicates: { key: True }
_reminder_sent = {}

# Track expired cleanup to avoid duplicates: { key: True }
_expired_cleanup_sent = {}

# Track pre-expiry container deletion to avoid duplicates: { key: True }
_pre_expiry_deletion_sent = {}

# ================== GLOBAL ERROR REPORTING ==================

async def report_error_to_admin(e, context=""):
    """Async global error reporter directly to telegram ID."""
    try:
        tb_str = traceback.format_exc()
        error_msg = f"⚠️ <b>BOT ERROR</b> ⚠️\n\n<b>Context:</b> {context}\n<b>Error:</b> <code>{e}</code>\n\n<b>Traceback:</b>\n<code>{tb_str[-3000:]}</code>"
        await bot.send_message(ADMIN_DEBUG_ID, error_msg, parse_mode="html")
    except Exception as inner_e:
        logger.error(f"Failed to report error to admin: {inner_e}")

def sync_report_error_to_admin(e, context=""):
    """Thread-safe synchronous wrapper for the error reporter."""
    try:
        asyncio.run_coroutine_threadsafe(report_error_to_admin(e, context), bot.loop)
    except Exception as ex:
        logger.error(f"Failed to schedule sync error report: {ex}")

# ================== SAFE RATE LIMIT HANDLER ==================

async def safe_edit(obj, *args, **kwargs):
    """
    Safely handles edits for events, messages, or the bot client.
    Handles FloodWaitError gracefully and silently drops MessageNotModifiedError.
    If the edit target fails, attempts a fallback to standard respond().
    """
    retries = 3
    for attempt in range(retries):
        try:
            if hasattr(obj, 'edit'):
                return await obj.edit(*args, **kwargs)
            else:
                return await obj.edit_message(*args, **kwargs)
        except MessageNotModifiedError:
            return None
        except FloodWaitError as e:
            logger.warning(f"FloodWaitError: waiting {e.seconds}s before editing.")
            if attempt < retries - 1:
                await asyncio.sleep(e.seconds)
            else:
                await report_error_to_admin(e, "safe_edit FloodWaitError limit reached")
        except Exception as e:
            logger.warning(f"Edit failed: {e}. Attempting respond fallback...")
            if hasattr(obj, 'respond'):
                try:
                    return await obj.respond(*args, **kwargs)
                except Exception as inner_e:
                    await report_error_to_admin(inner_e, "safe_edit respond fallback failed")
            else:
                await report_error_to_admin(e, "safe_edit unknown error fallback")
            break

# ================== BATCH MANAGEMENT ==================

def get_available_deployer():
    """Find the first deployer that hasn't reached its deployment limit."""
    for deployer in API_CLAIMER_DEPLOYERS:
        count = deployed_apps_col.count_documents({
            "deploy_url": deployer["url"],
            "status": "active"
        })
        if count < deployer["limit"]:
            return deployer
    return None

# ================== ERROR MESSAGE HELPER ==================

def get_user_friendly_deploy_error(error_data):
    """
    Check if the deployment error is due to app limit being reached.
    Returns a user-friendly error message if so, otherwise returns the original error.
    """
    error_msg = ""
    if isinstance(error_data, dict):
        error_msg = error_data.get("message", error_data.get("error", ""))
        if not error_msg:
            error_msg = str(error_data)
    else:
        error_msg = str(error_data)
    
    # Check for Heroku app limit error
    if "app limit" in error_msg.lower() or "reached your app limit" in error_msg.lower():
        return "All slots are already full in this batch."
    
    # Check for 422 status with invalid_params
    if "422" in error_msg and "invalid_params" in error_msg:
        return "All slots are already full in this batch."
    
    # Return original error if not app limit
    return error_msg if error_msg else "Unknown error"

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
        sync_report_error_to_admin(e, f"[ACTIVATE] Failed activation API for {username_with_at}")
        return False

def delete_deployed_app(app_name: str):
    """
    Delete a deployed Heroku app/container via the deploy API.
    Checks the DB to find the exact deploy_url, otherwise tries all known deployers.
    """
    success_any = False
    last_resp = {}
    urls_to_try = []

    app_doc = deployed_apps_col.find_one({"app_name": app_name})

    if app_doc and app_doc.get("deploy_url"):
        target_url = app_doc["deploy_url"]
        target_token = "fuck1234"  # Default fallback
        for d in API_CLAIMER_DEPLOYERS:
            if target_url == d["url"]:
                target_token = d["token"]
                break
        urls_to_try.append((target_url, target_token))
    else:
        # Fallback: try all urls in all deployers
        for d in API_CLAIMER_DEPLOYERS:
            urls_to_try.append((d["url"], d["token"]))

    for deploy_url, auth_token in urls_to_try:
        try:
            url = f"{deploy_url}/apps/{app_name}"
            headers = {
                "Authorization": f"Bearer {auth_token}"
            }
            
            logger.info(f"[DELETE_APP] Trying to delete container {app_name} via {deploy_url}")
            
            r = requests.delete(url, headers=headers, timeout=30)
            
            if r.status_code == 200:
                logger.info(f"[DELETE_APP] Successfully deleted app: {app_name} via {deploy_url}")
                success_any = True
                last_resp = r.json()
                break  # Stop trying other URLs if successful
            else:
                response_text = r.text.lower()
                if "not_found" in response_text or "\"id\":\"not_found\"" in response_text or "couldn't find that app" in response_text:
                    logger.info(f"[DELETE_APP] App {app_name} already deleted (not found) via {deploy_url}")
                    success_any = True
                    last_resp = {"status": "already_deleted", "message": "App was already deleted"}
                    break  # It's gone, break out
                else:
                    logger.error(f"[DELETE_APP] Failed via {deploy_url}: {r.status_code} - {r.text}")
                    last_resp = {"error": f"HTTP {r.status_code}: {r.text}"}
                    
        except Exception as e:
            logger.exception(f"[DELETE_APP] Exception deleting app {app_name} via {deploy_url}: {e}")
            sync_report_error_to_admin(e, f"[DELETE_APP] Exception deleting app {app_name} via {deploy_url}")
            last_resp = {"error": str(e)}

    return success_any, last_resp

def generate_unique_app_name(username: str, suffix_tag: str = ""):
    """
    Generate a unique app name based on username.
    Format: api-cl-{username}
    If taken, append random alphanumeric character.
    """
    # Clean username - remove @ and special characters
    clean_user = username.lstrip("@").lower()
    clean_user = ''.join(c for c in clean_user if c.isalnum() or c == '_')
    
    # Limit length
    if len(clean_user) > 18:
        clean_user = clean_user[:18]
    
    base_name = f"api-cl-{clean_user}{suffix_tag}"
    
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

def build_api_claimer_status_url(username: str):
    clean_user = username.lstrip("@").strip()
    return f"https://rebate.vibeshiftbots.workers.dev/api-cl?user={clean_user}"

def deploy_api_container(session_token: str, app_name: str, deploy_url: str, auth_token: str, mirror_site: str, progress_callback=None):
    """
    Deploy API container for the user.
    Calls the given deploy_url with the session token, app name, and mirror_site env var.
    Returns SSE streaming response and parses the final status.
    """
    try:
        url = f"{deploy_url}/deploy"
        headers = {
            "Authorization": f"Bearer {auth_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream"
        }
        payload = {
            "session_token": session_token,
            "app_name": app_name,
            "MIRROR_SITE": mirror_site
        }
        
        logger.info(f"[DEPLOY] Deploying container: {app_name} via {deploy_url} (MIRROR_SITE={mirror_site})")
        logger.info(f"[DEPLOY] Payload: session_token=***, app_name={app_name}, MIRROR_SITE={mirror_site}")
        
        # Use stream=True to handle SSE response. Increased timeout to 60 minutes (3600 seconds).
        r = requests.post(url, headers=headers, json=payload, stream=True, timeout=3600)
        
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
                    if progress_callback:
                        try:
                            progress_callback(status, message, progress)
                        except Exception as cb_error:
                            logger.warning(f"[DEPLOY] Progress callback failed: {cb_error}")
                    
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
        
    except requests.exceptions.Timeout as e:
        logger.error(f"[DEPLOY] Deploy timeout for {app_name}")
        sync_report_error_to_admin(e, f"[DEPLOY] Deploy timeout for {app_name}")
        return False, {"error": "Deployment timed out"}
    except Exception as e:
        logger.exception(f"[DEPLOY] Failed to deploy container {app_name}: {e}")
        sync_report_error_to_admin(e, f"[DEPLOY] Failed to deploy container {app_name}")
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

# ================== SINGLE DEPLOY PROGRESS ANIMATION ==================

async def animate_deploy_progress(user_id: int, username_clean: str, session_token: str):
    """
    Deploy A SINGLE container using an available load-balanced deployer.
    Returns (app_name, ok, res, deploy_url, auth_token).
    """
    deployer = get_available_deployer()
    if not deployer:
        msg = "❌ All deployment slots are currently full across all deployers. Please contact support to arrange availability."
        await bot.send_message(user_id, msg)
        return ("app_1", False, {"error": "All deployers full"}, "", "")

    deploy_url = deployer["url"]
    auth_token = deployer["token"]

    app_name = generate_unique_app_name(username_clean)

    progress_state = {
        "message": "Starting...", "progress": 0, "done": False, "success": False, "result": {}
    }

    # Send initial progress message
    def render_text():
        s = progress_state
        icon = "✅" if (s["done"] and s["success"]) else ("❌" if (s["done"] and not s["success"]) else "🔄")
        return (
            f"🚀 <b>Deploying your API Rebate Claimer...</b>\n\n"
            f"{icon} <b>Container</b> — <code>{app_name}</code> [{API_CLAIMER_MIRROR_SITE}]\n"
            f"   Progress: <b>{s['progress']}%</b> | {s['message']}\n\n"
            f"Region: <b>{API_CLAIMER_REGION.upper()}</b> | Deployer: <b>#{deployer['deploy_id']}</b>"
        )

    progress_msg = await bot.send_message(user_id, render_text(), parse_mode="html")

    last_rendered = None

    def make_cb():
        def cb(status, message, progress):
            progress_state["message"] = message or progress_state["message"]
            if progress is not None:
                progress_state["progress"] = progress
        return cb

    async def update_display(force=False):
        nonlocal last_rendered
        key = (progress_state["progress"], progress_state["message"], progress_state["done"])
        if not force and key == last_rendered:
            return
        await safe_edit(bot, user_id, progress_msg.id, render_text(), parse_mode="html")
        last_rendered = key

    # Launch deployment in thread
    task = asyncio.create_task(
        asyncio.to_thread(
            deploy_api_container,
            session_token, app_name, deploy_url, auth_token,
            API_CLAIMER_MIRROR_SITE, make_cb()
        )
    )

    while not task.done():
        await asyncio.wait([task], timeout=1.0)
        await update_display()

    try:
        ok, res = task.result()
    except Exception as e:
        ok, res = False, {"error": str(e)}
        await report_error_to_admin(e, f"animate_deploy_progress task execution for {username_clean}")
        
    progress_state["done"] = True
    progress_state["success"] = ok
    progress_state["result"] = res
    if ok:
        progress_state["progress"] = 100
        progress_state["message"] = "Deployed successfully!"
    else:
        progress_state["message"] = get_user_friendly_deploy_error(res)

    # Final forced update
    await update_display(force=True)

    status_url = build_api_claimer_status_url(username_clean)

    if ok:
        final_text = f"✅ Deployed successfully! (Deployer #{deployer['deploy_id']})\n\nDashboard URL: {status_url}"
        buttons = [[Button.url("📊 Dashboard", status_url)]]
    else:
        final_text = "⚠️ Deployment failed. Please contact support."
        buttons = [[Button.url("🛠 Contact Support", SUPPORT_CHAT_LINK)]]

    await safe_edit(bot, user_id, progress_msg.id, final_text, parse_mode="html", buttons=buttons)

    return app_name, ok, res, deploy_url, auth_token


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
        if product_type == "api_claimer":
            api_url = API_CLAIMER_AUTH_URL
            product_name = "API Rebate Claimer"
            forwards = []
        else:
            api_url = CLAIMER_API_URL
            product_name = "Code Rebate Claimer"
            forwards = [CLAIMER_FORWARD_1, CLAIMER_FORWARD_2]

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
                        await report_error_to_admin(e, f"wait_for_payment referral reward {referrer_id}")

                # === API CLAIMER SPECIFIC: SINGLE DEPLOY CONTAINER ===
                if product_type == "api_claimer":
                    session_token = session.get("session_token")
                    if session_token:
                        now_dt = datetime.now(timezone.utc)
                        expires_at = now_dt + timedelta(hours=hours)

                        deploy_result = await animate_deploy_progress(
                            user_id, username_clean, session_token
                        )

                        app_name, ok, res, dep_url, auth_tok = deploy_result

                        web_url = res.get("web_url", f"https://{app_name}.herokuapp.com") if ok else ""
                        deployed_apps_col.insert_one({
                            "user_id": user_id,
                            "username": username_clean,
                            "app_name": app_name,
                            "session_token": session_token,
                            "mirror_site": API_CLAIMER_MIRROR_SITE,
                            "deploy_url": dep_url,
                            "deployed_at": now_dt,
                            "expires_at": expires_at,
                            "status": "active" if ok else "deploy_failed",
                            "web_url": web_url,
                            "region": API_CLAIMER_REGION,
                            "product_type": "api_claimer"
                        })

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
                                    "updated_at": now_dt
                                }
                            },
                            upsert=True
                        )
                    else:
                        await bot.send_message(
                            user_id,
                            f"⚠️ <b>API key not found.</b>\nContact support to deploy your container manually.",
                            parse_mode="html",
                            buttons=[[Button.url("🛠 Contact Support", SUPPORT_CHAT_LINK)]]
                        )
                else:
                    await bot.send_message(
                        user_id,
                        f"✅ Payment confirmed!\n\n"
                        f"Your <b>{product_name} — {plan_label}</b> subscription is activated.\n"
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
                        await report_error_to_admin(e, f"Forward error for {chat} msg {msg_id}")

                if not activation_ok:
                    logger.warning(f"Activation API returned failure for @{username_clean} on {product_name} after payment {track_id}")

                return True

            if status in ("expired", "cancelled", "cancel", "failed"):
                await bot.send_message(user_id, f"❌ Invoice for {product_name} ({plan_label}) expired or cancelled.", parse_mode="html")
                return False

        except Exception as e:
            logger.exception(f"Invoice query error for track {track_id}: {e}")
            await report_error_to_admin(e, f"wait_for_payment invoice query {track_id}")

    # timeout reached
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
        sync_report_error_to_admin(e, f"get_active_users from {api_url}")
        return None

def rename_user_api(old_username: str, new_username: str):
    """
    Attempts to rename user on the Claimer API.
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
            sync_report_error_to_admin(e, f"rename_user_api to {ep}")
            
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
             sync_report_error_to_admin(e2, f"delete_user GET fallback on {api_url}")
    return False

# ================== ACTIVE USERS CHECKER (background) ==================

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
    # Try additional formats sometimes returned by APIs
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(s, fmt)
        except Exception:
            pass
    logger.warning(f"[PARSE_DT] Could not parse datetime string: {s!r}")
    return None

def _extract_active_users_list(data):
    """
    Robustly extract the list of active users from various API response formats.
    Handles: {"active_users": [...]}, {"users": [...]}, {"data": [...]}, or direct list.
    """
    if not data:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Try common key names
        for key in ("active_users", "users", "data", "result", "results"):
            val = data.get(key)
            if isinstance(val, list):
                return val
    return []

async def check_active_users_loop():
    await asyncio.sleep(5)
    logger.info("Active users reminder loop started (60s interval, 10min reminder, auto-delete containers).")
    
    api_sources = [
        {"name": "Code Rebate Claimer", "url": CLAIMER_API_URL},
        {"name": "API Rebate Claimer",  "url": API_CLAIMER_AUTH_URL},
    ]

    while True:
        try:
            now = datetime.now(timezone.utc)

            # ===================================================================
            # PHASE 1: CHECK ACTIVE USERS FROM EACH API — REMINDERS + PRE-EXPIRY
            # ===================================================================
            for source in api_sources:
                api_name = source["name"]
                api_url  = source["url"]

                try:
                    data = await asyncio.to_thread(get_active_users, api_url)
                except Exception as e:
                    logger.error(f"[REMINDER] get_active_users failed for {api_name}: {e}")
                    await report_error_to_admin(e, f"[REMINDER] get_active_users failed for {api_name}")
                    continue

                if not data:
                    logger.debug(f"[REMINDER] No data returned from {api_name}")
                    continue

                users = _extract_active_users_list(data)

                if not users:
                    logger.debug(f"[REMINDER] No active users found for {api_name}")
                    continue

                logger.debug(f"[REMINDER] {api_name}: {len(users)} active user(s) to check")

                for entry in users:
                    try:
                        expires_raw = entry.get("expires") or entry.get("expiry") or entry.get("expire_at") or entry.get("expires_at")
                        username    = entry.get("username") or entry.get("user") or entry.get("name")

                        if not expires_raw or not username:
                            logger.debug(f"[REMINDER] Skipping entry missing expires/username: {entry}")
                            continue

                        expires_dt = _parse_iso_datetime(str(expires_raw))
                        if not expires_dt:
                            logger.warning(f"[REMINDER] Could not parse expiry for {username}: {expires_raw!r}")
                            continue

                        # Make timezone-aware
                        if expires_dt.tzinfo is None:
                            expires_dt = expires_dt.replace(tzinfo=timezone.utc)

                        time_left    = expires_dt - now
                        seconds_left = time_left.total_seconds()
                        minutes_left = seconds_left / 60.0

                        username_clean = username.lstrip("@").strip()

                        # Fetch user_id from DB for DM
                        rec     = users_col.find_one({"username": username_clean})
                        user_id = rec.get("user_id") if rec else None

                        # -------------------------------------------------------
                        # REMINDER: send when <= 10 minutes left (and > 0 seconds)
                        # Key is per-username + api + expiry so it fires once only
                        # -------------------------------------------------------
                        if 0 < seconds_left <= (REMINDER_THRESHOLD_MINUTES * 60):
                            reminder_key = (
                                f"reminder_{username_clean.lower()}"
                                f"_{api_name}"
                                f"_{expires_dt.strftime('%Y%m%d%H%M')}"  # minute-precision → stable key
                            )

                            if not _reminder_sent.get(reminder_key):
                                logger.info(
                                    f"[REMINDER] {api_name} — @{username_clean} — "
                                    f"{minutes_left:.1f} min left — sending reminder"
                                )

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
                                            [Button.inline("🔄 Renew Subscription", b"buy_sub")],
                                            [Button.url("🛠 Contact Support", SUPPORT_CHAT_LINK)],
                                        ]
                                        await bot.send_message(
                                            user_id,
                                            rem_text,
                                            parse_mode="html",
                                            buttons=buttons
                                        )
                                        logger.info(
                                            f"[REMINDER] ✅ Sent to user_id={user_id} "
                                            f"(@{username_clean}) for {api_name}"
                                        )
                                    except Exception as e:
                                        logger.exception(
                                            f"[REMINDER] ❌ Failed to send DM to {username_clean}: {e}"
                                        )
                                        await report_error_to_admin(e, f"[REMINDER] Failed to send DM to {username_clean}")
                                else:
                                    logger.warning(
                                        f"[REMINDER] @{username_clean} not found in DB — "
                                        f"cannot send reminder for {api_name}"
                                    )

                                # Mark as sent regardless so we don't retry every poll
                                _reminder_sent[reminder_key] = True

                        # -------------------------------------------------------
                        # PRE-EXPIRY CONTAINER DELETION: only for API Claimer
                        # -------------------------------------------------------
                        if api_name == "API Rebate Claimer" and -300 < seconds_left <= 60:
                            deployed_apps = list(deployed_apps_col.find({
                                "username": username_clean,
                                "status": "active"
                            }))

                            for deployed_app in deployed_apps:
                                app_name = deployed_app.get("app_name")
                                if not app_name:
                                    continue

                                deletion_key = f"pre_expiry_{app_name}"
                                if _pre_expiry_deletion_sent.get(deletion_key):
                                    continue

                                logger.info(f"[PRE_EXPIRY_DELETE] Deleting container: {app_name}")
                                delete_ok, delete_resp = await asyncio.to_thread(
                                    delete_deployed_app, app_name
                                )

                                if delete_ok:
                                    deployed_apps_col.update_one(
                                        {"app_name": app_name},
                                        {"$set": {
                                            "status": "expired_deleted",
                                            "deleted_at": now,
                                            "deletion_reason": "subscription_expired"
                                        }}
                                    )
                                    api_subscriptions_col.update_many(
                                        {"$or": [{"app_name_1": app_name}, {"app_name_2": app_name}, {"app_name": app_name}]},
                                        {"$set": {
                                            "status": "expired_deleted",
                                            "deleted_at": now,
                                            "deletion_reason": "subscription_expired"
                                        }}
                                    )
                                    logger.info(f"[PRE_EXPIRY_DELETE] ✅ Deleted container: {app_name}")
                                else:
                                    logger.error(
                                        f"[PRE_EXPIRY_DELETE] ❌ Failed to delete {app_name}: {delete_resp}"
                                    )

                                _pre_expiry_deletion_sent[deletion_key] = True

                            # One expiry notification per user (after all containers processed)
                            notify_key = (
                                f"expiry_notify_{username_clean}"
                                f"_{expires_dt.strftime('%Y%m%d%H%M')}"
                            )
                            if not _pre_expiry_deletion_sent.get(notify_key) and deployed_apps:
                                notify_user_id = deployed_apps[0].get("user_id") or user_id
                                if notify_user_id:
                                    try:
                                        await bot.send_message(
                                            notify_user_id,
                                            f"⏰ <b>Subscription Expired</b>\n\n"
                                            f"Your API Rebate Claimer subscription has expired.\n"
                                            f"All containers have been automatically stopped.\n\n"
                                            f"Renew to get new containers deployed.",
                                            parse_mode="html",
                                            buttons=[
                                                [Button.inline("🔄 Renew Now", b"buy_product_api_claimer")],
                                                [Button.url("🛠 Contact Support", SUPPORT_CHAT_LINK)]
                                            ]
                                        )
                                    except Exception as e:
                                        logger.error(
                                            f"[PRE_EXPIRY] Failed to notify {notify_user_id}: {e}"
                                        )
                                        await report_error_to_admin(e, f"[PRE_EXPIRY] Failed to notify {notify_user_id}")
                                _pre_expiry_deletion_sent[notify_key] = True

                    except Exception as ee:
                        logger.exception(f"[REMINDER] Error processing active user entry: {ee}")
                        await report_error_to_admin(ee, "[REMINDER] Error processing active user entry")

            # ===================================================================
            # PHASE 2: DB FALLBACK — clean up expired containers from database
            # ===================================================================
            try:
                active_containers = list(deployed_apps_col.find({"status": "active"}))

                for container in active_containers:
                    try:
                        app_name         = container.get("app_name")
                        username         = container.get("username")
                        container_uid    = container.get("user_id")
                        expires_at       = container.get("expires_at")

                        if not app_name or not expires_at:
                            continue

                        if isinstance(expires_at, str):
                            expires_at = _parse_iso_datetime(expires_at)

                        if not expires_at:
                            continue

                        if expires_at.tzinfo is None:
                            expires_at = expires_at.replace(tzinfo=timezone.utc)

                        seconds_left = (expires_at - now).total_seconds()

                        if seconds_left <= 60:
                            deletion_key = f"db_cleanup_{app_name}"
                            if _expired_cleanup_sent.get(deletion_key):
                                continue

                            logger.info(
                                f"[DB_CLEANUP] Found expired/soon-to-expire container: "
                                f"{app_name} for @{username}"
                            )

                            delete_ok, delete_resp = await asyncio.to_thread(
                                delete_deployed_app, app_name
                            )

                            if delete_ok:
                                deployed_apps_col.update_one(
                                    {"app_name": app_name},
                                    {"$set": {
                                        "status": "expired_deleted",
                                        "deleted_at": now,
                                        "deletion_reason": "subscription_expired"
                                    }}
                                )
                                api_subscriptions_col.update_many(
                                    {"$or": [{"app_name_1": app_name}, {"app_name_2": app_name}, {"app_name": app_name}]},
                                    {"$set": {
                                        "status": "expired_deleted",
                                        "deleted_at": now,
                                        "deletion_reason": "subscription_expired"
                                    }}
                                )

                                if container_uid:
                                    try:
                                        await bot.send_message(
                                            container_uid,
                                            f"⏰ <b>Subscription Expired</b>\n\n"
                                            f"Your API Rebate Claimer subscription has expired.\n"
                                            f"Container <code>{app_name}</code> has been automatically stopped.\n\n"
                                            f"Renew to get new containers deployed.",
                                            parse_mode="html",
                                            buttons=[
                                                [Button.inline("🔄 Renew Now", b"buy_product_api_claimer")],
                                                [Button.url("🛠 Contact Support", SUPPORT_CHAT_LINK)]
                                            ]
                                        )
                                    except Exception as e:
                                        logger.error(
                                            f"[DB_CLEANUP] Failed to notify {container_uid}: {e}"
                                        )

                            logger.info(f"[DB_CLEANUP] ✅ Deleted container: {app_name}")
                        else:
                            # Not an error per se, we just don't do anything yet if not expired
                            # Leaving this block blank to suppress false error logging for active ones.
                            pass

                        if seconds_left <= 60:
                             _expired_cleanup_sent[deletion_key] = True

                    except Exception as e:
                        logger.exception(f"[DB_CLEANUP] Error processing container: {e}")
                        await report_error_to_admin(e, "[DB_CLEANUP] Error processing container")

            except Exception as e:
                logger.exception(f"[DB_CLEANUP] Phase 2 error: {e}")
                await report_error_to_admin(e, "[DB_CLEANUP] Phase 2 error")

        except Exception as e:
            logger.exception(f"check_active_users_loop top-level error: {e}")
            await report_error_to_admin(e, "check_active_users_loop top-level error")

        await asyncio.sleep(ACTIVE_USERS_POLL_INTERVAL)

# ================== ADD POINTS COMMAND ==================

@bot.on(events.NewMessage(pattern=r"^/add\s"))
async def add_points_handler(event):
    sender = await event.get_sender()
    is_rabit = getattr(sender, 'username', '').lower() == 'rabit0505'
    
    if not is_rabit and event.sender_id != BOT_OWNER_ID:
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
        await report_error_to_admin(e, "add_points_handler DB update")
        await event.reply(f"![❌](tg://emoji?id=5273914604752216432) Database error: {e}", parse_mode="markdown")

# ================== API CLAIMER STATS COMMAND (OWNER ONLY) ==================

@bot.on(events.NewMessage(pattern=r"^/apicstats$"))
async def apicstats_handler(event):
    if event.sender_id != BOT_OWNER_ID:
        return

    status_msg = await event.reply("📊 <b>Fetching API Rebate Claimer Stats...</b>", parse_mode="html")

    now = datetime.now(timezone.utc)
    
    api_data = await asyncio.to_thread(get_active_users, API_CLAIMER_AUTH_URL)
    
    api_users = []
    if api_data and isinstance(api_data, dict):
        api_users = _extract_active_users_list(api_data)
    
    db_containers = list(deployed_apps_col.find({"product_type": "api_claimer"}))
    
    total_api_users = len(api_users)
    total_db_containers = len(db_containers)
    active_containers = len([c for c in db_containers if c.get("status") == "active"])
    expired_containers = len([c for c in db_containers if c.get("status") in ["expired_deleted", "terminated"]])
    
    report_lines = []
    report_lines.append("<b>📊 API REBATE CLAIMER STATS (Single Deploy)</b>")
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
                expires_dt = _parse_iso_datetime(str(expires_raw))
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
            
            clean_username = username.lstrip("@") if username else ""
            containers = list(deployed_apps_col.find({"username": clean_username, "product_type": "api_claimer"}))
            container_info = ""
            for c in containers:
                c_status = c.get("status", "unknown")
                c_name = c.get("app_name", "N/A")
                c_mirror = c.get("mirror_site", "?")
                icon = "📦" if c_status == "active" else "📭"
                container_info += f"\n   {icon} {c_name} [{c_mirror}] ({c_status})"
            
            report_lines.append(f"{status_emoji} <code>@{clean_username}</code>")
            report_lines.append(f"   ⏱ {time_str} left | Expires: {expires_str}{container_info}")
    
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
            mirror = container.get("mirror_site", "?")
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
                    status_emoji = "🟡" if total_seconds > 0 else "🔴"
                else:
                    status_emoji = "⚪"
            else:
                status_emoji = "⚪"
            
            report_lines.append(f"{status_emoji} <code>@{username}</code>")
            report_lines.append(f"   📦 {app_name} [{mirror}] | Status: {status}")
            report_lines.append(f"   Deployed: {deployed_str} | Expires: {expires_str}")
    
    full_report = "\n".join(report_lines)
    
    if len(full_report) <= 4096:
        await safe_edit(status_msg, full_report, parse_mode="html")
    else:
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
                await safe_edit(status_msg, chunk, parse_mode="html")
            else:
                await event.reply(f"<b>📊 API Rebate Claimer Stats (continued)</b>\n\n{chunk}", parse_mode="html")

# ================== EXTEND TIME COMMAND (OWNER ONLY) ==================

@bot.on(events.NewMessage(pattern=r"^/extend\s"))
async def extend_time_handler(event):
    if event.sender_id != BOT_OWNER_ID:
        return

    args = event.message.message.split()
    
    if len(args) < 3:
        return await event.reply(
            "❌ <b>Usage:</b> <code>/extend &lt;username/userid&gt; &lt;hours&gt; [product]</code>\n\n"
            "<b>Products:</b> <code>claimer</code>, <code>api_claimer</code>\n\n"
            "<b>Examples:</b>\n"
            "<code>/extend @alice123 24 claimer</code>\n"
            "<code>/extend @alice123 72 api_claimer</code>\n"
            "<code>/extend 123456789 24 claimer</code>\n\n"
            "<i>If product not specified, extends on all active products.</i>",
            parse_mode="html"
        )
    
    target_arg = args[1]
    hours_arg = args[2]
    product_arg = args[3].lower() if len(args) > 3 else None
    
    try:
        hours_to_add = int(hours_arg)
        if hours_to_add <= 0:
            raise ValueError("Hours must be positive")
    except ValueError:
        return await event.reply("❌ Invalid hours. Please enter a positive number.", parse_mode="html")
    
    valid_products = ["claimer", "api_claimer"]
    if product_arg and product_arg not in valid_products:
        return await event.reply(f"❌ Invalid product. Valid options: {', '.join(valid_products)}", parse_mode="html")
    
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
    
    products_to_extend = [product_arg] if product_arg else valid_products
    
    for product in products_to_extend:
        if product == "claimer":
            api_url = CLAIMER_API_URL
            product_name = "Code Rebate Claimer"
        elif product == "api_claimer":
            api_url = API_CLAIMER_AUTH_URL
            product_name = "API Rebate Claimer"
        else:
            continue
        
        try:
            success = await asyncio.to_thread(activate_subscription, f"@{target_username}", hours_to_add, api_url)
            
            if success:
                results.append(f"✅ <b>{product_name}</b>: Extended by {hours_to_add} hours")
                
                if product == "api_claimer":
                    # Update active containers for this user
                    containers = list(deployed_apps_col.find({"username": target_username, "status": "active"}))
                    for container in containers:
                        current_expires = container.get("expires_at")
                        if current_expires:
                            if isinstance(current_expires, str):
                                current_expires = _parse_iso_datetime(current_expires)
                            if current_expires:
                                if current_expires.tzinfo is None:
                                    current_expires = current_expires.replace(tzinfo=timezone.utc)
                                now = datetime.now(timezone.utc)
                                if current_expires > now:
                                    new_expires = current_expires + timedelta(hours=hours_to_add)
                                else:
                                    new_expires = now + timedelta(hours=hours_to_add)
                                
                                app_name = container.get("app_name")
                                mirror = container.get("mirror_site", "?")
                                deployed_apps_col.update_one(
                                    {"app_name": app_name},
                                    {"$set": {"expires_at": new_expires}}
                                )
                                api_subscriptions_col.update_many(
                                    {"$or": [{"app_name_1": app_name}, {"app_name_2": app_name}, {"app_name": app_name}]},
                                    {"$set": {"expires_at": new_expires}}
                                )
                                results.append(f"   📦 Container <code>{app_name}</code> [{mirror}] updated to: {new_expires.strftime('%Y-%m-%d %H:%M UTC')}")
            else:
                results.append(f"❌ <b>{product_name}</b>: Failed to extend")
                
        except Exception as e:
            logger.exception(f"Error extending {product_name} for {target_username}: {e}")
            await report_error_to_admin(e, f"Error extending {product_name} for {target_username}")
            results.append(f"❌ <b>{product_name}</b>: Error - {str(e)[:50]}")
    
    result_text = (
        f"⏰ <b>Subscription Extension Result</b>\n\n"
        f"User: <code>@{target_username}</code>\n"
        f"Hours Added: <b>{hours_to_add}</b>\n\n"
    )
    result_text += "\n".join(results)
    
    if target_user_id:
        try:
            api_data = await asyncio.to_thread(get_active_users, API_CLAIMER_AUTH_URL if product_arg == "api_claimer" else CLAIMER_API_URL)
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
    
    await safe_edit(status_msg, result_text, parse_mode="html")

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
        f"💰 <b>Purchase Points Bundle</b>\n\n"
        f"Current Balance: <b>{current_points:.2f} Points</b>\n\n"
        f"<b>Available Bundles:</b>\n"
        f"• 5 Points — 5.0 USDT\n"
        f"• 10 Points — 9.5 USDT (5% discount)\n"
        f"• 25 Points — 22.5 USDT (10% discount)\n"
        f"• 50 Points — 42.5 USDT (15% discount)\n"
        f"• 100 Points — 80.0 USDT (20% discount)\n\n"
        f"<i>1 Point = 1 USDT value</i>\n\n"
        f"Select a bundle:"
    )
    
    buttons = [
        [
            Button.inline("5 Pts — $5", b"bulk_5"),
            Button.inline("10 Pts — $9.5", b"bulk_10"),
        ],
        [
            Button.inline("25 Pts — $22.5", b"bulk_25"),
            Button.inline("50 Pts — $42.5", b"bulk_50"),
        ],
        [
            Button.inline("100 Pts — $80", b"bulk_100"),
        ],
        [Button.inline("🏠 Back to Home", b"back_to_start")]
    ]
    
    try:
        await event.respond(text, parse_mode="html", buttons=buttons)
    except Exception as e:
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
        f"💰 <b>Points Bundle Purchase</b>\n\n"
        f"Bundle: <b>{label}</b>\n"
        f"Cost: <b>{amount} USDT</b>\n"
        f"Points to receive: <b>{points}</b>\n\n"
        f"Current Balance: <b>{current_points:.2f} Points</b>\n"
        f"After Purchase: <b>{current_points + points:.2f} Points</b>\n\n"
        f"Proceed to payment?"
    )
    
    buttons = [
        [Button.inline(f"💳 Pay {amount} USDT", b"bulk_pay_crypto")],
        [Button.inline("🔙 Back to Bundles", b"back_buypoints")]
    ]
    
    await safe_edit(event, text, parse_mode="html", buttons=buttons)


@bot.on(events.CallbackQuery(data=b"back_buypoints"))
async def back_buypoints_handler(event):
    await event.answer()
    user_id = event.sender_id
    
    user_data = users_col.find_one({"user_id": user_id})
    current_points = user_data.get("points", 0.0) if user_data else 0.0
    
    text = (
        f"💰 <b>Purchase Points Bundle</b>\n\n"
        f"Current Balance: <b>{current_points:.2f} Points</b>\n\n"
        f"<b>Available Bundles:</b>\n"
        f"• 5 Points — 5.0 USDT\n"
        f"• 10 Points — 9.5 USDT (5% discount)\n"
        f"• 25 Points — 22.5 USDT (10% discount)\n"
        f"• 50 Points — 42.5 USDT (15% discount)\n"
        f"• 100 Points — 80.0 USDT (20% discount)\n\n"
        f"<i>1 Point = 1 USDT value</i>\n\n"
        f"Select a bundle:"
    )
    
    buttons = [
        [
            Button.inline("5 Pts — $5", b"bulk_5"),
            Button.inline("10 Pts — $9.5", b"bulk_10"),
        ],
        [
            Button.inline("25 Pts — $22.5", b"bulk_25"),
            Button.inline("50 Pts — $42.5", b"bulk_50"),
        ],
        [
            Button.inline("100 Pts — $80", b"bulk_100"),
        ],
        [Button.inline("🏠 Back to Home", b"back_to_start")]
    ]
    
    await safe_edit(event, text, parse_mode="html", buttons=buttons)


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
    
    await safe_edit(event, "🔄 Creating invoice...", parse_mode="html")
    
    try:
        resp = await asyncio.to_thread(create_invoice, amount)
    except Exception as e:
        logger.exception(f"Invoice error: {e}")
        await report_error_to_admin(e, "bulk_pay_crypto_handler create_invoice")
        return await safe_edit(event, "Failed to create invoice.")
    
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
        return await safe_edit(event, "Payment gateway error.")
    
    session["track_id"] = track_id
    
    text = (
        f"✅ <b>Points Bundle Purchase</b>\n"
        f"Bundle: <b>{label}</b>\n"
        f"Amount: <b>{amount} USDT</b>\n"
        f"Points: <b>{points}</b>\n\n"
        f"Click <b>Pay</b> to open OxaPay.\n"
        f"Payment window: 15 minutes."
    )
    
    buttons = [
        [Button.url("🔗 Pay Now", pay_url)],
        [
            Button.url("🛠 Contact Support", SUPPORT_CHAT_LINK),
            Button.url("📢 Updates Channel", UPDATES_CHANNEL_LINK),
        ],
    ]
    
    await safe_edit(event, text, parse_mode="html", buttons=buttons)
    
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
        "<b><tg-emoji emoji-id='5445284980978621387'>🚀</tg-emoji> Rebate Buy Bot — Premium Rebate Tools</b>\n\n"
        "<b>Available Products:</b>\n"
        "• Code Rebate Claimer (High-speed code claiming)\n"
        "• API Rebate Claimer (Dedicated API container)\n\n"
        "Select a product to purchase or manage your account."
    )

    buttons = []
    buttons.append([Button.inline("⚡ Purchase Code Claimer", b"buy_product_claimer")])
    buttons.append([Button.inline("🔌 Purchase API Claimer", b"buy_product_api_claimer")])

    account_row = []
    if not first_time:
        account_row.append(Button.inline("✏️ Change Username", b"edit_username"))
    account_row.append(Button.inline("🎁 Referral Program", b"menu_referral"))
    buttons.append(account_row)
    
    buttons.append([Button.inline("💰 Buy Points Bundle", b"menu_buypoints")])
    
    if not first_time:
        buttons.append([Button.inline("🗑 Cancel Subscription", b"terminate_sub_menu")])

    buttons.append([
        Button.url("🛠 Contact Support", SUPPORT_CHAT_LINK),
        Button.url("📢 Updates Channel", UPDATES_CHANNEL_LINK),
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
        buttons=[[Button.url("🛠 Contact Support", SUPPORT_CHAT_LINK)]]
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
    
    text = (
        "<b><tg-emoji emoji-id='5411271889421086677'>🎁</tg-emoji> Referral Program</b>\n\n"
        "Invite friends and earn <b>10%</b> of their spendings as points!\n"
        "1 Point = 1 USDT value.\n\n"
        f"<tg-emoji emoji-id='5375312095346704820'>💰</tg-emoji> <b>Your Balance:</b> {points:.2f} Points\n"
        f"<tg-emoji emoji-id='5453957997418004470'>👥</tg-emoji> <b>Total Referrals:</b> {ref_count}\n\n"
        "<b>Your Referral Link:</b>\n"
        f"<code>{ref_link}</code>\n\n"
        "Share this link. You get notified instantly when someone joins."
    )
    
    buttons = [[Button.inline("🏠 Back to Home", b"back_to_start")]]
    
    await safe_edit(event, text, parse_mode="html", buttons=buttons)

# --- BUY POINTS MENU HANDLER ---

@bot.on(events.CallbackQuery(data=b"menu_buypoints"))
async def menu_buypoints_handler(event):
    await event.answer()
    user_id = event.sender_id
    
    user_data = users_col.find_one({"user_id": user_id})
    current_points = user_data.get("points", 0.0) if user_data else 0.0
    
    text = (
        f"💰 <b>Purchase Points Bundle</b>\n\n"
        f"Current Balance: <b>{current_points:.2f} Points</b>\n\n"
        f"<b>Available Bundles:</b>\n"
        f"• 5 Points — 5.0 USDT\n"
        f"• 10 Points — 9.5 USDT (5% discount)\n"
        f"• 25 Points — 22.5 USDT (10% discount)\n"
        f"• 50 Points — 42.5 USDT (15% discount)\n"
        f"• 100 Points — 80.0 USDT (20% discount)\n\n"
        f"<i>1 Point = 1 USDT value</i>\n\n"
        f"Select a bundle:"
    )
    
    buttons = [
        [
            Button.inline("5 Pts — $5", b"bulk_5"),
            Button.inline("10 Pts — $9.5", b"bulk_10"),
        ],
        [
            Button.inline("25 Pts — $22.5", b"bulk_25"),
            Button.inline("50 Pts — $42.5", b"bulk_50"),
        ],
        [
            Button.inline("100 Pts — $80", b"bulk_100"),
        ],
        [Button.inline("🏠 Back to Home", b"back_to_start")]
    ]
    
    await safe_edit(event, text, parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(data=b"back_to_start"))
async def back_start_handler(event):
    await event.answer()
    user_id = event.sender_id

    try:
        existing = users_col.find_one({"user_id": user_id})
    except:
        existing = None

    buttons = []
    buttons.append([Button.inline("⚡ Purchase Code Claimer", b"buy_product_claimer")])
    buttons.append([Button.inline("🔌 Purchase API Claimer", b"buy_product_api_claimer")])

    account_row = []
    if existing:
        account_row.append(Button.inline("✏️ Change Username", b"edit_username"))
    account_row.append(Button.inline("🎁 Referral Program", b"menu_referral"))
    buttons.append(account_row)
    
    buttons.append([Button.inline("💰 Buy Points Bundle", b"menu_buypoints")])
    
    if existing:
        buttons.append([Button.inline("🗑 Cancel Subscription", b"terminate_sub_menu")])

    buttons.append([
        Button.url("🛠 Contact Support", SUPPORT_CHAT_LINK),
        Button.url("📢 Updates Channel", UPDATES_CHANNEL_LINK),
    ])

    caption_text = (
        "<b><tg-emoji emoji-id='5445284980978621387'>🚀</tg-emoji> Rebate Buy Bot — Premium Rebate Tools</b>\n\n"
        "<b>Available Products:</b>\n"
        "• Code Rebate Claimer (High-speed code claiming)\n"
        "• API Rebate Claimer (Dedicated API container)\n\n"
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
        [Button.inline("⚡ Purchase Code Claimer", b"buy_product_claimer")],
        [Button.inline("🔌 Purchase API Claimer", b"buy_product_api_claimer")],
    ]
    await safe_edit(event, "<b>Select Product to Renew:</b>", parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(pattern=b"buy_product_"))
async def buy_product_handler(event):
    await event.answer()
    user_id = event.sender_id
    
    data_str = event.data.decode()
    product_type = "claimer"
    product_display = "Code Rebate Claimer"
    
    if "api_claimer" in data_str:
        product_type = "api_claimer"
        product_display = "API Rebate Claimer"
    
    session = user_sessions.setdefault(user_id, {})
    session["expecting_username"] = True
    session["product"] = product_type

    if product_type == "api_claimer":
        text = (
            f"<b>Purchase {product_display} — Step 1: Provide your Stake username</b>\n\n"
            "Send only the username. Examples:\n"
            "• <code>alice123</code>\n"
            "• <code>@alice123</code>\n\n"
            "Do NOT send profile links or screenshots.\n\n"
            "<i>After username confirmation, you'll need to provide your <b>Stake API key</b> for container deployment.</i>"
        )
    else:
        text = (
            f"<b>Purchase {product_display} — Step 1: Provide your Stake username</b>\n\n"
            "Send only the username. Examples:\n"
            "• <code>alice123</code>\n"
            "• <code>@alice123</code>\n\n"
            "Do NOT send profile links or screenshots. After you send the username you'll be asked to confirm it."
        )
    
    await safe_edit(event, text, parse_mode="html")

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
        "<b><tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Change Username — Step 1</b>\n\n"
        "Please enter the <b>OLD</b> Stake username (the one you want to replace).\n\n"
        "Example: <code>alice123</code>"
    )
    
    await safe_edit(event, text, parse_mode="html")

@bot.on(events.CallbackQuery(data=b"edit_cancel"))
async def edit_cancel_handler(event):
    await event.answer()
    user_id = event.sender_id
    session = user_sessions.setdefault(user_id, {})
    session.pop("expecting_rename_old", None)
    session.pop("expecting_rename_new", None)
    session.pop("rename_old_value", None)
    await safe_edit(event, "Edit cancelled.", parse_mode="html")

# ================== TERMINATE SUBSCRIPTION HANDLER ==================

@bot.on(events.CallbackQuery(data=b"terminate_sub_menu"))
async def terminate_sub_menu_handler(event):
    await event.answer()
    user_id = event.sender_id
    
    user_doc = users_col.find_one({"user_id": user_id})
    if not user_doc or not user_doc.get("username"):
        await safe_edit(event, "❌ No username linked to your account. Cannot terminate.", buttons=[[Button.inline("🏠 Back to Home", b"back_to_start")]])
        return

    username = user_doc.get("username").lstrip("@")
    
    active_details = None
    
    data_claimer = await asyncio.to_thread(get_active_users, CLAIMER_API_URL)
    if data_claimer:
        users = _extract_active_users_list(data_claimer)
        for u in users:
            if u.get("username", "").lower().lstrip("@") == username.lower():
                expires = _parse_iso_datetime(str(u.get("expires", "")))
                if expires:
                    active_details = (CLAIMER_API_URL, expires, "Code Rebate Claimer")
                    break

    if not active_details:
        data_api_claimer = await asyncio.to_thread(get_active_users, API_CLAIMER_AUTH_URL)
        if data_api_claimer:
            users = _extract_active_users_list(data_api_claimer)
            for u in users:
                if u.get("username", "").lower().lstrip("@") == username.lower():
                    expires = _parse_iso_datetime(str(u.get("expires", "")))
                    if expires:
                        active_details = (API_CLAIMER_AUTH_URL, expires, "API Rebate Claimer")
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
            [Button.inline("🏠 Back to Home", b"back_to_start")]
        ]
        await safe_edit(event, text, parse_mode="html", buttons=buttons)
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
        if product_name == "Code Rebate Claimer":
            refund_amount = remaining_hours * REFUND_RATE_CLAIMER_PER_HOUR
        elif product_name == "API Rebate Claimer":
            refund_amount = remaining_hours * REFUND_RATE_API_CLAIMER_PER_HOUR
        else:
            refund_amount = remaining_hours * REFUND_RATE_CLAIMER_PER_HOUR
            
    refund_amount = round(refund_amount, 2)
    
    session = user_sessions.setdefault(user_id, {})
    session["term_username"] = username
    session["term_api"] = api_url
    session["term_refund"] = refund_amount
    session["term_product"] = product_name
    
    text = (
        f"<b>🗑 Cancel Subscription</b>\n\n"
        f"Product: <b>{product_name}</b>\n"
        f"User: <code>@{username}</code>\n"
        f"Time Left: <b>{remaining_hours:.1f} Hours</b>\n\n"
        f"<b>Refund Estimate:</b> {refund_amount} Points\n"
        "<i>(Based on remaining time)</i>\n\n"
        "Are you sure? This will instantly stop the bot and remove your username."
    )
    
    buttons = [
        [Button.inline(f"✅ Confirm — Refund {refund_amount} Pts", b"terminate_sub_execute")],
        [Button.inline("❌ Go Back", b"back_to_start")]
    ]
    await safe_edit(event, text, parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(data=b"terminate_sub_force_db"))
async def terminate_force_db_handler(event):
    await event.answer()
    user_id = event.sender_id
    
    users_col.update_one({"user_id": user_id}, {"$unset": {"username": ""}})
    
    await safe_edit(event, "✅ Username removed from database.", buttons=[[Button.inline("🏠 Back to Home", b"back_to_start")]])

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
        await safe_edit(event, "Session expired. Please try again.", buttons=[[Button.inline("🏠 Back to Home", b"back_to_start")]])
        return
        
    await safe_edit(event, "⏳ Terminating subscription on all services...", parse_mode="html")

    # ===== CALL ALL PRODUCT APIs TO DELETE USER =====
    all_api_urls = [CLAIMER_API_URL, API_CLAIMER_AUTH_URL]
    
    seen_urls = set()
    unique_api_urls = []
    for u in all_api_urls:
        if u not in seen_urls:
            seen_urls.add(u)
            unique_api_urls.append(u)

    deletion_results = []
    any_success = False
    for del_url in unique_api_urls:
        ok = await asyncio.to_thread(delete_user_api, username, del_url)
        deletion_results.append((del_url, ok))
        if ok:
            any_success = True
        logger.info(f"[TERMINATE] delete_user_api on {del_url} for @{username}: {'ok' if ok else 'failed'}")

    # ===== ALWAYS DELETE API CLAIMER CONTAINERS =====
    container_lines = []
    try:
        deployed_apps_list = list(deployed_apps_col.find({"username": username, "status": "active"}))
        if deployed_apps_list:
            for deployed_app in deployed_apps_list:
                app_name = deployed_app.get("app_name")
                mirror = deployed_app.get("mirror_site", "?")
                if app_name:
                    delete_ok, delete_resp = await asyncio.to_thread(delete_deployed_app, app_name)
                    
                    now_dt = datetime.now(timezone.utc)
                    deployed_apps_col.update_one(
                        {"app_name": app_name},
                        {"$set": {
                            "status": "terminated",
                            "terminated_at": now_dt,
                            "deletion_reason": "user_terminated"
                        }}
                    )
                    api_subscriptions_col.update_many(
                        {"$or": [{"app_name_1": app_name}, {"app_name_2": app_name}, {"app_name": app_name}]},
                        {"$set": {
                            "status": "terminated",
                            "terminated_at": now_dt,
                            "deletion_reason": "user_terminated"
                        }}
                    )
                    
                    status_icon = "✅" if delete_ok else "⚠️"
                    container_lines.append(f"{status_icon} <code>{app_name}</code> [{mirror}]")
                    logger.info(f"[TERMINATE] Container {app_name} deletion: {'ok' if delete_ok else 'failed'} — {delete_resp}")
    except Exception as e:
        logger.error(f"[TERMINATE] Failed to delete deployed apps during termination: {e}")
        await report_error_to_admin(e, "[TERMINATE] Failed to delete deployed apps during termination")

    # ===== UPDATE DB: REFUND POINTS AND REMOVE USERNAME =====
    user_doc = users_col.find_one({"user_id": user_id})
    current_db_user = user_doc.get("username", "").lstrip("@") if user_doc else ""
    
    update_query = {"$inc": {"points": refund}}
    if current_db_user.lower() == username.lower().lstrip("@"):
        update_query["$unset"] = {"username": ""}
        
    users_col.update_one({"user_id": user_id}, update_query)

    # ===== BUILD RESULT MESSAGE =====
    if any_success:
        container_summary = ""
        if container_lines:
            container_summary = "\n\n<b>🗑 Containers Stopped:</b>\n" + "\n".join(container_lines)
        elif not container_lines:
            container_summary = "\n\n<i>No active containers found.</i>"

        await safe_edit(
            event,
            f"✅ <b>Subscription Cancelled</b>\n\n"
            f"User <code>@{username}</code> removed from all services.\n"
            f"Refunded: <b>{refund} Points</b>."
            f"{container_summary}",
            parse_mode="html",
            buttons=[[Button.inline("🏠 Back to Home", b"back_to_start")]]
        )
    else:
        await safe_edit(
            event,
            "❌ Failed to delete user from all servers. Please contact support.",
            buttons=[[Button.url("🛠 Contact Support", SUPPORT_CHAT_LINK)]]
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

    # --- HANDLE SESSION TOKEN (API KEY) FOR API CLAIMER ---
    if session.get("expecting_session_token"):
        session["expecting_session_token"] = False
        
        if len(raw_text) < 20:
            await event.respond(
                "❌ Invalid API key. Your Stake API key should be a long string.\n\n"
                "Please send a valid API key or click below to skip.",
                buttons=[[Button.inline("⏭️ Skip key", b"skip_session_token")]]
            )
            session["expecting_session_token"] = True  # keep waiting
            return
        
        session["session_token"] = raw_text
        
        username_clean = session.get("username", "UNKNOWN")
        prod_name = "API Rebate Claimer"
        
        text = (
            f"✅ <b>API Key Saved!</b> [{API_CLAIMER_MIRROR_SITE}]\n\n"
            f"Username: <code>@{username_clean}</code>\n"
            f"Product: <b>{prod_name}</b>\n\n"
            "Choose payment method:"
        )
        buttons = [
            [Button.inline("💳 Pay with Crypto / Points", b"buy_crypto")],
            [Button.inline("💵 Pay with UPI", b"buy_upi")],
            [
                Button.url("🛠 Contact Support", SUPPORT_CHAT_LINK),
                Button.url("📢 Updates Channel", UPDATES_CHANNEL_LINK),
            ],
        ]
        
        await event.respond(text, parse_mode="html", buttons=buttons)
        return

    # Skip pattern matching for commands and short text
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
        
        data_claimer = await asyncio.to_thread(get_active_users, CLAIMER_API_URL)
        if data_claimer:
            users = _extract_active_users_list(data_claimer)
            for u in users:
                if u.get("username", "").lower().lstrip("@") == target_username.lower():
                    expires = _parse_iso_datetime(str(u.get("expires", "")))
                    if expires:
                        active_details = (CLAIMER_API_URL, expires, "Code Rebate Claimer")
                        break

        if not active_details:
            data_api_claimer = await asyncio.to_thread(get_active_users, API_CLAIMER_AUTH_URL)
            if data_api_claimer:
                users = _extract_active_users_list(data_api_claimer)
                for u in users:
                    if u.get("username", "").lower().lstrip("@") == target_username.lower():
                        expires = _parse_iso_datetime(str(u.get("expires", "")))
                        if expires:
                            active_details = (API_CLAIMER_AUTH_URL, expires, "API Rebate Claimer")
                            break
        
        if not active_details:
            await event.respond(
                f"❌ No active subscription found for <code>@{target_username}</code> either.",
                parse_mode="html",
                buttons=[[Button.inline("🏠 Back to Home", b"back_to_start")]]
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
            if product_name == "Code Rebate Claimer":
                refund_amount = remaining_hours * REFUND_RATE_CLAIMER_PER_HOUR
            elif product_name == "API Rebate Claimer":
                refund_amount = remaining_hours * REFUND_RATE_API_CLAIMER_PER_HOUR
            else:
                refund_amount = remaining_hours * REFUND_RATE_CLAIMER_PER_HOUR
                
        refund_amount = round(refund_amount, 2)
        
        session["term_username"] = target_username
        session["term_api"] = api_url
        session["term_refund"] = refund_amount
        session["term_product"] = product_name
        
        text = (
            f"<b>🗑 Cancel Subscription (Old Username)</b>\n\n"
            f"Product: <b>{product_name}</b>\n"
            f"User: <code>@{target_username}</code>\n"
            f"Time Left: <b>{remaining_hours:.1f} Hours</b>\n\n"
            f"<b>Refund Estimate:</b> {refund_amount} Points\n"
            "<i>(Based on remaining time)</i>\n\n"
            "Are you sure? This will instantly stop the bot."
        )
        
        buttons = [
            [Button.inline(f"✅ Confirm — Refund {refund_amount} Pts", b"terminate_sub_execute")],
            [Button.inline("❌ Go Back", b"back_to_start")]
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
                await report_error_to_admin(e, "Failed to update DB entries after rename API success.")
            
            await event.respond(f"✅ Success! Username changed from <code>@{old_username}</code> to <code>@{new_username}</code>.", parse_mode="html")
            
        except Exception as e:
            logger.exception("Rename process failed.")
            await report_error_to_admin(e, "Rename process failed")
            await event.respond(f"❌ Error during rename: {e}", parse_mode="html")
        
        return

    # --- STANDARD PURCHASE FLOW ---
    if not session.get("expecting_username"):
        return

    session["pending_username"] = username_clean
    session["expecting_username"] = False
    
    prod = session.get("product", "claimer")
    if prod == "api_claimer":
        prod_name = "API Rebate Claimer"
    else:
        prod_name = "Code Rebate Claimer"

    text = (
        f"Product: <b>{prod_name}</b>\n\n"
        "You entered the Stake username:\n"
        f"<b>@{username_clean}</b>\n\n"
        "Is this correct?"
    )
    buttons = [
        [Button.inline("✅ Yes, confirm username", b"confirm_username_yes")],
        [Button.inline("✏️ No — re-enter username", b"confirm_username_no")],
    ]

    await event.respond(text, parse_mode="html", buttons=buttons)

# ================== API KEY HELPER CALLBACKS ==================

@bot.on(events.CallbackQuery(data=b"skip_session_token"))
async def skip_session_token_handler(event):
    """User skips the API key."""
    await event.answer()
    user_id = event.sender_id
    session = user_sessions.get(user_id)
    
    if not session:
        return await event.respond("Session expired. Restart with /start.")
    
    session["expecting_session_token"] = False
    
    username_clean = session.get("username", "UNKNOWN")
    prod_name = "API Rebate Claimer"
    
    text = (
        f"<b>⚠️ API Key Skipped</b>\n\n"
        f"Username: <code>@{username_clean}</code>\n"
        f"Product: <b>{prod_name}</b>\n\n"
        f"<i>No container will be deployed automatically. Contact support to deploy manually.</i>\n\n"
        "Choose payment method:"
    )
    buttons = [
        [Button.inline("💳 Pay with Crypto / Points", b"buy_crypto")],
        [Button.inline("💵 Pay with UPI", b"buy_upi")],
        [
            Button.url("🛠 Contact Support", SUPPORT_CHAT_LINK),
            Button.url("📢 Updates Channel", UPDATES_CHANNEL_LINK),
        ],
    ]
    
    await safe_edit(event, text, parse_mode="html", buttons=buttons)

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
    
    await safe_edit(event, text, parse_mode="html")

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
    except Exception as e:
        logger.exception("Failed to persist username to DB on confirm.")
        await report_error_to_admin(e, "confirm_username_yes DB update")

    prod = session.get("product", "claimer")
    if prod == "api_claimer":
        prod_name = "API Rebate Claimer"
    else:
        prod_name = "Code Rebate Claimer"

    # === API CLAIMER: ASK FOR FIRST SESSION TOKEN ===
    if prod == "api_claimer":
        text = (
            f"Stake username saved: <code>@{username_clean}</code>\n"
            f"Product: <b>{prod_name}</b>\n\n"
            f"<b>Step 2: Provide API Key</b> [{API_CLAIMER_MIRROR_SITE}]\n\n"
            "Your setup requires your <b>Stake API key</b>.\n\n"
            f"Please paste your <b>API key</b> now:"
        )
        buttons = [
            [Button.inline("⏭️ Skip key", b"skip_session_token")],
            [Button.inline("❌ Cancel", b"back_to_start")]
        ]
        session["expecting_session_token"] = True
        
        await safe_edit(event, text, parse_mode="html", buttons=buttons)
        return

    # Standard flow for other products
    text = (
        f"Stake username saved: <code>@{username_clean}</code>\n"
        f"Product: <b>{prod_name}</b>\n\n"
        "Choose payment method:"
    )
    
    pay_btn_text = "💳 Pay with Crypto / Points"
    
    buttons = [
        [Button.inline(pay_btn_text, b"buy_crypto")],
        [Button.inline("💵 Pay with UPI", b"buy_upi")],
        [
            Button.url("🛠 Contact Support", SUPPORT_CHAT_LINK),
            Button.url("📢 Updates Channel", UPDATES_CHANNEL_LINK),
        ],
    ]

    await safe_edit(event, text, parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(data=b"buy_upi"))
async def buy_upi_handler(event):
    await event.answer()
    text = (
        "<tg-emoji emoji-id='6325705628291436771'>💵</tg-emoji> <b>Pay with UPI</b>\n\n"
        "DM admin and mention your Stake username:\n"
        f"👉 <a href=\"{UPI_DM_LINK}\">@Rabit0505</a>"
    )
    buttons = [[Button.url("💬 DM for UPI Payment", UPI_DM_LINK)]]
    
    await safe_edit(event, text, parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(data=b"buy_crypto"))
async def buy_crypto_handler(event):
    await event.answer()
    user_id = event.sender_id
    session = user_sessions.get(user_id)

    if not session or "username" not in session:
        return await event.respond("Restart with /start and send your username first.")

    prod = session.get("product", "claimer")
    if prod == "api_claimer":
        prod_name = "API Rebate Claimer"
    else:
        prod_name = "Code Rebate Claimer"

    header = f"⚡ <b>{prod_name} Plans</b>"
    
    text = (
        f"{header}\n"
        "💳 <b>Select a Plan</b>\n\n"
        "Plans:\n"
    )
    
    buttons = []
    
    p2d = PLANS["2d"]
    p7d = PLANS["7d"]
    p30d = PLANS["30d"]
    p120d = PLANS["120d"]

    text += f"• {p2d['label']:<20} — {p2d['amount']} USDT\n"
    text += f"• {p7d['label']:<20} — {p7d['amount']} USDT\n"
    text += f"• {p30d['label']:<20} — {p30d['amount']} USDT\n"
    text += f"• {p120d['label']:<20} — {p120d['amount']} USDT\n"

    buttons.append([
        Button.inline(f"{p2d['label']} — ${p2d['amount']}", b"plan_2d"),
        Button.inline(f"{p7d['label']} — ${p7d['amount']}", b"plan_7d"),
    ])
    buttons.append([
        Button.inline(f"{p30d['label']} — ${p30d['amount']}", b"plan_30d"),
    ])
    buttons.append([
        Button.inline(f"{p120d['label']} — ${p120d['amount']}", b"plan_120d"),
    ])

    text += "\nSelect your plan:"

    await safe_edit(event, text, parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(pattern=b"plan_"))
async def plan_handler(event):
    await event.answer()
    user_id = event.sender_id
    session = user_sessions.get(user_id)

    if not session or "username" not in session:
        return await event.respond("Restart with /start and send your username first.")

    plan_key_raw = event.data.decode().split("_", 1)[1]
    
    prod = session.get("product", "claimer")

    if plan_key_raw.startswith("api_"):
        plan_key_raw = plan_key_raw.replace("api_", "")

    plan = PLANS.get(plan_key_raw)
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
    
    if prod == "api_claimer":
        prod_name = "API Rebate Claimer"
    else:
        prod_name = "Code Rebate Claimer"

    points_text = f"💰 Your Points: <b>{user_points:.2f}</b>\n\n"
    cost_text = f"Cost: <b>{amount} USDT</b> (or Points)\n"

    text = (
        f"🛒 <b>Checkout: {prod_name}</b>\n\n"
        f"Plan: <b>{label}</b>\n"
        f"{cost_text}"
        f"Duration: <b>{hours} Hours</b>\n\n"
        f"{points_text}"
        "Select payment method:"
    )
    
    buttons = [
        [Button.inline(f"💳 Pay Crypto — ${amount}", b"pay_method_crypto")],
    ]
    
    buttons.append([Button.inline(f"💰 Pay Points — {amount} Pts", b"pay_method_points")])

    buttons.append([Button.inline("🔙 Back to Plans", b"buy_crypto")])
    
    await safe_edit(event, text, parse_mode="html", buttons=buttons)

@bot.on(events.CallbackQuery(data=b"pay_method_points"))
async def pay_points_handler(event):
    await event.answer()
    user_id = event.sender_id
    session = user_sessions.get(user_id)
    
    if not session or "selected_amount" not in session:
        return await event.respond("Session expired. Please restart.")
        
    prod = session.get("product", "claimer")
        
    amount = session["selected_amount"]
    label = session["selected_label"]
    hours = session["selected_hours"]
    username_clean = session["username"]
    
    if prod == "api_claimer":
        api_url = API_CLAIMER_AUTH_URL
        prod_name = "API Rebate Claimer"
    else:
        api_url = CLAIMER_API_URL
        prod_name = "Code Rebate Claimer"
    
    user_data = users_col.find_one({"user_id": user_id})
    user_points = user_data.get("points", 0.0) if user_data else 0.0
    
    if user_points < amount:
        await event.answer(f"❌ Insufficient Points! You need {amount} points.", alert=True)
        return
        
    users_col.update_one({"user_id": user_id}, {"$inc": {"points": -amount}})
    
    await safe_edit(event, f"🔄 Activating {prod_name} subscription...", parse_mode="html")
    
    activation_ok = await asyncio.to_thread(activate_subscription, f"@{username_clean}", hours, api_url)
    
    if activation_ok:
        try:
            await event.delete()
        except:
            pass
        
        if prod == "api_claimer":
            # === API CLAIMER: SINGLE DEPLOY CONTAINER ===
            session_token = session.get("session_token")
            if session_token:
                now_dt = datetime.now(timezone.utc)
                expires_at = now_dt + timedelta(hours=hours)

                deploy_result = await animate_deploy_progress(
                    user_id, username_clean, session_token
                )

                app_name, ok, res, dep_url, auth_tok = deploy_result

                web_url = res.get("web_url", f"https://{app_name}.herokuapp.com") if ok else ""
                deployed_apps_col.insert_one({
                    "user_id": user_id,
                    "username": username_clean,
                    "app_name": app_name,
                    "session_token": session_token,
                    "mirror_site": API_CLAIMER_MIRROR_SITE,
                    "deploy_url": dep_url,
                    "deployed_at": now_dt,
                    "expires_at": expires_at,
                    "status": "active" if ok else "deploy_failed",
                    "web_url": web_url,
                    "region": API_CLAIMER_REGION,
                    "product_type": "api_claimer"
                })

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
                            "updated_at": now_dt
                        }
                    },
                    upsert=True
                )
            elif session.get("expecting_session_token") is False:
                # Key skipped, continue gracefully
                pass
            else:
                await bot.send_message(
                    user_id,
                    "⚠️ <b>No API key provided.</b>\nContact support to deploy your container manually.",
                    parse_mode="html",
                    buttons=[[Button.url("🛠 Contact Support", SUPPORT_CHAT_LINK)]]
                )
        else:
            # === CODE CLAIMER ===
            await bot.send_message(
                user_id,
                f"✅ Payment confirmed via Points!\n\n"
                f"Your <b>{prod_name} — {label}</b> subscription is activated.\n"
                f"Stake Username: <code>@{username_clean}</code>\n"
                f"Duration: <b>{hours} hours</b>.",
                parse_mode="html"
            )
            
            forwards = [CLAIMER_FORWARD_1, CLAIMER_FORWARD_2]
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
                    await report_error_to_admin(e, f"Forward error for {chat} msg {msg_id}")
    else:
        users_col.update_one({"user_id": user_id}, {"$inc": {"points": amount}})
        try:
            await event.respond("❌ Activation failed. Points refunded. Contact support.", parse_mode="html")
        except:
            pass

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
        prod_name = "API Rebate Claimer"
    else:
        prod_name = "Code Rebate Claimer"

    if user_id in user_tasks:
        old = user_tasks[user_id]
        if not old.done():
            old.cancel()

    await safe_edit(event, "🔄 Creating Invoice...", parse_mode="html")

    try:
        resp = await asyncio.to_thread(create_invoice, amount)
    except Exception as e:
        logger.exception(f"Invoice error: {e}")
        await report_error_to_admin(e, "pay_crypto_inv_handler create_invoice")
        return await safe_edit(event, "Failed to create invoice.")

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
        return await safe_edit(event, "Payment gateway error.")

    session["track_id"] = track_id
    
    text = (
        f"✅ Product: <b>{prod_name}</b>\n"
        f"✅ Plan: <b>{label}</b>\n"
        f"Amount: <b>{amount} USDT</b>\n"
        f"Duration: <b>{hours} Hours</b>\n\n"
        "Click <b>Pay Now</b> to open OxaPay.\n"
        "Payment window: 15 minutes."
    )
    buttons = [
        [Button.url("🔗 Pay Now", pay_url)],
        [
            Button.url("🛠 Contact Support", SUPPORT_CHAT_LINK),
            Button.url("📢 Updates Channel", UPDATES_CHANNEL_LINK),
        ],
    ]
    
    await safe_edit(event, text, parse_mode="html", buttons=buttons)

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
            await report_error_to_admin(e, f"Broadcast fail to {uid}")

    await event.reply(f"✅ Broadcast sent to {total} users.", parse_mode="html")

# ================== MAIN ==================

def main():
    logger.info("Rebate Buy Bot (Single Deployer) is running...")
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(check_active_users_loop())
    except Exception as e:
        logger.exception(f"Failed to schedule background tasks: {e}")
        sync_report_error_to_admin(e, "Failed to schedule background tasks")

    bot.run_until_disconnected()

if __name__ == "__main__":
    main()
