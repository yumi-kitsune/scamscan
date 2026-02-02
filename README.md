# Telegram Scammer Scanner (ScamScan)

ScamScan is a **Telethon-based userbot** that helps you detect known scam accounts across your Telegram groups.

It pulls a unified scammer list from Countersign and can:

- **Scan** your chats for known scammers
- **Immunize** your account by blocking scammer usernames (slowly)
- Run **Overwatch** mode: stay online and alert when scammers **message** or **join/leave** monitored groups

> ⚠️ **Important:** This is a *userbot*. It logs in using **your own Telegram account** (not a bot token).

---

## Features

✅ **Unified scammer list (v2)**
- Source: `https://countersign.chat/api/scammer_ids_v2.json`
- Includes metadata (topic id, reason, username/full name when available)

✅ **Scan chats**
- Scan **all** groups/channels, or filter by partial chat name
- Reports scammers found, including ScamTracking topic links (when available)

✅ **Three reporting modes (scan mode)**
1. Console only
2. Console + send report to **Saved Messages**
3. Console + post report in the **chat where scammers were found**

🛡️ **Immunize mode**
- Extracts scammer `username` values from the v2 API, normalizes to `@username`, and dedupes
- Skips `None` / `DELETED` usernames
- Blocks slowly (**1 account every 30 seconds**) to reduce rate-limit risk

🛰️ **Overwatch mode (always-on monitoring)**
- Watches chats with >2 users
- Detects **scammer messages** and **scammer join/leave** events
- Reporting options:
  1) Terminal only
  2) Terminal + **Saved Messages reminder** (scheduled 5 minutes in the future)
  3) Terminal + Saved Messages + **post to group** (rate-limited)

🧹 **Duplicate alert cleanup (Overwatch mode 3)**
- If someone else posts a matching scam alert shortly before you, ScamScan can delete its newer duplicate alert(s)

🔄 **Auto-refresh (Overwatch)**
- Refresh chats every **12 hours**
- Refresh scammer list every **1 hour**

🧯 **Life-check self-restart (Overwatch)**
- If no `NewMessage` events are seen for **4 hours**, the process restarts automatically

⬆️ **GitHub update checks (no git required)**
- Fetches raw `scan.py` from `raw.githubusercontent.com`
- Parses `__version__` and `__force__` from the remote script (no tags/releases)
- Checks once at startup, and every **2 hours** while running Overwatch
- If remote `__force__ = True` and your local version is behind → exits and refuses to run until updated

---

## NOTICE / Risk Guidance

This tool uses Telegram “userbot” behavior: your account is logged in and makes API calls as you.

Userbots are not explicitly addressed under Telegram’s ToS. While many people use them, **there is no guarantee** Telegram won’t rate-limit or restrict your account.

**Lower-risk usage:**
- Scan a single chat (don’t scan everything constantly)
- Prefer console-only reports
- Avoid aggressive actions / high-frequency loops
- Respect FloodWaits and keep delays in place

---

## Requirements

- **Python 3.8+**
- Telethon + Requests (auto-installed by the script if missing)

Download Python: https://www.python.org/downloads/

---

## Setup

1. Download this repository (or just `scan.py`)
2. Run it:
   ```bash
   python3 scan.py
   ```
3. On first run you’ll be guided through creating Telegram API credentials:
   - Visit: https://my.telegram.org/auth?to=apps
   - Create an app and copy **API ID** and **API Hash**
   - Saved locally to `config.json`

Telethon session file:
- `userbot_session.session`

---

## Usage

When launched, the script prompts you to choose a function:

1) Scan chats for known scammers (Unified API v2)
2) Immunize (block scammer usernames from Unified API v2)
3) Overwatch (stay online and alert on scammer messages / join / leave)

---

### Mode 1: Scan Chats for Scammers

Workflow:
1. Fetch the unified scammer list (v2)
2. Prompt for a chat name:
   - Provide a partial chat name to scan matching chats
   - Leave blank to scan **all** groups/channels
3. Choose reporting mode:
   ```
   1) Console only
   2) Console + Saved Messages
   3) Console + post in the chat where scammers were found
   ```
4. Watch progress per chat in your terminal

---

### Mode 2: Immunize (Block Scammer Usernames)

What it does:
- Extracts scammer usernames from the v2 payload
- Normalizes to `@username`, dedupes case-insensitively
- Blocks slowly (1 every 30 seconds)

Notes:
- Blocking happens on **your own Telegram account**
- If a username no longer resolves, it is skipped

---

### Mode 3: Overwatch (Always-On Monitoring)

Overwatch stays connected and watches your groups for scammers.

It monitors:
- **Scammer messages** in groups
- **Scammer join/leave** events

Reporting options:
1. Terminal only
2. Terminal + Saved Messages reminder (scheduled 5 minutes ahead)
3. Terminal + Saved Messages + post to group (1 alert per scammer per group per day)

Overwatch also:
- Periodically refreshes groups and scammer list
- Saves state to `overwatch_state.json` (dedupe keys, allowlist, timestamps)
- Performs a self-restart if no messages are seen for 4 hours

---

## GitHub Update Checks (No Git Required)

At the top of `scan.py`:
```python
__version__ = "0.3.5"
__force__ = False
```

Repo settings in the script:
```python
GITHUB_OWNER = "yumi-kitsune"
GITHUB_REPO  = "scamscan"
GITHUB_SCRIPT_PATH = "scan.py"
```

Behavior:
- Fetch remote `scan.py` (raw)
- Parse `__version__` and `__force__`
- Print a status line every check cycle
- If remote `__force__` is `True` and remote version is newer → exit with code `3`

---

## Files Created

- `config.json` — stores your Telegram API ID/hash
- `userbot_session.session` — Telethon session file
- `overwatch_state.json` — Overwatch persistence (allowlist, dedupe keys, timestamps)

---

## Reset / Remove Credentials

```bash
rm -f config.json userbot_session.session overwatch_state.json
```

---

## Troubleshooting

### FloodWait errors
Telegram is rate-limiting you. The script generally sleeps and continues, but you should:
- Reduce scan frequency
- Increase delays (e.g., the scan loop sleep)
- Avoid scanning huge groups repeatedly

### Participant fetching fails for some chats
Some large supergroups/channels restrict participant access. Scan mode relies on `client.get_participants()` which may fail depending on permissions or chat size.

---

## Disclaimer

This tool is provided as-is. You are responsible for how you use it and any consequences on your Telegram account.
