import discord
from redbot.core import commands, Config, checks
import asyncio
import datetime

class ActivityXP(commands.Cog):
    """Reward users with XP for chat and voice activity, with ranks and role rewards."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.config.register_guild(
            chat_xp_per_message=10,
            voice_xp_per_minute=5,
            ranks={},
            rank_roles={}
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

    @activityxp.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setup(self, ctx):
        """
        Interactive setup for ranks, roles, and XP rates.
        """
        def check_author(m):
            return m.author == ctx.author and m.channel == ctx.channel

        await ctx.send("Welcome to ActivityXP setup!\nHow many ranks do you want? (e.g. 10, 20, 30)")
        try:
            msg = await self.bot.wait_for("message", check=check_author, timeout=120)
            num_ranks = int(msg.content)
            if num_ranks < 2 or num_ranks > 50:
                await ctx.send("Please choose between 2 and 50 ranks.")
                return
        except (ValueError, asyncio.TimeoutError):
            await ctx.send("Setup cancelled.")
            return

        rank_names = []
        rank_roles = []
        for i in range(1, num_ranks + 1):
            await ctx.send(f"Enter the name for rank {i}:")
            try:
                msg = await self.bot.wait_for("message", check=check_author, timeout=120)
                rank_name = msg.content.strip()
            except asyncio.TimeoutError:
                await ctx.send("Setup cancelled.")
                return

            # List roles and ask to pick one or create new
            roles = [role for role in ctx.guild.roles if role < ctx.guild.me.top_role and not role.is_default()]
            roles = sorted(roles, key=lambda r: r.position, reverse=True)
            role_list = "\n".join(f"{idx+1}. {role.name}" for idx, role in enumerate(roles))
            await ctx.send(
                f"Choose a role for **{rank_name}**:\n"
                f"{role_list}\n"
                f"Type the number to pick, or type a new role name to create it."
            )
            try:
                msg = await self.bot.wait_for("message", check=check_author, timeout=120)
                choice = msg.content.strip()
                try:
                    idx = int(choice) - 1
                    if 0 <= idx < len(roles):
                        role = roles[idx]
                    else:
                        await ctx.send("Invalid number. Setup cancelled.")
                        return
                except ValueError:
                    # Create new role
                    try:
                        role = await ctx.guild.create_role(name=choice)
                        await ctx.send(f"Created role {role.mention}.")
                    except discord.Forbidden:
                        await ctx.send("I don't have permission to create roles. Setup cancelled.")
                        return
            except asyncio.TimeoutError:
                await ctx.send("Setup cancelled.")
                return

            rank_names.append(rank_name)
            rank_roles.append(role.id)

        await ctx.send("How many months should it take an active user to reach the highest rank? (e.g. 6)")
        try:
            msg = await self.bot.wait_for("message", check=check_author, timeout=120)
            months = float(msg.content)
            if months <= 0 or months > 24:
                await ctx.send("Please choose between 1 and 24 months.")
                return
        except (ValueError, asyncio.TimeoutError):
            await ctx.send("Setup cancelled.")
            return

        await ctx.send("How many messages per day is an 'active' user? (e.g. 30)")
        try:
            msg = await self.bot.wait_for("message", check=check_author, timeout=120)
            messages_per_day = int(msg.content)
            if messages_per_day < 1 or messages_per_day > 500:
                await ctx.send("Please choose a reasonable number of messages per day.")
                return
        except (ValueError, asyncio.TimeoutError):
            await ctx.send("Setup cancelled.")
            return

        await ctx.send("How many minutes in voice per day is an 'active' user? (e.g. 60)")
        try:
            msg = await self.bot.wait_for("message", check=check_author, timeout=120)
            voice_minutes_per_day = int(msg.content)
            if voice_minutes_per_day < 0 or voice_minutes_per_day > 1440:
                await ctx.send("Please choose a reasonable number of minutes per day.")
                return
        except (ValueError, asyncio.TimeoutError):
            await ctx.send("Setup cancelled.")
            return

        await ctx.send("How much XP should a chat message give? (e.g. 10)")
        try:
            msg = await self.bot.wait_for("message", check=check_author, timeout=120)
            chat_xp_per_message = int(msg.content)
            if chat_xp_per_message < 1 or chat_xp_per_message > 100:
                await ctx.send("Please choose a reasonable XP per message.")
                return
        except (ValueError, asyncio.TimeoutError):
            await ctx.send("Setup cancelled.")
            return

        await ctx.send("How much XP should a minute in voice give? (e.g. 5)")
        try:
            msg = await self.bot.wait_for("message", check=check_author, timeout=120)
            voice_xp_per_minute = int(msg.content)
            if voice_xp_per_minute < 1 or voice_xp_per_minute > 100:
                await ctx.send("Please choose a reasonable XP per minute.")
                return
        except (ValueError, asyncio.TimeoutError):
            await ctx.send("Setup cancelled.")
            return

        # Calculate total XP needed for top rank
        days = months * 30
        total_xp = (
            messages_per_day * chat_xp_per_message +
            voice_minutes_per_day * voice_xp_per_minute
        ) * days

        # Quadratic progression
        a = total_xp / (num_ranks ** 2)
        xp_thresholds = [int(a * (i + 1) ** 2) for i in range(num_ranks)]

        # Save to config
        ranks = {str(xp): name for xp, name in zip(xp_thresholds, rank_names)}
        rank_roles_dict = {str(xp): role_id for xp, role_id in zip(xp_thresholds, rank_roles)}

        await self.config.guild(ctx.guild).ranks.set(ranks)
        await self.config.guild(ctx.guild).rank_roles.set(rank_roles_dict)
        await self.config.guild(ctx.guild).chat_xp_per_message.set(chat_xp_per_message)
        await self.config.guild(ctx.guild).voice_xp_per_minute.set(voice_xp_per_minute)

        await ctx.send(
            f"Setup complete!\n"
            f"Ranks: {', '.join(rank_names)}\n"
            f"XP per message: {chat_xp_per_message}\n"
            f"XP per voice minute: {voice_xp_per_minute}\n"
            f"Total XP for top rank: {int(total_xp)}\n"
            f"XP thresholds: {', '.join(str(x) for x in xp_thresholds)}"
        )

    def cog_unload(self):
        for task in self.voice_tasks.values():
            task.cancel()
