import discord
from redbot.core import commands, Config, checks
import asyncio
import datetime

DEFAULT_RANKS = {
    100: "Bronze",
    500: "Silver",
    1000: "Gold",
    2500: "Platinum",
    5000: "Diamond"
}

class ActivityXP(commands.Cog):
    """Reward users with XP for chat and voice activity."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.config.register_guild(
            chat_xp_per_message=5,
            voice_xp_per_minute=2,
            ranks=DEFAULT_RANKS
        )
        self.config.register_member(
            xp=0,
            last_message=None,
            last_voice=None
        )
        self.voice_tasks = {}

    async def get_rank(self, guild, xp):
        ranks = await self.config.guild(guild).ranks()
        sorted_ranks = sorted((int(x), name) for x, name in ranks.items())
        current_rank = None
        for threshold, name in sorted_ranks:
            if xp >= threshold:
                current_rank = name
            else:
                break
        return current_rank or "Unranked"

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.guild or message.author.bot:
            return
        member = message.author
        guild = message.guild
        async with self.config.member(member).all() as data:
            now = datetime.datetime.utcnow()
            last_message = data.get("last_message")
            if last_message:
                last_message = datetime.datetime.fromisoformat(last_message)
                # Optional: prevent XP spam by adding a cooldown (e.g., 10s)
                if (now - last_message).total_seconds() < 10:
                    return
            chat_xp = await self.config.guild(guild).chat_xp_per_message()
            data["xp"] += chat_xp
            data["last_message"] = now.isoformat()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # Only track if user is in a guild
        if not member.guild:
            return

        # Remove task if user left voice
        if before.channel and (not after.channel or after.channel != before.channel):
            task = self.voice_tasks.pop(member.id, None)
            if task:
                task.cancel()

        # Start tracking if user joined a voice channel with >1 person
        if after.channel and (not before.channel or after.channel != before.channel):
            if len([m for m in after.channel.members if not m.bot]) > 1:
                task = asyncio.create_task(self._voice_xp_task(member, after.channel))
                self.voice_tasks[member.id] = task

    async def _voice_xp_task(self, member, channel):
        try:
            while True:
                await asyncio.sleep(60)
                # Only reward if still in channel and >1 person
                if member.voice and member.voice.channel == channel:
                    if len([m for m in channel.members if not m.bot]) > 1:
                        voice_xp = await self.config.guild(channel.guild).voice_xp_per_minute()
                        async with self.config.member(member).all() as data:
                            data["xp"] += voice_xp
                else:
                    break
        except asyncio.CancelledError:
            pass

    @commands.group()
    @commands.guild_only()
    async def activityxp(self, ctx):
        """Activity XP settings and info."""

    @activityxp.command()
    async def xp(self, ctx, member: discord.Member = None):
        """Show your or another user's XP and rank."""
        member = member or ctx.author
        xp = await self.config.member(member).xp()
        rank = await self.get_rank(ctx.guild, xp)
        await ctx.send(f"**{member.display_name}** has **{xp} XP** and is ranked **{rank}**.")

    @activityxp.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setchatxp(self, ctx, amount: int):
        """Set XP per chat message."""
        await self.config.guild(ctx.guild).chat_xp_per_message.set(amount)
        await ctx.send(f"Set chat XP per message to {amount}.")

    @activityxp.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setvoicexp(self, ctx, amount: int):
        """Set XP per minute in voice."""
        await self.config.guild(ctx.guild).voice_xp_per_minute.set(amount)
        await ctx.send(f"Set voice XP per minute to {amount}.")

    @activityxp.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setrank(self, ctx, xp: int, *, name: str):
        """Set a rank name for a given XP threshold."""
        async with self.config.guild(ctx.guild).ranks() as ranks:
            ranks[str(xp)] = name
        await ctx.send(f"Set rank '{name}' for {xp} XP.")

    @activityxp.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def removerank(self, ctx, xp: int):
        """Remove a rank at a given XP threshold."""
        async with self.config.guild(ctx.guild).ranks() as ranks:
            if str(xp) in ranks:
                del ranks[str(xp)]
                await ctx.send(f"Removed rank for {xp} XP.")
            else:
                await ctx.send("No rank at that XP threshold.")

    @activityxp.command()
    async def ranks(self, ctx):
        """Show all ranks."""
        ranks = await self.config.guild(ctx.guild).ranks()
        if not ranks:
            await ctx.send("No ranks set.")
            return
        sorted_ranks = sorted((int(x), name) for x, name in ranks.items())
        msg = "\n".join(f"{xp} XP: {name}" for xp, name in sorted_ranks)
        await ctx.send(f"**Ranks:**\n{msg}")

    def cog_unload(self):
        # Cancel all running voice tasks
        for task in self.voice_tasks.values():
            task.cancel()
