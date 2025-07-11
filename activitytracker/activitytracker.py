import discord
import asyncio
import aiohttp
import os
import json
from datetime import datetime

from redbot.core import commands, Config, app_commands # Added app_commands
from aiohttp import web
from redbot.core.utils.chat_formatting import humanize_list
from redbot.core.utils.views import ConfirmView 

import logging

log = logging.getLogger("red.Elkz.activitytracker")

class ActivityTracker(commands.Cog):
    """
    Tracks user voice activity, handles Discord role promotions (Recruit/Member, Military Ranks),
    and exposes an API for a Django website to query member initial role assignment and military rank definitions.
    Also includes a periodic role check.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        
        default_guild = {
            "api_url": None, # For sending activity updates (e.g., http://your.site:8000/api/update_activity/)
            "api_key": None, # Key for RedBot -> Django API (outbound authentication)
            "web_api_key": None, # Key for Django -> RedBot API (inbound authentication)
            "recruit_role_id": None,
            "member_role_id": None,
            "promotion_threshold_hours": 24.0, # Recruit to Member threshold
            "promotion_channel_id": None,
            "military_ranks": [], # List of dicts for military ranks, configured via bot commands
            "promotion_update_url": None # Specific URL for role update notifications (e.g., http://your.site:8000/api/update_role/)
        }
        self.config.register_guild(**default_guild)
        
        self.voice_tracking = {}
        self.session = aiohttp.ClientSession()
        
        self.web_app = web.Application()
        self.web_runner = None
        self.web_site = None
        
        # Define routes for the internal web server (for the Django site to call)
        self.web_app.router.add_post("/api/assign_initial_role", self.assign_initial_role_handler)
        self.web_app.router.add_get("/api/get_military_ranks", self.get_military_ranks_handler)
        self.web_app.router.add_get("/health", self.health_check_handler)

        # The web server is now started in on_ready

    async def _perform_unload_cleanup(self, pending_activity_updates: list):
        """Helper to await pending tasks and close the session during cog unload."""
        if pending_activity_updates:
            # Use return_exceptions=True to ensure all tasks run even if one fails
            results = await asyncio.gather(*pending_activity_updates, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception):
                    log.error(f"Error during pending activity update on unload: {res}")
            log.info("Completed pending activity updates during cog unload.")
        
        await self.session.close()
        log.info("aiohttp session closed.")

    def cog_unload(self):
        # Schedule web server shutdown immediately
        if self.web_runner:
            asyncio.create_task(self._shutdown_web_server())

        # Collect tasks for pending voice activity updates from self.voice_tracking
        pending_activity_updates = []
        for guild_id, members_tracking in self.voice_tracking.items():
            guild = self.bot.get_guild(guild_id)
            if guild:
                for member_id, join_time in members_tracking.items():
                    member = guild.get_member(member_id)
                    if member:
                        duration_minutes = (datetime.utcnow() - join_time).total_seconds() / 60
                        # Only log durations of at least 1 minute to avoid spam for quick joins/leaves
                        if duration_minutes >= 1:
                            log.info(f"Unloading: Logging {duration_minutes:.2f} minutes for {member.name} ({member.id}) due to cog unload.")
                            # Add to a list of coroutines to be awaited later
                            pending_activity_updates.append(self._update_website_activity(guild, member, int(duration_minutes)))
        self.voice_tracking.clear() # Clear tracking after gathering all data

        # Ensure these tasks are run. Use ensure_future to schedule them without blocking cog_unload
        # as cog_unload must return quickly. The cleanup helper will then await them.
        asyncio.ensure_future(self._perform_unload_cleanup(pending_activity_updates))


    async def _shutdown_web_server(self):
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
        # Ensure 'guild' is available in request.app from on_ready
        guild = request.app.get("guild")
        if not guild:
            log.critical("Web API received request before guild context was set.")
            raise web.HTTPInternalServerError(reason="Bot not fully initialized.")

        expected_key = await self.config.guild(guild).web_api_key() 
        if not expected_key:
            log.warning(f"Web API key is not set in config for guild {guild.id}, all requests to bot's API will fail authentication.")
            raise web.HTTPUnauthorized(reason="Web API Key not configured on RedBot for this guild.")
        
        provided_key = request.headers.get("X-API-Key")
        if not provided_key:
            raise web.HTTPUnauthorized(reason="X-API-Key header missing.")
        
        if provided_key != expected_key:
            raise web.HTTPForbidden(reason="Invalid API Key.")
        
        return True

    async def health_check_handler(self, request: web.Request):
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
                if recruit_role not in member.roles: 
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

    async def get_military_ranks_handler(self, request):
        """
        Web API handler to return the configured military rank definitions.
        Django website will call this endpoint to get the ranks.
        """
        try:
            await self._authenticate_web_request(request)
        except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
            log.warning(f"Authentication failed for /api/get_military_ranks endpoint: {e.reason}")
            return e
        
        guild = request.app["guild"]
        military_ranks = await self.config.guild(guild).military_ranks()
        
        if not military_ranks:
            log.debug(f"No military ranks configured in bot for guild {guild.id}.")
            return web.json_response([], status=200)

        try:
            sorted_ranks = sorted(
                [r for r in military_ranks if 'required_hours' in r and isinstance(r['required_hours'], (int, float))], 
                key=lambda x: x['required_hours']
            )
        except Exception as e:
            log.error(f"Error sorting military ranks for API response: {e}")
            return web.Response(text="Internal Server Error: Malformed rank data", status=500)

        log.debug(f"Returning {len(sorted_ranks)} military ranks from bot config via API.")
        return web.json_response(sorted_ranks)

    async def _get_total_minutes_from_django(self, guild: discord.Guild, member_id: int) -> int | None:
        """
        Fetches the total voice minutes for a specific Discord user from the Django backend.
        This assumes Django has an endpoint like /api/get_user_activity/<discord_id>/
        """
        guild_settings = await self.config.guild(guild).all()
        api_url = guild_settings.get("api_url")
        api_key = guild_settings.get("api_key")

        if not api_url or not api_key:
            log.warning(f"Django API URL or Key not configured for guild {guild.id}. Cannot fetch user activity.")
            return None

        # Construct the endpoint for fetching a specific user's activity
        endpoint = f"{api_url}/api/get_user_activity/{member_id}/" 
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

        try:
            async with self.session.get(endpoint, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    total_minutes = data.get("total_minutes")
                    if isinstance(total_minutes, (int, float)):
                        log.debug(f"Fetched {total_minutes} minutes for user {member_id} from Django.")
                        return int(total_minutes)
                    else:
                        log.error(f"Django API for user {member_id} returned invalid 'total_minutes': {total_minutes}")
                        return None
                elif resp.status == 404:
                    log.info(f"User {member_id} not found in Django activity records. Assuming 0 minutes.")
                    return 0 # User not found, assume 0 minutes
                else:
                    log.error(f"Failed to fetch activity for {member_id} from Django: {resp.status} - {await resp.text()}")
                    return None
        except aiohttp.ClientConnectorError as e:
            log.error(f"Network error fetching activity for {member_id} from Django: {e}. Is the server running and accessible?")
            return None
        except asyncio.TimeoutError:
            log.error(f"Timeout fetching activity for {member_id} from Django.")
            return None
        except Exception as e:
            log.exception(f"An unexpected error occurred fetching activity for {member_id} from Django: {e}")
            return None

    async def _periodic_role_check(self, guild_id: int):
        """
        Performs a periodic check of all guild members' roles based on their total voice activity.
        This function is intended to be called by the Redbot scheduler.
        """
        log.info(f"Starting periodic role check for guild ID: {guild_id}")
        guild = self.bot.get_guild(guild_id)
        if not guild:
            log.error(f"Guild with ID {guild_id} not found for periodic role check.")
            return

        members_checked = 0
        promotions_made = 0

        # Fetch all members to check. Using fetch_members() for full list, but be mindful of large guilds.
        try:
            # Added a small delay per 100 members to reduce Discord API load for large guilds
            member_count_for_delay = 0
            async for member in guild.fetch_members(limit=None): # Fetch all members
                if member.bot:
                    continue # Skip bots
                
                members_checked += 1
                total_minutes = await self._get_total_minutes_from_django(guild, member.id)

                if total_minutes is not None:
                    # _check_for_promotion handles both Recruit->Member and Military Ranks
                    initial_roles = {r.id for r in member.roles}
                    await self._check_for_promotion(guild, member, total_minutes)
                    final_roles = {r.id for r in member.roles}

                    if initial_roles != final_roles:
                        promotions_made += 1
                        log.info(f"Role change detected for {member.name} ({member.id}) during periodic check.")
                else:
                    log.warning(f"Could not get total minutes for {member.name} ({member.id}) during periodic check. Skipping.")
                
                member_count_for_delay += 1
                if member_count_for_delay % 100 == 0:
                    await asyncio.sleep(0.5) # Small delay to avoid hitting Discord/API rate limits too hard for large guilds

        except discord.Forbidden:
            log.error(f"Bot lacks permissions to fetch members in guild {guild.id} for periodic check. Ensure 'Members Intent' is enabled.")
        except Exception as e:
            log.exception(f"An unexpected error occurred during periodic role check for guild {guild.id}: {e}")

        log.info(f"Periodic role check complete for guild {guild.id}. Checked {members_checked} members, made {promotions_made} role changes.")

    @commands.Cog.listener()
    async def on_ready(self):
        """Initialize the web server when the bot is ready."""
        # Check if web server is already running (e.g., if cog reloaded)
        if self.web_runner:
            log.debug("ActivityTracker web API already running from previous load.")
            return

        guild_id_str = os.environ.get("DISCORD_GUILD_ID")
        if not guild_id_str:
            log.critical("CRITICAL ERROR: DISCORD_GUILD_ID environment variable not set. Web API will not function. Please set it.")
            return
            
        try:
            guild = self.bot.get_guild(int(guild_id_str))
        except ValueError:
            log.critical(f"CRITICAL ERROR: Invalid DISCORD_GUILD_ID '{guild_id_str}'. Must be an integer. Web API will not function.")
            return

        if not guild:
            log.critical(f"CRITICAL ERROR: Guild with ID {guild_id_str} not found. Web API will not function. Is the bot in this guild?")
            return

        self.web_app["guild"] = guild # Store guild object in app for handlers
        log.debug(f"Assigned guild {guild.name} ({guild.id}) to web_app context.")

        try:
            self.web_runner = web.AppRunner(self.web_app)
            await self.web_runner.setup()
            host = os.environ.get("ACTIVITY_WEB_HOST", "0.0.0.0")
            port = int(os.environ.get("ACTIVITY_WEB_PORT", 5002))
            self.web_site = web.TCPSite(self.web_runner, host, port) 
            await self.web_site.start()
            log.info(f"ActivityTracker API server started on http://{host}:{port}/")
        except Exception as e:
            log.critical(f"Failed to start ActivityTracker web API server: {e}")
            self.web_runner = None
            self.web_site = None


    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        
        # Only track for the specific guild defined by DISCORD_GUILD_ID
        guild_id_str = os.environ.get("DISCORD_GUILD_ID")
        if not guild_id_str:
            # If the env var isn't set, we can't determine the target guild, so skip.
            # This critical error is already logged in on_ready/initialize_webserver.
            return
        
        try:
            target_guild_id = int(guild_id_str)
        except ValueError:
            # Already logged in on_ready, but defensive check
            return 

        if member.guild.id != target_guild_id:
            return # Not the target guild

        # User joined voice channel
        if before.channel is None and after.channel is not None:
            # Only track if they are not muted by themselves (server mute is fine)
            # This is optional, but often bots ignore afk users or those explicitly muted
            # if not member.self_mute and not member.self_deaf:
            if after.channel.guild.id not in self.voice_tracking:
                self.voice_tracking[after.channel.guild.id] = {}
            self.voice_tracking[after.channel.guild.id][member.id] = datetime.utcnow()
            log.debug(f"User {member.name} ({member.id}) joined voice in {after.channel.name}. Starting session.")

        # User left voice channel
        elif before.channel is not None and after.channel is None:
            if before.channel.guild.id in self.voice_tracking and member.id in self.voice_tracking[before.channel.guild.id]:
                join_time = self.voice_tracking[before.channel.guild.id].pop(member.id)
                duration_seconds = (datetime.utcnow() - join_time).total_seconds()
                duration_minutes = duration_seconds / 60
                
                # Only log durations of at least 1 minute to avoid spam and noise from quick joins/leaves
                if duration_minutes < 1:
                    log.debug(f"User {member.name} ({member.id}) left voice. Duration too short ({duration_minutes:.2f}m). Skipping sync.")
                    return
                
                log.info(f"User {member.name} ({member.id}) left voice. Duration: {duration_minutes:.2f} minutes.")
                await self._update_website_activity(member.guild, member, int(duration_minutes))

    async def _update_website_activity(self, guild: discord.Guild, member: discord.Member, minutes_to_add: int):
        guild_settings = await self.config.guild(guild).all()
        api_url = guild_settings.get("api_url")
        api_key = guild_settings.get("api_key")
        if not api_url or not api_key: 
            log.warning(f"Django API URL or Key not configured for guild {guild.id}. Skipping activity update for {member.name}.")
            return
        
        # Ensure the URL has a trailing slash if it's expecting one for correct path resolution
        endpoint = f"{api_url.rstrip('/')}/api/update_activity/"
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {"discord_id": str(member.id), "voice_minutes": minutes_to_add}
        try:
            async with self.session.post(endpoint, headers=headers, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    log.info(f"Successfully synced {minutes_to_add} minutes for user {member.id}.")
                    data = await resp.json()
                    total_minutes = data.get("total_minutes", 0)
                    # Important: Use the total_minutes returned by Django, as it's the authoritative source
                    await self._check_for_promotion(guild, member, total_minutes)
                else:
                    log.error(f"Failed to update activity for {member.id}: {resp.status} - {await resp.text()}")
        except aiohttp.ClientConnectorError as e:
            log.error(f"Network error sending activity to Django API for {member.id}: {e}. Is the server running and accessible?")
        except asyncio.TimeoutError:
            log.error(f"Timeout sending activity to Django API for {member.id}.")
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
            member_role = guild.get_role(member_role_id) 
            
            if recruit_role and member_role and recruit_role in member.roles and total_minutes >= promotion_threshold_minutes:
                log.info(f"Promoting {member.name} ({member.id}) from Recruit to Member...")
                try:
                    # Remove recruit role first, then add member role
                    await member.remove_roles(recruit_role, reason="Automatic promotion via voice activity")
                    await member.add_roles(member_role, reason="Automatic promotion via voice activity")
                    await self._notify_website_of_promotion(guild, member.id, "member") # Notify as 'member'
                    
                    channel_id = guild_settings.get("promotion_channel_id")
                    if channel_id:
                        channel = guild.get_channel(channel_id)
                        if channel and isinstance(channel, discord.TextChannel):
                            await channel.send(
                                f"🎉 Congratulations {member.mention}! You've been promoted to **{member_role.name}** status!"
                            )
                except discord.Forbidden:
                    log.error(f"MEMBERSHIP ERROR: Missing permissions to promote {member.name} ({member.id}). Check bot permissions for roles and channels.")
                except Exception as e:
                    log.exception(f"MEMBERSHIP ERROR: An unexpected error occurred promoting {member.name} ({member.id}): {e}")
        
        # --- System 2: Military Time Rank Promotion (USING BOT'S LOCAL CONFIG) ---
        military_ranks_config = guild_settings.get("military_ranks")
        
        if not military_ranks_config:
            log.debug(f"No military ranks configured in bot for guild {guild.id}. Skipping military rank promotion.")
            return

        try:
            # Sort in reverse order to find the highest rank achieved
            sorted_ranks = sorted(
                [r for r in military_ranks_config if isinstance(r.get('required_hours'), (int, float))], 
                key=lambda x: x['required_hours'], 
                reverse=True
            )
        except Exception as e:
            log.error(f"RANKING ERROR: Malformed rank data in bot config. Could not sort: {e}")
            return

        user_hours = total_minutes / 60
        earned_rank_data = None
        for rank in sorted_ranks:
            if 'discord_role_id' not in rank or not str(rank['discord_role_id']).isdigit():
                log.warning(f"RANKING WARNING: Invalid or missing 'discord_role_id' in configured rank data: {rank}. Skipping.")
                continue

            if user_hours >= rank['required_hours']:
                earned_rank_data = rank
                break # Found the highest rank they qualify for

        if not earned_rank_data:
            log.debug(f"User {member.name} ({member.id}) does not qualify for any military rank yet.")
            return

        earned_role_id = int(earned_rank_data['discord_role_id'])
        earned_role_name = earned_rank_data.get('name', f"Rank {earned_role_id}") 

        # Check if the user already has this specific rank
        if any(role.id == earned_role_id for role in member.roles):
            log.debug(f"User {member.name} already has rank {earned_role_name}. Skipping role update.")
            return

        log.info(f"Updating {member.name}'s ({member.id}) rank to {earned_role_name} (Total Minutes: {total_minutes}).")
        
        # Build the new set of roles for the member
        all_military_rank_ids = {
            int(r['discord_role_id']) 
            for r in military_ranks_config 
            if r.get('discord_role_id') is not None and str(r['discord_role_id']).isdigit()
        }
        
        # Start with roles that are NOT military ranks, nor the recruit/member roles
        # This prevents removing unrelated roles and handles the base membership roles.
        current_roles_to_keep = {
            role for role in member.roles 
            if role.id not in all_military_rank_ids 
            and role.id != recruit_role_id 
            and role.id != member_role_id
        }
        
        # Add the newly earned military rank
        new_rank_role = guild.get_role(earned_role_id)
        if not new_rank_role:
            log.error(f"RANKING ERROR: Configured role ID {earned_role_id} for rank '{earned_role_name}' not found in guild {guild.id}. Please ensure the role exists in Discord.")
            return

        current_roles_to_keep.add(new_rank_role)

        # Ensure Recruit role is removed if they earned a military rank (and it's not the recruit role itself)
        # and ensure Member role is kept if they have it and it's not a military rank itself
        recruit_role = guild.get_role(recruit_role_id)
        member_role = guild.get_role(member_role_id)

        if recruit_role and recruit_role in member.roles and new_rank_role.id != recruit_role.id:
            log.debug(f"Removing Recruit role from {member.name} as they now qualify for a military rank.")
            current_roles_to_keep.discard(recruit_role) # Ensure it's not in the set

        if member_role and member_role in member.roles and member_role.id not in all_military_rank_ids:
            current_roles_to_keep.add(member_role) # Ensure member role is present if they should have it

        try:
            # Use await member.edit to set all roles at once for efficiency and atomicity
            await member.edit(roles=list(current_roles_to_keep), reason=f"Automatic time rank update to {earned_rank_data['name']}")
            log.info(f"RANKING SUCCESS: {member.name} ({member.id}) is now {earned_role_name}.")
            await self._notify_website_of_promotion(guild, member.id, earned_role_name)
            
            channel_id = guild_settings.get("promotion_channel_id")
            if channel_id:
                channel = guild.get_channel(channel_id)
                if channel and isinstance(channel, discord.TextChannel):
                    await channel.send(
                        f"🎖️ Bravo, {member.mention}! You've achieved the rank of **{earned_role_name}**!"
                    )

        except discord.Forbidden:
            log.error(f"RANKING ERROR: Missing permissions to manage roles for {member.name} ({member.id}). Check bot permissions for {earned_role_name} and other military roles (especially order in Discord).")
        except Exception as e:
            log.exception(f"RANKING ERROR: An unexpected error occurred during military rank update for {member.name} ({member.id}): {e}")

    async def _notify_website_of_promotion(self, guild: discord.Guild, discord_id: int, new_role_name: str):
        guild_settings = await self.config.guild(guild).all()
        promotion_update_url = guild_settings.get("promotion_update_url")
        api_key = guild_settings.get("api_key")
        if not promotion_update_url or not api_key: 
            log.warning(f"Promotion update URL or API Key not configured for guild {guild.id}. Skipping promotion notification for {discord_id}.")
            return
        
        # Ensure the URL has a trailing slash if it's expecting one for correct path resolution
        endpoint = f"{promotion_update_url.rstrip('/')}/api/update_role/"
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {"discord_id": str(discord_id), "new_role_name": new_role_name}
        try:
            async with self.session.post(endpoint, headers=headers, json=payload, timeout=5) as resp:
                if resp.status == 200:
                    log.info(f"Successfully notified website of promotion for {discord_id} to {new_role_name}.")
                else:
                    log.error(f"Failed to notify website of promotion for {discord_id} to {new_role_name}: {resp.status} - {await resp.text()}")
        except aiohttp.ClientConnectorError as e:
            log.error(f"NETWORK ERROR: Could not connect to promotion update API at {promotion_update_url}: {e}. Is the server running and accessible?")
        except asyncio.TimeoutError:
            log.error(f"NETWORK ERROR: Timeout notifying website of promotion for {discord_id} to {new_role_name}.")
        except Exception as e:
            log.exception(f"An unexpected error occurred notifying website of promotion for {discord_id}: {e}")

    @commands.group(name="activityset")
    @commands.admin_or_permissions(manage_guild=True)
    async def activityset(self, ctx: commands.Context): # Added type hint for ctx
        """Manage ActivityTracker settings."""
        pass
    
    @activityset.command(name="api")
    @app_commands.describe(url="The Django API endpoint URL (e.g., http://your.site:8000/api/update_activity/)")
    @app_commands.describe(key="The secret API key for your Django endpoint.")
    async def set_api(self, ctx: commands.Context, url: str, key: str):
        """Sets the Django API URL (for activity sync) and Key."""
        if not url.startswith("http"):
            return await ctx.send("The URL must start with `http://` or `https://`.")
        await self.config.guild(ctx.guild).api_url.set(url)
        await self.config.guild(ctx.guild).api_key.set(key)
        await ctx.send("Django API URL and Key for activity tracking have been set.")

    @activityset.command(name="promotionurl")
    @app_commands.describe(url="The Django API URL for notifying about role promotions (e.g., http://your.site:8000/api/update_role/).")
    async def set_promotion_url(self, ctx: commands.Context, url: str):
        """Sets the Django API URL for notifying about role promotions (e.g., /api/update_role/)."""
        if not url.startswith("http"):
            return await ctx.send("The URL must start with `http://` or `https://`.")
        await self.config.guild(ctx.guild).promotion_update_url.set(url)
        await ctx.send(f"Promotion update URL set to: `{url}`")

    @activityset.command(name="webapikey")
    @app_commands.describe(key="The secret API key for YOUR Django website to authenticate with THIS bot's API.")
    async def set_web_api_key(self, ctx: commands.Context, key: str):
        """Sets the secret API key for your Django website to authenticate with this bot's API."""
        if len(key) < 16:
            return await ctx.send("Please provide a longer, more secure API key (e.g., 16+ characters).")
        await self.config.guild(ctx.guild).web_api_key.set(key)
        await ctx.send("Web API Key for incoming requests has been set. Keep this key secure!")
        log.info(f"Web API key set by {ctx.author} for guild {ctx.guild.id}.")

    @activityset.command(name="roles")
    @app_commands.describe(recruit_role="The role new members receive (e.g., Recruit).")
    @app_commands.describe(member_role="The role members are promoted to (e.g., Member).")
    async def set_roles(self, ctx: commands.Context, recruit_role: discord.Role, member_role: discord.Role):
        """Sets the Recruit and Member roles for the promotion system."""
        await self.config.guild(ctx.guild).recruit_role_id.set(recruit_role.id)
        await self.config.guild(ctx.guild).member_role_id.set(member_role.id)
        await ctx.send(f"Membership promotion roles set: Recruit = `{recruit_role.name}`, Members = `{member_role.name}`")
    
    @activityset.command(name="threshold")
    @app_commands.describe(hours="The activity threshold (in hours) for Recruit to Member promotion (e.g., 24.0).")
    async def set_threshold(self, ctx: commands.Context, hours: float):
        """Sets the activity threshold (in hours) for Recruit to Member promotion."""
        if hours <= 0: return await ctx.send("Threshold must be a positive number of hours.")
        await self.config.guild(ctx.guild).promotion_threshold_hours.set(hours)
        await ctx.send(f"Recruit to Member promotion threshold set to `{hours}` hours.")
    
    @activityset.command(name="channel")
    @app_commands.describe(channel="The channel where promotion announcements will be sent (or leave blank to disable).")
    async def set_channel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Sets the channel where promotion announcements will be sent."""
        if channel:
            await self.config.guild(ctx.guild).promotion_channel_id.set(channel.id)
            await ctx.send(f"Promotion announcements will be sent to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).promotion_channel_id.set(None)
            await ctx.send("Promotion announcements have been disabled.")

    @activityset.group(name="militaryranks")
    async def military_ranks_group(self, ctx: commands.Context):
        """Manage military rank promotion settings (configured in the bot)."""
        pass

    @military_ranks_group.command(name="add")
    @app_commands.describe(role="The Discord role for this military rank.")
    @app_commands.describe(required_hours="The cumulative hours required for this rank.")
    async def add_military_rank(self, ctx: commands.Context, role: discord.Role, required_hours: float):
        """
        Adds or updates a military rank.
        Required hours should be cumulative for this rank.
        Ranks are ordered by required_hours internally.
        """
        if required_hours < 0:
            return await ctx.send("Required hours must be 0 or greater.")
        
        async with self.config.guild(ctx.guild).military_ranks() as military_ranks:
            existing_rank_index = next((i for i, r in enumerate(military_ranks) if str(r['discord_role_id']) == str(role.id)), -1)

            if existing_rank_index != -1:
                old_hours = military_ranks[existing_rank_index]['required_hours']
                military_ranks[existing_rank_index]['name'] = role.name
                military_ranks[existing_rank_index]['discord_role_id'] = str(role.id) # Ensure string for consistency
                military_ranks[existing_rank_index]['required_hours'] = required_hours
                await ctx.send(f"Updated military rank `{role.name}` (`{role.id}`). Old hours: `{old_hours}`. New hours: `{required_hours}`.")
            else:
                military_ranks.append({
                    "name": role.name,
                    "discord_role_id": str(role.id),
                    "required_hours": required_hours
                })
                await ctx.send(f"Added military rank `{role.name}` (`{role.id}`) requiring `{required_hours}` hours.")

    @military_ranks_group.command(name="remove")
    @app_commands.describe(role="The Discord role of the military rank to remove.")
    async def remove_military_rank(self, ctx: commands.Context, role: discord.Role):
        """Removes a military rank by its Discord role."""
        async with self.config.guild(ctx.guild).military_ranks() as military_ranks:
            initial_len = len(military_ranks)
            military_ranks[:] = [r for r in military_ranks if str(r['discord_role_id']) != str(role.id)]
            if len(military_ranks) < initial_len:
                await ctx.send(f"Removed military rank `{role.name}` (`{role.id}`).")
            else:
                await ctx.send(f"Military rank `{role.name}` (`{role.id}`) not found in config.")

    @military_ranks_group.command(name="list")
    async def list_military_ranks(self, ctx: commands.Context):
        """Lists all configured military ranks in order of required hours."""
        military_ranks = await self.config.guild(ctx.guild).military_ranks()
        
        if not military_ranks:
            return await ctx.send("No military ranks have been configured.")
        
        try:
            sorted_ranks = sorted(military_ranks, key=lambda x: x['required_hours'])
        except (KeyError, TypeError) as e:
            log.error(f"Error sorting military ranks for display: {e}")
            return await ctx.send("Error: Some military ranks have invalid or missing `required_hours` values. Check bot logs for details.")
        
        embed = discord.Embed(
            title="Military Ranks Configuration",
            description="Ranks are listed in order of required hours (lowest to highest).",
            color=discord.Color.blue()
        )
        
        for rank in sorted_ranks:
            role_id = rank.get('discord_role_id')
            role_name = rank.get('name', 'Unknown')
            hours = rank.get('required_hours', 'Unknown')
            
            role = ctx.guild.get_role(int(role_id)) if role_id and str(role_id).isdigit() else None
            status = "✅ Valid" if role else "❌ Role not found in server"
            
            embed.add_field(
                name=f"{role_name} ({hours} hours)",
                value=f"Role ID: {role_id}\nStatus: {status}",
                inline=False
            )
        
        await ctx.send(embed=embed)

    @military_ranks_group.command(name="clear")
    async def clear_military_ranks(self, ctx: commands.Context):
        """Clears all configured military ranks."""
        confirm_view = ConfirmView(ctx.author)
        await ctx.send("Are you sure you want to clear all military ranks? This cannot be undone.", view=confirm_view)
        await confirm_view.wait()
        
        if confirm_view.result:
            await self.config.guild(ctx.guild).military_ranks.set([])
            await ctx.send("All military ranks have been cleared.")
        else:
            await ctx.send("Operation cancelled.")

    @activityset.command(name="settings")
    async def show_settings(self, ctx: commands.Context):
        """Shows the current ActivityTracker settings."""
        settings = await self.config.guild(ctx.guild).all()
        
        embed = discord.Embed(
            title="ActivityTracker Settings",
            color=discord.Color.blue()
        )
        
        # API Settings
        api_url = settings.get("api_url")
        api_key = settings.get("api_key")
        web_api_key = settings.get("web_api_key") # Added for display
        promotion_url = settings.get("promotion_update_url")
        
        embed.add_field(
            name="API Configuration",
            value=(
                f"Django API URL (Outbound): `{api_url or 'Not set'}`\n"
                f"Django API Key (Outbound): `{'✓ Set' if api_key else '✗ Not set'}`\n"
                f"Web API Key (Inbound): `{'✓ Set' if web_api_key else '✗ Not set'}`\n" # Added
                f"Promotion URL: `{promotion_url or 'Not set'}`"
            ),
            inline=False
        )
        
        # Role Settings
        recruit_role_id = settings.get("recruit_role_id")
        member_role_id = settings.get("member_role_id")
        recruit_role = ctx.guild.get_role(recruit_role_id) if recruit_role_id else None
        member_role = ctx.guild.get_role(member_role_id) if member_role_id else None
        
        embed.add_field(
            name="Role Configuration",
            value=(
                f"Recruit Role: {recruit_role.mention if recruit_role else '`Not set`'}\n"
                f"Member Role: {member_role.mention if member_role else '`Not set`'}\n"
                f"Promotion Threshold: `{settings.get('promotion_threshold_hours')} hours`"
            ),
            inline=False
        )
        
        # Notification Settings
        channel_id = settings.get("promotion_channel_id")
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        
        embed.add_field(
            name="Notification Settings",
            value=f"Promotion Channel: {channel.mention if channel else '`Not set`'}",
            inline=False
        )
        
        # Military Ranks Summary
        military_ranks = settings.get("military_ranks", [])
        valid_ranks = [r for r in military_ranks if 'discord_role_id' in r and ctx.guild.get_role(int(r['discord_role_id']))]
        
        embed.add_field(
            name="Military Ranks",
            value=(
                f"Total Configured: `{len(military_ranks)}`\n"
                f"Valid Ranks: `{len(valid_ranks)}`\n"
                f"Use `{ctx.prefix}activityset militaryranks list` for details"
            ),
            inline=False
        )
        
        await ctx.send(embed=embed)

    @commands.command(name="status")
    @commands.guild_only()
    @app_commands.describe(member="The member whose voice activity status to check (defaults to you).")
    async def check_status(self, ctx: commands.Context, member: discord.Member = None):
        """
        Check your voice activity status or another member's status.
        Shows total voice minutes and progress toward promotions.
        """
        target = member or ctx.author
        
        # Get the total minutes from Django
        total_minutes = await self._get_total_minutes_from_django(ctx.guild, target.id)
        
        if total_minutes is None:
            return await ctx.send("❌ Unable to fetch activity data. The website may be down or not properly configured, or the API key is invalid.")
        
        embed = discord.Embed(
            title=f"Activity Status for {target.display_name}",
            color=target.color
        )
        
        # Add user avatar
        embed.set_thumbnail(url=target.display_avatar.url)
        
        # Basic stats
        total_hours = total_minutes / 60
        embed.add_field(
            name="Voice Activity",
            value=f"**{total_hours:.1f}** hours ({total_minutes} minutes)",
            inline=False
        )
        
        # Check Recruit -> Member status
        settings = await self.config.guild(ctx.guild).all()
        recruit_role_id = settings.get("recruit_role_id")
        member_role_id = settings.get("member_role_id")
        threshold_hours = settings.get("promotion_threshold_hours", 0)
        
        if recruit_role_id and member_role_id and threshold_hours > 0:
            recruit_role = ctx.guild.get_role(recruit_role_id)
            member_role = ctx.guild.get_role(member_role_id)
            
            if recruit_role and member_role:
                if member_role in target.roles:
                    embed.add_field(
                        name="Membership Status",
                        value=f"✅ Full Member ({member_role.mention})",
                        inline=False
                    )
                elif recruit_role in target.roles:
                    threshold_minutes = threshold_hours * 60
                    progress = min(100, (total_minutes / threshold_minutes) * 100)
                    remaining_minutes = max(0, threshold_minutes - total_minutes)
                    remaining_hours = remaining_minutes / 60
                    
                    progress_bar = self._generate_progress_bar(progress)
                    
                    embed.add_field(
                        name="Membership Progress",
                        value=(
                            f"{recruit_role.mention} → {member_role.mention}\n"
                            f"{progress_bar} **{progress:.1f}%**\n"
                            f"Remaining: **{remaining_hours:.1f}** hours"
                        ),
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="Membership Status",
                        value="Not in membership track (missing Recruit role)",
                        inline=False
                    )
        
        # Check Military Rank status
        military_ranks = settings.get("military_ranks", [])
        if military_ranks:
            try:
                sorted_ranks = sorted(
                    [r for r in military_ranks if isinstance(r.get('required_hours'), (int, float))],
                    key=lambda x: x['required_hours']
                )
                
                # Find current and next rank
                current_rank_data = None
                next_rank_data = None
                
                # Find the highest rank the user currently HAS the Discord role for
                highest_current_rank_role = None
                for role in target.roles:
                    for r_data in sorted_ranks:
                        if str(role.id) == str(r_data.get('discord_role_id')):
                            if highest_current_rank_role is None or r_data['required_hours'] > highest_current_rank_role['required_hours']:
                                highest_current_rank_role = r_data
                
                # Now, find the highest rank data based on total_minutes
                # This ensures we suggest the correct next promotion based on actual earned time
                earned_rank_by_time = None
                for r_data in reversed(sorted_ranks): # Iterate from highest hours down
                    if total_hours >= r_data['required_hours']:
                        earned_rank_by_time = r_data
                        break
                        
                # If the user has a role for a rank, but they don't yet qualify by time for it,
                # their 'current rank' for display purposes should be the highest one they *qualify* for by time.
                # However, the question here is about what they *currently have* vs what they *can get*.
                # Let's adjust current_rank_data to be the highest one they *qualify* for based on their hours.
                current_rank_data = None
                for r_data in reversed(sorted_ranks):
                    if total_hours >= r_data['required_hours']:
                        current_rank_data = r_data
                        break

                # Find the next rank
                if current_rank_data:
                    # Find the next rank in the sorted list after the current_rank_data
                    for i, r_data in enumerate(sorted_ranks):
                        if r_data['required_hours'] > current_rank_data['required_hours']:
                            next_rank_data = r_data
                            break
                elif sorted_ranks: # If no current rank, the next is the very first one
                    next_rank_data = sorted_ranks[0]
                
                # Display current rank
                if current_rank_data:
                    current_role_id = current_rank_data.get('discord_role_id')
                    current_role = ctx.guild.get_role(int(current_role_id)) if current_role_id else None
                    
                    embed.add_field(
                        name="Current Military Rank (by time)",
                        value=(
                            f"**{current_rank_data.get('name')}**\n"
                            f"{current_role.mention if current_role else 'Role not found'}\n"
                            f"Required: {current_rank_data.get('required_hours')} hours"
                        ),
                        inline=False
                    )
                
                # Display next rank and progress
                if next_rank_data:
                    next_role_id = next_rank_data.get('discord_role_id')
                    next_role = ctx.guild.get_role(int(next_role_id)) if next_role_id else None
                    
                    current_hours_for_progress_base = current_rank_data.get('required_hours', 0) if current_rank_data else 0
                    next_hours = next_rank_data.get('required_hours', 0)
                    
                    if next_hours > current_hours_for_progress_base: # Ensure valid range for percentage
                        progress = min(100, ((total_hours - current_hours_for_progress_base) / (next_hours - current_hours_for_progress_base)) * 100)
                        remaining_hours = max(0, next_hours - total_hours)
                        
                        progress_bar = self._generate_progress_bar(progress)
                        
                        embed.add_field(
                            name="Next Military Rank",
                            value=(
                                f"**{next_rank_data.get('name')}**\n"
                                f"{next_role.mention if next_role else 'Role not found'}\n"
                                f"{progress_bar} **{progress:.1f}%**\n"
                                f"Remaining: **{remaining_hours:.1f}** hours"
                            ),
                            inline=False
                        )
                    else: # This case means next_rank_data's hours are not strictly greater, which shouldn't happen with proper sorting
                        embed.add_field(
                            name="Next Military Rank",
                            value="Error: Next rank hours issue.", # Should be rare with correct sorting
                            inline=False
                        )
                elif current_rank_data: # If no next rank and current_rank_data exists, they are at max
                    embed.add_field(
                        name="Next Military Rank",
                        value="You have reached the highest rank! 🎖️",
                        inline=False
                    )
                else: # No military ranks configured or eligible
                    embed.add_field(
                        name="Military Rank",
                        value="No military ranks configured or eligible yet.",
                        inline=False
                    )
                        
            except Exception as e:
                log.exception(f"Error in check_status military rank display for {target.id}: {e}")
                embed.add_field(
                    name="Military Rank Error",
                    value=f"An error occurred processing military ranks: {str(e)}",
                    inline=False
                )
        else: # No military ranks are configured at all
            embed.add_field(
                name="Military Rank",
                value="Military rank system not configured.",
                inline=False
            )
        
        await ctx.send(embed=embed)


    def _generate_progress_bar(self, percent, length=10):
        """Generate a text-based progress bar."""
        filled_length = int(length * percent / 100)
        bar = '█' * filled_length + '░' * (length - filled_length)
        return f"[{bar}]"

    @activityset.command(name="runcheck")
    @commands.admin_or_permissions(administrator=True)
    async def run_role_check(self, ctx: commands.Context):
        """
        Manually trigger a full role check for all members.
        This will check and update roles based on activity time.
        """
        await ctx.send("Starting full role check for all members. This may take some time...")
        
        # Create a task to run the check
        self.bot.loop.create_task(self._periodic_role_check(ctx.guild.id))
        
        await ctx.send("Role check has been initiated. Results will be logged and role changes will be made automatically.")

# This is the crucial part for Redbot to load your cog
async def setup(bot):
    """Load the ActivityTracker cog."""
    cog = ActivityTracker(bot)
    await bot.add_cog(cog)