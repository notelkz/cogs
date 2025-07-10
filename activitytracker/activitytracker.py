import discord
import asyncio
import aiohttp
import os
import json
from datetime import datetime

from redbot.core import commands, Config
from aiohttp import web
from redbot.core.utils.chat_formatting import humanize_list
from redbot.core.utils.views import ConfirmView # Added for role removal confirmations

import logging

log = logging.getLogger("red.Elkz.activitytracker")

class ActivityTracker(commands.Cog):
    """Tracks user voice activity and syncs with a Django website API."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        
        default_guild = {
            "api_url": None, # For sending activity updates to Django
            "api_key": None, # Key for RedBot -> Django API
            "recruit_role_id": None,
            "member_role_id": None,
            "promotion_threshold_hours": 24.0, # Recruit to Member threshold
            "promotion_channel_id": None,
            "military_ranks": [], # NEW: List of dicts for military ranks
            "promotion_update_url": None # NEW: Specific URL for role update notifications
        }
        self.config.register_guild(**default_guild)
        
        self.voice_tracking = {}
        self.session = aiohttp.ClientSession()
        
        self.web_app = web.Application()
        self.web_runner = None
        self.web_site = None
        
        # Define routes for the internal web server (for the Django site to call)
        self.web_app.router.add_post("/api/assign_initial_role", self.assign_initial_role_handler)
        self.web_app.router.add_get("/health", self.health_check_handler) # Good practice to have

        self.bot.loop.create_task(self.initialize_webserver())

    async def initialize_webserver(self):
        await self.bot.wait_until_ready()
        
        # Get the guild ID from environment variables
        # This setup implies a single guild per bot instance, which might not be ideal for all Red setups.
        # Consider making the guild ID configurable via commands if multi-guild support is needed for this API.
        guild_id_str = os.environ.get("DISCORD_GUILD_ID")
        if not guild_id_str:
            log.critical("CRITICAL ERROR: DISCORD_GUILD_ID environment variable not set for the bot. Web API will not function correctly without a target guild.")
            # Depending on severity, you might want to raise an exception or disable the web server
            return
            
        guild = self.bot.get_guild(int(guild_id_str))
        if not guild:
            log.critical(f"CRITICAL ERROR: Guild with ID {guild_id_str} not found. Bot might not be in the guild or it's not cached yet. Web API will not function correctly.")
            return

        self.web_app["guild"] = guild # Store guild in app for handler access

        try:
            self.web_runner = web.AppRunner(self.web_app)
            await self.web_runner.setup()
            # Use a configurable host/port if desired, for now hardcoding to 0.0.0.0:5002
            host = os.environ.get("ACTIVITY_WEB_HOST", "0.0.0.0") # Allow env var override
            port = int(os.environ.get("ACTIVITY_WEB_PORT", 5002)) # Allow env var override
            self.web_site = web.TCPSite(self.web_runner, host, port) 
            await self.web_site.start()
            log.info(f"ActivityTracker API server started on http://{host}:{port}/")
        except Exception as e:
            log.critical(f"Failed to start ActivityTracker web API server: {e}")
            self.web_runner = None
            self.web_site = None


    def cog_unload(self):
        # Schedule the web server shutdown
        if self.web_runner:
            asyncio.create_task(self._shutdown_web_server())
        # Close the aiohttp session
        asyncio.create_task(self.session.close())
        # Ensure any active voice sessions are logged before unload
        for guild_id, members_tracking in self.voice_tracking.items():
            guild = self.bot.get_guild(guild_id)
            if guild:
                for member_id, join_time in members_tracking.items():
                    member = guild.get_member(member_id)
                    if member:
                        duration_minutes = (datetime.utcnow() - join_time).total_seconds() / 60
                        if duration_minutes >= 1:
                            log.info(f"Unloading: Logging {duration_minutes:.2f} minutes for {member.name} due to cog unload.")
                            asyncio.create_task(self._update_website_activity(guild, member, int(duration_minutes)))
        self.voice_tracking.clear()

    async def _shutdown_web_server(self):
        """Helper to gracefully shut down the aiohttp web server."""
        if self.web_runner:
            log.info("Shutting down ActivityTracker web API server...")
            try:
                await self.web_app.shutdown()
                await self.web_runner.cleanup()
                log.info("ActivityTracker web API server shut down successfully.")
            except Exception as e:
                log.error(f"Error during web API server shutdown: {e}")
        self.web_runner = None
        self.web_site = None

    async def _authenticate_web_request(self, request: web.Request):
        """Authenticates incoming web API requests based on X-API-Key header."""
        guild = request.app["guild"]
        expected_key = await self.config.guild(guild).api_key() # Using the RedBot -> Django key for this web API too
        if not expected_key:
            log.warning(f"Web API key is not set in config for guild {guild.id}, all requests will fail authentication.")
            raise web.HTTPUnauthorized(reason="Web API Key not configured on RedBot for this guild.")
        
        provided_key = request.headers.get("X-API-Key")
        if not provided_key:
            raise web.HTTPUnauthorized(reason="X-API-Key header missing.")
        
        if provided_key != expected_key:
            raise web.HTTPForbidden(reason="Invalid API Key.")
        
        return True

    async def health_check_handler(self, request: web.Request):
        """Simple health check endpoint for the web API."""
        log.debug("Received health check request.")
        return web.Response(text="OK", status=200)

    async def assign_initial_role_handler(self, request):
        """
        Web API handler to assign initial Recruit role to a user.
        Called by the Django website after user registration.
        """
        try:
            await self._authenticate_web_request(request)
        except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
            log.warning(f"Authentication failed for /api/assign_initial_role endpoint: {e.reason}")
            return e
        
        try:
            data = await request.json()
            discord_id = int(data.get("discord_id"))
        except (ValueError, TypeError, json.JSONDecodeError):
            log.warning("Invalid request data received for /api/assign_initial_role")
            return web.Response(text="Invalid request data", status=400)

        guild = request.app["guild"]
        recruit_role_id = await self.config.guild(guild).recruit_role_id()
        if not recruit_role_id:
            log.error(f"Recruit Role ID is not configured for guild {guild.id}.")
            return web.Response(text="Recruit role not configured", status=500)

        member = guild.get_member(discord_id)
        recruit_role = guild.get_role(recruit_role_id)

        if member and recruit_role:
            try:
                if recruit_role not in member.roles: # Only add if they don't have it
                    await member.add_roles(recruit_role, reason="Initial role assignment from website.")
                    log.info(f"Successfully assigned Recruit role to {member.name} ({member.id}).")
                else:
                    log.info(f"Member {member.name} ({member.id}) already has Recruit role. Skipping assignment.")
                return web.Response(text="Role assigned/already present successfully", status=200)
            except discord.Forbidden:
                log.error(f"Missing permissions to assign role to {member.name} ({member.id}).")
                return web.Response(text="Missing permissions", status=503)
            except Exception as e:
                log.exception(f"Failed to assign role to {member.name} ({member.id}): {e}")
                return web.Response(text="Internal server error", status=500)
        else:
            log.warning(f"Could not find member ({discord_id}) or recruit role ({recruit_role_id}) in guild {guild.id}.")
            return web.Response(text="Member or role not found", status=404)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        # Check if the guild of the member matches the configured guild for this cog instance
        # This is particularly relevant if the DISCORD_GUILD_ID env var is used to tie this cog to one guild.
        guild_id_for_cog = int(os.environ.get("DISCORD_GUILD_ID", 0)) # Default to 0 if not set
        if member.guild.id != guild_id_for_cog:
            return # Ignore activity from other guilds if specific guild is configured via env var

        if before.channel is None and after.channel is not None:
            # User joined a voice channel
            if after.channel.guild.id not in self.voice_tracking:
                self.voice_tracking[after.channel.guild.id] = {}
            self.voice_tracking[after.channel.guild.id][member.id] = datetime.utcnow()
            log.debug(f"User {member.name} joined voice in {after.channel.name}. Starting session.")
        elif before.channel is not None and after.channel is None:
            # User left a voice channel
            if before.channel.guild.id in self.voice_tracking and member.id in self.voice_tracking[before.channel.guild.id]:
                join_time = self.voice_tracking[before.channel.guild.id].pop(member.id)
                duration_minutes = (datetime.utcnow() - join_time).total_seconds() / 60
                
                # Only process if duration is at least 1 minute to avoid spamming for quick joins/leaves
                if duration_minutes < 1:
                    log.debug(f"User {member.name} left voice. Duration too short ({duration_minutes:.2f}m). Skipping sync.")
                    return
                
                log.info(f"User {member.name} left voice. Duration: {duration_minutes:.2f} minutes.")
                await self._update_website_activity(member.guild, member, int(duration_minutes))

    async def _update_website_activity(self, guild: discord.Guild, member: discord.Member, minutes_to_add: int):
        guild_settings = await self.config.guild(guild).all()
        api_url = guild_settings.get("api_url")
        api_key = guild_settings.get("api_key")
        if not api_url or not api_key: 
            log.warning(f"Django API URL or Key not configured for guild {guild.id}. Skipping activity update for {member.name}.")
            return
        
        endpoint = f"{api_url}/api/update_activity/" # This is the activity update URL
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {"discord_id": str(member.id), "voice_minutes": minutes_to_add}
        try:
            async with self.session.post(endpoint, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    log.info(f"Successfully synced {minutes_to_add} minutes for user {member.id}.")
                    data = await resp.json()
                    total_minutes = data.get("total_minutes", 0)
                    await self._check_for_promotion(guild, member, total_minutes)
                else:
                    log.error(f"Failed to update activity for {member.id}: {resp.status} - {await resp.text()}")
        except aiohttp.ClientError as e:
            log.error(f"Network error sending activity to Django API for {member.id}: {e}")
        except Exception as e:
            log.exception(f"An unexpected error occurred sending activity to Django API for {member.id}: {e}")

    async def _check_for_promotion(self, guild: discord.Guild, member: discord.Member, total_minutes: int):
        """
        Checks for both Member promotion and Military Rank promotion based on total_minutes.
        """
        guild_settings = await self.config.guild(guild).all()
        
        # --- System 1: Recruit -> Member Promotion ---
        recruit_role_id = guild_settings.get("recruit_role_id")
        member_role_id = guild_settings.get("member_role_id")
        promotion_threshold_hours = guild_settings.get("promotion_threshold_hours")
        
        if all([recruit_role_id, member_role_id, promotion_threshold_hours]):
            promotion_threshold_minutes = promotion_threshold_hours * 60
            recruit_role = guild.get_role(recruit_role_id)
            member_role = guild.get_role(member_role_id) # Get member role here
            
            # Promote if they are a Recruit and meet the threshold
            if recruit_role and member_role and recruit_role in member.roles and total_minutes >= promotion_threshold_minutes:
                log.info(f"Promoting {member.name} ({member.id}) from Recruit to Member...")
                try:
                    await member.remove_roles(recruit_role, reason="Automatic promotion via voice activity")
                    await member.add_roles(member_role, reason="Automatic promotion via voice activity")
                    await self._notify_website_of_promotion(guild, member.id, "member")
                    
                    channel_id = guild_settings.get("promotion_channel_id")
                    if channel_id:
                        channel = guild.get_channel(channel_id)
                        if channel and isinstance(channel, discord.TextChannel): # Ensure it's a text channel
                            await channel.send(
                                f"🎉 Congratulations {member.mention}! You've been promoted to **{member_role.name}** status!"
                            )
                except discord.Forbidden:
                    log.error(f"MEMBERSHIP ERROR: Missing permissions to promote {member.name} ({member.id}).")
                except Exception as e:
                    log.exception(f"MEMBERSHIP ERROR: An unexpected error occurred promoting {member.name} ({member.id}): {e}")
        
        # --- System 2: Military Time Rank Promotion (using local config now!) ---
        # NOTE: This system will now try to apply the highest qualified rank
        # regardless of whether they just got promoted to Member or not.
        # This is the desired consistent behavior.
        military_ranks_config = guild_settings.get("military_ranks")
        
        if not military_ranks_config:
            log.debug(f"No military ranks configured for guild {guild.id}. Skipping military rank promotion.")
            return

        # Sort ranks by required_hours descending to ensure we pick the highest qualified rank
        # We need to ensure required_hours is numeric for sorting
        sorted_ranks = sorted(
            [r for r in military_ranks_config if isinstance(r.get('required_hours'), (int, float))], 
            key=lambda x: x['required_hours'], 
            reverse=True
        )

        user_hours = total_minutes / 60
        earned_rank_data = None
        for rank in sorted_ranks:
            if user_hours >= rank['required_hours']:
                earned_rank_data = rank
                break # Found the highest rank they qualify for

        if not earned_rank_data:
            log.debug(f"User {member.name} ({member.id}) does not qualify for any military rank yet.")
            return # User doesn't qualify for any rank yet

        earned_role_id = int(earned_rank_data['discord_role_id'])
        earned_role_name = earned_rank_data['name'] # Get name from config for logs/messages

        # Optimization: Check if the user already has the highest earned rank.
        # This prevents unnecessary API calls to Discord if their roles are already correct.
        if any(role.id == earned_role_id for role in member.roles):
            log.debug(f"User {member.name} already has rank {earned_role_name}. Skipping role update.")
            return

        log.info(f"Updating {member.name}'s ({member.id}) rank to {earned_role_name} (Total Minutes: {total_minutes}).")
        
        # Get all possible military rank IDs from config to remove any old ones
        all_military_rank_ids = {int(r['discord_role_id']) for r in military_ranks_config if r.get('discord_role_id') is not None}
        
        # Filter out current roles that are old military ranks
        roles_to_remove = [
            role for role in member.roles 
            if role.id in all_military_rank_ids and role.id != earned_role_id
        ]
        
        new_rank_role = guild.get_role(earned_role_id)
        
        if not new_rank_role:
            log.error(f"RANKING ERROR: Configured role ID {earned_role_id} for rank '{earned_role_name}' not found in guild {guild.id}.")
            return

        # Prepare roles to be set. This includes all non-military roles + the new earned military role.
        current_non_military_roles = [
            role for role in member.roles 
            if role.id not in all_military_rank_ids and role.id != recruit_role_id and role.id != member_role_id
        ] # Exclude recruit/member as they are handled by the first system, but ensure no conflict.

        target_roles = set(current_non_military_roles + [new_rank_role])

        # If they are currently a Recruit, and now qualify for a military rank, remove Recruit role
        recruit_role = guild.get_role(recruit_role_id)
        if recruit_role and recruit_role in member.roles and new_rank_role.id != recruit_role_id:
             log.debug(f"Removing Recruit role from {member.name} as they now qualify for a military rank.")
             target_roles.discard(recruit_role)
        
        # Ensure 'Member' role is preserved if applicable and it's not a military rank (which it shouldn't be)
        member_role = guild.get_role(member_role_id)
        if member_role and member_role in member.roles and member_role.id not in all_military_rank_ids:
            target_roles.add(member_role)


        try:
            # Use edit(roles=...) to set the exact set of roles, removing/adding in one go
            await member.edit(roles=list(target_roles), reason=f"Automatic time rank update to {earned_rank_data['name']}")
            log.info(f"RANKING SUCCESS: {member.name} ({member.id}) is now {earned_role_name}.")
            await self._notify_website_of_promotion(guild, member.id, earned_rank_name) # Notify website
            
            # Send promotion message
            channel_id = guild_settings.get("promotion_channel_id")
            if channel_id:
                channel = guild.get_channel(channel_id)
                if channel and isinstance(channel, discord.TextChannel):
                    await channel.send(
                        f"🎖️ Bravo, {member.mention}! You've achieved the rank of **{earned_role_name}**!"
                    )

        except discord.Forbidden:
            log.error(f"RANKING ERROR: Missing permissions to manage roles for {member.name} ({member.id}).")
        except Exception as e:
            log.exception(f"RANKING ERROR: An unexpected error occurred during military rank update for {member.name} ({member.id}): {e}")

    async def _notify_website_of_promotion(self, guild: discord.Guild, discord_id: int, new_role_name: str):
        guild_settings = await self.config.guild(guild).all()
        promotion_update_url = guild_settings.get("promotion_update_url") # Use specific promotion URL
        api_key = guild_settings.get("api_key")
        if not promotion_update_url or not api_key: 
            log.warning(f"Promotion update URL or API Key not configured for guild {guild.id}. Skipping promotion notification for {discord_id}.")
            return
        
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {"discord_id": str(discord_id), "new_role_name": new_role_name} # Changed 'new_role' to 'new_role_name' for clarity
        try:
            async with self.session.post(promotion_update_url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    log.info(f"Successfully notified website of promotion for {discord_id} to {new_role_name}.")
                else:
                    log.error(f"Failed to notify website of promotion for {discord_id} to {new_role_name}: {resp.status} - {await resp.text()}")
        except aiohttp.ClientError as e:
            log.error(f"Network error notifying website of promotion for {discord_id}: {e}")
        except Exception as e:
            log.exception(f"An unexpected error occurred notifying website of promotion for {discord_id}: {e}")

    @commands.group(name="activityset")
    @commands.admin_or_permissions(manage_guild=True)
    async def activityset(self, ctx):
        """Manage ActivityTracker settings."""
        pass
    
    @activityset.command(name="api")
    async def set_api(self, ctx, url: str, key: str):
        """Sets the Django API URL and Key for sending activity updates."""
        if not url.startswith("http"):
            return await ctx.send("The URL must start with `http://` or `https://`.")
        await self.config.guild(ctx.guild).api_url.set(url)
        await self.config.guild(ctx.guild).api_key.set(key)
        await ctx.send("API URL and Key for activity tracking have been set.")

    @activityset.command(name="promotionurl")
    async def set_promotion_url(self, ctx, url: str):
        """Sets the Django API URL for notifying about role promotions (e.g., /api/update_role/)."""
        if not url.startswith("http"):
            return await ctx.send("The URL must start with `http://` or `https://`.")
        await self.config.guild(ctx.guild).promotion_update_url.set(url)
        await ctx.send(f"Promotion update URL set to: `{url}`")

    @activityset.command(name="roles")
    async def set_roles(self, ctx, recruit_role: discord.Role, member_role: discord.Role):
        """Sets the Recruit and Member roles for the promotion system."""
        await self.config.guild(ctx.guild).recruit_role_id.set(recruit_role.id)
        await self.config.guild(ctx.guild).member_role_id.set(member_role.id)
        await ctx.send(f"Membership promotion roles set: Recruit = `{recruit_role.name}`, Member = `{member_role.name}`")
    
    @activityset.command(name="threshold")
    async def set_threshold(self, ctx, hours: float):
        """Sets the activity threshold (in hours) for Recruit to Member promotion."""
        if hours <= 0: return await ctx.send("Threshold must be a positive number of hours.")
        await self.config.guild(ctx.guild).promotion_threshold_hours.set(hours)
        await ctx.send(f"Recruit to Member promotion threshold set to `{hours}` hours.")
    
    @activityset.command(name="channel")
    async def set_channel(self, ctx, channel: discord.TextChannel = None):
        """Sets the channel where promotion announcements will be sent."""
        if channel:
            await self.config.guild(ctx.guild).promotion_channel_id.set(channel.id)
            await ctx.send(f"Promotion announcements will be sent to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).promotion_channel_id.set(None)
            await ctx.send("Promotion announcements have been disabled.")

    @activityset.group(name="militaryranks")
    async def military_ranks_group(self, ctx):
        """Manage military rank promotion settings."""
        pass

    @military_ranks_group.command(name="add")
    async def add_military_rank(self, ctx, role: discord.Role, required_hours: float):
        """
        Adds or updates a military rank.
        Required hours should be cumulative for this rank.
        Ranks are ordered by required_hours internally.
        """
        if required_hours < 0:
            return await ctx.send("Required hours must be 0 or greater.")
        
        async with self.config.guild(ctx.guild).military_ranks() as military_ranks:
            # Check if this role ID already exists
            existing_rank_index = next((i for i, r in enumerate(military_ranks) if int(r['discord_role_id']) == role.id), -1)

            if existing_rank_index != -1:
                # Update existing rank
                old_hours = military_ranks[existing_rank_index]['required_hours']
                military_ranks[existing_rank_index]['name'] = role.name
                military_ranks[existing_rank_index]['required_hours'] = required_hours
                await ctx.send(f"Updated military rank `{role.name}` (`{role.id}`). Old hours: `{old_hours}`. New hours: `{required_hours}`.")
            else:
                # Add new rank
                military_ranks.append({
                    "name": role.name,
                    "discord_role_id": str(role.id), # Store as string for JSON compatibility and consistency
                    "required_hours": required_hours
                })
                await ctx.send(f"Added military rank `{role.name}` (`{role.id}`) requiring `{required_hours}` hours.")
            
            # Re-sort is not strictly necessary on add/update here, as it's done in _check_for_promotion, 
            # but can be useful for display consistency in `show` command or if you store `rank_order`.
            # For now, relying on sort in _check_for_promotion is fine.

    @military_ranks_group.command(name="remove")
    async def remove_military_rank(self, ctx, role: discord.Role):
        """Removes a military rank by its Discord role."""
        async with self.config.guild(ctx.guild).military_ranks() as military_ranks:
            initial_len = len(military_ranks)
            military_ranks[:] = [r for r in military_ranks if int(r['discord_role_id']) != role.id]
            if len(military_ranks) < initial_len:
                await ctx.send(f"Removed military rank `{role.name}` (`{role.id}`).")
            else:
                await ctx.send(f"Military rank `{role.name}` (`{role.id}`) not found in config.")

    @military_ranks_group.command(name="list")
    async def list_military_ranks(self, ctx):
        """Lists all configured military ranks."""
        military_ranks = await self.config.guild(ctx.guild).military_ranks()
        if not military_ranks:
            return await ctx.send("No military ranks configured.")
        
        # Sort for display, generally from lowest hours to highest for clarity
        sorted_ranks = sorted(military_ranks, key=lambda x: x.get('required_hours', 0))

        msg = "**Configured Military Ranks (by required hours):**\n"
        for rank in sorted_ranks:
            role = ctx.guild.get_role(int(rank['discord_role_id']))
            role_display = role.name if role else f"ID: {rank['discord_role_id']} (Not found)"
            msg += f"- `{role_display}`: **{rank['required_hours']} hours**\n"
        await ctx.send(msg)

    @activityset.command(name="status")
    @commands.admin_or_permissions(manage_guild=True)
    async def show_status(self, ctx):
        """Shows the current ActivityTracker settings."""
        settings = await self.config.guild(ctx.guild).all()

        api_url = settings.get("api_url")
        api_key_set = "Yes" if settings.get("api_key") else "No"
        promotion_url = settings.get("promotion_update_url")

        recruit_role = ctx.guild.get_role(settings.get("recruit_role_id"))
        member_role = ctx.guild.get_role(settings.get("member_role_id"))
        promotion_threshold = settings.get("promotion_threshold_hours")
        promotion_channel = ctx.guild.get_channel(settings.get("promotion_channel_id"))
        
        military_ranks = settings.get("military_ranks")
        military_ranks_count = len(military_ranks) if military_ranks else 0

        status_msg = (
            f"**ActivityTracker Settings for {ctx.guild.name}:**\n"
            f"  - **Django API URL (Activity Sync):** `{api_url or 'Not set'}`\n"
            f"  - **Django API Key Set (Activity Sync):** `{api_key_set}`\n"
            f"  - **Django API URL (Promotion Notify):** `{promotion_url or 'Not set'}`\n"
            f"  - **Recruit Role:** `{recruit_role.name}` ({recruit_role.id})" if recruit_role else "Not set" + "\n"
            f"  - **Member Role:** `{member_role.name}` ({member_role.id})" if member_role else "Not set" + "\n"
            f"  - **Promotion Threshold (Recruit->Member):** `{promotion_threshold or 'Not set'}` hours\n"
            f"  - **Promotion Announcement Channel:** {promotion_channel.mention}" if promotion_channel else "`Not set`" + "\n"
            f"  - **Configured Military Ranks:** `{military_ranks_count}` (Use `[p]activityset militaryranks list` to see details)\n"
        )
        await ctx.send(status_msg)


async def setup(bot):
    """Adds the ActivityTracker cog to the bot."""
    if not bot.intents.members:
        log.critical("Members intent is NOT enabled! ActivityTracker requires the Members intent to track voice activity and manage roles.")
        raise RuntimeError("Members intent is not enabled.")
    if not bot.intents.voice_states:
        log.critical("Voice States intent is NOT enabled! ActivityTracker requires the Voice States intent to track voice activity.")
        raise RuntimeError("Voice States intent is not enabled.")

    await bot.add_cog(ActivityTracker(bot))