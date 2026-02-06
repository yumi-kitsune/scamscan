__version__ = "0.3.6"
__force__ = False
## Version info. Force should force existing clients to exit.

import asyncio
import json
import os
import subprocess
import sys
import time
import re
from collections import defaultdict, deque
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
from telethon.tl.types import Channel, Chat, MessageActionChatAddUser, MessageActionChatJoinedByLink, ChannelParticipantsRecent
from telethon.tl import functions
from telethon.errors.rpcerrorlist import FloodWaitError, UsernameNotOccupiedError, UsernameInvalidError, UserIdInvalidError, UserPrivacyRestrictedError

# --- Config ---
CONFIG_FILE = 'config.json'
SESSION_NAME = 'userbot_session'
SCAMMER_API_V2 = 'https://countersign.chat/api/scammer_ids_v2.json'
SCAMMER_TOPIC_BASE = "https://t.me/scamtrackinglist"

GITHUB_OWNER = "yumi-kitsune"
GITHUB_REPO = "scamscan"
GITHUB_SCRIPT_PATH = "scan.py"
GITHUB_RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/{GITHUB_SCRIPT_PATH}"
UPDATE_CHECK_SECONDS = 2 * 60 * 60  # 2 hours

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

# --- Github Auto Update ---
_VERSION_RE = re.compile(r'^\s*__version__\s*=\s*["\']([^"\']+)["\']\s*$', re.MULTILINE)
_FORCE_RE   = re.compile(r'^\s*__force__\s*=\s*(True|False)\s*$', re.MULTILINE)

def _parse_version(v: str):
    """
    Semver-ish: '0.3.0' -> (0,3,0). Accepts 'v0.3.0' too.
    Non-digits ignored; missing parts default to 0.
    """
    v = (v or "").strip()
    if v.lower().startswith("v"):
        v = v[1:]
    parts = re.split(r"[^\d]+", v)
    nums = [int(p) for p in parts if p.isdigit()]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])

def _is_remote_newer(remote: str, local: str) -> bool:
    return _parse_version(remote) > _parse_version(local)

def _extract_remote_version_and_force(py_text: str) -> tuple[Optional[str], Optional[bool]]:
    """
    Extract __version__ and __force__ from script text via regex (no exec).
    """
    if not py_text:
        return None, None

    m_ver = _VERSION_RE.search(py_text)
    m_for = _FORCE_RE.search(py_text)

    remote_version = m_ver.group(1).strip() if m_ver else None
    remote_force = (m_for.group(1) == "True") if m_for else None
    return remote_version, remote_force

def fetch_remote_script_text(url: str) -> Optional[str]:
    """
    Downloads the raw script text. Returns None on failure.
    """
    try:
        r = requests.get(
            url + "?" + str(time.time()),
            timeout=15,
            headers={"User-Agent": "ScamScan-Overwatch", 'Cache-Control': 'no-cache'},
        )
        if r.status_code != 200:
            return None
        return r.text
    except Exception:
        return None

def check_for_update_once(
    local_version: str,
    local_force: bool,
    raw_url: str,
    *,
    print_prefix: str = "🔎 Update check",
) -> dict:
    """
    Checks remote scan.py for __version__/__force__.
    Always returns a dict describing status.
    May call sys.exit if remote has force=True and local is behind.
    """
    result = {
        "ok": False,
        "local_version": local_version,
        "local_force": local_force,
        "remote_version": None,
        "remote_force": None,
        "update_available": False,
        "forced_update_required": False,
        "error": None,
        "url": raw_url,
    }

    text = fetch_remote_script_text(raw_url)
    if not text:
        result["error"] = "fetch_failed"
        print(f"{print_prefix}: ⚠️ unable to fetch remote version info ({raw_url})")
        return result

    remote_version, remote_force = _extract_remote_version_and_force(text)
    result["remote_version"] = remote_version
    result["remote_force"] = remote_force

    if not remote_version:
        result["error"] = "parse_failed"
        print(f"{print_prefix}: ⚠️ fetched remote file but couldn't parse __version__")
        return result

    result["ok"] = True
    update_available = _is_remote_newer(remote_version, local_version)
    result["update_available"] = update_available

    forced_required = bool(update_available and (remote_force is True))
    result["forced_update_required"] = forced_required

    # You asked: "post a message in the terminal noting it every time the 2 hour cycle runs."
    # So print a line *every time*.
    if update_available:
        force_note = " (FORCED)" if forced_required else ""
        print(f"{print_prefix}: ⬆️ update available{force_note} — local={local_version} remote={remote_version} force={remote_force}")
    else:
        print(f"{print_prefix}: ✅ up to date — local={local_version} remote={remote_version} force={remote_force}")

    # If remote requires force update, refuse to run
    if forced_required:
        print("\n🛑 This version is blocked by upstream __force__.")
        print(f"   • Your version:  {local_version}")
        print(f"   • Required:      {remote_version}")
        print(f"   • Update from:   {raw_url}\n")
        sys.exit(3)

    return result

