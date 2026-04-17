import asyncio
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
    LAST_RESTORE_KEY = "webdav_backup_last_restore"
    LAST_RESTORE_ERROR_KEY = "webdav_backup_last_restore_error"
    DEFAULT_INTERVAL_MINUTES = 360
    MIN_INTERVAL_MINUTES = 10
    MAX_INTERVAL_MINUTES = 7 * 24 * 60
    RESTORE_ARTIFACT_KEEP_COUNT = 5

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

    async def _restart_bot_process(self, *, reason: str):
        await self._notify_owner(
            title="🔄 Bot Restart Requested",
            description=f"Reason: {reason}\nSystemd should restart the bot automatically.",
            color=discord.Color.orange(),
        )
        await asyncio.sleep(2)
        await self.bot.close()

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
    def _build_restore_paths(remote_filename: str):
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        restore_dir = os.path.join(root_dir, "artifacts", "db_backups", "restores")
        os.makedirs(restore_dir, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        safe_name = os.path.basename(remote_filename) or "economy_remote.db"
        downloaded_path = os.path.join(restore_dir, f"downloaded_{timestamp}_{safe_name}")
        pre_restore_path = os.path.join(restore_dir, f"economy_before_restore_{timestamp}.db")
        return downloaded_path, pre_restore_path, timestamp

    def _cleanup_restore_artifacts(self):
        root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        restore_dir = os.path.join(root_dir, "artifacts", "db_backups", "restores")
        if not os.path.isdir(restore_dir):
            return

        groups = {
            "downloaded_": [],
            "economy_before_restore_": [],
        }

        for entry in os.scandir(restore_dir):
            if not entry.is_file():
                continue
            for prefix in groups:
                if entry.name.startswith(prefix):
                    groups[prefix].append(entry.path)
                    break

        for _prefix, paths in groups.items():
            paths.sort(key=lambda path: os.path.getmtime(path), reverse=True)
            for old_path in paths[self.RESTORE_ARTIFACT_KEEP_COUNT:]:
                try:
                    os.remove(old_path)
                except OSError as exc:
                    print(f"Failed to remove old restore artifact {old_path}: {exc}")

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

    async def _download_file_from_webdav(self, remote_filename: str, local_path: str):
        if self.bot.http_session is None or self.bot.http_session.closed:
            raise RuntimeError("HTTP session is unavailable right now.")

        download_url = f"{WEBDAV_BACKUP_URL.rstrip('/')}/{remote_filename}"
        auth = aiohttp.BasicAuth(WEBDAV_USERNAME, WEBDAV_PASSWORD)

        async with self.bot.http_session.get(
            download_url,
            auth=auth,
            timeout=aiohttp.ClientTimeout(total=180),
        ) as response:
            if response.status != 200:
                response_text = await response.text()
                raise RuntimeError(f"WebDAV download failed ({response.status}): {response_text[:240]}")

            with open(local_path, "wb") as handle:
                async for chunk in response.content.iter_chunked(1024 * 256):
                    handle.write(chunk)

    @staticmethod
    def _validate_sqlite_file(local_path: str):
        with open(local_path, "rb") as handle:
            header = handle.read(16)
        if not header.startswith(b"SQLite format 3"):
            raise RuntimeError("Downloaded file is not a valid SQLite database.")

        conn = sqlite3.connect(local_path)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
        finally:
            conn.close()

        if not row or row[0].lower() != "ok":
            raise RuntimeError(f"SQLite quick_check failed: {row[0] if row else 'unknown error'}")

    @staticmethod
    def _restore_sqlite_database(source_path: str):
        source_conn = sqlite3.connect(source_path)
        target_conn = sqlite3.connect(DB_PATH, timeout=60)
        try:
            source_conn.backup(target_conn)
        finally:
            target_conn.close()
            source_conn.close()

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

    async def _perform_restore(self, remote_filename: str):
        if not self._is_webdav_configured():
            raise ValueError("WebDAV backup is not configured. Set WEBDAV_BACKUP_URL, WEBDAV_USERNAME, and WEBDAV_PASSWORD in `.env`.")

        downloaded_path, pre_restore_path, timestamp = self._build_restore_paths(remote_filename)
        try:
            await self._download_file_from_webdav(remote_filename, downloaded_path)
            self._validate_sqlite_file(downloaded_path)
            self._create_sqlite_snapshot(pre_restore_path)
            self._restore_sqlite_database(downloaded_path)
            set_setting(self.LAST_RESTORE_KEY, str(int(time.time())))
            set_setting(self.LAST_RESTORE_ERROR_KEY, "")
            await self._notify_owner(
                title="✅ WebDAV Restore Complete",
                description=(
                    f"Restored from: `{remote_filename}`\n"
                    f"Downloaded copy: `{downloaded_path}`\n"
                    f"Pre-restore snapshot: `{pre_restore_path}`\n"
                    f"Target DB: `{DB_PATH}`"
                ),
                color=discord.Color.green(),
            )
            self._cleanup_restore_artifacts()
            return downloaded_path, pre_restore_path, timestamp
        except Exception as exc:
            set_setting(self.LAST_RESTORE_ERROR_KEY, str(exc)[:500])
            await self._notify_owner(
                title="❌ WebDAV Restore Failed",
                description=(
                    f"Requested file: `{remote_filename}`\n"
                    f"Error: `{str(exc)[:1500]}`\n"
                    f"Target DB: `{DB_PATH}`"
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
        last_restore = self._utc_timestamp_text(get_setting(self.LAST_RESTORE_KEY, "0"))
        last_error = (get_setting(self.LAST_ERROR_KEY, "") or "").strip() or "None"
        last_restore_error = (get_setting(self.LAST_RESTORE_ERROR_KEY, "") or "").strip() or "None"
        target = WEBDAV_BACKUP_URL or "(not configured)"

        embed = discord.Embed(title="WebDAV Backup Status", color=discord.Color.teal())
        embed.add_field(name="Configured", value=configured, inline=True)
        embed.add_field(name="Auto Backup", value=auto_enabled, inline=True)
        embed.add_field(name="Interval", value=f"{interval} min", inline=True)
        embed.add_field(name="Last Attempt", value=last_attempt, inline=False)
        embed.add_field(name="Last Success", value=last_success, inline=False)
        embed.add_field(name="Last Restore", value=last_restore, inline=False)
        embed.add_field(name="Target", value=f"`{target}`", inline=False)
        embed.add_field(name="Last Error", value=last_error[:1024], inline=False)
        embed.add_field(name="Last Restore Error", value=last_restore_error[:1024], inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="dbrestore")
    @commands.is_owner()
    async def dbrestore_command(self, ctx: commands.Context, target: str = None, confirm: str = None, action: str = None):
        if target is None:
            await ctx.send(
                f"Usage: `{COMMAND_PREFIX}dbrestore latest confirm` or `{COMMAND_PREFIX}dbrestore <remote_filename> confirm`\n"
                f"Optional restart: `{COMMAND_PREFIX}dbrestore latest confirm restart`\n"
                f"This will overwrite the local `economy.db` after making a safety snapshot."
            )
            return

        if (confirm or "").strip().lower() != "confirm":
            await ctx.send(
                f"Restore requires confirmation.\n"
                f"Run: `{COMMAND_PREFIX}dbrestore {target} confirm`"
            )
            return

        remote_filename = "economy_latest.db" if target.strip().lower() == "latest" else os.path.basename(target.strip())
        if not remote_filename:
            await ctx.send("Please provide a valid remote backup filename.")
            return

        try:
            async with ctx.typing():
                downloaded_path, pre_restore_path, _timestamp = await self._perform_restore(remote_filename)

            await ctx.send(
                "✅ Database restore completed.\n"
                f"Restored from: `{remote_filename}`\n"
                f"Safety snapshot saved to: `{pre_restore_path}`\n"
                f"Downloaded copy saved to: `{downloaded_path}`\n"
                "Recommended: restart the bot now to ensure every task uses the restored database."
            )
            if (action or "").strip().lower() == "restart":
                await ctx.send("🔄 Restarting bot now... systemd should bring it back in about 10 seconds.")
                await self._restart_bot_process(reason=f"Database restored from `{remote_filename}`")
        except Exception as exc:
            print(f"Error in !dbrestore command: {exc}")
            await ctx.send(f"❌ Database restore failed: {exc}")

    @commands.command(name="restartbot")
    @commands.is_owner()
    async def restartbot_command(self, ctx: commands.Context, confirm: str = None):
        if (confirm or "").strip().lower() != "confirm":
            await ctx.send(f"Usage: `{COMMAND_PREFIX}restartbot confirm`")
            return

        await ctx.send("🔄 Restarting bot now... systemd should bring it back in about 10 seconds.")
        await self._restart_bot_process(reason=f"Owner `{ctx.author}` requested restart")

    @dbbackup_command.error
    @dbbackupauto_command.error
    @dbbackupinterval_command.error
    @dbbackupstatus_command.error
    @dbrestore_command.error
    @restartbot_command.error
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
