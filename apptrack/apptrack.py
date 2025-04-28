from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import Dict, List, Set
import discord
import asyncio
import datetime

class AppTrack(commands.Cog):
    """Track Discord Activities and assign roles automatically."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=856712356,
            force_registration=True
        )
        
        default_guild = {
            "activity_roles": {},  # Maps activity names to role IDs
            "tracked_activities": [],  # List of tracked activity names
            "discovered_activities": {}  # Dictionary of activity names and when they were first seen
        }
        
        self.config.register_guild(**default_guild)
        self.activity_check_task = self.bot.loop.create_task(self.periodic_activity_check())

    def cog_unload(self):
        """Cleanup when cog is unloaded."""
        if self.activity_check_task:
            self.activity_check_task.cancel()

    async def periodic_activity_check(self):
        """Check for new activities every 60 seconds."""
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild in self.bot.guilds:
                    current_activities = set()
                    for member in guild.members:
                        for activity in member.activities:
                            if activity.name:  # Ensure activity has a name
                                current_activities.add(activity.name)
                                
                    async with self.config.guild(guild).discovered_activities() as discovered:
                        for activity in current_activities:
                            if activity not in discovered:
                                discovered[activity] = str(datetime.datetime.now())
                
                await asyncio.sleep(60)  # Wait 60 seconds before next check
            except Exception as e:
                print(f"Error in activity check: {e}")
                await asyncio.sleep(60)

    @commands.group(aliases=["at"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def apptrack(self, ctx: commands.Context):
        """Manage activity tracking and role assignments."""
        if ctx.invoked_subcommand is None:
            await ctx.send("Please specify a subcommand. Use `!help apptrack` for more information.")

    @apptrack.command(name="discover")
    async def discover_activities(self, ctx: commands.Context):
        """List all discovered Discord Activities in the server."""
        discovered = await self.config.guild(ctx.guild).discovered_activities()
        
        if not discovered:
            await ctx.send("No activities have been discovered yet.")
            return
            
        # Sort activities by discovery date
        sorted_activities = sorted(discovered.items(), key=lambda x: x[1])
        
        embed = discord.Embed(
            title="Discovered Discord Activities",
            description="All activities that have been seen in the server",
            color=discord.Color.blue()
        )
        
        for activity_name, first_seen in sorted_activities:
            try:
                first_seen_dt = datetime.datetime.fromisoformat(first_seen)
                first_seen_str = first_seen_dt.strftime("%Y-%m-%d %H:%M:%S")
            except:
                first_seen_str = "Unknown"
                
            embed.add_field(
                name=activity_name,
                value=f"First seen: {first_seen_str}",
                inline=False
            )
            
        await ctx.send(embed=embed)

    @apptrack.command(name="current")
    async def current_activities(self, ctx: commands.Context):
        """List all currently active Discord Activities in the server."""
        current_activities = {}
        
        for member in ctx.guild.members:
            for activity in member.activities:
                if activity.name:
                    if activity.name not in current_activities:
                        current_activities[activity.name] = []
                    current_activities[activity.name].append(member.name)
        
        if not current_activities:
            await ctx.send("No activities are currently running in the server.")
            return
            
        embed = discord.Embed(
            title="Current Discord Activities",
            description="Activities currently running in the server",
            color=discord.Color.green()
        )
        
        for activity_name, users in current_activities.items():
            embed.add_field(
                name=f"{activity_name} ({len(users)} users)",
                value=", ".join(users[:5]) + ("..." if len(users) > 5 else ""),
                inline=False
            )
            
        await ctx.send(embed=embed)

    @apptrack.command(name="add")
    async def add_activity(self, ctx: commands.Context, *, activity_name: str):
        """Add an activity to the tracking list."""
        async with self.config.guild(ctx.guild).tracked_activities() as activities:
            if activity_name.lower() in [a.lower() for a in activities]:
                await ctx.send(f"Activity '{activity_name}' is already being tracked.")
                return
            activities.append(activity_name)
        await ctx.send(f"Now tracking activity: {activity_name}")

    @apptrack.command(name="remove")
    async def remove_activity(self, ctx: commands.Context, *, activity_name: str):
        """Remove an activity from the tracking list."""
        async with self.config.guild(ctx.guild).tracked_activities() as activities:
            if activity_name not in activities:
                await ctx.send(f"Activity '{activity_name}' is not being tracked.")
                return
            activities.remove(activity_name)
        await ctx.send(f"Stopped tracking activity: {activity_name}")

    @apptrack.command(name="list")
    async def list_activities(self, ctx: commands.Context):
        """List all tracked activities and their assigned roles."""
        activities = await self.config.guild(ctx.guild).tracked_activities()
        activity_roles = await self.config.guild(ctx.guild).activity_roles()
        
        if not activities:
            await ctx.send("No activities are currently being tracked.")
            return
            
        embed = discord.Embed(title="Tracked Activities", color=discord.Color.blue())
        for activity in activities:
            role_id = activity_roles.get(activity)
            role = ctx.guild.get_role(role_id) if role_id else None
            role_name = role.name if role else "No role assigned"
            embed.add_field(name=activity, value=role_name, inline=False)
            
        await ctx.send(embed=embed)

    @apptrack.command(name="link")
    async def link_role(self, ctx: commands.Context, role: discord.Role, *, activity_name: str):
        """Link an activity to a role."""
        async with self.config.guild(ctx.guild).tracked_activities() as activities:
            if activity_name not in activities:
                await ctx.send(f"Activity '{activity_name}' is not being tracked. Add it first.")
                return
                
        async with self.config.guild(ctx.guild).activity_roles() as activity_roles:
            activity_roles[activity_name] = role.id
            
        await ctx.send(f"Linked activity '{activity_name}' to role '{role.name}'")

    @apptrack.command(name="unlink")
    async def unlink_role(self, ctx: commands.Context, *, activity_name: str):
        """Unlink a role from an activity."""
        async with self.config.guild(ctx.guild).activity_roles() as activity_roles:
            if activity_name not in activity_roles:
                await ctx.send(f"Activity '{activity_name}' has no linked role.")
                return
            del activity_roles[activity_name]
            
        await ctx.send(f"Unlinked role from activity '{activity_name}'")

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        """Handle activity changes and role assignments."""
        if before.guild is None:
            return
            
        tracked_activities = await self.config.guild(before.guild).tracked_activities()
        activity_roles = await self.config.guild(before.guild).activity_roles()
        
        # Check for new activities
        for activity in after.activities:
            if activity.name in tracked_activities:
                role_id = activity_roles.get(activity.name)
                if role_id:
                    role = before.guild.get_role(role_id)
                    if role and role not in after.roles:
                        try:
                            await after.add_roles(role, reason="Activity detected")
                        except discord.Forbidden:
                            continue

        # Remove roles for stopped activities
        for activity in before.activities:
            if (activity.name in tracked_activities and 
                activity.name not in [a.name for a in after.activities]):
                role_id = activity_roles.get(activity.name)
                if role_id:
                    role = before.guild.get_role(role_id)
                    if role and role in after.roles:
                        try:
                            await after.remove_roles(role, reason="Activity ended")
                        except discord.Forbidden:
                            continue
