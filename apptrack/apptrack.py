from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import List
import discord
import datetime
import asyncio
import logging
from discord.ui import Select, View

class ActivitySelect(Select):
    def __init__(self, activities: List[str]):
        unique_activities = {}
        for idx, activity in enumerate(activities):
            if activity not in unique_activities:
                unique_activities[activity] = idx

        options = []
        for activity, idx in unique_activities.items():
            value = f"{idx}:{activity[:90]}"
            option = discord.SelectOption(
                label=activity[:95] + "..." if len(activity) > 98 else activity,
                value=value,
                description=f"Full name: {activity[:50]}..." if len(activity) > 50 else None
            )
            options.append(option)
        options = options[:25]
        super().__init__(
            placeholder="Choose an activity...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        assert self.view is not None
        view: ActivitySelectView = self.view
        view.selected_activity = view.original_activities.get(self.values[0])
        await interaction.response.defer()
        self.view.stop()

class ActivitySelectView(View):
    def __init__(self, activities: List[str], timeout: float = 60):
        super().__init__(timeout=timeout)
        self.selected_activity = None
        self.original_activities = {f"{idx}:{activity[:90]}": activity for idx, activity in enumerate(activities)}
        self.add_item(ActivitySelect(activities))

class AppTrack(commands.Cog):
    """Track Discord Activities and assign roles automatically.
    
    Users can be required to have a specific role to receive automatic role assignments.
    Use !at setrequired to set up this requirement.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=856712356,
            force_registration=True
        )
        default_guild = {
            "activity_roles": {},
            "tracked_activities": [],
            "discovered_activities": {},
            "last_reset": None,
            "required_role": None
        }
        self.config.register_guild(**default_guild)
        self.activity_check_task = self.bot.loop.create_task(self.periodic_activity_check())
        self.reset_check_task = self.bot.loop.create_task(self.check_daily_reset())

    def cog_unload(self):
        if self.activity_check_task:
            self.activity_check_task.cancel()
        if self.reset_check_task:
            self.reset_check_task.cancel()

    async def check_daily_reset(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild in self.bot.guilds:
                    last_reset = await self.config.guild(guild).last_reset()
                    now = datetime.datetime.now()
                    if last_reset is None:
                        await self.reset_activities(guild)
                    else:
                        last_reset_dt = datetime.datetime.fromisoformat(last_reset)
                        if (now - last_reset_dt).days >= 1:
                            await self.reset_activities(guild)
                await asyncio.sleep(3600)
            except Exception as e:
                logging.error(f"Error in daily reset check: {e}")
                await asyncio.sleep(3600)

    async def reset_activities(self, guild: discord.Guild):
        async with self.config.guild(guild).discovered_activities() as discovered:
            tracked = await self.config.guild(guild).tracked_activities()
            tracked_dict = {activity: discovered.get(activity) for activity in tracked if activity in discovered}
            discovered.clear()
            discovered.update(tracked_dict)
        await self.config.guild(guild).last_reset.set(datetime.datetime.now().isoformat())
        logging.info(f"Reset activities for guild {guild.name}")

    async def periodic_activity_check(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild in self.bot.guilds:
                    await self.update_activities(guild)
                await asyncio.sleep(1800)
            except Exception as e:
                logging.error(f"Error in activity check: {e}")
                await asyncio.sleep(1800)

    def is_valid_activity(self, activity) -> bool:
        if activity is None:
            return False
        return isinstance(activity, discord.Game) or (
            isinstance(activity, discord.Activity) and 
            activity.type == discord.ActivityType.playing and
            activity.name is not None and 
            activity.name.strip() != ""
        )

    def get_valid_activities(self, member: discord.Member) -> List[str]:
        valid_activities = []
        for activity in member.activities:
            if self.is_valid_activity(activity):
                valid_activities.append(activity.name)
        return valid_activities

    async def update_activities(self, guild: discord.Guild):
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

    @apptrack.command(name="setrequired")
    @commands.mod_or_permissions(manage_roles=True)
    async def set_required_role(self, ctx: commands.Context, role: discord.Role = None):
        """Set or clear the role required for automatic role assignment.
        Leave role empty to clear the requirement."""
        if role is None:
            await self.config.guild(ctx.guild).required_role.set(None)
            await ctx.send("Cleared the required role. Automatic role assignment will work for all users.")
            return
        await self.config.guild(ctx.guild).required_role.set(role.id)
        await ctx.send(f"Set {role.name} as the required role for automatic role assignment.")

    @apptrack.command(name="required")
    async def show_required_role(self, ctx: commands.Context):
        """Show the current required role for automatic role assignment."""
        role_id = await self.config.guild(ctx.guild).required_role()
        if role_id is None:
            await ctx.send("No role is required for automatic role assignment.")
            return
        role = ctx.guild.get_role(role_id)
        if role is None:
            await ctx.send("The previously set required role no longer exists.")
            return
        await ctx.send(f"Users must have the role '{role.name}' to receive automatic role assignments.")

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

    @apptrack.command(name="reset")
    @commands.mod_or_permissions(manage_roles=True)
    async def reset_activity_list(self, ctx: commands.Context):
        """Manually reset the activity list."""
        async with ctx.typing():
            await self.reset_activities(ctx.guild)
            await ctx.send("Activity list has been reset. Only tracked activities are preserved.")

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
        discovered = await self.config.guild(ctx.guild).discovered_activities()
        if not discovered:
            await ctx.send("No activities have been discovered yet. Use `!at update` to scan for activities.")
            return
        activities = sorted(discovered.keys())
        if len(activities) > 25:
            await ctx.send("There are more than 25 activities. Showing the first 25 alphabetically:")
            activities = activities[:25]
        view = ActivitySelectView(activities)
        await ctx.send("Select an activity to link:", view=view)
        await view.wait()
        if not view.selected_activity:
            await ctx.send("No activity selected or selection timed out. Command cancelled.")
            return
        await ctx.send(f"Selected activity: {view.selected_activity}\nPlease mention the role or provide the role ID to link.")
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel
        try:
            msg = await self.bot.wait_for('message', check=check, timeout=60.0)
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
            async with self.config.guild(ctx.guild).tracked_activities() as activities:
                if view.selected_activity not in activities:
                    activities.append(view.selected_activity)
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
        required_role_id = await self.config.guild(ctx.guild).required_role()
        if not activities:
            await ctx.send("No activities are currently being tracked.")
            return
        embed = discord.Embed(title="Tracked Activities", color=discord.Color.blue())
        if required_role_id:
            required_role = ctx.guild.get_role(required_role_id)
            if required_role:
                embed.description = f"Required role for automatic assignment: {required_role.name}"
            else:
                embed.description = "Required role is set but no longer exists"
        else:
            embed.description = "No role required for automatic assignment"
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

    @apptrack.command(name="removerole")
    @commands.mod_or_permissions(manage_roles=True)
    async def remove_activity_role(self, ctx: commands.Context, member: discord.Member, *, activity_name: str):
        """Manually remove an activity role from a member. Admin/Mod only."""
        activity_roles = await self.config.guild(ctx.guild).activity_roles()
        if activity_name not in activity_roles:
            await ctx.send(f"No role is linked to activity '{activity_name}'.")
            return
        role_id = activity_roles[activity_name]
        role = ctx.guild.get_role(role_id)
        if not role:
            await ctx.send(f"Could not find the role linked to activity '{activity_name}'.")
            return
        if role not in member.roles:
            await ctx.send(f"{member.name} doesn't have the role for '{activity_name}'.")
            return
        try:
            await member.remove_roles(role, reason=f"Manual removal of activity role: {activity_name}")
            await ctx.send(f"Removed role '{role.name}' from {member.name}")
        except discord.Forbidden:
            await ctx.send("I don't have permission to remove that role.")

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        """Handle activity changes and role assignments. Only adds roles, never removes them."""
        if before.guild is None:
            return

        # Check for required role
        required_role_id = await self.config.guild(before.guild).required_role()
        if required_role_id is not None:
            required_role = before.guild.get_role(required_role_id)
            if required_role is None or required_role not in after.roles:
                return  # Skip if user doesn't have the required role

        tracked_activities = await self.config.guild(before.guild).tracked_activities()
        activity_roles = await self.config.guild(before.guild).activity_roles()

        after_activities = set(self.get_valid_activities(after))

        # Only add roles, never remove
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

        # Update discovered activities
        async with self.config.guild(before.guild).discovered_activities() as discovered:
            for activity_name in after_activities:
                if activity_name not in discovered:
                    discovered[activity_name] = str(datetime.datetime.now())
