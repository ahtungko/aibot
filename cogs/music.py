# cogs/music.py — Music search and download commands
import io
import urllib.parse
import discord
from discord.ext import commands
from config import API_SEARCH_URLS, API_DOWNLOAD_URLS


class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.search_results_cache = {}  # {user_id: [song1, song2, ...]}

    @commands.command(name='ss', aliases=['searchsong'])
    async def search_song(self, ctx: commands.Context, *, query: str = None):
        """Searches for a song and displays the top 10 results."""
        if query is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}ss [query]`")
            return
        user_id = ctx.author.id
        self.search_results_cache[user_id] = []

        async with ctx.typing():
            try:
                url = f"{API_SEARCH_URLS['joox']}?key={urllib.parse.quote(query)}"
                async with self.bot.http_session.get(url) as response:
                    response.raise_for_status()
                    data = await response.json(content_type=None)

                if not data.get('data', {}).get('data'):
                    await ctx.send("No songs found for that query. Please try again.")
                    return

                songs = data['data']['data'][:10]
                self.search_results_cache[user_id] = songs

                embed = discord.Embed(
                    title="🎧 Search Results",
                    description=f"Found **{len(songs)}** songs. Use `!d [number]` to download one.",
                    color=discord.Color.dark_green()
                )

                for i, song in enumerate(songs):
                    song_title = song.get('title', 'Unknown Title')
                    artist_names = ', '.join([s.get('name') for s in song.get('singers', []) if s.get('name')]) or 'Unknown Artist'
                    album_name = song.get('album', {}).get('name', 'N/A')
                    duration_sec = song.get('duration', 0)
                    minutes, seconds = divmod(duration_sec, 60)
                    duration_str = f"{minutes}:{seconds:02d}"
                    platform = song.get('platform', 'N/A').title()

                    embed.add_field(
                        name=f"{i+1}. {song_title}",
                        value=f"**Artist:** {artist_names}\n**Album:** {album_name}\n**Duration:** `{duration_str}` | **Source:** `{platform}`",
                        inline=False
                    )

                await ctx.send(embed=embed)

            except Exception as e:
                print(f"Error in searchsong command: {e}")
                await ctx.send("Sorry, an error occurred while searching for music.")

    @commands.command(name='d', aliases=['downloadsong'])
    async def download_song(self, ctx: commands.Context, song_number: int = None):
        """Downloads a song from the previous search results."""
        if song_number is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}d [number]` — choose a number from your search results.")
            return
        user_id = ctx.author.id
        if user_id not in self.search_results_cache or not self.search_results_cache[user_id]:
            await ctx.send("Please use `!ss [query]` first to get a list of songs.")
            return

        if not 1 <= song_number <= len(self.search_results_cache[user_id]):
            await ctx.send("Invalid song number. Please choose a number from the search results.")
            return

        song = self.search_results_cache[user_id][song_number - 1]
        song_id = song.get('ID')
        song_title = song.get('title', 'song')
        song_artist = ', '.join([s.get('name') for s in song.get('singers', []) if s.get('name')]) or 'Unknown Artist'

        MAX_FILE_SIZE = 25 * 1024 * 1024

        best_link = None
        links = song.get('fileLinks', [])
        sorted_links = sorted(links, key=lambda x: x.get('quality', 0), reverse=True)

        for link in sorted_links:
            if link.get('size', float('inf')) <= MAX_FILE_SIZE:
                best_link = link
                break

        if not best_link:
            await ctx.send("No download format found that fits within Discord's file size limit.")
            return

        quality = best_link.get('quality')
        file_format = best_link.get('format')
        file_size_mb = best_link.get('size', 0) / (1024 * 1024)

        download_url = f"{API_DOWNLOAD_URLS['joox']}?ID={song_id}&quality={quality}&format={file_format}"

        await ctx.send(f"Downloading **{song_title}** by **{song_artist}**...\nQuality: `{quality}` | Size: `{file_size_mb:.2f} MB`")

        try:
            async with self.bot.http_session.get(download_url) as response:
                response.raise_for_status()
                audio_data = io.BytesIO(await response.read())
            audio_file = discord.File(fp=audio_data, filename=f"{song_title}_{song_artist}.{file_format}")
            await ctx.send(file=audio_file)
            await ctx.send(f"✅ Download complete!")
            self.search_results_cache.pop(user_id, None)

        except Exception as e:
            print(f"Error downloading song: {e}")
            await ctx.send("Sorry, I encountered an error while downloading the song.")


async def setup(bot):
    await bot.add_cog(Music(bot))
