# jbot.py — Slim entry point: bot setup, cog loading, on_message routing, help
import time
import asyncio
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
    'cogs.economy',
    'cogs.fun',
    'cogs.horoscope',
    'cogs.music',
    'cogs.pins',
    'cogs.precious',
    'cogs.minigames',
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

    # AI mention handler (non-blocking)
    if bot.user.mentioned_in(message):
        ai_cog = bot.get_cog("AI")
        if ai_cog:
            asyncio.create_task(ai_cog.handle_ai_mention(message))
        return

    # Currency fallback (dynamic currency codes like !usd, !eur, etc.)
    if message.content.startswith(COMMAND_PREFIX):
        currency_cog = bot.get_cog("Currency")
        if currency_cog:
            await currency_cog.handle_currency_command(message)
        return


# --- Help Command ---

class HelpDropdown(discord.ui.Select):
    def __init__(self, ctx, prefix):
        self.ctx = ctx
        self.p = prefix
        
        options = [
            discord.SelectOption(label="AI & Utilities", description="Chat, Summarize, AFK, Bookmarks", emoji="🤖", value="ai"),
            discord.SelectOption(label="Economy & Banking", description="Bal, Daily, Work, Dep/With, Top, PF", emoji="💰", value="eco"),
            discord.SelectOption(label="Gambling & Duels", description="Flip, Slots, BJ, Duel, BJDuel, Rob", emoji="🎲", value="gam"),
            discord.SelectOption(label="Shop & Inventory", description="Shop, Buy, Inv, Sell, SetRole", emoji="🏪", value="shop"),
            discord.SelectOption(label="Market & VIP", description="Gold, Silver, Currency, VIP Membership", emoji="📈", value="finance"),
            discord.SelectOption(label="Daily & Social", description="Check-in, Horoscope, Roasts, Cat Facts", emoji="🌟", value="social"),
            discord.SelectOption(label="Media & Music", description="Music, Steam Deals", emoji="🎵", value="media"),
            discord.SelectOption(label="Minigames", description="Horse Race, AI Mystery", emoji="🎮", value="minigames"),
        ]
        
        if ctx.author.id == ctx.bot.owner_id or ctx.author.guild_permissions.administrator:
            options.append(discord.SelectOption(label="Admin Setup", description="Owner/Admin Commands", emoji="👑", value="admin"))
            
        super().__init__(placeholder="Select a category...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message("❌ This help menu is not for you! Type `!help` to open your own.", ephemeral=True)
            return

        p = self.p
        val = self.values[0]
        bot_name = self.ctx.bot.user.name
        
        embed = discord.Embed(color=discord.Color.purple())
        
        if val == "ai":
            embed.title = "🤖 AI & Utilities"
            embed.add_field(name="AI Chat", value=f"Simply mention the bot (`@{bot_name}`) to chat or ask questions.", inline=False)
            embed.add_field(name="AI Tools", value=f"`{p}tldr [count]` - Summarize chat history\n`{p}clear` - Reset your AI conversation memory", inline=False)
            embed.add_field(name="Utilities", value=f"`{p}dict [word]` - Dictionary lookup\n`{p}afk [reason]` - Set your AFK status", inline=False)
            embed.add_field(name="Bookmarks", value=f"Reply to a message with `{p}pin` to save it\n`{p}pins` - View your pins\n`{p}unpin [num]` - Delete a pin", inline=False)
            
        elif val == "eco":
            embed.title = "💰 Economy & Banking"
            embed.add_field(name="Earning JC", value=f"`{p}daily` - Open daily crate\n`{p}work` - Earn JC (Progressive Tax 5-12%)\n`{p}fish` - Cast a line (Cost 50, CD 15s)\n`{p}top` - Leaderboard (Total Wealth)", inline=False)
            embed.add_field(name="Banking & Transfers", value=f"`{p}bal [@user]` - Check balance\n`{p}dep [amount]` - Secure JC in Bank\n`{p}with [amount]` - Withdraw JC\n`{p}give @user [amt]` - Transfer JC", inline=False)
            embed.add_field(name="Trading & Stats", value=f"`{p}pf` - Portfolio & Net Worth\n`{p}history` - Transaction logs\n`{p}vault` - Global community pool", inline=False)

        elif val == "gam":
            embed.title = "🎲 Gambling & Duels"
            embed.add_field(name="PVP Duels ⚔️", value=f"`{p}duel @user [bet]` - Coin Flip Duel", inline=False)
            embed.add_field(name="Gambling 🎲", value=f"`{p}flip [bet] [h/t]` - Coin Flip\n`{p}slots [bet]` - Slot Machine\n`{p}bj [bet]` - Blackjack\n`{p}crash [bet]` - Crash Game 🚀", inline=False)
            embed.add_field(name="Crimes 🥷", value=f"`{p}rob @user` - Steal JC & Gold!", inline=False)
            
        elif val == "shop":
            embed.title = "🏪 Shop & Collection"
            embed.add_field(name="Shopping", value=f"`{p}shop` - Browse upgrades & charms\n`{p}buy [item]` - Purchase item\n`{p}buy box` - Mystery Box (1k JC)", inline=False)
            embed.add_field(name="Collection", value=f"`{p}inv` - View items & perks\n`{p}sell [item]` - Sell rare collectibles\n`{p}setrole [color]` - Custom Role Color", inline=False)
            
        elif val == "finance":
            embed.title = "📈 Market & Finance"
            embed.add_field(name="Gold Market 📈", value=f"`{p}bg [JC]` - Buy Gold (Live rate)\n`{p}sg [grams]` - Sell Gold (Live rate)\n`{p}gold [currency]` - Current spot price", inline=False)
            embed.add_field(name="Currency & VIP", value=f"`{p}liverate` - Real-time conversion\n`{p}vip` - Get VIP perks (10k JC/mo)\n`{p}silver [curr]` - Silver Price", inline=False)

        elif val == "social":
            embed.title = "🌟 Daily & Social"
            embed.add_field(name="Daily Check-in", value=f"`{p}ck [note]` - Check in\n`{p}streak` - View streak\n`{p}lb` - Leaderboard", inline=False)
            embed.add_field(name="Horoscope", value=f"`{p}reg` - Register\n`{p}mod` - Modify sign\n`{p}modtz` - Modify Timezone\n`{p}list` - Show in channel\n`{p}remove` - Remove record", inline=False)
            embed.add_field(name="Fun", value=f"`{p}c` - Cat Picture\n`{p}cf` - Cat Fact\n`{p}roast @user` - AI Roast", inline=False)
            
        elif val == "minigames":
            embed.title = "🎮 Interactive Minigames"
            embed.description = "Play games against the bot or community!"
            embed.add_field(name="Global Horse Race 🏇", value=f"`{p}race` - Start sign-ups (30s)\n`{p}bet <#1-5> <amt>` - Bet on a horse (Payout 4.5x!)", inline=False)
            embed.add_field(name="AI Word Scramble 🧩", value=f"`{p}scramble` - Unscramble a word for 10-50 JC! (**5 JC Fee**)", inline=False)
            embed.add_field(name="AI Murder Mystery 🕵️‍♂️", value=f"`{p}mystery` - Solve an AI crime for a massive bounty!", inline=False)
            embed.set_footer(text="More minigames coming soon!")

        elif val == "media":
            embed.title = "🎵 Media & Games"
            embed.add_field(name="Music", value=f"`{p}ss [query]` - Search song\n`{p}d [number]` - Download song", inline=False)
            embed.add_field(name="Games", value=f"`{p}deals` - Top Steam Deals\n`{p}price [game]` - Check Game Price", inline=False)
            
        elif val == "admin":
            embed.title = "👑 Admin Setup"
            embed.description = "Owner/Administrator Commands"
            embed.add_field(name="Economy Controls", value=f"`{p}addcoins @user [amt]` - Give coins\n`{p}takecoins @user [amt]` - Take coins\n`{p}grantvip @user [days]` - Give VIP", inline=False)
            embed.add_field(name="Taxman System 🕵️", value=f"`{p}settaxmantoggle [on/off]` - Enable daily tax\n`{p}settaxmanpercent [%]` - Set tax rate\n`{p}taxstatus` - System status & timer\n`{p}settaxchannel [#ch]` - Set announcement channel", inline=False)
            embed.add_field(name="Rain & Notices", value=f"`{p}rainrate [%]` - Set rain chance\n`{p}setnoticechannel [#ch]` - Set notice channel\n`{p}setnotice [msg]` - Post announcement", inline=False)
            embed.add_field(name="Mystery Box", value=f"`{p}setbox [rates...]` - Start loot event\n`{p}setboxchannel [#ch]` - Set box channel", inline=False)
            embed.add_field(name="System", value=f"`{p}olist` - List active users\n`{p}test` - Test horoscope delivery", inline=False)
            
        embed.set_footer(text="Made with ❤️ by Jenny")
        await interaction.response.edit_message(embed=embed)


class HelpView(discord.ui.View):
    def __init__(self, ctx, prefix):
        super().__init__(timeout=120)
        self.add_item(HelpDropdown(ctx, prefix))
        self.message = None

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


@bot.command(name='help')
async def help_command(ctx):
    embed = discord.Embed(
        title=f"{bot.user.name} Help Menu 📖",
        description="Welcome to the help menu! Please select a category from the dropdown below to view the available commands.\n\n*(Note: This menu will expire after 2 minutes of inactivity)*",
        color=discord.Color.purple()
    )
    embed.set_thumbnail(url=bot.user.display_avatar.url if bot.user.display_avatar else None)
    embed.set_footer(text="Made with ❤️ by Jenny")
    
    view = HelpView(ctx, COMMAND_PREFIX)
    view.message = await ctx.send(embed=embed, view=view)


# --- Main ---
if __name__ == '__main__':
    try:
        bot.run(DISCORD_BOT_TOKEN)
    except discord.LoginFailure:
        print("FATAL ERROR: Invalid Discord bot token. Please check your .env file.")
    except Exception as e:
        print(f"An unexpected error occurred while starting the bot: {e}")
