import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Dict, Set, Any

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
from telethon import TelegramClient, events
from telethon.tl.types import Channel, Chat
from telethon.tl import functions
from telethon.errors.rpcerrorlist import FloodWaitError, UsernameNotOccupiedError, UsernameInvalidError
from telethon import utils as tl_utils

# --- Config ---
CONFIG_FILE = 'config.json'
SESSION_NAME = 'userbot_session'
SCAMMER_API_V2 = 'https://countersign.chat/api/scammer_ids_v2.json'
SCAMMER_TOPIC_BASE = "https://t.me/scamtrackinglist"

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

# --- Unified scammer data loader (v2) ---
def load_scammer_data_v2(api_url: str = SCAMMER_API_V2) -> Tuple[Dict[str, Dict[str, Any]], Set[str]]:
    """
    Returns:
      scammer_map: { "<user_id_str>": {"topic_id":..., "message_id":..., "reason":..., "username":..., "full_name":...}, ... }
      scammer_ids: set of user_id strings
    """
    print("🌐 Fetching unified scammer list (v2) ...")
    try:
        response = requests.get(api_url, timeout=30)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or "data" not in payload or not isinstance(payload.get("data"), dict):
            print("⚠️ API response format issue: expected { data: {...} }")
            return {}, set()

        data = payload["data"]
        scammer_map: Dict[str, Dict[str, Any]] = {}
        for k, v in data.items():
            if not isinstance(v, dict):
                continue
            scammer_map[str(k)] = v

        scammer_ids = set(scammer_map.keys())
        count = payload.get("count", len(scammer_ids))
        generated_at = payload.get("generated_at", None)
        if generated_at is not None:
            print(f"✅ Loaded {count} scammers (generated_at={generated_at}).\n")
        else:
            print(f"✅ Loaded {count} scammers.\n")

        return scammer_map, scammer_ids
    except Exception as e:
        print(f"❌ Error fetching scammer list (v2): {e}")
        return {}, set()

# --- Scammer formatting helpers (use v2 data) ---
def topic_link_for_scammer(scammer_info: Dict[str, Any]) -> Optional[str]:
    tid = scammer_info.get("topic_id")
    if tid is None:
        return None
    try:
        tid_int = int(tid)
    except Exception:
        return None
    return f"{SCAMMER_TOPIC_BASE}/{tid_int}"

def scammer_display_name_from_v2(scammer_info: Dict[str, Any]) -> str:
    """
    If username exists -> @username
    else -> full_name
    """
    u = (scammer_info.get("username") or "").strip()
    if u and u.lower() != "none" and u.lower() != "deleted":
        if not u.startswith("@"):
            u = "@" + u
        return u

    fn = (scammer_info.get("full_name") or "").strip()
    return fn if fn else "Unknown"

def name_for_telegram_user_fallback(user) -> str:
    if getattr(user, "username", None):
        return f"@{user.username}"
    full = f"{getattr(user, 'first_name', '') or ''} {getattr(user, 'last_name', '') or ''}".strip()
    return full if full else "Unknown"

def format_scammer_report(chat_title: str, scammers: List[Tuple[str, str, Optional[str]]]) -> str:
    header = f"🚨 Scammer(s) found in **{chat_title}** by ScamScan:"
    lines = []
    for uid, display, tlink in scammers:
        if tlink:
            lines.append(f"• {display} (id `{uid}`) — topic: {tlink}")
        else:
            lines.append(f"• {display} (id `{uid}`)")
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
            await asyncio.sleep(2)
        except Exception as e:
            print(f"❌ Failed to DM Saved Messages: {e}")
    elif mode == 3:
        try:
            await client.send_message(chat, report_text)
            await asyncio.sleep(2)
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

