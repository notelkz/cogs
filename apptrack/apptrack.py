from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import Dict, List, Set
import discord
import datetime
import asyncio
import logging
from discord.ui import Select, View

class ActivitySelect(Select):
    def __init__(self, activities: List[str]):
        options = [
            discord.SelectOption(label=activity[:100], value=activity) 
            for activity in activities
        ]
        super().__init__(
            placeholder="Choose an activity...",
            min_values=1,
            max_values=1,
            options=options[:25]  # Discord limits to 25 options
        )

class ActivitySelectView(View):
    def __init__(self, activities: List[str], timeout: float = 60):
        super().__init__(timeout=timeout)
        self.selected_activity = None
        self.add_item(ActivitySelect(activities))
        
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        select = self.children[0]
        self.selected_activity = select.values[0]
        await interaction.response.defer()
        self.stop()
        return True

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
        """Check for new activities every 30 minutes."""
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild in self.bot.guilds:
                    await self.update_activities(guild)
                await asyncio.sleep(1800)  # 30 minutes
            except Exception as e:
                logging.error(f"Error in activity check: {e}")
                await asyncio.sleep(1800)

    def is_valid_activity(self, activity) -> bool:
        """Check if the activity is valid for tracking."""
        if activity is None:
            return False
            
        return isinstance(activity, discord.Game) or (
            isinstance(activity, discord.Activity) and 
            activity.type == discord.ActivityType.playing and
            activity.name is not None and 
            activity.name.strip() != ""
        )

    def get_valid_activities(self, member: discord.Member) -> List[str]:
        """Get all valid activities for a member."""
        valid_activities = []
        for activity in member.activities:
            if self.is_valid_activity(activity):
                valid_activities.append(activity.name)
        return valid_activities

    async def update_activities(self, guild: discord.Guild) -> tuple[set, int]:
        """Update the activities list for a guild and return new activities."""
        current_activities = set()
        new_count = 0
        
        for member in guild.members:
            activities = self.get_valid_activities(member)
            current_activities.update(activities)
                    
        async with self.config.guild(guild).discovered_activities() as discovered:
            for activity in current_activities:
                if activity not in discovered:
                    discovered[activity] = str(datetime.datetime.now())
                    new_count += 1
                    
        return current_activities, new_count

    @commands.group(aliases=["at"])
    @commands.guild_only()
    @commands.mod_or_permissions(manage_roles=True)
    async def apptrack(self, ctx: commands.Context):
        """Manage activity tracking and role assignments."""
        if ctx.invoked_subcommand is None:
            await ctx.send("Please specify a subcommand. Use `!help apptrack` for more information.")

    @apptrack.command(name="update")
    @commands.mod_or_permissions(manage_roles=True)
    async def update_activity_list(self, ctx: commands.Context):
        """Manually update the activity list."""
        async with ctx.typing():
            current_activities, new_count = await self.update_activities(ctx.guild)
            
            if new_count > 0:
                await ctx.send(f"Update complete! Found {new_count} new activities. Use `!at discover` to see all activities.")
            else:
                await ctx.send("Update complete! No new activities found.")

    @apptrack.command(name="discover")
    async def discover_activities(self, ctx: commands.Context):
        """List all discovered Discord Activities in the server."""
        discovered = await self.config.guild(ctx.guild).discovered_activities()
        
        if not discovered:
            await ctx.send("No activities have been discovered yet. Use `!at update` to scan for activities.")
            return
            
        sorted_activities = sorted(discovered.items(), key=lambda x: x[1])
        
        embeds = []
        current_embed = discord.Embed(
            title="Discovered Discord Activities",
            description="All activities that have been seen in the server",
            color=discord.Color.blue()
        )
        field_count = 0
        
        for activity_name, first_seen in sorted_activities:
            if field_count == 25:
                embeds.append(current_embed)
                current_embed = discord.Embed(
                    title="Discovered Discord Activities (Continued)",
                    color=discord.Color.blue()
                )
                field_count = 0
                
            try:
                first_seen_dt = datetime.datetime.fromisoformat(first_seen)
                first_seen_str = first_seen_dt.strftime("%Y-%m-%d %H:%M:%S")
            except:
                first_seen_str = "Unknown"
                
            current_embed.add_field(
                name=activity_name,
                value=f"First seen: {first_seen_str}",
                inline=False
            )
            field_count += 1
            
        if field_count > 0:
            embeds.append(current_embed)
            
        for embed in embeds:
            await ctx.send(embed=embed)

    @apptrack.command(name="link")
    @commands.mod_or_permissions(manage_roles=True)
    async def link_role(self, ctx: commands.Context):
        """Link an activity to a role using a dropdown menu."""
        # Get discovered activities
        discovered = await self.config.guild(ctx.guild).discovered_activities()
        if not discovered:
            await ctx.send("No activities have been discovered yet. Use `!at update` to scan for activities.")
            return

        # Create and send activity selection menu
        view = ActivitySelectView(list(discovered.keys()))
        await ctx.send("Select an activity to link:", view=view)
        
        # Wait for selection
        await view.wait()
        if view.selected_activity is None:
            await ctx.send("No activity selected. Command cancelled.")
            return

        # Ask for role
        await ctx.send(f"Selected activity: {view.selected_activity}\nPlease mention the role or provide the role ID to link.")
        
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            msg = await self.bot.wait_for('message', check=check, timeout=60.0)
            
            # Try to get role from mention or ID
            role = None
            try:
                if msg.role_mentions:
                    role = msg.role_mentions[0]
                else:
                    role_id = int(msg.content)
                    role = ctx.guild.get_role(role_id)
            except ValueError:
                await ctx.send("Invalid role ID provided. Please use a role mention or valid role ID.")
                return

            if role is None:
                await ctx.send("Could not find the specified role.")
                return

            # Add activity to tracked list if not already there
            async with self.config.guild(ctx.guild).tracked_activities() as activities:
                if view.selected_activity not in activities:
                    activities.append(view.selected_activity)

            # Link role to activity
            async with self.config.guild(ctx.guild).activity_roles() as activity_roles:
                activity_roles[view.selected_activity] = role.id
                
            await ctx.send(f"Successfully linked activity '{view.selected_activity}' to role '{role.name}'")

        except asyncio.TimeoutError:
            await ctx.send("Command timed out. Please try again.")

    @apptrack.command(name="current")
    async def current_activities(self, ctx: commands.Context):
        """List all currently active Discord Activities in the server."""
        current_activities = {}
        
        for member in ctx.guild.members:
            activities = self.get_valid_activities(member)
            for activity_name in activities:
                if activity_name not in current_activities:
                    current_activities[activity_name] = []
                current_activities[activity_name].append(member.name)
        
        if not current_activities:
            await ctx.send("No activities are currently running in the server.")
            return
            
        embeds = []
        current_embed = discord.Embed(
            title="Current Discord Activities",
            description="Activities currently running in the server",
            color=discord.Color.green()
        )
        field_count = 0
        
        for activity_name, users in current_activities.items():
            if field_count == 25:
                embeds.append(current_embed)
                current_embed = discord.Embed(
                    title="Current Discord Activities (Continued)",
                    color=discord.Color.green()
                )
                field_count = 0
                
            current_embed.add_field(
                name=f"{activity_name} ({len(users)} users)",
                value=", ".join(users[:5]) + ("..." if len(users) > 5 else ""),
                inline=False
            )
            field_count += 1
            
        if field_count > 0:
            embeds.append(current_embed)
            
        for embed in embeds:
            await ctx.send(embed=embed)

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

    @apptrack.command(name="unlink")
    @commands.mod_or_permissions(manage_roles=True)
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
        
        before_activities = set(self.get_valid_activities(before))
        after_activities = set(self.get_valid_activities(after))
        
        # Handle new activities
        for activity_name in after_activities:
            if activity_name in tracked_activities:
                role_id = activity_roles.get(activity_name)
                if role_id:
                    role = before.guild.get_role(role_id)
                    if role and role not in after.roles:
                        try:
                            await after.add_roles(role, reason=f"Started activity: {activity_name}")
                        except discord.Forbidden:
                            continue

        # Handle stopped activities
        for activity_name in before_activities:
            if activity_name in tracked_activities and activity_name not in after_activities:
                role_id = activity_roles.get(activity_name)
                if role_id:
                    role = before.guild.get_role(role_id)
                    if role and role in after.roles:
                        try:
                            await after.remove_roles(role, reason=f"Stopped activity: {activity_name}")
                        except discord.Forbidden:
                            continue

        # Update discovered activities
        async with self.config.guild(before.guild).discovered_activities() as discovered:
            for activity_name in after_activities:
                if activity_name not in discovered:
                    discovered[activity_name] = str(datetime.datetime.now())
