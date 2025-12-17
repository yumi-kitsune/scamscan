import asyncio
import json
import os
import subprocess
import sys
import csv
import tempfile
from datetime import datetime, timedelta
from typing import List, Tuple, Optional

# 📦 --- Package Installer Helper ---
def ensure_packages():
    required_packages = ["telethon", "requests"]
    missing = []

    for pkg in required_packages:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)

    if missing:
        print("🚩 Missing packages detected: " + ", ".join(missing))
        choice = input("👉 Would you like me to install them now? (y/n): ").strip().lower()
        if choice == 'y':
            subprocess.check_call([sys.executable, "-m", "pip", "install", *missing])
            print("✅ Packages installed! Continuing...\n")
        else:
            print(f"❌ Please install them manually: pip install {' '.join(missing)}")
            sys.exit(1)

# Call the package checker before imports
ensure_packages()

# Safe to import now
import requests
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat
from telethon.tl import functions
from telethon.errors.rpcerrorlist import FloodWaitError, UsernameNotOccupiedError, UsernameInvalidError

# --- Config ---
CONFIG_FILE = 'config.json'
SESSION_NAME = 'userbot_session'
SCAMMER_API = 'https://countersign.chat/api/scammer_ids.json'
IMMUNIZE_CHANNEL = '@scamtrackingCSV'

# --- API Key Setup ---
def setup_api_credentials():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
            if 'api_id' in config and 'api_hash' in config:
                return config['api_id'], config['api_hash']

    print("Let's set up your Telegram API credentials.\n")
    print("1 Go to https://my.telegram.org/auth?to=apps")
    print("2 Log in with your phone number.")
    print("3 Click on 'API Development Tools'.")
    print("4 Create a new app (any name is fine).")
    print("5 Copy your 'API ID' and 'API Hash' below.\n")

    api_id = input("Enter your API ID: ").strip()
    api_hash = input("Enter your API Hash: ").strip()

    with open(CONFIG_FILE, 'w') as f:
        json.dump({'api_id': api_id, 'api_hash': api_hash}, f)

    print("✅ API credentials saved to 'config.json'. Next time this will auto-load!\n")
    print("Enter your phone number in the format of +12345678910\n")
    return api_id, api_hash

# --- Fetch scammer IDs ---
def load_scammer_ids(api_url=SCAMMER_API):
    print(f"🌐 Fetching scammer list ...")
    try:
        response = requests.get(api_url, timeout=30)
        response.raise_for_status()
        scammer_ids = response.json()
        if not isinstance(scammer_ids, list):
            print("⚠️ API response format issue: not a list!")
            return set()
        scammer_set = set(str(x) for x in scammer_ids)
        print(f"✅ Loaded {len(scammer_set)} scammer IDs.\n")
        return scammer_set
    except Exception as e:
        print(f"❌ Error fetching scammer list: {e}")
        return set()

# --- Formatting helpers ---
def name_for_user(user) -> str:
    if getattr(user, "username", None):
        return f"@{user.username}"
    full = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return full if full else "Unknown"

def format_scammer_report(chat_title: str, scammers: List[Tuple[str, str]]) -> str:
    header = f"🚨 Scammer(s) found in **{chat_title}** by ScamScan:"
    lines = [f"• {username} (id `{uid}`)" for uid, username in scammers]
    return header + "\n" + "\n".join(lines)

async def send_report(client: TelegramClient, mode: int, chat, report_text: str):
    """
    mode 1: Console only (handled by caller)
    mode 2: DM Saved Messages (me)
    mode 3: Send to the chat where scammers were found
    """
    if mode == 2:
        try:
            await client.send_message("me", report_text)
            await asyncio.sleep(4)
        except Exception as e:
            print(f"❌ Failed to DM Saved Messages: {e}")
    elif mode == 3:
        try:
            await client.send_message(chat, report_text)
            await asyncio.sleep(4)
        except Exception as e:
            print(f"❌ Failed to send message to chat '{getattr(chat, 'title', chat)}': {e}")

