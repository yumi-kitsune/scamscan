# Telegram Scammer Scanner

This script uses **Telethon** to automatically check all (or specific) Telegram chats for users who appear on ScamTracking. It’s designed for investigators, moderators, and community admins who want to quickly audit their chats for known scam accounts.

---

## Features

✅ **Automatic scammer lookup**  
✅ **Scans all chats or filters by name**  
✅ **Three reporting modes**:
1. **Console only**  
2. **Console + Saved Messages (DM)**  
3. **Console + post results in chat where scammers were found**

🛡️ **Immunize mode (NEW)**  
- Automatically blocks recently-active scam accounts from contacting your userbot  
- Pulls the most recent CSV from **@scamtrackingCSV**  
- Only blocks usernames that:
  - Were checked within the last **24 hours**
  - Are **not** marked as `DELETED` or `None`
- Blocks **one account every 30 seconds** to reduce rate-limit risk

✅ Always prints clear console output  
✅ Safe credential handling (stored once in `config.json`)  
✅ Attempts to honor Telegram rate limits  

---

## NOTICE

This program makes use of a feature of Telegram called a **userbot**, where *your own account* is used to perform scans and actions.

Userbots are not explicitly addressed under Telegram’s Terms of Service.  
While we have not experienced issues using this tool, **there is no guarantee** Telegram will not rate-limit or restrict your account.

**Risk guidance:**
- Scanning a **single chat**
- Using **console-only output**
- Avoiding aggressive actions

…are the safest ways to run this program.

---

## Requirements

- **Python 3.8+**
- **Telethon**
- **Requests**

The script automatically checks for and installs missing packages.

Python can be downloaded at:  
https://www.python.org/downloads/

---

## Setup

1. **Clone or download** this script to your system.
2. Run it once:
   ```bash
   python3 scan.py
   ```
3. The first time you run it:
   - You’ll be guided through creating a Telegram API app at  
     👉 [https://my.telegram.org/auth?to=apps](https://my.telegram.org/auth?to=apps)
   - Copy your **API ID** and **API Hash** into the script when prompted.
   - Your credentials will be saved locally in `config.json`.

---

## Usage

When launched, the script first asks which function you want to run:
1. Scan chats for known scammers
2. Immunize (block recent scammer usernames)

---

### Mode 1: Scan Chats for Scammers

1. Fetch the latest scammer ID list
2. Prompt you for a **chat name** to scan:
   - Enter part of a chat’s name (e.g. `Furry Friends`)  
     → only chats matching that string will be scanned.
   - Leave it **blank**  
     → all groups and channels will be scanned.

3. Choose your **reporting mode**:
   ```
   1) Console only
   2) Console + send report to Saved Messages
   3) Console + send report to the chat where scammers are found
   ```

4. Watch progress in your console as each chat is scanned.

---

### Mode 2: Immunize Against Scammers (NEW)

This mode proactively **blocks known scam accounts** from contacting you, based on the latest CSV published in **@scamtrackingCSV**.

**What it does:**
1. Looks up the channel: `@scamtrackingCSV`
2. Loads the **most recent message** and looks for an attached **CSV**
3. Downloads the CSV and reads the columns:
- `user_id`
- `username`
- `last_username_check`
4. Builds a “to block” list:
- Includes entries where `last_username_check` is within the last **24 hours**
- Excludes rows where `username` is `DELETED` or `None`
5. Blocks users **one every 30 seconds** to reduce the risk of triggering Telegram rate limits.

**Notes:**
- Blocking is performed on **your account** (the userbot session you logged in with).
- If a username no longer resolves, it will be skipped and the script continues.

---

## Output Example

**Console Output**
```
🌐 Fetching scammer list from https://countersign.chat/api/scammer_ids.json ...
✅ Loaded 942 scammer IDs.

🔎 No chat name provided → scanning ALL chats (groups/channels).

➡️ Checking chat: 'Furry Friends Group' (ID: 123456789)
⏳ Getting participant list for 'Furry Friends Group'...
🚨 Scammer(s) found in 'Furry Friends Group':
    ⚠️ User ID: 5432198765, Username: @scam_account
✅ Done! Press Enter to exit...
```

**Saved Messages (Mode 2)**
```
🚨 Scammer(s) found in Furry Friends Group:
• @scam_account (id `5432198765`)
```

---

## Safety

- Only your **own Telegram session** is used; no third-party tokens or logins.  
- API credentials are stored locally in `config.json`.  
- Reports are never sent externally except through Telegram itself.

---

## Advanced Notes

- Scans use `client.get_participants()` under the hood (may be limited for very large supergroups).
- If you hit Telegram rate limits, increase the delay in the code (`await asyncio.sleep(0.2)`).

---

## Reset or Remove Credentials

To remove your stored credentials and session:
```bash
rm -f config.json userbot_session.session
```

---