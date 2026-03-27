import random
import json
import asyncio
import discord
from discord.ext import commands
from cogs.economy import add_balance, log_transaction, get_balance, track_fee

# --- AI Murder Mystery Components ---

class MysteryView(discord.ui.View):
    def __init__(self, ctx, culprit_name, suspects, bounty):
        super().__init__(timeout=300) # 5 minutes
        self.ctx = ctx
        self.culprit_name = culprit_name
        self.suspects = suspects
        self.bounty = bounty
        self.solved = False

        # Add buttons for each suspect
        for i, suspect in enumerate(self.suspects):
            label = suspect['name']
            if len(label) > 75:
                label = label[:72] + "..."
            
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, custom_id=f"suspect_{i}")
            button.callback = self.create_callback(suspect['name'])
            self.add_item(button)

    def create_callback(self, name):
        async def callback(interaction: discord.Interaction):
            if self.solved:
                await interaction.response.send_message("The mystery has already been solved!", ephemeral=True)
                return
            if interaction.user.id == self.ctx.bot.user.id:
                return

            if name.lower() == self.culprit_name.lower():
                self.solved = True
                self.stop()
                
                uid = str(interaction.user.id)
                new_bal = add_balance(uid, self.bounty)
                log_transaction(uid, self.bounty, "Solved AI Mystery")
                
                embed = discord.Embed(
                    title="🎉 MYSTERY SOLVED!",
                    description=f"Congratulations {interaction.user.mention}! \n\nYou correctly identified **{self.culprit_name}** as the culprit!",
                    color=discord.Color.green()
                )
                embed.add_field(name="Bounty Awarded", value=f"**{self.bounty:,}** JC", inline=True)
                embed.add_field(name="New Balance", value=f"**{new_bal:,}** JC", inline=True)
                embed.set_footer(text=f"Solved in {self.ctx.channel.name}")
                
                await interaction.response.edit_message(embed=embed, view=None)
                await self.ctx.send(f"🏆 {interaction.user.mention} just won **{self.bounty:,}** JC for solving the mystery!")
            else:
                await interaction.response.send_message(f"❌ **{name}** is innocent! Keep looking...", ephemeral=True)
        return callback

# --- Horse Race Components ---

class Horse:
    def __init__(self, name, emoji, position=0):
        self.name = name
        self.emoji = emoji
        self.position = position

class HorseRaceInstance:
    def __init__(self):
        self.horses = [
            Horse("Thunderbolt", "🐎"),
            Horse("Vroom Vroom", "🏎️"),
            Horse("Farmer Joe", "🚜"),
            Horse("Cycle Path", "🚲"),
            Horse("Witch's Ride", "🧹")
        ]
        self.bets = {} # user_id -> {'horse_index': int, 'amount': int}
        self.started = False
        self.recruiting = True
        self.finish_line = 15

    def add_bet(self, user_id, horse_index, amount):
        if user_id in self.bets:
            self.bets[user_id]['amount'] += amount
            self.bets[user_id]['horse_index'] = horse_index
        else:
            self.bets[user_id] = {'horse_index': horse_index, 'amount': amount}

    def get_track_display(self):
        display = ""
        for i, horse in enumerate(self.horses):
            track = "-" * self.finish_line
            track_list = list(track)
            if horse.position < self.finish_line:
                track_list[horse.position] = horse.emoji
            else:
                track_list[-1] = horse.emoji
            
            display += f"**{i+1}** | `{''.join(track_list)}` | **🏁**\n"
        return display

# --- Minigames Cog ---

