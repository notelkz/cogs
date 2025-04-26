from redbot.core import commands, Config
import discord
from discord import Embed
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

    async def format_time_remaining(self, seconds):
        """Format seconds into a readable time string"""
        if seconds > 86400:
            return f"{seconds // 86400:.1f} days"
        elif seconds > 3600:
            return f"{seconds // 3600:.1f} hours"
        elif seconds > 60:
            return f"{seconds // 60:.1f} minutes"
        else:
            return f"{seconds:.1f} seconds"

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

                                    # Create embed for notification
                                    embed = Embed(
                                        title="Member Duration Milestone",
                                        description=f"🎉 {member.mention} has reached their waiting period!",
                                        color=discord.Color.green()
                                    )
                                    
                                    embed.add_field(
                                        name="Details",
                                        value=f"Join Date: {join_date_str}\n"
                                              f"Duration: {guild_data.get('wait_period_display', '14 days')}",
                                        inline=False
                                    )

                                    if notify_changes:
                                        role_changes = []
                                        if roles_to_add:
                                            role_changes.append(f"Added: {', '.join(role.name for role in roles_to_add if role)}")
                                        if roles_to_remove:
                                            role_changes.append(f"Removed: {', '.join(role.name for role in roles_to_remove if role)}")
                                        if role_changes:
                                            embed.add_field(
                                                name="Role Changes",
                                                value="\n".join(role_changes),
                                                inline=False
                                            )

                                    await channel.send(embed=embed)

                                except discord.Forbidden:
                                    error_embed = Embed(
                                        title="Permission Error",
                                        description=f"Failed to modify roles for {member.mention}",
                                        color=discord.Color.red()
                                    )
                                    await channel.send(embed=error_embed)

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
        if ctx.invoked_subcommand is None:
            embed = Embed(
                title="MemberTracker Help",
                description="Available commands:",
                color=discord.Color.blue()
            )
            commands_list = [
                ("setchannel", "Set the notification channel"),
                ("setperiod", "Set the waiting period"),
                ("testing", "Enable/disable testing mode"),
                ("setroles", "Configure role management"),
                ("settings", "View current settings"),
                ("checkjoins", "View tracked members")
            ]
            for cmd, desc in commands_list:
                embed.add_field(name=f"`[p]membertrack {cmd}`", value=desc, inline=False)
            
            await ctx.send(embed=embed)

    @membertrack.command()
    async def test(self, ctx):
        """Test if the cog is working"""
        embed = Embed(
            title="MemberTracker Test",
            description="✅ The MemberTracker cog is working!",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @membertrack.command()
    async def setchannel(self, ctx, channel: discord.TextChannel):
        """Set the notification channel"""
        await self.config.guild(ctx.guild).notification_channel.set(channel.id)
        
        embed = Embed(
            title="Channel Updated",
            description=f"Notification channel set to {channel.mention}",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Updated by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @membertrack.command()
    async def setperiod(self, ctx, amount: int, unit: str = "days"):
        """Set the waiting period"""
        unit = unit.lower()
        allowed_units = ["days", "day", "hours", "hour", "seconds", "second", "s", "h", "d"]
        
        if unit not in allowed_units:
            embed = Embed(
                title="Error",
                description="Invalid unit. Please use 'days', 'hours', or 'seconds'.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        if amount < 1:
            embed = Embed(
                title="Error",
                description="The amount must be at least 1.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
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
            testing_enabled = await self.config.guild(ctx.guild).get_raw("testing_mode", default=False)
            if not testing_enabled:
                embed = Embed(
                    title="Error",
                    description="Seconds are only available in testing mode.\nUse `[p]membertrack testing enable` first.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return
            seconds = amount
            display_unit = "seconds"
            display_amount = amount
        
        async with self.config.guild(ctx.guild).all() as guild_data:
            guild_data["wait_period_seconds"] = seconds
            guild_data["wait_period_display"] = f"{display_amount} {display_unit}"

        embed = Embed(
            title="Wait Period Updated",
            description=f"Waiting period set to {display_amount} {display_unit}",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Updated by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @membertrack.command()
    async def testing(self, ctx, state: str):
        """Enable or disable testing mode"""
        if not ctx.author.guild_permissions.administrator:
            embed = Embed(
                title="Error",
                description="Only administrators can change testing mode.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        state = state.lower()
        if state not in ["enable", "disable", "on", "off"]:
            embed = Embed(
                title="Error",
                description="Please specify either 'enable' or 'disable'.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        enable = state in ["enable", "on"]
        await self.config.guild(ctx.guild).set_raw("testing_mode", value=enable)
        
        embed = Embed(
            title="Testing Mode Updated",
            description=f"Testing mode has been {'enabled' if enable else 'disabled'}",
            color=discord.Color.blue() if enable else discord.Color.red()
        )
        embed.set_footer(text=f"Updated by {ctx.author.display_name}")
        await ctx.send(embed=embed)

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

        embed = Embed(
            title="MemberTracker Settings",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="General Settings",
            value=f"**Notification Channel:** {channel_mention}\n"
                  f"**Wait Period:** {wait_period_display}\n"
                  f"**Testing Mode:** {'Enabled' if testing_mode else 'Disabled'}",
            inline=False
        )
        
        embed.add_field(
            name="Role Management",
            value=f"**Roles to Add:** {', '.join(add_roles) if add_roles else 'None'}\n"
                  f"**Roles to Remove:** {', '.join(remove_roles) if remove_roles else 'None'}\n"
                  f"**Role Change Notifications:** {'Enabled' if guild_data['notify_role_changes'] else 'Disabled'}",
            inline=False
        )
        
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @membertrack.command()
    async def checkjoins(self, ctx):
        """Check all tracked member joins"""
        guild_data = await self.config.guild(ctx.guild).all()
        joins = guild_data.get("member_joins", {})
        
        if not joins:
            embed = Embed(
                title="Tracked Members",
                description="No member joins are currently being tracked.",
                color=discord.Color.blue()
            )
            await ctx.send(embed=embed)
            return

        current_time = datetime.utcnow()
        wait_period_seconds = guild_data.get("wait_period_seconds", 1209600)
        wait_period_display = guild_data.get("wait_period_display", "14 days")

        embed = Embed(
            title="Tracked Members",
            description=f"Wait period: {wait_period_display}",
            color=discord.Color.blue()
        )

        # Sort members by time remaining
        sorted_members = []
        for member_id, join_date_str in joins.items():
            member = ctx.guild.get_member(int(member_id))
            if member:
                join_date = datetime.strptime(join_date_str, "%d/%m/%Y")
                seconds_passed = (current_time - join_date).total_seconds()
                seconds_left = wait_period_seconds - seconds_passed
                sorted_members.append((member, join_date_str, seconds_left))
        
        sorted_members.sort(key=lambda x: x[2])  # Sort by time remaining

        for member, join_date_str, seconds_left in sorted_members:
            time_left = await self.format_time_remaining(seconds_left)
            embed.add_field(
                name=member.display_name,
                value=f"Joined: {join_date_str}\nTime remaining: {time_left}",
                inline=True
            )

        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @membertrack.command()
    async def setroles(self, ctx):
        """Set roles to add/remove after the waiting period"""
        embed = Embed(
            title="Role Setup",
            description="Please enter the roles to add after the waiting period.\n"
                       "You can use role IDs or mentions, separated by commas.\n"
                       "Type 'none' if you don't want to add any roles.",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

        async def get_roles(prompt):
            prompt_embed = Embed(
                title="Role Selection",
                description=prompt,
                color=discord.Color.blue()
            )
            await ctx.send(embed=prompt_embed)
            
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
                timeout_embed = Embed(
                    title="Timeout",
                    description="Setup timed out. Please try again.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=timeout_embed)
                return None

        add_roles = await get_roles("Which roles should be added?")
        if add_roles is None:
            return

        remove_roles = await get_roles("Which roles should be removed? (Type 'none' for no roles)")
        if remove_roles is None:
            return

        notify_embed = Embed(
            title="Notification Settings",
            description="Would you like to receive notifications when roles are added/removed? (yes/no)",
            color=discord.Color.blue()
        )
        await ctx.send(embed=notify_embed)

        try:
            response = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ["yes", "no"],
                timeout=30
            )
            notify_changes = response.content.lower() == "yes"
        except asyncio.TimeoutError:
            notify_changes = True
            timeout_embed = Embed(
                title="Timeout",
                description="No response received, defaulting to showing notifications.",
                color=discord.Color.blue()
            )
            await ctx.send(embed=timeout_embed)

        async with self.config.guild(ctx.guild).all() as guild_data:
            guild_data["roles_to_add"] = add_roles
            guild_data["roles_to_remove"] = remove_roles
            guild_data["notify_role_changes"] = notify_changes

        success_embed = Embed(
            title="Setup Complete",
            description="Role settings have been updated!",
            color=discord.Color.green()
        )
        success_embed.set_footer(text=f"Updated by {ctx.author.display_name}")
        await ctx.send(embed=success_embed)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Log when a member joins the server"""
        if member.bot:
            return

        join_date = datetime.utcnow().strftime("%d/%m/%Y")
        async with self.config.guild(member.guild).member_joins() as joins:
            joins[str(member.id)] = join_date

        # Send join notification
        channel_id = await self.config.guild(member.guild).notification_channel()
        if channel_id:
            channel = member.guild.get_channel(channel_id)
            if channel:
                embed = Embed(
                    title="New Member Joined",
                    description=f"Started tracking {member.mention}",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="Join Date",
                    value=join_date,
                    inline=True
                )
                await channel.send(embed=embed)

async def setup(bot):
    await bot.add_cog(MemberTracker(bot))