async def scan_chat_for_scammers(
    client: TelegramClient,
    chat,
    scammer_ids: Set[str],
    scammer_map: Dict[str, Dict[str, Any]],
    report_mode: int
):
    chat_title = getattr(chat, "title", str(chat))
    print(f"\n➡️ Checking chat: '{chat_title}' (ID: {chat.id})")

    try:
        print(f"⏳ Getting participant list for '{chat_title}'...")
        participants = await client.get_participants(chat)
    except Exception as e:
        print(f"❌ Could not retrieve participants for '{chat_title}': {e}")
        return

    scammers_found: List[Tuple[str, str, Optional[str]]] = []
    for user in participants:
        uid_str = str(user.id)
        if uid_str in scammer_ids:
            info = scammer_map.get(uid_str, {})
            display = scammer_display_name_from_v2(info) if info else name_for_telegram_user_fallback(user)
            tlink = topic_link_for_scammer(info) if info else None
            scammers_found.append((uid_str, display, tlink))

    if scammers_found:
        print(f"🚨 Known scammer(s) found in '{chat_title}':")
        for uid, display, tlink in scammers_found:
            if tlink:
                print(f"    ⚠️ {display} (id {uid}) topic: {tlink}")
            else:
                print(f"    ⚠️ {display} (id {uid})")

        report = format_scammer_report(chat_title, scammers_found)
        await send_report(client, report_mode, chat, report)
    else:
        print(f"✅ No scammers found in '{chat_title}'.")

async def check_chats_for_scammers(
    client: TelegramClient,
    chat_name: str,
    scammer_ids: Set[str],
    scammer_map: Dict[str, Dict[str, Any]],
    report_mode: int
):
    matching_chats = await dialogs_matching(client, chat_name)
    if not matching_chats:
        return

    print("\n📋 Starting scan...\n")
    for idx, chat in enumerate(matching_chats, 1):
        print(f"[{idx}/{len(matching_chats)}]")
        await scan_chat_for_scammers(client, chat, scammer_ids, scammer_map, report_mode)
        await asyncio.sleep(0.2)
        

# --- Immunize mode (block scammers via usernames from unified API v2) ---

def build_usernames_to_block_from_v2(
    scammer_ids: Set[str],
    scammer_map: Dict[str, Dict[str, Any]]
) -> List[str]:
    """
    Build a de-duped list of @usernames to block from v2 data.
    - Ignores username values that are None / "" / "None" / "DELETED"
    - Normalizes to "@username"
    - Dedupes case-insensitively
    """
    out: List[str] = []
    seen: Set[str] = set()

    for uid_str in scammer_ids:
        info = scammer_map.get(uid_str) or {}
        u = (info.get("username") or "").strip()

        if not u:
            continue
        if u.lower() in ("none", "deleted"):
            continue

        if not u.startswith("@"):
            u = "@" + u

        key = u.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(u)

    # Optional: stable ordering (alphabetical) to be deterministic
    out.sort(key=lambda x: x.lower())
    return out


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


async def immunize_against_scammers(
    client: TelegramClient,
    scammer_ids: Set[str],
    scammer_map: Dict[str, Dict[str, Any]]
):
    """
    Immunize mode (v2):
      - Build list of scammer usernames from v2 payload
      - Block them one every 30 seconds
    """
    print("🛡️ Immunize mode selected (using unified API v2 usernames).")
    usernames = build_usernames_to_block_from_v2(scammer_ids, scammer_map)

    # If you want visibility into how many got filtered out:
    print(f"🧾 Usernames extracted from v2: {len(usernames)} (ignored None/DELETED/etc.)\n")

    await block_usernames_slowly(client, usernames, delay_seconds=30)

# --- Overwatch mode (passive monitoring) ---
def _internal_id_from_peer(chat_id: int) -> str:
    s = str(chat_id)
    if s.startswith("-100"):
        return s[4:]
    if chat_id < 0:
        return str(-chat_id)
    return str(chat_id)

def _chat_link(entity, chat_id: int) -> str:
    uname = getattr(entity, "username", None)
    if uname:
        return f"https://t.me/{uname}"
    return f"https://t.me/c/{_internal_id_from_peer(chat_id)}"

def _chat_link_for_message(entity, chat_id: int, msg_id: int) -> str:
    uname = getattr(entity, "username", None)
    if uname:
        return f"https://t.me/{uname}/{msg_id}"
    return f"https://t.me/c/{_internal_id_from_peer(chat_id)}/{msg_id}"

