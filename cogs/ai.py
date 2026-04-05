# cogs/ai.py - AI mention handler, !clear, !tldr, conversation memory, !nsfw
import asyncio
import time

import discord
import httpx
from discord.ext import commands

from config import (
    AI_PERSONALITY,
    COMMAND_PREFIX,
    DEFAULT_MODEL,
    FALLBACK_MODEL,
    HISTORY_EXPIRY_SECONDS,
    MAX_HISTORY_MESSAGES,
    MIN_DELAY_BETWEEN_CALLS,
    NSFW_API_KEY,
    NSFW_MODEL,
    NSFW_RESPONSES_URL,
    OPENAI_API_KEY,
    OPENAI_BACKUP_API_KEY,
    OPENAI_BACKUP_BASE_URL,
    OPENAI_BASE_URL,
)
class AI(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.http_client = None
        self.backup_client = None
        self.nsfw_client = None
        self.last_ai_call_time = 0
        self.conversation_history = {}

    @staticmethod
    def _chunk_text(text, max_length=1990):
        if len(text) <= max_length:
            return [text]
        return [text[i:i + max_length] for i in range(0, len(text), max_length)]

    @staticmethod
    def _channel_is_nsfw(channel):
        if channel is None:
            return False

        is_nsfw = getattr(channel, "is_nsfw", None)
        if callable(is_nsfw):
            try:
                return bool(is_nsfw())
            except TypeError:
                return False

        return bool(getattr(channel, "nsfw", False))

    @staticmethod
    def _extract_response_text(payload):
        if not isinstance(payload, dict):
            return None

        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()

        parts = []
        for item in payload.get("output", []):
            if not isinstance(item, dict):
                continue

            content = item.get("content", [])
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
                continue

            if not isinstance(content, list):
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue
                text = block.get("text") or block.get("output_text")
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())

        if parts:
            return "\n".join(parts)

        choices = payload.get("choices", [])
        if isinstance(choices, list) and choices:
            first_choice = choices[0] if isinstance(choices[0], dict) else {}
            message = first_choice.get("message", {})
            content = message.get("content")

            if isinstance(content, str) and content.strip():
                return content.strip()

            if isinstance(content, list):
                fallback_parts = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    text = block.get("text")
                    if isinstance(text, str) and text.strip():
                        fallback_parts.append(text.strip())
                if fallback_parts:
                    return "\n".join(fallback_parts)

        return None

    async def _send_text_chunks(self, destination, text, *, reply_to=None):
        chunks = self._chunk_text(text)
        for index, chunk in enumerate(chunks):
            if index == 0 and reply_to is not None:
                await reply_to.reply(chunk)
            else:
                await destination.send(chunk)
            if index < len(chunks) - 1:
                await asyncio.sleep(1)

    async def cog_load(self):
        if OPENAI_API_KEY and OPENAI_BASE_URL:
            try:
                self.http_client = httpx.AsyncClient(
                    base_url=OPENAI_BASE_URL,
                    headers={
                        "Authorization": f"Bearer {OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                    },
                    verify=False,
                    timeout=15.0,
                )
                print(f"Successfully initialized AI HTTP client: model={DEFAULT_MODEL}, base_url={OPENAI_BASE_URL}")
            except Exception as e:
                print(f"CRITICAL: Error initializing primary AI HTTP client: {e}")
                self.http_client = None

        if OPENAI_BACKUP_BASE_URL:
            try:
                self.backup_client = httpx.AsyncClient(
                    base_url=OPENAI_BACKUP_BASE_URL,
                    headers={
                        "Authorization": f"Bearer {OPENAI_BACKUP_API_KEY}",
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                    },
                    verify=False,
                    timeout=15.0,
                )
                print(f"Successfully initialized Backup AI HTTP client: base_url={OPENAI_BACKUP_BASE_URL}")
            except Exception as e:
                print(f"CRITICAL: Error initializing backup AI HTTP client: {e}")
                self.backup_client = None

        if NSFW_API_KEY and NSFW_RESPONSES_URL:
            try:
                self.nsfw_client = httpx.AsyncClient(
                    headers={
                        "Authorization": f"Bearer {NSFW_API_KEY}",
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                    },
                    verify=False,
                    timeout=30.0,
                )
                print(f"Successfully initialized NSFW AI HTTP client: model={NSFW_MODEL}, url={NSFW_RESPONSES_URL}")
            except Exception as e:
                print(f"CRITICAL: Error initializing NSFW AI HTTP client: {e}")
                self.nsfw_client = None

        if not any((self.http_client, self.backup_client, self.nsfw_client)):
            print("AI API configuration not found. AI functionality is disabled.")

    async def cog_unload(self):
        if self.http_client:
            await self.http_client.aclose()
        if self.backup_client:
            await self.backup_client.aclose()
        if self.nsfw_client:
            await self.nsfw_client.aclose()

    async def call_ai(self, messages, instructions=AI_PERSONALITY, return_node=False):
        clients = []
        if self.http_client:
            clients.append((self.http_client, "Primary"))
        if self.backup_client:
            clients.append((self.backup_client, "Backup"))

        if not clients:
            return (None, None) if return_node else None

        ai_response_text = None
        models_to_try = [DEFAULT_MODEL, FALLBACK_MODEL]
        full_messages = [{"role": "system", "content": instructions}] + messages

        for model_name in models_to_try:
            for client, client_name in clients:
                for _attempt in range(2):
                    try:
                        payload = {
                            "model": model_name,
                            "messages": full_messages,
                            "temperature": 0.7,
                        }

                        response = await client.post("/chat/completions", json=payload)

                        try:
                            resp_json = response.json()
                            import json

                            print(f"--- AI RESPONSE JSON ({model_name}) ---\n{json.dumps(resp_json, indent=2)}\n--- END ---")

                            if response.status_code == 200:
                                ai_response_text = resp_json["choices"][0]["message"]["content"]
                                return (ai_response_text, client_name) if return_node else ai_response_text

                            print(f"API Error ({response.status_code}): {response.text}")
                            if response.status_code in [400, 401, 403, 404]:
                                break
                            if response.status_code in [429, 500, 502, 503, 504]:
                                print(f"Server overloaded ({response.status_code}), instantly shifting to next node.")
                                break
                        except Exception as log_err:
                            print(f"Log Error: Could not parse response: {log_err}")
                            print(f"Raw Response: {response.text}")

                    except Exception as e:
                        err_str = str(e).lower()
                        if "503" in err_str or "502" in err_str or "529" in err_str:
                            print(f"AI Call overloaded [{client_name}]: {e}")
                            break
                        if "timeout" in err_str or "closed" in err_str:
                            print(f"AI Call early-break [{client_name}] (connection dead): {e}")
                            break

                        print(f"AI Call error [{client_name}]: {e}")
                        await asyncio.sleep(1)

                if ai_response_text:
                    break
            if ai_response_text:
                break

        return (None, None) if return_node else None

    async def call_nsfw_ai(self, prompt, instructions=None):
        if self.nsfw_client is None:
            return None

        payload = {
            "model": NSFW_MODEL,
            "input": prompt,
            "stream": False,
            "store": False,
        }
        if instructions:
            payload["instructions"] = instructions

        try:
            response = await self.nsfw_client.post(NSFW_RESPONSES_URL, json=payload)
        except Exception as e:
            print(f"NSFW API call error: {e}")
            return None

        try:
            resp_json = response.json()
            import json

            print(f"--- NSFW RESPONSE JSON ({NSFW_MODEL}) ---\n{json.dumps(resp_json, indent=2)}\n--- END ---")
        except Exception as log_err:
            print(f"NSFW Log Error: Could not parse response: {log_err}")
            print(f"Raw Response: {response.text}")
            return None

        if response.status_code != 200:
            print(f"NSFW API Error ({response.status_code}): {response.text}")
            return None

        return self._extract_response_text(resp_json)

    async def handle_ai_mention(self, message):
        if self.http_client is None and self.backup_client is None:
            await message.reply("My AI brain is currently offline.")
            return

        user_message = message.content.replace(f"<@{self.bot.user.id}>", "").strip()
        if not user_message:
            await message.reply("Hello! Mention me with a question to get an AI response.")
            return

        current_time = time.time()
        if current_time - self.last_ai_call_time < MIN_DELAY_BETWEEN_CALLS:
            remaining_time = MIN_DELAY_BETWEEN_CALLS - (current_time - self.last_ai_call_time)
            await message.reply(f"I'm thinking... please wait {remaining_time:.1f}s.")
            return

        uid = str(message.author.id)
        if uid in self.conversation_history:
            if current_time - self.conversation_history[uid]["last_active"] > HISTORY_EXPIRY_SECONDS:
                del self.conversation_history[uid]
        if uid not in self.conversation_history:
            self.conversation_history[uid] = {"messages": [], "last_active": current_time}

        history = self.conversation_history[uid]
        history["last_active"] = current_time
        history["messages"].append({"role": "user", "content": user_message})

        if len(history["messages"]) > MAX_HISTORY_MESSAGES:
            history["messages"] = history["messages"][-MAX_HISTORY_MESSAGES:]

        try:
            async with message.channel.typing():
                result = await self.call_ai(history["messages"], return_node=True)
                ai_response_text, client_name = result if result != (None, None) else (None, None)

                if not ai_response_text:
                    await message.reply("I'm sorry, I couldn't generate a response right now.")
                    return

                history["messages"].append({"role": "assistant", "content": ai_response_text})
                if len(history["messages"]) > MAX_HISTORY_MESSAGES:
                    history["messages"] = history["messages"][-MAX_HISTORY_MESSAGES:]

                self.last_ai_call_time = time.time()
                display_text = f"{ai_response_text}\n\n*[{client_name}]*"
                await self._send_text_chunks(message.channel, display_text, reply_to=message)
        except Exception as e:
            print(f"Error processing OpenAI prompt: {e}")
            await message.reply("I'm sorry, I encountered an error while trying to generate a response.")

    @commands.command(name="nsfw")
    async def nsfw_command(self, ctx: commands.Context, *, prompt: str = None):
        if not prompt:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}nsfw [prompt]`")
            return

        if self.nsfw_client is None:
            await ctx.send("NSFW AI is currently offline. Ask the bot owner to configure the endpoint first.")
            return

        if not self._channel_is_nsfw(ctx.channel):
            await ctx.send("This command only works in channels marked NSFW.")
            return

        current_time = time.time()
        if current_time - self.last_ai_call_time < MIN_DELAY_BETWEEN_CALLS:
            remaining_time = MIN_DELAY_BETWEEN_CALLS - (current_time - self.last_ai_call_time)
            await ctx.send(f"I'm thinking... please wait {remaining_time:.1f}s.")
            return

        try:
            async with ctx.typing():
                ai_response_text = await self.call_nsfw_ai(
                    prompt,
                    instructions=(
                        "Respond in the same language as the user's prompt. "
                        "Answer directly and naturally, without unnecessary preamble."
                    ),
                )

                if not ai_response_text:
                    await ctx.send("I'm sorry, I couldn't generate a response right now.")
                    return

                self.last_ai_call_time = time.time()
                await self._send_text_chunks(ctx, ai_response_text)
        except Exception as e:
            print(f"Error in !nsfw command: {e}")
            await ctx.send("Failed to generate a response. Something went wrong.")

    @commands.command(name="clear")
    async def clear_command(self, ctx: commands.Context):
        uid = str(ctx.author.id)
        if uid in self.conversation_history:
            del self.conversation_history[uid]
            await ctx.send(f"🧹 {ctx.author.mention}, your AI conversation history has been cleared!")
        else:
            await ctx.send(f"💭 {ctx.author.mention}, you don't have any conversation history.")

    @commands.command(name="tldr", aliases=["summarize"])
    async def tldr_command(self, ctx: commands.Context, count: int = 50):
        if self.http_client is None and self.backup_client is None:
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
                    await ctx.send("💭 Not enough messages to summarize.")
                    return

                messages.reverse()

                lines = []
                for msg in messages:
                    timestamp = msg.created_at.strftime("%H:%M")
                    content = msg.content[:200] if msg.content else "[attachment/embed]"
                    lines.append(f"[{timestamp}] {msg.author.display_name}: {content}")

                conversation_log = "\n".join(lines)
                prompt = (
                    "Summarize the following Discord chat conversation. "
                    "Give a concise TL;DR in bullet points covering the main topics discussed. "
                    "Always respond in English.\n\n"
                    f"--- CHAT LOG ({len(messages)} messages) ---\n{conversation_log}\n--- END ---"
                )

                result = await self.call_ai(
                    [{"role": "user", "content": prompt}],
                    instructions="You are a concise summarizer. Output only the summary, no preamble.",
                    return_node=True,
                )
                ai_response_text, client_name = result if result != (None, None) else (None, None)

                if not ai_response_text:
                    await ctx.send("❌ AI couldn't generate a summary. Try again later.")
                    return

                embed = discord.Embed(
                    title=f"📋 TL;DR - Last {len(messages)} messages",
                    description=ai_response_text[:4000],
                    color=discord.Color.blue(),
                )
                embed.set_footer(text=f"Requested by {ctx.author.display_name} • Node: [{client_name}]")
                await ctx.send(embed=embed)
        except Exception as e:
            print(f"Error in !tldr command: {e}")
            await ctx.send("❌ Failed to summarize. Something went wrong.")


async def setup(bot):
    await bot.add_cog(AI(bot))
