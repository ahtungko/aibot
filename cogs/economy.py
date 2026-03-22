# cogs/economy.py — JenCoin economy: wallet, daily, gambling, transfers
import os
import random
import sqlite3
import time
import discord
from discord.ext import commands
from config import COMMAND_PREFIX

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
WORK_MAX = 50
STARTING_BALANCE = 0


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("CREATE TABLE IF NOT EXISTS wallets (user_id TEXT PRIMARY KEY, balance INTEGER DEFAULT 0, last_daily TEXT DEFAULT '', last_work TEXT DEFAULT '')")
    conn.execute("CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT, amount INTEGER, type TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)")
    
    # Migration: Add last_work column if it doesn't exist (for existing databases)
    try:
        conn.execute("ALTER TABLE wallets ADD COLUMN last_work TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass # Column already exists
        
    conn.commit()
    return conn


def log_transaction(user_id: str, amount: int, trans_type: str):
    conn = get_db()
    conn.execute("INSERT INTO transactions (user_id, amount, type) VALUES (?, ?, ?)", (user_id, amount, trans_type))
    conn.commit()
    conn.close()


def get_balance(user_id: str) -> int:
    conn = get_db()
    row = conn.execute("SELECT balance FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row[0] if row else STARTING_BALANCE


def set_balance(user_id: str, amount: int):
    conn = get_db()
    conn.execute(
        "INSERT INTO wallets (user_id, balance) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET balance = ?",
        (user_id, amount, amount)
    )
    conn.commit()
    conn.close()


def add_balance(user_id: str, amount: int) -> int:
    bal = get_balance(user_id)
    new_bal = max(0, bal + amount)
    set_balance(user_id, new_bal)
    return new_bal


def get_last_daily(user_id: str) -> str:
    conn = get_db()
    row = conn.execute("SELECT last_daily FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row[0] if row else ""


def set_last_daily(user_id: str, date_str: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO wallets (user_id, last_daily) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET last_daily = ?",
        (user_id, date_str, date_str)
    )
    conn.commit()
    conn.close()

def get_last_work(user_id: str) -> str:
    conn = get_db()
    row = conn.execute("SELECT last_work FROM wallets WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row[0] if row else ""

def set_last_work(user_id: str, ts_str: str):
    conn = get_db()
    conn.execute("INSERT INTO wallets (user_id, last_work) VALUES (?, ?) ON CONFLICT(user_id) DO UPDATE SET last_work = ?", (user_id, ts_str, ts_str))
    conn.commit()
    conn.close()


def get_top_balances(limit=10) -> list:
    conn = get_db()
    rows = conn.execute("SELECT user_id, balance FROM wallets ORDER BY balance DESC LIMIT ?", (limit,)).fetchall()
    conn.close()
    return rows


class Economy(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='bal', aliases=['balance', 'wallet'])
    async def balance_command(self, ctx: commands.Context, member: discord.Member = None):
        """Check your (or someone else's) JenCoin balance."""
        target = member or ctx.author
        bal = get_balance(str(target.id))
        embed = discord.Embed(
            title=f"💰 {target.display_name}'s Wallet",
            description=f"**{bal:,}** JenCoins",
            color=discord.Color.gold()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        await ctx.send(embed=embed)

    @commands.command(name='daily')
    async def daily_command(self, ctx: commands.Context):
        """Claim your daily JenCoins!"""
        uid = str(ctx.author.id)
        from datetime import datetime, timezone, timedelta
        # Use GMT+8 as the reset timezone (same as check-in)
        gmt8 = timezone(timedelta(hours=8))
        today = datetime.now(gmt8).strftime("%Y-%m-%d")
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
            description=f"💵 {ctx.author.mention} received **{total:,}** JenCoins!",
            color=discord.Color.green()
        )
        embed.add_field(name="New Balance", value=f"**{new_bal:,}** JenCoins", inline=False)
        await ctx.send(embed=embed)

    @commands.command(name='work', aliases=['job'])
    async def work_command(self, ctx: commands.Context):
        """Work for some JenCoins! (1 hour cooldown)"""
        uid = str(ctx.author.id)
        from datetime import datetime
        now = datetime.now()
        last_str = get_last_work(uid)
        
        if last_str:
            last_ts = datetime.fromisoformat(last_str)
            diff = (now - last_ts).total_seconds()
            if diff < WORK_COOLDOWN:
                remaining = int(WORK_COOLDOWN - diff)
                mins = remaining // 60
                secs = remaining % 60
                await ctx.send(f"⏳ {ctx.author.mention}, you're exhausted! Come back in **{mins}m {secs}s**.")
                return

        reward = random.randint(WORK_MIN, WORK_MAX)
        new_bal = add_balance(uid, reward)
        set_last_work(uid, now.isoformat())
        log_transaction(uid, reward, "Work Payment")

        jobs = [
            "cleaned the server kitchen", "coded a new feature", "moderated a spicy channel",
            "organized the bot's database", "helped a new member", "fixed a bunch of bugs",
            "wrote some elegant documentation", "designed a new logo", "streamed for 2 hours"
        ]
        job = random.choice(jobs)

        embed = discord.Embed(
            title="⚒️ Hard Work Pays Off!",
            description=f"{ctx.author.mention}, you **{job}** and earned **{reward}** JenCoins!",
            color=discord.Color.blue()
        )
        embed.add_field(name="Wallet", value=f"**{new_bal:,}** JenCoins", inline=False)
        await ctx.send(embed=embed)

    @commands.command(name='give', aliases=['pay', 'transfer'])
    async def give_command(self, ctx: commands.Context, member: discord.Member = None, amount: int = None):
        """Give JenCoins to another user."""
        if not member or amount is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}give @user [amount]`")
            return
        if member.id == ctx.author.id:
            await ctx.send("You can't give coins to yourself!")
            return
        if member.bot:
            await ctx.send("You can't give coins to a bot!")
            return
        if amount <= 0:
            await ctx.send("Amount must be positive!")
            return

        sender_bal = get_balance(str(ctx.author.id))
        if sender_bal < amount:
            await ctx.send(f"❌ You only have **{sender_bal:,}** JenCoins.")
            return

        add_balance(str(ctx.author.id), -amount)
        new_receiver = add_balance(str(member.id), amount)

        log_transaction(str(ctx.author.id), -amount, f"Transfer to {member.display_name}")
        log_transaction(str(member.id), amount, f"Transfer from {ctx.author.display_name}")

        embed = discord.Embed(
            title="💸 Transfer Complete",
            description=f"{ctx.author.mention} → {member.mention}\n**{amount:,}** JenCoins",
            color=discord.Color.blue()
        )
        embed.add_field(name=f"{member.display_name}'s Balance", value=f"**{new_receiver:,}**", inline=True)
        await ctx.send(embed=embed)

    @commands.command(name='top', aliases=['rich'])
    async def top_command(self, ctx: commands.Context):
        """Show the richest users."""
        rows = get_top_balances(10)
        if not rows:
            await ctx.send("📭 No one has any JenCoins yet! Use `!daily` to get started.")
            return

        embed = discord.Embed(title="🏦 JenCoin Leaderboard", color=discord.Color.gold())
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
    async def flip_command(self, ctx: commands.Context, amount: int = None, side: str = None):
        """Flip a coin! Guess 'h' or 't'. Win = double, Lose = nothing."""
        if amount is None or side is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}flip [amount] [h/t]` — bet your JenCoins on heads or tails!")
            return
        if amount <= 0:
            await ctx.send("Bet must be positive!")
            return

        side = side.lower()
        if side not in ['h', 'heads', 't', 'tails']:
            await ctx.send("Please pick `h` (heads) or `t` (tails)!")
            return

        uid = str(ctx.author.id)
        bal = get_balance(uid)
        if bal < amount:
            await ctx.send(f"❌ You only have **{bal:,}** JenCoins.")
            return

        outcome = random.choice(['h', 't'])
        user_choice = 'h' if side in ['h', 'heads'] else 't'
        won = (user_choice == outcome)

        outcome_full = "Heads" if outcome == 'h' else "Tails"

        if won:
            winnings = amount
            new_bal = add_balance(uid, winnings)
            log_transaction(uid, winnings, "Flip Win")
            embed = discord.Embed(
                title=f"🪙 Coin Flip — {outcome_full}!",
                description=f"🎉 You guessed right!\nYou won **{winnings:,}** JenCoins!",
                color=discord.Color.green()
            )
        else:
            new_bal = add_balance(uid, -amount)
            log_transaction(uid, -amount, "Flip Loss")
            embed = discord.Embed(
                title=f"🪙 Coin Flip — {outcome_full}",
                description=f"😢 You guessed wrong.\nYou lost **{amount:,}** JenCoins.",
                color=discord.Color.red()
            )

        embed.add_field(name="Balance", value=f"**{new_bal:,}** JenCoins", inline=False)
        embed.set_footer(text=f"Bet: {amount:,} JC | Picked: {side}")
        await ctx.send(embed=embed)

    @commands.command(name='slots', aliases=['slot'])
    async def slots_command(self, ctx: commands.Context, amount: int = None):
        """Spin the slot machine! 🎰"""
        if amount is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}slots [amount]` — bet your JenCoins!")
            return
        if amount <= 0:
            await ctx.send("Bet must be positive!")
            return

        uid = str(ctx.author.id)
        bal = get_balance(uid)
        if bal < amount:
            await ctx.send(f"❌ You only have **{bal:,}** JenCoins.")
            return

        # Spin 3 reels
        reels = [random.choice(SLOT_EMOJIS) for _ in range(3)]
        reel_display = " | ".join(reels)

        # Determine outcome
        if reels[0] == reels[1] == reels[2]:
            # Jackpot! 3 of a kind
            multiplier = SLOT_PAYOUTS.get(reels[0], 2)
            winnings = amount * multiplier
            new_bal = add_balance(uid, winnings)
            log_transaction(uid, winnings, f"Slots Win ({reels[0]})")
            if reels[0] == "7️⃣":
                title = "🎰 JACKPOT!!! 🎰"
                desc = f"**[ {reel_display} ]**\n\n🤑 **MEGA JACKPOT!** You won **{winnings:,}** JenCoins! (x{multiplier})"
            else:
                title = "🎰 THREE OF A KIND!"
                desc = f"**[ {reel_display} ]**\n\n🎉 You won **{winnings:,}** JenCoins! (x{multiplier})"
            color = discord.Color.gold()
        elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
            # 2 of a kind — return the bet (net 0)
            new_bal = get_balance(uid)  # no change
            log_transaction(uid, 0, "Slots Draw")
            title = "🎰 Two of a Kind"
            desc = f"**[ {reel_display} ]**\n\n😌 Two match! You got your bet back."
            color = discord.Color.blue()
        else:
            # No match — lose bet
            new_bal = add_balance(uid, -amount)
            log_transaction(uid, -amount, "Slots Loss")
            title = "🎰 No Match"
            desc = f"**[ {reel_display} ]**\n\n💨 No luck this time. You lost **{amount:,}** JenCoins."
            color = discord.Color.red()

        embed = discord.Embed(title=title, description=desc, color=color)
        embed.add_field(name="Balance", value=f"**{new_bal:,}** JenCoins", inline=False)
        embed.set_footer(text=f"Bet: {amount:,} JC")
        await ctx.send(embed=embed)

    @commands.command(name='history', aliases=['logs', 'stats'])
    async def history_command(self, ctx: commands.Context):
        """View your last 5 economy transactions."""
        uid = str(ctx.author.id)
        conn = get_db()
        rows = conn.execute(
            "SELECT amount, type, timestamp FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 5",
            (uid,)
        ).fetchall()
        conn.close()

        if not rows:
            await ctx.send("📭 You haven't made any transactions yet!")
            return

        embed = discord.Embed(title=f"📜 {ctx.author.display_name}'s Recent Activity", color=discord.Color.blue())
        
        history_text = ""
        for amount, trans_type, timestamp in rows:
            sign = "+" if amount > 0 else ""
            fmt_amount = f"{sign}{amount:,}" if amount != 0 else "0"
            # Extract just date/time from timestamp
            ts = timestamp.split(".")[0] if "." in timestamp else timestamp
            history_text += f"`{ts}` | **{trans_type}**: `{fmt_amount} JC`\n"

        embed.description = history_text
        bal = get_balance(uid)
        embed.set_footer(text=f"Current Balance: {bal:,} JC")
        await ctx.send(embed=embed)

    # --- Admin Commands ---

    @commands.command(name='addcoins', aliases=['addjc'])
    @commands.has_permissions(administrator=True)
    async def addcoins_command(self, ctx: commands.Context, member: discord.Member, amount: int):
        """Admin Only: Add JenCoins to a user."""
        if amount <= 0:
            await ctx.send("Amount must be positive.")
            return
        
        new_bal = add_balance(str(member.id), amount)
        log_transaction(str(member.id), amount, f"Admin Add (by {ctx.author.display_name})")
        
        await ctx.send(f"✅ Added **{amount:,}** JenCoins to {member.mention}. New balance: **{new_bal:,}**.")

    @commands.command(name='takecoins', aliases=['removejc', 'takejc'])
    @commands.has_permissions(administrator=True)
    async def takecoins_command(self, ctx: commands.Context, member: discord.Member, amount: int):
        """Admin Only: Remove JenCoins from a user."""
        if amount <= 0:
            await ctx.send("Amount must be positive.")
            return
        
        new_bal = add_balance(str(member.id), -amount)
        log_transaction(str(member.id), -amount, f"Admin Remove (by {ctx.author.display_name})")
        
        await ctx.send(f"✅ Removed **{amount:,}** JenCoins from {member.mention}. New balance: **{new_bal:,}**.")


async def setup(bot):
    await bot.add_cog(Economy(bot))
