# /home/elkz/.local/share/Red-DiscordBot/data/zerolivesleft/cogs/CogManager/cogs/gamecounter/gamecounter.py

import discord
import asyncio
import json
import aiohttp
import os
from aiohttp import web
from redbot.core import commands, Config, app_commands
from redbot.core.utils.menus import DEFAULT_CONTROLS 
from redbot.core.utils.chat_formatting import humanize_list 
from redbot.core.utils.views import ConfirmView
from redbot.core.bot import Red
from discord.ext import tasks
import logging

log = logging.getLogger("red.Elkz.gamecounter")

class GameCounter(commands.Cog):
    """
    Periodically counts users with specific Discord roles and sends the data to a Django website API.
    Also serves a read-only API for Discord role members for the website.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        self.config = Config.get_conf(
            self, identifier=123456789012345, force_registration=True
        )
        # Removed web server config - this is now handled by the WebServer cog
        self.config.register_global(
            api_url=None,
            api_key=None,
            interval=15,
            guild_id=None,
            game_role_mappings={},
            activity_data={},
            website_api_url="https://zerolivesleft.net/api/update_role/",
            website_api_key=None
        )
        
        # This task will be started in the on_ready listener
        self.count_and_update.start()
        # This task will run once the bot is ready to register routes
        asyncio.create_task(self.initialize())

    async def initialize(self):
        """Waits for the bot to be ready and then registers API routes with the WebServer cog."""
        await self.bot.wait_until_ready()
        
        webserver_cog = self.bot.get_cog("WebServer")
        if not webserver_cog:
            log.error("WebServer cog not found. GameCounter API endpoints will not be available.")
            return

        # Define the routes this cog will handle
        routes = [
            web.get("/guilds/{guild_id}/roles/{role_id}/members", self.get_role_members_handler),
            web.get("/api/get_time_ranks/", self.get_time_ranks_handler)
        ]
        
        # Register the routes with the central web server
        webserver_cog.add_routes(routes)
        log.info("Successfully registered GameCounter routes with the WebServer cog.")

    def cog_unload(self):
        """Cleanup when the cog is unloaded."""
        if self.count_and_update.is_running():
            self.count_and_update.cancel()
        asyncio.create_task(self.session.close())

    async def red_delete_data_for_user(self, *, requester: str, user_id: int) -> None:
        activity_data = await self.config.activity_data()
        if str(user_id) in activity_data:
            del activity_data[str(user_id)]
            await self.config.activity_data.set(activity_data)
        return

    async def _authenticate_request(self, request: web.Request):
        """Authenticates incoming web API requests using the WebServer cog's API key."""
        webserver_cog = self.bot.get_cog("WebServer")
        if not webserver_cog:
            log.error("WebServer cog not loaded, cannot authenticate request.")
            raise web.HTTPInternalServerError(reason="Authentication service is unavailable.")

        # Fetch the API key from the central WebServer's config
        expected_key = await webserver_cog.config.api_key()
        if not expected_key:
            log.warning("Web API key is not set in the WebServer cog's config.")
            raise web.HTTPUnauthorized(reason="Web API Key not configured on RedBot.")
        
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

    async def get_time_ranks_handler(self, request: web.Request):
        """Handler for the time ranks API endpoint."""
        try:
            await self._authenticate_request(request)
        except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
            log.warning(f"Authentication failed for /api/get_time_ranks/ endpoint: {e.reason}")
            return e
            
        military_ranks = [
            {"name": "Private", "role_id": "1274274605435060224", "minutes_required": 10 * 60},
            # ... (rest of your ranks)
            {"name": "General of the Army", "role_id": "1358213816617275483", "minutes_required": 6000 * 60},
        ]
        
        return web.json_response(military_ranks)

    async def get_role_members_handler(self, request: web.Request):
        """Web API handler to return members of a specific Discord role."""
        try:
            await self._authenticate_request(request)
        except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
            log.warning(f"Authentication failed for /guilds/roles/members endpoint: {e.reason}")
            return e

        guild_id_str = request.match_info.get("guild_id")
        role_id_str = request.match_info.get("role_id")

        if not guild_id_str or not role_id_str:
            raise web.HTTPBadRequest(reason="Missing guild_id or role_id in path.")

        try:
            guild_id = int(guild_id_str)
            role_id = int(role_id_str)
        except ValueError:
            raise web.HTTPBadRequest(reason="Invalid guild_id or role_id format.")

        guild = self.bot.get_guild(guild_id)
        if not guild:
            raise web.HTTPNotFound(reason=f"Guild with ID {guild_id} not found.")

        if not guild.chunked:
            log.debug(f"Chunking guild {guild.id} for API request.")
            try:
                await guild.chunk()
            except Exception as e:
                log.error(f"Error chunking guild {guild.id} for API request: {e}")
                raise web.HTTPInternalServerError(reason="Failed to fetch guild members.")

        role = guild.get_role(role_id)
        if not role:
            raise web.HTTPNotFound(reason=f"Role with ID {role_id} not found in guild {guild.id}.")

        members_with_status = []
        for member in role.members:
            streaming_activity = next((a for a in member.activities if isinstance(a, discord.Streaming)), None)
            is_live = streaming_activity is not None
            twitch_url = streaming_activity.url if is_live else f"https://www.twitch.tv/{member.name}"

            member_data = {
                "id": str(member.id),
                "name": member.name,
                "display_name": member.display_name,
                "avatar_url": str(member.display_avatar.url) if member.display_avatar else None,
                "discriminator": member.discriminator if member.discriminator != "0" else None,
                "is_live": is_live,
                "twitch_url": twitch_url
            }
            members_with_status.append(member_data)
        
        log.debug(f"Returning {len(members_with_status)} members with status for role {role_id}.")
        return web.json_response(members_with_status)

    async def check_military_rank(self, member, minutes):
        """This logic should be in ActivityTracker. Keeping it here as per original file."""
        # ... (your existing check_military_rank logic)
        pass

    async def update_member_activity(self, member, minutes_to_add=5):
        """This logic should be in ActivityTracker. Keeping it here as per original file."""
        # ... (your existing update_member_activity logic)
        pass

    @tasks.loop(minutes=5)
    async def count_and_update(self):
        """Periodically count users with specific roles and update the Django website."""
        await self.bot.wait_until_ready()
        try:
            guild_id = await self.config.guild_id()
            if not guild_id:
                if self.count_and_update.current_loop == 0:
                    log.warning("GameCounter: Guild ID not set. The loop will not run until it is set.")
                return
            
            guild = self.bot.get_guild(guild_id)
            if not guild:
                log.error(f"GameCounter: Could not find guild with ID {guild_id}.")
                return
            
            # ... (rest of your count_and_update logic is fine)
            
        except Exception as e:
            log.error(f"Error in count_and_update: {e}", exc_info=True)

    # --- ADMIN/CONFIG COMMANDS for GameCounter ---
    
    @commands.hybrid_group(name="gamecounter", aliases=["gc"])
    async def gamecounter_settings(self, ctx: commands.Context):
        """Manage the GameCounter settings."""
        pass

    # ... (All your other commands like setapiurl, setapikey, addmapping, etc. remain here)
    # I am omitting them for brevity, but you should keep them in your file.
    # The web server specific commands (setwebhost, etc.) should be removed.

async def setup(bot: Red):
    """Set up the GameCounter cog."""
    cog = GameCounter(bot)
    await bot.add_cog(cog)
