import asyncio
import json
import os
import subprocess
import sys
from typing import Iterable, List, Tuple

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

# --- Config ---
CONFIG_FILE = 'config.json'
SESSION_NAME = 'userbot_session'
SCAMMER_API = 'https://countersign.chat/api/scammer_ids.json'

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
    # Prefer @username, fall back to "First Last" or "Unknown"
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
    # Mode 2
    if mode == 2:
        try:
            await client.send_message("me", report_text)
            await asyncio.sleep(4)  # gentle pacing
        except Exception as e:
            print(f"❌ Failed to DM Saved Messages: {e}")

    # Mode 3
    elif mode == 3:
        try:
            await client.send_message(chat, report_text)
            await asyncio.sleep(4)
        except Exception as e:
            print(f"❌ Failed to send message to chat '{getattr(chat, 'title', chat)}': {e}")

# --- Chat scanning ---
async def dialogs_matching(client: TelegramClient, chat_name: str):
    """
    Returns a list of entities (Channel/Chat/User) matching the given name.
    If chat_name is empty, returns all dialogs that are Chat or Channel (skips one-on-one users).
    """
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
                title = ent.title
                if needle in title.lower():
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
        # You can tune limit if needed; Telethon paginates internally.
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

        # Build a single consolidated message per chat
        report = format_scammer_report(chat_title, scammers_found)
        # Always console; additional send depends on mode
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
        # Light pacing to be nice to Telegram servers
        await asyncio.sleep(0.2)

# --- Main ---
async def main():
    api_id, api_hash = setup_api_credentials()

    client = TelegramClient(SESSION_NAME, api_id, api_hash)
    await client.start()
    
    scammer_ids = load_scammer_ids()
    if not scammer_ids:
        print("⚠️ No scammer IDs loaded. Please check the API.")
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
