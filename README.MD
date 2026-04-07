# OWN USE ONLY


# JenBot 🤖

A feature-rich Discord bot with AI chat, currency exchange, horoscopes, music downloads, game deals, and more.

## Features

| Category | Commands |
|---|---|
| 🤖 **AI Chat & Utils** | `@JenBot [message]`, `!tldr [count]`, `!clear`, `!nsfw [prompt]` (NSFW channels), `!afk [reason]`, `!pin`, `!pins` |
| 💰 **Economy & Bank**   | `!bal`, `!daily`, `!work`, `!dep [amount]`, `!with [amount]`, `!pf`, `!vault` |
| 🎲 **Gambling & Duels** | `!flip`, `!slots`, `!bj`, `!duel @user`, `!bjduel @user`, `!rob @user` |
| 🏪 **Shop & Inventory** | `!shop`, `!buy [item]`, `!inv`, `!sell [item]`, `!setrole [color]` |
| 📈 **Market & VIP**     | `!gold`, `!silver`, `!bg [amount]`, `!sg [grams]`, `!liverate` |
| 🌟 **Daily & Fun**      | `!ck`, `!streak`, `!horoscope`, `!roast @user`, `!c`, `!cf` |
| 🎵 **Media & Music**    | `!s [query]`, `!d [id]`, `!deals`, `!price [game]` |
| 🌐 **Owner Dashboard**  | Local Flask web application showing live economy insights |

## Project Structure

```
JenBot/
├── jbot.py              # Entry point
├── config.py            # Environment variables & constants
├── requirements.txt     # Python dependencies
├── .env.example         # Template for secrets
├── cogs/                # Modular command groups
│   ├── ai.py            # AI chat, !tldr, !clear, !nsfw
│   ├── checkin.py       # !ck, !streak, !lb
│   ├── currency.py      # Currency exchange, !liverate
│   ├── economy.py       # Economy, Bank, Casino, Duels, Shop, Market
│   ├── fun.py           # !c, !cf, !deals, !price, !dict, !afk, !roast
│   ├── horoscope.py     # Horoscope commands + daily task
│   ├── music.py         # !s, !d (Music streaming and downloads)
│   ├── pins.py          # !pin, !pins, !unpin
│   └── precious.py      # !gold, !silver, live gold status task
├── dashboard/           # JenBot Owner Web Dashboard
│   ├── run_dashboard.py # Python Flask server logic
│   └── templates/       # HTML frontend components
├── utils/               # Shared helpers
│   ├── helpers.py       # Utility functions
│   └── storage.py       # SQLite database logic and File I/O
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
OPENAI_BASE_URL=your_openai_compatible_base_url
NSFW_RESPONSES_URL=https://g2p.tinalee.eu.org/v1/responses
NSFW_API_KEY=your_nsfw_api_key_here
NSFW_MODEL=grok-4
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

### Step 8: Run the Owner Web Dashboard (Optional)

The Flask dashboard runs locally to provide live economy insights without disrupting the Discord bot. You can start it manually:

```bash
cd ~/JenBot
python3 dashboard/run_dashboard.py
```

**Run the Dashboard as a Background Service (systemd)**
To keep the dashboard running automatically in the background, create a dedicated service:

```bash
sudo nano /etc/systemd/system/jenbot-dashboard.service
```

Paste the following configuration:

```ini
[Unit]
Description=JenBot Web Dashboard
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/JenBot
ExecStart=/root/JenBot/.venv/bin/python3 dashboard/run_dashboard.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Then start and enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable jenbot-dashboard
sudo systemctl start jenbot-dashboard
```

*(The dashboard will be available at `http://127.0.0.1:5000`. You can proxy this port via Nginx if you wish to securely expose it to the web).*

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

# 1. Start the main bot:
python jbot.py

# 2. In a separate terminal, start the web UI:
python dashboard/run_dashboard.py
```

---

## License

Made with ❤️ by Jenny
