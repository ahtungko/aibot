import random
import time
import json
import asyncio
import discord
from discord.ext import commands
from cogs.economy import (
    CODE_CRACKER_ENTRY_FEE_TX,
    CODE_CRACKER_LOSS_TX,
    CODE_CRACKER_TIMEOUT_REFUND_TX,
    CODE_CRACKER_WIN_TX,
    MYSTERY_ENTRY_FEE_TX,
    MYSTERY_EXPIRED_TX,
    MYSTERY_LOSS_TX,
    MYSTERY_SOLVED_TX,
    SCRAMBLE_ENTRY_FEE_TX,
    SCRAMBLE_TIMEOUT_TX,
    SCRAMBLE_WIN_PREFIX,
    add_balance,
    log_transaction,
    get_balance,
    track_fee,
    db_query,
    get_user_stats,
    update_user_stats,
)

# --- AI Murder Mystery Components ---

class MysteryView(discord.ui.View):
    def __init__(self, ctx, culprit_name, suspects, bounty):
        super().__init__(timeout=300) # 5 minutes
        self.ctx = ctx
        self.culprit_name = culprit_name
        self.suspects = suspects
        self.bounty = bounty
        self.solved = False
        self.failed = False

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
            
            # Restrict to the person who started the game
            if interaction.user.id != self.ctx.author.id:
                await interaction.response.send_message(f"❌ This mystery belongs to **{self.ctx.author.display_name}**. Wait for the next one!", ephemeral=True)
                return

            if name.lower() == self.culprit_name.lower():
                self.solved = True
                self.stop()
                
                uid = str(interaction.user.id)
                # Tax 20% of bounty to vault
                tax = int(self.bounty * 0.20)
                net_bounty = self.bounty - tax
                track_fee(tax)
                
                new_bal = add_balance(uid, net_bounty)
                log_transaction(uid, net_bounty, MYSTERY_SOLVED_TX)
                log_transaction(uid, -tax, "Mystery Bounty Tax", processed=1)
                
                embed = discord.Embed(
                    title="🎉 MYSTERY SOLVED!",
                    description=f"Congratulations {interaction.user.mention}! \n\nYou correctly identified **{self.culprit_name}** as the culprit!",
                    color=discord.Color.green()
                )
                embed.add_field(name="Gross Bounty", value=f"**{self.bounty:,}** JC", inline=True)
                embed.add_field(name="Tax (20%)", value=f"**{tax:,}** JC", inline=True)
                embed.add_field(name="Net Received", value=f"**{net_bounty:,}** JC", inline=True)
                embed.add_field(name="New Balance", value=f"**{new_bal:,}** JC", inline=False)
                embed.set_footer(text=f"Solved in {self.ctx.channel.name}")
                
                await interaction.response.edit_message(embed=embed, view=None)
                await self.ctx.send(f"🏆 {interaction.user.mention} solved the mystery! Won **{net_bounty:,}** JC! (Tax: {tax:,} JC)")
            else:
                # Wrong answer — Game Over immediately for the requester
                self.failed = True
                self.stop()
                log_transaction(str(interaction.user.id), 0, MYSTERY_LOSS_TX)
                embed = discord.Embed(
                    title="❌ CASE CLOSED (FAILED)",
                    description=f"Sorry {interaction.user.mention}, your accusation was wrong.\n\n**{name}** is innocent! The real culprit was **{self.culprit_name}**.\n\nYou have failed the investigation.",
                    color=discord.Color.red()
                )
                await interaction.response.edit_message(embed=embed, view=None)
        return callback

# --- Code Cracker Components ---

