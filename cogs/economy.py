import os
import random
import sqlite3
import time
import asyncio
from datetime import datetime, timezone, timedelta
import discord
from discord.ext import commands
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

DAILY_BASE = 100        # base daily coins
DAILY_STREAK_BONUS = 20 # extra per streak day (capped at 10)
WORK_COOLDOWN = 3600  # 1 hour in seconds
WORK_MIN = 20
WORK_MAX = 500
STARTING_BALANCE = 0


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS wallets (user_id TEXT PRIMARY KEY, balance INTEGER DEFAULT 0, last_daily TEXT DEFAULT '', last_work TEXT DEFAULT '')")
    conn.execute("CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, amount INTEGER, type TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
    conn.execute("CREATE TABLE IF NOT EXISTS inventory (user_id TEXT, item_name TEXT, item_type TEXT, item_data TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS investments (user_id TEXT PRIMARY KEY, gold_grams REAL DEFAULT 0.0)")
    conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
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

def log_transaction(user_id: str, amount: int, trans_type: str):
    ts = int(time.time())
    db_query("INSERT INTO transactions (user_id, amount, type, timestamp) VALUES (?, ?, ?, ?)", (user_id, amount, trans_type, ts), commit=True)

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

def get_top_balances(limit=10) -> list:
    return db_query("SELECT user_id, balance FROM wallets ORDER BY balance DESC LIMIT ?", (limit,), fetchall=True)

def add_item(user_id, item_name, item_type="Collectible", item_data=""):
    db_query("INSERT INTO inventory (user_id, item_name, item_type, item_data) VALUES (?, ?, ?, ?)", (user_id, item_name, item_type, item_data), commit=True)

def get_inventory(user_id):
    return db_query("SELECT item_name, item_type FROM inventory WHERE user_id = ?", (user_id,), fetchall=True)

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
    """Fetches the live gold price in MYR/g"""
    currency_code = "MYR"
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

def remove_item(user_id, item_name):
    # Remove only ONE instance of the item
    db_query("DELETE FROM inventory WHERE ROWID = (SELECT ROWID FROM inventory WHERE user_id = ? AND item_name = ? LIMIT 1)", (user_id, item_name), commit=True)

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

# --- Helpers ---

async def validate_bet(ctx: commands.Context, amount_str):
    """
    Validates a bet amount, handling commas and 'max'/'all'.
    Returns (amount_int, error_message)
    """
    uid = str(ctx.author.id)
    bal = get_balance(uid)

    if amount_str is None:
        return None, "❌ Please provide a positive bet amount!"

    s = str(amount_str).lower().replace(',', '')
    if s in ['max', 'all']:
        amount = bal
    else:
        try:
            amount = int(s)
        except ValueError:
            return None, "❌ Invalid amount! Use numbers or 'max'."

    if amount <= 0:
        return None, "❌ Please provide a positive bet amount!"
    
    if bal < amount:
        return None, f"❌ You only have **{bal:,}** JC."
    
    return amount, None

async def validate_admin_amount(ctx: commands.Context, amount: int):
    if amount <= 0:
        await ctx.send("❌ Amount must be positive.")
        return False
    return True


class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='bal', aliases=['balance', 'wallet'])
    async def balance_command(self, ctx: commands.Context, member: discord.Member = None):
        """Check your (or someone else's) JC balance."""
        target = member or ctx.author
        uid = str(target.id)
        
        wallet = get_balance(uid)
        bank = get_bank(uid)
        total = wallet + bank
        
        embed = discord.Embed(
            title=f"💰 {target.display_name}'s Balances",
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="💵 Wallet", value=f"**{wallet:,}** JC", inline=True)
        embed.add_field(name="🏦 Bank", value=f"**{bank:,}** JC", inline=True)
        embed.add_field(name="Total Net Worth", value=f"**{total:,}** JC", inline=False)
        await ctx.send(embed=embed)
        
    @commands.command(name='deposit', aliases=['dep'])
    async def deposit_command(self, ctx: commands.Context, amount_str: str = None):
        """Deposit JC into your secure Bank. Usage: !deposit [amount | max]"""
        uid = str(ctx.author.id)
        amount, err = await validate_bet(ctx, amount_str)
        if err:
            await ctx.send(err)
            return
            
        add_balance(uid, -amount)
        new_bank = add_bank(uid, amount)
        log_transaction(uid, amount, "Bank Deposit")
        
        await ctx.send(f"🏦 {ctx.author.mention}, you deposited **{amount:,}** JC into your bank.\nNew Bank Balance: **{new_bank:,}** JC.")

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

        total = DAILY_BASE
        new_bal = add_balance(uid, total)
        set_last_daily(uid, today)
        log_transaction(uid, total, "Daily Reward")

        embed = discord.Embed(
            title="🎁 Daily Claimed!",
            description=f"💵 {ctx.author.mention} received **{total:,}** JC!",
            color=discord.Color.green()
        )
        embed.add_field(name="New Balance", value=f"**{new_bal:,}** JC", inline=False)
        await ctx.send(embed=embed)

    @commands.command(name='work', aliases=['job'])
    async def work_command(self, ctx: commands.Context):
        """Work for some JC! (1 hour cooldown)"""
        uid = str(ctx.author.id)
        now = int(time.time())
        last_str = get_last_work(uid)
        
        if last_str:
            try:
                last_ts = int(float(last_str))
                diff = now - last_ts
                if diff < WORK_COOLDOWN:
                    remaining = int(WORK_COOLDOWN - diff)
                    mins = remaining // 60
                    secs = remaining % 60
                    await ctx.send(f"⏳ {ctx.author.mention}, you're exhausted! Come back in **{mins}m {secs}s**.")
                    return
            except ValueError:
                pass

        reward = random.randint(WORK_MIN, WORK_MAX)
        fee_rate = 0.02 if is_vip(uid) else 0.05
        tax = max(1, int(reward * fee_rate))
        net_reward = reward - tax
        
        new_bal = add_balance(uid, net_reward)
        set_last_work(uid, str(now))
        track_fee(tax)
        log_transaction(uid, net_reward, "Work Payment")

        jobs = [
            "cleaned the server kitchen", "coded a new feature", "moderated a spicy channel",
            "organized the bot's database", "helped a new member", "fixed a bunch of bugs",
            "wrote some elegant documentation", "designed a new logo", "streamed for 2 hours"
        ]
        job = random.choice(jobs)

        embed = discord.Embed(
            title="⚒️ Hard Work Pays Off!",
            description=f"{ctx.author.mention}, you **{job}** and earned **{reward}** JC!",
            color=discord.Color.blue()
        )
        tax_percent = int(fee_rate * 100)
        embed.add_field(name="Income Tax", value=f"**{tax}** JC ({tax_percent}%)", inline=True)
        embed.add_field(name="Net Received", value=f"**{net_reward:,}** JC", inline=True)
        embed.add_field(name="New Wallet", value=f"**{new_bal:,}** JC", inline=False)
        embed.set_footer(text="Tax collected is added to the global fee vault!")
        await ctx.send(embed=embed)

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

        add_balance(str(ctx.author.id), -amount)
        new_receiver = add_balance(str(member.id), amount)

        log_transaction(str(ctx.author.id), -amount, f"Transfer to {member.display_name}")
        log_transaction(str(member.id), amount, f"Transfer from {ctx.author.display_name}")

        embed = discord.Embed(
            title="💸 Transfer Complete",
            description=f"{ctx.author.mention} → {member.mention}\n**{amount:,}** JC",
            color=discord.Color.blue()
        )
        embed.add_field(name=f"{member.display_name}'s Balance", value=f"**{new_receiver:,}**", inline=True)
        await ctx.send(embed=embed)

    # --- Live Gold Trading ---

    @commands.command(name='portfolio', aliases=['pf'])
    async def portfolio_command(self, ctx: commands.Context, member: discord.Member = None):
        """View your Investment Portfolio."""
        target = member or ctx.author
        uid = str(target.id)
        
        wallet = get_balance(uid)
        bank = get_bank(uid)
        gold_grams = get_gold_grams(uid)
        vip_active = is_vip(uid)
        
        embed = discord.Embed(title=f"📊 {target.display_name}'s Portfolio", color=discord.Color.dark_gold())
        embed.set_thumbnail(url=target.display_avatar.url)
        
        vip_status = "❌ None"
        if vip_active:
            expiry = get_vip_expiry(uid)
            vip_status = f"✅ Active (Expires <t:{expiry}:R>)"
        
        embed.add_field(name="VIP Membership 👑", value=vip_status, inline=False)
        embed.add_field(name="Balances 💵🏦", value=f"Wallet: **{wallet:,}** JC\nBank: **{bank:,}** JC", inline=False)
        
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
        jc_amount, err = await validate_bet(ctx, amount)
        if err:
            await ctx.send(err)
            return
            
        msg = await ctx.send("<a:loading:111> Fetching live gold exchange rate...")
        live_price = await fetch_live_gold_price(self.bot)
        
        if not live_price:
            await msg.edit(content="❌ The Gold Market is currently closed. Please try again later.")
            return
            
        fee_rate = 0.02 if is_vip(uid) else 0.05
        fee = max(1, int(jc_amount * fee_rate)) 
        purchase_power = jc_amount - fee
        
        grams_bought = purchase_power / live_price
        
        add_balance(uid, -jc_amount)
        add_gold_grams(uid, grams_bought)
        track_fee(fee)
        log_transaction(uid, -jc_amount, "Bought Gold")
        
        embed = discord.Embed(title="🏦 Gold Purchase Receipt", color=discord.Color.green())
        embed.add_field(name="Spent", value=f"**{jc_amount:,}** JC\n*(Includes **{fee:,}** JC fee)*", inline=True)
        embed.add_field(name="Acquired", value=f"**{grams_bought:.4f}g** Gold", inline=True)
        fee_percent = int(fee_rate * 100)
        embed.add_field(name="Execution Price", value=f"{live_price:,.2f} JC/g ({fee_percent}% Fee)", inline=False)
        embed.set_footer(text="Trade executed successfully at market price.")
        
        await msg.edit(content=None, embed=embed)

    @commands.command(name='buyvip', aliases=['vip'])
    async def buy_vip_command(self, ctx: commands.Context):
        """Purchase 30 days of VIP Membership for 10,000 JC. Reduces Gold fees to 2%."""
        uid = str(ctx.author.id)
        cost = 10000
        bal = get_balance(uid)
        
        if bal < cost:
            await ctx.send(f"❌ VIP Membership costs **{cost:,}** JC. You only have **{bal:,}** JC.")
            return
            
        add_balance(uid, -cost)
        set_vip(uid, 30)
        log_transaction(uid, -cost, "Purchased VIP")
        
        expiry = get_vip_expiry(uid)
        embed = discord.Embed(
            title="👑 VIP Membership Activated!",
            description=f"Congratulations {ctx.author.mention}! Your VIP status is now active.\n\n"
                        f"✨ **Perks unlocked:**\n"
                        f"- Gold Trading Fees reduced from **5%** to **2%**!\n"
                        f"- Shiny VIP Badge in `!pf`.\n\n"
                        f"📅 **Expiry:** <t:{expiry}:F> (<t:{expiry}:R>)",
            color=discord.Color.purple()
        )
        await ctx.send(embed=embed)

    @commands.command(name='sellgold', aliases=['sg'])
    async def sellgold_command(self, ctx: commands.Context, grams_to_sell: str = None):
        """Sell your Gold at the live market rate. (5% Fee) Usage: !sellgold [grams | max]"""
        uid = str(ctx.author.id)
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
            
        gross_value = int(sell_amount * live_price)
        fee_rate = 0.02 if is_vip(uid) else 0.05
        fee = max(1, int(gross_value * fee_rate)) 
        net_payout = gross_value - fee
        
        add_gold_grams(uid, -sell_amount)
        add_balance(uid, net_payout)
        track_fee(fee)
        log_transaction(uid, net_payout, "Sold Gold")
        
        embed = discord.Embed(title="🏦 Gold Sale Receipt", color=discord.Color.green())
        embed.add_field(name="Sold", value=f"**{sell_amount:.4f}g** Gold", inline=True)
        embed.add_field(name="Received", value=f"**{net_payout:,}** JC\n*(After **{fee:,}** JC fee)*", inline=True)
        fee_percent = int(fee_rate * 100)
        embed.add_field(name="Execution Price", value=f"{live_price:,.2f} JC/g ({fee_percent}% Fee)", inline=False)
        embed.set_footer(text="Trade executed successfully at market price.")
        
        await msg.edit(content=None, embed=embed)

    @commands.command(name='vault', aliases=['fees'])
    async def vault_command(self, ctx: commands.Context):
        """View the Global JC Fee Vault."""
        total_fees = int(get_setting("fee_vault", "0"))
        embed = discord.Embed(
            title="🏦 Global Fee Vault",
            description=f"This vault tracks all processing fees collected from Gold traders. "
                        f"These funds will be periodically returned to the community!\n\n"
                        f"💰 **Total Collected:** **{total_fees:,}** JC",
            color=discord.Color.dark_blue()
        )
        embed.set_footer(text="Trade fees keep the server economy healthy!")
        await ctx.send(embed=embed)


    @commands.command(name='top', aliases=['rich'])
    async def top_command(self, ctx: commands.Context):
        """Show the richest users."""
        rows = get_top_balances(10)
        if not rows:
            await ctx.send("📭 No one has any JC yet! Use `!daily` to get started.")
            return

        embed = discord.Embed(title="🏦 JC Leaderboard", color=discord.Color.gold())
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (user_id, balance) in enumerate(rows):
            medal = medals[i] if i < 3 else f"`{i+1}.`"
            try:
                user = await self.bot.fetch_user(int(user_id))
                name = user.display_name
            except Exception:
                name = f"User {user_id}"
            lines.append(f"{medal} **{name}** — {balance:,} JC")
        embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

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
        outcome = random.choice(['h', 't'])
        user_choice = 'h' if side in ['h', 'heads'] else 't'
        won = (user_choice == outcome)
        outcome_full = "Heads" if outcome == 'h' else "Tails"

        if won:
            winnings = amount
            new_bal = add_balance(uid, winnings)
            log_transaction(uid, winnings, "Flip Win")
            color = discord.Color.green()
            msg = f"🎉 You guessed right!\nYou won **{winnings:,}** JC!"
        else:
            new_bal = add_balance(uid, -amount)
            log_transaction(uid, -amount, "Flip Loss")
            color = discord.Color.red()
            msg = f"😢 You guessed wrong.\nYou lost **{amount:,}** JC."

        embed = discord.Embed(title=f"🪙 Coin Flip — {outcome_full}!", description=msg, color=color)
        embed.add_field(name="Balance", value=f"**{new_bal:,}** JC", inline=False)
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
        reels = [random.choice(SLOT_EMOJIS) for _ in range(3)]
        reel_display = " | ".join(reels)

        if reels[0] == reels[1] == reels[2]:
            multiplier = SLOT_PAYOUTS.get(reels[0], 2)
            winnings = amount * multiplier
            new_bal = add_balance(uid, winnings)
            log_transaction(uid, winnings, f"Slots Win ({reels[0]})")
            title = "🎰 JACKPOT!!! 🎰" if reels[0] == "7️⃣" else "🎰 THREE OF A KIND!"
            desc = f"**[ {reel_display} ]**\n\n🎉 You won **{winnings:,}** JC! (x{multiplier})"
            color = discord.Color.gold()
        elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
            new_bal = get_balance(uid)
            log_transaction(uid, 0, "Slots Draw")
            title = "🎰 Two of a Kind"
            desc = f"**[ {reel_display} ]**\n\n😌 Two match! You got your bet back."
            color = discord.Color.blue()
        else:
            new_bal = add_balance(uid, -amount)
            log_transaction(uid, -amount, "Slots Loss")
            title = "🎰 No Match"
            desc = f"**[ {reel_display} ]**\n\n💨 No luck this time. You lost **{amount:,}** JC."
            color = discord.Color.red()

        embed = discord.Embed(title=title, description=desc, color=color)
        embed.add_field(name="Balance", value=f"**{new_bal:,}** JC", inline=False)
        embed.set_footer(text=f"Bet: {amount:,} JC")
        await ctx.send(embed=embed)

    @commands.command(name='rob', aliases=['steal', 'stolen'])
    async def rob_command(self, ctx: commands.Context, member: discord.Member = None):
        """Try to rob another user's JC! (4 hour cooldown)"""
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
        cooldown = 4 * 3600 # 4 hours
        last_str = get_last_rob(uid)
        if last_str:
            try:
                last_ts = int(float(last_str))
                diff = now - last_ts
                if diff < cooldown:
                    rem = cooldown - diff
                    await ctx.send(f"⏳ {ctx.author.mention}, you're still lying low! Try again in **{rem//3600}h {(rem%3600)//60}m**.")
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
        
        success = random.random() < success_rate and not has_shield
        set_last_rob(uid, now)
        
        if success:
            # Stolen amount: 10-25%
            percent = random.uniform(0.10, 0.25)
            stolen = int(v_bal * percent)
            
            # Guild Tax (5%)
            tax = int(stolen * 0.05)
            net_gain = stolen - tax
            
            add_balance(vid, -stolen)
            add_balance(uid, net_gain)
            track_fee(tax)
            log_transaction(uid, net_gain, f"Robbed {member.display_name}")
            log_transaction(vid, -stolen, f"Robbed by {ctx.author.display_name}")
            
            embed = discord.Embed(title="🥷 Successful Robbery!", color=discord.Color.green())
            embed.description = f"You managed to snatch **{stolen:,}** JC from {member.mention}!"
            embed.add_field(name="Net Gain", value=f"**{net_gain:,}** JC", inline=True)
            embed.add_field(name="Guild Tax", value=f"**{tax:,}** JC (Sent to Vault)", inline=True)
            embed.set_footer(text="Crime pays... for now.")
            await ctx.send(embed=embed)
        else:
            # Penalty: 10% of thief's wallet
            penalty_rate = 0.10
            if is_vip(uid): penalty_rate = 0.05 # VIPs pay half penalty
            
            fine = int(t_bal * penalty_rate)
            
            # Legal Fees (2%)
            legal_fee = int(fine * 0.02)
            restitution = fine - legal_fee
            
            add_balance(uid, -fine)
            add_balance(vid, restitution)
            track_fee(legal_fee)
            log_transaction(uid, -fine, f"Failed Robbery of {member.display_name}" + (" (Shielded)" if has_shield else ""))
            log_transaction(vid, restitution, f"Compensated for Attempted Robbery")
            
            if has_shield:
                remove_item(vid, "Vault Shield")
                embed = discord.Embed(title="🛡️ SHIELD ACTIVATED!", color=discord.Color.blue())
                embed.description = (f"{member.mention}'s **Vault Shield** blocked the robbery attempt!\n\n"
                                    f"🚔 {ctx.author.mention} was caught and forced to pay a fine.")
                embed.add_field(name="Thief Paid", value=f"**{fine:,}** JC", inline=True)
                embed.add_field(name="Victim Restitution", value=f"**{restitution:,}** JC", inline=True)
                embed.add_field(name="Vault Fees", value=f"**{legal_fee:,}** JC", inline=True)
                embed.set_footer(text="The shield was consumed in the process.")
            else:
                embed = discord.Embed(title="🚔 CAUGHT IN THE ACT!", color=discord.Color.red())
                embed.description = (f"You were spotted and forced to pay a fine to {member.mention}!\n\n"
                                    f"💸 **You lost {fine:,} JC**.")
                embed.add_field(name="Victim Restitution", value=f"**{restitution:,}** JC", inline=True)
                embed.add_field(name="Legal Fees", value=f"**{legal_fee:,}** JC (Sent to Vault)", inline=True)
                embed.set_footer(text="Better luck next time, thief!")
            
            await ctx.send(embed=embed)

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
        # Dynamic rain rate (default 0.1% if not set)
        rate_str = get_setting('rain_rate', '0.1')
        try:
            rate = float(rate_str) / 100.0
        except ValueError:
            rate = 0.001
            
        if random.random() < rate:
            await self.start_rain(message.channel)

    @commands.command(name='rain')
    @commands.is_owner()
    async def rain_command(self, ctx: commands.Context):
        """Owner Only: Manually trigger a JC Rain 🌧️"""
        await self.start_rain(ctx.channel)

    @commands.command(name='rainrate')
    @commands.is_owner()
    async def rainrate_command(self, ctx: commands.Context, rate: float):
        """Owner Only: Set the percentage chance of random rain (0-100)."""
        if 0 <= rate <= 100:
            set_setting('rain_rate', str(rate))
            await ctx.send(f"✅ Random rain rate set to **{rate}%**.")
        else:
            await ctx.send("❌ Please provide a rate between 0 and 100.")

    @commands.command(name='rainamount')
    @commands.is_owner()
    async def rainamount_command(self, ctx: commands.Context, min_amt: int, max_amt: int):
        """Owner Only: Set the min/max JC awarded in a rain catch."""
        if 0 < min_amt <= max_amt:
            set_setting('rain_min', str(min_amt))
            set_setting('rain_max', str(max_amt))
            await ctx.send(f"✅ Rain catch range set to **{min_amt:,} - {max_amt:,} JC**.")
        else:
            await ctx.send("❌ Invalid range! Ensure 0 < min <= max.")

    @commands.command(name='raintotal')
    @commands.is_owner()
    async def raintotal_command(self, ctx: commands.Context, total: int):
        """Owner Only: Set the total JC pool for a rain event."""
        if total > 0:
            set_setting('rain_pool', str(total))
            await ctx.send(f"✅ Total rain pool set to **{total:,} JC**.")
        else:
            await ctx.send("❌ Please provide a positive amount.")

    async def start_rain(self, channel):
        # Fetch pool or use default
        try:
            pool = int(get_setting('rain_pool', '500'))
        except (ValueError, TypeError):
            pool = 500
            
        view = RainView(pool=pool)
        embed = discord.Embed(
            title="🌧️ IT'S RAINING JENCOINS!",
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
            name="🛡️ **Vault Shield** — `2,000 JC`",
            value="Protects you from **1** robbery attempt (100% block). **Max 3 in inventory!**\nUsage: `!buy shield`",
            inline=False
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
            bal = get_balance(uid)
            
            if bal < total_cost:
                await ctx.send(f"❌ You need **{total_cost:,} JC** for {count} Mystery Box(es)! You have **{bal:,} JC**.")
                return
            
            # Deduct total cost upfront
            add_balance(uid, -total_cost)
            log_transaction(uid, -total_cost, f"Bought {count}x Mystery Box")
            
            msg = await ctx.send(f"🎁 {ctx.author.mention} is opening **{count}** Mystery Box(es)...")
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
                if res < 0.01:
                    win = 50000
                    item = "🏆 Golden JC"
                    rarity = "LEGENDARY"
                    color = discord.Color.gold()
                elif res < 0.03:
                    win = 10000
                    item = "🥈 Silver Coin"
                    rarity = "EPIC"
                    color = discord.Color.purple()
                elif res < 0.30:
                    win = random.randint(1500, 3000)
                    item = None
                    rarity = "RARE"
                    color = discord.Color.blue()
                else:
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
            if get_balance(uid) < cost:
                await ctx.send(f"❌ You need **{cost:,} JC** for a Vault Shield!")
                return
            
            # Limit check: Max 3 shields
            shield_count = db_query("SELECT COUNT(*) FROM inventory WHERE user_id = ? AND item_name = 'Vault Shield'", (uid,), fetchone=True)
            if shield_count and shield_count[0] >= 3:
                await ctx.send("🛡️ You already have the maximum of **3 Vault Shields**! You must use one before buying more.")
                return
            
            add_balance(uid, -cost)
            add_item(uid, "Vault Shield", "Protection")
            log_transaction(uid, -cost, "Bought Vault Shield")
            
            await ctx.send(f"🛡️ {ctx.author.mention}, you purchased a **Vault Shield**! You now have **{shield_count[0]+1}/3** active shields. (1 Use each)")

        elif item_type in ['role', 'custom role']:
            cost = 500000
            fee = 25000 # 5% to Vault
            if get_balance(uid) < cost:
                await ctx.send(f"❌ You need at least **{cost:,} JC** to buy a Custom Role pass!")
                return
            
            # Check if they already own it
            if get_inventory_item(uid, "Custom Role Pass"):
                await ctx.send("🎟️ You already own a **Custom Role Pass**! Use `!setrole <name> <#hex>` to configure it.")
                return
                
            add_balance(uid, -cost)
            track_fee(fee)
            add_item(uid, "Custom Role Pass", "Perk", "")
            log_transaction(uid, -cost, "Bought Custom Role Pass")
            
            embed = discord.Embed(title="✨ Custom Role Pass Purchased!", color=discord.Color.magenta())
            embed.description = (f"Congratulations {ctx.author.mention}! You can now create your own custom role.\n\n"
                                 f"**Usage:** `{COMMAND_PREFIX}setrole #HexColor`\n"
                                 f"**Example:** `{COMMAND_PREFIX}setrole #FFD700`\n\n"
                                 f"*(You also paid **{fee:,} JC** in taxes to the Global Vault!)*")
            await ctx.send(embed=embed)

        else:
            await ctx.send("🛒 The shop is currently being restocked! Try `!buy box`, `!buy shield`, or `!buy role`.")

    @commands.command(name='inventory', aliases=['inv'])
    async def inv_command(self, ctx: commands.Context):
        """View your collected items."""
        uid = str(ctx.author.id)
        items = get_inventory(uid)
        
        if not items:
            await ctx.send("🎒 Your inventory is empty. Try opening some `!buy box`!")
            return

        # Group items
        item_list = {}
        for name, type in items:
            item_list[name] = item_list.get(name, 0) + 1
        
        display = "\n".join([f"• {name} x{count}" for name, count in item_list.items()])
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
                
        try:
            zero_perms = discord.Permissions.none()
            if my_role:
                # Edit existing role - Costs 450,000 JC
                edit_cost = 450000
                if get_balance(uid) < edit_cost:
                    await ctx.send(f"❌ Editing your custom role costs **{edit_cost:,} JC**. You don't have enough!")
                    return
                
                add_balance(uid, -edit_cost)
                log_transaction(uid, -edit_cost, "Edited Custom Role")
                
                await my_role.edit(name="JC", color=role_color, permissions=zero_perms, hoist=True, reason=f"Custom role edit by {ctx.author.name}")
                await ctx.send(f"✨ Successfully updated your custom role color to `{color_display}`! *(Cost: **{edit_cost:,} JC**)*")
            else:
                # Create new role and assign it (First time free)
                my_role = await ctx.guild.create_role(name="JC", color=role_color, permissions=zero_perms, hoist=True, reason=f"Custom role creation by {ctx.author.name}")
                await ctx.author.add_roles(my_role)
                db_query("UPDATE inventory SET item_data = ? WHERE user_id = ? AND item_name = 'Custom Role Pass'", (str(my_role.id), uid), commit=True)
                await ctx.send(f"✨ Successfully created and equipped your new custom role: **JC** with color `{color_display}`! (First time free)")
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to manage roles! Please make sure my bot role is higher than the custom roles and has the 'Manage Roles' permission.")
        except discord.HTTPException as e:
            await ctx.send(f"❌ An error occurred while managing the role. Make sure the name isn't too long or contains invalid characters. Details: {e}")

    @commands.command(name='sell')
    async def sell_command(self, ctx: commands.Context, *, item_name: str = None):
        """Sell a collectible item for JC. Usage: !sell [item name]"""
        if not item_name:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}sell [item name]` (e.g. `!sell golden jc`)")
            return

        uid = str(ctx.author.id)
        search_name = item_name.lower().strip()
        
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
        
        # Check if they own it
        if not get_inventory_item(uid, exact_name):
            await ctx.send(f"❌ You don't have any **{exact_name}** in your inventory to sell!")
            return
            
        # Execute Sale
        remove_item(uid, exact_name)
        new_bal = add_balance(uid, price)
        log_transaction(uid, price, f"Sold {exact_name}")
        
        embed = discord.Embed(
            title="🤝 Item Sold!",
            description=f"You successfully sold **1x {exact_name}** for **{price:,} JC**.",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"New Balance: {new_bal:,} JC")
        await ctx.send(embed=embed)

    # --- Admin Commands ---

    @commands.command(name='addcoins', aliases=['addjc'])
    @commands.is_owner()
    async def addcoins_command(self, ctx: commands.Context, member: discord.Member, amount: int):
        """Owner Only: Add JC to a user."""
        if not await validate_admin_amount(ctx, amount): return
        new_bal = add_balance(str(member.id), amount)
        log_transaction(str(member.id), amount, f"Admin Add (by {ctx.author.display_name})")
        await ctx.send(f"✅ Added **{amount:,}** JC to {member.mention}. New balance: **{new_bal:,}**.")

    @commands.command(name='takecoins', aliases=['removejc', 'takejc'])
    @commands.is_owner()
    async def takecoins_command(self, ctx: commands.Context, member: discord.Member, amount: int):
        """Owner Only: Remove JC from a user."""
        if not await validate_admin_amount(ctx, amount): return
        new_bal = add_balance(str(member.id), -amount)
        log_transaction(str(member.id), -amount, f"Admin Remove (by {ctx.author.display_name})")
        await ctx.send(f"✅ Removed **{amount:,}** JC from {member.mention}. New balance: **{new_bal:,}**.")

    @commands.command(name='grantvip', aliases=['givevip'])
    @commands.is_owner()
    async def grantvip_command(self, ctx: commands.Context, member: discord.Member, days: int = 30):
        """Owner Only: Grant VIP membership to a user."""
        if days <= 0:
            await ctx.send("❌ Please provide a positive number of days.")
            return
        
        set_vip(str(member.id), days)
        expiry = get_vip_expiry(str(member.id))
        log_transaction(str(member.id), 0, f"Admin VIP Grant ({days}d by {ctx.author.display_name})")
        
        await ctx.send(f"👑 Granted **{days} days** of VIP to {member.mention}!\n📅 Expires: <t:{expiry}:F> (<t:{expiry}:R>)")

# --- Blackjack Game Logic ---

class BlackjackView(discord.ui.View):
    def __init__(self, ctx, bet):
        super().__init__(timeout=60)
        self.ctx = ctx
        self.bet = bet
        self.deck = self.create_deck()
        self.player_hand = [self.draw_card(), self.draw_card()]
        self.dealer_hand = [self.draw_card(), self.draw_card()]
        self.message = None
        self.is_natural = False
        self.game_over = False

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
            # Payout logic: 3:2 for natural, 1:1 for standard
            multiplier = 1.5 if self.is_natural else 1.0
            payout = int(self.bet * multiplier)
            
            new_bal = add_balance(uid, payout)
            log_transaction(uid, payout, "Blackjack Win" + (" (Natural)" if self.is_natural else ""))
            color = discord.Color.green()
        elif win is False:
            new_bal = add_balance(uid, -self.bet)
            log_transaction(uid, -self.bet, "Blackjack Loss")
            color = discord.Color.red()
        else: # Tie
            new_bal = get_balance(uid)
            log_transaction(uid, 0, "Blackjack Push")
            color = discord.Color.blue()

        embed = self.make_embed(finished=True)
        embed.title = f"🃏 {result_text}"
        embed.color = color
        embed.add_field(name="ResultBalance", value=f"**{new_bal:,}** JC", inline=False)
        
        if self.message:
            await self.message.edit(embed=embed, view=None)
        else:
            await self.ctx.send(embed=embed)

    async def on_timeout(self):
        if not self.game_over:
            await self.finish_game("Game Timed Out (Bust)", win=False)

# --- Rain Event Logic ---

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
            r_min = int(get_setting('rain_min', '20'))
            r_max = int(get_setting('rain_max', '100'))
        except (ValueError, TypeError):
            r_min, r_max = 20, 100

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
            desc = f"🌈 The rain has stopped! Here are our lucky catchers:\n\n{w_list}"

        embed = discord.Embed(title="☀️ Rain Over", description=desc, color=discord.Color.gold())
        await self.message.edit(embed=embed, view=None)

    async def on_timeout(self):
        await self.finish_rain()


async def setup(bot):
    await bot.add_cog(Economy(bot))

