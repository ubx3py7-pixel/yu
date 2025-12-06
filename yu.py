#!/usr/bin/env python3
# yu.py
"""
FIRE DROP OSINT Bot - lightweight implementation using raw Telegram Bot API and aiohttp.
Improved OSINT parsing and debugging (saves raw JSON and notifies owner when response shape is unexpected).
No aiogram/pyrogram required.
"""

import asyncio
import aiohttp
import sqlite3
import time
import random
import string
import json
from typing import Optional

# ---------------- CONFIG ----------------
BOT_TOKEN = "8365786304:AAGzmEgcLTmS2vzJMJOM-YrEtgtw8T3I-4Q"  # your bot token (already provided)
OWNER_ID = 6940098775

API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"
OSINT_API_TEMPLATE = "https://dark-trace-networks.vercel.app/api?key=DarkTrace_Network&type=mobile&term={term}"

DB_PATH = "firedrop_min.db"
DEFAULT_CREDITS = 5
SLOWMODE_AFTER = 2
SLOWMODE_SECONDS = 10
POLL_TIMEOUT = 30  # long polling seconds
# ----------------------------------------

# Templates
TEMPLATE_FOUND = """════════════════════
║  📱 PHONE INFO 📱  ║
════════════════════
📞 Number: {number}

👤 Name: {name}  
👨 Father: {father}

📧 Email: {email}
📱 Alt Number: {alt_number}

🌐 Circle: {circle}
🆔 ID: {id_field}
🏠 Address: {address}

━━━━━━━━━━━━━━━━━━━━
💎 Credits Left: {credits_left}
Zoro found it! 💨
"""

TEMPLATE_NOT_FOUND = """Zoro lost the battle 😔.
No data found

Remaining credits: {credits_left}
"""

# ------------ DB helpers (synchronous sqlite) ------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS users (
            tg_id INTEGER PRIMARY KEY,
            username TEXT,
            credits INTEGER DEFAULT {DEFAULT_CREDITS},
            cmd_count INTEGER DEFAULT 0,
            slowed_until INTEGER DEFAULT 0
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS redeem_codes (
            code TEXT PRIMARY KEY,
            credits INTEGER NOT NULL,
            remaining_uses INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def ensure_user(tg_id: int, username: Optional[str]):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (tg_id, username, credits) VALUES (?, ?, ?)",
                (tg_id, username or "", DEFAULT_CREDITS))
    cur.execute("UPDATE users SET username = ? WHERE tg_id = ?", (username or "", tg_id))
    conn.commit()
    conn.close()

