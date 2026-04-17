import os
import sqlite3
import time
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import commands, tasks

from config import COMMAND_PREFIX, WEBDAV_BACKUP_URL, WEBDAV_PASSWORD, WEBDAV_USERNAME
from cogs.economy import DB_PATH, get_setting, set_setting


class DatabaseBackup(commands.Cog):
    AUTO_ENABLED_KEY = "webdav_backup_enabled"
    INTERVAL_MINUTES_KEY = "webdav_backup_interval_minutes"
    LAST_ATTEMPT_KEY = "webdav_backup_last_attempt"
    LAST_SUCCESS_KEY = "webdav_backup_last_success"
    LAST_ERROR_KEY = "webdav_backup_last_error"
    DEFAULT_INTERVAL_MINUTES = 360
    MIN_INTERVAL_MINUTES = 10
    MAX_INTERVAL_MINUTES = 7 * 24 * 60

    def __init__(self, bot):
        self.bot = bot
        self.auto_backup_task.start()

    def cog_unload(self):
        self.auto_backup_task.cancel()

    @staticmethod
    def _utc_timestamp_text(raw_value: str | None):
        try:
            value = int(float(raw_value or "0"))
        except (TypeError, ValueError):
            return "Never"

        if value <= 0:
            return "Never"

        return f"<t:{value}:F> (<t:{value}:R>)"

    def _is_webdav_configured(self) -> bool:
        return all((WEBDAV_BACKUP_URL, WEBDAV_USERNAME, WEBDAV_PASSWORD))

    def _is_auto_enabled(self) -> bool:
        stored = (get_setting(self.AUTO_ENABLED_KEY, "false") or "false").strip().lower()
        return stored in {"1", "true", "on", "enabled", "enable"}

    def _set_auto_enabled(self, enabled: bool):
        set_setting(self.AUTO_ENABLED_KEY, "true" if enabled else "false")

    def _get_interval_minutes(self) -> int:
        raw_value = get_setting(self.INTERVAL_MINUTES_KEY, str(self.DEFAULT_INTERVAL_MINUTES))
        try:
            value = int(float(raw_value))
        except (TypeError, ValueError):
            value = self.DEFAULT_INTERVAL_MINUTES

        return max(self.MIN_INTERVAL_MINUTES, min(self.MAX_INTERVAL_MINUTES, value))

    def _set_interval_minutes(self, value: int):
        clamped = max(self.MIN_INTERVAL_MINUTES, min(self.MAX_INTERVAL_MINUTES, int(value)))
        set_setting(self.INTERVAL_MINUTES_KEY, str(clamped))

    async def _notify_owner(self, *, title: str, description: str, color: discord.Color):
        owner_id = getattr(self.bot, "owner_id", None)
        if not owner_id:
            return

        owner = self.bot.get_user(owner_id)
        if owner is None:
            try:
                owner = await self.bot.fetch_user(owner_id)
            except Exception:
                owner = None

        if owner is None:
            return

        embed = discord.Embed(
            title=title,
            description=description[:4000],
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        try:
            await owner.send(embed=embed)
        except Exception as exc:
            print(f"Failed to DM backup notification to owner: {exc}")

    @staticmethod
    def _build_backup_paths():
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        backup_dir = os.path.join(root_dir, "artifacts", "db_backups")
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        local_path = os.path.join(backup_dir, f"economy_{timestamp}.db")
        return local_path, timestamp

    @staticmethod
    def _create_sqlite_snapshot(target_path: str):
        source_conn = sqlite3.connect(DB_PATH)
        target_conn = sqlite3.connect(target_path)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
            source_conn.close()

    async def _upload_file_to_webdav(self, local_path: str, remote_filename: str):
        if self.bot.http_session is None or self.bot.http_session.closed:
            raise RuntimeError("HTTP session is unavailable right now.")

        upload_url = f"{WEBDAV_BACKUP_URL.rstrip('/')}/{remote_filename}"
        auth = aiohttp.BasicAuth(WEBDAV_USERNAME, WEBDAV_PASSWORD)

        with open(local_path, "rb") as handle:
            async with self.bot.http_session.put(
                upload_url,
                data=handle,
                auth=auth,
                headers={"Content-Type": "application/octet-stream"},
                timeout=aiohttp.ClientTimeout(total=180),
            ) as response:
                response_text = await response.text()
                if response.status not in {200, 201, 204}:
                    raise RuntimeError(f"WebDAV upload failed ({response.status}): {response_text[:240]}")

    async def _perform_backup(self, *, source: str = "manual"):
        if not self._is_webdav_configured():
            raise ValueError("WebDAV backup is not configured. Set WEBDAV_BACKUP_URL, WEBDAV_USERNAME, and WEBDAV_PASSWORD in `.env`.")

        local_path, timestamp = self._build_backup_paths()
        set_setting(self.LAST_ATTEMPT_KEY, str(int(time.time())))
        try:
            self._create_sqlite_snapshot(local_path)
            await self._upload_file_to_webdav(local_path, f"economy_{timestamp}.db")
            await self._upload_file_to_webdav(local_path, "economy_latest.db")
            set_setting(self.LAST_SUCCESS_KEY, str(int(time.time())))
            set_setting(self.LAST_ERROR_KEY, "")
            await self._notify_owner(
                title="✅ WebDAV Backup Complete",
                description=(
                    f"Source: `{source}`\n"
                    f"Uploaded files:\n"
                    f"- `economy_{timestamp}.db`\n"
                    f"- `economy_latest.db`\n"
                    f"Target: `{WEBDAV_BACKUP_URL}`"
                ),
                color=discord.Color.green(),
            )
            return local_path, timestamp
        except Exception as exc:
            set_setting(self.LAST_ERROR_KEY, str(exc)[:500])
            await self._notify_owner(
                title="❌ WebDAV Backup Failed",
                description=(
                    f"Source: `{source}`\n"
                    f"Error: `{str(exc)[:1500]}`\n"
                    f"Target: `{WEBDAV_BACKUP_URL or '(not configured)'}`"
                ),
                color=discord.Color.red(),
            )
            raise

    @tasks.loop(minutes=10)
    async def auto_backup_task(self):
        if not self._is_auto_enabled():
            return

        if not self._is_webdav_configured():
            return

        now = int(time.time())
        interval_seconds = self._get_interval_minutes() * 60
        last_attempt_raw = get_setting(self.LAST_ATTEMPT_KEY, "0")
        try:
            last_attempt = int(float(last_attempt_raw))
        except (TypeError, ValueError):
            last_attempt = 0

        if last_attempt and (now - last_attempt) < interval_seconds:
            return

        try:
            local_path, _timestamp = await self._perform_backup(source="auto")
            try:
                os.remove(local_path)
            except OSError:
                pass
            print("WebDAV backup completed successfully.")
        except Exception as exc:
            print(f"WebDAV backup failed: {exc}")

    @auto_backup_task.before_loop
    async def before_auto_backup_task(self):
        await self.bot.wait_until_ready()

    @commands.command(name="dbbackup")
    @commands.is_owner()
    async def dbbackup_command(self, ctx: commands.Context):
        if not self._is_webdav_configured():
            await ctx.send("WebDAV backup is not configured yet. Please set `WEBDAV_BACKUP_URL`, `WEBDAV_USERNAME`, and `WEBDAV_PASSWORD` in `.env`.")
            return

        local_path = None
        try:
            async with ctx.typing():
                local_path, timestamp = await self._perform_backup(source="manual")

            await ctx.send(
                f"✅ WebDAV backup uploaded successfully.\n"
                f"Files: `economy_{timestamp}.db` and `economy_latest.db`"
            )
        except Exception as exc:
            print(f"Error in !dbbackup command: {exc}")
            await ctx.send(f"❌ WebDAV backup failed: {exc}")
        finally:
            if local_path and os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except OSError:
                    pass

    @commands.command(name="dbbackupauto")
    @commands.is_owner()
    async def dbbackupauto_command(self, ctx: commands.Context, state: str = None):
        if state is None:
            current = "ON" if self._is_auto_enabled() else "OFF"
            await ctx.send(
                f"WebDAV auto backup is currently **{current}**.\n"
                f"Usage: `{COMMAND_PREFIX}dbbackupauto on` or `{COMMAND_PREFIX}dbbackupauto off`"
            )
            return

        normalized = state.strip().lower()
        if normalized in {"on", "enable", "enabled", "true", "1"}:
            self._set_auto_enabled(True)
            await ctx.send("✅ WebDAV auto backup has been **enabled**.")
            return

        if normalized in {"off", "disable", "disabled", "false", "0"}:
            self._set_auto_enabled(False)
            await ctx.send("✅ WebDAV auto backup has been **disabled**.")
            return

        await ctx.send(f"Usage: `{COMMAND_PREFIX}dbbackupauto on` or `{COMMAND_PREFIX}dbbackupauto off`")

    @commands.command(name="dbbackupinterval")
    @commands.is_owner()
    async def dbbackupinterval_command(self, ctx: commands.Context, minutes: int = None):
        if minutes is None:
            current = self._get_interval_minutes()
            await ctx.send(
                f"Current WebDAV backup interval: **{current} minutes**.\n"
                f"Usage: `{COMMAND_PREFIX}dbbackupinterval <minutes>`"
            )
            return

        if minutes < self.MIN_INTERVAL_MINUTES or minutes > self.MAX_INTERVAL_MINUTES:
            await ctx.send(
                f"Please choose a value between **{self.MIN_INTERVAL_MINUTES}** and **{self.MAX_INTERVAL_MINUTES}** minutes."
            )
            return

        self._set_interval_minutes(minutes)
        await ctx.send(f"✅ WebDAV backup interval set to **{minutes} minutes**.")

    @commands.command(name="dbbackupstatus")
    @commands.is_owner()
    async def dbbackupstatus_command(self, ctx: commands.Context):
        configured = "Yes" if self._is_webdav_configured() else "No"
        auto_enabled = "🟢 Enabled" if self._is_auto_enabled() else "🔴 Disabled"
        interval = self._get_interval_minutes()
        last_attempt = self._utc_timestamp_text(get_setting(self.LAST_ATTEMPT_KEY, "0"))
        last_success = self._utc_timestamp_text(get_setting(self.LAST_SUCCESS_KEY, "0"))
        last_error = (get_setting(self.LAST_ERROR_KEY, "") or "").strip() or "None"
        target = WEBDAV_BACKUP_URL or "(not configured)"

        embed = discord.Embed(title="WebDAV Backup Status", color=discord.Color.teal())
        embed.add_field(name="Configured", value=configured, inline=True)
        embed.add_field(name="Auto Backup", value=auto_enabled, inline=True)
        embed.add_field(name="Interval", value=f"{interval} min", inline=True)
        embed.add_field(name="Last Attempt", value=last_attempt, inline=False)
        embed.add_field(name="Last Success", value=last_success, inline=False)
        embed.add_field(name="Target", value=f"`{target}`", inline=False)
        embed.add_field(name="Last Error", value=last_error[:1024], inline=False)
        await ctx.send(embed=embed)

    @dbbackup_command.error
    @dbbackupauto_command.error
    @dbbackupinterval_command.error
    @dbbackupstatus_command.error
    async def backup_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.NotOwner):
            await ctx.send("Only the bot owner can manage WebDAV backups.")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send("Invalid argument. Please check the command usage and try again.")
            return
        raise error


async def setup(bot):
    await bot.add_cog(DatabaseBackup(bot))
