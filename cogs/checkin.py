# cogs/checkin.py — Check-in, streak, leaderboard commands
import discord
from discord.ext import commands
from config import CHECKIN_WORKER_URL, CHECKIN_AUTH_PASS


class Checkin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='ck', aliases=['checkin'])
    async def checkin_command(self, ctx: commands.Context, *, note: str = "Just vibing today"):
        """Log your daily check-in with AI (once per day, resets at 00:00 GMT+8)."""
        if not CHECKIN_WORKER_URL:
            await ctx.send("❌ Check-in is not configured. The bot owner needs to set `CHECKIN_WORKER_URL` in the `.env` file.")
            return

        payload = {
            "user_pass": CHECKIN_AUTH_PASS,
            "user_id": str(ctx.author.id),
            "user_name": str(ctx.author),
            "checkin_note": note
        }

        try:
            async with ctx.typing():
                async with self.bot.http_session.post(CHECKIN_WORKER_URL, json=payload) as response:
                    data = await response.json()

                    if response.status == 200 and data.get("success"):
                        ai_reply = data.get("message", "AI is silent today.")
                        streak = data.get("streak", 0)
                        streak_text = f"\n🔥 Streak: **{streak} day{'s' if streak != 1 else ''}**" if streak else ""
                        await ctx.send(f"✅ **Check-in Logged!** ({ctx.author.mention})\n📝 *{note}*\n🤖: *{ai_reply}*{streak_text}")
                    elif response.status == 200 and not data.get("success"):
                        error_msg = data.get("error", "You already checked in today!")
                        await ctx.send(f"⏰ {ctx.author.mention}, {error_msg}")
                    else:
                        error_msg = data.get("error", "Access Denied or Unknown Error")
                        await ctx.send(f"❌ **Check-in Failed:** {error_msg}")
        except Exception as e:
            print(f"Error in !ck command: {e}")
            await ctx.send(f"❌ **Worker Error:** Could not reach the check-in server.")

    @commands.command(name='streak')
    async def streak_command(self, ctx: commands.Context):
        """Show your current check-in streak."""
        if not CHECKIN_WORKER_URL:
            await ctx.send("❌ Check-in is not configured.")
            return

        payload = {
            "user_pass": CHECKIN_AUTH_PASS,
            "action": "streak",
            "user_id": str(ctx.author.id),
        }

        try:
            async with self.bot.http_session.post(CHECKIN_WORKER_URL, json=payload) as response:
                data = await response.json()
                if data.get("success"):
                    streak = data.get("streak", 0)
                    total = data.get("total_checkins", 0)
                    checked = data.get("checked_today", False)
                    today_icon = "✅" if checked else "❌"
                    embed = discord.Embed(title=f"🔥 {ctx.author.display_name}'s Streak", color=discord.Color.orange())
                    embed.add_field(name="Current Streak", value=f"**{streak}** day{'s' if streak != 1 else ''}", inline=True)
                    embed.add_field(name="Total Check-ins", value=f"**{total}**", inline=True)
                    embed.add_field(name="Today", value=f"{today_icon} {'Checked in' if checked else 'Not yet'}", inline=True)
                    await ctx.send(embed=embed)
                else:
                    await ctx.send(f"❌ {data.get('error', 'Unknown error')}")
        except Exception as e:
            print(f"Error in !streak command: {e}")
            await ctx.send("❌ Could not reach the check-in server.")

    @commands.command(name='lb', aliases=['leaderboard'])
    async def leaderboard_command(self, ctx: commands.Context):
        """Show top 10 check-in streaks."""
        if not CHECKIN_WORKER_URL:
            await ctx.send("❌ Check-in is not configured.")
            return

        payload = {
            "user_pass": CHECKIN_AUTH_PASS,
            "action": "leaderboard",
        }

        try:
            async with self.bot.http_session.post(CHECKIN_WORKER_URL, json=payload) as response:
                data = await response.json()
                if data.get("success"):
                    board = data.get("leaderboard", [])
                    if not board:
                        await ctx.send("📊 No check-in streaks yet! Use `!ck` to start.")
                        return
                    embed = discord.Embed(title="🏆 Check-in Leaderboard", description="Top streaks (resets at 00:00 GMT+8)", color=discord.Color.gold())
                    medals = ["🥇", "🥈", "🥉"]
                    lines = []
                    for i, entry in enumerate(board):
                        medal = medals[i] if i < 3 else f"`{i+1}.`"
                        name = entry.get("user_name", "Unknown").split("#")[0]
                        streak = entry.get("streak", 0)
                        lines.append(f"{medal} **{name}** — {streak} day{'s' if streak != 1 else ''}")
                    embed.add_field(name="Rankings", value="\n".join(lines), inline=False)
                    await ctx.send(embed=embed)
                else:
                    await ctx.send(f"❌ {data.get('error', 'Unknown error')}")
        except Exception as e:
            print(f"Error in !lb command: {e}")
            await ctx.send("❌ Could not reach the check-in server.")


async def setup(bot):
    await bot.add_cog(Checkin(bot))