async def periodic_update_checker(
    stop_event: asyncio.Event,
    *,
    local_version: str,
    local_force: bool,
    raw_url: str,
    interval_seconds: int,
):
    """
    Runs update checks every interval_seconds until stop_event is set.
    Uses asyncio.to_thread to keep requests off the event loop.
    """
    # Check immediately on start of this task too (optional but useful)
    await asyncio.to_thread(
        check_for_update_once,
        local_version,
        local_force,
        raw_url,
        print_prefix="🔎 Update check (periodic)",
    )

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
            break
        except asyncio.TimeoutError:
            pass

        await asyncio.to_thread(
            check_for_update_once,
            local_version,
            local_force,
            raw_url,
            print_prefix="🔎 Update check (periodic)",
        )

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
    print(f"🧾 Usernames extracted from v2: {len(usernames)} (ignored None/DELETED/etc.)\n")
    await block_usernames_slowly(client, usernames, delay_seconds=30)

# --- Overwatch mode helpers ---
def _extract_action_user_ids(msg) -> list[int]:
    """
    Returns user IDs involved in a join/add service message, else [].
    """
    action = getattr(msg, "action", None)
    if isinstance(action, MessageActionChatAddUser):
        # action.users is a list of user IDs (ints)
        return [int(x) for x in (action.users or [])]
    if isinstance(action, MessageActionChatJoinedByLink):
        # This action doesn't directly include the joiner, but the service message
        # usually has from_id = joiner in many chats. We'll fallback to that.
        from_id = getattr(msg, "from_id", None)
        uid = getattr(from_id, "user_id", None)
        return [int(uid)] if uid else []
    return []

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
    except Exception as e:
        print('E-userfind', e)
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

def _is_long_time_ago_status(user) -> bool:
    st = getattr(user, "status", None)
    # In Telethon/MTProto, "last seen a long time ago" corresponds to UserStatusEmpty.
    return st is not None and st.__class__.__name__ == "UserStatusEmpty"
    
async def _try_resolve_user_entity(
    client: TelegramClient,
    user_id: int,
    username: Optional[str] = None,
):
    """
    Try get_entity(user_id) first, then get_entity(username) if provided.
    Returns (user_entity_or_None, how_string)
    """
    # 1) by id
    try:
        u = await client.get_entity(user_id)
        return u, "id"
    except Exception as e_id:
        # 2) by username (if present)
        if username:
            ustr = username.strip()
            if ustr:
                if not ustr.startswith("@"):
                    ustr = "@" + ustr
                try:
                    u = await client.get_entity(ustr)
                    return u, f"username:{ustr}"
                except Exception:
                    pass
        return None, f"fail:{e_id!r}"
        
async def _recent_participants_contains_user(
    client: TelegramClient,
    chat_entity,
    target_user_id: int,
    limit: int = 100,
) -> Optional[bool]:
    """
    One API call: recent participants. Works for Channels/Megagroups (not basic Chats).
    Returns True/False/None (None on unsupported/error).
    """
    try:
        res = await client(functions.channels.GetParticipantsRequest(
            channel=chat_entity,
            filter=ChannelParticipantsRecent(),
            offset=0,
            limit=limit,
            hash=0
        ))
        for u in (res.users or []):
            if getattr(u, "id", None) == target_user_id:
                return True
        return False
    except FloodWaitError as e:
        print(f"   ⏳ FloodWait in recent participants: sleeping {e.seconds}s")
        await asyncio.sleep(e.seconds)
        return None
    except Exception as e:
        # Often "CHAT_ADMIN_REQUIRED" or "CHANNEL_INVALID" or "not a channel"
        print(f"   ℹ️ recent participants check unavailable: {e}")
        return None