class Minigames(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_mysteries = set()
        self.active_races = {} # channel_id -> HorseRaceInstance

    # --- Horse Race Commands ---

    @commands.group(name='race', invoke_without_command=True)
    @commands.cooldown(1, 30, commands.BucketType.channel)
    async def race_group(self, ctx):
        """Starts a Global Horse Race!"""
        if ctx.channel.id in self.active_races:
            await ctx.send("❌ A race is already being prepared in this channel!")
            return

        race = HorseRaceInstance()
        self.active_races[ctx.channel.id] = race

        embed = discord.Embed(
            title="🏁 GLOBAL HORSE RACE: SIGN-UPS OPEN!",
            description="A new race is starting in **30 seconds**!\nPlace your bets now using `!bet <horse_number> <amount>`.",
            color=discord.Color.blue()
        )
        for i, horse in enumerate(race.horses):
            embed.add_field(name=f"Horse #{i+1}", value=f"{horse.emoji} **{horse.name}**", inline=True)
        
        embed.set_footer(text="Payout: 4.5x for the winner! (10% House Edge goes to Vault)")
        await ctx.send(embed=embed)

        await asyncio.sleep(30)

        if not race.bets:
            await ctx.send("🚫 No bets were placed. The race has been cancelled.")
            del self.active_races[ctx.channel.id]
            return

        race.recruiting = False
        race.started = True
        
        # Start the Race Animation
        race_msg = await ctx.send("🏇 **AND THEY'RE OFF!**")
        
        while True:
            winner = None
            for horse in race.horses:
                move = random.randint(0, 2)
                horse.position += move
                if horse.position >= race.finish_line:
                    winner = horse
                    break
            
            embed = discord.Embed(title="🏇 THE RACE IS ON!", description=race.get_track_display(), color=discord.Color.gold())
            try:
                await race_msg.edit(content=None, embed=embed)
            except: pass
            
            if winner: break
            await asyncio.sleep(1.5)

        # Handle Results
        winner_index = race.horses.index(winner)
        results_embed = discord.Embed(
            title="🏁 RACE RESULTS!",
            description=f"The winner is **Horse #{winner_index+1}: {winner.emoji} {winner.name}**!",
            color=discord.Color.green()
        )
        
        total_bet_pool = sum(b['amount'] for b in race.bets.values())
        total_paid_out = 0
        winners_list = []
        
        for uid, bet in race.bets.items():
            if bet['horse_index'] == winner_index:
                payout = int(bet['amount'] * 4.5)
                add_balance(uid, payout)
                log_transaction(uid, payout, f"Won Horse Race (Horse #{winner_index+1})")
                winners_list.append(f"<@{uid}> won **{payout:,}** JC!")
                total_paid_out += payout

        # House Edge (Tax)
        house_edge = total_bet_pool - total_paid_out
        if house_edge > 0:
            track_fee(house_edge)
            results_embed.set_footer(text=f"🏛️ {house_edge:,} JC contributed to the Global Vault.")

        if winners_list:
            results_embed.add_field(name="Winners 🏆", value="\n".join(winners_list), inline=False)
        else:
            results_embed.add_field(name="Outcome", value="No one bet on the winning horse. The House takes the pool!", inline=False)

        await ctx.send(embed=results_embed)
        del self.active_races[ctx.channel.id]

    @commands.command(name='bet')
    async def bet_command(self, ctx, horse_num: int, amount: str):
        """Place a bet on an active horse race."""
        if ctx.channel.id not in self.active_races:
            await ctx.send("❌ No active race! Start one with `!race`.")
            return
        race = self.active_races[ctx.channel.id]
        if not race.recruiting:
            await ctx.send("❌ Sign-ups closed!")
            return
        if not 1 <= horse_num <= 5:
            await ctx.send("❌ Choose Horse 1-5.")
            return

        uid = str(ctx.author.id)
        bal = get_balance(uid)
        if amount.lower() == 'all': amt = bal
        else:
            try: amt = int(amount)
            except: return
        if amt <= 0 or bal < amt:
            await ctx.send("❌ Invalid amount.")
            return
        if uid in race.bets and race.bets[uid]['horse_index'] != (horse_num - 1):
             await ctx.send("❌ Already bet on another horse.")
             return

        add_balance(uid, -amt)
        log_transaction(uid, -amt, f"Bet on Horse #{horse_num}")
        race.add_bet(uid, horse_num - 1, amt)
        await ctx.send(f"✅ {ctx.author.mention} bet **{amt:,}** JC on **Horse #{horse_num}**!")

    @race_group.error
    async def race_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"⏳ Wait **{int(error.retry_after)}s**.")

    # --- AI Scramble Commands ---

    @commands.command(name='scramble')
    @commands.cooldown(1, 3600, commands.BucketType.user)
    async def scramble_command(self, ctx: commands.Context):
        """Unscramble the AI-themed word! (Personal challenge)"""
        uid = str(ctx.author.id)
        bal = get_balance(uid)
        
        if bal < 5:
            await ctx.send(f"❌ You need at least **5 JC** to play! (Balance: {bal:,} JC)")
            return

        # Deduct entry fee (Tax)
        add_balance(uid, -5)
        track_fee(5) # Sent to the Global Vault
        log_transaction(uid, -5, "Scramble Entry Fee")

        ai_cog = self.bot.get_cog("AI")
        if ai_cog is None or ai_cog.openai_client is None:
            await ctx.send("❌ This command / game is currently not available.")
            add_balance(uid, 5) # Refund the fee
            return

        try:
            async with ctx.typing():
                prompt = (
                    "Pick a word (6-10 letters) and scramble it. JSON only: "
                    "{\"original\": \"WORD\", \"scrambled\": \"DWRO\", \"category\": \"Theme\"}"
                )
                response_text = await ai_cog.call_ai(
                    [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
                    instructions="Word puzzle master. Output JSON only."
                )

            if not response_text:
                await ctx.send("❌ This command / game is currently not available.")
                add_balance(uid, 5)
                return

            if "```json" in response_text: response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text: response_text = response_text.split("```")[1].split("```")[0].strip()

            data = json.loads(response_text)
            original = data.get('original', "").strip().upper()
            scrambled = data.get('scrambled', "").strip().upper()
            category = data.get('category', "General")

            bounty = random.randint(10, 50)
            embed = discord.Embed(title="🧩 AI WORD SCRAMBLE!", color=discord.Color.purple())
            embed.description = f"**SCRAMBLED:** `{scrambled}`\n**CATEGORY:** `{category}`\n\nOnly {ctx.author.mention} can solve this! Payout: **{bounty:,} JC**"
            embed.set_footer(text="Fee: 5 JC | Time: 15s | Cooldown: 1 Hour")
            await ctx.send(embed=embed)

            def check(m):
                return m.channel == ctx.channel and m.author == ctx.author and m.content.strip().upper() == original

            try:
                msg = await self.bot.wait_for('message', check=check, timeout=15.0)
                new_bal = add_balance(uid, bounty)
                log_transaction(uid, bounty, f"Won Scramble ({original})")
                await ctx.send(f"🏆 {ctx.author.mention} solved it! The word was **{original}**. Won **{bounty:,} JC**!")
            except asyncio.TimeoutError:
                await ctx.send(f"⌛ **Time's up!** The word was **{original}**. Better luck next time!")

        except Exception as e:
            await ctx.send("❌ AI error. Entry fee not refunded.")

    # --- AI Murder Mystery Commands (Old) ---

    @commands.command(name='mystery')
    @commands.cooldown(1, 60, commands.BucketType.channel)
    async def mystery_command(self, ctx: commands.Context):
        """Starts an AI-powered Murder Mystery game!"""
        if ctx.channel.id in self.active_mysteries:
            await ctx.send("❌ A mystery is already being solved!")
            return

        ai_cog = self.bot.get_cog("AI")
        if ai_cog is None or ai_cog.openai_client is None:
            await ctx.send("❌ This command / game is currently not available.")
            return
        self.active_mysteries.add(ctx.channel.id)
        
        try:
            async with ctx.typing():
                prompt = ("Generate a Murder Mystery JSON object with: crime, suspects (name/desc), clues, and culprit.")
                response_text = await ai_cog.call_ai(
                    [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
                    instructions="Master mystery writer. Output JSON only."
                )

            if "```json" in response_text: response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text: response_text = response_text.split("```")[1].split("```")[0].strip()

            data = json.loads(response_text)
            crime, suspects, clues, culprit = data.get('crime'), data.get('suspects'), data.get('clues'), data.get('culprit')
            bounty = random.randint(1000, 1500)
            
            embed = discord.Embed(title="🕵️‍♂️ AI MYSTERY", description=f"**CRIME:**\n{crime}", color=discord.Color.gold())
            for s in suspects: embed.add_field(name=s['name'], value=s['desc'], inline=False)
            
            view = MysteryView(ctx, culprit, suspects, bounty)
            msg = await ctx.send(embed=embed, view=view)
            
            for i, clue in enumerate(clues):
                if view.solved: break
                await asyncio.sleep(45)
                if view.solved: break
                await ctx.send(embed=discord.Embed(title=f"🔍 CLUE #{i+1}", description=f"*{clue}*", color=discord.Color.blue()))

            if not view.solved:
                await asyncio.sleep(75)
                if not view.solved:
                    view.stop()
                    await msg.edit(embed=discord.Embed(title="⌛ EXPIRED", description=f"The culprit was **{culprit}**.", color=discord.Color.light_grey()), view=None)
            self.active_mysteries.remove(ctx.channel.id)
        except Exception as e:
            if ctx.channel.id in self.active_mysteries: self.active_mysteries.remove(ctx.channel.id)
            await ctx.send("❌ Error setting up mystery.")

    @mystery_command.error
    async def mystery_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown): await ctx.send(f"⏳ Wait {int(error.retry_after)}s.")

async def setup(bot):
    await bot.add_cog(Minigames(bot))