# --- Chat scanning ---
async def dialogs_matching(client: TelegramClient, chat_name: str):
    print("⏳ Fetching your Telegram dialogs... please wait.")
    dialogs = await client.get_dialogs()
    print(f"✅ Found {len(dialogs)} total dialogs.")

    matches = []
    if chat_name.strip() == "":
        print("🔎 No chat name provided → scanning **ALL chats** (groups/channels).")
        for d in dialogs:
            ent = d.entity
            if isinstance(ent, (Channel, Chat)) and getattr(ent, "title", None):
                matches.append(ent)
    else:
        needle = chat_name.lower()
        for d in dialogs:
            ent = d.entity
            if isinstance(ent, (Channel, Chat)) and getattr(ent, "title", None):
                if needle in ent.title.lower():
                    matches.append(ent)

        if not matches:
            print(f"⚠️ No chats found matching '{chat_name}'.")
            return []

        print(f"🔍 Found {len(matches)} chat(s) matching '{chat_name}'.")
    return matches

async def scan_chat_for_scammers(client: TelegramClient, chat, scammer_ids: set, report_mode: int):
    chat_title = getattr(chat, "title", str(chat))
    print(f"\n➡️ Checking chat: '{chat_title}' (ID: {chat.id})")

    try:
        print(f"⏳ Getting participant list for '{chat_title}'...")
        participants = await client.get_participants(chat)
    except Exception as e:
        print(f"❌ Could not retrieve participants for '{chat_title}': {e}")
        return

    scammers_found: List[Tuple[str, str]] = []
    for user in participants:
        uid_str = str(user.id)
        if uid_str in scammer_ids:
            scammers_found.append((uid_str, name_for_user(user)))

    if scammers_found:
        print(f"🚨 Known scammer(s) found in '{chat_title}':")
        for uid, username in scammers_found:
            print(f"    ⚠️ User ID: {uid}, Username: {username}")
        report = format_scammer_report(chat_title, scammers_found)
        await send_report(client, report_mode, chat, report)
    else:
        print(f"✅ No scammers found in '{chat_title}'.")

async def check_chats_for_scammers(client: TelegramClient, chat_name: str, scammer_ids: set, report_mode: int):
    matching_chats = await dialogs_matching(client, chat_name)
    if not matching_chats:
        return

    print("\n📋 Starting scan...\n")
    for idx, chat in enumerate(matching_chats, 1):
        print(f"[{idx}/{len(matching_chats)}]")
        await scan_chat_for_scammers(client, chat, scammer_ids, report_mode)
        await asyncio.sleep(0.2)

# --- Immunize mode (block recent scammer usernames from CSV channel) ---
def _looks_like_csv_filename(name: Optional[str]) -> bool:
    return bool(name) and name.lower().endswith(".csv")

async def fetch_latest_csv_from_channel(client: TelegramClient, channel_username: str) -> str:
    """
    Finds the most recent message (or near-most-recent) in the channel that has a CSV attached.
    Downloads it and returns the local filepath.
    """
    channel = await client.get_entity(channel_username)
    msgs = await client.get_messages(channel, limit=10)

    chosen = None
    for m in msgs:
        if not m:
            continue
        # Look for document name ending in .csv
        if getattr(m, "file", None) and _looks_like_csv_filename(getattr(m.file, "name", None)):
            chosen = m
            break
        # Sometimes name isn't set; fall back to mime-type
        if getattr(m, "document", None) and getattr(m.document, "mime_type", "") == "text/csv":
            chosen = m
            break

    if not chosen:
        raise RuntimeError("No recent CSV attachment found in the last 10 messages.")

    tmpdir = tempfile.mkdtemp(prefix="immunize_")
    out_path = os.path.join(tmpdir, getattr(chosen.file, "name", None) or "scamtracking.csv")
    print(f"⬇️ Downloading CSV from {channel_username} to: {out_path}")
    downloaded = await chosen.download_media(file=out_path)
    if not downloaded or not os.path.exists(downloaded):
        raise RuntimeError("CSV download failed.")
    return downloaded

def parse_to_block_usernames(csv_path: str) -> List[str]:
    """
    CSV columns: user_id, username, last_username_check
    Include username if:
      - last_username_check is within 1 day of now
      - username not in {"DELETED", "None", "", None}
    """
    now = datetime.utcnow()
    cutoff = now - timedelta(days=1)

    to_block = []
    seen = set()

    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        # Normalize headers just in case
        fieldnames = [h.strip() for h in (reader.fieldnames or [])]
        if not {"user_id", "username", "last_username_check"}.issubset(set(fieldnames)):
            raise ValueError(f"CSV missing required columns. Found headers: {fieldnames}")

        for row in reader:
            username = (row.get("username") or "").strip()
            last_check = (row.get("last_username_check") or "").strip()

            if username in ("", "DELETED", "None"):
                continue

            try:
                dt = datetime.fromisoformat(last_check)
            except Exception:
                # If a row has a weird timestamp, skip it (don’t accidentally block stale/unknown data)
                continue

            # Treat naive times as UTC
            if dt.tzinfo is not None:
                dt = dt.astimezone(tz=None).replace(tzinfo=None)

            if dt < cutoff:
                continue

            if not username.startswith("@"):
                username = "@" + username

            if username.lower() not in seen:
                seen.add(username.lower())
                to_block.append(username)

    return to_block

