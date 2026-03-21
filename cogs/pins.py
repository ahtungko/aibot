# cogs/pins.py — Bookmark / pin commands
import time
import discord
from discord.ext import commands
from config import COMMAND_PREFIX
from utils.storage import load_pins, save_pins


class Pins(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='pin')
    async def pin_command(self, ctx: commands.Context):
        """Reply to a message with !pin to bookmark it."""
        if not ctx.message.reference:
            await ctx.send(f"📌 Reply to a message with `{COMMAND_PREFIX}pin` to bookmark it.")
            return

        ref_msg = ctx.message.reference.resolved
        if ref_msg is None:
            try:
                ref_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            except Exception:
                await ctx.send("❌ Could not find the referenced message.")
                return

        uid = str(ctx.author.id)
        pins = load_pins()
        if uid not in pins:
            pins[uid] = []

        if len(pins[uid]) >= 50:
            await ctx.send("📌 You've reached the maximum of 50 pins. Use `!unpin [number]` to remove some.")
            return

        pin_entry = {
            "content": ref_msg.content[:500] if ref_msg.content else "[attachment/embed]",
            "author": str(ref_msg.author.display_name),
            "channel": str(ctx.channel.name),
            "url": ref_msg.jump_url,
            "saved_at": time.strftime("%Y-%m-%d %H:%M"),
        }
        pins[uid].append(pin_entry)
        save_pins(pins)
        await ctx.send(f"📌 Pinned! You now have **{len(pins[uid])}** bookmark(s). View with `{COMMAND_PREFIX}pins`.")

    @commands.command(name='pins', aliases=['bookmarks'])
    async def pins_command(self, ctx: commands.Context):
        """List your saved bookmarks."""
        uid = str(ctx.author.id)
        pins = load_pins()
        user_pins = pins.get(uid, [])

        if not user_pins:
            await ctx.send(f"📭 No pins yet! Reply to a message with `{COMMAND_PREFIX}pin` to bookmark it.")
            return

        embed = discord.Embed(title=f"📌 {ctx.author.display_name}'s Pins", color=discord.Color.teal())
        recent = list(reversed(user_pins[-10:]))
        for i, pin in enumerate(recent):
            idx = len(user_pins) - i
            preview = pin['content'][:100]
            embed.add_field(
                name=f"#{idx} — {pin.get('author', '?')} in #{pin.get('channel', '?')}",
                value=f"{preview}\n[Jump to message]({pin['url']}) • {pin.get('saved_at', '')}",
                inline=False
            )
        if len(user_pins) > 10:
            embed.set_footer(text=f"Showing latest 10 of {len(user_pins)} pins")
        await ctx.send(embed=embed)

    @commands.command(name='unpin')
    async def unpin_command(self, ctx: commands.Context, number: int = None):
        """Remove a pin by its number."""
        if number is None:
            await ctx.send(f"Usage: `{COMMAND_PREFIX}unpin [number]` — check `{COMMAND_PREFIX}pins` for numbers.")
            return

        uid = str(ctx.author.id)
        pins = load_pins()
        user_pins = pins.get(uid, [])

        if number < 1 or number > len(user_pins):
            await ctx.send(f"❌ Invalid pin number. You have {len(user_pins)} pin(s).")
            return

        removed = user_pins.pop(number - 1)
        pins[uid] = user_pins
        save_pins(pins)
        preview = removed['content'][:50]
        await ctx.send(f"🗑️ Removed pin #{number}: *{preview}...*")


async def setup(bot):
    await bot.add_cog(Pins(bot))
