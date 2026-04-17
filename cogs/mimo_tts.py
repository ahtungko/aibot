import base64
import io
import json
import re
import shlex
import time
import wave

import discord
from discord.ext import commands

from config import COMMAND_PREFIX, MIMO_API_KEY, MIMO_TTS_MODEL, MIMO_TTS_URL
from cogs.economy import get_setting, set_setting


class MimoTTS(commands.Cog):
    DEFAULT_VOICE = "mimo_default"
    SUPPORTED_VOICES = {"mimo_default", "default_zh", "default_en", "custom"}
    MAX_TEXT_LENGTH = 1800
    MAX_SAMPLE_BYTES = 5 * 1024 * 1024
    DISCORD_UPLOAD_LIMIT = 8 * 1024 * 1024
    TTS_TOGGLE_SETTING_KEY = "mimo_tts_enabled"

    def __init__(self, bot):
        self.bot = bot

    def _is_tts_enabled(self) -> bool:
        stored = (get_setting(self.TTS_TOGGLE_SETTING_KEY, "on") or "on").strip().lower()
        return stored not in {"0", "off", "false", "disabled", "disable"}

    def _set_tts_enabled(self, enabled: bool):
        set_setting(self.TTS_TOGGLE_SETTING_KEY, "on" if enabled else "off")

    @staticmethod
    def _usage():
        return (
            f"Usage: `{COMMAND_PREFIX}tts [--voice mimo_default|default_zh|default_en|custom] "
            f"[--style 开心] [--auto] [--user \"optional user message\"] <assistant text>`\n"
            f"Examples:\n"
            f"- `{COMMAND_PREFIX}tts --style 开心 明天就是周五了！`\n"
            f"- `{COMMAND_PREFIX}tts --auto 明天就是周五了！`\n"
            f"- `{COMMAND_PREFIX}tts --voice default_en \"Hello, this is MiMo.\"`\n"
            f"- `{COMMAND_PREFIX}tts --voice custom --style 粤语 你好呀` + attach/reply to a WAV file"
        )

    @staticmethod
    def _sayai_usage():
        return (
            f"Usage: `{COMMAND_PREFIX}sayai [--voice mimo_default|default_zh|default_en|custom] "
            f"[--style 开心] [--auto] <prompt>`\n"
            f"Examples:\n"
            f"- `{COMMAND_PREFIX}sayai write a cheerful good morning message in Chinese`\n"
            f"- `{COMMAND_PREFIX}sayai --auto comfort me after a stressful day`\n"
            f"- `{COMMAND_PREFIX}sayai --voice default_en write a warm birthday greeting`"
        )

    @staticmethod
    def _extract_json_object(raw_text: str):
        text = (raw_text or "").strip()
        if not text:
            return None

        text = text.removeprefix("```json").removeprefix("```JSON").removeprefix("```").removesuffix("```").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None

        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _strip_code_block(value: str) -> str:
        text = (value or "").strip()
        if text.startswith("```") and text.endswith("```"):
            lines = text.splitlines()
            if len(lines) >= 2:
                return "\n".join(lines[1:-1]).strip()
        return text

    @staticmethod
    def _strip_style_tags(value: str) -> str:
        text = value or ""
        text = re.sub(r"<style>.*?</style>\s*", "", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"</?style>", "", text, flags=re.IGNORECASE)
        return text.strip()

    def _parse_args(self, raw_args: str):
        voice = self.DEFAULT_VOICE
        style = ""
        user_message = ""
        auto_style = False
        voice_explicit = False
        style_explicit = False

        try:
            tokens = shlex.split(raw_args or "")
        except ValueError as exc:
            raise ValueError(f"Couldn't parse your options: {exc}") from exc

        assistant_tokens = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in {"--help", "-h"}:
                raise ValueError(self._usage())
            if token in {"--voice", "-v"}:
                index += 1
                if index >= len(tokens):
                    raise ValueError("Missing value after `--voice`.")
                voice = tokens[index].strip()
                voice_explicit = True
            elif token in {"--style", "-s"}:
                index += 1
                if index >= len(tokens):
                    raise ValueError("Missing value after `--style`.")
                style = tokens[index].strip()
                style_explicit = True
            elif token in {"--auto", "--auto-style", "--autostyle", "-a"}:
                auto_style = True
            elif token in {"--user", "-u"}:
                index += 1
                if index >= len(tokens):
                    raise ValueError("Missing value after `--user`.")
                user_message = tokens[index].strip()
            else:
                assistant_tokens = tokens[index:]
                break
            index += 1

        assistant_message = self._strip_code_block(" ".join(assistant_tokens))
        if not assistant_message:
            raise ValueError("Please provide the text you want MiMo to speak.")

        if len(assistant_message) > self.MAX_TEXT_LENGTH:
            raise ValueError(f"Assistant text is too long. Keep it under {self.MAX_TEXT_LENGTH} characters.")

        if user_message and len(user_message) > self.MAX_TEXT_LENGTH:
            raise ValueError(f"User message is too long. Keep it under {self.MAX_TEXT_LENGTH} characters.")

        voice = voice.lower()
        if voice not in self.SUPPORTED_VOICES:
            supported = ", ".join(f"`{name}`" for name in sorted(self.SUPPORTED_VOICES))
            raise ValueError(f"Unsupported voice `{voice}`. Supported voices: {supported}.")

        if style and "<style>" not in assistant_message.lower():
            assistant_message = f"<style>{style}</style>{assistant_message}"

        return {
            "voice": voice,
            "style": style,
            "auto_style": auto_style,
            "voice_explicit": voice_explicit,
            "style_explicit": style_explicit,
            "user_message": user_message,
            "assistant_message": assistant_message,
        }

    def _parse_sayai_args(self, raw_args: str):
        voice = self.DEFAULT_VOICE
        style = ""
        auto_style = True
        voice_explicit = False
        style_explicit = False

        try:
            tokens = shlex.split(raw_args or "")
        except ValueError as exc:
            raise ValueError(f"Couldn't parse your options: {exc}") from exc

        prompt_tokens = []
        index = 0
        while index < len(tokens):
            token = tokens[index]
            if token in {"--help", "-h"}:
                raise ValueError(self._sayai_usage())
            if token in {"--voice", "-v"}:
                index += 1
                if index >= len(tokens):
                    raise ValueError("Missing value after `--voice`.")
                voice = tokens[index].strip()
                voice_explicit = True
            elif token in {"--style", "-s"}:
                index += 1
                if index >= len(tokens):
                    raise ValueError("Missing value after `--style`.")
                style = tokens[index].strip()
                style_explicit = True
            elif token in {"--auto", "--auto-style", "--autostyle", "-a"}:
                auto_style = True
            elif token in {"--no-auto", "--no-autostyle"}:
                auto_style = False
            else:
                prompt_tokens = tokens[index:]
                break
            index += 1

        prompt = self._strip_code_block(" ".join(prompt_tokens))
        if not prompt:
            raise ValueError("Please provide a prompt for the AI to write.")

        if len(prompt) > self.MAX_TEXT_LENGTH:
            raise ValueError(f"Prompt is too long. Keep it under {self.MAX_TEXT_LENGTH} characters.")

        voice = voice.lower()
        if voice not in self.SUPPORTED_VOICES:
            supported = ", ".join(f"`{name}`" for name in sorted(self.SUPPORTED_VOICES))
            raise ValueError(f"Unsupported voice `{voice}`. Supported voices: {supported}.")

        return {
            "voice": voice,
            "style": style,
            "auto_style": auto_style,
            "voice_explicit": voice_explicit,
            "style_explicit": style_explicit,
            "prompt": prompt,
        }

    def _get_ai_callable(self):
        ai_cog = self.bot.get_cog("AI")
        if ai_cog is None:
            return None, None

        if getattr(ai_cog, "mention_client", None) is not None:
            return ai_cog.call_mention_ai, "Grok"

        if getattr(ai_cog, "http_client", None) is not None:
            return ai_cog.call_ai, "AI"

        return None, None

    async def _infer_auto_style(self, options: dict):
        if not options.get("auto_style"):
            return options

        ai_callable, _ = self._get_ai_callable()
        if ai_callable is None:
            return options

        if options.get("style_explicit"):
            return options

        prompt = (
            "Choose a speaking tone for MiMo TTS and return JSON only.\n"
            "Rules:\n"
            "- Return exactly one JSON object.\n"
            "- Keys: voice, style, assistant_text.\n"
            "- voice must be one of: mimo_default, default_zh, default_en.\n"
            "- style should be short, like 开心, 悲伤, 生气, 粤语, 东北话, 变慢, 悄悄话, excited, calm.\n"
            "- assistant_text should preserve the original meaning and language.\n"
            "- You may add one opening <style>...</style> tag if useful.\n"
            "- If the text is neutral, set style to an empty string.\n"
            "- Prefer default_zh for Chinese, default_en for English, otherwise mimo_default.\n\n"
            f"User message context: {options.get('user_message') or '(none)'}\n"
            f"Assistant text: {options['assistant_message']}"
        )

        try:
            raw_result = await ai_callable(
                [{"role": "user", "content": prompt}],
                instructions=(
                    "You are a tone classifier for TTS. "
                    "Output JSON only. No markdown, no explanation."
                ),
            )
            parsed = self._extract_json_object(raw_result)
            if not isinstance(parsed, dict):
                return options

            style = (parsed.get("style") or "").strip()
            assistant_text = (parsed.get("assistant_text") or "").strip()
            suggested_voice = (parsed.get("voice") or "").strip().lower()

            if assistant_text:
                options["assistant_message"] = assistant_text

            if style and not options["style_explicit"]:
                options["style"] = style
                if "<style>" not in options["assistant_message"].lower():
                    options["assistant_message"] = f"<style>{style}</style>{options['assistant_message']}"

            if (
                suggested_voice in {"mimo_default", "default_zh", "default_en"}
                and not options["voice_explicit"]
                and options["voice"] != "custom"
            ):
                options["voice"] = suggested_voice
        except Exception as exc:
            print(f"Auto-style inference failed: {exc}")

        return options

    async def _generate_ai_reply(self, prompt: str):
        ai_callable, provider_name = self._get_ai_callable()
        if ai_callable is None:
            raise ValueError("AI text generation is offline right now. Configure Grok or the primary AI first.")

        generation_prompt = (
            "Write a natural reply for text-to-speech.\n"
            "Rules:\n"
            "- Return only the final spoken reply text.\n"
            "- No markdown, no code blocks, no bullet lists unless the user explicitly asks.\n"
            "- Keep it expressive and easy to speak aloud.\n"
            "- Match the user's language.\n"
            "- Keep it concise unless the prompt clearly asks for a long response.\n\n"
            f"User request: {prompt}"
        )

        response_text = await ai_callable(
            [{"role": "user", "content": generation_prompt}],
            instructions=(
                "You write polished assistant replies that will be spoken aloud. "
                "Return only the final reply text."
            ),
        )

        reply_text = self._strip_code_block(response_text or "")
        if not reply_text:
            raise RuntimeError(f"{provider_name or 'AI'} could not generate a reply.")

        if len(reply_text) > self.MAX_TEXT_LENGTH:
            reply_text = reply_text[: self.MAX_TEXT_LENGTH].rstrip()

        return reply_text, provider_name or "AI"

    async def _send_tts_result(self, ctx: commands.Context, *, options: dict, wav_bytes: bytes, response_data: dict, prefix: str, generated_text: str | None = None):
        if len(wav_bytes) > self.DISCORD_UPLOAD_LIMIT:
            await ctx.send("Generated WAV is too large for a normal Discord upload. Try shorter text.")
            return

        content = None
        display_text = self._strip_style_tags(generated_text or options.get("assistant_message", ""))
        if display_text:
            if len(display_text) > 1200:
                display_text = display_text[:1200].rstrip() + "…"
            content = display_text

        filename = f"mimo_tts_{int(time.time())}.wav"
        file = discord.File(io.BytesIO(wav_bytes), filename=filename)
        await ctx.send(content=content, file=file)

    @staticmethod
    def _is_wav_attachment(attachment: discord.Attachment) -> bool:
        if attachment.filename.lower().endswith(".wav"):
            return True
        content_type = (attachment.content_type or "").lower()
        return "wav" in content_type

    async def _find_voice_sample(self, ctx: commands.Context):
        for attachment in ctx.message.attachments:
            if self._is_wav_attachment(attachment):
                return attachment

        reference = ctx.message.reference
        if not reference or not reference.message_id:
            return None

        referenced_message = reference.resolved if isinstance(reference.resolved, discord.Message) else None
        if referenced_message is None:
            try:
                referenced_message = await ctx.channel.fetch_message(reference.message_id)
            except Exception:
                referenced_message = None

        if referenced_message is None:
            return None

        for attachment in referenced_message.attachments:
            if self._is_wav_attachment(attachment):
                return attachment

        return None

    @staticmethod
    def _wrap_pcm_to_wav(pcm_bytes: bytes, *, sample_rate: int = 24000, bits_per_sample: int = 16, channels: int = 1) -> bytes:
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(bits_per_sample // 8)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(pcm_bytes)
        return buffer.getvalue()

    async def _build_payload(self, ctx: commands.Context, options: dict):
        payload = {
            "model": MIMO_TTS_MODEL,
            "audio": {"format": "wav"},
            "messages": [],
        }

        if options["user_message"]:
            payload["messages"].append({"role": "user", "content": options["user_message"]})
        payload["messages"].append({"role": "assistant", "content": options["assistant_message"]})

        if options["voice"] == "custom":
            sample_attachment = await self._find_voice_sample(ctx)
            if sample_attachment is None:
                raise ValueError("`--voice custom` needs a WAV file attached to your command or the message you reply to.")

            if sample_attachment.size > self.MAX_SAMPLE_BYTES:
                raise ValueError("Voice sample is too large. Keep the WAV file under 5 MB.")

            sample_bytes = await sample_attachment.read()
            payload["audio"]["voice_audio"] = {
                "format": "wav",
                "data": base64.b64encode(sample_bytes).decode("ascii"),
            }
        else:
            payload["audio"]["voice"] = options["voice"]

        return payload

    async def _request_tts(self, payload: dict):
        if self.bot.http_session is None or self.bot.http_session.closed:
            raise RuntimeError("HTTP session is unavailable right now.")

        async with self.bot.http_session.post(
            MIMO_TTS_URL,
            headers={
                "api-key": MIMO_API_KEY,
                "Content-Type": "application/json",
            },
            data=json.dumps(payload),
        ) as response:
            response_text = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"MiMo API request failed ({response.status}): {response_text[:240]}")

        try:
            data = json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("MiMo API returned invalid JSON.") from exc

        audio_data = (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("audio", {})
            .get("data")
        )
        if not audio_data:
            raise RuntimeError("MiMo API response did not include audio data.")

        try:
            raw_audio = base64.b64decode(audio_data)
        except Exception as exc:
            raise RuntimeError("Failed to decode the returned audio data.") from exc

        if raw_audio[:4] != b"RIFF":
            raw_audio = self._wrap_pcm_to_wav(raw_audio)

        return raw_audio, data

    @commands.command(name="tts", aliases=["mimo"])
    @commands.cooldown(1, 20, commands.BucketType.user)
    async def tts_command(self, ctx: commands.Context, *, args: str = None):
        if not self._is_tts_enabled():
            await ctx.send("MiMo TTS is currently disabled by the bot owner.")
            return

        if not MIMO_API_KEY:
            await ctx.send("MiMo TTS is not configured yet. Add `MIMO_API_KEY` to `.env` first.")
            return

        if not args:
            await ctx.send(self._usage())
            return

        try:
            options = self._parse_args(args)
        except ValueError as exc:
            await ctx.send(str(exc))
            return

        try:
            async with ctx.typing():
                options = await self._infer_auto_style(options)
                payload = await self._build_payload(ctx, options)
                wav_bytes, response_data = await self._request_tts(payload)
            await self._send_tts_result(
                ctx,
                options=options,
                wav_bytes=wav_bytes,
                response_data=response_data,
                prefix="MiMo TTS ready",
            )
        except ValueError as exc:
            await ctx.send(str(exc))
        except Exception as exc:
            print(f"Error in !tts command: {exc}")
            await ctx.send(f"Failed to generate MiMo TTS audio: {exc}")

    @tts_command.error
    async def tts_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Please wait {error.retry_after:.1f}s before using `!tts` again.")
            return
        raise error

    @commands.command(name="sayai", aliases=["aitts", "mimosay"])
    @commands.cooldown(1, 25, commands.BucketType.user)
    async def sayai_command(self, ctx: commands.Context, *, args: str = None):
        if not self._is_tts_enabled():
            await ctx.send("MiMo TTS is currently disabled by the bot owner.")
            return

        if not MIMO_API_KEY:
            await ctx.send("MiMo TTS is not configured yet. Add `MIMO_API_KEY` to `.env` first.")
            return

        if not args:
            await ctx.send(self._sayai_usage())
            return

        try:
            parsed = self._parse_sayai_args(args)
        except ValueError as exc:
            await ctx.send(str(exc))
            return

        try:
            async with ctx.typing():
                generated_text, provider_name = await self._generate_ai_reply(parsed["prompt"])
                options = {
                    "voice": parsed["voice"],
                    "style": parsed["style"],
                    "auto_style": parsed["auto_style"],
                    "voice_explicit": parsed["voice_explicit"],
                    "style_explicit": parsed["style_explicit"],
                    "user_message": parsed["prompt"],
                    "assistant_message": generated_text,
                }
                options = await self._infer_auto_style(options)
                payload = await self._build_payload(ctx, options)
                wav_bytes, response_data = await self._request_tts(payload)

            await self._send_tts_result(
                ctx,
                options=options,
                wav_bytes=wav_bytes,
                response_data=response_data,
                prefix=f"{provider_name} → MiMo ready",
                generated_text=options["assistant_message"],
            )
        except ValueError as exc:
            await ctx.send(str(exc))
        except Exception as exc:
            print(f"Error in !sayai command: {exc}")
            await ctx.send(f"Failed to generate AI-to-speech audio: {exc}")

    @sayai_command.error
    async def sayai_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Please wait {error.retry_after:.1f}s before using `!sayai` again.")
            return
        raise error

    @commands.command(name="ttstoggle")
    @commands.is_owner()
    async def ttstoggle_command(self, ctx: commands.Context, state: str = None):
        current_enabled = self._is_tts_enabled()

        if state is None:
            current_text = "ON" if current_enabled else "OFF"
            await ctx.send(
                f"MiMo TTS is currently **{current_text}**.\n"
                f"Usage: `{COMMAND_PREFIX}ttstoggle on` or `{COMMAND_PREFIX}ttstoggle off`"
            )
            return

        normalized = state.strip().lower()
        if normalized in {"on", "enable", "enabled", "true", "1"}:
            self._set_tts_enabled(True)
            await ctx.send("✅ MiMo TTS has been **enabled**.")
            return

        if normalized in {"off", "disable", "disabled", "false", "0"}:
            self._set_tts_enabled(False)
            await ctx.send("✅ MiMo TTS has been **disabled**.")
            return

        await ctx.send(f"Usage: `{COMMAND_PREFIX}ttstoggle on` or `{COMMAND_PREFIX}ttstoggle off`")

    @ttstoggle_command.error
    async def ttstoggle_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.NotOwner):
            await ctx.send("Only the bot owner can toggle MiMo TTS.")
            return
        raise error


async def setup(bot):
    await bot.add_cog(MimoTTS(bot))
