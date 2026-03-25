# cogs/precious.py — Gold, silver commands + gold price status task
import discord
from discord.ext import commands, tasks
from datetime import datetime, timezone
import sqlite3
import os
from config import TROY_OUNCE_TO_GRAMS

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'economy.db')


class Precious(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        if not self.update_gold_price_status.is_running():
            self.update_gold_price_status.start()
            print("Started the live gold price status update task.")

    async def cog_unload(self):
        self.update_gold_price_status.cancel()

    def _get_headers_cookies(self):
        cookies = {'wcid': 'D95hVgSMso1SAAAC', 'react_component_complete': 'true'}
        headers = {
            'accept': '*/*', 'accept-language': 'en-US,en-GB;q=0.9,en;q=0.8',
            'referer': 'https://goldprice.org/spot-gold.html', 'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors', 'sec-fetch-site': 'same-origin',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36',
        }
        return cookies, headers

    @tasks.loop(seconds=120)
    async def update_gold_price_status(self):
        currency_code = "USD"
        cookies, headers = self._get_headers_cookies()
        price_api_url = f"https://data-asg.goldprice.org/dbXRates/{currency_code}"

        try:
            async with self.bot.http_session.get(price_api_url, cookies=cookies, headers=headers) as price_response:
                price_response.raise_for_status()
                price_json = await price_response.json()

            price_data = price_json.get("items")[0]
            xau_price_gram = price_data.get('xauPrice', 0) / TROY_OUNCE_TO_GRAMS
            status_text = f"Gold: {xau_price_gram:,.2f} USD/g"

            activity = discord.Activity(type=discord.ActivityType.watching, name=status_text)
            await self.bot.change_presence(activity=activity)
            
            # Cache the price for the dashboard
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = ?",
                    ('last_gold_price', str(xau_price_gram), str(xau_price_gram))
                )
                conn.commit()
                conn.close()
            except Exception as db_e:
                print(f"Failed to cache gold price: {db_e}")

            print(f"Updated bot status: {status_text}")

        except Exception as e:
            print(f"An error occurred in the status update task: {e}")
            try:
                error_activity = discord.Activity(type=discord.ActivityType.watching, name="Price API Error")
                await self.bot.change_presence(activity=error_activity)
            except Exception:
                pass

    @update_gold_price_status.before_loop
    async def before_status_task(self):
        await self.bot.wait_until_ready()

    @commands.command(name='gold', aliases=['g'])
    async def gold(self, ctx: commands.Context, currency: str = "USD"):
        """Fetches and displays the live price and performance of gold."""
        currency_code = currency.upper()
        cookies, headers = self._get_headers_cookies()
        price_api_url = f"https://data-asg.goldprice.org/dbXRates/{currency_code}"
        gold_perf_api_url = f"https://goldprice.org/performance-json/gold-price-performance-{currency_code}.json"

        msg = await ctx.send(f"Fetching live gold prices for **{currency_code}**...")

        try:
            async with self.bot.http_session.get(price_api_url, cookies=cookies, headers=headers) as price_response:
                price_response.raise_for_status()
                price_json = await price_response.json()

            async with self.bot.http_session.get(gold_perf_api_url, cookies=cookies, headers=headers) as gold_perf_response:
                gold_perf_response.raise_for_status()
                gold_perf_json = await gold_perf_response.json()

            price_data = price_json.get("items")[0]
            unix_timestamp = price_json.get("ts", 0) // 1000
            xau_pc_change = price_data.get('pcXau', 0)
            gold_perf_list = gold_perf_json.get("Change", [])
            gold_perf_data = {k: v for item in gold_perf_list for k, v in item.items()}
            xau_price_gram = price_data.get('xauPrice', 0) / TROY_OUNCE_TO_GRAMS
            xau_change_gram = price_data.get('chgXau', 0) / TROY_OUNCE_TO_GRAMS

            embed = discord.Embed(
                title=f"🥇 Live Gold Price ({currency_code})",
                description=f"Price is per gram in **{currency_code}**.",
                color=discord.Color.gold(),
                timestamp=datetime.fromtimestamp(unix_timestamp, tz=timezone.utc)
            )
            embed.set_footer(text="Data from goldprice.org")

            xau_sign = "+" if xau_change_gram >= 0 else ""
            xau_emoji = "📈" if xau_change_gram >= 0 else "📉"
            xau_value = f"**Price:** `{xau_price_gram:,.4f}`\n**Change:** {xau_emoji} `{xau_sign}{xau_change_gram:,.4f} ({xau_sign}{xau_pc_change:.2f}%)"
            embed.add_field(name="Gold (XAU)", value=xau_value, inline=False)

            g_today_sign = "+" if xau_pc_change >= 0 else ""
            g_today_perf = f"{g_today_sign}{xau_pc_change:.2f}%"
            g_30d = gold_perf_data.get('30 Days', {}).get('percentage', 'N/A')
            g_6m = gold_perf_data.get('6 Months', {}).get('percentage', 'N/A')
            g_1y = gold_perf_data.get('1 Year', {}).get('percentage', 'N/A')
            g_perf_str = f"**Today:** `{g_today_perf}` | **30D:** `{g_30d}`\n**6M:** `{g_6m}` | **1Y:** `{g_1y}`"
            embed.add_field(name=f"Gold Performance ({currency_code})", value=g_perf_str, inline=False)

            await msg.edit(content=None, embed=embed)
        except Exception as e:
            await msg.edit(content=f"An error occurred while fetching gold prices for **{currency_code}**.")
            print(f"Error in !gold command: {e}")

    @commands.command(name='silver', aliases=['s'])
    async def silver(self, ctx: commands.Context, currency: str = "USD"):
        """Fetches and displays the live price and performance of silver."""
        currency_code = currency.upper()
        cookies, headers = self._get_headers_cookies()
        price_api_url = f"https://data-asg.goldprice.org/dbXRates/{currency_code}"
        silver_perf_api_url = f"https://goldprice.org/performance-json/silver-price-performance-{currency_code}.json"

        msg = await ctx.send(f"Fetching live silver prices for **{currency_code}**...")

        try:
            async with self.bot.http_session.get(price_api_url, cookies=cookies, headers=headers) as price_response:
                price_response.raise_for_status()
                price_json = await price_response.json()

            async with self.bot.http_session.get(silver_perf_api_url, cookies=cookies, headers=headers) as silver_perf_response:
                silver_perf_response.raise_for_status()
                silver_perf_json = await silver_perf_response.json()

            price_data = price_json.get("items")[0]
            unix_timestamp = price_json.get("ts", 0) // 1000
            xag_pc_change = price_data.get('pcXag', 0)
            silver_perf_list = silver_perf_json.get("Change", [])
            silver_perf_data = {k: v for item in silver_perf_list for k, v in item.items()}
            xag_price_gram = price_data.get('xagPrice', 0) / TROY_OUNCE_TO_GRAMS
            xag_change_gram = price_data.get('chgXag', 0) / TROY_OUNCE_TO_GRAMS

            embed = discord.Embed(
                title=f"🥈 Live Silver Price ({currency_code})",
                description=f"Price is per gram in **{currency_code}**.",
                color=discord.Color.light_grey(),
                timestamp=datetime.fromtimestamp(unix_timestamp, tz=timezone.utc)
            )
            embed.set_footer(text="Data from goldprice.org")

            xag_sign = "+" if xag_change_gram >= 0 else ""
            xag_emoji = "📈" if xag_change_gram >= 0 else "📉"
            xag_value = f"**Price:** `{xag_price_gram:,.4f}`\n**Change:** {xag_emoji} `{xag_sign}{xag_change_gram:,.4f} ({xag_sign}{xag_pc_change:.2f}%)"
            embed.add_field(name="Silver (XAG)", value=xag_value, inline=False)

            s_today_sign = "+" if xag_pc_change >= 0 else ""
            s_today_perf = f"{s_today_sign}{xag_pc_change:.2f}%"
            s_30d = silver_perf_data.get('30 Days', {}).get('percentage', 'N/A')
            s_6m = silver_perf_data.get('6 Months', {}).get('percentage', 'N/A')
            s_1y = silver_perf_data.get('1 Year', {}).get('percentage', 'N/A')
            s_perf_str = f"**Today:** `{s_today_perf}` | **30D:** `{s_30d}`\n**6M:** `{s_6m}` | **1Y:** `{s_1y}`"
            embed.add_field(name=f"Silver Performance ({currency_code})", value=s_perf_str, inline=False)

            await msg.edit(content=None, embed=embed)
        except Exception as e:
            await msg.edit(content=f"An error occurred while fetching silver prices for **{currency_code}**.")
            print(f"Error in !silver command: {e}")


async def setup(bot):
    await bot.add_cog(Precious(bot))
