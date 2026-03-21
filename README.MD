# OWN USE ONLY


# JenBot 🤖

A feature-rich Discord bot with AI chat, currency exchange, horoscopes, music downloads, game deals, and more.

## Features

| Category | Commands |
|---|---|
| 🤖 **AI Chat** | `@JenBot [message]`, `!tldr [count]`, `!clear` |
| 💱 **Currency** | `!usd`, `!usd 100 myr`, `!liverate 100 EUR USD` |
| ✨ **Horoscope** | `!reg`, `!mod`, `!modtz`, `!remove`, `!list` |
| 🎵 **Music** | `!ss [query]`, `!d [number]` |
| 🐱 **Fun** | `!c`, `!cf`, `!roast @user` |
| 🎮 **Game Deals** | `!deals`, `!price [game]` |
| 📚 **Utility** | `!dict [word]`, `!gold`, `!silver` |
| 📝 **Check-in** | `!ck [note]`, `!streak`, `!lb` |
| 💤 **AFK** | `!afk [reason]` |
| 📌 **Bookmarks** | `!pin`, `!pins`, `!unpin [#]` |

## Project Structure

```
JenBot/
├── jbot.py              # Entry point
├── config.py            # Environment variables & constants
├── requirements.txt     # Python dependencies
├── .env.example         # Template for secrets
├── cogs/                # Modular command groups
│   ├── ai.py            # AI chat, !tldr, !clear
│   ├── checkin.py       # !ck, !streak, !lb
│   ├── currency.py      # Currency exchange, !liverate
│   ├── fun.py           # !c, !cf, !deals, !price, !dict, !afk, !roast
│   ├── horoscope.py     # Horoscope commands + daily task
│   ├── music.py         # !ss, !d
│   ├── pins.py          # !pin, !pins, !unpin
│   └── precious.py      # !gold, !silver, status task
├── utils/               # Shared helpers
│   ├── helpers.py       # Utility functions
│   └── storage.py       # File I/O helpers
└── cf/worker/           # Cloudflare Worker (check-in backend)
```

---

## Installation (VPS / Linux Server)

### Prerequisites

- **Python 3.10+**
- **A Discord Bot Token** — [Create one here](https://discord.com/developers/applications)
- **A VPS** (Ubuntu 22.04+ recommended)

### Step 1: Initial Server Setup

```bash
# Update packages
sudo apt update && sudo apt upgrade -y

# Install Python and pip
sudo apt install python3 python3-pip python3-venv git -y
```

### Step 2: Clone the Repository

```bash
cd ~
git clone https://github.com/ahtungko/JenBot.git
cd JenBot
```

### Step 3: Create Virtual Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Step 4: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 5: Configure Environment Variables

```bash
cp .env.example .env
nano .env
```

Fill in your values:

```env
DISCORD_BOT_TOKEN=your_bot_token_here
OPENAI_API_KEY=your_openai_key_here
BOT_OWNER_ID=your_discord_user_id
WISE_SANDBOX_TOKEN=your_wise_token          # optional
CHECKIN_WORKER_URL=your_worker_url          # optional
CHECKIN_AUTH_PASS=your_worker_password       # optional
```

Save with `Ctrl+X`, `Y`, `Enter`.

### Step 6: Test Run

```bash
python3 jbot.py
```

If you see `Bot is ready!` and the cogs loading, it's working. Press `Ctrl+C` to stop.

### Step 7: Run as a Background Service (systemd)

Create a systemd service so the bot starts automatically and survives reboots:

```bash
sudo nano /etc/systemd/system/jenbot.service
```

Paste:

```ini
[Unit]
Description=JenBot Discord Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/JenBot
ExecStart=/root/JenBot/.venv/bin/python3 jbot.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

> **Note:** If you cloned to a different directory or use a non-root user, update `User`, `WorkingDirectory`, and `ExecStart` paths accordingly.

```bash
# Reload systemd, enable and start the bot
sudo systemctl daemon-reload
sudo systemctl enable jenbot
sudo systemctl start jenbot

# Check status
sudo systemctl status jenbot

# View live logs
sudo journalctl -u jenbot -f
```

### Managing the Bot

```bash
# Restart after code changes
sudo systemctl restart jenbot

# Stop the bot
sudo systemctl stop jenbot

# View recent logs
sudo journalctl -u jenbot -n 50
```

### Updating the Bot

```bash
cd ~/JenBot
git pull
sudo systemctl restart jenbot
```

---

## Local Development (Windows)

```powershell
git clone https://github.com/ahtungko/JenBot.git
cd JenBot
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# Edit .env with your values
python jbot.py
```

---

## License

Made with ❤️ by Jenny
