import discord
from redbot.core import commands, Config, checks
import asyncio
import datetime

DEFAULT_RANKS = {
    120: "Rank 1",
    480: "Rank 2",
    1080: "Rank 3",
    1920: "Rank 4",
    3000: "Rank 5",
    4320: "Rank 6",
    5880: "Rank 7",
    7680: "Rank 8",
    9720: "Rank 9",
    12000: "Rank 10",
    14520: "Rank 11",
    17280: "Rank 12",
    20280: "Rank 13",
    23520: "Rank 14",
    27000: "Rank 15",
    30720: "Rank 16",
    34680: "Rank 17",
    38880: "Rank 18",
    43320: "Rank 19",
    48000: "Rank 20",
    52920: "Rank 21",
    58080: "Rank 22",
    63480: "Rank 23",
    69120: "Rank 24",
    75000: "Rank 25",
    81120: "Rank 26",
    87480: "Rank 27",
    94080: "Rank 28",
    100920: "Rank 29",
    108000: "Rank 30"
}

class ActivityXP(commands.Cog):
    """Reward users with XP for chat and voice activity, with ranks and role rewards."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.config.register_guild(
            chat_xp_per_message=10,
            voice_xp_per_minute=5,
            ranks=DEFAULT_RANKS,
            rank_roles={}  # XP threshold (str) -> role_id (int)
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

    async def _update_member_roles(self, member, new_xp):
        guild = member.guild
        rank_roles = await self.config.guild(guild).rank_roles()
        if not rank_roles:
            return

        thresholds = sorted((int(xp), int(role_id)) for xp, role_id in rank_roles.items())
        roles_to_give = []
        roles_to_remove = []

        highest = None
        for xp, role_id in thresholds:
            if new_xp >= xp:
                highest = role_id
            else:
                break

        for xp, role_id in thresholds:
            role = guild.get_role(role_id)
            if not role:
                continue
            if role in member.roles:
                if role_id != highest:
                    roles_to_remove.append(role)
            elif role_id == highest:
                roles_to_give.append(role)

        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="Rank up")
        if roles_to_give:
            await member.add_roles(*roles_to_give, reason="Rank up")

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
                if (now - last_message).total_seconds() < 10:
                    return
            chat_xp = await self.config.guild(guild).chat_xp_per_message()
            data["xp"] += chat_xp
            data["last_message"] = now.isoformat()
            await self._update_member_roles(member, data["xp"])

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
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
                if member.voice and member.voice.channel == channel:
                    if len([m for m in channel.members if not m.bot]) > 1:
                        voice_xp = await self.config.guild(channel.guild).voice_xp_per_minute()
                        async with self.config.member(member).all() as data:
                            data["xp"] += voice_xp
                            await self._update_member_roles(member, data["xp"])
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

    @activityxp.command(name="bulkranks")
    @checks.admin_or_permissions(manage_guild=True)
    async def bulkranks(self, ctx, *, ranks: str):
        """
        Bulk add ranks. Format: XP:Rank Name, XP:Rank Name, ...
        Example: 100:Bronze, 500:Silver, 1000:Gold
        """
        pairs = [pair.strip() for pair in ranks.split(",")]
        added = []
        async with self.config.guild(ctx.guild).ranks() as ranks_conf:
            for pair in pairs:
                if ":" not in pair:
                    continue
                xp_str, name = pair.split(":", 1)
                try:
                    xp = int(xp_str.strip())
                except ValueError:
                    continue
                name = name.strip()
                ranks_conf[str(xp)] = name
                added.append(f"{xp}: {name}")
        if added:
            await ctx.send(f"Added ranks:\n" + "\n".join(added))
        else:
            await ctx.send("No valid ranks provided.")

    @activityxp.command(name="clearranks")
    @checks.admin_or_permissions(manage_guild=True)
    async def clearranks(self, ctx):
        """Remove all ranks."""
        await self.config.guild(ctx.guild).ranks.clear()
        await ctx.send("All ranks have been cleared.")

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

    @activityxp.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setrankrole(self, ctx, xp: int, role: discord.Role):
        """Link a role to a rank (XP threshold)."""
        async with self.config.guild(ctx.guild).rank_roles() as rr:
            rr[str(xp)] = role.id
        await ctx.send(f"Linked {role.mention} to {xp} XP.")

    @activityxp.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def removerankrole(self, ctx, xp: int):
        """Remove the role linked to a rank (XP threshold)."""
        async with self.config.guild(ctx.guild).rank_roles() as rr:
            if str(xp) in rr:
                del rr[str(xp)]
                await ctx.send(f"Removed role link for {xp} XP.")
            else:
                await ctx.send("No role linked to that XP threshold.")

    @activityxp.command()
    async def rankroles(self, ctx):
        """Show all rank role links."""
        rank_roles = await self.config.guild(ctx.guild).rank_roles()
        if not rank_roles:
            await ctx.send("No rank roles set.")
            return
        msg = []
        for xp, role_id in sorted(rank_roles.items(), key=lambda x: int(x[0])):
            role = ctx.guild.get_role(int(role_id))
            if role:
                msg.append(f"{xp} XP: {role.mention}")
            else:
                msg.append(f"{xp} XP: (role not found)")
        await ctx.send("**Rank Roles:**\n" + "\n".join(msg))

    def cog_unload(self):
        for task in self.voice_tasks.values():
            task.cancel()
