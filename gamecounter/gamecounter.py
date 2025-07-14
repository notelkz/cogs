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
        # MODIFIED: Removed web server specific config values
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
        
        # Start the main loop
        self.count_and_update.start()
        # ADDED: Create a task to register routes once the bot is ready
        asyncio.create_task(self.initialize())

    # ADDED: New method to register routes with the central WebServer cog
    async def initialize(self):
        """Waits for the bot to be ready and then registers API routes."""
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

    # MODIFIED: Simplified unload method
    def cog_unload(self):
        """Cleanup when the cog is unloaded."""
        if self.count_and_update.is_running():
            self.count_and_update.cancel()
        asyncio.create_task(self.session.close())

    # REMOVED: _shutdown_web_server method is no longer needed

    async def red_delete_data_for_user(self, *, requester: str, user_id: int) -> None:
        activity_data = await self.config.activity_data()
        if str(user_id) in activity_data:
            del activity_data[str(user_id)]
            await self.config.activity_data.set(activity_data)
        return

    # MODIFIED: Authentication now uses the WebServer cog's config
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

    # --- API Handlers ---
    
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

    # --- Core Logic ---

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
            
            if not guild.chunked:
                await guild.chunk()

            mappings = await self.config.game_role_mappings()
            if not mappings:
                return

            game_counts = {}
            active_users = set()
            
            for role_id_str, game_name in mappings.items():
                role = guild.get_role(int(role_id_str))
                if role:
                    game_counts[game_name] = len(role.members)
                    for member in role.members:
                        if not member.bot:
                            active_users.add(member)
            
            for member in active_users:
                await self.update_member_activity(member)
            
            api_url = await self.config.api_url()
            api_key = await self.config.api_key()
            
            if api_url and api_key:
                headers = {"Authorization": f"Token {api_key}"}
                async with self.session.post(api_url, json=game_counts, headers=headers) as response:
                    if response.status != 200:
                        log.error(f"Failed to send game counts. Status: {response.status}, Response: {await response.text()}")
        except Exception as e:
            log.error(f"Error in count_and_update: {e}", exc_info=True)

    # ... (Your check_military_rank and update_member_activity methods remain here) ...
    async def check_military_rank(self, member, minutes):
        # ... (your existing logic)
        pass

    async def update_member_activity(self, member, minutes_to_add=5):
        # ... (your existing logic)
        pass

    # --- Commands ---
    # RESTORED: All your original commands are here, minus the obsolete web server ones.
    
    @commands.hybrid_group(name="gamecounter", aliases=["gc"])
    async def gamecounter_settings(self, ctx: commands.Context):
        """Manage the GameCounter settings."""
        pass

    @gamecounter_settings.command(name="setwebsiteapi")
    @commands.is_owner()
    @app_commands.describe(url="The website API URL for role updates", api_key="The API key for the website")
    async def set_website_api(self, ctx: commands.Context, url: str, api_key: str):
        """Set the website API URL and key for role update notifications."""
        if not url.startswith("http"):
            return await ctx.send("Please provide a valid URL starting with `http://` or `https://`.")
        
        await self.config.website_api_url.set(url)
        await self.config.website_api_key.set(api_key)
        await ctx.send(f"Website API settings updated:\nURL: `{url}`\nAPI Key: Set")

    @gamecounter_settings.command(name="setapiurl")
    @commands.is_owner()
    @app_commands.describe(url="The Django API endpoint URL (e.g., https://zerolivesleft.net/api/update_game_counts/)")
    async def set_api_url(self, ctx: commands.Context, url: str):
        """Sets the Django API endpoint URL."""
        if not url.startswith("http"):
            return await ctx.send("Please provide a valid URL starting with `http://` or `https://`.")
        await self.config.api_url.set(url)
        await ctx.send(f"Django API URL set to: `{url}`")

    @gamecounter_settings.command(name="setapikey")
    @commands.is_owner()
    @app_commands.describe(key="The secret API key for your Django endpoint.")
    async def set_api_key(self, ctx: commands.Context, key: str):
        """Sets the secret API key for your Django endpoint."""
        await self.config.api_key.set(key)
        await ctx.send("Django API Key has been set.")

    @gamecounter_settings.command(name="setinterval")
    @commands.is_owner()
    @app_commands.describe(minutes="Interval in minutes for the counter to run (min 1).")
    async def set_interval(self, ctx: commands.Context, minutes: int):
        """Sets the interval (in minutes) for the counter to run."""
        if minutes < 1:
            return await ctx.send("Interval must be at least 1 minute.")
        await self.config.interval.set(minutes)
        self.count_and_update.change_interval(minutes=minutes)
        await ctx.send(f"Counter interval set to `{minutes}` minutes.")

    @gamecounter_settings.command(name="setguild")
    @commands.is_owner()
    @app_commands.describe(guild_id="The ID of the guild where roles should be counted.")
    async def set_guild(self, ctx: commands.Context, guild_id: int):
        """Sets the guild ID where game roles should be counted."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return await ctx.send(f"Could not find a guild with ID `{guild_id}`.")
        await self.config.guild_id.set(guild_id)
        await ctx.send(f"Counting guild set to **{guild.name}** (`{guild.id}`).")

    @gamecounter_settings.command(name="addmapping")
    @commands.is_owner()
    @app_commands.describe(discord_role_id="The Discord ID of the role.", django_game_name="The exact name of the GameCategory in Django.")
    async def add_mapping(self, ctx: commands.Context, discord_role_id: int, django_game_name: str):
        """Adds a mapping between a Discord Role ID and a Django GameCategory name."""
        async with self.config.game_role_mappings() as mappings:
            mappings[str(discord_role_id)] = django_game_name
        await ctx.send(f"Mapping added: Role ID `{discord_role_id}` -> Game `{django_game_name}`")

    @gamecounter_settings.command(name="removemapping")
    @commands.is_owner()
    @app_commands.describe(discord_role_id="The Discord ID of the role to remove.")
    async def remove_mapping(self, ctx: commands.Context, discord_role_id: int):
        """Removes a mapping for a Discord Role ID."""
        async with self.config.game_role_mappings() as mappings:
            if str(discord_role_id) in mappings:
                del mappings[str(discord_role_id)]
                await ctx.send(f"Mapping removed for Role ID `{discord_role_id}`.")
            else:
                await ctx.send("No mapping found for that Role ID.")

    @gamecounter_settings.command(name="listmappings")
    @commands.is_owner()
    async def list_mappings(self, ctx: commands.Context):
        """Lists all current role-to-game mappings."""
        mappings = await self.config.game_role_mappings()
        if not mappings:
            return await ctx.send("No mappings configured.")
        
        guild_id = await self.config.guild_id()
        guild = self.bot.get_guild(guild_id) if guild_id else None
        
        msg = "**Current Role to Game Mappings:**\n"
        for role_id, game_name in mappings.items():
            role = guild.get_role(int(role_id)) if guild else None
            role_name = f"`{role.name}`" if role else "`Unknown Role`"
            msg += f"- {role_name} (ID: `{role_id}`) -> `{game_name}`\n"
        await ctx.send(msg)

    # ... (Your resetactivity and viewactivity commands remain here) ...
    @gamecounter_settings.command(name="resetactivity")
    @commands.is_owner()
    async def reset_activity(self, ctx: commands.Context, user: discord.Member = None):
        # ... (your existing logic)
        pass

    @gamecounter_settings.command(name="viewactivity")
    @commands.is_owner()
    async def view_activity(self, ctx: commands.Context, user: discord.Member):
        # ... (your existing logic)
        pass

async def setup(bot: Red):
    """Set up the GameCounter cog."""
    cog = GameCounter(bot)
    await bot.add_cog(cog)
