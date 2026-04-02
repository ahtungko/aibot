# cogs/fun.py — Cat, cat facts, game deals, dictionary, AFK, and Roast commands
import io
import time
import urllib.parse
import discord
import aiohttp
from discord.ext import commands
from config import COMMAND_PREFIX
from utils.storage import load_afk, save_afk
from utils.helpers import format_duration


class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.afk_users = load_afk()

    # --- AFK logic (called from jbot.py on_message) ---

    def get_afk_users(self):
        return self.afk_users

    def clear_afk(self, user_id: str):
        if user_id in self.afk_users:
            info = self.afk_users.pop(user_id)
            save_afk(self.afk_users)
            return info
        return None

    @commands.command(name='afk')
    async def afk_command(self, ctx: commands.Context, *, reason: str = "AFK"):
        """Set your AFK status. Auto-clears when you send a message."""
        uid = str(ctx.author.id)
        self.afk_users[uid] = {"reason": reason, "since": time.time()}
        save_afk(self.afk_users)
        await ctx.send(f"💤 {ctx.author.mention} is now AFK: *{reason}*")

    @commands.command(name='c')
    async def cat(self, ctx: commands.Context):
        API_URL = "https://api.thecatapi.com/v1/images/search"
        try:
            async with ctx.typing():
                async with self.bot.http_session.get(API_URL) as response:
                    response.raise_for_status()
                    data = await response.json()
                if not data:
                    await ctx.send("The cat API returned no cats. 😿")
                    return
                embed = discord.Embed(title="Meow! Here's a cat for you 🐱", color=discord.Color.blue())
                embed.set_image(url=data[0]['url'])
                await ctx.send(embed=embed)
        except Exception as e:
            print(f"Error in !c command: {e}")
            await ctx.send("Sorry, an unexpected error stopped me from getting a cat. 😿")

    @commands.command(name='cf')
    async def cat_fact(self, ctx: commands.Context):
        API_URL = "https://meowfacts.herokuapp.com/"
        try:
            async with ctx.typing():
                async with self.bot.http_session.get(API_URL) as response:
                    response.raise_for_status()
                    data = await response.json()
                if 'data' not in data or not data['data']:
                    await ctx.send("The cat fact API is empty. 😿")
                    return
                embed = discord.Embed(title="🐱 Did You Know?", description=data['data'][0], color=discord.Color.green())
                await ctx.send(embed=embed)
        except Exception as e:
            print(f"Error in !cf command: {e}")
            await ctx.send("Sorry, an unexpected error stopped me from getting a cat fact. 😿")

    @commands.command(name='deals')
    async def deals(self, ctx: commands.Context):
        API_URL = "https://www.cheapshark.com/api/1.0/deals?storeID=1&sortBy=Savings&pageSize=5"
        try:
            async with ctx.typing():
                async with self.bot.http_session.get(API_URL) as response:
                    response.raise_for_status()
                    deals_data = await response.json()
                if not deals_data:
                    await ctx.send("I couldn't find any hot deals on Steam right now.")
                    return
                embed = discord.Embed(title="🔥 Top 5 Steam Deals Right Now", description="Here are the hottest deals, sorted by discount!", color=discord.Color.from_rgb(10, 29, 45))
                for deal in deals_data:
                    deal_link = f"https://www.cheapshark.com/redirect?dealID={deal.get('dealID')}"
                    value_text = (f"**Price:** ~~${deal.get('normalPrice', 'N/A')}~~ → **${deal.get('salePrice', 'N/A')}**\n" f"**Discount:** `{round(float(deal.get('savings', 0)))}%`\n" f"[Link to Deal]({deal_link})")
                    embed.add_field(name=f"**{deal.get('title', 'Unknown Game')}**", value=value_text, inline=False)
                embed.set_thumbnail(url="https://store.cloudflare.steamstatic.com/public/shared/images/header/logo_steam.svg?t=962016")
                await ctx.send(embed=embed)
        except Exception as e:
            print(f"Error in !deals command: {e}")
            await ctx.send("Sorry, an unexpected error stopped me from getting game deals. 😿")

    @commands.command(name='price')
    async def price(self, ctx: commands.Context, *, game_name: str = None):
        if not game_name:
            await ctx.send("Please tell me which game you want to check! Usage: `!price [game name]`")
            return
        formatted_game_name = urllib.parse.quote(game_name)
        DEAL_API_URL = f"https://www.cheapshark.com/api/1.0/deals?storeID=1&onSale=1&exact=1&title={formatted_game_name}"
        try:
            async with ctx.typing():
                async with self.bot.http_session.get(DEAL_API_URL) as response:
                    response.raise_for_status()
                    deals_data = await response.json()
                if deals_data:
                    deal = deals_data[0]
                    steam_store_link = f"https://store.steampowered.com/app/{deal.get('steamAppID')}"
                    embed = discord.Embed(title=f"🔥 Deal Found for: {deal.get('title', 'Unknown Game')}", url=steam_store_link, color=discord.Color.green())
                    if deal.get('thumb'):
                        embed.set_thumbnail(url=deal.get('thumb'))
                    embed.add_field(name="Price", value=f"~~${deal.get('normalPrice', 'N/A')}~~ → **${deal.get('salePrice', 'N/A')}**", inline=True)
                    embed.add_field(name="Discount", value=f"**{round(float(deal.get('savings', 0)))}% OFF**", inline=True)
                    embed.add_field(name="Metacritic Score", value=f"`{deal.get('metacriticScore', 'N/A')}`", inline=True)
                    await ctx.send(embed=embed)
                else:
                    lookup_url = f"https://www.cheapshark.com/api/1.0/games?title={formatted_game_name}&exact=1"
                    async with self.bot.http_session.get(lookup_url) as lookup_response:
                        lookup_response.raise_for_status()
                        game_data = await lookup_response.json()
                    if not game_data:
                        await ctx.send(f"Sorry, I couldn't find a game with the exact name **'{game_name}'**.")
                        return
                    game_info = game_data[0]
                    steam_store_link = f"https://store.steampowered.com/app/{game_info.get('steamAppID')}"
                    embed = discord.Embed(title=f"Price Check for: {game_info.get('external', 'Unknown Game')}", url=steam_store_link, color=discord.Color.light_grey())
                    if game_info.get('thumb'):
                        embed.set_thumbnail(url=game_info.get('thumb'))
                    embed.add_field(name="Status", value="This game is **not currently on sale** on Steam.", inline=False)
                    embed.add_field(name="Current Price", value=f"**${game_info.get('cheapest', 'N/A')}**", inline=False)
                    await ctx.send(embed=embed)
        except Exception as e:
            print(f"Error in !price command: {e}")
            await ctx.send("Sorry, an unexpected error stopped me from checking the price. 😿")

    @commands.command(name='dict')
    async def dict_command(self, ctx: commands.Context, *, word: str = None):
        """Provides definitions for a given word."""
        if not word:
            await ctx.send("Please provide a word to look up. Usage: `!dict [word]`")
            return

        API_URL = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"

        async with ctx.typing():
            try:
                async with self.bot.http_session.get(API_URL) as response:
                    if response.status == 404:
                        await ctx.send(f"Sorry, I couldn't find a definition for **'{word}'**. Please check the spelling.")
                        return
                    response.raise_for_status()
                    data = await response.json()

                if isinstance(data, dict) and data.get("title") == "No Definitions Found":
                    await ctx.send(f"Sorry, I couldn't find a definition for **'{word}'**. Please check the spelling.")
                    return

                word_data = data[0]
                word_text = word_data.get('word', 'N/A')

                embed = discord.Embed(title=f"**{word_text.title()}**", color=discord.Color.light_grey())

                phonetic_text = None
                audio_url = None
                if 'phonetics' in word_data and word_data['phonetics']:
                    for p in word_data['phonetics']:
                        if p.get('text'):
                            phonetic_text = p.get('text')
                            break
                    for p in word_data['phonetics']:
                        if p.get('audio'):
                            audio_url = p.get('audio')
                            break

                if phonetic_text:
                    embed.description = f"**Phonetic:** `{phonetic_text}`"

                if 'meanings' in word_data:
                    for meaning in word_data['meanings']:
                        part_of_speech = meaning.get('partOfSpeech', 'N/A').title()
                        definitions = []
                        for i, definition_info in enumerate(meaning.get('definitions', [])):
                            if i < 3:
                                definition_text = definition_info.get('definition', 'No definition available.')
                                definitions.append(f"**{i+1}.** {definition_text}")
                        if definitions:
                            embed.add_field(name=f"As a {part_of_speech}", value="\n".join(definitions), inline=False)

                audio_file = None
                if audio_url:
                    try:
                        async with self.bot.http_session.get(audio_url) as audio_response:
                            audio_response.raise_for_status()
                            if 'audio' in audio_response.headers.get('Content-Type', ''):
                                audio_data = io.BytesIO(await audio_response.read())
                                audio_file = discord.File(fp=audio_data, filename=f"{word_text}_pronunciation.mp3")
                    except Exception as e:
                        print(f"Failed to download audio file: {e}")

                await ctx.send(embed=embed, file=audio_file)

            except aiohttp.ClientResponseError as e:
                if e.status == 404:
                    await ctx.send(f"Sorry, I couldn't find a definition for **'{word}'**. Please check the spelling.")
                else:
                    await ctx.send(f"An HTTP error occurred: {e}")
            except Exception as e:
                print(f"Error in !dict command: {e}")
                await ctx.send("An unexpected error occurred while looking up the word. 😿")

    # --- Roast Me (NEW!) ---

    @commands.command(name='roast')
    async def roast_command(self, ctx: commands.Context, member: discord.Member = None):
        """Roast a user based on their recent messages!"""
        if member is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}roast @user`")
            return

        if member.id == self.bot.user.id:
            await ctx.send("Nice try, but I'm not roasting myself. 😎")
            return

        # Get the AI cog for generating the roast
        ai_cog = self.bot.get_cog("AI")
        if ai_cog is None or ai_cog.http_client is None:
            await ctx.send("❌ AI is offline. Can't roast without brains!")
            return

        try:
            async with ctx.typing():
                # Fetch last 15 messages from this user in this channel
                user_messages = []
                async for msg in ctx.channel.history(limit=200):
                    if msg.author.id == member.id and msg.content:
                        user_messages.append(msg.content[:150])
                        if len(user_messages) >= 15:
                            break

                if len(user_messages) < 3:
                    await ctx.send(f"😐 **{member.display_name}** hasn't said enough in this channel to roast. They need to talk more!")
                    return

                messages_text = "\n".join(f"- {m}" for m in user_messages)

                prompt = (
                    f"Based on the following recent messages from a Discord user named '{member.display_name}', "
                    f"write a funny, lighthearted roast about them. Keep it PG-13, playful, and not genuinely hurtful. "
                    f"Reference specific things they actually said. Keep it to 2-3 sentences max.\n\n"
                    f"Their recent messages:\n{messages_text}"
                )

                roast_text = await ai_cog.call_ai(
                    [{"role": "user", "content": prompt}],
                    instructions="You are a witty comedian. Write a short, funny roast. Be playful, not mean."
                )

                if not roast_text:
                    await ctx.send("❌ AI couldn't come up with a roast. They must be too boring to roast.")
                    return

                embed = discord.Embed(
                    title=f"🔥 Roast of {member.display_name}",
                    description=roast_text,
                    color=discord.Color.red()
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_footer(text=f"Requested by {ctx.author.display_name} • Don't take it personally!")
                await ctx.send(embed=embed)

        except Exception as e:
            print(f"Error in !roast command: {e}")
            await ctx.send("❌ Something went wrong while roasting. The target might be un-roastable.")


async def setup(bot):
    await bot.add_cog(Fun(bot))
