from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import Dict, List, Set
import discord
import datetime
import logging

class AppTrack(commands.Cog):
    """Track Discord Activities and assign roles automatically."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=856712356,
            force_registration=True
        )
        
        # Ensure we have the required intents
        if not bot.intents.presences or not bot.intents.members:
            logging.warning("Bot is missing required intents (presences and/or members)!")
        
        default_guild = {
            "activity_roles": {},  # Maps activity names to role IDs
            "tracked_activities": [],  # List of tracked activity names
            "discovered_activities": {}  # Dictionary of activity names and when they were first seen
        }
        
        self.config.register_guild(**default_guild)

    def is_valid_activity(self, activity) -> bool:
        """Check if the activity is valid for tracking."""
        if activity is None:
            return False
            
        # Log activity details for debugging
        logging.info(f"Checking activity: {activity.name} - Type: {activity.type}")
            
        # Check specifically for game activities
        return isinstance(activity, discord.Game) or (
            isinstance(activity, discord.Activity) and 
            activity.type == discord.ActivityType.playing and
            activity.name is not None and 
            activity.name.strip() != ""
        )

    def get_valid_activities(self, member: discord.Member) -> List[str]:
        """Get all valid activities for a member."""
        valid_activities = []
        
        # Log member activities for debugging
        logging.info(f"Checking activities for {member.name}")
        for activity in member.activities:
            logging.info(f"Found activity: {activity.name if hasattr(activity, 'name') else 'No name'} - Type: {type(activity)}")
            
        for activity in member.activities:
            if self.is_valid_activity(activity):
                valid_activities.append(activity.name)
                logging.info(f"Valid activity found: {activity.name}")
                
        return valid_activities

    async def update_activities(self, guild: discord.Guild) -> tuple[set, int]:
        """Update the activities list for a guild and return new activities."""
        current_activities = set()
        new_count = 0
        
        # Collect activities from all members
        for member in guild.members:
            activities = self.get_valid_activities(member)
            current_activities.update(activities)
            
        logging.info(f"Found activities in update: {current_activities}")
                    
        async with self.config.guild(guild).discovered_activities() as discovered:
            for activity in current_activities:
                if activity not in discovered:
                    discovered[activity] = str(datetime.datetime.now())
                    new_count += 1
                    
        return current_activities, new_count

    @commands.group(aliases=["at"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def apptrack(self, ctx: commands.Context):
        """Manage activity tracking and role assignments."""
        if ctx.invoked_subcommand is None:
            await ctx.send("Please specify a subcommand. Use `!help apptrack` for more information.")

    @apptrack.command(name="debug")
    @commands.is_owner()
    async def debug_info(self, ctx: commands.Context):
        """Show debug information about the bot's configuration."""
        intents_info = (
            f"Presence Intent: {self.bot.intents.presences}\n"
            f"Members Intent: {self.bot.intents.members}\n"
            f"Server Members: {len(ctx.guild.members)}\n"
        )
        
        # Sample some member activities
        sample_activities = []
        for member in list(ctx.guild.members)[:5]:  # Sample first 5 members
            activities = self.get_valid_activities(member)
            if activities:
                sample_activities.append(f"{member.name}: {', '.join(activities)}")
        
        activities_info = "Sample Activities:\n" + "\n".join(sample_activities) if sample_activities else "No activities found in sample"
        
        tracked = await self.config.guild(ctx.guild).tracked_activities()
        roles = await self.config.guild(ctx.guild).activity_roles()
        
        config_info = (
            f"Tracked Activities: {tracked}\n"
            f"Role Mappings: {roles}"
        )
        
        await ctx.send(f"```\nDebug Information:\n\n{intents_info}\n{activities_info}\n\n{config_info}```")

    @apptrack.command(name="update")
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
            
        # Sort activities by discovery date
        sorted_activities = sorted(discovered.items(), key=lambda x: x[1])
        
        # Create embeds (Discord has a 25 field limit per embed)
        embeds = []
        current_embed = discord.Embed(
            title="Discovered Discord Activities",
            description="All activities that have been seen in the server",
            color=discord.Color.blue()
        )
        field_count = 0
        
        for activity_name, first_seen in sorted_activities:
            try:
                first_seen_dt = datetime.datetime.fromisoformat(first_seen)
                first_seen_str = first_seen_dt.strftime("%Y-%m-%d %H:%M:%S")
            except:
                first_seen_str = "Unknown"
                
            if field_count == 25:  # Maximum fields reached
                embeds.append(current_embed)
                current_embed = discord.Embed(
                    title="Discovered Discord Activities (Continued)",
                    color=discord.Color.blue()
                )
                field_count = 0
                
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
            
        # Create embeds (Discord has a 25 field limit per embed)
        embeds = []
        current_embed = discord.Embed(
            title="Current Discord Activities",
            description="Activities currently running in the server",
            color=discord.Color.green()
        )
        field_count = 0
        
        for activity_name, users in current_activities.items():
            if field_count == 25:  # Maximum fields reached
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
            
        logging.info(f"Presence update for {after.name}")
        
        tracked_activities = await self.config.guild(before.guild).tracked_activities()
        activity_roles = await self.config.guild(before.guild).activity_roles()
        
        # Get activities before and after update
        before_activities = set(self.get_valid_activities(before))
        after_activities = set(self.get_valid_activities(after))
        
        logging.info(f"Before activities: {before_activities}")
        logging.info(f"After activities: {after_activities}")
        
        # Handle new activities
        for activity_name in after_activities:
            if activity_name in tracked_activities:
                role_id = activity_roles.get(activity_name)
                if role_id:
                    role = before.guild.get_role(role_id)
                    if role and role not in after.roles:
                        try:
                            await after.add_roles(role, reason=f"Started activity: {activity_name}")
                            logging.info(f"Added role {role.name} to {after.name} for activity {activity_name}")
                        except discord.Forbidden:
                            logging.error(f"Failed to add role {role.name} to {after.name}")
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
                            logging.info(f"Removed role {role.name} from {after.name} for activity {activity_name}")
                        except discord.Forbidden:
                            logging.error(f"Failed to remove role {role.name} from {after.name}")
                            continue

        # Update discovered activities
        async with self.config.guild(before.guild).discovered_activities() as discovered:
            for activity_name in after_activities:
                if activity_name not in discovered:
                    discovered[activity_name] = str(datetime.datetime.now())
