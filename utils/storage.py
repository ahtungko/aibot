# utils/storage.py — File-based storage helpers
import os
import json
import aiofiles
from config import USER_DATA_FILE, AFK_FILE, PINS_FILE, AI_SETTINGS_FILE


# --- Horoscope User Data (async, abc.txt) ---

async def load_user_data():
    if not os.path.exists(USER_DATA_FILE):
        return {}
    try:
        async with aiofiles.open(USER_DATA_FILE, 'r') as f:
            return json.loads(await f.read())
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

async def save_user_data(data):
    async with aiofiles.open(USER_DATA_FILE, 'w') as f:
        await f.write(json.dumps(data, indent=4))


# --- AFK Data (sync, afk.json) ---

def load_afk():
    if not os.path.exists(AFK_FILE):
        return {}
    try:
        with open(AFK_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

def save_afk(data):
    with open(AFK_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)


# --- Pins Data (sync, pins.json) ---

def load_pins():
    if not os.path.exists(PINS_FILE):
        return {}
    try:
        with open(PINS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

def save_pins(data):
    with open(PINS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# --- AI Settings (sync, ai_settings.json) ---

def load_ai_settings():
    if not os.path.exists(AI_SETTINGS_FILE):
        return {}
    try:
        with open(AI_SETTINGS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        return {}

def save_ai_settings(data):
    with open(AI_SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
