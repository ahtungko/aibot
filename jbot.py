# jbot.py — Slim entry point: bot setup, cog loading, on_message routing, help
import time
import discord
import aiohttp
from discord.ext import commands
from config import DISCORD_BOT_TOKEN, COMMAND_PREFIX, OWNER_ID
from utils.helpers import format_duration

# --- Bot Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.messages = True

class JenBot(commands.Bot):
    async def setup_hook(self):
        self.http_session = aiohttp.ClientSession()
        for cog in COGS:
            try:
                await self.load_extension(cog)
                print(f"  Loaded cog: {cog}")
            except Exception as e:
                print(f"  Failed to load cog {cog}: {e}")

    async def close(self):
        if hasattr(self, 'http_session') and self.http_session and not self.http_session.closed:
            await self.http_session.close()
            print("Closed aiohttp session.")
        await super().close()

bot = JenBot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None, owner_id=OWNER_ID)

# --- Cog Loading ---
COGS = [
    'cogs.ai',
    'cogs.checkin',
    'cogs.currency',
    'cogs.fun',
    'cogs.horoscope',
    'cogs.music',
    'cogs.pins',
    'cogs.precious',
]


# --- Events ---

@bot.event
async def on_ready():
    print(f'Bot is ready! Logged in as {bot.user.name} (ID: {bot.user.id})')
    print(f"Command Prefix: '{COMMAND_PREFIX}' | Mention: @{bot.user.name}")
    print('------')


@bot.event
async def on_disconnect():
    print("Bot disconnected from Discord. Reconnecting...")


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    if isinstance(message.channel, discord.DMChannel):
        if message.content.strip():
            try:
                await message.channel.send("I operate in server channels.")
            except discord.errors.Forbidden:
                print(f"Could not send a DM reply to {message.author}")
        return

    # Auto-recreate session if closed
    if bot.http_session is None or bot.http_session.closed:
        bot.http_session = aiohttp.ClientSession()

    # AFK: auto-clear if AFK user sends a message
    fun_cog = bot.get_cog("Fun")
    if fun_cog:
        uid = str(message.author.id)
        afk_info = fun_cog.clear_afk(uid)
        if afk_info:
            try:
                await message.channel.send(f"👋 Welcome back {message.author.mention}! You were AFK for {format_duration(time.time() - afk_info['since'])}.")
            except Exception:
                pass

        # AFK: notify if someone mentions an AFK user
        afk_users = fun_cog.get_afk_users()
        for mentioned in message.mentions:
            mid = str(mentioned.id)
            if mid in afk_users:
                reason = afk_users[mid].get('reason', 'AFK')
                since = afk_users[mid].get('since', time.time())
                await message.channel.send(f"{message.author.mention}, 💤 **{mentioned.display_name}** is AFK: *{reason}* (since {format_duration(time.time() - since)} ago)")

    # Process commands FIRST (before mention check)
    ctx = await bot.get_context(message)
    if ctx.valid:
        await bot.process_commands(message)
        return

    # AI mention handler
    if bot.user.mentioned_in(message):
        ai_cog = bot.get_cog("AI")
        if ai_cog:
            await ai_cog.handle_ai_mention(message)
        return

    # Currency fallback (dynamic currency codes like !usd, !eur, etc.)
    if message.content.startswith(COMMAND_PREFIX):
        currency_cog = bot.get_cog("Currency")
        if currency_cog:
            await currency_cog.handle_currency_command(message)
        return


# --- Help Command ---

@bot.command(name='help')
async def help_command(ctx):
    p = COMMAND_PREFIX
    embed = discord.Embed(title=f"{bot.user.name} Help", description="This bot provides AI Chat, Currency Exchange, and Horoscope functionalities.", color=discord.Color.purple())
    embed.add_field(name="🤖 AI Chat Functionality", value=f"To chat with the AI, simply mention the bot (`@{bot.user.name}`) followed by your question.", inline=False)
    embed.add_field(name=f"💱 Currency Exchange (Prefix: `{p}`)", value=(f"**Get Daily Rates:** `{p}usd`\n" f"**Convert (Daily Rate):** `{p}usd 100 myr`\n" f"**Convert (LIVE Rate):** `{p}liverate` or `{p}r [amount] <source> <target>`\n\n" f"Click `📈` to see a graph for daily rate conversions."), inline=False)
    embed.add_field(name=f"✨ Daily Horoscope (Prefix: `{p}`)", value=(f"**Register:** `{p}reg`\n" f"**Modify Sign:** `{p}mod`\n" f"**Modify Timezone:** `{p}modtz`\n" f"**Remove your record:** `{p}remove`\n" f"**Show in channel:** `{p}list`\n\n" f"Receive a daily horoscope in your timezone!"), inline=False)
    embed.add_field(name=f"🎵 Music Download (Prefix: `{p}`)", value=(f"**Search for a song:** `{p}ss [query]`\n" f"**Download a song from results:** `{p}d [number]`"), inline=False)
    embed.add_field(name=f"🐱 Fun Commands (Prefix: `{p}`)", value=(f"**Cat Picture:** `{p}c`\n" f"**Cat Fact:** `{p}cf`\n" f"**Roast someone:** `{p}roast @user`"), inline=False)
    embed.add_field(name=f"🎮 Game Deals (Prefix: `{p}`)", value=(f"**Top Steam Deals:** `{p}deals`\n" f"**Check Game Price:** `{p}price [game name]`"), inline=False)
    embed.add_field(name=f"📚 Utility Commands (Prefix: `{p}`)", value=(f"**Dictionary:** `{p}dict [word]`\n" f"**Gold Price:** `{p}gold [currency]`\n" f"**Silver Price:** `{p}silver [currency]`"), inline=False)
    embed.add_field(name=f"📝 Daily Check-in (Prefix: `{p}`)", value=(f"**Check in:** `{p}ck [note]`\n" f"**Your streak:** `{p}streak`\n" f"**Leaderboard:** `{p}lb`\n" f"Once per day, resets at midnight GMT+8."), inline=False)
    embed.add_field(name=f"🧹 AI Tools", value=(f"**Summarize chat:** `{p}tldr [count]`\n" f"**Clear AI memory:** `{p}clear`\n" f"The AI remembers your last few messages."), inline=False)
    embed.add_field(name=f"💤 AFK", value=(f"**Set AFK:** `{p}afk [reason]`\n" f"Auto-clears when you send a message."), inline=False)
    embed.add_field(name=f"📌 Bookmarks", value=(f"**Pin:** Reply to a message with `{p}pin`\n" f"**View pins:** `{p}pins`\n" f"**Remove pin:** `{p}unpin [number]`"), inline=False)

    if ctx.author.id == bot.owner_id:
        embed.add_field(name=f"👑 Owner Commands", value=f"**List all horoscope users:** `{p}olist`\n**Test your horoscope DM:** `{p}test`", inline=False)
    embed.set_footer(text="Made with ❤️ by Jenny")
    await ctx.send(embed=embed)


# --- Main ---
if __name__ == '__main__':
    try:
        bot.run(DISCORD_BOT_TOKEN)
    except discord.LoginFailure:
        print("FATAL ERROR: Invalid Discord bot token. Please check your .env file.")
    except Exception as e:
        print(f"An unexpected error occurred while starting the bot: {e}")
