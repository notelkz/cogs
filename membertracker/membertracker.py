from redbot.core import commands, Config
import discord
from datetime import datetime, timedelta
import asyncio
from typing import Optional

class MemberTracker(commands.Cog):
    """Tracks member joins and manages roles after a specified time period"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "notification_channel": None,
            "wait_period_seconds": 1209600,  # 14 days in seconds
            "wait_period_display": "14 days",
            "member_joins": {},
            "roles_to_add": [],
            "roles_to_remove": [],
            "notify_role_changes": True,
            "testing_mode": False
        }
        self.config.register_guild(**default_guild)
        self.bg_task = self.bot.loop.create_task(self.check_member_duration())

    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.bg_task.cancel()

    async def check_member_duration(self):
        """Background task to check member duration and manage roles"""
        await self.bot.wait_until_ready()
        while True:
            try:
                all_guilds = await self.config.all_guilds()
                for guild_id, guild_data in all_guilds.items():
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        continue

                    channel_id = guild_data.get("notification_channel")
                    if not channel_id:
                        continue

                    channel = guild.get_channel(channel_id)
                    if not channel:
                        continue

                    wait_period_seconds = guild_data.get("wait_period_seconds", 1209600)
                    member_joins = guild_data.get("member_joins", {})
                    notify_changes = guild_data.get("notify_role_changes", True)
                    current_time = datetime.utcnow()

                    for member_id, join_date_str in member_joins.copy().items():
                        join_date = datetime.strptime(join_date_str, "%d/%m/%Y")
                        seconds_passed = (current_time - join_date).total_seconds()

                        if seconds_passed >= wait_period_seconds:
                            member = guild.get_member(int(member_id))
                            if member:
                                roles_to_add = [guild.get_role(role_id) for role_id in guild_data.get("roles_to_add", [])]
                                roles_to_remove = [guild.get_role(role_id) for role_id in guild_data.get("roles_to_remove", [])]

                                try:
                                    # Add roles
                                    for role in roles_to_add:
                                        if role and role not in member.roles:
                                            await member.add_roles(role)

                                    # Remove roles
                                    for role in roles_to_remove:
                                        if role and role in member.roles:
                                            await member.remove_roles(role)

                                    # Base notification
                                    message = (
                                        f"🎉 {member.mention} has been a member for {guild_data.get('wait_period_display', '14 days')}! "
                                        f"They joined on {join_date_str}"
                                    )

                                    # Add role change information if enabled
                                    if notify_changes:
                                        role_changes = []
                                        if roles_to_add:
                                            role_changes.append(f"Added roles: {', '.join(role.name for role in roles_to_add if role)}")
                                        if roles_to_remove:
                                            role_changes.append(f"Removed roles: {', '.join(role.name for role in roles_to_remove if role)}")
                                        if role_changes:
                                            message += f"\n{' | '.join(role_changes)}"

                                    await channel.send(message)

                                except discord.Forbidden:
                                    await channel.send(f"⚠️ Failed to modify roles for {member.mention} due to permissions.")

                            # Remove the member from tracking after processing
                            async with self.config.guild(guild).member_joins() as joins:
                                joins.pop(str(member_id), None)

            except Exception as e:
                print(f"Error in member duration check: {e}")

            # Check frequency based on testing mode
            testing_mode = False
            for guild_id in all_guilds:
                guild_data = await self.config.guild(self.bot.get_guild(guild_id)).all()
                if guild_data.get("testing_mode", False):
                    testing_mode = True
                    break
            
            await asyncio.sleep(1 if testing_mode else 300)

    @commands.group()
    @commands.admin_or_permissions(administrator=True)
    async def membertrack(self, ctx):
        """Member tracking commands"""
        pass

    @membertrack.command()
    async def test(self, ctx):
        """Test if the cog is working"""
        await ctx.send("MemberTracker cog is working!")

    @membertrack.command()
    async def setchannel(self, ctx, channel: discord.TextChannel):
        """Set the notification channel"""
        await self.config.guild(ctx.guild).notification_channel.set(channel.id)
        await ctx.send(f"Channel set to {channel.mention}")

    @membertrack.command()
    async def setperiod(self, ctx, amount: int, unit: str = "days"):
        """Set the waiting period
        
        Units can be:
        - days (default)
        - hours
        - seconds (testing only)
        
        Example:
        [p]membertrack setperiod 14 days
        [p]membertrack setperiod 48 hours
        [p]membertrack setperiod 30 seconds (testing only)
        """
        unit = unit.lower()
        allowed_units = ["days", "day", "hours", "hour", "seconds", "second", "s", "h", "d"]
        
        if unit not in allowed_units:
            await ctx.send("Invalid unit. Please use 'days', 'hours', or 'seconds'.")
            return

        if amount < 1:
            await ctx.send("The amount must be at least 1.")
            return

        # Convert to seconds for storage
        if unit in ["days", "day", "d"]:
            seconds = amount * 86400
            display_unit = "days"
            display_amount = amount
        elif unit in ["hours", "hour", "h"]:
            seconds = amount * 3600
            display_unit = "hours"
            display_amount = amount
        elif unit in ["seconds", "second", "s"]:
            # Check if testing is enabled for this guild
            testing_enabled = await self.config.guild(ctx.guild).get_raw("testing_mode", default=False)
            if not testing_enabled:
                await ctx.send("Seconds are only available in testing mode. Use `[p]membertrack testing enable` first.")
                return
            seconds = amount
            display_unit = "seconds"
            display_amount = amount
        
        async with self.config.guild(ctx.guild).all() as guild_data:
            guild_data["wait_period_seconds"] = seconds
            guild_data["wait_period_display"] = f"{display_amount} {display_unit}"

        await ctx.send(f"Waiting period set to {display_amount} {display_unit}.")

    @membertrack.command()
    async def testing(self, ctx, state: str):
        """Enable or disable testing mode (allows seconds for wait period)"""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("Only administrators can change testing mode.")
            return

        state = state.lower()
        if state not in ["enable", "disable", "on", "off"]:
            await ctx.send("Please specify either 'enable' or 'disable'.")
            return

        enable = state in ["enable", "on"]
        await self.config.guild(ctx.guild).set_raw("testing_mode", value=enable)
        status = "enabled" if enable else "disabled"
        await ctx.send(f"Testing mode has been {status}.")

    @membertrack.command()
    async def setroles(self, ctx):
        """Set roles to add/remove after the waiting period"""
        async def get_roles(prompt):
            await ctx.send(prompt)
            try:
                response = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=60
                )
                if response.content.lower() == "none":
                    return []
                
                roles = []
                role_ids = [role.strip() for role in response.content.split(",")]
                for role_id in role_ids:
                    try:
                        if role_id.startswith("<@&") and role_id.endswith(">"):
                            role_id = role_id[3:-1]
                        role = ctx.guild.get_role(int(role_id))
                        if role:
                            roles.append(role.id)
                    except ValueError:
                        continue
                return roles
            except asyncio.TimeoutError:
                await ctx.send("Setup timed out. Please try again.")
                return None

        await ctx.send("Please enter the roles to add after the waiting period.\n"
                      "You can use role IDs or mentions, separated by commas.\n"
                      "Type 'none' if you don't want to add any roles.")
        
        add_roles = await get_roles("Which roles should be added?")
        if add_roles is None:
            return

        remove_roles = await get_roles("Which roles should be removed? (Type 'none' for no roles)")
        if remove_roles is None:
            return

        await ctx.send("Would you like to receive notifications when roles are added/removed? (yes/no)")
        try:
            response = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ["yes", "no"],
                timeout=30
            )
            notify_changes = response.content.lower() == "yes"
        except asyncio.TimeoutError:
            await ctx.send("No response received, defaulting to showing notifications.")
            notify_changes = True

        async with self.config.guild(ctx.guild).all() as guild_data:
            guild_data["roles_to_add"] = add_roles
            guild_data["roles_to_remove"] = remove_roles
            guild_data["notify_role_changes"] = notify_changes

        await ctx.send("Role settings have been updated!")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Log when a member joins the server"""
        if member.bot:
            return

        join_date = datetime.utcnow().strftime("%d/%m/%Y")
        async with self.config.guild(member.guild).member_joins() as joins:
            joins[str(member.id)] = join_date

    @membertrack.command()
    async def settings(self, ctx):
        """View current settings"""
        guild_data = await self.config.guild(ctx.guild).all()
        
        channel = ctx.guild.get_channel(guild_data["notification_channel"])
        channel_mention = channel.mention if channel else "Not set"
        
        add_roles = [ctx.guild.get_role(role_id).name for role_id in guild_data["roles_to_add"] if ctx.guild.get_role(role_id)]
        remove_roles = [ctx.guild.get_role(role_id).name for role_id in guild_data["roles_to_remove"] if ctx.guild.get_role(role_id)]
        
        testing_mode = guild_data.get("testing_mode", False)
        wait_period_display = guild_data.get("wait_period_display", "14 days")

        settings_msg = (
            f"**Current Settings**\n"
            f"Notification Channel: {channel_mention}\n"
            f"Wait Period: {wait_period_display}\n"
            f"Testing Mode: {'Enabled' if testing_mode else 'Disabled'}\n"
            f"Roles to Add: {', '.join(add_roles) if add_roles else 'None'}\n"
            f"Roles to Remove: {', '.join(remove_roles) if remove_roles else 'None'}\n"
            f"Role Change Notifications: {'Enabled' if guild_data['notify_role_changes'] else 'Disabled'}"
        )
        await ctx.send(settings_msg)

    @membertrack.command()
    async def checkjoins(self, ctx):
        """Check all tracked member joins"""
        guild_data = await self.config.guild(ctx.guild).all()
        joins = guild_data.get("member_joins", {})
        
        if not joins:
            await ctx.send("No member joins are currently being tracked.")
            return

        current_time = datetime.utcnow()
        wait_period_seconds = guild_data.get("wait_period_seconds", 1209600)
        wait_period_display = guild_data.get("wait_period_display", "14 days")

        message = f"Current tracked members (Wait period: {wait_period_display}):\n"
        for member_id, join_date_str in joins.items():
            member = ctx.guild.get_member(int(member_id))
            if member:
                join_date = datetime.strptime(join_date_str, "%d/%m/%Y")
                seconds_passed = (current_time - join_date).total_seconds()
                seconds_left = wait_period_seconds - seconds_passed
                
                if seconds_left > 86400:
                    time_left = f"{seconds_left // 86400:.1f} days"
                elif seconds_left > 3600:
                    time_left = f"{seconds_left // 3600:.1f} hours"
                else:
                    time_left = f"{seconds_left // 60:.1f} minutes"
                
                message += f"{member.name}: Joined {join_date_str} ({time_left} remaining)\n"

        await ctx.send(message)

async def setup(bot):
    await bot.add_cog(MemberTracker(bot))