def get_user(tg_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT tg_id, username, credits, cmd_count, slowed_until FROM users WHERE tg_id = ?", (tg_id,))
    row = cur.fetchone()
    conn.close()
    return row

def change_credits(tg_id: int, delta: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET credits = credits + ? WHERE tg_id = ?", (delta, tg_id))
    conn.commit()
    cur.execute("SELECT credits FROM users WHERE tg_id = ?", (tg_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else None

def inc_cmd_count(tg_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET cmd_count = cmd_count + 1 WHERE tg_id = ?", (tg_id,))
    conn.commit()
    cur.execute("SELECT cmd_count FROM users WHERE tg_id = ?", (tg_id,))
    v = cur.fetchone()[0]
    conn.close()
    return v

def set_cmd_count(tg_id: int, value: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET cmd_count = ? WHERE tg_id = ?", (value, tg_id))
    conn.commit()
    conn.close()

def set_slowed_until(tg_id: int, until_ts: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("UPDATE users SET slowed_until = ? WHERE tg_id = ?", (until_ts, tg_id))
    conn.commit()
    conn.close()

def create_redeem_code(credits: int, uses: int = 1):
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO redeem_codes (code, credits, remaining_uses) VALUES (?, ?, ?)", (code, credits, uses))
    conn.commit()
    conn.close()
    return code

def redeem_code_db(code: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT credits, remaining_uses FROM redeem_codes WHERE code = ?", (code,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return None
    credits, uses = row
    if uses <= 0:
        conn.close()
        return None
    cur.execute("UPDATE redeem_codes SET remaining_uses = remaining_uses - 1 WHERE code = ?", (code,))
    conn.commit()
    conn.close()
    return credits

def get_user_count():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    c = cur.fetchone()[0]
    conn.close()
    return c

# --------------- HTTP helpers -----------------
async def api_request(session, method, payload=None):
    url = f"{API_BASE}/{method}"
    try:
        async with session.post(url, json=payload or {}, timeout=60) as resp:
            return await resp.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

async def send_message(session, chat_id: int, text: str, parse_mode: str = "HTML"):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    return await api_request(session, "sendMessage", payload)

# -------------- Improved OSINT fetch ----------------
async def fetch_osint(number: str):
    """
    Fetch OSINT API, save raw response to file for debugging, and return parsed JSON.
    """
    url = OSINT_API_TEMPLATE.format(term=number)
    async with aiohttp.ClientSession() as s:
        try:
            async with s.get(url, timeout=12) as r:
                text = await r.text()
                # save raw response with timestamp
                ts = int(time.time())
                try:
                    fname = f"osint_{number}_{ts}.json"
                    with open(fname, "w", encoding="utf-8") as f:
                        f.write(f"URL: {url}\n\n")
                        f.write(text)
                except Exception:
                    pass
                # parse JSON if possible
                try:
                    js = json.loads(text)
                    return js
                except Exception:
                    # return raw text info so caller can debug
                    return {"_raw_text": text, "_error": "invalid json"}
        except Exception as e:
            return {"_error": str(e)}

# ---------- helper to normalize different API shapes ----------
def normalize_osint_response(resp):
    """
    Try several heuristics to extract a 'best' record (dict).
    Returns a dict of extracted fields (name,father,email,alt,circle,id_field,address)
    or None if nothing looks like real data.
    """
    if not resp:
        return None

    # treat error-only dicts as none
    if isinstance(resp, dict) and set(resp.keys()) <= {"error", "_error", "_raw_text"}:
        return None

    candidate = None

    # If dict, look for nested containers
    if isinstance(resp, dict):
        # check common container keys
        for k in ("data", "result", "records", "output", "items"):
            if k in resp and resp[k]:
                if isinstance(resp[k], list) and len(resp[k]) > 0 and isinstance(resp[k][0], dict):
                    candidate = resp[k][0]
                    break
                if isinstance(resp[k], dict):
                    candidate = resp[k]
                    break
        # If not found, maybe resp itself is the record
        if candidate is None:
            rec_keys = set(resp.keys())
            if rec_keys & {"name", "fullname", "father", "email", "address", "id", "aadhar", "circle", "operator"}:
                candidate = resp

    # If response is a list, take first dict element
    if candidate is None and isinstance(resp, list) and len(resp) > 0 and isinstance(resp[0], dict):
        candidate = resp[0]

    if not candidate or not isinstance(candidate, dict):
        return None

    def pick(*names, default="N/A"):
        for n in names:
            v = candidate.get(n)
            if v is not None and v != "":
                return v
        return default

    name = pick("name", "fullname", "owner", "username", "full_name")
    father = pick("father", "father_name", "f_name", "parent", "fatherName")
    email = pick("email", "mail", "e_mail")
    alt = pick("alt", "alt_number", "alternate", "secondary", "phone2", "mobile2")
    circle = pick("circle", "network", "operator", "service_provider")
    id_field = pick("id", "nid", "aadhar", "aadhar_no", "id_no", "identification")
    address = pick("address", "addr", "location", "residence", "permanent_address")

    if all(v == "N/A" for v in (name, father, email, alt, circle, id_field, address)):
        return None

    return {
        "name": name,
        "father": father,
        "email": email,
        "alt": alt,
        "circle": circle,
        "id_field": id_field,
        "address": address,
    }

# -------------- Message handling ----------------
def is_owner(uid: int):
    return uid == OWNER_ID

async def process_message(session, msg):
    """
    msg: dict (the message object from Telegram getUpdates)
    """
    if "text" not in msg:
        return
    text = msg["text"].strip()
    chat = msg["chat"]
    chat_id = chat["id"]
    from_user = msg.get("from") or {}
    user_id = from_user.get("id")
    username = from_user.get("username") or from_user.get("first_name") or ""
    ensure_user(user_id, username)

    if not text.startswith("/"):
        return

    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    async def pre_check():
        user = get_user(user_id)
        slowed_until = user[4] if user else 0
        now = int(time.time())
        if slowed_until and slowed_until > now:
            seconds_left = slowed_until - now
            return (f"Slow mode is active ❗\nTry again in {seconds_left} seconds.\n\n"
                    f"To remove slow mode DM @Firedrop_69\nGet removed your slow mode today for only ₹20 ..")
        return None

    # /start
    if cmd == "/start":
        await send_message(session, chat_id, "Welcome to FIRE DROP osint bot . Type /help to see command menu")
        return

    # /help
    if cmd == "/help":
        help_text = ("/num - number info check\n"
                     "/addhar - addhar info check\n"
                     "/gaddi - gaddi info check\n"
                     "/credits - available credits for that user\n\n"
                     "Note :- This is a osint bot that uses leaked database to provide information. "
                     "This might be incorrect sometimes.")
        await send_message(session, chat_id, help_text)
        return

    # /credits
    if cmd in ("/credits", "/credit"):
        user = get_user(user_id)
        credits = user[2] if user else 0
        await send_message(session, chat_id, f"💎 Credits Left: {credits}")
        return

    # lookup commands
    if cmd in ("/num", "/addhar", "/aadhar", "/aadhaar", "/gaddi"):
        if not arg:
            usage = {
                "/num": "Usage: /num <mobile_number>\nExample: /num 9259013600",
                "/addhar": "Usage: /addhar <aadhar_number>\nExample: /addhar 123456789012",
                "/gaddi": "Usage: /gaddi <vehicle_number>\nExample: /gaddi UP32AB1234"
            }
            await send_message(session, chat_id, usage.get(cmd, "Usage: provide argument"))
            return

        pre = await pre_check()
        if pre:
            await send_message(session, chat_id, pre)
            return

        user = get_user(user_id)
        credits = user[2] if user else 0
        if credits <= 0:
            await send_message(session, chat_id, f"Dear {username} you have used all ur credit Dm @Firedrop_69 to buys credits.")
            return

        cmd_c = inc_cmd_count(user_id)

        resp = await fetch_osint(arg)

        normalized = normalize_osint_response(resp)
        if normalized:
            result = TEMPLATE_FOUND.format(number=arg,
                                           name=normalized["name"],
                                           father=normalized["father"],
                                           email=normalized["email"],
                                           alt_number=normalized["alt"],
                                           circle=normalized["circle"],
                                           id_field=normalized["id_field"],
                                           address=normalized["address"],
                                           credits_left=max(0, credits-1))
        else:
            # send debug to owner (short)
            try:
                short = json.dumps(resp) if not isinstance(resp, str) else str(resp)
            except Exception:
                short = str(resp)
            try:
                preview = short if len(short) <= 1500 else short[:1500] + "..."
                await send_message(session, OWNER_ID, f"OSINT parse warning for query `{arg}`:\n\n{preview}")
            except Exception:
                pass
            result = TEMPLATE_NOT_FOUND.format(credits_left=max(0, credits-1))

        # deduct credit
        change_credits(user_id, -1)
        await send_message(session, chat_id, result)

        # slow-mode enforcement
        if cmd_c >= SLOWMODE_AFTER:
            until_ts = int(time.time()) + SLOWMODE_SECONDS
            set_slowed_until(user_id, until_ts)
            set_cmd_count(user_id, 0)
            await send_message(session, chat_id, ("Slow mode is active ❗\n"
                                                  "To gain remove slow mode Dm @Firedrop_69 and\n"
                                                  "Get removed your slow mode today for only ₹20 .."))
        return

    # /redeem
    if cmd == "/redeem":
        if not arg:
            await send_message(session, chat_id, "Usage: /redeem <CODE>")
            return
        code = arg.strip().upper()
        credits = redeem_code_db(code)
        if credits is None:
            await send_message(session, chat_id, "Invalid or expired redeem code.")
            return
        ensure_user(user_id, username)
        new = change_credits(user_id, credits)
        await send_message(session, chat_id, f"Redeemed {credits} credits! New balance: {new}")
        return

    # owner-only commands
    if cmd == "/announcement":
        if not is_owner(user_id):
            await send_message(session, chat_id, "Unauthorized.")
            return
        if not arg:
            await send_message(session, chat_id, "Usage: /announcement <message>")
            return
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT tg_id FROM users")
        rows = cur.fetchall()
        conn.close()
        sent = 0
        for r in rows:
            uid = r[0]
            try:
                await send_message(session, uid, f"📢 Announcement:\n\n{arg}")
                sent += 1
            except Exception:
                pass
        await send_message(session, chat_id, f"Announcement sent to {sent} users.")
        return

    if cmd == "/gen":
        if not is_owner(user_id):
            await send_message(session, chat_id, "Unauthorized.")
            return
        parts = arg.split()
        if len(parts) < 2:
            await send_message(session, chat_id, "Usage: /gen <credit_amount> <uses>")
            return
        try:
            credit_amount = int(parts[0])
            uses = int(parts[1])
        except:
            await send_message(session, chat_id, "Invalid arguments.")
            return
        code = create_redeem_code(credit_amount, uses)
        await send_message(session, chat_id, f"Generated code: {code} (credits: {credit_amount}, uses: {uses})")
        return

    if cmd == "/give":
        if not is_owner(user_id):
            await send_message(session, chat_id, "Unauthorized.")
            return
        parts = arg.split()
        if len(parts) < 2:
            await send_message(session, chat_id, "Usage: /give <all|tg-id> <amount>")
            return
        target = parts[0]
        try:
            amount = int(parts[1])
        except:
            await send_message(session, chat_id, "Amount must be an integer.")
            return
        if target.lower() == "all":
            conn = get_conn()
            cur = conn.cursor()
            cur.execute("SELECT tg_id FROM users")
            rows = cur.fetchall()
            conn.close()
            for r in rows:
                change_credits(r[0], amount)
            await send_message(session, chat_id, f"Gave {amount} credits to all users ({len(rows)} users).")
        else:
            try:
                uid = int(target)
                ensure_user(uid, "")
                new = change_credits(uid, amount)
                await send_message(session, chat_id, f"Gave {amount} credits to {uid}. New balance: {new}")
                try:
                    await send_message(session, uid, f"You received {amount} free credits from the owner! New balance: {new}")
                except Exception:
                    pass
            except:
                await send_message(session, chat_id, "Invalid target.")
        return

    if cmd == "/rem":
        if not is_owner(user_id):
            await send_message(session, chat_id, "Unauthorized.")
            return
        parts = arg.split()
        if not parts:
            await send_message(session, chat_id, "Usage: /rem <tg-id>")
            return
        try:
            uid = int(parts[0])
        except:
            await send_message(session, chat_id, "Invalid tg-id.")
            return
        set_slowed_until(uid, 0)
        change_credits(uid, 5)
        try:
            await send_message(session, uid, "Hello, Thanks for supporting us here is ur free 5 credits and removed soke mode")
        except Exception:
            pass
        await send_message(session, chat_id, f"Removed slow-mode and gave 5 credits to {uid}.")
        return

    # unknown command — ignore or optionally send help
    return

# -------------- Poll loop ----------------
async def poll_updates():
    init_db()
    print("DB initialized. Starting poll loop...")
    offset = 0
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                params = {"timeout": POLL_TIMEOUT, "offset": offset, "allowed_updates": ["message"]}
                async with session.get(f"{API_BASE}/getUpdates", params=params, timeout=POLL_TIMEOUT+10) as resp:
                    data = await resp.json()
                if not data.get("ok"):
                    await asyncio.sleep(1)
                    continue
                updates = data.get("result") or []
                for upd in updates:
                    offset = max(offset, upd["update_id"] + 1)
                    if "message" in upd:
                        # process message in background task
                        asyncio.create_task(process_message(session, upd["message"]))
            except Exception as e:
                print("Polling error:", e)
                await asyncio.sleep(1)

# -------------- Entry point --------------
if __name__ == "__main__":
    try:
        asyncio.run(poll_updates())
    except KeyboardInterrupt:
        print("Stopped by user")
