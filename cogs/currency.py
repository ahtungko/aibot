# cogs/currency.py — Currency exchange + liverate + graph view
import re
import asyncio
import discord
from discord import ui
from discord.ext import commands
from datetime import datetime
from config import BASE_CURRENCY_API_URL, WISE_SANDBOX_TOKEN, COMMAND_PREFIX
from utils.helpers import generate_history_graph
import aiohttp


class HistoricalGraphView(ui.View):
    def __init__(self, bot, base_currency: str, target_currency: str, *, timeout=180):
        super().__init__(timeout=timeout)
        self.bot = bot
        self.base_currency = base_currency
        self.target_currency = target_currency

    @ui.button(label="Show History", style=discord.ButtonStyle.primary, emoji="📈")
    async def show_graph(self, interaction: discord.Interaction, button: ui.Button):
        button.disabled = True
        button.label = "Generating Graph..."
        await interaction.response.edit_message(view=self)
        api_url = f"https://currencyhistoryapi.tinaleewx99.workers.dev/?base={self.base_currency}&symbols={self.target_currency}"
        try:
            async with self.bot.http_session.get(api_url) as response:
                response.raise_for_status()
                data = await response.json()
            rates_over_time = data.get('rates', {})
            if not rates_over_time:
                await interaction.followup.send("Sorry, I couldn't find any historical data for this currency pair.", ephemeral=True)
                return
            sorted_dates = sorted(rates_over_time.keys())
            rates_for_target = [rates_over_time[date][self.target_currency] for date in sorted_dates]
            num_days_with_data = len(sorted_dates)
            loop = asyncio.get_running_loop()
            graph_buffer = await loop.run_in_executor(None, generate_history_graph, sorted_dates, rates_for_target, self.base_currency, self.target_currency, num_days_with_data)
            graph_file = discord.File(graph_buffer, filename=f"{self.base_currency}-{self.target_currency}_history.png")
            await interaction.followup.send(file=graph_file)
        except Exception as e:
            print(f"An error occurred during graph generation: {e}")
            await interaction.followup.send("I'm sorry, an unexpected error occurred while creating the graph.", ephemeral=True)


