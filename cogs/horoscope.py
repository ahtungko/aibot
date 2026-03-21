# cogs/horoscope.py — Horoscope commands + daily task + UI views
import discord
from discord import ui
from discord.ext import commands, tasks
from datetime import datetime, time as dt_time, timezone, timedelta
from utils.storage import load_user_data, save_user_data
from config import COMMAND_PREFIX


# --- UI Components ---

async def handle_timezone_selection(interaction: discord.Interaction, select_item: ui.Select, offset_str: str):
    user_id = str(interaction.user.id)
    users = await load_user_data()
    user_data = users.get(user_id)
    sign = getattr(select_item.view, 'sign', None)

    if user_data and not sign:
        if isinstance(user_data, str):
            users[user_id] = {"sign": user_data, "timezone_offset": offset_str}
        else:
            user_data['timezone_offset'] = offset_str
    else:
        if not sign:
            await interaction.response.edit_message(content="Something went wrong. Please start over with `!reg`.", view=None)
            return
        users[user_id] = {"sign": sign, "timezone_offset": offset_str}

    await save_user_data(users)

    for item in select_item.view.children:
        item.disabled = True

    await interaction.response.edit_message(content=f"✅ Your timezone has been set to **UTC{offset_str}**! All set.", view=select_item.view)


class TimezoneSelectA(ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=f"UTC{i:+d}", value=str(i)) for i in range(-12, 1)]
        super().__init__(placeholder="Timezones (UTC-12 to UTC+0)", options=options)

    async def callback(self, interaction: discord.Interaction):
        await handle_timezone_selection(interaction, self, self.values[0])


class TimezoneSelectB(ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=f"UTC{i:+d}", value=str(i)) for i in range(1, 15)]
        super().__init__(placeholder="Timezones (UTC+1 to UTC+14)", options=options)

    async def callback(self, interaction: discord.Interaction):
        await handle_timezone_selection(interaction, self, self.values[0])


class TimezoneSelectC(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="UTC-09:30", value="-9.5"), discord.SelectOption(label="UTC-03:30", value="-3.5"),
            discord.SelectOption(label="UTC+03:30", value="3.5"), discord.SelectOption(label="UTC+04:30", value="4.5"),
            discord.SelectOption(label="UTC+05:30 (India)", value="5.5"), discord.SelectOption(label="UTC+05:45 (Nepal)", value="5.75"),
            discord.SelectOption(label="UTC+06:30 (Myanmar)", value="6.5"), discord.SelectOption(label="UTC+08:45 (W. Australia)", value="8.75"),
            discord.SelectOption(label="UTC+09:30 (C. Australia)", value="9.5"), discord.SelectOption(label="UTC+10:30 (Lord Howe Is.)", value="10.5"),
        ]
        super().__init__(placeholder="Non-Integer Timezones (India, etc.)", options=options)

    async def callback(self, interaction: discord.Interaction):
        await handle_timezone_selection(interaction, self, self.values[0])


class TimezoneSelectionView(ui.View):
    def __init__(self, author: discord.User, sign: str = None):
        super().__init__(timeout=120)
        self.author = author
        self.sign = sign
        self.add_item(TimezoneSelectA())
        self.add_item(TimezoneSelectB())
        self.add_item(TimezoneSelectC())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("This selection menu is not for you.", ephemeral=True)
            return False
        return True


