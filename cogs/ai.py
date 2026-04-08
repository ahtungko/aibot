# cogs/ai.py - AI mention handler, !clear, !tldr, conversation memory, !nsfw, !news
import asyncio
from datetime import timedelta, timezone
from email.utils import parsedate_to_datetime
import html
import re
import time
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

import discord
import httpx
from discord.ext import commands

from config import (
    AI_PERSONALITY,
    COMMAND_PREFIX,
    DEFAULT_MODEL,
    HISTORY_EXPIRY_SECONDS,
    MAX_HISTORY_MESSAGES,
    MIN_DELAY_BETWEEN_CALLS,
    NSFW_API_KEY,
    NSFW_MODEL,
    NSFW_RESPONSES_URL,
    # OPENAI_API_KEY,
    # OPENAI_BACKUP_API_KEY,
    # OPENAI_BACKUP_BASE_URL,
    # OPENAI_BASE_URL,
    XAI_API_KEY,
    XAI_BASE_URL,
)
from utils.storage import load_ai_settings, save_ai_settings


class AI(commands.Cog):
    INLINE_CITATION_PATTERN = re.compile(r"\[\[(\d+)\]\]\((https?://[^\s)]+)\)")
    PRIMARY_MODEL_SETTING_KEY = "primary_model"
    DEFAULT_NEWS_COUNTRY = "my"
    DEFAULT_NEWS_LANGUAGE = "en"
    NEWS_COUNTRY_ALIASES = {
        "au": "au",
        "australia": "au",
        "cn": "cn",
        "china": "cn",
        "gb": "gb",
        "uk": "gb",
        "united kingdom": "gb",
        "hk": "hk",
        "hong kong": "hk",
        "id": "id",
        "indonesia": "id",
        "in": "in",
        "india": "in",
        "jp": "jp",
        "japan": "jp",
        "kr": "kr",
        "korea": "kr",
        "south korea": "kr",
        "malaysia": "my",
        "my": "my",
        "ph": "ph",
        "philippines": "ph",
        "sg": "sg",
        "singapore": "sg",
        "th": "th",
        "thailand": "th",
        "tw": "tw",
        "taiwan": "tw",
        "us": "us",
        "usa": "us",
        "united states": "us",
        "vn": "vn",
        "vietnam": "vn",
    }
    NEWS_COUNTRY_LABELS = {
        "au": "Australia",
        "cn": "China",
        "gb": "United Kingdom",
        "hk": "Hong Kong",
        "id": "Indonesia",
        "in": "India",
        "jp": "Japan",
        "kr": "South Korea",
        "malaysia": "Malaysia",
        "my": "Malaysia",
        "ph": "Philippines",
        "sg": "Singapore",
        "th": "Thailand",
        "tw": "Taiwan",
        "us": "United States",
        "vn": "Vietnam",
    }
    NEWS_LANGUAGE_ALIASES = {
        "chinese": "zh",
        "en": "en",
        "english": "en",
        "id": "id",
        "indonesian": "id",
        "ja": "ja",
        "japanese": "ja",
        "ko": "ko",
        "korean": "ko",
        "ms": "ms",
        "malay": "ms",
        "ta": "ta",
        "tamil": "ta",
        "th": "th",
        "thai": "th",
        "vi": "vi",
        "vietnamese": "vi",
        "zh": "zh",
    }
    NEWS_LANGUAGE_LABELS = {
        "en": "English",
        "id": "Indonesian",
        "ja": "Japanese",
        "ko": "Korean",
        "ms": "Malay",
        "ta": "Tamil",
        "th": "Thai",
        "vi": "Vietnamese",
        "zh": "Chinese",
    }
    NEWS_RSS_LANGUAGE_CODES = {
        "en": "en",
        "id": "id",
        "ja": "ja",
        "ko": "ko",
        "ms": "ms",
        "ta": "ta",
        "th": "th",
        "vi": "vi",
        "zh": "zh-CN",
    }
    NEWS_COUNTRY_TIMEZONES = {
        "au": "Australia/Sydney",
        "cn": "Asia/Shanghai",
        "gb": "Europe/London",
        "hk": "Asia/Hong_Kong",
        "id": "Asia/Jakarta",
        "in": "Asia/Kolkata",
        "jp": "Asia/Tokyo",
        "kr": "Asia/Seoul",
        "my": "Asia/Kuala_Lumpur",
        "ph": "Asia/Manila",
        "sg": "Asia/Singapore",
        "th": "Asia/Bangkok",
        "tw": "Asia/Taipei",
        "us": "America/New_York",
        "vn": "Asia/Ho_Chi_Minh",
    }
    NEWS_COUNTRY_FIXED_OFFSETS = {
        "au": (600, "AEST"),
        "cn": (480, "CST"),
        "gb": (0, "GMT"),
        "hk": (480, "HKT"),
        "id": (420, "WIB"),
        "in": (330, "IST"),
        "jp": (540, "JST"),
        "kr": (540, "KST"),
        "my": (480, "MYT"),
        "ph": (480, "PHT"),
        "sg": (480, "SGT"),
        "th": (420, "ICT"),
        "tw": (480, "CST"),
        "us": (-300, "EST"),
        "vn": (420, "ICT"),
    }

    def __init__(self, bot):
        self.bot = bot
        self.http_client = None
        self.nsfw_client = None
        self.last_ai_call_time = 0
        self.conversation_history = {}
        self.primary_model = DEFAULT_MODEL
        self._load_model_settings()

    @staticmethod
    def _normalize_model_name(model_name):
        value = (model_name or "").strip()
        if not value or len(value) > 100:
            return None
        return value

    def _load_model_settings(self):
        settings = load_ai_settings()
        if not isinstance(settings, dict):
            settings = {}

        saved_model = self._normalize_model_name(settings.get(self.PRIMARY_MODEL_SETTING_KEY))
        self.primary_model = saved_model or DEFAULT_MODEL

    def _save_model_settings(self):
        settings = load_ai_settings()
        if not isinstance(settings, dict):
            settings = {}

        if self.primary_model == DEFAULT_MODEL:
            settings.pop(self.PRIMARY_MODEL_SETTING_KEY, None)
        else:
            settings[self.PRIMARY_MODEL_SETTING_KEY] = self.primary_model

        save_ai_settings(settings)

    @staticmethod
    def _extract_model_ids(payload):
        data = payload.get("data")
        if not isinstance(data, list):
            return []

        model_ids = []
        for item in data:
            if not isinstance(item, dict):
                continue
            model_id = item.get("id")
            if isinstance(model_id, str) and model_id.strip():
                model_ids.append(model_id.strip())

        return model_ids

    @staticmethod
    def _chunk_text(text, max_length=1990):
        if len(text) <= max_length:
            return [text]
        return [text[i:i + max_length] for i in range(0, len(text), max_length)]

    @staticmethod
    def _is_retryable_discord_error(error):
        if isinstance(error, discord.DiscordServerError):
            return True
        if isinstance(error, discord.HTTPException):
            return getattr(error, "status", None) in {500, 502, 503, 504}
        return False

    async def _retry_discord_call(self, operation, *, label, attempts=3):
        last_error = None

        for attempt in range(1, attempts + 1):
            try:
                return await operation()
            except Exception as error:
                if not self._is_retryable_discord_error(error):
                    raise

                last_error = error
                print(f"Discord {label} failed (attempt {attempt}/{attempts}): {error}")

                if attempt < attempts:
                    await asyncio.sleep(attempt)

        raise last_error

    async def _safe_send(self, destination, *args, **kwargs):
        return await self._retry_discord_call(
            lambda: destination.send(*args, **kwargs),
            label="send",
        )

    async def _safe_reply(self, message, *args, **kwargs):
        return await self._retry_discord_call(
            lambda: message.reply(*args, **kwargs),
            label="reply",
        )

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

    @classmethod
    def _resolve_news_country(cls, country_code):
        raw_value = (country_code or cls.DEFAULT_NEWS_COUNTRY).strip().lower()
        if not raw_value:
            raw_value = cls.DEFAULT_NEWS_COUNTRY

        value = cls.NEWS_COUNTRY_ALIASES.get(raw_value, raw_value)
        label = cls.NEWS_COUNTRY_LABELS.get(value)
        if label:
            return value, label

        fallback = raw_value.upper() if len(raw_value) <= 3 else raw_value.title()
        return value, fallback

    @classmethod
    def _resolve_news_language(cls, language_code):
        raw_value = (language_code or cls.DEFAULT_NEWS_LANGUAGE).strip().lower()
        if not raw_value:
            raw_value = cls.DEFAULT_NEWS_LANGUAGE

        value = cls.NEWS_LANGUAGE_ALIASES.get(raw_value, raw_value)
        label = cls.NEWS_LANGUAGE_LABELS.get(value)
        if label:
            return value, label

        fallback = raw_value.upper() if len(raw_value) <= 3 else raw_value.title()
        return value, fallback

    @classmethod
    def _build_google_news_rss_url(cls, country_code, language_code):
        country = country_code.upper()
        rss_language = cls.NEWS_RSS_LANGUAGE_CODES.get(language_code, language_code)
        ceid_language = rss_language.split("-", 1)[0]
        hl = rss_language if "-" in rss_language else f"{rss_language}-{country}"
        return f"https://news.google.com/rss?hl={hl}&gl={country}&ceid={country}:{ceid_language}"

    @classmethod
    def _build_google_news_search_rss_url(cls, query, country_code, language_code):
        country = country_code.upper()
        rss_language = cls.NEWS_RSS_LANGUAGE_CODES.get(language_code, language_code)
        ceid_language = rss_language.split("-", 1)[0]
        hl = rss_language if "-" in rss_language else f"{rss_language}-{country}"
        query_string = urlencode({"q": query})
        return f"https://news.google.com/rss/search?{query_string}&hl={hl}&gl={country}&ceid={country}:{ceid_language}"

    @classmethod
    def _get_news_timezone(cls, country_code):
        tz_name = cls.NEWS_COUNTRY_TIMEZONES.get(country_code)
        if tz_name:
            try:
                return ZoneInfo(tz_name)
            except Exception:
                pass

        fallback = cls.NEWS_COUNTRY_FIXED_OFFSETS.get(country_code)
        if not fallback:
            return None

        offset_minutes, label = fallback
        return timezone(timedelta(minutes=offset_minutes), name=label)

    @classmethod
    def _format_news_timestamp(cls, pub_date, country_code):
        if not pub_date:
            return None

        try:
            dt = parsedate_to_datetime(pub_date)
        except Exception:
            return None

        timezone = cls._get_news_timezone(country_code)
        if timezone is not None:
            dt = dt.astimezone(timezone)

        return dt.strftime("%Y-%m-%d %I:%M %p %Z")

    async def _fetch_google_news(self, country_code, language_code, limit=5):
        url = self._build_google_news_rss_url(country_code, language_code)
        return await self._fetch_google_news_feed(url, country_code, limit=limit)

    async def _fetch_google_news_search(self, query, country_code, language_code, limit=5):
        url = self._build_google_news_search_rss_url(query, country_code, language_code)
        return await self._fetch_google_news_feed(url, country_code, limit=limit)

    async def _fetch_google_news_feed(self, url, country_code, limit=5):

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=15.0,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/123.0.0.0 Safari/537.36"
                    )
                },
            ) as client:
                response = await client.get(url)
        except Exception as e:
            print(f"Google News RSS request error: {e}")
            return []

        if response.status_code != 200:
            print(f"Google News RSS error ({response.status_code}): {response.text[:300]}")
            return []

        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as e:
            print(f"Google News RSS parse error: {e}")
            return []

        items = []
        seen_links = set()

        for item in root.findall("./channel/item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()
            source_elem = item.find("source")
            source_name = source_elem.text.strip() if source_elem is not None and source_elem.text else "Google News"

            if not title or not link or link in seen_links:
                continue

            headline = html.unescape(title)
            if source_name and headline.endswith(f" - {source_name}"):
                headline = headline[: -(len(source_name) + 3)].strip()

            items.append({
                "title": headline,
                "source": html.unescape(source_name),
                "link": link,
                "published_at": self._format_news_timestamp(pub_date, country_code),
            })
            seen_links.add(link)

            if len(items) >= limit:
                break

        return items

    @staticmethod
    def _build_news_embed(title, language_label, items, *, footer_source="Google News RSS"):
        lines = []
        for index, item in enumerate(items, start=1):
            source_link = f"[{item['source']}]({item['link']})"
            published_line = (
                f"\nPublished: {item['published_at']}"
                if item.get("published_at")
                else ""
            )
            lines.append(
                f"**{index}. {item['title']}**\n"
                f"Source: {source_link}{published_line}"
            )

        embed = discord.Embed(
            title=title[:256],
            description="\n\n".join(lines)[:4096],
            color=discord.Color.blue(),
        )
        embed.set_footer(text=f"Language: {language_label} • Source: {footer_source}")
        return embed

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

    @staticmethod
    def _extract_response_citations(payload):
        if not isinstance(payload, dict):
            return []

        urls = []
        seen = set()

        def add_url(value):
            url = None
            if isinstance(value, str):
                url = value.strip()
            elif isinstance(value, dict):
                for key in ("url", "webpage_url", "uri"):
                    candidate = value.get(key)
                    if isinstance(candidate, str) and candidate.strip():
                        url = candidate.strip()
                        break

            if not url or url in seen:
                return

            seen.add(url)
            urls.append(url)

        for citation in payload.get("citations", []):
            add_url(citation)

        for item in payload.get("output", []):
            if not isinstance(item, dict):
                continue

            content = item.get("content", [])
            if not isinstance(content, list):
                continue

            for block in content:
                if not isinstance(block, dict):
                    continue
                for annotation in block.get("annotations", []):
                    add_url(annotation)

        return urls

    @classmethod
    def _format_response_for_discord(cls, payload):
        text = cls._extract_response_text(payload)
        if not text:
            return None

        inline_citations = {}

        def replace_inline_citation(match):
            index = int(match.group(1))
            inline_citations[index] = match.group(2)
            return f" [{index}]"

        formatted_text = cls.INLINE_CITATION_PATTERN.sub(replace_inline_citation, text).strip()

        if inline_citations:
            sources = "\n".join(
                f"[{index}]({inline_citations[index]})"
                for index in sorted(inline_citations)
            )
            return f"{formatted_text}\n\nSources:\n{sources}"

        citation_urls = cls._extract_response_citations(payload)
        if citation_urls:
            sources = "\n".join(
                f"[{index}]({url})"
                for index, url in enumerate(citation_urls, start=1)
            )
            return f"{formatted_text}\n\nSources:\n{sources}"

        return formatted_text

    async def _send_text_chunks(self, destination, text, *, reply_to=None):
        chunks = self._chunk_text(text)
        for index, chunk in enumerate(chunks):
            if index == 0 and reply_to is not None:
                await self._safe_reply(reply_to, chunk)
            else:
                await self._safe_send(destination, chunk)
            if index < len(chunks) - 1:
                await asyncio.sleep(1)

    async def cog_load(self):
        # if OPENAI_API_KEY and OPENAI_BASE_URL:
        if XAI_API_KEY and XAI_BASE_URL:
            try:
                self.http_client = httpx.AsyncClient(
                    # base_url=OPENAI_BASE_URL,
                    base_url=XAI_BASE_URL,
                    headers={
                        # "Authorization": f"Bearer {OPENAI_API_KEY}",
                        "Authorization": f"Bearer {XAI_API_KEY}",
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                    },
                    verify=False,
                    timeout=15.0,
                )
                # print(f"Successfully initialized AI HTTP client: model={DEFAULT_MODEL}, base_url={OPENAI_BASE_URL}")
                print(f"Successfully initialized Grok AI HTTP client: model={self.primary_model}, base_url={XAI_BASE_URL}")
            except Exception as e:
                # print(f"CRITICAL: Error initializing primary AI HTTP client: {e}")
                print(f"CRITICAL: Error initializing primary Grok AI HTTP client: {e}")
                self.http_client = None

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
                print(f"Successfully initialized Grok Responses AI HTTP client: model={NSFW_MODEL}, url={NSFW_RESPONSES_URL}")
            except Exception as e:
                print(f"CRITICAL: Error initializing Grok Responses AI HTTP client: {e}")
                self.nsfw_client = None

        if not any((self.http_client, self.nsfw_client)):
            print("Grok API configuration not found. AI functionality is disabled.")

    async def cog_unload(self):
        if self.http_client:
            await self.http_client.aclose()
        if self.nsfw_client:
            await self.nsfw_client.aclose()

    @staticmethod
    def _build_responses_input(messages):
        response_input = []

        for message in messages:
            if not isinstance(message, dict):
                continue

            role = message.get("role") or "user"
            if role not in {"user", "assistant", "system", "developer"}:
                role = "user"

            content = message.get("content")
            if isinstance(content, str):
                text = content.strip()
                if not text:
                    continue
                response_input.append({
                    "role": role,
                    "content": [{"type": "input_text", "text": text}],
                })
                continue

            if not isinstance(content, list):
                continue

            blocks = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    blocks.append({"type": "input_text", "text": text.strip()})

            if blocks:
                response_input.append({"role": role, "content": blocks})

        return response_input

    async def call_ai(self, messages, instructions=AI_PERSONALITY):
        if self.http_client is None:
            return None

        response_input = self._build_responses_input(messages)

        for _attempt in range(2):
            try:
                payload = {
                    "model": self.primary_model,
                    "input": response_input,
                    "instructions": instructions,
                    "stream": False,
                    "store": False,
                }

                # response = await self.http_client.post("/chat/completions", json=payload)
                response = await self.http_client.post("/responses", json=payload)

                try:
                    resp_json = response.json()
                    import json

                    print(f"--- AI RESPONSE JSON ({self.primary_model}) ---\n{json.dumps(resp_json, indent=2)}\n--- END ---")

                    if response.status_code == 200:
                        ai_response_text = self._format_response_for_discord(resp_json)
                        if ai_response_text:
                            return ai_response_text
                        print(f"Grok API returned 200 without extractable text for model={self.primary_model}.")
                        break

                    print(f"API Error ({response.status_code}): {response.text}")
                    if response.status_code in [400, 401, 403, 404]:
                        break
                    if response.status_code in [429, 500, 502, 503, 504]:
                        print(f"Server overloaded ({response.status_code}) on primary Grok endpoint.")
                        break
                except Exception as log_err:
                    print(f"Log Error: Could not parse response: {log_err}")
                    print(f"Raw Response: {response.text}")

            except Exception as e:
                err_str = str(e).lower()
                if "503" in err_str or "502" in err_str or "529" in err_str:
                    print(f"AI Call overloaded [Primary]: {e}")
                    break
                if "timeout" in err_str or "closed" in err_str:
                    print(f"AI Call early-break [Primary] (connection dead): {e}")
                    break

                print(f"AI Call error [Primary]: {e}")
                await asyncio.sleep(1)

        return None

    async def fetch_available_models(self):
        if self.http_client is None:
            return None

        try:
            response = await self.http_client.get("/models")
        except Exception as e:
            print(f"Models API call error: {e}")
            return None

        try:
            resp_json = response.json()
            import json

            print(f"--- MODELS API JSON ({self.primary_model}) ---\n{json.dumps(resp_json, indent=2)}\n--- END ---")
        except Exception as log_err:
            print(f"Models Log Error: Could not parse response: {log_err}")
            print(f"Raw Response: {response.text}")
            return None

        if response.status_code != 200:
            print(f"Models API Error ({response.status_code}): {response.text}")
            return None

        return self._extract_model_ids(resp_json)

    async def call_responses_ai(self, prompt, instructions=None, tools=None):
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
        if tools:
            payload["tools"] = tools

        try:
            response = await self.nsfw_client.post(NSFW_RESPONSES_URL, json=payload)
        except Exception as e:
            print(f"Responses API call error: {e}")
            return None

        try:
            resp_json = response.json()
            import json

            print(f"--- RESPONSES API JSON ({NSFW_MODEL}) ---\n{json.dumps(resp_json, indent=2)}\n--- END ---")
        except Exception as log_err:
            print(f"Responses Log Error: Could not parse response: {log_err}")
            print(f"Raw Response: {response.text}")
            return None

        if response.status_code != 200:
            print(f"Responses API Error ({response.status_code}): {response.text}")
            return None

        return self._format_response_for_discord(resp_json)

    async def handle_ai_mention(self, message):
        if self.http_client is None:
            await self._safe_reply(message, "My AI brain is currently offline.")
            return

        user_message = message.content.replace(f"<@{self.bot.user.id}>", "").strip()
        if not user_message:
            await self._safe_reply(message, "Hello! Mention me with a question to get an AI response.")
            return

        current_time = time.time()
        if current_time - self.last_ai_call_time < MIN_DELAY_BETWEEN_CALLS:
            remaining_time = MIN_DELAY_BETWEEN_CALLS - (current_time - self.last_ai_call_time)
            await self._safe_reply(message, f"I'm thinking... please wait {remaining_time:.1f}s.")
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
                ai_response_text = await self.call_ai(history["messages"])
                if not ai_response_text:
                    await self._safe_reply(message, "I'm sorry, I couldn't generate a response right now.")
                    return

                history["messages"].append({"role": "assistant", "content": ai_response_text})
                if len(history["messages"]) > MAX_HISTORY_MESSAGES:
                    history["messages"] = history["messages"][-MAX_HISTORY_MESSAGES:]

                self.last_ai_call_time = time.time()
                await self._send_text_chunks(message.channel, ai_response_text, reply_to=message)
        except Exception as e:
            # print(f"Error processing OpenAI prompt: {e}")
            print(f"Error processing Grok prompt: {e}")
            try:
                await self._safe_reply(message, "I'm sorry, I encountered an error while trying to generate a response.")
            except Exception as send_error:
                print(f"Failed to deliver AI error message: {send_error}")

    @commands.command(name="nsfw")
    async def nsfw_command(self, ctx: commands.Context, *, prompt: str = None):
        if not prompt:
            await self._safe_send(ctx, f"Usage: `{COMMAND_PREFIX}nsfw [prompt]`")
            return

        if self.nsfw_client is None:
            await self._safe_send(ctx, "NSFW AI is currently offline. Ask the bot owner to configure the endpoint first.")
            return

        if not self._channel_is_nsfw(ctx.channel):
            await self._safe_send(ctx, "This command only works in channels marked NSFW.")
            return

        current_time = time.time()
        if current_time - self.last_ai_call_time < MIN_DELAY_BETWEEN_CALLS:
            remaining_time = MIN_DELAY_BETWEEN_CALLS - (current_time - self.last_ai_call_time)
            await self._safe_send(ctx, f"I'm thinking... please wait {remaining_time:.1f}s.")
            return

        try:
            async with ctx.typing():
                ai_response_text = await self.call_responses_ai(
                    prompt,
                    instructions=(
                        "Respond in the same language as the user's prompt. "
                        "Answer directly and naturally, without unnecessary preamble."
                    ),
                )

                if not ai_response_text:
                    await self._safe_send(ctx, "I'm sorry, I couldn't generate a response right now.")
                    return

                self.last_ai_call_time = time.time()
                await self._send_text_chunks(ctx, ai_response_text)
        except Exception as e:
            print(f"Error in !nsfw command: {e}")
            try:
                await self._safe_send(ctx, "Failed to generate a response. Something went wrong.")
            except Exception as send_error:
                print(f"Failed to deliver !nsfw error message: {send_error}")

    @commands.command(name="news")
    async def news_command(
        self,
        ctx: commands.Context,
        country_code: str = DEFAULT_NEWS_COUNTRY,
        language_code: str = DEFAULT_NEWS_LANGUAGE,
    ):
        current_time = time.time()
        if current_time - self.last_ai_call_time < MIN_DELAY_BETWEEN_CALLS:
            remaining_time = MIN_DELAY_BETWEEN_CALLS - (current_time - self.last_ai_call_time)
            await self._safe_send(ctx, f"I'm thinking... please wait {remaining_time:.1f}s.")
            return

        country_code, country_label = self._resolve_news_country(country_code)
        language_code, language_label = self._resolve_news_language(language_code)

        try:
            async with ctx.typing():
                news_items = await self._fetch_google_news(country_code, language_code)

                if not news_items:
                    await self._safe_send(ctx, "I'm sorry, I couldn't fetch the news right now.")
                    return

                self.last_ai_call_time = time.time()
                await self._safe_send(
                    ctx,
                    embed=self._build_news_embed(
                        f"{country_label} Latest News",
                        language_label,
                        news_items,
                    )
                )
        except Exception as e:
            print(f"Error in !news command: {e}")
            try:
                await self._safe_send(ctx, "Failed to fetch the news. Something went wrong.")
            except Exception as send_error:
                print(f"Failed to deliver !news error message: {send_error}")

    @commands.command(name="aimodel")
    @commands.is_owner()
    async def aimodel_command(self, ctx: commands.Context, *, model_name: str = None):
        if model_name is None:
            source = "startup default" if self.primary_model == DEFAULT_MODEL else "owner override"
            await ctx.send(
                f"Primary AI model: `{self.primary_model}` ({source}). "
                f"Default from .env: `{DEFAULT_MODEL}`. "
                f"Use `{COMMAND_PREFIX}aimodel <model>` to change it, `{COMMAND_PREFIX}aimodel default` to reset, "
                f"or `{COMMAND_PREFIX}aimodels` to query models."
            )
            return

        if model_name.strip().lower() in {"default", "reset"}:
            self.primary_model = DEFAULT_MODEL
            self._save_model_settings()
            await ctx.send(f"Primary AI model reset to `{self.primary_model}`.")
            return

        normalized_model = self._normalize_model_name(model_name)
        if not normalized_model:
            await ctx.send("Please provide a valid AI model name.")
            return

        self.primary_model = normalized_model
        self._save_model_settings()
        await ctx.send(f"Primary AI model set to `{self.primary_model}`.")

    @commands.command(name="aimodels")
    @commands.is_owner()
    async def aimodels_command(self, ctx: commands.Context):
        if self.http_client is None:
            await ctx.send("AI is currently offline. Can't fetch models.")
            return

        try:
            async with ctx.typing():
                model_ids = await self.fetch_available_models()

            if not model_ids:
                await ctx.send(f"No models were returned from `{XAI_BASE_URL}/models`.")
                return

            lines = [f"Available models from `{XAI_BASE_URL}/models`:"]
            for model_id in model_ids:
                marker = " (current)" if model_id == self.primary_model else ""
                lines.append(f"- `{model_id}`{marker}")

            await self._send_text_chunks(ctx, "\n".join(lines))
        except Exception as e:
            print(f"Error in !aimodels command: {e}")
            await ctx.send("Failed to fetch the models list. Something went wrong.")

    @aimodel_command.error
    async def aimodel_command_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.NotOwner):
            await ctx.send("Only the bot owner can change the AI model.")
            return
        raise error

    @aimodels_command.error
    async def aimodels_command_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.NotOwner):
            await ctx.send("Only the bot owner can fetch the AI models list.")
            return
        raise error

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

                ai_response_text = await self.call_ai(
                    [{"role": "user", "content": prompt}],
                    instructions="You are a concise summarizer. Output only the summary, no preamble.",
                )

                if not ai_response_text:
                    await ctx.send("❌ AI couldn't generate a summary. Try again later.")
                    return

                embed = discord.Embed(
                    title=f"📋 TL;DR - Last {len(messages)} messages",
                    description=ai_response_text[:4000],
                    color=discord.Color.blue(),
                )
                embed.set_footer(text=f"Requested by {ctx.author.display_name}")
                await ctx.send(embed=embed)
        except Exception as e:
            print(f"Error in !tldr command: {e}")
            await ctx.send("❌ Failed to summarize. Something went wrong.")


async def setup(bot):
    await bot.add_cog(AI(bot))
