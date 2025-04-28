from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import Dict, List
import discord

class AppTrack(commands.Cog):
    """Track Discord Activities and assign roles automatically."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=856712356,  # Random unique identifier
            force_registration=True
        )
        
        default_guild = {
            "activity_roles": {},  # Maps activity names to role IDs
            "tracked_activities": []  # List of tracked activity names
        }
        
        self.config.register_guild(**default_guild)

    @commands.group(aliases=["at"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True)
    async def apptrack(self, ctx: commands.Context):
        """Manage activity tracking and role assignments."""
        if ctx.invoked_subcommand is None:
            await ctx.send("Please specify a subcommand. Use `!help apptrack` for more information.")

    @apptrack.command(name="add")
    async def add_activity(self, ctx: commands.Context, activity_name: str):
        """Add an activity to the tracking list."""
        async with self.config.guild(ctx.guild).tracked_activities() as activities:
            if activity_name.lower() in [a.lower() for a in activities]:
                await ctx.send(f"Activity '{activity_name}' is already being tracked.")
                return
            activities.append(activity_name)
        await ctx.send(f"Now tracking activity: {activity_name}")

    @apptrack.command(name="remove")
    async def remove_activity(self, ctx: commands.Context, activity_name: str):
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
    async def link_role(self, ctx: commands.Context, activity_name: str, role: discord.Role):
        """Link an activity to a role."""
        async with self.config.guild(ctx.guild).tracked_activities() as activities:
            if activity_name not in activities:
                await ctx.send(f"Activity '{activity_name}' is not being tracked. Add it first.")
                return
                
        async with self.config.guild(ctx.guild).activity_roles() as activity_roles:
            activity_roles[activity_name] = role.id
            
        await ctx.send(f"Linked activity '{activity_name}' to role '{role.name}'")

    @apptrack.command(name="unlink")
    async def unlink_role(self, ctx: commands.Context, activity_name: str):
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
