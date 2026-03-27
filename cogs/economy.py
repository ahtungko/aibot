import os
import random
import sqlite3
import time
import asyncio
from datetime import datetime, timezone, timedelta
import discord
from discord.ext import commands, tasks
from config import COMMAND_PREFIX, TROY_OUNCE_TO_GRAMS
from utils.storage import load_user_data

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'economy.db')

# --- Slot machine config ---
SLOT_EMOJIS = ["🍒", "🍋", "🍊", "🍇", "💎", "7️⃣"]
# Payouts: 3 of a kind multiplier
SLOT_PAYOUTS = {
    "🍒": 2,
    "🍋": 3,
    "🍊": 4,
    "🍇": 5,
    "💎": 10,
    "7️⃣": 25,
}
# 2 of a kind returns your bet


WORK_COOLDOWN = 3600  # 1 hour in seconds
WORK_MIN = 20
WORK_MAX = 150
STARTING_BALANCE = 0


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS wallets (user_id TEXT PRIMARY KEY, balance INTEGER DEFAULT 0, last_daily TEXT DEFAULT '', last_work TEXT DEFAULT '')")
    conn.execute("CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, amount INTEGER, type TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS inventory (user_id TEXT, item_name TEXT, item_type TEXT, item_data TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS investments (user_id TEXT PRIMARY KEY, gold_grams REAL DEFAULT 0.0)")
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS user_stats ("
                 "user_id TEXT PRIMARY KEY, overtime_uses INTEGER DEFAULT 0, "
                 "overtime_last_reset INTEGER DEFAULT 0, overtime_active INTEGER DEFAULT 0, "
                 "last_passive_time INTEGER DEFAULT 0, passive_hourly_total INTEGER DEFAULT 0, "
                 "passive_hour_start INTEGER DEFAULT 0, "
                 "scavenge_daily_total INTEGER DEFAULT 0, "
                 "scavenge_last_reset INTEGER DEFAULT 0)")
    # Migration: Add last_work column if it doesn't exist
    try:
        conn.execute("ALTER TABLE wallets ADD COLUMN last_work TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    # Migration: Add bank column if it doesn't exist
    try:
        conn.execute("ALTER TABLE wallets ADD COLUMN bank INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    # Migration: Add scavenge columns if they don't exist
    for col in [("scavenge_daily_total", "INTEGER DEFAULT 0"), ("scavenge_last_reset", "INTEGER DEFAULT 0"), ("last_scavenge", "INTEGER DEFAULT 0")]:
        try:
            conn.execute(f"ALTER TABLE user_stats ADD COLUMN {col[0]} {col[1]}")
        except sqlite3.OperationalError:
            pass

    # Migration: Add vault_processed to transactions
    try:
        conn.execute("ALTER TABLE transactions ADD COLUMN vault_processed INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    return conn

def get_setting(key: str, default: str = None) -> str:
    row = db_query("SELECT value FROM settings WHERE key = ?", (key,), fetchone=True)
    return row[0] if row else default

def set_setting(key: str, value: str):
    db_query("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?", (key, value, value), commit=True)

def db_query(query, params=(), fetchone=False, fetchall=False, commit=False):
    conn = get_db()
    cursor = conn.execute(query, params)
    result = None
    if fetchone: result = cursor.fetchone()
    if fetchall: result = cursor.fetchall()
    if commit: conn.commit()
    conn.close()
    return result

def log_transaction(user_id, amount, tx_type, processed=0):
    db_query("INSERT INTO transactions (user_id, amount, type, timestamp, vault_processed) VALUES (?, ?, ?, ?, ?)", 
             (str(user_id), amount, tx_type, int(time.time()), processed), commit=True)

def track_fee(amount):
    """Updates the global fee vault and returns True if successful."""
    try:
        vault_bal = int(float(get_setting("fee_vault", "0")))
        set_setting("fee_vault", str(vault_bal + amount))
        return True
    except:
        return False

def get_balance(user_id: str) -> int:
    row = db_query("SELECT balance FROM wallets WHERE user_id = ?", (user_id,), fetchone=True)
    return row[0] if row else STARTING_BALANCE

def set_balance(user_id: str, amount: int):
    db_query("INSERT INTO wallets (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = ?", (user_id, amount, amount), commit=True)

def add_balance(user_id: str, amount: int) -> int:
    new_bal = max(0, get_balance(user_id) + amount)
    set_balance(user_id, new_bal)
    return new_bal

def get_bank(user_id: str) -> int:
    row = db_query("SELECT bank FROM wallets WHERE user_id = ?", (user_id,), fetchone=True)
    return row[0] if row and row[0] is not None else 0

def set_bank(user_id: str, amount: int):
    db_query("INSERT INTO wallets (user_id, bank) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET bank = ?", (user_id, amount, amount), commit=True)

def add_bank(user_id: str, amount: int) -> int:
    new_bank = max(0, get_bank(user_id) + amount)
    set_bank(user_id, new_bank)
    return new_bank

def pay_jc(user_id: str, amount: int) -> tuple[bool, str]:
    """
    Attempts to deduct 'amount' from user's Wallet only.
    Returns (Success, Description)
    """
    wallet = get_balance(user_id)
    
    if wallet < amount:
        return False, f"❌ You need **{amount:,} JC** in your Wallet, but you only have **{wallet:,} JC**! Withdraw some from your Bank first."
    
    add_balance(user_id, -amount)
    return True, f"💸 Paid **{amount:,} JC** from your Wallet."

def get_bank_limit(user_id: str) -> float:
    """
    Calculates the user's total bank storage limit (Base + Upgrades).
    Upgrades are stackable (+50k per Safe, +250k per Vault).
    """
    base = 50000
    
    # Check for Unlimited Bunker
    if get_inventory_item(user_id, "Titanium Bunker"):
        return float('inf')
        
    # Count instances of stackable upgrades
    iron_count_row = db_query("SELECT COUNT(*) FROM inventory WHERE user_id = ? AND item_name = 'Iron Safe'", (user_id,), fetchone=True)
    steel_count_row = db_query("SELECT COUNT(*) FROM inventory WHERE user_id = ? AND item_name = 'Steel Vault'", (user_id,), fetchone=True)
    
    iron_count = iron_count_row[0] if iron_count_row else 0
    steel_count = steel_count_row[0] if steel_count_row else 0
    
    extra = (iron_count * 50000) + (steel_count * 250000)
    return base + extra

def get_last_daily(user_id: str) -> str:
    row = db_query("SELECT last_daily FROM wallets WHERE user_id = ?", (user_id,), fetchone=True)
    return row[0] if row else ""

def set_last_daily(user_id: str, date_str: str):
    db_query("INSERT INTO wallets (user_id, last_daily) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET last_daily = ?", (user_id, date_str, date_str), commit=True)

def get_last_work(user_id: str) -> str:
    row = db_query("SELECT last_work FROM wallets WHERE user_id = ?", (user_id,), fetchone=True)
    return row[0] if row else ""

def set_last_work(user_id: str, ts_str: str):
    db_query("INSERT INTO wallets (user_id, last_work) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET last_work = ?", (user_id, ts_str, ts_str), commit=True)

def get_user_stats(user_id: str) -> dict:
    row = db_query("SELECT overtime_uses, overtime_last_reset, overtime_active, last_passive_time, passive_hourly_total, passive_hour_start, scavenge_daily_total, scavenge_last_reset, last_scavenge FROM user_stats WHERE user_id = ?", (user_id,), fetchone=True)
    if row:
        return {
            "overtime_uses": row[0],
            "overtime_last_reset": row[1],
            "overtime_active": row[2],
            "last_passive_time": row[3],
            "passive_hourly_total": row[4],
            "passive_hour_start": row[5],
            "scavenge_daily_total": row[6] or 0,
            "scavenge_last_reset": row[7] or 0,
            "last_scavenge": row[8] or 0
        }
    else:
        db_query("INSERT INTO user_stats (user_id) VALUES (?)", (user_id,), commit=True)
        return {
            "overtime_uses": 0,
            "overtime_last_reset": 0,
            "overtime_active": 0,
            "last_passive_time": 0,
            "passive_hourly_total": 0,
            "passive_hour_start": 0,
            "scavenge_daily_total": 0,
            "scavenge_last_reset": 0,
            "last_scavenge": 0
        }

def update_user_stats(user_id: str, **kwargs):
    if not kwargs: return
    fields = []
    values = []
    for k, v in kwargs.items():
        fields.append(f"{k} = ?")
        values.append(v)
    values.append(user_id)
    query = f"UPDATE user_stats SET {', '.join(fields)} WHERE user_id = ?"
    db_query(query, tuple(values), commit=True)

def get_top_balances(limit=10) -> list:
    """Returns Top users with their JC and Gold stats for Net Worth calculation."""
    return db_query(
        "SELECT w.user_id, w.balance, IFNULL(w.bank, 0), IFNULL(i.gold_grams, 0) "
        "FROM wallets w "
        "LEFT JOIN investments i ON w.user_id = i.user_id "
        "ORDER BY (w.balance + IFNULL(w.bank, 0)) DESC LIMIT 50", # Fetch more to sort by net worth in Python
        fetchall=True
    )

def add_item(user_id, item_name, item_type="Collectible", item_data=""):
    db_query("INSERT INTO inventory (user_id, item_name, item_type, item_data) VALUES (?, ?, ?, ?)", (user_id, item_name, item_type, item_data), commit=True)

def get_inventory(user_id):
    return db_query("SELECT item_name, item_type, item_data FROM inventory WHERE user_id = ?", (user_id,), fetchall=True)

# --- Investment Helpers ---

def get_gold_grams(user_id: str) -> float:
    row = db_query("SELECT gold_grams FROM investments WHERE user_id = ?", (user_id,), fetchone=True)
    return row[0] if row else 0.0

def add_gold_grams(user_id: str, amount: float):
    current = get_gold_grams(user_id)
    new_amount = current + amount
    if new_amount < 0.000001:  # Floating point precision safe zero
        new_amount = 0.0
    db_query("INSERT INTO investments (user_id, gold_grams) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET gold_grams = ?", (user_id, new_amount, new_amount), commit=True)

async def fetch_live_gold_price(bot) -> float:
    """Fetches the live gold price in USD/g"""
    currency_code = "USD"
    cookies = {'wcid': 'D95hVgSMso1SAAAC', 'react_component_complete': 'true'}
    headers = {
        'accept': '*/*', 'accept-language': 'en-US,en-GB;q=0.9,en;q=0.8',
        'referer': 'https://goldprice.org/spot-gold.html', 'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors', 'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
    }
    price_api_url = f"https://data-asg.goldprice.org/dbXRates/{currency_code}"
    try:
        async with bot.http_session.get(price_api_url, cookies=cookies, headers=headers) as resp:
            resp.raise_for_status()
            data = await resp.json()
        price_data = data.get("items")[0]
        xau_price_gram = price_data.get('xauPrice', 0) / TROY_OUNCE_TO_GRAMS
        return xau_price_gram
    except Exception as e:
        print(f"Error fetching gold price: {e}")
        return None

# --- VIP Helpers ---

def get_vip_expiry(user_id: str) -> int:
    row = db_query("SELECT item_data FROM inventory WHERE user_id = ? AND item_name = 'VIP'", (user_id,), fetchone=True)
    try:
        return int(row[0]) if row else 0
    except (ValueError, TypeError):
        return 0

def is_vip(user_id: str) -> bool:
    expiry = get_vip_expiry(user_id)
    return expiry > int(time.time())

def set_vip(user_id: str, days: int):
    now = int(time.time())
    current_expiry = get_vip_expiry(user_id)
    
    start_time = max(now, current_expiry)
    new_expiry = start_time + (days * 24 * 3600)
    
    if current_expiry > 0:
        db_query("UPDATE inventory SET item_data = ? WHERE user_id = ? AND item_name = 'VIP'", (str(new_expiry), user_id), commit=True)
    else:
        db_query("INSERT INTO inventory (user_id, item_name, item_type, item_data) VALUES (?, 'VIP', 'Subscription', ?)", (user_id, str(new_expiry)), commit=True)

def get_inventory_item(user_id, item_name):
    row = db_query("SELECT 1 FROM inventory WHERE user_id = ? AND item_name = ?", (user_id, item_name), fetchone=True)
    return row is not None

def get_item_count(user_id, item_name):
    row = db_query("SELECT COUNT(*) FROM inventory WHERE user_id = ? AND item_name = ?", (user_id, item_name), fetchone=True)
    return row[0] if row else 0

def remove_item(user_id, item_name):
    # Remove only ONE instance of the item
    db_query("DELETE FROM inventory WHERE ROWID = (SELECT ROWID FROM inventory WHERE user_id = ? AND item_name = ? LIMIT 1)", (user_id, item_name), commit=True)

def remove_items(user_id, item_name, count=1):
    # Remove multiple instances of the item
    db_query("DELETE FROM inventory WHERE ROWID IN (SELECT ROWID FROM inventory WHERE user_id = ? AND item_name = ? LIMIT ?)", (user_id, item_name, count), commit=True)

def get_luck_bonus(user_id: str) -> float:
    """Returns 0.05 if a Lucky Charm is active, else 0."""
    now = int(time.time())
    row = db_query("SELECT MAX(item_data) FROM inventory WHERE user_id = ? AND item_name = 'Lucky Charm'", (user_id,), fetchone=True)
    if row and row[0]:
        try:
            expiry = int(row[0])
            if expiry > now:
                return 0.05
        except: pass
    return 0.0

def get_best_pickaxe(user_id: str) -> dict:
    """
    Returns data for the user's best mining tool.
    Returns: {name: str, bonus: int, cooldown_reduction: int, overtime_max: int, tax_reduction: float, tax_dodge: float, passive_active: bool}
    """
    # Order matters: Mithril -> Netherite -> Diamond -> Golden -> Iron -> Stone
    tools = [
        {"name": "Mithril Drill", "bonus": 80, "cooldown_reduction": 2100, "overtime_max": 3, "tax_reduction": 0.0, "tax_dodge": 0.0, "passive_active": True},
        {"name": "Netherite Pickaxe", "bonus": 60, "cooldown_reduction": 1500, "overtime_max": 2, "tax_reduction": 0.0, "tax_dodge": 0.10, "passive_active": False},
        {"name": "Diamond Pickaxe", "bonus": 45, "cooldown_reduction": 1200, "overtime_max": 1, "tax_reduction": 0.0, "tax_dodge": 0.0, "passive_active": False},
        {"name": "Golden Pickaxe", "bonus": 30, "cooldown_reduction": 900, "overtime_max": 0, "tax_reduction": 0.01, "tax_dodge": 0.0, "passive_active": False},
        {"name": "Iron Pickaxe", "bonus": 20, "cooldown_reduction": 600, "overtime_max": 0, "tax_reduction": 0.0, "tax_dodge": 0.0, "passive_active": False},
        {"name": "Stone Pickaxe", "bonus": 10, "cooldown_reduction": 300, "overtime_max": 0, "tax_reduction": 0.0, "tax_dodge": 0.0, "passive_active": False}
    ]
    
    for tool in tools:
        if get_inventory_item(user_id, tool["name"]):
            return tool
            
    # Default Wooden Pickaxe (or no pickaxe) state
    return {"name": "Wooden Pickaxe", "bonus": 0, "cooldown_reduction": 0, "overtime_max": 0, "tax_reduction": 0.0, "tax_dodge": 0.0, "passive_active": False}

def get_last_rob(user_id: str) -> int:
    row = db_query("SELECT item_data FROM inventory WHERE user_id = ? AND item_name = 'last_rob'", (user_id,), fetchone=True)
    try:
        return int(row[0]) if row and row[0] else 0
    except (ValueError, TypeError):
        return 0

def set_last_rob(user_id: str, ts: int):
    existing_rob_entry = db_query("SELECT 1 FROM inventory WHERE user_id = ? AND item_name = 'last_rob'", (user_id,), fetchone=True)
    
    if existing_rob_entry:
        db_query("UPDATE inventory SET item_data = ? WHERE user_id = ? AND item_name = 'last_rob'", (str(ts), user_id), commit=True)
    else:
        db_query("INSERT INTO inventory (user_id, item_name, item_type, item_data) VALUES (?, 'last_rob', 'Cooldown', ?)", (user_id, str(ts)), commit=True)

def track_fee(amount: int):
    """Adds to the global fee vault."""
    current = int(get_setting("fee_vault", "0"))
    set_setting("fee_vault", str(current + amount))

def track_gold_fee(amount: float):
    """Adds to the global gold fee vault."""
    current = float(get_setting("gold_fee_vault", "0.0"))
    set_setting("gold_fee_vault", str(current + amount))

def get_box_rates():
    """Returns the currently active Mystery Box loot rates."""
    now = int(time.time())
    expiry = int(float(get_setting('box_event_expiry', '0')))
    
    if now < expiry:
        leg = float(get_setting('box_legendary_event', '0.001'))
        epic = float(get_setting('box_epic_event', '0.01'))
        rare = float(get_setting('box_rare_event', '0.03'))
        is_event = True
    else:
        leg = float(get_setting('box_legendary_rate', '0.001'))
        epic = float(get_setting('box_epic_rate', '0.01'))
        rare = float(get_setting('box_rare_rate', '0.03'))
        is_event = False
        
    return {
        'legendary': leg,
        'epic': epic,
        'rare': rare,
        'is_event': is_event,
        'expiry': expiry
    }

def get_last_gold_fee(user_id: str):
    res = db_query("SELECT item_data FROM inventory WHERE user_id = ? AND item_name = 'last_gold_fee'", (user_id,), fetchone=True)
    return int(res[0]) if res else None

def set_last_gold_fee(user_id: str, ts: int):
    existing = db_query("SELECT 1 FROM inventory WHERE user_id = ? AND item_name = 'last_gold_fee'", (user_id,), fetchone=True)
    if existing:
        db_query("UPDATE inventory SET item_data = ? WHERE user_id = ? AND item_name = 'last_gold_fee'", (str(ts), user_id), commit=True)
    else:
        db_query("INSERT INTO inventory (user_id, item_name, item_type, item_data) VALUES (?, 'last_gold_fee', 'System', ?)", (user_id, str(ts)), commit=True)

def apply_gold_fees(user_id: str):
    """
    Checks if 7 days have passed since the last gold fee.
    Deducts 10% (Normal) or 8% (VIP) and updates the vault.
    Returns a warning message if fees were paid, otherwise None.
    """
    now = int(time.time())
    last_fee_ts = get_last_gold_fee(user_id)
    gold = get_gold_grams(user_id)
    
    if gold < 0.001:
        # No gold, just keep the timestamp updated
        set_last_gold_fee(user_id, now)
        return None

    if last_fee_ts is None:
        # First time initialization
        set_last_gold_fee(user_id, now)
        return None
    
    diff = now - last_fee_ts
    week_seconds = 7 * 24 * 3600
    if diff < week_seconds:
        return None
    
    periods = int(diff // week_seconds)
    rate = 0.08 if is_vip(user_id) else 0.10
    
    # Calculate compounded fee
    new_gold = gold * ((1 - rate) ** periods)
    fee_amount = int((gold - new_gold) * 1000) / 1000.0
    
    if fee_amount > 0:
        add_gold_grams(user_id, -fee_amount)
        track_gold_fee(fee_amount)
        # Advance the timestamp by full weeks to keep the schedule
        set_last_gold_fee(user_id, last_fee_ts + (periods * week_seconds))
        
        log_transaction(user_id, 0, f"Paid {fee_amount}g Storage Fee ({periods} weeks)")
        return f"⚖️ **Gold Storage Fee**: You paid **{fee_amount:.3g}g** in storage fees for the last **{periods}** week(s)."
    
    return None

# --- Helpers ---

async def validate_bet(ctx: commands.Context, amount_str):
    """
    Validates a bet amount, handling commas and 'max'/'all'.
    Checks Wallet balance only.
    Returns (amount_int, error_message)
    """
    uid = str(ctx.author.id)
    wallet = get_balance(uid)

    if amount_str is None:
        return None, "❌ Please provide a positive bet amount!"

    s = str(amount_str).lower().replace(',', '')
    if s in ['max', 'all']:
        amount = wallet
    else:
        try:
            amount = int(s)
        except ValueError:
            return None, "❌ Invalid amount! Use numbers or 'max'."

    if amount <= 0:
        return None, "❌ Please provide a positive bet amount!"
    
    if wallet < amount:
        return None, f"❌ You only have **{wallet:,}** JC in your Wallet. Withdraw from your Bank if needed."
    
    return amount, None

async def validate_admin_amount(ctx: commands.Context, amount: int):
    if amount <= 0:
        await ctx.send("❌ Amount must be positive.")
        return False
    return True


class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.taxman_task.start()
        self.passive_cache = {} # {uid: last_awarded_time}

    def _get_stability_ratio(self):
        """Calculates current vault-to-circulation ratio."""
        try:
            # Get Wallet + Bank totals
            row = db_query("SELECT SUM(balance + IFNULL(bank, 0)) FROM wallets", fetchone=True)
            total_jc = row[0] if row and row[0] else 0
            
            # Get Vault
            vault_jc = int(float(get_setting("fee_vault", "0")))
            
            if total_jc <= 0: return 2.0 # Assume high stability if no players
            return vault_jc / total_jc
        except:
            return 0.5 # Safe default

    def cog_unload(self):
        self.taxman_task.cancel()

    @tasks.loop(hours=1) # Check more frequently than 24h to handle restarts better
    async def taxman_task(self):
        """The Taxman visits once a day..."""
        # Wait for bot to be ready
        await self.bot.wait_until_ready()
        
        # Check if Taxman is enabled
        enabled = get_setting("taxman_enabled", "False").lower() == "true"
        if not enabled:
            return
            
        # Check last tax time
        now = int(time.time())
        day_seconds = 24 * 60 * 60
        last_tax = int(get_setting("last_tax_timestamp", "0"))
        
        if now - last_tax < day_seconds:
            # Not time yet
            return
            
        # Update last tax time immediately to prevent race conditions or multiple triggers
        set_setting("last_tax_timestamp", str(now))
        
        # Get target channel
        channel_id = get_setting("tax_channel_id")
        channel = None
        if channel_id:
            channel = self.bot.get_channel(int(channel_id))
        
        # If no channel set, look for first available channel
        if not channel:
            for guild in self.bot.guilds:
                target = guild.system_channel
                if not target or not target.permissions_for(guild.me).send_messages:
                    for ch in guild.text_channels:
                        if ch.permissions_for(guild.me).send_messages:
                            target = ch
                            break
                if target:
                    channel = target
                    break
        
        if not channel:
            print("Taxman: No announcement channel found.")
            # We still run the tax even if no announcement channel
        
        # Get tax percentage (default 10%)
        tax_pct = int(get_setting("taxman_percent", "10"))
        
        # Get all users with total wealth > 100,000
        # This is a bit expensive but runs only once a day
        rows = db_query("SELECT user_id, balance, bank FROM wallets WHERE (balance + bank) > 100000", fetchall=True)
        if not rows:
            return

        population = []
        weights = []
        
        for row in rows:
            uid, bal, bank = row
            total = bal + bank
            population.append(row)
            weights.append(total) # Richer = higher chance
            
        if not population:
            return
            
        # Weighted random selection
        victim_row = random.choices(population, weights=weights, k=1)[0]
        v_uid, v_bal, v_bank = victim_row
        v_total = v_bal + v_bank
        
        # Check for insurance
        now = int(time.time())
        row = db_query("SELECT item_data FROM inventory WHERE user_id = ? AND item_name = 'Coin Insurance'", (v_uid,), fetchone=True)
        is_insured = False
        if row:
            try:
                expiry = int(row[0])
                if expiry > now:
                    is_insured = True
            except: pass
            
        if is_insured:
            if channel:
                member = await self.bot.fetch_user(int(v_uid))
                embed = discord.Embed(
                    title="🕵️ The Taxman Visit",
                    description=f"The Taxman knocked on {member.mention}'s door, but they had **Coin Insurance**! 📜\n\nNo taxes were collected today.",
                    color=discord.Color.blue()
                )
                await channel.send(embed=embed)
            return

        # Tax configured %
        tax_amount = int(v_total * (tax_pct / 100))
        
        # Deduct proportionally
        wallet_tax = int(tax_amount * (v_bal / v_total)) if v_total > 0 else 0
        bank_tax = tax_amount - wallet_tax
        
        add_balance(v_uid, -wallet_tax)
        add_bank(v_uid, -bank_tax)
        track_fee(tax_amount) # Track tax collection
        log_transaction(v_uid, -tax_amount, "The Taxman (10% Tax)")
        
        if channel:
            member = await self.bot.fetch_user(int(v_uid))
            embed = discord.Embed(
                title="🚨 TAXED BY THE TAXMAN!",
                description=f"The Taxman has visited {member.mention} and collected a **10%** wealth tax! 🏛️",
                color=discord.Color.red()
            )
            embed.add_field(name="Amount Collected", value=f"**{tax_amount:,}** JC", inline=True)
            embed.set_footer(text="No warning, no mercy. Get Coin Insurance to stay safe!")
            await channel.send(content=member.mention, embed=embed)

    @taxman_task.before_loop
    async def before_taxman_task(self):
        await self.bot.wait_until_ready()

    @commands.command(name='settaxchannel')
    @commands.is_owner()
    async def settaxchannel_command(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Set the channel for Taxman announcements. Usage: !settaxchannel [#channel]"""
        if not channel:
            channel = ctx.channel
            
        set_setting("tax_channel_id", str(channel.id))
        await ctx.send(f"✅ Taxman announcements will now be sent to {channel.mention}!")

    @settaxchannel_command.error
    async def settaxchannel_error(self, ctx, error):
        if isinstance(error, commands.NotOwner):
            await ctx.send("❌ Only the bot owner can set the tax channel!")

    @commands.command(name='setboxchannel')
    @commands.is_owner()
    async def setboxchannel_command(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Set the channel for Mystery Box event announcements. Usage: !setboxchannel [#channel]"""
        if not channel:
            channel = ctx.channel
            
        set_setting("box_channel_id", str(channel.id))
        await ctx.send(f"✅ Mystery Box event announcements will now be sent to {channel.mention}!")

    @setboxchannel_command.error
    async def setboxchannel_error(self, ctx, error):
        if isinstance(error, commands.NotOwner):
            await ctx.send("❌ Only the bot owner can set the box channel!")

    @commands.command(name='setnoticechannel')
    @commands.is_owner()
    async def setnoticechannel_command(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Set the channel for general bot notices. Usage: !setnoticechannel [#channel]"""
        if not channel:
            channel = ctx.channel
            
        set_setting("notice_channel_id", str(channel.id))
        await ctx.send(f"✅ General bot notices will now be sent to {channel.mention}!")

    @setnoticechannel_command.error
    async def setnoticechannel_error(self, ctx, error):
        if isinstance(error, commands.NotOwner):
            await ctx.send("❌ Only the bot owner can set the notice channel!")

    @commands.command(name='setnotice')
    @commands.is_owner()
    async def setnotice_command(self, ctx: commands.Context, *, message: str = None):
        """Send an announcement to the notice channel. Usage: !setnotice [message]"""
        if not message:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}setnotice [your message]`")
            return
            
        channel_id = get_setting("notice_channel_id")
        channel = None
        if channel_id:
            channel = self.bot.get_channel(int(channel_id))
            
        if not channel:
            # Default to first available text channel
            for guild in self.bot.guilds:
                target = guild.system_channel
                if not target or not target.permissions_for(guild.me).send_messages:
                    for ch in guild.text_channels:
                        if ch.permissions_for(guild.me).send_messages:
                            target = ch
                            break
                if target:
                    channel = target
                    break
        
        if not channel:
            await ctx.send("❌ No announcement channel found and couldn't find a fallback!")
            return
            
        embed = discord.Embed(
            title="📢 BOT ANNOUNCEMENT",
            description=message,
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text=f"By {ctx.author.display_name}")
        await channel.send(embed=embed)
        await ctx.send(f"✅ Announcement sent to {channel.mention}!")

    @commands.command(name='taxstatus')
    @commands.is_owner()
    async def taxstatus_command(self, ctx: commands.Context):
        """Owner Only: Check Taxman settings and next visit schedule."""
        enabled = get_setting("taxman_enabled", "False").lower() == "true"
        tax_pct = int(get_setting("taxman_percent", "10"))
        last_tax = int(get_setting("last_tax_timestamp", "0"))
        now = int(time.time())
        day_seconds = 24 * 60 * 60
        next_tax = last_tax + day_seconds
        
        status_str = "🟢 **Active**" if enabled else "🔴 **Disabled**"
        
        embed = discord.Embed(title="🕵️ Taxman System Status", color=discord.Color.gold())
        embed.add_field(name="Status", value=status_str, inline=True)
        embed.add_field(name="Tax Rate", value=f"**{tax_pct}%**", inline=True)
        embed.add_field(name="Threshold", value="**> 100,000 JC**", inline=True)
        
        if enabled:
            if now >= next_tax:
                embed.add_field(name="Next Visit", value="*Imminent!* (Checking...) ", inline=False)
            else:
                embed.add_field(name="Next Visit", value=f"<t:{next_tax}:R>", inline=False)
            if last_tax > 0:
                embed.add_field(name="Last Visit", value=f"<t:{last_tax}:R>", inline=True)
        else:
            embed.add_field(name="Next Visit", value="*N/A (System Disabled)*", inline=False)
            
        await ctx.send(embed=embed)

    @commands.command(name='settaxmantoggle')
    @commands.is_owner()
    async def settaxmantoggle_command(self, ctx: commands.Context, status: str = None):
        """Owner Only: Enable or disable the Taxman. Usage: !settaxmantoggle [on/off]"""
        if not status:
            current = get_setting("taxman_enabled", "False").lower() == "true"
            await ctx.send(f"Current Taxman status is: {'**ON**' if current else '**OFF**'}. Use `!settaxmantoggle on` or `off` to change.")
            return
            
        status = status.lower()
        if status in ['on', 'yes', 'true', '1', 'enable']:
            set_setting("taxman_enabled", "True")
            await ctx.send("✅ **Taxman has been ENABLED.** He will visit once every 24 hours.")
        elif status in ['off', 'no', 'false', '0', 'disable']:
            set_setting("taxman_enabled", "False")
            await ctx.send("🛑 **Taxman has been DISABLED.**")
        else:
            await ctx.send("❌ Please use `on` or `off`!")

    @commands.command(name='settaxmanpercent')
    @commands.is_owner()
    async def settaxmanpercent_command(self, ctx: commands.Context, percent: int = None):
        """Owner Only: Set the Taxman's tax rate percentage. Usage: !settaxmanpercent [1-100]"""
        if percent is None:
            current = get_setting("taxman_percent", "10")
            await ctx.send(f"Current Taxman rate is: **{current}%**. Usage: `!settaxmanpercent [number]`")
            return
            
        if not (1 <= percent <= 100):
            await ctx.send("❌ Percentage must be between 1 and 100!")
            return
            
        set_setting("taxman_percent", str(percent))
        await ctx.send(f"✅ **Taxman tax rate set to {percent}%!**")

    @settaxmantoggle_command.error
    @settaxmanpercent_command.error
    @taxstatus_command.error
    async def taxmansettings_error(self, ctx, error):
        if isinstance(error, commands.NotOwner):
            await ctx.send("❌ This command is restricted to the bot owner!")


            
    @commands.command(name='fish')
    @commands.cooldown(1, 15, commands.BucketType.user)
    async def fish_command(self, ctx: commands.Context):
        """Cast your line! Cost: 50 JC | Returns: 85-95% (Avg)"""
        uid = str(ctx.author.id)
        cost = 50
        
        # Check balance
        bal = get_balance(uid)
        if bal < cost:
            await ctx.send(f"❌ You need at least **{cost} JC** to fish!")
            ctx.command.reset_cooldown(ctx)
            return
            
        # Deduct cost
        add_balance(uid, -cost)
        log_transaction(uid, -cost, "Fishing Trip Fee")
        
        # --- RNG & Outcomes ---
        # 50% Trash (0 JC)
        # 42% Common (55-65 JC, Avg 60)
        # 7% Rare (120-180 JC, Avg 150)
        # 1% Legendary (200-400 JC, Avg 300)
        # Overall Avg Return: ~43.2 -> ~38.7 JC (77.4%)
        
        roll = random.random() * 100
        rarity = "Trash"
        reward = 0
        fish_name = None
        
        trash_lines = [
            "You fished up... absolutely nothing. Even the fish logged off.",
            "A boot. Not even a matching pair.",
            "The ocean saw you coming and hid everything.",
            "You caught water. Congratulations.",
            "Fish spotted you and chose violence: they left.",
            "Not even trash wanted to be caught by you.",
            "You scared the ecosystem.",
            "Even the trash dodged you. That’s impressive.",
            "You reeled in disappointment.",
            "That spot is now officially fishless."
        ]
        rare_roast = "💀 Local fish union has banned you from fishing."
        
        common_lines = [
            "Nice catch! Dinner secured 🍽️",
            "Not bad, not bad. The fish slipped up.",
            "You actually caught something. Improvement!",
            "A solid catch. Fisher instincts kicking in.",
            "Clean pull. Nothing fancy, but it counts.",
            "The ocean finally acknowledged your existence.",
            "Respectable catch. You won’t starve today.",
            "That fish made a mistake... and paid for it."
        ]
        
        rare_lines = [
            "🔥 That’s a rare one! Big haul!",
            "Now THAT’S what we call fishing!",
            "You struck gold... but fish.",
            "The ocean regrets underestimating you.",
            "Elite catch! Chat better be watching this.",
            "That fish had dreams. You ended them.",
            "Certified fisherman moment 🎣",
            "That’s going straight to the trophy wall."
        ]
        rare_hype = "💫 Legend says only 1 in many get this... and you did."
        
        legendary_lines = [
            "👑 LEGENDARY CATCH! The ocean bows to you.",
            "You didn’t fish... you conquered.",
            "This will be remembered in fishing history.",
            "The sea is filing a complaint against you.",
            "ABSOLUTE MONSTER CATCH 🐉",
            "You just peaked. It’s all downhill from here.",
            "Even the whales are impressed.",
            "You are now legally the ocean’s main character."
        ]
        legendary_twist = "💀 You caught a legendary fish... it was NOT happy about it."
        
        tease_lines = [
            "Something HUGE got away at the last second...",
            "Your line snapped. That one was big.",
            "You felt a massive pull... then nothing.",
            "A rare fish escaped. Skill issue?",
            "That was almost legendary. Almost."
        ]
        
        embed_color = discord.Color.light_grey()
        title = "🎣 Fishing Trip"
        
        if roll <= 50: # Trash
            rarity = "Trash"
            reward = 0
            if random.random() < 0.05: # 5% Rare Roast
                msg = rare_roast
            else:
                msg = random.choice(trash_lines)
            embed_color = discord.Color.dark_grey()
            
            # 10% change to show a tease line instead
            if random.random() < 0.10:
                msg = random.choice(tease_lines)
                
        elif roll <= 92: # Common
            rarity = "Common"
            reward = random.randint(55, 65)
            msg = random.choice(common_lines)
            fish_name = random.choice(["Sardine", "Cod", "Mackerel", "Sea Bass", "Salmon"])
            embed_color = discord.Color.blue()
            
        elif roll <= 99: # Rare
            rarity = "Rare"
            reward = random.randint(120, 180)
            if random.random() < 0.10: # 10% Rare Hype
                msg = rare_hype
            else:
                msg = random.choice(rare_lines)
            fish_name = random.choice(["Golden Trout", "Anglerfish", "Pufferfish", "Swordfish", "Tuna"])
            embed_color = discord.Color.purple()
            title = "✨ RARE CATCH! ✨"
            
        else: # Legendary
            rarity = "Legendary"
            reward = random.randint(200, 400)
            if random.random() < 0.10: # 10% Legendary Twist
                msg = legendary_twist
            else:
                msg = random.choice(legendary_lines)
            fish_name = random.choice(["Blue Marlin", "The Kraken", "Great White Shark", "Ancient Coelacanth"])
            embed_color = discord.Color.gold()
            title = "🐉 LEGENDARY CATCH!!! 🐉"

        # Apply reward
        if reward > 0:
            add_balance(uid, reward)
            log_transaction(uid, reward, f"Fishing Reward ({rarity})")
            

            
        async with ctx.typing():
            await asyncio.sleep(2) # Fishing wait...
            
        embed = discord.Embed(title=title, description=msg, color=embed_color)
        if fish_name:
            embed.add_field(name="Caught", value=f"**{fish_name}** ({rarity})", inline=True)
            embed.add_field(name="Payout", value=f"**{reward} JC**", inline=True)
        else:
            embed.add_field(name="Result", value="Nothing but junk.", inline=True)
            
        embed.set_footer(text=f"Total Wealth: {get_balance(uid) + get_bank(uid):,} JC")
        await ctx.send(content=ctx.author.mention, embed=embed)

    @fish_command.error
    async def fish_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏳ Don't overfish! Cast again in **{error.retry_after:.1f}s**.")


        
    @commands.command(name='testtaxman')
    @commands.is_owner()
    async def testtaxman_command(self, ctx: commands.Context):
        """Trigger the taxman task manually (Owner only)."""
        await ctx.send("🕵️ Triggering the Taxman...")
        await self.taxman_task()
        await ctx.send("✅ Taxman task execution finished.")

    @commands.command(name='bal', aliases=['balance', 'wallet'])
    async def balance_command(self, ctx: commands.Context, member: discord.Member = None):
        """Check your (or someone else's) JC balance."""
        target = member or ctx.author
        uid = str(target.id)
        
        # Apply storage fees if applicable
        fee_msg = apply_gold_fees(uid)
        if fee_msg: await ctx.send(f"{target.mention}, {fee_msg}")
        
        wallet = get_balance(uid)
        bank = get_bank(uid)
        total = wallet + bank
        limit = get_bank_limit(uid)
        limit_str = f"{limit:,}" if limit != float('inf') else "Unlimited"
        
        embed = discord.Embed(
            title=f"💰 {target.display_name}'s Balances",
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="💵 Wallet", value=f"**{wallet:,}** JC", inline=True)
        embed.add_field(name="🏦 Bank", value=f"**{bank:,}** / {limit_str} JC", inline=True)
        embed.add_field(name="Total Net Worth", value=f"**{total:,}** JC", inline=False)
        await ctx.send(embed=embed)
        
    @commands.command(name='deposit', aliases=['dep'])
    async def deposit_command(self, ctx: commands.Context, amount_str: str = None):
        """Deposit JC into your secure Bank. Usage: !deposit [amount | max]"""
        uid = str(ctx.author.id)
        current_bank = get_bank(uid)
        limit = get_bank_limit(uid)

        # Check room left
        room_left = limit - current_bank
        if room_left <= 0 and limit != float('inf'):
            await ctx.send(f"❌ Your bank is already at or above its current capacity (**{limit:,}** JC)! Upgrade your vault in the `!shop` to store more.")
            return

        # Handle max/all properly with the limit
        amount, err = await validate_bet(ctx, amount_str)
        if err:
            await ctx.send(err)
            return
            
        # --- AUTO-CAP LOGIC ---
        is_capped = False
        if amount > room_left and limit != float('inf'):
            amount = int(room_left)
            is_capped = True
            
        add_balance(uid, -amount)
        new_bank = add_bank(uid, amount)
        log_transaction(uid, amount, "Bank Deposit")
        
        if is_capped:
            await ctx.send(f"🏦 **Bank Full!** {ctx.author.mention}, you deposited **{amount:,}** JC (filling the vault to its **{limit:,}** limit).\nNew Bank Balance: **{new_bank:,}** JC.")
        else:
            await ctx.send(f"🏦 {ctx.author.mention}, you deposited **{amount:,}** JC into your bank.\nNew Bank Balance: **{new_bank:,}** / {limit if limit != float('inf') else 'Unlimited':,} JC.")

    @commands.command(name='withdraw', aliases=['with'])
    async def withdraw_command(self, ctx: commands.Context, amount_str: str = None):
        """Withdraw JC from your secure Bank. Usage: !withdraw [amount | max]"""
        uid = str(ctx.author.id)
        bank = get_bank(uid)
        
        if amount_str is None:
            await ctx.send(f"❌ Please provide an amount to withdraw! (Current Bank: **{bank:,}** JC)")
            return
            
        s = str(amount_str).lower().replace(',', '')
        if s in ['max', 'all']:
            amount = bank
        else:
            try:
                amount = int(s)
            except ValueError:
                await ctx.send("❌ Invalid amount! Use numbers or 'max'.")
                return
                
        if amount <= 0:
            await ctx.send("❌ Amount must be positive.")
            return
            
        if bank < amount:
            await ctx.send(f"❌ You only have **{bank:,}** JC in your bank.")
            return
            
        add_bank(uid, -amount)
        new_bal = add_balance(uid, amount)
        log_transaction(uid, amount, "Bank Withdrawal")
        
        await ctx.send(f"💵 {ctx.author.mention}, you withdrew **{amount:,}** JC from your bank.\nNew Wallet Balance: **{new_bal:,}** JC.")

    @commands.command(name='daily')
    async def daily_command(self, ctx: commands.Context):
        """Claim your daily JC!"""
        uid = str(ctx.author.id)
        now_gmt8 = datetime.now(timezone(timedelta(hours=8)))
        today = now_gmt8.strftime("%Y-%m-%d")
        last = get_last_daily(uid)

        if last == today:
            await ctx.send(f"⏰ {ctx.author.mention}, you already claimed your daily! Come back tomorrow.")
            return

        base_reward = random.randint(40, 80)
        bonus = 0
        bonus_msg = ""
        
        # 5% chance for Crate Bonus
        if random.random() < 0.05:
            bonus = random.randint(100, 300)
            bonus_msg = f"\n🎉 **CRATE BONUS!** You found an extra **{bonus} JC**!"
            
        # 1% chance for ultra-rare flavor
        if random.random() < 0.01:
            bonus_msg += "\n🍀 **LUCKY DAY!** The universe smiles upon you today. Go do something great!"
            
        total = base_reward + bonus
        new_bal = add_balance(uid, total)
        set_last_daily(uid, today)
        log_transaction(uid, total, "Daily Crate")

        embed = discord.Embed(
            title="🎁 Daily Crate Opened!",
            description=f"💵 {ctx.author.mention} received **{base_reward:,}** JC!{bonus_msg}",
            color=discord.Color.green()
        )
        embed.add_field(name="New Balance", value=f"**{new_bal:,}** JC", inline=False)
        await ctx.send(embed=embed)

    @commands.command(name='work', aliases=['job'])
    async def work_command(self, ctx: commands.Context):
        """Work for some JC! (1 hour CD, reduced by Pickaxe)"""
        uid = str(ctx.author.id)
        now = int(time.time())
        last_str = get_last_work(uid)
        
        # Check tool stats
        pick = get_best_pickaxe(uid)
        actual_cooldown = WORK_COOLDOWN - pick["cooldown_reduction"]
        
        if last_str:
            try:
                last_ts = int(float(last_str))
                diff = now - last_ts
                if diff < actual_cooldown:
                    remaining = int(actual_cooldown - diff)
                    mins = remaining // 60
                    secs = remaining % 60
                    await ctx.send(f"⏳ {ctx.author.mention}, you're exhausted! Come back in **{mins}m {secs}s**.")
                    return
            except ValueError:
                pass

        reward = random.randint(WORK_MIN, WORK_MAX)
        
        # 1. Apply Flat Pickaxe Bonus
        bonus_val = pick["bonus"]
        reward += bonus_val
        
        # 2. Check Overtime
        stats = get_user_stats(uid)
        is_overtime = False
        if stats["overtime_active"] == 1:
            reward *= 2
            is_overtime = True
            update_user_stats(uid, overtime_active=0)

        # 3. Apply Taxes & Perks
        if reward < 100:
            base_fee = 0.05
        elif reward <= 300:
            base_fee = 0.08
        else:
            base_fee = 0.12
            
        if is_vip(uid):
            base_fee = max(0.02, base_fee - 0.03)
            
        fee_rate = max(0.0, base_fee - pick["tax_reduction"])
        
        tax_dodged = False
        if pick["tax_dodge"] > 0 and random.random() < pick["tax_dodge"]:
            tax = 0
            tax_dodged = True
        else:
            tax = max(1, int(reward * fee_rate))
            
        net_reward = reward - tax
        
        # 4. Process Payouts
        new_bal = add_balance(uid, net_reward)
        set_last_work(uid, str(now))
        track_fee(tax)
        
        log_msg = "Work Reward"
        if is_overtime: log_msg += " (Overtime)"
        log_transaction(uid, net_reward, log_msg)
        log_transaction(uid, -tax, "Work Tax", processed=1)
        
        # 5. Coin Shard (Iron Pickaxe Perk)
        shard_msg = ""
        if pick["name"] == "Iron Pickaxe" and random.random() < 0.05:
            shard_reward = 25
            new_bal = add_balance(uid, shard_reward)
            log_transaction(uid, shard_reward, "Found Coin Shard")
            shard_msg = f"\n💎 **Coin Shard Found!** (+{shard_reward} JC added to wallet)"

        jobs = [
            "cleaned the server kitchen", "coded a new feature", "moderated a spicy channel",
            "organized the bot's database", "helped a new member", "fixed a bunch of bugs",
            "wrote some elegant documentation", "designed a new logo", "streamed for 2 hours"
        ]
        job = random.choice(jobs)

        embed = discord.Embed(
            title="⚒️ Hard Work Pays Off!",
            description=f"{ctx.author.mention}, you **{job}** and earned **{reward:,}** JC!",
            color=discord.Color.blue()
        )
        
        msg_details = []
        if bonus_val > 0:
            msg_details.append(f"✨ Includes **+{bonus_val} JC** flat bonus from **{pick['name']}**.")
        if is_overtime:
            msg_details.append(f"🔥 **OVERTIME ACTIVE!** Base yield and bonus were **DOUBLED!**")
        if shard_msg:
            msg_details.append(shard_msg)
            
        if msg_details:
            embed.description += "\n\n" + "\n".join(msg_details)

        tax_percent = int(fee_rate * 100)
        if tax_dodged:
            embed.add_field(name="Income Tax", value=f"~~{max(1, int(reward * fee_rate))} JC~~ (**DODGED!**)", inline=True)
        else:
            embed.add_field(name="Income Tax", value=f"**{tax}** JC ({tax_percent}%)", inline=True)
            
        embed.add_field(name="Net Received", value=f"**{net_reward:,}** JC", inline=True)
        embed.add_field(name="New Wallet", value=f"**{new_bal:,}** JC", inline=False)
        if not tax_dodged:
            embed.set_footer(text="Tax collected is added to the global fee vault!")
        await ctx.send(embed=embed)
        
    @commands.command(name='scavenge', aliases=['search'])
    async def scavenge_command(self, ctx: commands.Context):
        """Scavenge for a few JC! (8 min CD, 50 JC daily limit)"""
        uid = str(ctx.author.id)
        now = int(time.time())
        stats = get_user_stats(uid)
        
        # 1. Cooldown Check (8 minutes = 480 seconds)
        last_scavenge = stats["last_scavenge"]
        cooldown = 480
        if now - last_scavenge < cooldown:
            remaining = cooldown - (now - last_scavenge)
            mins = remaining // 60
            secs = remaining % 60
            await ctx.send(f"⏳ {ctx.author.mention}, you just scavenged! Try again in **{mins}m {secs}s**.")
            return
            
        # 2. Daily Limit Check (Midnight GMT+8 Reset)
        now_gmt8 = datetime.now(timezone(timedelta(hours=8)))
        today_start = int(now_gmt8.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        
        if stats["scavenge_last_reset"] < today_start:
            stats["scavenge_daily_total"] = 0
            stats["scavenge_last_reset"] = now
            update_user_stats(uid, scavenge_daily_total=0, scavenge_last_reset=now)
            
        daily_limit = 50
        if stats["scavenge_daily_total"] >= daily_limit:
            # Calculate time until next midnight GMT+8
            next_midnight = today_start + 86400
            rem = next_midnight - now
            hrs = max(0, rem // 3600)
            mins = max(0, (rem % 3600) // 60)
            await ctx.send(f"🛑 {ctx.author.mention}, you've hit your daily scavenging limit (**{daily_limit} JC**)! Reset in **{hrs}h {mins}m**.")
            return
            
        # 3. Yield Logic (1-4 JC)
        reward = random.randint(1, 4)
        
        # Adjust reward if it would exceed the limit
        if stats["scavenge_daily_total"] + reward > daily_limit:
            reward = daily_limit - stats["scavenge_daily_total"]
            
        if reward <= 0: # Should not happen with above checks but stay safe
            await ctx.send(f"🛑 {ctx.author.mention}, you've hit your daily scavenging limit!")
            return

        # 4. Process Payout
        new_bal = add_balance(uid, reward)
        new_daily_total = stats["scavenge_daily_total"] + reward
        update_user_stats(uid, last_scavenge=now, scavenge_daily_total=new_daily_total)
        
        log_transaction(uid, reward, "Scavenge Reward")
        
        scavenge_locs = [
            "the back of an old sofa", "under a vending machine", "in a dusty corner of the server",
            "between the keys of a mechanical keyboard", "in an old digital wallet", "inside a discarded lootbox"
        ]
        loc = random.choice(scavenge_locs)
        
        embed = discord.Embed(
            title="🔍 Scavenging Success!",
            description=f"{ctx.author.mention}, you searched **{loc}** and found **{reward}** JC!",
            color=discord.Color.light_grey()
        )
        embed.add_field(name="Daily Progress", value=f"**{new_daily_total} / {daily_limit}** JC", inline=True)
        embed.add_field(name="Current Wallet", value=f"**{new_bal:,}** JC", inline=True)
        embed.set_footer(text="Keep searching to find more spare change!")
        await ctx.send(embed=embed)

    @commands.command(name='overtime')
    async def overtime_command(self, ctx: commands.Context):
        """Prepare to work OVERTIME for double pay! (Requires Diamond Pickaxe+)"""
        uid = str(ctx.author.id)
        pick = get_best_pickaxe(uid)
        max_uses = pick["overtime_max"]
        
        if max_uses <= 0:
            await ctx.send(f"❌ You need at least a **Diamond Pickaxe** to work `!overtime`! Check the `!shop`.")
            return
            
        stats = get_user_stats(uid)
        now = int(time.time())
        
        # Check daily reset (Midnight GMT+8)
        now_gmt8 = datetime.now(timezone(timedelta(hours=8)))
        today_start = int(now_gmt8.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        
        if stats["overtime_last_reset"] < today_start:
            stats["overtime_uses"] = 0
            stats["overtime_last_reset"] = now
            update_user_stats(uid, overtime_uses=0, overtime_last_reset=now)
            
        if stats["overtime_uses"] >= max_uses:
            next_midnight = today_start + 86400
            rem = next_midnight - now
            hrs = max(0, rem // 3600)
            mins = max(0, (rem % 3600) // 60)
            await ctx.send(f"⏳ {ctx.author.mention}, you've used all your overtime for today (**{max_uses}** shifts)! Next reset in **{hrs}h {mins}m**.")
            return
            
        if stats["overtime_active"] == 1:
            await ctx.send("🔥 You are already geared up for Overtime! Use `!work` to consume the charge.")
            return
            
        # Activate it
        uses = stats["overtime_uses"] + 1
        update_user_stats(uid, overtime_active=1, overtime_uses=uses)
        
        await ctx.send(f"🔥 **OVERTIME ACTIVATED!** ({uses}/{max_uses} uses today).\n{ctx.author.mention}, your VERY NEXT `!work` will yield **DOUBLE** JC!")

    @commands.command(name='give', aliases=['pay', 'transfer'])
    async def give_command(self, ctx: commands.Context, member: discord.Member = None, amount: int = None):
        """Give JC to another user."""
        if not member or amount is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}give @user [amount]`")
            return
        if member.id == ctx.author.id:
            await ctx.send("You can't give coins to yourself!")
        if member.bot:
            await ctx.send("You can't give coins to a bot!")
            return
        if amount <= 0:
            await ctx.send("Amount must be positive!")
            return

        sender_bal = get_balance(str(ctx.author.id))
        if sender_bal < amount:
            await ctx.send(f"❌ You only have **{sender_bal:,}** JC.")
            return

        tax = int(amount * 0.05)
        net_amount = amount - tax
        
        track_fee(tax)
        log_transaction(str(ctx.author.id), -amount, f"Transfer to {member.display_name}")
        log_transaction(str(member.id), net_amount, f"Transfer from {ctx.author.display_name}")
        log_transaction(str(ctx.author.id), -tax, "Transfer Fee", processed=1)

        embed = discord.Embed(
            title="💸 Transfer Complete",
            description=f"{ctx.author.mention} → {member.mention}\n**{amount:,}** JC sent.",
            color=discord.Color.blue()
        )
        embed.add_field(name="Laundering Fee", value=f"**{tax:,}** JC (Burned 🔥)", inline=True)
        embed.add_field(name="Net Received", value=f"**{net_amount:,}** JC", inline=True)
        embed.add_field(name=f"{member.display_name}'s Balance", value=f"**{new_receiver:,}**", inline=False)
        await ctx.send(embed=embed)

    # --- Live Gold Trading ---

    @commands.command(name='portfolio', aliases=['pf'])
    async def portfolio_command(self, ctx: commands.Context, member: discord.Member = None):
        """View your Investment Portfolio."""
        target = member or ctx.author
        uid = str(target.id)
        
        # Apply storage fees if applicable
        fee_msg = apply_gold_fees(uid)
        if fee_msg: await ctx.send(f"{target.mention}, {fee_msg}")
        
        wallet = get_balance(uid)
        bank = get_bank(uid)
        limit = get_bank_limit(uid)
        limit_str = f"{limit:,}" if limit != float('inf') else "Unlimited"
        gold_grams = get_gold_grams(uid)
        vip_active = is_vip(uid)
        
        # Get Inventory Collectibles
        inv_items = get_inventory(uid) or [] # returns list of (name, type, data)
        collectibles = {}
        for name, itype, idata in inv_items:
            if itype in ["Collectible", "Perk"]:
                collectibles[name] = collectibles.get(name, 0) + 1
        
        coll_str = "None"
        if collectibles:
            coll_str = "\n".join([f"• {name} (x{count})" for name, count in collectibles.items()])
        
        embed = discord.Embed(title=f"📊 {target.display_name}'s Portfolio", color=discord.Color.dark_gold())
        embed.set_thumbnail(url=target.display_avatar.url)
        
        vip_status = "❌ None"
        if vip_active:
            expiry = get_vip_expiry(uid)
            vip_status = f"✅ Active (Expires <t:{expiry}:R>)"
        
        embed.add_field(name="VIP Membership 👑", value=vip_status, inline=False)
        embed.add_field(name="Balances 💵🏦", value=f"Wallet: **{wallet:,}** JC\nBank: **{bank:,}** / {limit_str} JC", inline=False)
        embed.add_field(name="Collectibles 🎒✨", value=coll_str, inline=False)
        
        base_net_worth = wallet + bank
        
        if gold_grams > 0:
            msg = await ctx.send("Fetching live market data...")
            live_price = await fetch_live_gold_price(self.bot)
            
            if live_price:
                gold_value = int(gold_grams * live_price)
                net_worth = base_net_worth + gold_value
                
                embed.add_field(name="Gold Holdings 🥇", value=f"Weight: **{gold_grams:.4f}g**\nLive Value: **{gold_value:,}** JC", inline=False)
                embed.add_field(name="Total Net Worth", value=f"**{net_worth:,}** JC", inline=False)
                embed.set_footer(text=f"Live Gold Rate: {live_price:,.2f} JC/g")
                await msg.edit(content=None, embed=embed)
            else:
                embed.add_field(name="Gold Holdings 🥇", value=f"Weight: **{gold_grams:.4f}g**\nLive Value: `API Offline`", inline=False)
                embed.add_field(name="Net Worth (JC Only)", value=f"**{base_net_worth:,}** JC", inline=False)
                await msg.edit(content=None, embed=embed)
        else:
            embed.add_field(name="Gold Holdings 🥇", value="0.0000g (*No investments yet*)", inline=False)
            embed.add_field(name="Total Net Worth", value=f"**{base_net_worth:,}** JC", inline=False)
            await ctx.send(embed=embed)

    @commands.command(name='buygold', aliases=['bg'])
    async def buygold_command(self, ctx: commands.Context, amount: str = None):
        """Buy virtual Gold at the live market rate. (5% Fee) Usage: !buygold [JC amount | max]"""
        uid = str(ctx.author.id)
        
        # Apply storage fees if applicable
        fee_msg = apply_gold_fees(uid)
        if fee_msg: await ctx.send(f"{ctx.author.mention}, {fee_msg}")
        jc_amount, err = await validate_bet(ctx, amount)
        if err:
            await ctx.send(err)
            return
            
        msg = await ctx.send("<a:loading:111> Fetching live gold exchange rate...")
        live_price = await fetch_live_gold_price(self.bot)
        
        if not live_price:
            await msg.edit(content="❌ The Gold Market is currently closed. Please try again later.")
            return
            
        # RE-CHECK balance after the await to prevent double-spend race conditions
        success, pay_msg = pay_jc(uid, jc_amount)
        if not success:
            await msg.edit(content=f"❌ Transaction failed. {pay_msg}")
            return
            
        fee_rate = 0.02 if is_vip(uid) else 0.05
        fee = max(1, int(jc_amount * fee_rate)) 
        purchase_power = jc_amount - fee
        
        grams_bought = purchase_power / live_price
        
        add_gold_grams(uid, grams_bought)
        track_fee(fee)
        log_transaction(uid, -(jc_amount - fee), "Bought Gold")
        log_transaction(uid, -fee, "Gold Purchase Fee", processed=1)
        
        embed = discord.Embed(title="🏦 Gold Purchase Receipt", color=discord.Color.green())
        embed.add_field(name="Spent", value=f"**{jc_amount:,}** JC\n*(Includes **{fee:,}** JC fee)*", inline=True)
        embed.add_field(name="Acquired", value=f"**{grams_bought:.4f}g** Gold", inline=True)
        embed.add_field(name="Payment", value=pay_msg, inline=False)
        fee_percent = int(fee_rate * 100)
        embed.add_field(name="Execution Price", value=f"{live_price:,.2f} JC/g ({fee_percent}% Fee)", inline=False)
        embed.set_footer(text="Trade executed successfully at market price.")
        
        await msg.edit(content=None, embed=embed)

    @commands.command(name='buyvip', aliases=['vip'])
    async def buy_vip_command(self, ctx: commands.Context):
        """Purchase 30 days of VIP Membership for 10,000 JC."""
        uid = str(ctx.author.id)
        cost = 10000
        bal = get_balance(uid)
        
        if bal < cost:
            await ctx.send(f"❌ VIP Membership costs **{cost:,}** JC. You only have **{bal:,}** JC.")
            return
            
        success, pay_msg = pay_jc(uid, cost)
        set_vip(uid, 30)
        log_transaction(uid, -cost, "Purchased VIP")
        
        expiry = get_vip_expiry(uid)
        embed = discord.Embed(
            title="👑 VIP Membership Activated!",
            description=f"Congratulations {ctx.author.mention}! Your VIP status is now active.\n\n"
                        f"✨ **Payment:** {pay_msg}\n\n"
                        f"✨ **Exclusive Perks:**\n"
                        f"- 🪙 **Market Discount:** Gold fees reduced from **5%** to **2%**.\n"
                        f"- ⚒️ **Tax Haven:** Work taxes reduced from **5%** to **2%**.\n"
                        f"- 🥷 **Low Bail:** Failed robbery fines reduced from **10%** to **5%**.\n"
                        f"- 📉 **Vault Access:** Weekly Gold storage fees reduced from **10%** to **8%**.\n"
                        f"- 🛡️ **Elusive:** You are **10% harder to rob** than normal players.\n\n"
                        f"📅 **Expiry:** <t:{expiry}:F> (<t:{expiry}:R>)",
            color=discord.Color.purple()
        )
        await ctx.send(embed=embed)

    @commands.command(name='sellgold', aliases=['sg'])
    async def sellgold_command(self, ctx: commands.Context, grams_to_sell: str = None):
        """Sell your Gold at the live market rate. (5% Fee) Usage: !sellgold [grams | max]"""
        uid = str(ctx.author.id)
        
        # Apply storage fees if applicable
        fee_msg = apply_gold_fees(uid)
        if fee_msg: await ctx.send(f"{ctx.author.mention}, {fee_msg}")
        current_grams = get_gold_grams(uid)
        
        if current_grams <= 0:
            await ctx.send("❌ You don't own any gold to sell! `!buygold` first.")
            return
            
        if not grams_to_sell:
            await ctx.send(f"❌ How much? You have **{current_grams:.4f}g**. Usage: `!sellgold [amount | max]`")
            return
            
        sell_amount = 0.0
        s = str(grams_to_sell).lower()
        if s in ['max', 'all']:
            sell_amount = current_grams
        else:
            try:
                sell_amount = float(s)
            except ValueError:
                await ctx.send("❌ Invalid amount! Use a number or 'max'.")
                return
                
        if sell_amount <= 0 or sell_amount > current_grams:
            await ctx.send(f"❌ Invalid amount. You own exactly **{current_grams:.4f}g**.")
            return

        msg = await ctx.send("<a:loading:111> Fetching live gold exchange rate...")
        live_price = await fetch_live_gold_price(self.bot)
        
        if not live_price:
            await msg.edit(content="❌ The Gold Market is currently closed. Please try again later.")
            return
            
        # RE-CHECK gold balance to prevent double-sell race conditions
        current_grams_now = get_gold_grams(uid)
        if current_grams_now < sell_amount:
            await msg.edit(content=f"❌ Transaction failed. You no longer have **{sell_amount:.4f}g** to sell (Current: {current_grams_now:.4f}g).")
            return
            
        gross_value = int(sell_amount * live_price)
        fee_rate = 0.02 if is_vip(uid) else 0.05
        fee = max(1, int(gross_value * fee_rate)) 
        net_payout = gross_value - fee
        
        # Deduct REAL amount safely
        new_gold_bal = current_grams_now - sell_amount
        db_query("UPDATE investments SET gold_grams = ? WHERE user_id = ?", (new_gold_bal, uid), commit=True)
        
        add_balance(uid, net_payout)
        track_fee(fee)
        log_transaction(uid, net_payout, "Sold Gold")
        log_transaction(uid, -fee, "Gold Sale Fee", processed=1)
        
        embed = discord.Embed(title="🏦 Gold Sale Receipt", color=discord.Color.green())
        embed.add_field(name="Sold", value=f"**{sell_amount:.4f}g** Gold", inline=True)
        embed.add_field(name="Received", value=f"**{net_payout:,}** JC\n*(After **{fee:,}** JC fee)*", inline=True)
        fee_percent = int(fee_rate * 100)
        embed.add_field(name="Execution Price", value=f"{live_price:,.2f} JC/g ({fee_percent}% Fee)", inline=False)
        embed.set_footer(text="Trade executed successfully at market price.")
        
        await msg.edit(content=None, embed=embed)

    @commands.command(name='vault', aliases=['fees'])
    async def vault_command(self, ctx: commands.Context):
        """View the global fee vault balances."""
        jc_vault = int(get_setting("fee_vault", "0"))
        gold_vault = float(get_setting("gold_fee_vault", "0.0"))
        
        embed = discord.Embed(
            title="🏦 Global Fee Vault",
            description="All taxes and fines are collected here for community events!",
            color=discord.Color.blue()
        )
        embed.add_field(name="💰 JC Vault", value=f"**{jc_vault:,}** JC", inline=True)
        embed.add_field(name="✨ Gold Vault", value=f"**{gold_vault:.3f}g**", inline=True)
        embed.set_footer(text="Recycling JC and Gold into community rewards!")
        await ctx.send(embed=embed)


    @commands.command(name='top', aliases=['rich', 'jclb', 'jcleaderboard'])
    async def top_command(self, ctx: commands.Context):
        """Show the Top 10 users by Total Net Worth (JC + Gold Value)."""
        msg = await ctx.send("<a:loading:111> Calculating global wealth rankings...")
        
        live_price = await fetch_live_gold_price(self.bot)
        rows = get_top_balances(50) # Helper now returns (uid, bal, bank, gold)
        
        if not rows:
            await msg.edit(content="📭 No one has any JC yet! Use `!daily` to get started.")
            return

        # Calculate Net Worth in Python
        leaderboard_data = []
        for uid, bal, bank, gold in rows:
            jc_total = bal + bank
            gold_val = int(gold * live_price) if live_price else 0
            net_worth = jc_total + gold_val
            leaderboard_data.append({
                "uid": uid,
                "net_worth": net_worth,
                "jc": jc_total,
                "gold": gold
            })
            
        # Sort by Net Worth
        leaderboard_data.sort(key=lambda x: x['net_worth'], reverse=True)
        top_10 = leaderboard_data[:10]

        embed = discord.Embed(
            title="🏦 Global Wealth Leaderboard", 
            description="Ranked by **Total Net Worth** (Wallet + Bank + Gold Value)",
            color=discord.Color.gold()
        )
        
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, data in enumerate(top_10):
            medal = medals[i] if i < 3 else f"`{i+1}.`"
            try:
                user = await self.bot.fetch_user(int(data["uid"]))
                name = user.display_name
            except Exception:
                name = f"User {data['uid']}"
            
            gold_str = f" + {data['gold']:.2f}g Gold" if data['gold'] > 0 else ""
            lines.append(f"{medal} **{name}** — **{data['net_worth']:,}** JC\n   *( {data['jc']:,} {gold_str} )*")
            
        embed.description += "\n\n" + "\n".join(lines)
        if live_price:
            embed.set_footer(text=f"Live Gold Rate: {live_price:,.2f} JC/g | Net worth updated instantly.")
        else:
            embed.set_footer(text="Market stats offline. Sorting by JC only.")
            
        await msg.edit(content=None, embed=embed)

    # --- Gambling ---

    @commands.command(name='flip', aliases=['coinflip'])
    async def flip_command(self, ctx: commands.Context, amount: str = None, side: str = None):
        """Flip a coin! Guess 'h' or 't'. Win = double, Lose = nothing."""
        val, err = await validate_bet(ctx, amount)
        if err:
            await ctx.send(err)
            return
        amount = val
        if side is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}flip [amount] [h/t]` — bet your JC on heads or tails!")
            return

        side = side.lower()
        if side not in ['h', 'heads', 't', 'tails']:
            await ctx.send("Please pick `h` (heads) or `t` (tails)!")
            return

        uid = str(ctx.author.id)
        
        # Lucky Charm Bonus (+5% odds)
        luck_bonus = get_luck_bonus(uid)
        win_chance = 0.50 + luck_bonus
        won = random.random() < win_chance
        
        user_choice = 'h' if side in ['h', 'heads'] else 't'
        outcome = user_choice if won else ("t" if user_choice == "h" else "h")
        won = (user_choice == outcome)
        outcome_full = "Heads" if outcome == 'h' else "Tails"

        # Auto-deduct payment
        _, pay_msg = pay_jc(uid, amount)

        if won:
            winnings = int(amount * 1.9)
            new_bal = add_balance(uid, winnings)
            log_transaction(uid, winnings, "Flip Win")
            color = discord.Color.green()
            msg = f"🎉 You guessed right!\nYou won **{amount:,}** JC!"
        else:
            new_bal = get_balance(uid)
            track_fee(amount)
            log_transaction(uid, -amount, "Flip Loss", processed=1)
            color = discord.Color.red()
            msg = f"😢 You guessed wrong.\nYou lost **{amount:,}** JC."

        embed = discord.Embed(title=f"🪙 Coin Flip — {outcome_full}!", description=msg, color=color)
        embed.add_field(name="Current Wallet", value=f"**{new_bal:,}** JC", inline=True)
        embed.add_field(name="Payment", value=pay_msg, inline=True)
        embed.set_footer(text=f"Bet: {amount:,} JC | Picked: {side}")
        await ctx.send(embed=embed)

    @commands.command(name='slots', aliases=['slot'])
    async def slots_command(self, ctx: commands.Context, amount: str = None):
        """Spin the slot machine! 🎰"""
        val, err = await validate_bet(ctx, amount)
        if err:
            await ctx.send(err)
            return
        amount = val
        uid = str(ctx.author.id)

        # Lucky Charm Bonus
        luck_bonus = get_luck_bonus(uid)
        
        # Reels simulation
        if luck_bonus > 0 and random.random() < 0.05: # 5% chance to nudge ONE reel
            base = random.choice(SLOT_EMOJIS)
            reels = [base, base, random.choice(SLOT_EMOJIS)]  # 2-match nudge only
            random.shuffle(reels)
        else:
            reels = [random.choice(SLOT_EMOJIS) for _ in range(3)]
            
        reel_display = " | ".join(reels)

        # Auto-deduct payment
        _, pay_msg = pay_jc(uid, amount)

        if reels[0] == reels[1] == reels[2]:
            multiplier = SLOT_PAYOUTS.get(reels[0], 2)
            winnings = amount * multiplier
            new_bal = add_balance(uid, winnings)
            log_transaction(uid, winnings, f"Slots Win ({reels[0]})")
            title = "🎰 JACKPOT!!! 🎰" if reels[0] == "7️⃣" else "🎰 THREE OF A KIND!"
            desc = f"**[ {reel_display} ]**\n\n🎉 You won **{winnings:,}** JC! (x{multiplier})"
            color = discord.Color.gold()
        elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
            new_bal = add_balance(uid, amount)  # Bet back
            log_transaction(uid, amount, "Slots Draw")
            title = "🎰 Two of a Kind"
            desc = f"**[ {reel_display} ]**\n\n😌 Two match! You got your bet back."
            color = discord.Color.blue()
        else:
            new_bal = get_balance(uid)
            track_fee(amount) # Added track_fee for losses
            log_transaction(uid, -amount, "Slots Loss")
            title = "🎰 No Match"
            desc = f"**[ {reel_display} ]**\n\n💨 No luck this time. You lost **{amount:,}** JC."
            color = discord.Color.red()

        embed = discord.Embed(title=title, description=desc, color=color)
        embed.add_field(name="Current Wallet", value=f"**{new_bal:,}** JC", inline=True)
        embed.add_field(name="Payment", value=pay_msg, inline=True)
        embed.set_footer(text=f"Bet: {amount:,} JC")
        await ctx.send(embed=embed)

    @commands.command(name='duel', aliases=['challenge'])
    async def duel_command(self, ctx: commands.Context, member: discord.Member = None, amount: str = None):
        """Challenge another user to a PVP Coin Flip!"""
        if not member or amount is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}duel @user [amount]`")
            return
        if member.id == ctx.author.id:
            await ctx.send("You can't duel yourself!")
            return
        if member.bot:
            await ctx.send("Bots won't duel you!")
            return

        val, err = await validate_bet(ctx, amount)
        if err:
            await ctx.send(err)
            return
        amount = val

        # Check if receiver can afford it
        if get_balance(str(member.id)) < amount:
            await ctx.send(f"❌ {member.display_name} doesn't have enough JC to accept a **{amount:,} JC** duel!")
            return

        # Take P1's bet upfront
        uid = str(ctx.author.id)
        _, pay_msg = pay_jc(uid, amount)

        view = DuelView(ctx, member, amount, pay_msg)
        embed = discord.Embed(
            title="⚔️ Duel Challenge!",
            description=f"{ctx.author.mention} has challenged {member.mention} to a **{amount:,} JC** coin flip!\n\n**Winner takes the pot (minus 5% fee)!**",
            color=discord.Color.orange()
        )
        embed.set_footer(text="Challenge expires in 60 seconds.")
        view.message = await ctx.send(content=member.mention, embed=embed, view=view)


    @commands.command(name='crash')
    async def crash_command(self, ctx: commands.Context, amount: str = None):
        """Bet JC and cash out before the rocket crashes! 🚀"""
        if amount is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}crash [amount]`")
            return

        val, err = await validate_bet(ctx, amount)
        if err:
            await ctx.send(err)
            return
        amount = val
        uid = str(ctx.author.id)
        # --- JC Sink Logic: VIP Perks ---
        is_user_vip = is_vip(uid)
        entry_rate = 0.10 if is_user_vip else 0.15
        entry_fee = int(amount * entry_rate)
        active_bet = amount - entry_fee
        
        # Deduct TOTAL bet upfront
        _, pay_msg = pay_jc(uid, amount)
        track_fee(entry_fee)
        log_transaction(uid, -amount, f"Crash Game (Fee: {entry_fee} JC)")

        view = CrashView(ctx, active_bet, amount, is_user_vip) # Pass VIP status
        embed = discord.Embed(
            title="🚀 Preparing for Takeoff...",
            description=(
                f"Multiplier: **1.00x**\n"
                f"Potential Win: **{active_bet:,}** JC\n\n"
                f"💰 **Entry Fee**: `{entry_fee:,} JC` {'⭐ (VIP)' if is_user_vip else ''}\n"
                f"🛡️ **Active Bet**: `{active_bet:,} JC`"
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Total Bet: {amount:,} JC | Sink Rate: {int(entry_rate*100)}%")
        
        view.message = await ctx.send(embed=embed, view=view)
        # Start the game loop
        asyncio.create_task(view.run_game())

    @commands.command(name='rob', aliases=['steal', 'stolen'])
    async def rob_command(self, ctx: commands.Context, member: discord.Member = None):
        """Try to rob another user's JC! (20 minute cooldown)"""
        if not member:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}rob @user`")
            return
        
        if member.id == ctx.author.id:
            await ctx.send("You can't rob yourself, silly!")
            return
            
        if member.bot:
            await ctx.send("Bots don't carry any coins!")
            return

        uid = str(ctx.author.id)
        vid = str(member.id)
        
        # Cooldown Check
        now = int(time.time())
        cooldown = 20 * 60 # 20 minutes
        last_str = get_last_rob(uid)
        if last_str:
            try:
                last_ts = int(float(last_str))
                diff = now - last_ts
                if diff < cooldown:
                    rem = cooldown - diff
                    await ctx.send(f"⏳ {ctx.author.mention}, you're still lying low! Try again in **{rem//60}m {rem%60}s**.")
                    return
            except ValueError: pass

        t_bal = get_balance(uid)
        v_bal = get_balance(vid)
        
        if t_bal < 500:
            await ctx.send("❌ You need at least **500 JC** to risk a robbery!")
            return
        if v_bal < 200:
            await ctx.send(f"❌ {member.display_name} is too poor to be worth robbing!")
            return

        # --- SHIELD CHECK ---
        has_shield = get_inventory_item(vid, "Vault Shield")
        
        # Luck & VIP Logic
        success_rate = 0.40
        if is_vip(vid): success_rate -= 0.10 # Harder to rob VIPs
        
        # Sticky Gloves Bonus (+5%)
        gloves_active = False
        if get_inventory_item(uid, "Sticky Gloves"):
            success_rate += 0.05
            remove_item(uid, "Sticky Gloves")
            gloves_active = True

        # Result Calculation
        success_roll = random.random() < success_rate
        set_last_rob(uid, now)
        
        # --- GOLD THEFT CHECK (50% Chance, Bypasses JC Shield) ---
        gold_stolen = 0
        gold_msg = ""
        if success_roll and random.random() < 0.50:
            v_gold = get_gold_grams(vid)
            if v_gold > 0.001:
                gold_percent = random.uniform(0.20, 0.30)
                gold_stolen = int(v_gold * gold_percent * 100) / 100.0
                add_gold_grams(vid, -gold_stolen)
                add_gold_grams(uid, gold_stolen)
                log_transaction(uid, 0, f"Stole {gold_stolen}g Gold from {member.display_name}")
                log_transaction(vid, 0, f"Gold stolen by {ctx.author.display_name}: {gold_stolen}g")
                gold_msg = f"\n🔥 **BONUS**: You also made off with **{gold_stolen}g** of Gold!"

        if success_roll:
            # JC Robbery success (could be blocked by shield)
            if has_shield:
                remove_item(vid, "Vault Shield")
                embed = discord.Embed(title="🛡️ Robbery Blocked!", color=discord.Color.orange())
                msg = f"{member.mention}'s **Vault Shield** blocked your attempt to steal their JC!"
                if gold_stolen > 0:
                    msg += f"\n\n...But the shield didn't protect their Gold! {gold_msg}"
                embed.description = msg
                embed.set_footer(text="The shield was consumed in the struggle.")
                await ctx.send(embed=embed)
                return

            # Standard JC theft
            percent = random.uniform(0.10, 0.25)
            stolen = int(v_bal * percent)
            
            # Laundering Fee (5%)
            tax = int(stolen * 0.05)
            net_gain = stolen - tax
            track_fee(tax)
            
            add_balance(vid, -stolen)
            add_balance(uid, net_gain)
            log_transaction(uid, net_gain, f"Robbed {member.display_name}")
            log_transaction(vid, -stolen, f"Robbed by {ctx.author.display_name}")
            
            embed = discord.Embed(title="🥷 Successful Robbery!", color=discord.Color.green())
            embed.description = f"You managed to snatch **{stolen:,}** JC from {member.mention}!{gold_msg}"
            embed.add_field(name="Net Gain", value=f"**{net_gain:,}** JC", inline=True)
            embed.add_field(name="Laundering Fee", value=f"**{tax:,}** JC (Burned)", inline=True)
            if gold_stolen > 0:
                embed.add_field(name="Gold Looted", value=f"**{gold_stolen}g**", inline=True)
            embed.set_footer(text="Crime pays... for now.")
            await ctx.send(embed=embed)
        else:
            # Penalty: 15% of thief's wallet
            penalty_rate = 0.15
            if is_vip(uid): penalty_rate = 0.08 # VIPs pay reduced penalty
            
            fine = int(t_bal * penalty_rate)
            
            # Legal Fees (2%)
            legal_fee = int(fine * 0.02)
            restitution = fine - legal_fee
            
            success, pay_msg = pay_jc(uid, fine)
            add_balance(vid, restitution)
            track_fee(legal_fee)
            
            # --- GOLD PENALTY (10% of Thief's Gold) ---
            t_gold = get_gold_grams(uid)
            gold_fine_victim = 0
            gold_fine_vault = 0
            gold_msg = ""
            if t_gold > 0.001:
                # 5% to victim, 5% to vault (10% total)
                gold_fine_victim = round(t_gold * 0.05, 3)
                gold_fine_vault = round(t_gold * 0.05, 3)
                
                add_gold_grams(uid, -(gold_fine_victim + gold_fine_vault))
                add_gold_grams(vid, gold_fine_victim)
                track_gold_fee(gold_fine_vault)
                
                log_transaction(uid, 0, f"Robbery Fine: {gold_fine_victim}g to victim, {gold_fine_vault}g to vault")
                log_transaction(vid, 0, f"Restitution: Received {gold_fine_victim}g from failed thief")
                gold_msg = f"\n⚠️ **EXTRA**: You also paid **{gold_fine_victim}g** to {member.display_name} and **{gold_fine_vault}g** in legal fees!"

            # Consolidate failure embed
            if has_shield:
                remove_item(vid, "Vault Shield")
                embed = discord.Embed(title="🛡️ SHIELD ACTIVATED!", color=discord.Color.blue())
                embed.description = (f"{member.mention}'s **Vault Shield** blocked the robbery attempt!\n\n"
                                     f"🚔 {ctx.author.mention} was still caught and forced to pay a fine.{gold_msg}")
            else:
                embed = discord.Embed(title="🚔 CAUGHT IN THE ACT!", color=discord.Color.red())
                embed.description = f"You were spotted trying to rob {member.mention} and forced to pay a fine!{gold_msg}"

            embed.add_field(name="Fine Paid", value=f"**{fine:,}** JC ({pay_msg})", inline=True)
            embed.add_field(name="Victim Restit.", value=f"**{restitution:,}** JC", inline=True)
            embed.add_field(name="Legal Fees", value=f"**{legal_fee:,}** JC", inline=True)
            
            if gold_fine_victim > 0:
                embed.add_field(name="Gold Penalty", value=f"**{gold_fine_victim + gold_fine_vault:.3f}g**", inline=True)
                
            embed.set_footer(text="The law always catches up... eventually.")
            await ctx.send(embed=embed)
            log_transaction(uid, -fine, f"Failed Robbery of {member.display_name}" + (" (Shielded)" if has_shield else ""))
            log_transaction(vid, restitution, f"Compensated for Attempted Robbery")

    @commands.command(name='history', aliases=['logs', 'stats'])
    async def history_command(self, ctx: commands.Context):
        """View your last 5 economy transactions."""
        uid = str(ctx.author.id)
        rows = db_query("SELECT amount, type, timestamp FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 5", (uid,), fetchall=True)

        if not rows:
            await ctx.send("📭 You haven't made any transactions yet!")
            return

        embed = discord.Embed(title=f"📜 {ctx.author.display_name}'s Recent Activity", color=discord.Color.blue())
        history_text = ""
        for amount, trans_type, timestamp in rows:
            sign = "+" if amount > 0 else ""
            fmt_amount = f"{sign}{amount:,}" if amount != 0 else "0"
            try:
                ts_int = int(float(timestamp))
                ts_display = f"<t:{ts_int}:f>"
            except (ValueError, TypeError):
                ts_display = f"`{timestamp}`"
            history_text += f"{ts_display} | **{trans_type}**: `{fmt_amount} JC`\n"

        embed.description = history_text
        bal = get_balance(uid)
        embed.set_footer(text=f"Current Balance: {bal:,} JC")
        await ctx.send(embed=embed)

    # --- Blackjack ---

    @commands.command(name='blackjack', aliases=['bj'])
    async def bj_command(self, ctx: commands.Context, amount: str = None):
        """Play a game of Blackjack! 🃏"""
        val, err = await validate_bet(ctx, amount)
        if err:
            await ctx.send(err)
            return
        amount = val

        view = BlackjackView(ctx, amount)
        await view.start_game()

    # --- JC Rain ---

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot: return
        
        uid = str(message.author.id)
        now = int(time.time())
        
        # --- Mithril Drill Chat Passive ---
        last_time = self.passive_cache.get(uid, 0)
        if now - last_time >= 60:
            pick = get_best_pickaxe(uid)
            if pick["passive_active"]:
                stats = get_user_stats(uid)
                # Check Hourly Reset
                if now - stats["passive_hour_start"] >= 3600:
                    stats["passive_hourly_total"] = 0
                    stats["passive_hour_start"] = now
                
                # Award if under 15 cap
                if stats["passive_hourly_total"] < 15:
                    add_balance(uid, 1)
                    new_total = stats["passive_hourly_total"] + 1
                    update_user_stats(uid, 
                        last_passive_time=now, 
                        passive_hourly_total=new_total,
                        passive_hour_start=stats["passive_hour_start"]
                    )
                    self.passive_cache[uid] = now
                    
        # Dynamic rain rate (default 0.1% if not set)
        rate_str = get_setting('rain_rate', '0.1')
        try:
            rate = float(rate_str) / 100.0
        except ValueError:
            rate = 0.001
            
        ratio = self._get_stability_ratio()
        
        # ADAPTIVE STABILITY SCALE:
        # Higher stability = More aggressive rain to return JC to users
        if ratio < 0.2:     trigger_multiplier = 0.5   # Critical Low (0.05% chance)
        elif ratio < 1.0:   trigger_multiplier = 1.0   # Healthy (0.1% chance)
        elif ratio < 3.0:   trigger_multiplier = 2.5   # Stable (0.25% chance)
        elif ratio < 5.0:   trigger_multiplier = 5.0   # Hyper-Stable (0.5% chance)
        else:               trigger_multiplier = 10.0  # Overloaded (1.0% chance)

        if random.random() < (rate * trigger_multiplier):
            # Vault and Cooldown check for random rain
            vault_bal = int(float(get_setting("fee_vault", "0")))
            now = int(time.time())
            last_rain = int(float(get_setting("last_rain_time", "0")))
            
            if vault_bal >= 500 and (now - last_rain) >= 600:
                set_setting("last_rain_time", str(now))
                await self.start_rain(message.channel, is_random=True)

    @commands.command(name='rain')
    @commands.is_owner()
    async def rain_command(self, ctx: commands.Context):
        """Owner Only: Manually trigger a JC Rain 🌧️"""
        await self.start_rain(ctx.channel)

    @commands.command(name='rainrate')
    @commands.is_owner()
    async def rainrate_command(self, ctx: commands.Context, rate: float = None):
        """Owner Only: Set the percentage chance of random rain (0-100)."""
        if rate is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}rainrate [0-100]`")
            return
        if 0 <= rate <= 100:
            set_setting('rain_rate', str(rate))
            await ctx.send(f"✅ Random rain rate set to **{rate}%**.")
        else:
            await ctx.send("❌ Please provide a rate between 0 and 100.")

    @commands.command(name='rainamount')
    @commands.is_owner()
    async def rainamount_command(self, ctx: commands.Context, min_amt: int = None, max_amt: int = None):
        """Owner Only: Set the min/max JC awarded in a rain catch."""
        if min_amt is None or max_amt is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}rainamount [min] [max]`")
            return
        if 0 < min_amt <= max_amt:
            set_setting('rain_min', str(min_amt))
            set_setting('rain_max', str(max_amt))
            await ctx.send(f"✅ Rain catch range set to **{min_amt:,} - {max_amt:,} JC**.")
        else:
            await ctx.send("❌ Invalid range! Ensure 0 < min <= max.")

    @commands.command(name='raintotal')
    @commands.is_owner()
    async def raintotal_command(self, ctx: commands.Context, total: int = None):
        """Owner Only: Set the total JC pool for a rain event."""
        if total is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}raintotal [amount]`")
            return
        if total > 0:
            set_setting('rain_pool', str(total))
            await ctx.send(f"✅ Total rain pool set to **{total:,} JC**.")
        else:
            await ctx.send("❌ Please provide a positive amount.")

    async def start_rain(self, channel, is_random=False):
        ratio = self._get_stability_ratio()
        if is_random:
            vault_bal = int(float(get_setting("fee_vault", "0")))
            
            # DRAIN SURPLUS: If Stability > 100%, take 20% of vault. Else 10%.
            drain_rate = 0.20 if ratio > 1.0 else 0.10
            pool = int(vault_bal * drain_rate)
            
            # Increase caps for "Mega Rain"
            max_cap = 10000 if ratio > 1.0 else 2000
            pool = max(200, min(max_cap, pool))
            
            set_setting("fee_vault", str(max(0, vault_bal - pool)))
        else:
            # Fetch pool or use default
            try:
                pool = int(get_setting('rain_pool', str(random.randint(300, 800))))
            except (ValueError, TypeError):
                pool = random.randint(300, 800)
            
        view = RainView(pool=pool)
        embed = discord.Embed(
            title="🌧️ IT'S RAINING JC!",
            description=f"A total pool of **{pool:,} JC** is falling! Quick! Click below to catch some!\n\n**Catch 'em before the pool runs dry!**",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url="https://cdn.pixabay.com/animation/2023/03/19/02/45/02-45-20-441_512.gif")
        view.message = await channel.send(embed=embed, view=view)

    # --- Shop & Inventory ---

    @commands.command(name='shop', aliases=['store', 'market'])
    async def shop_command(self, ctx: commands.Context):
        """Browse the JenBot Shop! 🛍️"""
        embed = discord.Embed(
            title="Convenience Store 🎭",
            description="Spend your JC on unique rewards!",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="👑 **VIP Membership** — `10,000 JC`",
            value="30 days of elite perks: **-3% Work Tax** (progressive), **2% Gold fees**, **8% Storage fees**, **5% Robbery fines**, **+10% Robbery defense**, **10% Crash Entry Fee**, and **-3% Crash Profit Tax**.\nUsage: `!buyvip` or `!vip` (for short)",
            inline=False
        )
        embed.add_field(
            name="✨ **Custom Role** — `500,000 JC`",
            value="Create and equip your own custom Discord role!\nUsage: `!buy role` then `!setrole <name> <#hex>`",
            inline=False
        )
        embed.add_field(
            name="🎁 **Mystery Box** — `1,000 JC`",
            value="High stakes! Win coins or rare collectibles.\nUsage: `!buy box [qty]` (Max 10)",
            inline=False
        )
        embed.add_field(
            name="⛏️ **Mining Tool Upgrades**",
            value=(
                "**Stone Pickaxe** — `500 JC` (+10 JC)\n"
                "**Iron Pickaxe** — `1,500 JC` (+20 JC, 5% Shard) • *Req: Stone*\n"
                "**Golden Pickaxe** — `3,500 JC` (+30 JC, -1% Tax) • *Req: Iron*\n"
                "**Diamond Pickaxe** — `8,000 JC` (+45 JC, 1x OT) • *Req: Golden*\n"
                "**Netherite Pickaxe** — `20,000 JC` (+60 JC, 2x OT, 10% Dodge) • *Req: Diamond*\n"
                "**Mithril Drill** — `50,000 JC` (+80 JC, 3x OT, Chat Passive) • *Req: Netherite*"
            ),
            inline=False
        )
        embed.add_field(
            name="🍀 **Lucky Charm** — `2,000 JC`",
            value="Increases gambling win chance by **+5%** for **1 hour**.\nUsage: `!buy charm`",
            inline=True
        )
        embed.add_field(
            name="🧤 **Sticky Gloves** — `5,000 JC`",
            value="Increases robbery success rate by **+5%** for **1 attempt**.\nUsage: `!buy gloves`",
            inline=True
        )
        embed.add_field(
            name="🛡️ **Vault Shield** — `2,000 JC`",
            value="Protects you from **1** robbery attempt (100% block). **Max 3 in inventory!**\nUsage: `!buy shield`",
            inline=True
        )
        embed.add_field(
            name="📦 **Iron Safe** — `20,000 JC`",
            value="Increases your Bank Capacity by **+50,000 JC**.\nUsage: `!buy iron`",
            inline=True
        )
        embed.add_field(
            name="🛡️ **Steel Vault** — `100,000 JC`",
            value="Increases your Bank Capacity by **+250,000 JC**.\nUsage: `!buy steel`",
            inline=True
        )
        embed.set_footer(text=f"Your Balance: {get_balance(str(ctx.author.id)):,} JC")
        await ctx.send(embed=embed)

    @commands.command(name='buy')
    async def buy_command(self, ctx: commands.Context, item_type: str = None, qty: str = None):
        """Buy an item from the shop."""
        if item_type is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}buy [item] [qty]` (e.g. `!buy box 3`)")
            return

        uid = str(ctx.author.id)
        item_type = item_type.lower()
        
        shop = {
            "box": 1000,
            "shield": 2000,
            "role": 500000,
            "iron": 20000,
            "steel": 100000,
            "stone": 500,
            "ironpick": 1500,
            "golden": 3500,
            "diamond": 8000,
            "netherite": 20000,
            "drill": 50000,
            "mithril": 50000
        }
        
        if item_type in ["pickaxe", "stone", "ironpick", "golden", "diamond", "netherite", "drill", "mithril"]:
            # Sequential Logic
            tiers = [
                ("Stone Pickaxe", 500, None),
                ("Iron Pickaxe", 1500, "Stone Pickaxe"),
                ("Golden Pickaxe", 3500, "Iron Pickaxe"),
                ("Diamond Pickaxe", 8000, "Golden Pickaxe"),
                ("Netherite Pickaxe", 20000, "Diamond Pickaxe"),
                ("Mithril Drill", 50000, "Netherite Pickaxe")
            ]
            
            # Find which one they are trying to buy
            target_name = None
            price = 0
            req = None
            
            if item_type in ["pickaxe", "stone"]:
                target_name, price, req = tiers[0]
            elif item_type == "ironpick":
                target_name, price, req = tiers[1]
            elif item_type == "golden":
                target_name, price, req = tiers[2]
            elif item_type == "diamond":
                target_name, price, req = tiers[3]
            elif item_type == "netherite":
                target_name, price, req = tiers[4]
            elif item_type in ["drill", "mithril"]:
                target_name, price, req = tiers[5]
                
            # Check if they already have it or a better one
            current_pick = get_best_pickaxe(uid)
            pickaxe_order = ["Stone Pickaxe", "Iron Pickaxe", "Golden Pickaxe", "Diamond Pickaxe", "Netherite Pickaxe", "Mithril Drill"]
            
            try:
                current_rank = pickaxe_order.index(current_pick["name"]) if current_pick["name"] else -1
            except ValueError:
                current_rank = -1
            target_rank = pickaxe_order.index(target_name)
                
            if current_rank >= target_rank:
                await ctx.send(f"❌ You already have a **{current_pick['name']}** or better!")
                return
                
            # Check requirement
            if req and not get_inventory_item(uid, req):
                await ctx.send(f"❌ You need to own a **{req}** before you can upgrade to a **{target_name}**!")
                return
                
            success, pay_msg = pay_jc(uid, price)
            if not success:
                await ctx.send(pay_msg)
                return
                
            # Remove old tool and add new one
            if req:
                remove_item(uid, req)
            
            add_item(uid, target_name)
            log_transaction(uid, -price, f"Bought {target_name}")
            await ctx.send(f"⛏️ {ctx.author.mention}, you upgraded to a **{target_name}**! {pay_msg}")
            return

        if item_type == 'box':
            # Parse quantity (default 1, max 10)
            count = 1
            if qty:
                try:
                    count = int(qty)
                except ValueError:
                    await ctx.send("❌ Invalid quantity! Use a number like `!buy box 3`.")
                    return
            
            if count < 1 or count > 10:
                await ctx.send("❌ You can buy between **1** and **10** boxes at a time!")
                return
            
            cost_per = 1000
            total_cost = cost_per * count
            
            success, msg_text = pay_jc(uid, total_cost)
            if not success:
                await ctx.send(msg_text)
                return
            
            log_transaction(uid, -total_cost, f"Bought {count}x Mystery Box")
            
            msg = await ctx.send(f"🎁 {ctx.author.mention} is opening **{count}** Mystery Box(es)... ({msg_text})")
            await asyncio.sleep(1.5)
            
            # Open all boxes
            results = []
            total_won = 0
            items_found = []
            best_color = discord.Color.light_grey()
            rarity_order = {"COMMON": 0, "RARE": 1, "EPIC": 2, "LEGENDARY": 3}
            best_rarity_rank = -1
            
            for _ in range(count):
                res = random.random()
                
                # Dynamic loot rates (respects active events)
                rates = get_box_rates()
                rate_leg = rates['legendary']
                rate_epic = rates['epic']
                rate_rare = rates['rare']
                
                # Thresholds
                thresh_leg = rate_leg
                thresh_epic = thresh_leg + rate_epic
                thresh_rare = thresh_epic + rate_rare
                
                if res < thresh_leg:  # Legendary
                    win = 15000
                    item = "🏆 Golden JC"
                    rarity = "LEGENDARY"
                    color = discord.Color.gold()
                elif res < thresh_epic:  # Epic
                    win = 5000
                    item = "🥈 Silver Coin"
                    rarity = "EPIC"
                    color = discord.Color.purple()
                elif res < thresh_rare:  # Rare
                    win = random.randint(1500, 3000)
                    item = None
                    rarity = "RARE"
                    color = discord.Color.blue()
                else:  # Common
                    win = random.randint(200, 500)
                    item = None
                    rarity = "COMMON"
                    color = discord.Color.light_grey()
                
                total_won += win
                add_balance(uid, win)
                log_transaction(uid, win, f"Box Reveal: {rarity}")
                if item:
                    add_item(uid, item)
                    items_found.append(item)
                
                # Track the best rarity for embed color
                rank = rarity_order.get(rarity, 0)
                if rank > best_rarity_rank:
                    best_rarity_rank = rank
                    best_color = color
                
                results.append(f"📦 **{rarity}** — **{win:,}** JC" + (f" + {item}" if item else ""))
            
            # Build summary embed
            net = total_won - total_cost
            net_str = f"+{net:,}" if net >= 0 else f"{net:,}"
            
            embed = discord.Embed(
                title=f"✨ {ctx.author.display_name}'s Mystery Box Results",
                color=best_color
            )
            embed.description = "\n".join(results)
            embed.add_field(name="💰 Total Won", value=f"**{total_won:,}** JC", inline=True)
            embed.add_field(name="💸 Total Spent", value=f"**{total_cost:,}** JC", inline=True)
            embed.add_field(name="📊 Net P/L", value=f"**{net_str}** JC", inline=True)
            if items_found:
                embed.add_field(name="🎁 Items Found", value="\n".join(items_found), inline=False)
            embed.set_footer(text=f"Balance: {get_balance(uid):,} JC")
            
            await msg.edit(content=None, embed=embed)
            
        elif item_type in ['shield', 'vault shield', 'vaultshield']:
            cost = 2000
            
            # Limit check: Max 3 shields
            shield_count_row = db_query("SELECT COUNT(*) FROM inventory WHERE user_id = ? AND item_name = 'Vault Shield'", (uid,), fetchone=True)
            shield_count = shield_count_row[0] if shield_count_row else 0
            if shield_count >= 3:
                await ctx.send("🛡️ You already have the maximum of **3 Vault Shields**! You must use one before buying more.")
                return
            
            success, msg_text = pay_jc(uid, cost)
            if not success:
                await ctx.send(msg_text)
                return
                
            add_item(uid, "Vault Shield", "Protection")
            log_transaction(uid, -cost, "Bought Vault Shield")
            
            await ctx.send(f"🛡️ {ctx.author.mention}, you purchased a **Vault Shield**! {msg_text} You now have **{shield_count+1}/3** active shields. (1 Use each)")

        elif item_type in ['role', 'custom role']:
            cost = 500000
            fee = 25000 # 5% to Vault
            
            # Check if they already own it
            if get_inventory_item(uid, "Custom Role Pass"):
                await ctx.send("🎟️ You already own a **Custom Role Pass**! Use `!setrole <color>` to configure it.")
                return
            
            success, msg_text = pay_jc(uid, cost)
            if not success:
                await ctx.send(msg_text)
                return
                
            track_fee(fee)
            add_item(uid, "Custom Role Pass", "Perk", "")
            log_transaction(uid, -cost, "Bought Custom Role Pass")
            log_transaction(uid, -fee, "Role Fee", processed=1)
            
            embed = discord.Embed(title="✨ Custom Role Pass Purchased!", color=discord.Color.magenta())
            embed.description = (f"Congratulations {ctx.author.mention}! You can now create your own custom role.\n\n"
                                 f"**Usage:** `{COMMAND_PREFIX}setrole #HexColor`\n"
                                 f"**Example:** `{COMMAND_PREFIX}setrole #FFD700`\n\n"
                                 f"*(You also paid **{fee:,} JC** in taxes to the Global Vault!)*")
            await ctx.send(embed=embed)

        elif item_type == "iron":
            cost = shop["iron"]
            success, pay_msg = pay_jc(uid, cost)
            if not success:
                await ctx.send(pay_msg)
                return
                
            add_item(uid, "Iron Safe", "Upgrades", "")
            log_transaction(uid, -cost, "Bought Iron Safe")
            new_limit = get_bank_limit(uid)
            await ctx.send(f"📦 **{ctx.author.name}**, you purchased an **Iron Safe**! Your total bank capacity is now **{new_limit:,} JC**. ({pay_msg})")
            return
            
        elif item_type == "steel":
            cost = shop["steel"]
            success, pay_msg = pay_jc(uid, cost)
            if not success:
                await ctx.send(pay_msg)
                return
                
            add_item(uid, "Steel Vault", "Upgrades", "")
            log_transaction(uid, -cost, "Bought Steel Vault")
            new_limit = get_bank_limit(uid)
            await ctx.send(f"🛡️ **{ctx.author.name}**, you purchased a **Steel Vault**! Your total bank capacity is now **{new_limit:,} JC**. ({pay_msg})")
            return


        elif item_type in ['charm', 'lucky charm']:
            cost = 2000
            now = int(time.time())
            expiry = now + 3600 # 1 hour
            
            success, msg_text = pay_jc(uid, cost)
            if not success:
                await ctx.send(msg_text)
                return
                
            # Lucky Charms stack in duration
            existing_expiry = 0
            row = db_query("SELECT MAX(item_data) FROM inventory WHERE user_id = ? AND item_name = 'Lucky Charm'", (uid,), fetchone=True)
            if row and row[0]:
                try: 
                    existing_expiry = int(row[0])
                    if existing_expiry > now:
                        expiry = existing_expiry + 3600
                except: pass
            
            add_item(uid, "Lucky Charm", "Charm", str(expiry))
            log_transaction(uid, -cost, "Bought Lucky Charm")
            await ctx.send(f"🍀 {ctx.author.mention}, you purchased a **Lucky Charm**! {msg_text} Your gambling luck is boosted until <t:{expiry}:t>.")

        elif item_type in ['gloves', 'sticky gloves']:
            cost = 5000
            success, msg_text = pay_jc(uid, cost)
            if not success:
                await ctx.send(msg_text)
                return
                
            add_item(uid, "Sticky Gloves", "Tool")
            log_transaction(uid, -cost, "Bought Sticky Gloves")
            await ctx.send(f"🧤 {ctx.author.mention}, you purchased **Sticky Gloves**! {msg_text} Your next robbery attempt will have a **+5% success rate**.")

        else:
            await ctx.send("🛒 Item not found or restocked! Try `!buy box`, `!buy pickaxe`, `!buy charm`, `!buy gloves`, etc.")

    @commands.command(name='inventory', aliases=['inv'])
    async def inv_command(self, ctx: commands.Context):
        """View your collected items."""
        uid = str(ctx.author.id)
        items = get_inventory(uid)
        
        if not items:
            await ctx.send("🎒 Your inventory is empty. Try opening some `!buy box`!")
            return

        # Group items
        item_list = {} # name: count
        item_details = {} # name: list of data strings
        
        for name, type, data in items:
            # Filter out internal/system items
            if type in ['System', 'Cooldown']:
                continue
                
            item_list[name] = item_list.get(name, 0) + 1
            if data and data.strip():
                if name not in item_details: item_details[name] = []
                item_details[name].append(data)
        
        lines = []
        for name, count in item_list.items():
            line = f"• **{name}** x{count}"
            
            # Special case for Lucky Charm expiry
            if name == "Lucky Charm" and name in item_details:
                # Show the latest expiry
                try:
                    expiries = [int(d) for d in item_details[name] if d.isdigit()]
                    if expiries:
                        latest = max(expiries)
                        if latest > int(time.time()):
                            line += f" (Expires: <t:{latest}:R>)"
                        else:
                            line += " (Expired)"
                except: pass
            elif name == "VIP" and name in item_details:
                try:
                    expiry = int(item_details[name][0])
                    line += f" (Expires: <t:{expiry}:d>)"
                except: pass
                
            lines.append(line)
        
        display = "\n".join(lines)
        embed = discord.Embed(title=f"🎒 {ctx.author.display_name}'s Inventory", description=display, color=discord.Color.blue())
        await ctx.send(embed=embed)

    @commands.command(name='setrole')
    async def setrole_command(self, ctx: commands.Context, color_input: str = None):
        """Configure your custom role! Usage: !setrole [ColorName or #Hex]"""
        if not color_input:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}setrole [ColorName or #HexColor]`\nExamples: `!setrole blue`, `!setrole #FFD700`")
            return
            
        uid = str(ctx.author.id)
        
        # Verify ownership
        if not get_inventory_item(uid, "Custom Role Pass"):
            await ctx.send(f"❌ You don't own a Custom Role Pass! Buy one in the `!shop` for 500,000 JC first.")
            return

        # Color validation and mapping
        color_val = color_input.strip().lower()
        role_color = None
        color_display = color_val
        
        color_map = {
            "red": discord.Color.red(),
            "blue": discord.Color.blue(),
            "green": discord.Color.green(),
            "yellow": discord.Color.gold(),
            "gold": discord.Color.gold(),
            "purple": discord.Color.purple(),
            "magenta": discord.Color.magenta(),
            "orange": discord.Color.orange(),
            "teal": discord.Color.teal(),
            "cyan": discord.Color(0x00FFFF),
            "pink": discord.Color(0xFFB6C1),
            "white": discord.Color.light_grey(),
            "black": discord.Color(0x010101) # Near black to render correctly
        }

        if color_val in color_map:
            role_color = color_map[color_val]
            color_display = color_val.capitalize()
        elif color_val.startswith("#") and len(color_val) == 7:
            try:
                role_color = discord.Color(int(color_val.lstrip("#"), 16))
                color_display = color_val.upper()
            except ValueError:
                pass
                
        if role_color is None:
            await ctx.send("❌ Invalid color! Please use a named color (e.g., `blue`, `red`, `gold`) or a 6-character Hex code (e.g., `#FF0000`).")
            return

        # Fetch existing role ID from DB if it exists
        row = db_query("SELECT item_data FROM inventory WHERE user_id = ? AND item_name = 'Custom Role Pass'", (uid,), fetchone=True)
        role_id_str = row[0] if row else ""
        
        my_role = None
        if role_id_str:
            try:
                my_role = ctx.guild.get_role(int(role_id_str))
            except ValueError:
                pass
                
        # Position calculation: Just below the bot's top role
        bot_top = ctx.guild.me.top_role
        target_pos = bot_top.position - 1
        if target_pos < 1: target_pos = 1

        try:
            zero_perms = discord.Permissions.none()
            if my_role:
                # Edit existing role - Costs 450,000 JC
                edit_cost = 450000
                success, msg_text = pay_jc(uid, edit_cost)
                if not success:
                    await ctx.send(msg_text)
                    return
                
                log_transaction(uid, -edit_cost, "Edited Custom Role")
                
                await my_role.edit(name="JC", color=role_color, permissions=zero_perms, hoist=True, mentionable=False, position=target_pos, reason=f"Custom role edit by {ctx.author.name}")
                await ctx.send(f"✨ Successfully updated color to `{color_display}` and moved it to the top! {msg_text} *(Cost: **{edit_cost:,} JC**)*")
            else:
                # Create new role and assign it (First time free)
                my_role = await ctx.guild.create_role(name="JC", color=role_color, permissions=zero_perms, hoist=True, mentionable=False, reason=f"Custom role creation by {ctx.author.name}")
                await my_role.edit(position=target_pos)
                await ctx.author.add_roles(my_role)
                db_query("UPDATE inventory SET item_data = ? WHERE user_id = ? AND item_name = 'Custom Role Pass'", (str(my_role.id), uid), commit=True)
                await ctx.send(f"✨ Successfully created role **JC** with color `{color_display}`! (First time free - Auto-positioned to top)")
        except discord.Forbidden:
            await ctx.send("❌ **Permission Denied!** I don't have the **'Manage Roles'** permission, or I am trying to edit a role that is higher than mine. Please move my **JenBot** role to the top of the list in Server Settings!")
        except discord.HTTPException as e:
            if e.code == 50013:
                await ctx.send("❌ **Role Hierarchy Error!** I don't have permission to manage this role. Please go to **Server Settings -> Roles** and drag the **JenBot** role to the **TOP** of the list (above all custom roles).")
            else:
                await ctx.send(f"❌ An error occurred while managing the role. Make sure the name isn't too long or contains invalid characters. Details: {e}")

    @commands.command(name='sell')
    async def sell_command(self, ctx: commands.Context, *, input_str: str = None):
        """Sell a collectible item for JC. Usage: !sell [item name] [quantity]"""
        if not input_str:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}sell [item name] [quantity]` (e.g. `!sell golden jc 2`)")
            return

        uid = str(ctx.author.id)
        parts = input_str.split()
        
        quantity = 1
        item_name_parts = parts
        
        # Check if the last part is a number (quantity)
        if len(parts) > 1 and parts[-1].isdigit():
            quantity = int(parts[-1])
            item_name_parts = parts[:-1]
            
        search_name = " ".join(item_name_parts).lower().strip()
        
        # Valid sellable items mapping (search_string -> (Exact DB Name, Price))
        sellable = {
            "golden jc": ("🏆 Golden JC", 25000),
            "golden": ("🏆 Golden JC", 25000),
            "silver coin": ("🥈 Silver Coin", 5000),
            "silver": ("🥈 Silver Coin", 5000)
        }
        
        if search_name not in sellable:
            await ctx.send("❌ You can only sell **Golden JC** or **Silver Coin**.")
            return
            
        exact_name, price = sellable[search_name]
        
        # Check if they own enough
        owned_count = get_item_count(uid, exact_name)
        if owned_count < quantity:
            await ctx.send(f"❌ You don't have enough **{exact_name}**! (Owned: **{owned_count}**, Requested: **{quantity}**)")
            return
        
        if quantity <= 0:
            await ctx.send("❌ Quantity must be at least 1!")
            return
            
        # Execute Sale
        total_price = price * quantity
        remove_items(uid, exact_name, quantity)
        new_bal = add_balance(uid, total_price)
        log_transaction(uid, total_price, f"Sold {quantity}x {exact_name}")
        
        embed = discord.Embed(
            title="🤝 Items Sold!",
            description=f"You successfully sold **{quantity}x {exact_name}** for **{total_price:,} JC**.",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"New Balance: {new_bal:,} JC")
        await ctx.send(embed=embed)

    # --- Admin Commands ---

    @commands.command(name='addcoins', aliases=['addjc'])
    @commands.is_owner()
    async def addcoins_command(self, ctx: commands.Context, member: discord.Member = None, amount: int = None):
        """Owner Only: Add JC to a user."""
        if member is None or amount is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}addcoins @user [amount]`")
            return
        if not await validate_admin_amount(ctx, amount): return
        new_bal = add_balance(str(member.id), amount)
        log_transaction(str(member.id), amount, f"Admin Add (by {ctx.author.display_name})")
        await ctx.send(f"✅ Added **{amount:,}** JC to {member.mention}. New balance: **{new_bal:,}**.")

    @commands.command(name='takecoins', aliases=['removejc', 'takejc'])
    @commands.is_owner()
    async def takecoins_command(self, ctx: commands.Context, member: discord.Member = None, amount: int = None):
        """Owner Only: Remove JC from a user."""
        if member is None or amount is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}takecoins @user [amount]`")
            return
        if not await validate_admin_amount(ctx, amount): return
        new_bal = add_balance(str(member.id), -amount)
        log_transaction(str(member.id), -amount, f"Admin Remove (by {ctx.author.display_name})")
        await ctx.send(f"✅ Removed **{amount:,}** JC from {member.mention}. New balance: **{new_bal:,}**.")

    @commands.command(name='grantvip', aliases=['givevip'])
    @commands.is_owner()
    async def grantvip_command(self, ctx: commands.Context, member: discord.Member = None, days: int = 30):
        """Owner Only: Grant VIP membership to a user."""
        if member is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}grantvip @user [days]`")
            return
        if days <= 0:
            await ctx.send("❌ Please provide a positive number of days.")
            return
        
        set_vip(str(member.id), days)
        expiry = get_vip_expiry(str(member.id))
        log_transaction(str(member.id), 0, f"Admin VIP Grant ({days}d by {ctx.author.display_name})")
        
        await ctx.send(f"👑 Granted **{days} days** of VIP to {member.mention}!\n📅 Expires: <t:{expiry}:F> (<t:{expiry}:R>)")

    @commands.command(name='nuke', aliases=['nukeuser', 'resetuser'])
    @commands.is_owner()
    async def nukeuser_command(self, ctx: commands.Context, member: discord.Member = None):
        """Owner Only: Completely reset a user's wallet, bank, gold, and stats."""
        if member is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}nuke @user`")
            return
        
        uid = str(member.id)
        
        # 1. Wipe Wallet & Bank
        set_balance(uid, 0)
        set_bank(uid, 0)
        
        # 2. Wipe Gold
        db_query("DELETE FROM investments WHERE user_id = ?", (uid,), commit=True)
        
        # 3. Wipe Inventory
        db_query("DELETE FROM inventory WHERE user_id = ?", (uid,), commit=True)
        
        # 4. Wipe Stats
        db_query("DELETE FROM user_stats WHERE user_id = ?", (uid,), commit=True)
        
        # 5. Log it
        log_transaction(uid, 0, f"ADMIN NUKE (by {ctx.author.display_name})")
        
        await ctx.send(f"☢️ **TOTAL NUKE COMPLETE.** {member.mention} has been reset to level zero. All JC, Gold, and Items have been incinerated.")

    @commands.command(name='setbox')
    @commands.is_owner()
    async def setbox_command(self, ctx: commands.Context, legendary: str = None, epic: str = None, rare: str = None, minutes: str = None):
        """Owner Only: Start a Mystery Box loot event. Usage: !setbox [leg%] [epic%] [rare%] [minutes]"""
        if not all([legendary, epic, rare, minutes]):
            await ctx.send(f"❌ Usage: `{COMMAND_PREFIX}setbox [legendary%] [epic%] [rare%] [minutes]`\n"
                           f"Example: `{COMMAND_PREFIX}setbox 0.1 2 3 30` → Leg 0.1%, Epic 2%, Rare 3%, Common 94.9% for 30min")
            return
        
        try:
            leg_pct = float(legendary)
            epic_pct = float(epic)
            rare_pct = float(rare)
            duration = int(float(minutes))
        except ValueError:
            await ctx.send("❌ Invalid input! All rates must be numbers and duration must be in minutes.")
            return
        
        if leg_pct < 0 or epic_pct < 0 or rare_pct < 0:
            await ctx.send("❌ Rates cannot be negative!")
            return
        
        total_pct = leg_pct + epic_pct + rare_pct
        if total_pct >= 100:
            await ctx.send(f"❌ Combined rates ({total_pct:.2f}%) must be less than 100%!")
            return
        
        if duration <= 0:
            await ctx.send("❌ Duration must be at least 1 minute!")
            return
        
        # Convert percentages to decimal (e.g., 0.1% → 0.001)
        leg_dec = leg_pct / 100
        epic_dec = epic_pct / 100
        rare_dec = rare_pct / 100
        common_pct = 100 - total_pct
        
        now = int(time.time())
        expiry = now + (duration * 60)
        
        # Save event rates and expiry
        set_setting('box_legendary_event', str(leg_dec))
        set_setting('box_epic_event', str(epic_dec))
        set_setting('box_rare_event', str(rare_dec))
        set_setting('box_event_expiry', str(expiry))
        
        # Build announcement embed
        embed = discord.Embed(
            title="🎁✨ MYSTERY BOX EVENT! ✨🎁",
            description=f"A special loot event has begun! Mystery Box rates are boosted for a limited time!",
            color=discord.Color.gold()
        )
        embed.add_field(
            name="🎲 Event Rates",
            value=(
                f"🏆 Legendary: **{leg_pct}%**\n"
                f"💜 Epic: **{epic_pct}%**\n"
                f"💙 Rare: **{rare_pct}%**\n"
                f"⬜ Common: **{common_pct:.2f}%**"
            ),
            inline=True
        )
        embed.add_field(
            name="⏰ Duration",
            value=f"Ends <t:{expiry}:R> (<t:{expiry}:T>)",
            inline=True
        )
        embed.set_footer(text="Open some boxes before the event ends! Use !buy box")
        
        # Broadcast to specifically set channel or fall back to system channels
        box_channel_id = get_setting("box_channel_id")
        sent_count = 0
        
        target_guild_id = None
        if box_channel_id:
            target = self.bot.get_channel(int(box_channel_id))
            if target:
                try:
                    await target.send(embed=embed)
                    sent_count += 1
                    target_guild_id = target.guild.id
                except Exception: pass
        
        # Original broadcast logic for other guilds
        for guild in self.bot.guilds:
            if guild.id == target_guild_id:
                continue # Already sent to this guild
            
            target_channel = guild.system_channel
            if not target_channel or not target_channel.permissions_for(guild.me).send_messages:
                # Fallback: find the first text channel we can send to
                for ch in guild.text_channels:
                    if ch.permissions_for(guild.me).send_messages:
                        target_channel = ch
                        break
                else:
                    continue
            
            try:
                await target_channel.send(embed=embed)
                sent_count += 1
            except Exception:
                pass
        
        await ctx.send(f"✅ **Mystery Box Event started!** Announced to **{sent_count}** server(s).\n"
                       f"🏆 Leg: {leg_pct}% | 💜 Epic: {epic_pct}% | 💙 Rare: {rare_pct}% | ⬜ Common: {common_pct:.2f}%\n"
                       f"⏰ Ends <t:{expiry}:R>")

    @commands.command(name='boxrates', aliases=['boxrate', 'br'])
    async def boxrates_command(self, ctx: commands.Context):
        """View the current Mystery Box loot rates."""
        rates = get_box_rates()
        leg_pct = rates['legendary'] * 100
        epic_pct = rates['epic'] * 100
        rare_pct = rates['rare'] * 100
        common_pct = 100 - leg_pct - epic_pct - rare_pct
        
        if rates['is_event']:
            expiry = rates['expiry']
            embed = discord.Embed(
                title="🎁✨ Mystery Box Rates (EVENT ACTIVE!)",
                description=f"A loot event is currently active! Ends <t:{expiry}:R>.",
                color=discord.Color.gold()
            )
        else:
            embed = discord.Embed(
                title="🎁 Mystery Box Rates",
                description="Standard loot rates are active.",
                color=discord.Color.greyple()
            )
        
        embed.add_field(
            name="🎲 Current Rates",
            value=(
                f"🏆 Legendary: **{leg_pct:.2f}%**\n"
                f"💜 Epic: **{epic_pct:.2f}%**\n"
                f"💙 Rare: **{rare_pct:.2f}%**\n"
                f"⬜ Common: **{common_pct:.2f}%**"
            ),
            inline=False
        )
        embed.set_footer(text=f"Open boxes with {COMMAND_PREFIX}buy box")
        await ctx.send(embed=embed)

# --- Blackjack Game Logic ---

class DuelView(discord.ui.View):
    def __init__(self, ctx, target, bet, pay_msg):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.target = target
        self.bet = bet
        self.pay_msg = pay_msg # P1's payment status
        self.message = None
        self.resolved = False

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green, emoji="⚔️")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("This challenge isn't for you!", ephemeral=True)
            return

        uid1 = str(self.ctx.author.id)
        uid2 = str(self.target.id)

        # Check if P2 still has money
        if get_balance(uid2) < self.bet:
            await interaction.response.send_message("You don't have enough JC to accept this duel anymore!", ephemeral=True)
            return

        self.resolved = True
        self.stop()
        
        # Deduct from P2
        _, pay_msg2 = pay_jc(uid2, self.bet)

        # Flip the coin
        winner = random.choice([self.ctx.author, self.target])
        
        # Pot calculation (Total - 5% fee)
        total_pot = self.bet * 2
        fee = int(total_pot * 0.05)
        winnings = total_pot - fee
        
        track_fee(fee)
        add_balance(str(winner.id), winnings)
        log_transaction(str(winner.id), winnings, "Duel Win")
        log_transaction(uid1 if winner.id != self.ctx.author.id else uid2, -self.bet, "Duel Loss")

        embed = discord.Embed(
            title="⚔️ Duel Results!",
            description=f"The coin spins in the air...\n\n🏆 **{winner.display_name}** wins the duel!\n💰 They take home **{winnings:,} JC** (after 5% vault fee).",
            color=discord.Color.gold()
        )
        embed.add_field(name="Participants", value=f"{self.ctx.author.display_name} vs {self.target.display_name}", inline=False)
        embed.set_footer(text=f"Total Pot: {total_pot:,} | Vault Fee: {fee:,}")
        
        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in [self.ctx.author.id, self.target.id]:
            await interaction.response.send_message("You can't do that!", ephemeral=True)
            return

        self.resolved = True
        self.stop()
        
        # Refund P1
        add_balance(str(self.ctx.author.id), self.bet)
        log_transaction(str(self.ctx.author.id), self.bet, "Duel Cancelled (Refund)")

        msg = "declined the challenge." if interaction.user.id == self.target.id else "cancelled the challenge."
        await interaction.response.edit_message(content=f"❌ Duel {msg} (Refunded)", embed=None, view=None)

    async def on_timeout(self):
        if not self.resolved:
            # Refund P1
            add_balance(str(self.ctx.author.id), self.bet)
            log_transaction(str(self.ctx.author.id), self.bet, "Duel Timed Out (Refund)")
            if self.message:
                try:
                    await self.message.edit(content="⏰ Duel challenge timed out. (Refunded)", embed=None, view=None)
                except: pass


class BlackjackView(discord.ui.View):
    def __init__(self, ctx, bet):
        super().__init__(timeout=180)
        self.ctx = ctx
        self.bet = bet
        self.deck = self.create_deck()
        self.player_hand = [self.draw_card(), self.draw_card()]
        self.dealer_hand = [self.draw_card(), self.draw_card()]
        self.message = None
        self.is_natural = False
        self.game_over = False
        
        # Take bet upfront
        uid = str(ctx.author.id)
        _, self.pay_msg = pay_jc(uid, bet)

    def create_deck(self):
        suits = ['♠️', '♥️', '♣️', '♦️']
        ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        deck = [f"{r} {s}" for r in ranks for s in suits]
        random.shuffle(deck)
        return deck

    def draw_card(self):
        return self.deck.pop()

    def calculate_value(self, hand):
        value = 0
        aces = 0
        for card in hand:
            rank = card.split()[0]
            if rank in ['J', 'Q', 'K']: value += 10
            elif rank == 'A':
                value += 11
                aces += 1
            else: value += int(rank)
        
        while value > 21 and aces:
            value -= 10
            aces -= 1
        return value

    def get_hand_str(self, hand, hide_first=False):
        if hide_first:
            return f"❓ | {hand[1]}"
        return " | ".join(hand)

    async def start_game(self):
        player_val = self.calculate_value(self.player_hand)
        if player_val == 21:
            self.is_natural = True
            await self.finish_game("Natural Blackjack! 🎊", win=True)
            return

        embed = self.make_embed()
        self.message = await self.ctx.send(embed=embed, view=self)

    def make_embed(self, finished=False):
        player_val = self.calculate_value(self.player_hand)
        dealer_val = self.calculate_value(self.dealer_hand)
        
        embed = discord.Embed(title="🃏 Blackjack Table", color=discord.Color.blue())
        embed.add_field(name=f"Your Hand ({player_val})", value=self.get_hand_str(self.player_hand), inline=False)
        
        if finished:
            embed.add_field(name=f"Dealer's Hand ({dealer_val})", value=self.get_hand_str(self.dealer_hand), inline=False)
        else:
            embed.add_field(name="Dealer's Hand", value=self.get_hand_str(self.dealer_hand, hide_first=True), inline=False)
        
        embed.set_footer(text=f"Bet: {self.bet:,} JC")
        if not finished:
            embed.add_field(name="Payment", value=self.pay_msg, inline=False)
        return embed

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.green, emoji="➕")
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id: return
        
        self.player_hand.append(self.draw_card())
        val = self.calculate_value(self.player_hand)
        
        if val > 21:
            await interaction.response.defer()
            await self.finish_game("Bust! 💥 You went over 21.", win=False)
        elif val == 21:
            await interaction.response.defer()
            await self.stand_logic()
        else:
            await interaction.response.edit_message(embed=self.make_embed())

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.grey, emoji="🛑")
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id: return
        await interaction.response.defer()
        await self.stand_logic()

    async def stand_logic(self):
        # Dealer's turn
        while self.calculate_value(self.dealer_hand) < 17:
            self.dealer_hand.append(self.draw_card())
        
        p_val = self.calculate_value(self.player_hand)
        d_val = self.calculate_value(self.dealer_hand)
        
        if d_val > 21:
            await self.finish_game("Dealer Busts! 🥳", win=True)
        elif d_val > p_val:
            await self.finish_game("Dealer wins. 📉", win=False)
        elif d_val < p_val:
            await self.finish_game("You win! 🏆", win=True)
        else:
            await self.finish_game("It's a Tie! (Push) 🤝", win=None)

    async def finish_game(self, result_text, win):
        self.game_over = True
        self.stop()
        
        uid = str(self.ctx.author.id)
        if win is True:
            # Payout logic: Original bet back + winnings
            # Standard: bet + (bet * 0.9) = 1.9x
            # Natural: bet + (bet * 1.2) = 2.2x
            # We redirect 10% of what should have been the winnings (bet * 0.1) to the vault
            profit_multiplier = 1.2 if self.is_natural else 0.9
            payout = int(self.bet + (self.bet * profit_multiplier))
            
            # Tax Logic: The 0.1x difference is the tax
            tax_amount = int(self.bet * 0.1)
            vault_bal = int(float(get_setting("fee_vault", "0")))
            set_setting("fee_vault", str(vault_bal + tax_amount))
            
            new_bal = add_balance(uid, payout)
            log_transaction(uid, payout, "Blackjack Win" + (" (Natural)" if self.is_natural else ""))
            log_transaction(uid, tax_amount, "Blackjack Tax", processed=1)
            
            color = discord.Color.green()
        elif win is False:
            new_bal = get_balance(uid)
            track_fee(self.bet)
            log_transaction(uid, -self.bet, "Blackjack Loss", processed=1)
            color = discord.Color.red()
        else: # Tie (Push) - Get bet back
            new_bal = add_balance(uid, self.bet)
            log_transaction(uid, self.bet, "Blackjack Push")
            color = discord.Color.blue()

        embed = self.make_embed(finished=True)
        embed.title = f"🃏 {result_text}"
        embed.color = color
        
        if win is True:
            payout_display = int(self.bet + (self.bet * (1.2 if self.is_natural else 0.9)))
            embed.add_field(name="💰 Payout", value=f"**{payout_display:,}** JC returned to Wallet", inline=False)
        elif win is None:
            embed.add_field(name="🤝 Refund", value=f"**{self.bet:,}** JC returned to Wallet", inline=False)
            
        embed.add_field(name="💳 New Balance", value=f"**{new_bal:,}** JC", inline=False)
        
        if self.message:
            await self.message.edit(embed=embed, view=None)
        else:
            await self.ctx.send(embed=embed)

    async def on_timeout(self):
        if not self.game_over:
            await self.finish_game("Game Timed Out (Refunded)", win=None)

# --- Rain Event Logic ---

def should_game_crash(multiplier: float) -> bool:
    """
    Calculates if the game should crash at the current multiplier.
    The chance increases exponentially as the multiplier gets higher.
    """
    # 7% Instant Crash chance at 1.00x (House Edge)
    if multiplier <= 1.00 and random.random() < 0.07:
        return True
    
    # Exponential Probability of crashing each tick (approx 1.5s)
    # This creates a "Natural Death Curve" instead of a hard cap.
    # At 2x: ~10% | At 5x: ~27% | At 10x: ~68% | At 13x: ~100%
    base_chance = 0.05
    risk_factor = 0.02 * (multiplier ** 1.5)
    total_chance = base_chance + risk_factor
    
    return random.random() < total_chance

class CrashView(discord.ui.View):
    def __init__(self, ctx, active_bet, original_bet, is_vip=False):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.active_bet = active_bet
        self.original_bet = original_bet
        self.is_vip = is_vip
        self.multiplier = 1.00
        self.cashed_out = False
        self.crashed = False
        self.message = None

    def get_tax_rate(self):
        """Returns the tax rate based on current multiplier (VIPs get 3% discount)."""
        bonus = 0.03 if self.is_vip else 0.0
        if self.multiplier < 2.0: return 0.50 - bonus
        if self.multiplier < 5.0: return 0.35 - bonus
        return 0.20 - bonus

    async def run_game(self):
        """The background loop that drives the multiplier and crash checks."""
        # Initial 7% check before takeoff
        if should_game_crash(self.multiplier):
            self.crashed = True
            await self.do_crash()
            return

        while not self.cashed_out and not self.crashed:
            await asyncio.sleep(1.5)
            if self.cashed_out or self.crashed:
                break

            # Increase multiplier
            increment = random.uniform(0.10, 0.25) # Slightly faster growth
            if self.multiplier > 5: increment *= 1.5
            
            self.multiplier = round(self.multiplier + increment, 2)

            # Check for crash at this NEW multiplier
            if should_game_crash(self.multiplier):
                self.crashed = True
                await self.do_crash()
                break
            else:
                await self.update_display()

    async def update_display(self):
        """Updates the embed with the current multiplier."""
        if not self.message: return
        
        tax_rate = self.get_tax_rate()
        gross_payout = int(self.active_bet * self.multiplier)
        profit = max(0, gross_payout - self.active_bet)
        est_tax = int(profit * tax_rate)
        net_payout = gross_payout - est_tax
        
        embed = discord.Embed(title="🚀 CRASH GAME", color=discord.Color.blue())
        embed.description = (
            f"Multiplier: **{self.multiplier:.2f}x**\n"
            f"Gross Total: **{gross_payout:,}** JC\n"
            f"Estimated Tax: **{est_tax:,}** JC ({int(tax_rate*100)}%)\n\n"
            f"🔥 **NET PAYOUT**: **{net_payout:,}** JC\n\n"
            f"*Click below to Cash Out!*"
        )
        embed.set_thumbnail(url="https://cdn.pixabay.com/animation/2022/11/16/11/48/11-48-26-444_512.gif")
        embed.set_footer(text=f"Total Bet: {self.original_bet:,} | Active: {self.active_bet:,}")
        
        try:
            await self.message.edit(embed=embed, view=self)
        except:
            pass

    @discord.ui.button(label="CASH OUT 💸", style=discord.ButtonStyle.green)
    async def cash_out_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ This is not your game!", ephemeral=True)
            return
        
        if self.cashed_out or self.crashed:
            return

        self.cashed_out = True
        self.stop()
        
        tax_rate = self.get_tax_rate()
        gross_payout = int(self.active_bet * self.multiplier)
        profit = max(0, gross_payout - self.active_bet)
        tax_deducted = int(profit * tax_rate)
        final_payout = gross_payout - tax_deducted
        
        track_fee(tax_deducted)
        new_bal = add_balance(str(self.ctx.author.id), final_payout)
        
        log_transaction(str(self.ctx.author.id), final_payout, f"Crash Win ({self.multiplier}x)")
        
        embed = discord.Embed(title="🚀 CASHED OUT!", color=discord.Color.green())
        net_result = final_payout - self.original_bet
        result_str = f"+{net_result:,}" if net_result >= 0 else f"{net_result:,}"
        
        embed.description = (
            f"You cashed out at **{self.multiplier:.2f}x**!\n\n"
            f"💰 **Final Payout**: **{final_payout:,}** JC\n"
            f"💸 **Tax Deducted**: `{tax_deducted:,} JC` ({int(tax_rate*100)}%)\n"
            f"🧤 **Entry Fee**: `{self.original_bet - self.active_bet:,} JC` {'⭐ (VIP)' if self.is_vip else ''}\n"
            f"📊 **Net Profit/Loss**: **{result_str}** JC"
        )
        embed.add_field(name="Current Wallet", value=f"**{new_bal:,}** JC")
        embed.set_footer(text=f"Original Bet: {self.original_bet:,} | Active: {self.active_bet:,}")
        
        await interaction.response.edit_message(embed=embed, view=None)

    async def do_crash(self):
        """Handles the crash state."""
        self.stop()
        track_fee(self.active_bet)
        
        embed = discord.Embed(title="💥 CRASHED!!!", color=discord.Color.red())
        embed.description = (
            f"The rocket crashed at **{self.multiplier:.2f}x**!\n"
            f"💨 You lost your total bet of **{self.original_bet:,}** JC."
        )
        embed.set_thumbnail(url="https://cdn.pixabay.com/animation/2022/11/16/11/48/11-48-26-444_512.gif")
        
        try:
            await self.message.edit(embed=embed, view=None)
        except:
            pass

    async def on_timeout(self):
        if not self.cashed_out and not self.crashed:
            self.crashed = True
            await self.do_crash()

class RainView(discord.ui.View):
    def __init__(self, pool):
        super().__init__(timeout=60)
        self.pool = pool
        self.winners = []
        self.message = None

    @discord.ui.button(label="CATCH 🖐️", style=discord.ButtonStyle.blurple)
    async def catch(self, interaction: discord.Interaction, button: discord.ui.Button):
        uid = str(interaction.user.id)
        
        # Check if already caught
        if any(w['id'] == uid for w in self.winners):
            await interaction.response.send_message("❌ You already caught some rain! Let others have a chance.", ephemeral=True)
            return

        if self.pool <= 0:
            await interaction.response.send_message("❌ The rain has already dried up!", ephemeral=True)
            return

        # Fetch dynamic range or use defaults
        try:
            r_min = int(get_setting('rain_min', '100'))
            r_max = int(get_setting('rain_max', '500'))
        except (ValueError, TypeError):
            r_min, r_max = 100, 500

        # Amount is random but capped by the remaining pool
        amount = random.randint(r_min, r_max)
        if amount > self.pool:
            amount = self.pool
        
        self.pool -= amount
        new_bal = add_balance(uid, amount)
        log_transaction(uid, amount, "Caught Rain")
        
        self.winners.append({'id': uid, 'name': interaction.user.display_name, 'amount': amount})
        
        await interaction.response.send_message(f"🧤 **CATCH!** You caught **{amount}** JC! (Remaining Pool: {self.pool:,})", ephemeral=True)

        if self.pool <= 0:
            await self.finish_rain()

    async def finish_rain(self):
        self.stop()
        if not self.message: return

        if not self.winners:
            desc = "⛈️ The rain has dried up... No one caught anything."
        else:
            w_list = "\n".join([f"✨ **{w['name']}**: `{w['amount']} JC`" for w in self.winners])
            
            # Guard against Discord's 4096 character embed description limit
            prefix = "🌈 The rain has stopped! Here are our lucky catchers:\n\n"
            if len(prefix) + len(w_list) > 4000:
                w_list = w_list[:3900] + "\n... (Long list truncated)"
                
            desc = prefix + w_list

        embed = discord.Embed(title="☀️ Rain Over", description=desc, color=discord.Color.gold())
        await self.message.edit(embed=embed, view=None)

    async def on_timeout(self):
        await self.finish_rain()

async def setup(bot):
    await bot.add_cog(Economy(bot))