async def _build_group_allowlist(client: TelegramClient) -> Set[int]:
    allow = set()
    dialogs = await client.get_dialogs()
    for d in dialogs:
        ent = d.entity
        if not isinstance(ent, (Channel, Chat)):
            continue
        if isinstance(ent, Channel) and not getattr(ent, "megagroup", False):
            continue
        pc = getattr(ent, "participants_count", None)
        if isinstance(pc, int) and pc > 2:
            allow.add(d.id)
    return allow

async def _is_user_still_in_chat_via_common_chats(client: TelegramClient, user_id: int, chat_id: int) -> Optional[bool]:
    try:
        u = await client.get_entity(user_id)
        res = await client(functions.messages.GetCommonChatsRequest(
            user_id=u,
            max_id=0,
            limit=100
        ))
        internal = int(_internal_id_from_peer(chat_id))
        for ch in (res.chats or []):
            cid = getattr(ch, "id", None)
            if cid is None:
                continue
            if cid == internal or cid == chat_id:
                return True
        return False
    except FloodWaitError as e:
        print(f"   ⏳ FloodWait in common chats check: sleeping {e.seconds}s")
        await asyncio.sleep(e.seconds)
        return None
    except Exception:
        return None

async def _send_saved_message_reminder_in_xm(client: TelegramClient, text: str):
    """
    Send to Saved Messages as a *scheduled* message 5 minutes in the future,
    so it pings like a reminder rather than being silently delivered.
    This is kinda jank but.. works sometimes...
    """
    try:
        me_peer = await client.get_input_entity("me")
        schedule_date = datetime.now() + timedelta(minutes=5)
        await client(functions.messages.SendMessageRequest(
            peer=me_peer,
            message=text,
            schedule_date=schedule_date
        ))
    except FloodWaitError as e:
        print(f"   ⏳ FloodWait while scheduling reminder: sleeping {e.seconds}s then retrying once")
        await asyncio.sleep(e.seconds)
        try:
            me_peer = await client.get_input_entity("me")
            schedule_date = datetime.now() + timedelta(minutes=5)
            await client(functions.messages.SendMessageRequest(
                peer=me_peer,
                message=text,
                schedule_date=schedule_date
            ))
        except Exception as e2:
            print(f"❌ Failed to schedule Saved Messages reminder: {e2}")
    except Exception as e:
        print(f"❌ Failed to schedule Saved Messages reminder: {e}")

# --- Overwatch auto-refresh helpers (12h) ---

OVERWATCH_DIALOG_REFRESH_SECONDS = 12 * 60 * 60  # 12 hours
OVERWATCH_SCAMMER_REFRESH_SECOND = 60 * 60  # 1 hour

async def _refresh_scammer_data_periodically(
    client: TelegramClient,
    state: Dict[str, Any],
    state_lock: asyncio.Lock,
    stop_event: asyncio.Event,
    refresh_seconds: int = OVERWATCH_SCAMMER_REFRESH_SECOND,
):
    """
    Periodically refresh scammer_map + scammer_ids from Unified API v2.
    Uses asyncio.to_thread so requests.get() doesn't block the event loop.
    """
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=refresh_seconds)
            break  # stop requested
        except asyncio.TimeoutError:
            pass

        print("🔄 Overwatch refresh: fetching updated scammer list (v2) ...")
        try:
            new_map, new_ids = await asyncio.to_thread(load_scammer_data_v2)
            if not new_ids:
                print("⚠️ Overwatch refresh: scammer list refresh returned empty; keeping old list.")
                continue

            async with state_lock:
                state["scammer_map"] = new_map
                state["scammer_ids"] = new_ids

            print(f"✅ Overwatch refresh: updated scammer list: {len(new_ids)} scammers.")
        except Exception as e:
            print(f"❌ Overwatch refresh: failed to update scammer list: {e}")


async def _refresh_allowlist_periodically(
    client: TelegramClient,
    state: Dict[str, Any],
    state_lock: asyncio.Lock,
    stop_event: asyncio.Event,
    refresh_seconds: int = OVERWATCH_DIALOG_REFRESH_SECONDS,
):
    """
    Periodically refresh the allowlist based on latest dialogs (>2 users).
    """
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=refresh_seconds)
            break
        except asyncio.TimeoutError:
            pass

        print("🔄 Overwatch refresh: fetching updated dialogs / allowlist ...")
        try:
            new_allow = await _build_group_allowlist(client)
            async with state_lock:
                state["allowlist"] = new_allow
            print(f"✅ Overwatch refresh: updated allowlist: {len(new_allow)} chats.")
        except Exception as e:
            print(f"❌ Overwatch refresh: failed to update allowlist: {e}")


