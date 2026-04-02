# cogs/ai.py — AI mention handler, !clear, !tldr, conversation memory
import time
import asyncio
import discord
import httpx
from discord.ext import commands
from config import (
    OPENAI_API_KEY, OPENAI_BASE_URL, DEFAULT_MODEL, FALLBACK_MODEL,
    AI_PERSONALITY, MAX_HISTORY_MESSAGES, HISTORY_EXPIRY_SECONDS, MIN_DELAY_BETWEEN_CALLS
)


class AI(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.http_client = None
        self.last_ai_call_time = 0
        self.conversation_history = {}  # {user_id: {"messages": [...], "last_active": timestamp}}

    async def cog_load(self):
        if OPENAI_API_KEY and OPENAI_BASE_URL:
            try:
                # Custom HTTP client with common User-Agent to bypass proxy blocking
                self.http_client = httpx.AsyncClient(
                    base_url=OPENAI_BASE_URL,
                    headers={
                        "Authorization": f"Bearer {OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
                    },
                    verify=False,
                    timeout=60.0
                )
                print(f"Successfully initialized AI HTTP client: model={DEFAULT_MODEL}, base_url={OPENAI_BASE_URL}")
            except Exception as e:
                print(f"CRITICAL: Error initializing AI HTTP client: {e}")
                self.http_client = None
        else:
            print("AI API key or Base URL not found. AI functionality is disabled.")

    async def cog_unload(self):
        if self.http_client:
            await self.http_client.aclose()

    async def call_ai(self, messages, instructions=AI_PERSONALITY):
        """Call the AI with retry and fallback logic. Returns response text or None."""
        if not self.http_client:
            return None

        ai_response_text = None
        models_to_try = [DEFAULT_MODEL, FALLBACK_MODEL]
        full_messages = [{"role": "system", "content": instructions}] + messages

        for model_name in models_to_try:
            for attempt in range(3):
                try:
                    payload = {
                        "model": model_name,
                        "messages": full_messages,
                        "temperature": 0.7
                    }
                    
                    response = await self.http_client.post("/chat/completions", json=payload)
                    
                    # Log the JSON output for debugging
                    try:
                        resp_json = response.json()
                        import json
                        print(f"--- AI RESPONSE JSON ({model_name}) ---\n{json.dumps(resp_json, indent=2)}\n--- END ---")
                        
                        if response.status_code == 200:
                            ai_response_text = resp_json['choices'][0]['message']['content']
                            return ai_response_text
                        else:
                            print(f"API Error ({response.status_code}): {response.text}")
                    except Exception as log_err:
                        print(f"Log Error: Could not parse response: {log_err}")
                        print(f"Raw Response: {response.text}")

                except Exception as e:
                    err_str = str(e)
                    if '503' in err_str or '502' in err_str or '529' in err_str:
                        await asyncio.sleep(2)
                    else:
                        print(f"AI Call error: {e}")
            if ai_response_text:
                break
        return ai_response_text

    async def handle_ai_mention(self, message):
        if self.http_client is None:
            await message.reply("My AI brain is currently offline.")
            return
        user_message = message.content.replace(f'<@{self.bot.user.id}>', '').strip()
        if not user_message:
            await message.reply("Hello! Mention me with a question to get an AI response.")
            return
        current_time = time.time()
        if current_time - self.last_ai_call_time < MIN_DELAY_BETWEEN_CALLS:
            remaining_time = MIN_DELAY_BETWEEN_CALLS - (current_time - self.last_ai_call_time)
            await message.reply(f"I'm thinking... please wait {remaining_time:.1f}s.")
            return

        # Build conversation history for this user
        uid = str(message.author.id)
        if uid in self.conversation_history:
            if current_time - self.conversation_history[uid]["last_active"] > HISTORY_EXPIRY_SECONDS:
                del self.conversation_history[uid]
        if uid not in self.conversation_history:
            self.conversation_history[uid] = {"messages": [], "last_active": current_time}

        history = self.conversation_history[uid]
        history["last_active"] = current_time

        history["messages"].append({
            "role": "user",
            "content": user_message
        })

        if len(history["messages"]) > MAX_HISTORY_MESSAGES:
            history["messages"] = history["messages"][-MAX_HISTORY_MESSAGES:]

        try:
            async with message.channel.typing():
                ai_response_text = await self.call_ai(history["messages"])

                if not ai_response_text:
                    await message.reply("I'm sorry, I couldn't generate a response right now.")
                    return

                # Save assistant reply to history
                history["messages"].append({
                    "role": "assistant",
                    "content": ai_response_text
                })
                if len(history["messages"]) > MAX_HISTORY_MESSAGES:
                    history["messages"] = history["messages"][-MAX_HISTORY_MESSAGES:]

                self.last_ai_call_time = time.time()
                if len(ai_response_text) > 2000:
                    chunks = [ai_response_text[i:i + 1990] for i in range(0, len(ai_response_text), 1990)]
                    for i, chunk in enumerate(chunks):
                        if i == 0:
                            await message.reply(chunk)
                        else:
                            await message.channel.send(chunk)
                        await asyncio.sleep(1)
                else:
                    await message.reply(ai_response_text)
        except Exception as e:
            print(f"Error processing OpenAI prompt: {e}")
            await message.reply("I'm sorry, I encountered an error while trying to generate a response.")

    @commands.command(name='clear')
    async def clear_command(self, ctx: commands.Context):
        """Clear your AI conversation memory."""
        uid = str(ctx.author.id)
        if uid in self.conversation_history:
            del self.conversation_history[uid]
            await ctx.send(f"🧹 {ctx.author.mention}, your AI conversation history has been cleared!")
        else:
            await ctx.send(f"📭 {ctx.author.mention}, you don't have any conversation history.")

    @commands.command(name='tldr', aliases=['summarize'])
    async def tldr_command(self, ctx: commands.Context, count: int = 50):
        """Summarize the last N messages in this channel using AI."""
        if self.http_client is None:
            await ctx.send("❌ AI is currently offline. Can't summarize.")
            return

        count = max(10, min(count, 200))

        try:
            async with ctx.typing():
                messages = []
                async for msg in ctx.channel.history(limit=count + 1):
                    if msg.id == ctx.message.id:
                        continue
                    if msg.author.bot and msg.author.id == self.bot.user.id:
                        continue
                    messages.append(msg)

                if len(messages) < 3:
                    await ctx.send("📭 Not enough messages to summarize.")
                    return

                messages.reverse()

                lines = []
                for msg in messages:
                    timestamp = msg.created_at.strftime("%H:%M")
                    content = msg.content[:200] if msg.content else "[attachment/embed]"
                    lines.append(f"[{timestamp}] {msg.author.display_name}: {content}")

                conversation_log = "\n".join(lines)

                prompt = (
                    f"Summarize the following Discord chat conversation. "
                    f"Give a concise TL;DR in bullet points covering the main topics discussed. "
                    f"Always respond in English.\n\n"
                    f"--- CHAT LOG ({len(messages)} messages) ---\n{conversation_log}\n--- END ---"
                )

                ai_response_text = await self.call_ai(
                    [{"role": "user", "content": prompt}],
                    instructions="You are a concise summarizer. Output only the summary, no preamble."
                )

                if not ai_response_text:
                    await ctx.send("❌ AI couldn't generate a summary. Try again later.")
                    return

                embed = discord.Embed(
                    title=f"📋 TL;DR — Last {len(messages)} messages",
                    description=ai_response_text[:4000],
                    color=discord.Color.blue()
                )
                embed.set_footer(text=f"Requested by {ctx.author.display_name}")
                await ctx.send(embed=embed)

        except Exception as e:
            print(f"Error in !tldr command: {e}")
            await ctx.send("❌ Failed to summarize. Something went wrong.")


async def setup(bot):
    await bot.add_cog(AI(bot))