async def _verify_user_presence_stepped(
    client: TelegramClient,
    chat_entity,
    chat_id: int,
    user_id: int,
    *,
    username: Optional[str] = None,
    recent_limit: int = 100,
) -> tuple[Optional[bool], str]:

    u, how = await _try_resolve_user_entity(client, user_id, username=username)

    if u is None:
        # Can't resolve entity -> go straight to recent participants
        if chat_entity is not None:
            rp = await _recent_participants_contains_user(client, chat_entity, user_id, limit=recent_limit)
            if rp is True:
                return True, f"resolve_failed:{how} -> recent_participants:yes"
            if rp is False:
                return False, f"resolve_failed:{how} -> recent_participants:no"
        return None, f"resolve_failed:{how} -> recent_participants:unavailable"

    # ✅ Your requested behavior: if it looks "blocked" (long time ago), don't bother
    # waiting for a “status change”; jump to recent participants immediately.
    if _is_long_time_ago_status(u):
        if chat_entity is not None:
            rp = await _recent_participants_contains_user(client, chat_entity, user_id, limit=recent_limit)
            if rp is True:
                return True, f"status:long_ago ({how}) -> recent_participants:yes"
            if rp is False:
                return False, f"status:long_ago ({how}) -> recent_participants:no"
            return None, f"status:long_ago ({how}) -> recent_participants:unavailable"
        return None, f"status:long_ago ({how}) -> no_chat_entity"

    # Otherwise try common chats (cheap)
    try:
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
                return True, f"common_chats:yes ({how})"
        return False, f"common_chats:no ({how})"

    except FloodWaitError as e:
        print(f"   ⏳ FloodWait in common chats check: sleeping {e.seconds}s")
        await asyncio.sleep(e.seconds)
        return None, f"common_chats:floodwait ({how})"

    except Exception as e:
        # If common chats fails (including access_hash/input entity weirdness), fall back
        if chat_entity is not None:
            rp = await _recent_participants_contains_user(client, chat_entity, user_id, limit=recent_limit)
            if rp is True:
                return True, f"common_chats:error ({how}) {e!r} -> recent_participants:yes"
            if rp is False:
                return False, f"common_chats:error ({how}) {e!r} -> recent_participants:no"
            return None, f"common_chats:error ({how}) {e!r} -> recent_participants:unavailable"
        return None, f"common_chats:error ({how}) {e!r}"

# --- Overwatch duplicate detection (mode 3) ---
DUPLICATE_WINDOW_SECONDS = 10 * 60   # 10 minutes
DUPLICATE_PRUNE_SECONDS  = 12 * 60   # prune slightly beyond window
DUPLICATE_MARKER_EMOJI = "🚨"

_UID_RE = re.compile(r"\b\d{6,}\b")  # telegram user ids are usually 7-12 digits; 6+ is a safe floor