# --- Overwatch mode (passive monitoring) ---
async def overwatch_mode(
    client: TelegramClient,
    scammer_ids: Set[str],
    scammer_map: Dict[str, Dict[str, Any]],
    overwatch_report_mode: int
):
    """
    overwatch_report_mode:
      1) terminal only
      2) terminal + Saved Messages (scheduled reminder in 2 minutes)
      3) terminal + Saved Messages (scheduled) + also send to the group (rate-limited per scammer per group per day)

    - Every 12 hours, refresh:
      - dialogs
    -- Every hour, refresh:
      - scammer list
    """
    print("🛰️ Overwatch mode enabled.")
    print("   - Listening for scammer messages and join/leave events")
    print("   - Monitoring chats with >2 users (best-effort, based on dialog participant counts)")
    print("   - Scammer alerts include a link to the scammer's topic\n")
    print("📣 Overwatch reporting:")
    print(f"   - Mode: {overwatch_report_mode} "
          f"({'terminal only' if overwatch_report_mode == 1 else 'terminal + saved messages' if overwatch_report_mode == 2 else 'terminal + saved + group'})")
    if overwatch_report_mode in (2, 3):
        print("   - Saved Messages are sent as a scheduled reminder (2 minutes in the future) to ping you.\n")
    else:
        print()

    # Shared, refreshable state
    state_lock = asyncio.Lock()
    stop_event = asyncio.Event()
    state: Dict[str, Any] = {
        "allowlist": set(),            # Set[int]
        "scammer_ids": set(scammer_ids),  # Set[str]
        "scammer_map": dict(scammer_map), # Dict[str, Dict[str, Any]]
    }

    print("Reading groups...")
    initial_allowlist = await _build_group_allowlist(client)
    async with state_lock:
        state["allowlist"] = initial_allowlist
    print(f"✅ Overwatch allowlist ready: {len(initial_allowlist)} chat(s) with >2 users.\n")

    # Start periodic refresh tasks
    refresh_tasks = [
        asyncio.create_task(_refresh_allowlist_periodically(client, state, state_lock, stop_event)),
        asyncio.create_task(_refresh_scammer_data_periodically(client, state, state_lock, stop_event)),
    ]

    # Soft dedupe to avoid tight repeats (terminal + optional sends)
    last_notified: Dict[Tuple[str, int, str, str], float] = {}
    DEDUPE_SECONDS = 30.0

    # Hard rate-limit for group sending (mode 3): 1 alert per scammer per group per day
    group_last_sent: Dict[Tuple[int, str], float] = {}
    GROUP_LIMIT_SECONDS = 86400.0  # 1 day

    async def maybe_send_to_group_with_daily_limit(chat_entity, chat_id: int, uid_str: str, text: str) -> bool:
        key = (chat_id, uid_str)
        now = time.time()
        last = group_last_sent.get(key, 0.0)
        if (now - last) < GROUP_LIMIT_SECONDS:
            return False
        try:
            await client.send_message(chat_entity, text)
            group_last_sent[key] = now
            return True
        except FloodWaitError as e:
            print(f"   ⏳ FloodWait while sending to group: sleeping {e.seconds}s (suppressing this send)")
            await asyncio.sleep(e.seconds)
            return False
        except Exception as e:
            print(f"❌ Failed to send alert to group '{getattr(chat_entity, 'title', chat_entity)}': {e}")
            return False

    async def notify(kind: str, chat_entity, chat_id: int, uid_str: str, extra_key: str, text: str):
        """
        Applies a short dedupe; then:
          - mode 2/3: schedule reminder to Saved Messages (2 min)
          - mode 3: attempt send to group once/day per (chat,scammer)
        """
        key = (kind, chat_id, uid_str, extra_key)
        now = time.time()
        if (now - last_notified.get(key, 0.0)) < DEDUPE_SECONDS:
            return
        last_notified[key] = now

        if overwatch_report_mode in (2, 3):
            await _send_saved_message_reminder_in_xm(client, text)

        if overwatch_report_mode == 3 and chat_entity is not None:
            sent = await maybe_send_to_group_with_daily_limit(chat_entity, chat_id, uid_str, text)
            if not sent:
                print(f"ℹ️ Overwatch: group alert suppressed (daily limit) for scammer {uid_str} in chat {chat_id}")

    async def delayed_join_verify(
        chat_entity,
        chat_id: int,
        chat_title: str,
        chat_link: str,
        uid_str: str,
        scammer_display: str,
        scammer_topic: Optional[str]
    ):
        await asyncio.sleep(120)
        try:
            uid_int = int(uid_str)
        except Exception:
            return

        still = await _is_user_still_in_chat_via_common_chats(client, uid_int, chat_id)

        topic_line = f"• Scammer topic: {scammer_topic}\n" if scammer_topic else ""
        if still is True:
            text = (
                f"✅ **Scammer joined chat**\n"
                f"• Chat: **{chat_title}** (`{chat_id}`)\n"
                f"• Chat link: {chat_link}\n"
                f"• Scammer: {scammer_display} (id `{uid_str}`)\n"
                f"{topic_line}"
            ).rstrip()
            print(f"✅ Overwatch verify: still in '{chat_title}': {scammer_display} ({uid_str})")
            await notify("verify", chat_entity, chat_id, uid_str, "still", text)

        elif still is False:
            pass
            print(f"⚠️ Overwatch verify: gone from '{chat_title}': {scammer_display} ({uid_str})")

        else:
            text = (
                f"✅ **Scammer joined chat**\n"
                f"• Chat: **{chat_title}** (`{chat_id}`)\n"
                f"• Chat link: {chat_link}\n"
                f"• Scammer: {scammer_display} (id `{uid_str}`)\n"
                f"{topic_line}"
            )
            print(f"ℹ️ Overwatch verify: inconclusive (but likely in) for '{chat_title}': {scammer_display} ({uid_str})")

    @client.on(events.NewMessage())
    async def on_new_message(event: events.NewMessage.Event):
        chat_id = event.chat_id
        if chat_id is None:
            return

        # Snapshot allowlist + scammer set
        async with state_lock:
            allowlist = state["allowlist"]
            scammer_ids_local = state["scammer_ids"]
            scammer_map_local = state["scammer_map"]

        if chat_id not in allowlist:
            return

        sender = await event.get_sender()
        if not sender:
            return

        uid = getattr(sender, "id", None)
        if uid is None:
            return

        uid_str = str(uid)
        if uid_str not in scammer_ids_local:
            return

        try:
            chat_entity = await event.get_chat()
        except Exception:
            chat_entity = None

        chat_title = getattr(chat_entity, "title", None) or "(unknown chat)"
        msg_link = _chat_link_for_message(chat_entity, chat_id, event.message.id)
        chat_link = _chat_link(chat_entity, chat_id)

        info = scammer_map_local.get(uid_str, {})
        scammer_display = scammer_display_name_from_v2(info) if info else name_for_telegram_user_fallback(sender)
        scammer_topic = topic_link_for_scammer(info) if info else None

        msg_preview = (event.raw_text or "").strip()
        if len(msg_preview) > 400:
            msg_preview = msg_preview[:400] + "…"

        topic_line = f"• Scammer topic: {scammer_topic}\n" if scammer_topic else ""
        text = (
            f"🚨 **Scammer message detected**\n"
            f"• Chat: **{chat_title}**\n"
            f"• Scammer: {scammer_display} (id `{uid_str}`)\n"
            f"{topic_line}"
            f"• Message link: {msg_link}"
            #f"Message:\n{msg_preview if msg_preview else '(no text)'}"
        )
        print(f"🚨 Overwatch: scammer message in '{chat_title}' by {scammer_display} ({uid_str}) -> {msg_link}")
        await notify("msg", chat_entity, chat_id, uid_str, str(event.message.id), text)

    @client.on(events.ChatAction())
    async def on_chat_action(event: events.ChatAction.Event):
        chat_id = event.chat_id
        if chat_id is None:
            return

        # Snapshot allowlist + scammer set
        async with state_lock:
            allowlist = state["allowlist"]
            scammer_ids_local = state["scammer_ids"]
            scammer_map_local = state["scammer_map"]

        if chat_id not in allowlist:
            return

        joined = bool(event.user_joined or event.user_added)
        left = bool(event.user_left or event.user_kicked)
        if not (joined or left):
            return

        try:
            chat_entity = await event.get_chat()
        except Exception:
            chat_entity = None

        chat_title = getattr(chat_entity, "title", None) or "(unknown chat)"
        chat_link = _chat_link(chat_entity, chat_id)

        uid = getattr(event, "user_id", None)
        if uid is None:
            try:
                u = await event.get_user()
                uid = getattr(u, "id", None)
            except Exception:
                uid = None
        if uid is None:
            return

        uid_str = str(uid)
        if uid_str not in scammer_ids_local:
            return

        info = scammer_map_local.get(uid_str, {})
        scammer_display = scammer_display_name_from_v2(info) if info else uid_str
        scammer_topic = topic_link_for_scammer(info) if info else None
        topic_line = f"• Scammer topic: {scammer_topic}\n" if scammer_topic else ""

        action = "joined" if joined else "left"

        text = (
            f"🚨 **Scammer {action} detected**\n"
            f"• Chat: **{chat_title}** (`{chat_id}`)\n"
            f"• Chat link: {chat_link}\n"
            f"• Scammer: {scammer_display} (id `{uid_str}`)\n"
            f"{topic_line}"
        ).rstrip()

        print(f"🚨 Overwatch: scammer {action} in '{chat_title}': {scammer_display} ({uid_str})")
        await notify("action", chat_entity, chat_id, uid_str, action, text)

        if joined:
            asyncio.create_task(delayed_join_verify(
                chat_entity,
                chat_id,
                chat_title,
                chat_link,
                uid_str,
                scammer_display,
                scammer_topic
            ))

    print("🟢 Overwatch is running. Press Ctrl+C to stop.\n")

    try:
        await client.run_until_disconnected()
    except KeyboardInterrupt:
        print("\n🛑 Overwatch stopping (Ctrl+C).")
    finally:
        # stop periodic refresh tasks
        stop_event.set()
        for t in refresh_tasks:
            t.cancel()
        await asyncio.gather(*refresh_tasks, return_exceptions=True)

        try:
            await client.disconnect()
        except Exception:
            pass