class ZodiacSelect(ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Aries", emoji="♈"), discord.SelectOption(label="Taurus", emoji="♉"),
            discord.SelectOption(label="Gemini", emoji="♊"), discord.SelectOption(label="Cancer", emoji="♋"),
            discord.SelectOption(label="Leo", emoji="♌"), discord.SelectOption(label="Virgo", emoji="♍"),
            discord.SelectOption(label="Libra", emoji="♎"), discord.SelectOption(label="Scorpio", emoji="♏"),
            discord.SelectOption(label="Sagittarius", emoji="♐"), discord.SelectOption(label="Capricorn", emoji="♑"),
            discord.SelectOption(label="Aquarius", emoji="♒"), discord.SelectOption(label="Pisces", emoji="♓"),
        ]
        super().__init__(placeholder="Choose your zodiac sign...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)
        selected_sign = self.values[0]
        users = await load_user_data()
        user_data = users.get(user_id)
        if user_data:
            if isinstance(user_data, str):
                users[user_id] = {"sign": selected_sign, "timezone_offset": "+0"}
            elif isinstance(user_data, dict):
                user_data['sign'] = selected_sign
            await save_user_data(users)
            await interaction.response.edit_message(content=f"✅ Your zodiac sign has been updated to **{selected_sign}**!", view=None)
        else:
            view = TimezoneSelectionView(author=interaction.user, sign=selected_sign)
            await interaction.response.edit_message(content="Great! Now, please select your timezone offset from the dropdowns below.", view=view)


class ZodiacSelectionView(ui.View):
    def __init__(self, author: discord.User, *, timeout=120):
        super().__init__(timeout=timeout)
        self.author = author
        self.add_item(ZodiacSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("This selection menu is not for you.", ephemeral=True)
            return False
        return True


# --- Helpers ---

def create_horoscope_embed(sign_name, data, request_date):
    horoscope_date = data.get('current_date', 'N/A')
    description = data.get('description', 'No horoscope data found for today.')
    compatibility = data.get('compatibility', 'N/A').title()
    mood = data.get('mood', 'N/A').title()
    color = data.get('color', 'N/A').title()
    lucky_number = data.get('lucky_number', 'N/A')
    lucky_time = data.get('lucky_time', 'N/A')
    date_range = data.get('date_range', '')
    embed = discord.Embed(title=f"✨ Daily Horoscope for {sign_name.title()} ✨", description=f"_{description}_", color=discord.Color.purple())
    embed.set_footer(text=f"Horoscope For: {horoscope_date} | Your Date: {request_date} | Range: {date_range}")
    embed.add_field(name="Mood", value=mood, inline=True)
    embed.add_field(name="Compatibility", value=compatibility, inline=True)
    embed.add_field(name="Lucky Color", value=color, inline=True)
    embed.add_field(name="Lucky Number", value=str(lucky_number), inline=True)
    embed.add_field(name="Lucky Time", value=lucky_time, inline=True)
    return embed


class Horoscope(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        if not self.send_daily_horoscopes.is_running():
            self.send_daily_horoscopes.start()
            print("Started the daily horoscope background task.")

    async def cog_unload(self):
        self.send_daily_horoscopes.cancel()

    async def fetch_and_send_horoscope(self, destination, sign, user: discord.User = None):
        users = await load_user_data()
        user_id = str(user.id)
        user_data = users.get(user_id)
        offset_str = '+0'
        if isinstance(user_data, dict):
            offset_str = user_data.get('timezone_offset', '+0')
        user_timezone = timezone(timedelta(hours=float(offset_str)))
        today_date = datetime.now(user_timezone).date().isoformat()
        url = f"https://api.aistrology.beandev.xyz/v1?sign={sign.lower()}&date={today_date}"
        try:
            mention_text = f"{user.mention}, " if user else ""
            if isinstance(destination, (commands.Context, discord.TextChannel, discord.Interaction)):
                await destination.send(f"{mention_text}fetching today's horoscope for **{sign}**...")
            async with self.bot.http_session.get(url) as response:
                response.raise_for_status()
                horoscope_data_list = await response.json()
            if horoscope_data_list and isinstance(horoscope_data_list, list):
                data = horoscope_data_list[0]
                embed = create_horoscope_embed(sign, data, today_date)
                if hasattr(destination, 'send'):
                    await destination.send(embed=embed)
                return True
            else:
                if hasattr(destination, 'send'):
                    await destination.send("Sorry, I couldn't retrieve the horoscope right now.")
                return False
        except Exception as e:
            print(f"An error occurred in fetch_and_send_horoscope for sign {sign}: {e}")
            if hasattr(destination, 'send'):
                await destination.send("An unexpected error occurred while fetching your horoscope.")
            return False

    # --- Daily Task ---

    @tasks.loop(time=dt_time(hour=0, minute=0, tzinfo=timezone.utc))
    async def send_daily_horoscopes(self):
        print(f"[{datetime.now()}] Running daily horoscope task...")
        users = await load_user_data()
        if not users:
            print("No registered users to send horoscopes to.")
            return
        for user_id, data in users.items():
            try:
                sign, offset_str = None, "+0"
                if isinstance(data, str):
                    sign = data
                elif isinstance(data, dict):
                    sign = data.get("sign")
                    offset_str = data.get('timezone_offset', '+0')
                if not sign:
                    continue
                user_timezone = timezone(timedelta(hours=float(offset_str)))
                user_today_date = datetime.now(user_timezone).date().isoformat()
                url = f"https://api.aistrology.beandev.xyz/v1?sign={sign.lower()}&date={user_today_date}"
                async with self.bot.http_session.get(url) as response:
                    response.raise_for_status()
                    horoscope_data_list = await response.json()
                if horoscope_data_list and isinstance(horoscope_data_list, list):
                    horoscope_data = horoscope_data_list[0]
                    user = await self.bot.fetch_user(int(user_id))
                    embed = create_horoscope_embed(sign, horoscope_data, user_today_date)
                    await user.send(embed=embed)
                    print(f"Sent horoscope to {user.name} ({user_id}) for sign {sign}")
            except Exception as e:
                print(f"An error occurred while processing user {user_id}: {e}")
        print("Daily horoscope task finished.")

    @send_daily_horoscopes.before_loop
    async def before_daily_task(self):
        await self.bot.wait_until_ready()

    # --- Commands ---

    @commands.command(name='reg')
    async def reg(self, ctx: commands.Context):
        if str(ctx.author.id) in await load_user_data():
            await ctx.send(f"You are already registered, {ctx.author.mention}! Use `{COMMAND_PREFIX}mod` to change your sign or `{COMMAND_PREFIX}modtz` to change your timezone.")
            return
        view = ZodiacSelectionView(author=ctx.author)
        await ctx.send(f"Welcome, {ctx.author.mention}! Please select your zodiac sign to get started:", view=view)

    @commands.command(name='mod')
    async def mod(self, ctx: commands.Context):
        if str(ctx.author.id) not in await load_user_data():
            await ctx.send(f"You haven't registered yet, {ctx.author.mention}. Please use `{COMMAND_PREFIX}reg` to get started.")
            return
        view = ZodiacSelectionView(author=ctx.author)
        await ctx.send(f"{ctx.author.mention}, please select your new zodiac sign:", view=view)

    @commands.command(name='modtz')
    async def modtz(self, ctx: commands.Context):
        if str(ctx.author.id) not in await load_user_data():
            await ctx.send(f"You need to register with `{COMMAND_PREFIX}reg` first before changing your timezone.")
            return
        view = TimezoneSelectionView(author=ctx.author)
        await ctx.send("Please select your new timezone offset from the dropdowns:", view=view)

    @commands.command(name='remove')
    async def remove_record(self, ctx: commands.Context):
        user_id = str(ctx.author.id)
        users = await load_user_data()
        if user_id in users:
            del users[user_id]
            await save_user_data(users)
            await ctx.send(f"✅ Your record has been deleted, {ctx.author.mention}. Use `{COMMAND_PREFIX}reg` to register again.")
        else:
            await ctx.send(f"You do not have a registered sign to delete, {ctx.author.mention}.")

    @commands.command(name='list')
    async def list_horoscope(self, ctx: commands.Context):
        user_id = str(ctx.author.id)
        users = await load_user_data()
        user_data = users.get(user_id)
        sign = None
        if isinstance(user_data, str):
            sign = user_data
        elif isinstance(user_data, dict):
            sign = user_data.get("sign")
        if sign:
            await self.fetch_and_send_horoscope(ctx, sign, user=ctx.author)
        else:
            await ctx.send(f"You haven't registered your sign yet, {ctx.author.mention}. Use `{COMMAND_PREFIX}reg` to get started.")

    @commands.command(name='olist')
    @commands.is_owner()
    async def olist(self, ctx: commands.Context):
        """Lists all users registered for daily horoscopes."""
        users = await load_user_data()
        if not users:
            await ctx.send("No users have registered for horoscopes yet.")
            return
        embed = discord.Embed(title="Horoscope Registered User List", color=discord.Color.gold())
        output_lines = []
        count = 1
        for user_id, data in users.items():
            try:
                user = await self.bot.fetch_user(int(user_id))
                user_display = f"{user.name}#{user.discriminator}"
            except discord.NotFound:
                user_display = "Unknown User (ID not found)"
            except Exception:
                user_display = "Error Fetching User"
            sign, timezone_str = "N/A", "N/A"
            if isinstance(data, str):
                sign, timezone_str = data, "Not Set (Old Format)"
            elif isinstance(data, dict):
                sign = data.get('sign', 'N/A')
                offset = data.get('timezone_offset', 'N/A')
                timezone_str = f"UTC{offset}"
            output_lines.append(f"**{count}. {user_display}** `(ID: {user_id})`\n  - **Sign:** {sign}\n  - **Timezone:** {timezone_str}")
            count += 1
        description_text = "\n\n".join(output_lines)
        if len(description_text) > 4000:
            description_text = description_text[:4000] + "\n\n... (list truncated)"
        embed.description = description_text
        embed.set_footer(text=f"Total Registered Users: {len(users)}")
        await ctx.send(embed=embed)

    @commands.command(name='test')
    @commands.is_owner()
    async def test_daily_horoscopes(self, ctx):
        await ctx.message.add_reaction('🧪')
        owner_id = str(ctx.author.id)
        users = await load_user_data()
        owner_data = users.get(owner_id)
        sign = None
        if isinstance(owner_data, str):
            sign = owner_data
        elif isinstance(owner_data, dict):
            sign = owner_data.get("sign")
        if sign:
            await ctx.author.send(f"✅ Running a personal test for your sign: **{sign}**. You should receive your horoscope message next.")
            await self.fetch_and_send_horoscope(ctx.author, sign, user=ctx.author)
        else:
            await ctx.author.send(f"⚠️ You are not registered for horoscopes. Please use `{COMMAND_PREFIX}reg` first to test this feature.")


async def setup(bot):
    await bot.add_cog(Horoscope(bot))
