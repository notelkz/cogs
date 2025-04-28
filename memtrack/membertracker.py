from redbot.core import commands, Config
import discord
from discord import Embed
from datetime import datetime, timedelta
import asyncio
from typing import Optional, List

class MemTrack(commands.Cog):
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
            "tracked_roles": [],
            "track_all_users": True,
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

    async def should_track_member(self, member: discord.Member) -> bool:
        """Check if a member should be tracked based on their roles"""
        if member.bot:
            return False

        guild_data = await self.config.guild(member.guild).all()
        
        # If tracking all users, return True
        if guild_data.get("track_all_users", True):
            return True
            
        # Check if member has any of the tracked roles
        tracked_roles = guild_data.get("tracked_roles", [])
        return any(role.id in tracked_roles for role in member.roles)

    async def check_member_duration(self):
        """Background task to check member durations and update roles"""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                current_time = datetime.utcnow()
                all_guilds = self.bot.guilds
                
                for guild in all_guilds:
                    guild_data = await self.config.guild(guild).all()
                    wait_period = guild_data["wait_period_seconds"]
                    joins = guild_data["member_joins"]
                    
                    for member_id, join_date_str in list(joins.items()):
                        member = guild.get_member(int(member_id))
                        if not member:
                            continue
                            
                        join_date = datetime.strptime(join_date_str, "%d/%m/%Y")
                        if (current_time - join_date).total_seconds() >= wait_period:
                            # Process role changes
                            roles_to_add = [guild.get_role(role_id) for role_id in guild_data["roles_to_add"]]
                            roles_to_remove = [guild.get_role(role_id) for role_id in guild_data["roles_to_remove"]]
                            
                            try:
                                await member.add_roles(*[r for r in roles_to_add if r])
                                await member.remove_roles(*[r for r in roles_to_remove if r])
                                
                                # Remove from tracking
                                async with self.config.guild(guild).member_joins() as joins:
                                    joins.pop(str(member_id), None)
                                    
                                # Send notification
                                if guild_data["notify_role_changes"]:
                                    channel = guild.get_channel(guild_data["notification_channel"])
                                    if channel:
                                        embed = Embed(
                                            title="Member Roles Updated",
                                            description=f"Updated roles for {member.mention} after waiting period",
                                            color=discord.Color.green()
                                        )
                                        await channel.send(embed=embed)
                            except discord.Forbidden:
                                continue
                            
            except Exception as e:
                print(f"Error in check_member_duration: {e}")
                
            await asyncio.sleep(300)  # Check every 5 minutes

    @commands.group()
    @commands.admin_or_permissions(administrator=True)
    async def membertrack(self, ctx):
        """Member tracking commands"""
        if ctx.invoked_subcommand is None:
            embed = Embed(
                title="MemTrack Help",
                description="Available commands:",
                color=discord.Color.blue()
            )
            commands_list = [
                ("setchannel", "Set the notification channel"),
                ("setperiod", "Set the waiting period"),
                ("testing", "Enable/disable testing mode"),
                ("setroles", "Configure role management"),
                ("trackroles", "Configure which roles to track"),
                ("trackmode", "Set whether to track all users or only specific roles"),
                ("settings", "View current settings"),
                ("checkjoins", "View tracked members")
            ]
            for cmd, desc in commands_list:
                embed.add_field(name=f"`[p]membertrack {cmd}`", value=desc, inline=False)
            
            await ctx.send(embed=embed)

    @membertrack.command()
    async def trackmode(self, ctx, mode: str):
        """Set whether to track all users or only those with specific roles
        
        Mode can be:
        - all: Track all users
        - roles: Track only users with specific roles"""
        
        mode = mode.lower()
        if mode not in ["all", "roles"]:
            embed = Embed(
                title="Error",
                description="Mode must be either 'all' or 'roles'",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        track_all = mode == "all"
        await self.config.guild(ctx.guild).track_all_users.set(track_all)
        
        embed = Embed(
            title="Tracking Mode Updated",
            description=f"Now tracking {'all users' if track_all else 'only users with specified roles'}",
            color=discord.Color.blue()
        )
        embed.set_footer(text=f"Updated by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @membertrack.command()
    async def trackroles(self, ctx, operation: str = None, role: discord.Role = None):
        """Manage roles to track
        
        Operations:
        - add: Add a role to track
        - remove: Remove a role from tracking
        - list: List currently tracked roles
        
        Example:
        [p]membertrack trackroles add @Role
        [p]membertrack trackroles remove @Role
        [p]membertrack trackroles list"""
        
        if not operation:
            operation = "list"

        operation = operation.lower()
        
        if operation not in ["add", "remove", "list"]:
            embed = Embed(
                title="Error",
                description="Operation must be 'add', 'remove', or 'list'",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        if operation in ["add", "remove"] and not role:
            embed = Embed(
                title="Error",
                description=f"Please specify a role to {operation}",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return

        async with self.config.guild(ctx.guild).tracked_roles() as tracked_roles:
            if operation == "add":
                if role.id not in tracked_roles:
                    tracked_roles.append(role.id)
                    embed = Embed(
                        title="Role Added",
                        description=f"Now tracking role: {role.name}",
                        color=discord.Color.green()
                    )
                else:
                    embed = Embed(
                        title="Note",
                        description=f"Already tracking role: {role.name}",
                        color=discord.Color.blue()
                    )
                
            elif operation == "remove":
                if role.id in tracked_roles:
                    tracked_roles.remove(role.id)
                    embed = Embed(
                        title="Role Removed",
                        description=f"Stopped tracking role: {role.name}",
                        color=discord.Color.blue()
                    )
                else:
                    embed = Embed(
                        title="Note",
                        description=f"Wasn't tracking role: {role.name}",
                        color=discord.Color.blue()
                    )
            
            else:  # list
                embed = Embed(
                    title="Tracked Roles",
                    color=discord.Color.blue()
                )
                
                if tracked_roles:
                    role_names = []
                    for role_id in tracked_roles:
                        role = ctx.guild.get_role(role_id)
                        if role:
                            role_names.append(role.name)
                    
                    embed.description = "\n".join(f"• {name}" for name in role_names)
                else:
                    embed.description = "No roles are currently being tracked"

        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    @membertrack.command()
    async def settings(self, ctx):
        """View current settings"""
        guild_data = await self.config.guild(ctx.guild).all()
        
        channel = ctx.guild.get_channel(guild_data["notification_channel"])
        channel_mention = channel.mention if channel else "Not set"
        
        add_roles = [ctx.guild.get_role(role_id).name for role_id in guild_data["roles_to_add"] if ctx.guild.get_role(role_id)]
        remove_roles = [ctx.guild.get_role(role_id).name for role_id in guild_data["roles_to_remove"] if ctx.guild.get_role(role_id)]
        tracked_roles = [ctx.guild.get_role(role_id).name for role_id in guild_data["tracked_roles"] if ctx.guild.get_role(role_id)]
        
        testing_mode = guild_data.get("testing_mode", False)
        wait_period_display = guild_data.get("wait_period_display", "14 days")
        track_all_users = guild_data.get("track_all_users", True)

        embed = Embed(
            title="MemTrack Settings",
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
            name="Tracking Settings",
            value=f"**Track All Users:** {'Yes' if track_all_users else 'No'}\n"
                  f"**Tracked Roles:** {', '.join(tracked_roles) if tracked_roles else 'None'}",
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
            if member and await self.should_track_member(member):
                join_date = datetime.strptime(join_date_str, "%d/%m/%Y")
                seconds_passed = (current_time - join_date).total_seconds()
                seconds_left = wait_period_seconds - seconds_passed
                sorted_members.append((member, join_date_str, seconds_left))
        
        sorted_members.sort(key=lambda x: x[2])  # Sort by time remaining

        if not sorted_members:
         