class CodeCrackerView(discord.ui.View):
    def __init__(self, cog, ctx, secret_code, bounty):
        super().__init__(timeout=900) # Increased to 15 minutes
        self.cog = cog
        self.ctx = ctx
        self.message = None # Set by command
        self.secret_code = secret_code # List of 4 digits
        self.bounty = bounty
        self.current_guess = []
        self.attempts_left = 5
        self.history = [] # List of {"guess": str, "bulls": int, "cows": int}
        self.game_over = False

        # Add 0-9 buttons
        for i in range(10):
            button = discord.ui.Button(label=str(i), style=discord.ButtonStyle.secondary, custom_id=f"num_{i}", row=i // 5)
            button.callback = self.create_num_callback(i)
            self.add_item(button)
        
        # Add utility buttons
        clear_btn = discord.ui.Button(label="Clear", style=discord.ButtonStyle.danger, row=2)
        clear_btn.callback = self.clear_callback
        self.add_item(clear_btn)

        submit_btn = discord.ui.Button(label="Submit", style=discord.ButtonStyle.success, row=2)
        submit_btn.callback = self.submit_callback
        self.add_item(submit_btn)

    def create_num_callback(self, num):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.ctx.author.id:
                await interaction.response.send_message("❌ This is not your game!", ephemeral=True)
                return
            if self.game_over: return

            if len(self.current_guess) < 4:
                self.current_guess.append(num)
                await self.update_message(interaction)
            else:
                await interaction.response.send_message("Code is already 4 digits!", ephemeral=True)
        return callback

    async def clear_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id: return
        self.current_guess = []
        await self.update_message(interaction)

    async def submit_callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.ctx.author.id: return
        if len(self.current_guess) < 4:
            await interaction.response.send_message("Enter a 4-digit code first!", ephemeral=True)
            return
        
        # Calculate Bulls and Cows
        bulls = 0
        cows = 0
        temp_secret = list(self.secret_code)
        temp_guess = list(self.current_guess)

        # First pass: Bulls
        for i in range(4):
            if temp_guess[i] == temp_secret[i]:
                bulls += 1
                temp_secret[i] = -1 # Mark as used
                temp_guess[i] = -2

        # Second pass: Cows
        for i in range(4):
            if temp_guess[i] in temp_secret:
                cows += 1
                temp_secret[temp_secret.index(temp_guess[i])] = -1 # Mark as used

        self.history.append({"guess": "".join(map(str, self.current_guess)), "bulls": bulls, "cows": cows})
        self.attempts_left -= 1
        self.current_guess = []

        if bulls == 4:
            self.game_over = True
            self.cog.active_cracks.pop(self.ctx.channel.id, None)
            await self.handle_win(interaction)
        elif self.attempts_left <= 0:
            self.game_over = True
            self.cog.active_cracks.pop(self.ctx.channel.id, None)
            await self.handle_loss(interaction)
        else:
            await self.update_message(interaction)

    async def update_message(self, interaction: discord.Interaction):
        embed = self.create_embed()
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=self)
        else:
            await interaction.response.edit_message(embed=embed, view=self)

    def create_embed(self):
        display_guess = "".join(map(str, self.current_guess)).ljust(4, "_")
        desc = f"**Current Guess:** `{display_guess}`\n"
        desc += f"**Attempts Left:** `{self.attempts_left}`\n\n"
        
        if self.history:
            desc += "**History:**\n"
            for h in self.history:
                desc += f"`{h['guess']}` - 🟢 {h['bulls']} | 🟡 {h['cows']}\n"
        
        embed = discord.Embed(title="🔐 CODE CRACKER", description=desc, color=discord.Color.blue())
        embed.set_footer(text="🟢 = Correct digit & spot | 🟡 = Correct digit, wrong spot")
        return embed

    async def handle_win(self, interaction: discord.Interaction):
        uid = str(self.ctx.author.id)
        tax = int(self.bounty * 0.20)
        net_bounty = self.bounty - tax
        track_fee(tax)
        new_bal = add_balance(uid, net_bounty)
        log_transaction(uid, net_bounty, CODE_CRACKER_WIN_TX)
        log_transaction(uid, -tax, "Code Cracker Tax", processed=1)

        embed = self.create_embed()
        embed.title = "🎊 CODE CRACKED!"
        embed.color = discord.Color.green()
        embed.description = f"Excellent work {self.ctx.author.mention}! The code was indeed `{''.join(map(str, self.secret_code))}`."
        
        embed.add_field(name="Prize Money", value=f"**{self.bounty:,}** JC", inline=True)
        embed.add_field(name="Tax Paid (20%)", value=f"- **{tax:,}** JC", inline=True)
        embed.add_field(name="Net Bounty", value=f"**{net_bounty:,}** JC", inline=True)
        embed.add_field(name="New Balance", value=f"**{new_bal:,}** JC", inline=False)
        
        await interaction.response.edit_message(embed=embed, view=None)

    async def handle_loss(self, interaction: discord.Interaction):
        uid = str(self.ctx.author.id)
        log_transaction(uid, 0, CODE_CRACKER_LOSS_TX) # Log result for audit records
        
        embed = self.create_embed()
        embed.title = "💥 ACCESS DENIED!"
        embed.color = discord.Color.red()
        embed.description = f"Security lockout engaged. The code was `{''.join(map(str, self.secret_code))}`.\nBetter luck next time, {self.ctx.author.mention}."
        await interaction.response.edit_message(embed=embed, view=None)

    async def on_timeout(self):
        if self.game_over: return
        self.cog.active_cracks.pop(self.ctx.channel.id, None)
        
        for child in self.children:
            child.disabled = True
            
        uid = str(self.ctx.author.id)
        # Refund 100 JC and reset cooldown
        add_balance(uid, 100)
        update_user_stats(uid, last_crack=0)
        log_transaction(uid, 100, CODE_CRACKER_TIMEOUT_REFUND_TX)

        try:
            if self.message:
                embed = self.create_embed()
                embed.title = "⏰ SESSION EXPIRED - REFUNDED"
                embed.description += "\n\n💰 **100 JC has been refunded** and your cooldown reset."
                embed.color = discord.Color.orange()
                await self.message.edit(embed=embed, view=self)
                await self.ctx.send(f"⚠️ {self.ctx.author.mention}, your Code Cracker session timed out. **100 JC has been refunded.**", delete_after=15)
        except:
            pass

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
        self.active_races = {} # channel_id -> HorseRaceInstance
        self.active_cracks = {} # channel_id -> CodeCrackerView

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
        
        try:
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
        finally:
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

    async def refill_scramble_bank(self):
        """Pre-generate 60 unique words from AI and save to DB."""
        ai_cog = self.bot.get_cog("AI")
        if ai_cog is None or ai_cog.http_client is None:
            return

        categories = [
            "Space Exploration", "Deep Sea Creatures", "Medieval Weapons", "Cyberpunk Cities", 
            "Ancient Mythology", "Cooking Ingredients", "Fictional Magic Systems", "Types of Clouds",
            "Board Games", "Retro Video Games", "Musical Instruments", "Rare Gemstones",
            "Arctic Animals", "Famous Landmarks", "Modern Architecture", "Types of Cheese",
            "Desserts", "Types of Fabric", "Invention History", "Superpowers", 
            "Botanical Names", "Types of Pasta", "Olympic Sports", "Coffee Types",
            "Car Parts", "Software Engineering Terms", "Sci-Fi Gadgets", "Tropical Fruits",
            "Famous Artists", "Types of Dance", "Bird Species", "Dinosaurs",
            "Pirate Terminology", "Steampunk Inventions", "Forest Ecosystems", "Volcanoes",
            "Microscopic Life", "Ocean Currents", "Famous Explorers", "Wonders of the World"
        ]
        
        # Pick 10 random categories to ask for 6 words each
        selected_cats = random.sample(categories, 10)
        
        prompt = (
            f"Provide 6 words each for these 10 categories: {', '.join(selected_cats)}.\n"
            "Words should be 6-10 letters long and interesting.\n"
            "Return ONLY a JSON list of objects: "
            "[{\"original\": \"WORD\", \"scrambled\": \"DWRO\", \"category\": \"Theme\"}]"
        )
        
        try:
            response_text = await ai_cog.call_ai(
                [{"role": "user", "content": prompt}],
                instructions="You are a word puzzle master. Output raw JSON list only. No intro or outro."
            )
            
            if "```json" in response_text: response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text: response_text = response_text.split("```")[1].split("```")[0].strip()
            
            words_data = json.loads(response_text)
            
            # Insert into DB
            for item in words_data:
                orig = item.get('original', "").strip().upper()
                scram = item.get('scrambled', "").strip().upper()
                cat = item.get('category', "General")
                
                if orig and scram:
                    # Check for duplicates by original word
                    exists = db_query("SELECT id FROM scramble_words WHERE original = ?", (orig,), fetchone=True)
                    if not exists:
                        db_query("INSERT INTO scramble_words (original, scrambled, category, status) VALUES (?, ?, ?, 0)", 
                                 (orig, scram, cat), commit=True)
        except Exception as e:
            print(f"Refill error: {e}")

    async def refill_mystery_bank(self):
        """Pre-generate 5 unique mystery questions from AI and save to DB."""
        ai_cog = self.bot.get_cog("AI")
        if ai_cog is None or ai_cog.http_client is None:
            return

        prompt = (
            "Generate 5 unique Murder Mystery JSON objects in a list. "
            "Each object should have: crime, suspects (list of {name, desc}), clues (list of 3 clues), and culprit (name). "
            "Varied settings (Victorian, Cyberpunk, Space, Ancient, etc.). "
            "Return ONLY raw JSON list of objects."
        )

        try:
            response_text = await ai_cog.call_ai(
                [{"role": "user", "content": prompt}],
                instructions="Master mystery writer. Output raw JSON list only. No intro or outro."
            )

            if "```json" in response_text: response_text = response_text.split("```json")[1].split("```")[0].strip()
            elif "```" in response_text: response_text = response_text.split("```")[1].split("```")[0].strip()

            mysteries_data = json.loads(response_text)

            for item in mysteries_data:
                crime = item.get('crime')
                suspects = json.dumps(item.get('suspects'))
                clues = json.dumps(item.get('clues'))
                culprit = item.get('culprit')
                
                # AI sometimes returns culprit as a dict like {"name": "..."} instead of a string
                if isinstance(culprit, dict):
                    culprit = culprit.get('name', str(culprit))

                if crime and suspects and clues and culprit:
                    db_query("INSERT INTO mystery_bank (crime, suspects, clues, culprit, status) VALUES (?, ?, ?, ?, 0)", 
                             (crime, suspects, clues, culprit), commit=True)
        except Exception as e:
            print(f"Refill mystery error: {e}")

    @commands.command(name='scramble')
    async def scramble_command(self, ctx: commands.Context):
        """Unscramble the AI-themed word! (Personal challenge)"""
        uid = str(ctx.author.id)
        now = time.time()

        # Persistent Cooldown Check
        stats = get_user_stats(uid)
        last_scramble = stats.get('last_scramble', 0)
        if last_scramble > now:
            remaining = last_scramble - now
            await ctx.send(f"⏳ **{ctx.author.display_name}**, look at your hands! They are tired from scrambling words. \nTry again in **{int(remaining/60)}m {int(remaining%60)}s**.")
            return

        bal = get_balance(uid)
        
        if bal < 5:
            await ctx.send(f"❌ You need at least **5 JC** to play! (Balance: {bal:,} JC)")
            return

        # Try to pull from Word Bank
        row = db_query("SELECT id, original, scrambled, category FROM scramble_words WHERE status = 0 ORDER BY RANDOM() LIMIT 1", fetchone=True)
        
        if not row:
            await ctx.send("❌ Game bank is empty! Generating new words... please try again in a few seconds.")
            await self.refill_scramble_bank()
            return

        row_id, original, scrambled, category = row
        
        # Mark as used immediately
        db_query("UPDATE scramble_words SET status = 1 WHERE id = ?", (row_id,), commit=True)

        # Apply Cooldown ONLY after we know we have a word
        update_user_stats(uid, last_scramble=now + 3600)

        # Deduct entry fee (Tax)
        add_balance(uid, -5)
        track_fee(5) # Sent to the Global Vault
        log_transaction(uid, -5, SCRAMBLE_ENTRY_FEE_TX)

        # Start game
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
            log_transaction(uid, bounty, f"{SCRAMBLE_WIN_PREFIX}{original})")
            await ctx.send(f"🏆 {ctx.author.mention} solved it! The word was **{original}**. Won **{bounty:,} JC**!")
        except asyncio.TimeoutError:
            log_transaction(uid, 0, SCRAMBLE_TIMEOUT_TX)
            await ctx.send(f"⌛ **Time's up!** The word was **{original}**. Better luck next time!")

        # Check if we need to refill the bank
        unused_count = db_query("SELECT COUNT(*) FROM scramble_words WHERE status = 0", fetchone=True)[0]
        if unused_count < 30:
            asyncio.create_task(self.refill_scramble_bank())

    @scramble_command.error
    async def scramble_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            # Tell the user about the cooldown
            await ctx.send(f"⏳ **{ctx.author.display_name}**, look at your hands! They are tired from scrambling words. \nTry again in **{int(error.retry_after/60)}m {int(error.retry_after%60)}s**.")

    # --- AI Murder Mystery Commands ---

    @commands.command(name='mystery')
    async def mystery_command(self, ctx: commands.Context):
        """Starts an AI-powered Murder Mystery game! (100 JC entry, 1hr CD)"""
        uid = str(ctx.author.id)
        now = time.time()

        # Persistent 1-hour User Cooldown
        stats = get_user_stats(uid)
        last_mystery = stats.get('last_mystery', 0)
        if last_mystery > now:
            remaining = last_mystery - now
            await ctx.send(f"⏳ **{ctx.author.display_name}**, you need to rest your detective brain. \nTry again in **{int(remaining/60)}m {int(remaining%60)}s**.")
            return

        # Entry Fee Check
        bal = get_balance(uid)
        if bal < 100:
            await ctx.send(f"❌ You need at least **100 JC** to start a mystery! (Balance: {bal:,} JC)")
            return

        # Try to pull from Mystery Bank
        row = db_query("SELECT id, crime, suspects, clues, culprit FROM mystery_bank WHERE status = 0 ORDER BY RANDOM() LIMIT 1", fetchone=True)
        
        if not row:
            await ctx.send("❌ No mysteries available! Generating more... please try again in a few seconds.")
            # Trigger refill
            ai_cog = self.bot.get_cog("AI")
            if ai_cog and ai_cog.http_client:
                await self.refill_mystery_bank()
            return

        row_id, crime, suspects_raw, clues_raw, culprit = row
        try:
            suspects = json.loads(suspects_raw)
            clues = json.loads(clues_raw)
        except:
            await ctx.send("❌ Corrupt mystery file in database. Please contact an admin.")
            db_query("UPDATE mystery_bank SET status = 2 WHERE id = ?", (row_id,), commit=True) # Status 2 for corrupt
            return

        # Mark as used immediately
        db_query("UPDATE mystery_bank SET status = 1 WHERE id = ?", (row_id,), commit=True)

        # Deduct entry fee AFTER all validation passes
        add_balance(uid, -100)
        track_fee(100)
        log_transaction(uid, -100, MYSTERY_ENTRY_FEE_TX)

        # Apply cooldown AFTER fee is taken
        update_user_stats(uid, last_mystery=now + 3600)

        try:
            bounty = random.randint(1000, 1500)
            
            embed = discord.Embed(title="🕵️‍♂️ AI MYSTERY", description=f"**CRIME:**\n{crime}\n\n⚠️ **One guess per person!** Choose wisely.", color=discord.Color.gold())
            for s in suspects: embed.add_field(name=s['name'], value=s['desc'], inline=False)
            embed.set_footer(text=f"Entry: 100 JC | Bounty: {bounty:,} JC (20% Tax) | 1 Guess Only")
            
            view = MysteryView(ctx, culprit, suspects, bounty)
            msg = await ctx.send(embed=embed, view=view)
            
            for i, clue in enumerate(clues):
                if view.solved or view.failed: break
                await asyncio.sleep(45)
                if view.solved or view.failed: break
                await ctx.send(embed=discord.Embed(title=f"🔍 CLUE #{i+1}", description=f"*{clue}*", color=discord.Color.blue()))

            if not view.solved and not view.failed:
                await asyncio.sleep(75)
                if not view.solved and not view.failed:
                    view.stop()
                    log_transaction(uid, 0, MYSTERY_EXPIRED_TX)
                    await msg.edit(embed=discord.Embed(title="⌛ EXPIRED", description=f"The culprit was **{culprit}**. No one solved it!", color=discord.Color.light_grey()), view=None)
            # Check if we need to refill the bank
            unused_count = db_query("SELECT COUNT(*) FROM mystery_bank WHERE status = 0", fetchone=True)[0]
            if unused_count < 5:
                task = asyncio.create_task(self.refill_mystery_bank())
                task.add_done_callback(lambda t: print(f"refill_mystery_bank error: {t.exception()}") if t.exception() else None)

        except Exception as e:
            print(f"Error mystery: {e}")
            await ctx.send("❌ Error during mystery game setup.")


    @commands.command(name='crack', aliases=['safe'])
    async def crack_command(self, ctx: commands.Context):
        """Logic-based Code Cracker puzzle! (100 JC entry, 30s CD)"""
        uid = str(ctx.author.id)
        now = int(time.time())

        # Persistent Cooldown Check (30 seconds)
        stats = get_user_stats(uid)
        last_crack = stats.get('last_crack', 0)
        if last_crack > now:
            remaining = int(last_crack - now)
            await ctx.send(f"⏳ {ctx.author.mention}, your safe-cracking fingers need a rest! Try again in **{remaining}s**.")
            return

        if ctx.channel.id in self.active_cracks:
            await ctx.send("🔐 A code is already being cracked in this channel!")
            return
            
        # Entry Fee Check
        bal = get_balance(uid)
        if bal < 100:
            await ctx.send(f"❌ You need at least **100 JC** to play! (Balance: {bal:,} JC)")
            return

        # Deduct entry fee
        add_balance(uid, -100)
        update_user_stats(uid, last_crack=now + 30)
        track_fee(100)
        log_transaction(uid, -100, CODE_CRACKER_ENTRY_FEE_TX)

        # Generate unique 4-digit code
        secret = random.sample(range(10), 4)
        bounty = random.randint(1000, 1500)

        # Initialize game state
        view = CodeCrackerView(self, ctx, secret, bounty)
        self.active_cracks[ctx.channel.id] = view
        
        embed = view.create_embed()
        embed.set_footer(text=f"Entry: 100 JC | Bounty: {bounty:,} JC (20% Tax) | 5 Attempts | 30s CD")
        try:
            view.message = await ctx.send(embed=embed, view=view)
        except:
            self.active_cracks.pop(ctx.channel.id, None)
            raise

async def setup(bot):
    await bot.add_cog(Minigames(bot))