def _looks_like_scam_alert(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return (DUPLICATE_MARKER_EMOJI in text) and ("scam" in t)

def _extract_uids_from_text(text: str) -> Set[str]:
    if not text:
        return set()
    return set(_UID_RE.findall(text))

def _now_ts() -> float:
    return time.time()

# --- Overwatch auto-refresh helpers ---
OVERWATCH_DIALOG_REFRESH_SECONDS = 12 * 60 * 60  # 12 hours
OVERWATCH_SCAMMER_REFRESH_SECOND = 60 * 60  # 1 hour

async def _refresh_scammer_data_periodically(
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
            break
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

# --- NEW: Overwatch life-check (restart if no messages in 4h) ---
OVERWATCH_LIFE_CHECK_SECONDS = 5 * 60          # check every 5 minutes
OVERWATCH_NO_MESSAGE_RESTART_SECONDS = 4 * 60 * 60  # 4 hours

async def _life_check_periodically(
    state: Dict[str, Any],
    state_lock: asyncio.Lock,
    stop_event: asyncio.Event,
    refresh_seconds: int = OVERWATCH_LIFE_CHECK_SECONDS,
    no_msg_seconds: int = OVERWATCH_NO_MESSAGE_RESTART_SECONDS,
):
    """
    If no messages (from anyone) have been seen in no_msg_seconds, request restart.
    We only track NewMessage events (messages), as requested.
    """
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=refresh_seconds)
            break
        except asyncio.TimeoutError:
            pass

        now = time.time()
        async with state_lock:
            last_msg_ts = state.get("last_message_ts", None)
            restarting = state.get("restart_requested", False)

        if restarting:
            return

        if last_msg_ts is None:
            # Haven't seen any message since starting; don't immediately restart.
            continue

        idle = now - float(last_msg_ts)
        if idle >= no_msg_seconds:
            async with state_lock:
                # double-check inside lock
                if not state.get("restart_requested", False):
                    state["restart_requested"] = True
            print(f"🧯 LIFE CHECK: no messages seen for {idle/3600:.2f} hours. Requesting restart...")
            stop_event.set()
            return
            
# --- Overwatch persistent state ---
OVERWATCH_STATE_FILE = "overwatch_state.json"
OVERWATCH_STATE_SAVE_SECONDS = 60  # save once a minute
OVERWATCH_STATE_VERSION = 1

def _encode_key(parts: List[Any]) -> str:
    # Safe-ish separator for tuple keys
    return "|".join(str(p).replace("|", "%7C") for p in parts)

def _decode_key(s: str, n_parts: int) -> Optional[Tuple[str, ...]]:
    try:
        parts = s.split("|")
        if len(parts) != n_parts:
            return None
        parts = tuple(p.replace("%7C", "|") for p in parts)
        return parts
    except Exception:
        return None

def load_overwatch_state_from_disk() -> Dict[str, Any]:
    """
    Returns a dict with keys:
      - allowlist: Set[int]
      - last_message_ts: Optional[float]
      - group_last_sent: Dict[Tuple[int,str], float]
      - last_notified: Dict[Tuple[str,int,str,str], float]
    Missing/corrupt file => returns empty defaults.
    """
    if not os.path.exists(OVERWATCH_STATE_FILE):
        return {
            "allowlist": set(),
            "last_message_ts": None,
            "group_last_sent": {},
            "last_notified": {},
        }

    try:
        with open(OVERWATCH_STATE_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            raise ValueError("state payload not a dict")

        if payload.get("version") != OVERWATCH_STATE_VERSION:
            print("⚠️ Overwatch state version mismatch; ignoring saved state.")
            return {
                "allowlist": set(),
                "last_message_ts": None,
                "group_last_sent": {},
                "last_notified": {},
            }

        allowlist = set()
        for x in payload.get("allowlist", []) or []:
            try:
                allowlist.add(int(x))
            except Exception:
                pass

        last_message_ts = payload.get("last_message_ts", None)
        if last_message_ts is not None:
            try:
                last_message_ts = float(last_message_ts)
            except Exception:
                last_message_ts = None

        group_last_sent: Dict[Tuple[int, str], float] = {}
        for k, v in (payload.get("group_last_sent", {}) or {}).items():
            tup = _decode_key(str(k), 2)
            if not tup:
                continue
            chat_id_s, uid_str = tup
            try:
                chat_id = int(chat_id_s)
                ts = float(v)
                group_last_sent[(chat_id, str(uid_str))] = ts
            except Exception:
                continue

        last_notified: Dict[Tuple[str, int, str, str], float] = {}
        for k, v in (payload.get("last_notified", {}) or {}).items():
            tup = _decode_key(str(k), 4)
            if not tup:
                continue
            kind, chat_id_s, uid_str, extra_key = tup
            try:
                chat_id = int(chat_id_s)
                ts = float(v)
                last_notified[(str(kind), chat_id, str(uid_str), str(extra_key))] = ts
            except Exception:
                continue

        print(f"💾 Loaded overwatch state from disk: "
              f"allowlist={len(allowlist)}, "
              f"group_last_sent={len(group_last_sent)}, "
              f"last_notified={len(last_notified)}, "
              f"last_message_ts={'set' if last_message_ts else 'None'}")

        return {
            "allowlist": allowlist,
            "last_message_ts": last_message_ts,
            "group_last_sent": group_last_sent,
            "last_notified": last_notified,
        }

    except Exception as e:
        print(f"⚠️ Failed to load overwatch state; starting fresh: {e}")
        return {
            "allowlist": set(),
            "last_message_ts": None,
            "group_last_sent": {},
            "last_notified": {},
        }

def save_overwatch_state_to_disk(
    allowlist: Set[int],
    last_message_ts: Optional[float],
    group_last_sent: Dict[Tuple[int, str], float],
    last_notified: Dict[Tuple[str, int, str, str], float],
):
    """
    Writes state to OVERWATCH_STATE_FILE (overwrite, not atomic).
    """
    try:
        payload = {
            "version": OVERWATCH_STATE_VERSION,
            "saved_at": int(time.time()),
            "allowlist": sorted(list(allowlist)),
            "last_message_ts": last_message_ts,
            "group_last_sent": {
                _encode_key([chat_id, uid_str]): ts
                for (chat_id, uid_str), ts in group_last_sent.items()
            },
            "last_notified": {
                _encode_key([kind, chat_id, uid_str, extra_key]): ts
                for (kind, chat_id, uid_str, extra_key), ts in last_notified.items()
            },
        }
        with open(OVERWATCH_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        print(f"⚠️ Failed to save overwatch state: {e}")

async def _persist_overwatch_state_periodically(
    state: Dict[str, Any],
    state_lock: asyncio.Lock,
    stop_event: asyncio.Event,
    save_seconds: int = OVERWATCH_STATE_SAVE_SECONDS,
):
    """
    Periodically snapshots state and writes it to disk.
    """
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=save_seconds)
            break
        except asyncio.TimeoutError:
            pass

        try:
            async with state_lock:
                allowlist = set(state.get("allowlist", set()))
                last_message_ts = state.get("last_message_ts", None)
                group_last_sent = state.get("group_last_sent", {})
                last_notified = state.get("last_notified", {})

                # shallow copy so we don't serialize while dict is mutating
                group_last_sent_copy = dict(group_last_sent)
                last_notified_copy = dict(last_notified)

            save_overwatch_state_to_disk(
                allowlist=allowlist,
                last_message_ts=last_message_ts,
                group_last_sent=group_last_sent_copy,
                last_notified=last_notified_copy,
            )
        except Exception as e:
            print(f"⚠️ Persist task error: {e}")


async def run_overwatch_forever(api_id, api_hash, overwatch_report_mode):
    backoff = 5
    while True:
        client = TelegramClient(SESSION_NAME, api_id, api_hash)
        try:
            await client.start()

            scammer_map, scammer_ids = load_scammer_data_v2()
            if not scammer_ids:
                print("⚠️ No scammer data loaded; retrying soon...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)
                continue

            backoff = 5
            await overwatch_mode(client, scammer_ids, scammer_map, overwatch_report_mode)

        except Exception as e:
            print(f"🔌 Overwatch crashed/disconnected: {e!r}")
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

        print(f"🔁 Reconnecting in {backoff}s...")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300)

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
      2) terminal + Saved Messages (scheduled reminder in 5 minutes)
      3) terminal + Saved Messages (scheduled) + also send to the group (rate-limited per scammer per group per day)

    - Every 12 hours, refresh:
      - dialogs
    -- Every hour, refresh:
      - scammer list

    Life-check:
      - if no NewMessage events in 4 hours => restart process
    """
    print("🛰️ Overwatch mode enabled.")
    print("   - Listening for scammer messages and join/leave events")
    print("   - Monitoring chats with >2 users (best-effort, based on dialog participant counts)")
    print("   - Scammer alerts include a link to the scammer's topic\n")
    print("📣 Overwatch reporting:")
    print(f"   - Mode: {overwatch_report_mode} "
          f"({'terminal only' if overwatch_report_mode == 1 else 'terminal + saved messages' if overwatch_report_mode == 2 else 'terminal + saved + group'})")
    if overwatch_report_mode in (2, 3):
        print("   - Saved Messages are sent as a scheduled reminder (5 minutes in the future) to ping you.\n")
    else:
        print()

    # Shared, refreshable state
    state_lock = asyncio.Lock()
    stop_event = asyncio.Event()


    # Load persisted state (for manual restarts too)
    persisted = load_overwatch_state_from_disk()
    
    me = await client.get_me()
    my_id = getattr(me, "id", None)


    # Soft dedupe + daily group limits (persisted)
    last_notified: Dict[Tuple[str, int, str, str], float] = dict(persisted.get("last_notified", {}))
    group_last_sent: Dict[Tuple[int, str], float] = dict(persisted.get("group_last_sent", {}))

    state: Dict[str, Any] = {
        "allowlist": set(persisted.get("allowlist", set())),   # will be refreshed from dialogs
        "scammer_ids": set(scammer_ids),
        "scammer_map": dict(scammer_map),
        "last_message_ts": persisted.get("last_message_ts", None),
        "restart_requested": False,

        # references so the persister can snapshot them
        "group_last_sent": group_last_sent,
        "last_notified": last_notified,
    }
    
    # For duplicate message detection: what WE posted recently per chat
    # chat_id -> deque of entries: {"msg_id": int, "ts": float, "uids": set(str)}
    state["own_alerts"] = defaultdict(deque)

    # chat_id -> dict uid_str -> last_seen_ts (uids we alerted about recently)
    state["own_recent_uids"] = defaultdict(dict)

    state["my_id"] = my_id

    print("Reading groups...")
    initial_allowlist = await _build_group_allowlist(client)
    async with state_lock:
        state["allowlist"] = initial_allowlist
    print(f"✅ Overwatch allowlist ready: {len(initial_allowlist)} chat(s) with >2 users.\n")

    # Start periodic tasks
    refresh_tasks = [
        asyncio.create_task(_refresh_allowlist_periodically(client, state, state_lock, stop_event)),
        asyncio.create_task(_refresh_scammer_data_periodically(state, state_lock, stop_event)),
        asyncio.create_task(_life_check_periodically(state, state_lock, stop_event)),
        asyncio.create_task(_persist_overwatch_state_periodically(state, state_lock, stop_event)),
        asyncio.create_task(periodic_update_checker(stop_event, local_version=__version__, local_force=__force__, raw_url=GITHUB_RAW_URL, interval_seconds=UPDATE_CHECK_SECONDS)),
    ]

    DEDUPE_SECONDS = 30.0
    GROUP_LIMIT_SECONDS = 86400.0  # 1 day

    async def maybe_send_to_group_with_daily_limit(chat_entity, chat_id: int, uid_str: str, text: str):
        key = (chat_id, uid_str)
        now = _now_ts()
        last = group_last_sent.get(key, 0.0)
        if (now - last) < GROUP_LIMIT_SECONDS:
            return None

        try:
            msg = await client.send_message(chat_entity, text)
            group_last_sent[key] = now

            return msg

        except FloodWaitError as e:
            print(f"   ⏳ FloodWait while sending to group: sleeping {e.seconds}s (suppressing this send)")
            await asyncio.sleep(e.seconds)
            return None
        except Exception as e:
            print(f"❌ Failed to send alert to group '{getattr(chat_entity, 'title', chat_entity)}': {e}")
            return None

    async def notify(kind: str, chat_entity, chat_id: int, uid_str: str, extra_key: str, text: str):
        """
        Applies a short dedupe; then:
          - mode 2/3: schedule reminder to Saved Messages (5 min)
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
            sent_msg = await maybe_send_to_group_with_daily_limit(chat_entity, chat_id, uid_str, text)
            if sent_msg:
                await _record_own_group_alert(chat_id, sent_msg, {uid_str})
            else:
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

        # ✅ pull current scammer_map from shared state (it can refresh hourly)
        async with state_lock:
            scammer_map_now = dict(state.get("scammer_map", {}))

        username = None
        info = scammer_map_now.get(uid_str, {})
        u = (info.get("username") or "").strip()
        if u and u.lower() not in ("none", "deleted"):
            username = u

        still, why = await _verify_user_presence_stepped(
            client,
            chat_entity,
            chat_id,
            uid_int,
            username=username,
            recent_limit=200,
        )
        print(f"   🔎 verify detail: {why}")

        topic_line = f"• Scammer topic: {scammer_topic}\n" if scammer_topic else ""

        if still is True:
            text = (
                f"🚨 **Scammer joined chat**\n"
                f"• Chat: **{chat_title}**\n"
                f"• Chat link: {chat_link}\n"
                f"• Scammer: {scammer_display} (id `{uid_str}`)\n"
                f"{topic_line}"
            ).rstrip()
            print(f"✅ Overwatch verify: still in '{chat_title}': {scammer_display} ({uid_str})")
            await notify("verify", chat_entity, chat_id, uid_str, "still", text)

        elif still is False:
            print(f"⚠️ Overwatch verify: gone from '{chat_title}': {scammer_display} ({uid_str})")

        else:
            text = (
                f"🚨 **Scammer joined chat (verify inconclusive)**\n"
                f"• Chat: **{chat_title}**\n"
                f"• Chat link: {chat_link}\n"
                f"• Scammer: {scammer_display} (id `{uid_str}`)\n"
                f"{topic_line}"
                f"• Verify: {why}"
            ).rstrip()
            print(f"ℹ️ Overwatch verify: inconclusive for '{chat_title}': {scammer_display} ({uid_str}) ({why})")
            await notify("verify", chat_entity, chat_id, uid_str, "unknown", text)
            
    async def _record_own_group_alert(chat_id: int, sent_msg, uids: Set[str]):
        """
        Track our sent group alerts so we can delete duplicates if someone else posted earlier.
        """
        if not sent_msg:
            return

        ts = None
        try:
            ts = sent_msg.date.timestamp() if sent_msg.date else _now_ts()
        except Exception:
            ts = _now_ts()

        async with state_lock:
            # store per-chat alert message
            dq = state["own_alerts"][chat_id]
            dq.append({"msg_id": int(sent_msg.id), "ts": float(ts), "uids": set(uids)})

            # store per-chat recent uids
            uid_map = state["own_recent_uids"][chat_id]
            for u in uids:
                uid_map[str(u)] = float(ts)

            # prune
            cutoff = _now_ts() - DUPLICATE_PRUNE_SECONDS
            while dq and dq[0]["ts"] < cutoff:
                dq.popleft()

            # prune uid map
            for u, uts in list(uid_map.items()):
                if uts < cutoff:
                    uid_map.pop(u, None)
                    
    async def _handle_possible_duplicate_alert(event, chat_id: int):
        if overwatch_report_mode != 3:
            return

        msg = event.message
        text = (event.raw_text or "").strip()
        if not _looks_like_scam_alert(text):
            return

        # Ignore our own outgoing messages
        if getattr(msg, "out", False):
            return

        candidate_uids = _extract_uids_from_text(text)
        if not candidate_uids:
            return

        try:
            candidate_ts = msg.date.timestamp() if msg.date else _now_ts()
        except Exception:
            candidate_ts = _now_ts()

        cutoff = _now_ts() - DUPLICATE_WINDOW_SECONDS
        prune_cutoff = _now_ts() - DUPLICATE_PRUNE_SECONDS

        async with state_lock:
            recent_uid_map = state["own_recent_uids"][chat_id]
            recent_uids = {u for u, uts in recent_uid_map.items() if uts >= cutoff}

            overlap = candidate_uids & recent_uids
            if not overlap:
                return  # not confirmed

            dq = state["own_alerts"][chat_id]

            # prune old tracking
            while dq and dq[0]["ts"] < prune_cutoff:
                dq.popleft()
            for u, uts in list(recent_uid_map.items()):
                if uts < prune_cutoff:
                    recent_uid_map.pop(u, None)

            my_matching = [
                a for a in dq
                if a["ts"] >= cutoff and (a["uids"] & overlap)
            ]

        if not my_matching:
            return

        # Delete OUR messages that are newer than the candidate
        to_delete = [a["msg_id"] for a in my_matching if a["ts"] > candidate_ts]
        if not to_delete:
            return

        try:
            await client.delete_messages(chat_id, to_delete, revoke=True)
            print(f"🧹 Duplicate detector: deleted {len(to_delete)} newer duplicate alert(s) in chat {chat_id} "
                  f"(uids={sorted(list(overlap))[:5]}{'...' if len(overlap)>5 else ''})")
        except FloodWaitError as e:
            print(f"   ⏳ FloodWait while deleting duplicates: sleeping {e.seconds}s")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            print(f"⚠️ Duplicate detector: failed to delete duplicates in chat {chat_id}: {e}")

        async with state_lock:
            dq = state["own_alerts"][chat_id]
            state["own_alerts"][chat_id] = deque([a for a in dq if a["msg_id"] not in set(to_delete)])


    @client.on(events.NewMessage())
    async def on_new_message(event: events.NewMessage.Event):
        async with state_lock:
            state["last_message_ts"] = time.time()

        chat_id = event.chat_id
        if chat_id is None:
            return

        async with state_lock:
            allowlist = state["allowlist"]
            scammer_ids_local = state["scammer_ids"]
            scammer_map_local = state["scammer_map"]

        if chat_id not in allowlist:
            return

        # NEW: duplicate watcher (mode 3)
        await _handle_possible_duplicate_alert(event, chat_id)
        
        # ✅ NEW: catch "invited/added/joined" service messages here
        action_uids = _extract_action_user_ids(event.message)
        if action_uids:
            # Only resolve chat entity once
            try:
                chat_entity = await event.get_chat()
            except Exception:
                chat_entity = None

            chat_title = getattr(chat_entity, "title", None) or "(unknown chat)"
            chat_link = _chat_link(chat_entity, chat_id)

            # Check each user that was added (can be multiple)
            for auid in action_uids:
                auid_str = str(auid)
                if auid_str not in scammer_ids_local:
                    continue

                info = scammer_map_local.get(auid_str, {})
                scammer_display = scammer_display_name_from_v2(info) if info else auid_str
                scammer_topic = topic_link_for_scammer(info) if info else None
                topic_line = f"• Scammer topic: {scammer_topic}\n" if scammer_topic else ""

                text = (
                    f"🚨 **Scammer invited/added detected**\n"
                    f"• Chat: **{chat_title}** (`{chat_id}`)\n"
                    f"• Chat link: {chat_link}\n"
                    f"• Scammer: {scammer_display} (id `{auid_str}`)\n"
                    f"{topic_line}"
                ).rstrip()

                print(f"🚨 Overwatch: scammer added/invited in '{chat_title}': {scammer_display} ({auid_str})")

                # Use a stable extra_key so repeated identical service messages dedupe for 30s
                await notify("joinmsg", chat_entity, chat_id, auid_str, str(event.message.id), text)

                # (Optional) you can also re-use your delayed verification like ChatAction does:
                # asyncio.create_task(delayed_join_verify(
                #     chat_entity, chat_id, chat_title, chat_link, auid_str, scammer_display, scammer_topic
                # ))

            # IMPORTANT: prevent falling through and treating the service message as "scammer message detected"
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

        info = scammer_map_local.get(uid_str, {})
        scammer_display = scammer_display_name_from_v2(info) if info else name_for_telegram_user_fallback(sender)
        scammer_topic = topic_link_for_scammer(info) if info else None

        topic_line = f"• Scammer topic: {scammer_topic}\n" if scammer_topic else ""
        text = (
            f"🚨 **Scammer message detected**\n"
            f"• Chat: **{chat_title}**\n"
            f"• Scammer: {scammer_display} (id `{uid_str}`)\n"
            f"{topic_line}"
            f"• Message link: {msg_link}"
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

    restart_requested = False
    try:
        await client.run_until_disconnected()
    except KeyboardInterrupt:
        print("\n🛑 Overwatch stopping (Ctrl+C).")
    finally:
        # stop periodic tasks
        async with state_lock:
            restart_requested = bool(state.get("restart_requested", False))

        stop_event.set()
        for t in refresh_tasks:
            t.cancel()
        await asyncio.gather(*refresh_tasks, return_exceptions=True)

        try:
            await client.disconnect()
        except Exception:
            pass

    # If life-check requested restart: execv the current script
    if restart_requested:
        print("🔁 Restarting process via execv...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

# --- Main ---
async def main():
    check_for_update_once(__version__, __force__, GITHUB_RAW_URL, print_prefix="🔎 Update check (startup)")
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
        print("  2) Terminal + Saved Messages (scheduled reminder in 5 min)")
        print("  3) Terminal + Saved Messages (scheduled) + send to group (limited: 1 alert per scammer per group per day)")
        ow_raw = input("Enter 1 / 2 / 3: ").strip()
        try:
            overwatch_report_mode = int(ow_raw)
        except ValueError:
            overwatch_report_mode = 1
        if overwatch_report_mode not in (1, 2, 3):
            print("⚠️ Invalid choice. Defaulting to Overwatch mode 1 (Terminal only).")
            overwatch_report_mode = 1
        await client.disconnect()
        await run_overwatch_forever(api_id, api_hash, overwatch_report_mode)
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
