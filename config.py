# config.py — Shared configuration and constants
import os
from dotenv import load_dotenv

load_dotenv()

# Bot and API Credentials
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
BOT_OWNER_ID_STR = os.getenv("BOT_OWNER_ID")
WISE_SANDBOX_TOKEN = os.getenv("WISE_SANDBOX_TOKEN")
CHECKIN_WORKER_URL = os.getenv("CHECKIN_WORKER_URL")
CHECKIN_AUTH_PASS = os.getenv("CHECKIN_AUTH_PASS", "")

# Bot Settings
COMMAND_PREFIX = "!"
USER_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "abc.txt")
AFK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "afk.json")
PINS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pins.json")

# API URLs
BASE_CURRENCY_API_URL = "https://api.frankfurter.dev/v1/latest"
OPENAI_BASE_URL = "https://ai.qaq.al/v1"
DEFAULT_MODEL = "gpt-5.4"
FALLBACK_MODEL = "gpt-5.3-codex"

# AI Settings
MAX_HISTORY_MESSAGES = 10
HISTORY_EXPIRY_SECONDS = 1800
MIN_DELAY_BETWEEN_CALLS = 1.1

AI_PERSONALITY = (
    "You are a helpful and friendly AI assistant. Your goal is to provide accurate, clear, and concise information. "
    "You should be polite and respectful in all your responses. "
    "IMPORTANT: You MUST detect the language of the user's message and ALWAYS respond in that same language. "
    "For example, if the user writes in Chinese, you must reply in Chinese. If they write in Malay, you reply in Malay."
)

# Music API URLs
API_DOWNLOAD_URLS = {
    'joox': 'https://music.wjhe.top/api/music/joox/url',
    'migu': 'https://music.wjhe.top/api/music/migu/url',
    'qobuz': 'https://music.wjhe.top/api/music/qobuz/url'
}
API_SEARCH_URLS = {
    'joox': 'https://music.wjhe.top/api/music/joox/search',
    'migu': 'https://music.wjhe.top/api/music/migu/search',
    'qobuz': 'https://music.wjhe.top/api/music/qobuz/search'
}

# Precious Metals
TROY_OUNCE_TO_GRAMS = 31.1034768

# --- Sanity Checks ---
if not DISCORD_BOT_TOKEN:
    print("FATAL ERROR: DISCORD_BOT_TOKEN not found in .env file.")
    exit(1)
if not OPENAI_API_KEY:
    print("Warning: OPENAI_API_KEY not found. AI features will be disabled.")
if not BOT_OWNER_ID_STR:
    print("Warning: BOT_OWNER_ID not found. Owner-only commands will be disabled.")
if not WISE_SANDBOX_TOKEN:
    print("Warning: WISE_SANDBOX_TOKEN not found. The !liverate command will be disabled.")
if not CHECKIN_WORKER_URL:
    print("Warning: CHECKIN_WORKER_URL not found. The !ck check-in command will be disabled.")

try:
    OWNER_ID = int(BOT_OWNER_ID_STR) if BOT_OWNER_ID_STR else None
except ValueError:
    print(f"Warning: Invalid BOT_OWNER_ID '{BOT_OWNER_ID_STR}'.")
    OWNER_ID = None
