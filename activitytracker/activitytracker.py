import discord
import asyncio
import aiohttp
import os
import json
from datetime import datetime

from redbot.core import commands, Config
from aiohttp import web
from redbot.core.utils.chat_formatting import humanize_list
from redbot.core.utils.views import ConfirmView

import logging

log = logging.getLogger("red.activitytracker")

class ActivityTracker(commands.Cog):
    """
    Tracks user voice activity, handles Discord role promotions (Recruit/Member, Military Ranks),
    and exposes an API for a Django website to query member initial role assignment and military rank definitions.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        default_guild = {
            "user_activity": {},  # user_id: total_minutes
            "recruit_role_id": None,
            "member_role_id": None,
            "promotion_threshold_hours": 24.0,
            "military_ranks": [],  # list of dicts: {name, discord_role_id, required_hours}
            "api_url": None,
            "api_key": None,
            "promotion_update_url": None,
            "military_rank_update_url": None,  # New URL for military rank updates
            "promotion_channel_id": None,
        }
        self.config.register_guild(**default_guild)
        self.voice_tracking = {}  # guild_id: {user_id: join_time}
        self.session = aiohttp.ClientSession()

        # Web API
        self.web_app = web.Application()
        self.web_runner = None
        self.web_site = None
        self.web_app.router.add_post("/api/assign_initial_role", self.assign_initial_role_handler)
        self.web_app.router.add_get("/api/get_military_ranks", self.get_military_ranks_handler)
        self.web_app.router.add_get("/health", self.health_check_handler)
        # Add the new endpoint for getting all activity data
        self.web_app.router.add_get("/api/get_all_activity", self.get_all_activity_handler)
        self.bot.loop.create_task(self.initialize_webserver())

    async def initialize_webserver(self):
        await self.bot.wait_until_ready()
        guild_id_str = os.environ.get("DISCORD_GUILD_ID")
        if not guild_id_str:
            log.critical("DISCORD_GUILD_ID environment variable not set. Web API will not function.")
            return
        guild = self.bot.get_guild(int(guild_id_str))
        if not guild:
            log.critical(f"Guild with ID {guild_id_str} not found. Web API will not function.")
            return
        self.web_app["guild"] = guild
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

    def cog_unload(self):
        if self.web_runner:
            asyncio.create_task(self._shutdown_web_server())
        asyncio.create_task(self.session.close())
        # Save voice time for users still in voice channels
        for guild_id, members_tracking in self.voice_tracking.items():
            guild = self.bot.get_guild(guild_id)
            if guild:
                for member_id, join_time in members_tracking.items():
                    member = guild.get_member(member_id)
                    if member:
                        duration_minutes = (datetime.utcnow() - join_time).total_seconds() / 60
                        if duration_minutes >= 1:
                            log.info(f"Unloading: Logging {duration_minutes:.2f} minutes for {member.name} due to cog unload.")
                            asyncio.create_task(self._update_user_voice_minutes(guild, member, int(duration_minutes)))
        self.voice_tracking.clear()

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
        guild = request.app["guild"]
        expected_key = await self.config.guild(guild).api_key()
        if not expected_key:
            raise web.HTTPUnauthorized(reason="Web API Key not configured on RedBot for this guild.")
        provided_key = request.headers.get("X-API-Key")
        if not provided_key:
            raise web.HTTPUnauthorized(reason="X-API-Key header missing.")
        if provided_key != expected_key:
            raise web.HTTPForbidden(reason="Invalid API Key.")
        return True

    async def health_check_handler(self, request: web.Request):
        return web.Response(text="OK", status=200)

    async def assign_initial_role_handler(self, request):
        try:
            await self._authenticate_web_request(request)
        except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
            return e
        try:
            data = await request.json()
            discord_id = int(data.get("discord_id"))
        except (ValueError, TypeError, json.JSONDecodeError):
            return web.Response(text="Invalid request data", status=400)
        guild = request.app["guild"]
        recruit_role_id = await self.config.guild(guild).recruit_role_id()
        if not recruit_role_id:
            return web.Response(text="Recruit role not configured", status=500)
        member = guild.get_member(discord_id)
        recruit_role = guild.get_role(recruit_role_id)
        if member and recruit_role:
            try:
                if recruit_role not in member.roles:
                    await member.add_roles(recruit_role, reason="Initial role assignment from website.")
                return web.Response(text="Role assigned/already present successfully", status=200)
            except discord.Forbidden:
                return web.Response(text="Missing permissions", status=503)
            except Exception:
                return web.Response(text="Internal server error", status=500)
        else:
            return web.Response(text="Member or role not found", status=404)

    async def get_military_ranks_handler(self, request):
        try:
            await self._authenticate_web_request(request)
        except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
            return e
        guild = request.app["guild"]
        military_ranks = await self.config.guild(guild).military_ranks()
        if not military_ranks:
            return web.json_response([], status=200)
        try:
            sorted_ranks = sorted(
                [r for r in military_ranks if 'required_hours' in r and isinstance(r['required_hours'], (int, float))],
                key=lambda x: x['required_hours']
            )
        except Exception:
            return web.Response(text="Internal Server Error: Malformed rank data", status=500)
        return web.json_response(sorted_ranks)

    async def get_all_activity_handler(self, request):
        """API endpoint to get all user activity data."""
        try:
            await self._authenticate_web_request(request)
        except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
            return e
        
        guild_id_str = os.environ.get("DISCORD_GUILD_ID")
        if not guild_id_str:
            return web.HTTPInternalServerError(reason="DISCORD_GUILD_ID not set")
        
        guild = self.bot.get_guild(int(guild_id_str))
        if not guild:
            return web.HTTPInternalServerError(reason="Guild not found")
        
        # Get all user activity data from the bot's config
        user_activity = await self.config.guild(guild).user_activity()
        
        # Format the data for the response
        activity_data = []
        for user_id, minutes in user_activity.items():
            # Add current session time if user is in voice
            total_minutes = minutes
            if guild.id in self.voice_tracking and int(user_id) in self.voice_tracking[guild.id]:
                join_time = self.voice_tracking[guild.id][int(user_id)]
                current_session_minutes = int((datetime.utcnow() - join_time).total_seconds() / 60)
                if current_session_minutes >= 1:
                    total_minutes += current_session_minutes
            
            activity_data.append({
                "discord_id": user_id,
                "minutes": total_minutes
            })
        
        return web.json_response(activity_data)

    # --- VOICE TRACKING ---

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        
        log.info(f"Voice state update for {member.name} ({member.id})")
        log.info(f"Before channel: {before.channel}, After channel: {after.channel}")
        
        guild = member.guild
        guild_id = guild.id
        user_id = member.id
        
        # Initialize guild in tracking dict if needed
        if guild_id not in self.voice_tracking:
            log.info(f"Initializing voice tracking for guild {guild_id}")
            self.voice_tracking[guild_id] = {}
        
        # User joined a voice channel
        if before.channel is None and after.channel is not None:
            log.info(f"{member.name} joined voice channel {after.channel.name}")
            self.voice_tracking[guild_id][user_id] = datetime.utcnow()
        
        # User left a voice channel
        elif before.channel is not None and after.channel is None:
            log.info(f"{member.name} left voice channel {before.channel.name}")
            if user_id in self.voice_tracking[guild_id]:
                join_time = self.voice_tracking[guild_id][user_id]
                duration = datetime.utcnow() - join_time
                minutes = duration.total_seconds() / 60
                
                log.info(f"{member.name} was in voice for {minutes:.2f} minutes")
                
                if minutes >= 1:  # Only count if at least 1 minute
                    log.info(f"Updating activity for {member.name}: {minutes:.2f} minutes")
                    await self._update_user_voice_minutes(guild, member, int(minutes))
                else:
                    log.info(f"Duration too short ({minutes:.2f}m). Skipping sync.")
                
                del self.voice_tracking[guild_id][user_id]
            else:
                log.warning(f"{member.name} left voice but wasn't being tracked")

    async def _update_user_voice_minutes(self, guild, member, minutes_to_add):
        async with self.config.guild(guild).user_activity() as user_activity:
            uid = str(member.id)
            user_activity[uid] = user_activity.get(uid, 0) + minutes_to_add
            log.info(f"Updated voice minutes for {member.name}: added {minutes_to_add}, new total: {user_activity[uid]}")
        
        # After updating internal tracking, send to Django
        asyncio.create_task(self._update_website_activity(guild, member, minutes_to_add))
        
        # Check for promotion based on updated minutes
        total_minutes = await self._get_user_voice_minutes(guild, member.id)
        await self._check_for_promotion(guild, member, total_minutes)

    async def _get_user_voice_minutes(self, guild, user_id):
        user_activity = await self.config.guild(guild).user_activity()
        total_minutes = user_activity.get(str(user_id), 0)
        guild_id = guild.id
        if guild_id in self.voice_tracking and user_id in self.voice_tracking[guild_id]:
            join_time = self.voice_tracking[guild_id][user_id]
            current_session_minutes = int((datetime.utcnow() - join_time).total_seconds() / 60)
            if current_session_minutes >= 1:
                total_minutes += current_session_minutes
                log.debug(f"Added {current_session_minutes} minutes from current session for user {user_id}")
        return total_minutes

    # --- DJANGO SYNC ---

    async def _update_website_activity(self, guild, member, minutes_to_add):
        """Sends activity updates to the Django website."""
        guild_settings = await self.config.guild(guild).all()
        api_url = guild_settings.get("api_url")
        api_key = guild_settings.get("api_key")
        
        log.info(f"Attempting to update website activity for {member.name} ({member.id}): {minutes_to_add} minutes")
        log.info(f"API URL: {api_url}, API Key set: {'Yes' if api_key else 'No'}")
        
        if not api_url or not api_key: 
            log.warning(f"Django API URL or Key not configured for guild {guild.id}. Skipping activity update for {member.name}.")
            return
        
        endpoint = f"{api_url}/api/update_activity/"
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {"discord_id": str(member.id), "voice_minutes": minutes_to_add}
        
        log.info(f"Sending request to {endpoint} with payload: {payload}")
        
        try:
            async with self.session.post(endpoint, headers=headers, json=payload, timeout=10) as resp:
                log.info(f"API response status: {resp.status}")
                
                if resp.status == 200:
                    response_data = await resp.json()
                    log.info(f"API response data: {response_data}")
                    log.info(f"Successfully synced {minutes_to_add} minutes for user {member.id}.")
                else:
                    error_text = await resp.text()
                    log.error(f"Failed to update activity for {member.id}: {resp.status} - {error_text}")
        except Exception as e:
            log.error(f"Exception updating website activity for {member.id}: {str(e)}")

    async def _notify_website_of_promotion(self, guild, discord_id, new_role_name):
        """Notify the website of a community role promotion."""
        promotion_update_url = await self.config.guild(guild).promotion_update_url()
        api_key = await self.config.guild(guild).api_key()
        
        log.info(f"Notifying website of community role promotion for {discord_id} to {new_role_name}")
        
        if not promotion_update_url or not api_key: 
            log.warning(f"Promotion update URL or API Key not configured for guild {guild.id}. Skipping promotion notification for {discord_id}.")
            return
        
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {"discord_id": str(discord_id), "new_role": new_role_name}
        
        log.info(f"Sending request to {promotion_update_url} with payload: {payload}")
        
        try:
            async with self.session.post(promotion_update_url, headers=headers, json=payload, timeout=5) as resp:
                log.info(f"API response status: {resp.status}")
                
                if resp.status == 200:
                    response_data = await resp.json()
                    log.info(f"API response data: {response_data}")
                    log.info(f"Successfully notified website of community role promotion for {discord_id} to {new_role_name}.")
                else:
                    error_text = await resp.text()
                    log.error(f"Failed to notify website of community role promotion for {discord_id} to {new_role_name}: {resp.status} - {error_text}")
        except Exception as e:
            log.error(f"Exception notifying website of community role promotion for {discord_id}: {str(e)}")

    async def _notify_website_of_military_rank(self, guild, discord_id, rank_name):
        """Notify the website of a military rank update."""
        # First try to use the dedicated military rank URL if set
        military_rank_update_url = await self.config.guild(guild).military_rank_update_url()
        api_url = await self.config.guild(guild).api_url()
        api_key = await self.config.guild(guild).api_key()
        
        log.info(f"Notifying website of military rank update for {discord_id} to {rank_name}")
        
        if not api_key: 
            log.warning(f"API Key not configured for guild {guild.id}. Skipping military rank update for {discord_id}.")
            return
        
        # If dedicated URL is set, use it, otherwise construct from api_url
        if military_rank_update_url:
            endpoint = military_rank_update_url
        elif api_url:
            endpoint = f"{api_url}/api/update_military_rank/"
        else:
            log.warning(f"Neither military_rank_update_url nor api_url configured for guild {guild.id}. Skipping military rank update.")
            return
        
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {"discord_id": str(discord_id), "rank_name": rank_name}
        
        log.info(f"Sending military rank update to {endpoint} with payload: {payload}")
        
        try:
            async with self.session.post(endpoint, headers=headers, json=payload, timeout=5) as resp:
                log.info(f"API response status: {resp.status}")
                
                if resp.status == 200:
                    response_data = await resp.json()
                    log.info(f"API response data: {response_data}")
                    log.info(f"Successfully updated military rank for {discord_id} to {rank_name}")
                else:
                    error_text = await resp.text()
                    log.error(f"Failed to update military rank: {resp.status} - {error_text}")
        except Exception as e:
            log.error(f"Exception updating military rank: {str(e)}")

    # --- PERIODIC TASKS ---

    @commands.Cog.listener()
    async def on_ready(self):
        """Sets up periodic tasks when the bot is ready."""
        log.info("ActivityTracker is ready and setting up periodic tasks.")
        await self._setup_periodic_tasks()

    async def _setup_periodic_tasks(self):
        """Sets up periodic tasks for role checking and activity updates."""
        guild_id_str = os.environ.get("DISCORD_GUILD_ID")
        if not guild_id_str:
            log.error("DISCORD_GUILD_ID environment variable not set. Periodic tasks will not be scheduled.")
            return

        guild_id = int(guild_id_str)
        guild = self.bot.get_guild(guild_id)
        if not guild:
            log.error(f"Guild with ID {guild_id} not found. Periodic tasks will not be scheduled.")
            return

        # Schedule the periodic role check every 24 hours
        self.bot.loop.create_task(self._schedule_periodic_role_check(guild_id))
        
        # Schedule the periodic activity update every 5 minutes
        self.bot.loop.create_task(self._schedule_periodic_activity_updates(guild_id))

    async def _schedule_periodic_role_check(self, guild_id: int):
        """Schedules the periodic role check to run every 24 hours."""
        while self == self.bot.get_cog("ActivityTracker"):  # Run while cog is loaded
            try:
                log.info(f"Running scheduled role check for guild ID: {guild_id}")
                await self._periodic_role_check(guild_id)
                log.info(f"Completed scheduled role check for guild ID: {guild_id}")
            except Exception as e:
                log.exception(f"An error occurred during the scheduled role check: {e}")
            await asyncio.sleep(86400)  # Sleep for 24 hours

    async def _schedule_periodic_activity_updates(self, guild_id: int):
        """Schedules periodic updates of activity for users currently in voice channels."""
        while self == self.bot.get_cog("ActivityTracker"):  # Run while cog is loaded
            try:
                log.info(f"Running periodic activity update for guild ID: {guild_id}")
                await self._update_active_voice_users(guild_id)
                log.info(f"Completed periodic activity update for guild ID: {guild_id}")
            except Exception as e:
                log.exception(f"An error occurred during the periodic activity update: {e}")
            await asyncio.sleep(300)  # Sleep for 5 minutes

    async def _update_active_voice_users(self, guild_id: int):
        """Updates activity for users currently in voice channels."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            log.error(f"Guild with ID {guild_id} not found for periodic activity update.")
            return
        
        updates_sent = 0
        
        # Check each voice channel in the guild
        for voice_channel in guild.voice_channels:
            for member in voice_channel.members:
                if member.bot:
                    continue  # Skip bots
                    
                # Check if we're tracking this user
                if guild_id in self.voice_tracking and member.id in self.voice_tracking[guild_id]:
                    join_time = self.voice_tracking[guild_id][member.id]
                    current_time = datetime.utcnow()
                    
                    # Calculate minutes since last update or join
                    minutes_since_join = int((current_time - join_time).total_seconds() / 60)
                    
                    if minutes_since_join >= 5:  # Only update if at least 5 minutes have passed
                        log.info(f"Periodic update: {member.name} has been in voice for {minutes_since_join} minutes")
                        
                        # Update the user's activity
                        await self._update_user_voice_minutes(guild, member, minutes_since_join)
                        
                        # Reset the join time to now (to avoid double-counting)
                        self.voice_tracking[guild_id][member.id] = current_time
                        
                        updates_sent += 1
        
        log.info(f"Periodic activity update complete. Sent {updates_sent} updates.")

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
            async for member in guild.fetch_members(limit=None): # Fetch all members
                if member.bot:
                    continue # Skip bots
                
                members_checked += 1
                total_minutes = await self._get_user_voice_minutes(guild, member.id)

                # _check_for_promotion handles both Recruit->Member and Military Ranks
                initial_roles = {r.id for r in member.roles}
                await self._check_for_promotion(guild, member, total_minutes)
                final_roles = {r.id for r in member.roles}

                if initial_roles != final_roles:
                    promotions_made += 1
                    log.info(f"Role change detected for {member.name} ({member.id}) during periodic check.")
                
                await asyncio.sleep(0.1) # Small delay to avoid hitting Discord/API rate limits too hard

        except discord.Forbidden:
            log.error(f"Bot lacks permissions to fetch members in guild {guild.id} for periodic check.")
        except Exception as e:
            log.exception(f"An unexpected error occurred during periodic role check for guild {guild.id}: {e}")

        log.info(f"Periodic role check complete for guild {guild.id}. Checked {members_checked} members, made {promotions_made} role changes.")

    # --- PROMOTION LOGIC ---

    async def _check_for_promotion(self, guild, member, total_minutes):
        # Recruit -> Member
        recruit_role_id = await self.config.guild(guild).recruit_role_id()
        member_role_id = await self.config.guild(guild).member_role_id()
        threshold_hours = await self.config.guild(guild).promotion_threshold_hours()
        if recruit_role_id and member_role_id and threshold_hours:
            recruit_role = guild.get_role(recruit_role_id)
            member_role = guild.get_role(member_role_id)
            if recruit_role and member_role and recruit_role in member.roles:
                if total_minutes >= threshold_hours * 60:
                    await member.remove_roles(recruit_role, reason="Promotion")
                    await member.add_roles(member_role, reason="Promotion")
                    await self._notify_website_of_promotion(guild, member.id, "member")
                    channel_id = await self.config.guild(guild).promotion_channel_id()
                    if channel_id:
                        channel = guild.get_channel(channel_id)
                        if channel:
                            await channel.send(
                                f"🎉 Congratulations {member.mention}! You've been promoted to **{member_role.name}** status!"
                            )
        # Military Ranks
        military_ranks = await self.config.guild(guild).military_ranks()
        if not military_ranks:
            return
        sorted_ranks = sorted(
            [r for r in military_ranks if isinstance(r.get('required_hours'), (int, float))],
            key=lambda x: x['required_hours'],
            reverse=True
        )
        user_hours = total_minutes / 60
        for rank in sorted_ranks:
            if user_hours >= rank['required_hours']:
                role = guild.get_role(int(rank['discord_role_id']))
                if role and role not in member.roles:
                    all_rank_ids = [int(r['discord_role_id']) for r in military_ranks if 'discord_role_id' in r]
                    remove_roles = [r for r in member.roles if r.id in all_rank_ids]
                    await member.remove_roles(*remove_roles, reason="Rank promotion")
                    await member.add_roles(role, reason="Rank promotion")
                    
                    # Update both systems separately
                    # 1. Update community role (if not already a member)
                    if member_role_id:
                        member_role = guild.get_role(member_role_id)
                        if member_role and member_role not in member.roles:
                            await self._notify_website_of_promotion(guild, member.id, "member")
                    
                    # 2. Update military rank
                    await self._notify_website_of_military_rank(guild, member.id, rank['name'])
                    
                    channel_id = await self.config.guild(guild).promotion_channel_id()
                    if channel_id:
                        channel = guild.get_channel(channel_id)
                        if channel:
                            await channel.send(
                                f"🎖️ Bravo, {member.mention}! You've achieved the rank of **{rank['name']}**!"
                            )
                break

    # --- COMMANDS ---

    @commands.command()
    async def myvoicetime(self, ctx):
        """Shows your total accumulated voice time."""
        total_minutes = await self._get_user_voice_minutes(ctx.guild, ctx.author.id)
        hours = total_minutes // 60
        minutes = total_minutes % 60
        await ctx.send(f"Your total voice time is {hours} hours and {minutes} minutes.")

    @commands.command(name="status")
    async def status(self, ctx, member: discord.Member = None):
        """Show your (or another's) voice time and promotion progress."""
        target = member or ctx.author
        total_minutes = await self._get_user_voice_minutes(ctx.guild, target.id)
        hours = total_minutes // 60
        minutes = total_minutes % 60
        embed = discord.Embed(
            title=f"Activity Status for {target.display_name}",
            color=target.color
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(
            name="Voice Activity",
            value=f"**{hours}** hours and **{minutes}** minutes",
            inline=False
        )
        # Membership progress
        recruit_role_id = await self.config.guild(ctx.guild).recruit_role_id()
        member_role_id = await self.config.guild(ctx.guild).member_role_id()
        threshold_hours = await self.config.guild(ctx.guild).promotion_threshold_hours()
        if recruit_role_id and member_role_id and threshold_hours:
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
                        name="Membership Status",
                        value="Not in membership track (missing Recruit role)",
                        inline=False
                    )
        # Military Rank progress
        military_ranks = await self.config.guild(ctx.guild).military_ranks()
        if military_ranks:
            try:
                sorted_ranks = sorted(
                    [r for r in military_ranks if isinstance(r.get('required_hours'), (int, float))],
                    key=lambda x: x['required_hours']
                )
                current_rank = None
                next_rank = None
                user_rank_ids = {
                    role.id for role in target.roles
                    if any(str(role.id) == str(r.get('discord_role_id')) for r in military_ranks)
                }
                if user_rank_ids:
                    user_ranks = [r for r in sorted_ranks if str(r.get('discord_role_id')) in map(str, user_rank_ids)]
                    if user_ranks:
                        current_rank = max(user_ranks, key=lambda x: x['required_hours'])
                if current_rank:
                    higher_ranks = [r for r in sorted_ranks if r['required_hours'] > current_rank['required_hours']]
                    if higher_ranks:
                        next_rank = min(higher_ranks, key=lambda x: x['required_hours'])
                else:
                    if sorted_ranks:
                        next_rank = sorted_ranks[0]
                if current_rank:
                    current_role_id = current_rank.get('discord_role_id')
                    current_role = ctx.guild.get_role(int(current_role_id)) if current_role_id else None
                    embed.add_field(
                        name="Current Military Rank",
                        value=(
                            f"**{current_rank.get('name')}**\n"
                            f"{current_role.mention if current_role else 'Role not found'}\n"
                            f"Required: {current_rank.get('required_hours')} hours"
                        ),
                        inline=False
                    )
                if next_rank:
                    next_role_id = next_rank.get('discord_role_id')
                    next_role = ctx.guild.get_role(int(next_role_id)) if next_role_id else None
                    current_hours = current_rank.get('required_hours', 0) if current_rank else 0
                    next_hours = next_rank.get('required_hours', 0)
                    if next_hours > current_hours:
                        progress = min(100, ((hours - current_hours) / (next_hours - current_hours)) * 100)
                        remaining_hours = max(0, next_hours - hours)
                        progress_bar = self._generate_progress_bar(progress)
                        embed.add_field(
                            name="Next Military Rank",
                            value=(
                                f"**{next_rank.get('name')}**\n"
                                f"{next_role.mention if next_role else 'Role not found'}\n"
                                f"{progress_bar} **{progress:.1f}%**\n"
                                f"Remaining: **{remaining_hours:.1f}** hours"
                            ),
                            inline=False
                        )
                    else:
                        embed.add_field(
                            name="Next Military Rank",
                            value="You have reached the highest rank! 🎖️",
                            inline=False
                        )
                elif current_rank:
                    embed.add_field(
                        name="Next Military Rank",
                        value="You have reached the highest rank! 🎖️",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="Military Rank",
                        value="No military ranks configured or eligible",
                        inline=False
                    )
            except Exception as e:
                embed.add_field(
                    name="Military Rank Error",
                    value=f"An error occurred processing military ranks: {str(e)}",
                    inline=False
                )
        await ctx.send(embed=embed)

    def _generate_progress_bar(self, percent, length=10):
        filled_length = int(length * percent / 100)
        bar = '█' * filled_length + '░' * (length - filled_length)
        return f"[{bar}]"

    # --- ADMIN/CONFIG COMMANDS ---

    @commands.group(name="activityset")
    @commands.admin_or_permissions(manage_guild=True)
    async def activityset(self, ctx):
        """Manage ActivityTracker settings."""
        pass

    @activityset.command()
    async def roles(self, ctx, recruit: discord.Role, member: discord.Role):
        """Set the Recruit and Member roles."""
        await self.config.guild(ctx.guild).recruit_role_id.set(recruit.id)
        await self.config.guild(ctx.guild).member_role_id.set(member.id)
        await ctx.send("Recruit and Member roles have been set.")

    @activityset.command()
    async def threshold(self, ctx, hours: float):
        """Set the voice hours required to be promoted from Recruit to Member."""
        await self.config.guild(ctx.guild).promotion_threshold_hours.set(hours)
        await ctx.send(f"Promotion threshold set to {hours} hours.")

    @activityset.command()
    async def api(self, ctx, url: str, key: str):
        """Set the base API URL and the API Key for the website."""
        await self.config.guild(ctx.guild).api_url.set(url)
        await self.config.guild(ctx.guild).api_key.set(key)
        await ctx.send("API URL and Key have been saved.")

    @activityset.command()
    async def promotionurl(self, ctx, url: str):
        """Set the full URL for community role promotions."""
        await self.config.guild(ctx.guild).promotion_update_url.set(url)
        await ctx.send("Community role promotion URL set.")

    @activityset.command()
    async def militaryrankurl(self, ctx, url: str):
        """Set the full URL for military rank updates."""
        await self.config.guild(ctx.guild).military_rank_update_url.set(url)
        await ctx.send("Military rank update URL set.")

    @activityset.command()
    async def promotionchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel for promotion notifications."""
        await self.config.guild(ctx.guild).promotion_channel_id.set(channel.id)
        await ctx.send(f"Promotion notification channel set to {channel.mention}.")

    @activityset.group()
    async def militaryranks(self, ctx):
        """Manage military ranks."""
        pass

    @militaryranks.command(name="add")
    async def add_rank(self, ctx, role: discord.Role, required_hours: float):
        """Add a new military rank."""
        async with self.config.guild(ctx.guild).military_ranks() as ranks:
            ranks.append({
                "name": role.name,
                "discord_role_id": str(role.id),
                "required_hours": required_hours
            })
        await ctx.send(f"Added military rank: {role.name} at {required_hours} hours.")

    @militaryranks.command(name="clear")
    async def clear_ranks(self, ctx):
        """Clear all configured military ranks."""
        await self.config.guild(ctx.guild).military_ranks.set([])
        await ctx.send("All military ranks have been cleared.")

    @militaryranks.command(name="list")
    async def list_ranks(self, ctx):
        """List all configured military ranks."""
        ranks = await self.config.guild(ctx.guild).military_ranks()
        if not ranks:
            await ctx.send("No military ranks have been set.")
            return
        msg = "**Configured Military Ranks:**\n"
        sorted_ranks = sorted(ranks, key=lambda r: r['required_hours'])
        for r in sorted_ranks:
            msg += f"- **{r['name']}**: {r['required_hours']} hours (Role ID: {r['discord_role_id']})\n"
        await ctx.send(msg)

    @activityset.command(name="settings")
    async def show_settings(self, ctx):
        """Shows the current ActivityTracker settings."""
        settings = await self.config.guild(ctx.guild).all()
        embed = discord.Embed(
            title="ActivityTracker Settings",
            color=discord.Color.blue()
        )
        api_url = settings.get("api_url")
        api_key = settings.get("api_key")
        promotion_url = settings.get("promotion_update_url")
        military_rank_url = settings.get("military_rank_update_url")
        embed.add_field(
            name="API Configuration",
            value=(
                f"Base API URL: `{api_url or 'Not set'}`\n"
                f"API Key: `{'✓ Set' if api_key else '✗ Not set'}`\n"
                f"Community Role URL: `{promotion_url or 'Not set'}`\n"
                f"Military Rank URL: `{military_rank_url or 'Not set'}`"
            ),
            inline=False
        )
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
        channel_id = settings.get("promotion_channel_id")
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        embed.add_field(
            name="Notification Settings",
            value=f"Promotion Channel: {channel.mention if channel else '`Not set`'}",
            inline=False
        )
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

    @activityset.command(name="syncroles")
    @commands.admin_or_permissions(administrator=True)
    async def sync_roles(self, ctx):
        """Sync all user roles from Discord to the website database."""
        await ctx.send("Starting role sync from Discord to website...")
        
        guild = ctx.guild
        guild_settings = await self.config.guild(guild).all()
        
        # Get community role IDs
        recruit_role_id = guild_settings.get("recruit_role_id")
        member_role_id = guild_settings.get("member_role_id")
        if not recruit_role_id or not member_role_id:
            return await ctx.send("Recruit or Member role not configured.")
        
        recruit_role = guild.get_role(recruit_role_id)
        member_role = guild.get_role(member_role_id)
        if not recruit_role or not member_role:
            return await ctx.send("Recruit or Member role not found in the server.")
            
        # Get all military ranks
        military_ranks = guild_settings.get("military_ranks", [])
        military_role_ids = {int(r['discord_role_id']): r['name'] for r in military_ranks if 'discord_role_id' in r}
        
        synced_community = 0
        synced_military = 0
        
        for member in guild.members:
            if member.bot:
                continue
            
            # Sync Community Role
            if member_role in member.roles:
                await self._notify_website_of_promotion(guild, member.id, "member")
                synced_community += 1
            elif recruit_role in member.roles:
                await self._notify_website_of_promotion(guild, member.id, "recruit")
                synced_community += 1
            
            # Sync Military Rank
            has_military_rank = False
            for role in member.roles:
                if role.id in military_role_ids:
                    rank_name = military_role_ids[role.id]
                    await self._notify_website_of_military_rank(guild, member.id, rank_name)
                    synced_military += 1
                    has_military_rank = True
                    break
            
            # If user has no military rank, we could optionally send a "None" update
            # For now, we'll just skip if they don't have one.
            
            await asyncio.sleep(0.1)
        
        await ctx.send(f"Sync complete. Synced {synced_community} community roles and {synced_military} military ranks.")

    # --- DEBUG/UTILITY ---

    @activityset.command(name="debug")
    @commands.is_owner()
    async def debug_info(self, ctx):
        """Shows debug information about the ActivityTracker cog."""
        embed = discord.Embed(
            title="ActivityTracker Debug Information",
            color=discord.Color.gold()
        )
        web_status = "Running" if self.web_runner and self.web_site else "Not running"
        host = os.environ.get("ACTIVITY_WEB_HOST", "0.0.0.0")
        port = os.environ.get("ACTIVITY_WEB_PORT", "5002")
        embed.add_field(
            name="Web API Server",
            value=f"Status: {web_status}\nHost: {host}\nPort: {port}",
            inline=False
        )
        total_tracked = 0
        for guild_id, members in self.voice_tracking.items():
            total_tracked += len(members)
        embed.add_field(
            name="Voice Tracking",
            value=f"Currently tracking: {total_tracked} users",
            inline=False
        )
        guild_id_env = os.environ.get("DISCORD_GUILD_ID", "Not set")
        embed.add_field(
            name="Environment Variables",
            value=f"DISCORD_GUILD_ID: {guild_id_env}\nACTIVITY_WEB_HOST: {host}\nACTIVITY_WEB_PORT: {port}",
            inline=False
        )
        embed.set_footer(text=f"ActivityTracker Cog | Discord.py {discord.__version__}")
        await ctx.send(embed=embed)

    @activityset.command(name="forcesync")
    @commands.is_owner()
    async def force_sync(self, ctx):
        """Force a sync of all active voice users."""
        guild = ctx.guild
        guild_id = guild.id
        
        updates_sent = 0
        
        await ctx.send("Starting forced sync of all active voice users...")
        
        # Check each voice channel in the guild
        for voice_channel in guild.voice_channels:
            for member in voice_channel.members:
                if member.bot:
                    continue  # Skip bots
                    
                # Check if we're tracking this user
                if guild_id in self.voice_tracking and member.id in self.voice_tracking[guild_id]:
                    join_time = self.voice_tracking[guild_id][member.id]
                    current_time = datetime.utcnow()
                    
                    # Calculate minutes since last update or join
                    minutes_since_join = int((current_time - join_time).total_seconds() / 60)
                    
                    if minutes_since_join >= 1:  # Only update if at least 1 minute has passed
                        await ctx.send(f"Syncing {member.name}: {minutes_since_join} minutes")
                        
                        # Update the user's activity
                        await self._update_user_voice_minutes(guild, member, minutes_since_join)
                        
                        # Reset the join time to now (to avoid double-counting)
                        self.voice_tracking[guild_id][member.id] = current_time
                        
                        updates_sent += 1
        
        await ctx.send(f"Forced sync complete. Sent {updates_sent} updates.")

def setup(bot):
    bot.add_cog(ActivityTracker(bot))