async def block_usernames_slowly(client: TelegramClient, usernames: List[str], delay_seconds: int = 30):
    """
    Tries to block each username, one every delay_seconds seconds.
    """
    if not usernames:
        print("✅ No usernames qualified for blocking (recent + non-deleted).")
        return

    print(f"🛡️ Immunize mode: {len(usernames)} username(s) to block.")
    print(f"⏱️ Blocking cadence: 1 every {delay_seconds} seconds.\n")

    for idx, uname in enumerate(usernames, 1):
        print(f"[{idx}/{len(usernames)}] 🚫 Blocking {uname} ...")
        try:
            ent = await client.get_entity(uname)
            await client(functions.contacts.BlockRequest(id=ent))
            print(f"   ✅ Blocked {uname}")
        except FloodWaitError as e:
            print(f"   ⏳ FloodWait: sleeping {e.seconds}s then continuing...")
            await asyncio.sleep(e.seconds)
        except (UsernameNotOccupiedError, UsernameInvalidError):
            print(f"   ⚠️ Username not resolvable/invalid: {uname} (skipping)")
        except Exception as e:
            print(f"   ❌ Failed to block {uname}: {e}")

        if idx < len(usernames):
            await asyncio.sleep(delay_seconds)

async def immunize_against_scammers(client: TelegramClient):
    """
    1) Look up @scamtrackingCSV
    2) Download most recent attached CSV
    3) Parse usernames with last_username_check within 1 day, excluding DELETED/None
    4) Block them one every 30 seconds
    """
    print(f"🛡️ Immunize mode selected.")
    print(f"📥 Source channel: {IMMUNIZE_CHANNEL}")
    try:
        csv_path = await fetch_latest_csv_from_channel(client, IMMUNIZE_CHANNEL)
    except Exception as e:
        print(f"❌ Could not fetch CSV: {e}")
        return

    try:
        usernames = parse_to_block_usernames(csv_path)
    except Exception as e:
        print(f"❌ Could not parse CSV: {e}")
        return

    await block_usernames_slowly(client, usernames, delay_seconds=30)

# --- Main ---
async def main():
    api_id, api_hash = setup_api_credentials()
    client = TelegramClient(SESSION_NAME, api_id, api_hash)
    await client.start()

    print("\nSelect a function:")
    print("  1) Scan chats for known scammers (Countersign API)")
    print("  2) Immunize (block recent scammer usernames from @scamtrackingCSV)")
    choice = input("Enter 1 / 2: ").strip()

    if choice == "2":
        await immunize_against_scammers(client)
        await client.disconnect()
        input("\n✅ Done! Press Enter to exit...")
        return

    # Default: Scan mode
    scammer_ids = load_scammer_ids()
    if not scammer_ids:
        print("⚠️ No scammer IDs loaded. Please check the API.")
        await client.disconnect()
        return

    print("🔎 Enter the chat name (or partial name) to scan.")
    print("   • Leave it **blank** to scan **ALL** chats (groups/channels).")
    chat_name = input("Chat name (blank = all): ").strip()

    print("\n📣 Choose reporting mode:")
    print("  1) Console only")
    print("  2) Console + send to your Saved Messages when scammers are found")
    print("  3) Console + send to the chat where scammers are found")
    mode_raw = input("Enter 1 / 2 / 3: ").strip()
    try:
        report_mode = int(mode_raw)
    except ValueError:
        report_mode = 1
    if report_mode not in (1, 2, 3):
        print("⚠️ Invalid choice. Defaulting to mode 1 (Console only).")
        report_mode = 1

    print("\n🧭 Summary:")
    print(f"   • Target: {'ALL chats' if chat_name == '' else f'Chats matching: {chat_name!r}'}")
    print(f"   • Reporting: {report_mode} "
          f"({'Console only' if report_mode == 1 else 'Console + Saved Messages' if report_mode == 2 else 'Console + Chat message'})\n")

    await check_chats_for_scammers(client, chat_name, scammer_ids, report_mode)

    await client.disconnect()
    input("\n✅ Done! Press Enter to exit...")

if __name__ == '__main__':
    asyncio.run(main())