class Currency(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def fetch_exchange_rates(self, base_currency: str, target_currency: str = None):
        params = {'base': base_currency.upper()}
        if target_currency:
            params['to'] = target_currency.upper()
        try:
            async with self.bot.http_session.get(BASE_CURRENCY_API_URL, params=params) as response:
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientError as e:
            print(f"Error fetching exchange rates from API: {e}")
            return None

    async def handle_currency_command(self, message):
        full_command_parts = message.content[len(COMMAND_PREFIX):].strip().split()
        if not full_command_parts:
            return
        base_currency, amount, target_currency = None, 1.0, None
        first_arg = full_command_parts[0]
        # Restrict to strictly 3-letter currency codes (ISO standard) to prevent hijacking commands like !fish, !test, etc.
        currency_amount_match = re.match(r'^([A-Z]{3})(\d*\.?\d*)?$', first_arg, re.IGNORECASE)
        if currency_amount_match:
            base_currency = currency_amount_match.group(1).upper()
            attached_amount_str = currency_amount_match.group(2)
            if attached_amount_str:
                try:
                    amount = float(attached_amount_str)
                except ValueError:
                    amount = 1.0
            if len(full_command_parts) > 1:
                second_arg = full_command_parts[1]
                if re.match(r'^\d+(\.\d+)?$', second_arg):
                    try:
                        amount = float(second_arg)
                        if len(full_command_parts) > 2:
                            target_currency = full_command_parts[2].upper()
                    except ValueError:
                        pass
                else:
                    target_currency = second_arg.upper()
        else:
            return
        status_message = await message.channel.send(f"Fetching exchange rates for **{base_currency}**, please wait...")
        rates_data = await self.fetch_exchange_rates(base_currency, target_currency)
        if rates_data and rates_data.get('rates'):
            base = rates_data.get('base')
            date = rates_data.get('date')
            rates = rates_data.get('rates')
            header = f"**Exchange Rates for {amount:.2f} {base} (as of {date}):**\n"
            if target_currency:
                rate_for_one = rates.get(target_currency)
                if rate_for_one is not None:
                    converted_rate = rate_for_one * amount
                    response_message = header + f"**{amount:.2f} {base} = {converted_rate:.4f} {target_currency}**"
                    view = HistoricalGraphView(self.bot, base_currency=base, target_currency=target_currency)
                    await status_message.edit(content=response_message, view=view)
                else:
                    await status_message.edit(content=f"Could not find rate for `{target_currency}`.")
            else:
                await status_message.edit(content=header)
                rate_lines = [f"  - {currency}: {(rate_val * amount):.4f}" for currency, rate_val in rates.items()]
                current_chunk = ""
                for line in rate_lines:
                    if len(current_chunk) + len(line) + 1 > 1900:
                        await message.channel.send(f"```\n{current_chunk}\n```")
                        current_chunk = line
                    else:
                        current_chunk += "\n" + line
                if current_chunk:
                    await message.channel.send(f"```\n{current_chunk}\n```")
        else:
            await status_message.edit(content=f"Sorry, I couldn't fetch exchange rates for `{base_currency}`.")

    @commands.command(name='liverate', aliases=['r'])
    async def liverate(self, ctx: commands.Context, *args):
        """Converts a currency amount using live rates from the Wise Sandbox API."""
        if not WISE_SANDBOX_TOKEN:
            await ctx.send("Sorry, the live rate feature is not configured by the bot owner.")
            return

        amount, source, target = 1.0, None, None

        if not args:
            await ctx.send("Usage: `!liverate [amount] <source> <target>`\n(e.g., `!liverate 100 EUR USD` or `!liverate EUR USD`)")
            return

        try:
            if len(args) == 2:
                match = re.match(r'^(\d*\.?\d+)([a-zA-Z]{3,4})$', args[0], re.IGNORECASE)
                if match:
                    amount = float(match.group(1))
                    source = match.group(2)
                    target = args[1]
                else:
                    amount = 1.0
                    source = args[0]
                    target = args[1]
            elif len(args) == 3:
                amount = float(args[0])
                source = args[1]
                target = args[2]
            else:
                await ctx.send("Invalid format. Please use `!liverate [amount] <source> <target>`.")
                return
        except (ValueError, IndexError):
            await ctx.send("I couldn't understand your input. Please use a valid format like `!liverate 100 EUR USD`.")
            return

        source_curr, target_curr = source.upper(), target.upper()
        api_url = f"https://api.sandbox.transferwise.tech/v1/rates?source={source_curr}&target={target_curr}"
        headers = {"Authorization": f"Bearer {WISE_SANDBOX_TOKEN}"}

        async with ctx.typing():
            try:
                async with self.bot.http_session.get(api_url, headers=headers) as response:
                    response.raise_for_status()
                    data = await response.json()

                if not data or not isinstance(data, list):
                    await ctx.send(f"The Wise API returned an unexpected response for {source_curr} to {target_curr}.")
                    return

                rate_info = data[0]
                live_rate = rate_info.get('rate')
                time_str = rate_info.get('time')

                if not live_rate or not time_str:
                    await ctx.send("The API response was missing the rate or time.")
                    return

                converted_amount = amount * live_rate

                if time_str.endswith("+0000"):
                    time_str = time_str[:-2] + ":" + time_str[-2:]
                dt_object = datetime.fromisoformat(time_str)
                unix_timestamp = int(dt_object.timestamp())

                embed = discord.Embed(title="Live Rate", description=f"**{amount:,.2f} {source_curr}** is equal to\n# **`{converted_amount:,.2f} {target_curr}`**", color=discord.Color.blue())
                embed.add_field(name="Live Rate", value=f"1 {source_curr} = {live_rate} {target_curr}", inline=False)
                embed.add_field(name="Rate As Of", value=f"<t:{unix_timestamp}:f>", inline=False)
                embed.set_footer(text="Rates from Wise")
                await ctx.send(embed=embed)

            except aiohttp.ClientResponseError:
                await ctx.send(f"Sorry, I couldn't get a rate for **{source_curr}** to **{target_curr}**. Please check if the currency codes are valid.")
            except Exception as e:
                print(f"An error occurred in the liverate command: {e}")
                await ctx.send("An unexpected error occurred.")


async def setup(bot):
    await bot.add_cog(Currency(bot))