# --- Main ---
async def main():
    api_id, api_hash = setup_api_credentials()
    client = TelegramClient(SESSION_NAME, api_id, api_hash)
    await client.start()

    # Unified load once at start for all modes
    scammer_map, scammer_ids = load_scammer_data_v2()
    if not scammer_ids:
        print("⚠️ No scammer data loaded. Please check the API.")
        await client.disconnect()
        return

    print("\nSelect a function:")
    print("  1) Scan chats for known scammers (Unified API v2)")
    print("  2) Immunize (block scammers from Unified API v2)")
    print("  3) Overwatch (stay online and alert on scammer messages / join / leave)")
    choice = input("Enter 1 / 2 / 3: ").strip()

    if choice == "2":
        await immunize_against_scammers(client, scammer_ids, scammer_map)
        await client.disconnect()
        input("\n✅ Done! Press Enter to exit...")
        return

    if choice == "3":
        print("\n📣 Overwatch reporting mode:")
        print("  1) Terminal only")
        print("  2) Terminal + Saved Messages (scheduled reminder in 2 min)")
        print("  3) Terminal + Saved Messages (scheduled) + send to group (limited: 1 alert per scammer per group per day)")
        ow_raw = input("Enter 1 / 2 / 3: ").strip()
        try:
            overwatch_report_mode = int(ow_raw)
        except ValueError:
            overwatch_report_mode = 1
        if overwatch_report_mode not in (1, 2, 3):
            print("⚠️ Invalid choice. Defaulting to Overwatch mode 1 (Terminal only).")
            overwatch_report_mode = 1

        await overwatch_mode(client, scammer_ids, scammer_map, overwatch_report_mode)
        return

    # Default: Scan mode
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

    await check_chats_for_scammers(client, chat_name, scammer_ids, scammer_map, report_mode)

    await client.disconnect()
    input("\n✅ Done! Press Enter to exit...")

if __name__ == '__main__':
    asyncio.run(main())
