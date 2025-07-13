import discord
import asyncio
import json
from aiohttp.web import Request
import aiohttp
import os
from datetime import datetime, timedelta

from redbot.core import commands, Config, app_commands
from redbot.core.utils.menus import DEFAULT_CONTROLS 
from redbot.core.utils.chat_formatting import humanize_list 
from redbot.core.utils.views import ConfirmView
from redbot.core.bot import Red
from discord.ext import tasks
from aiohttp import web

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
        self.config.register_global(
            api_url=None, # Django API URL for game counts, e.g., https://zerolivesleft.net/api/update-game-counts/
            api_key=None, # API Key for Django API
            interval=15,
            guild_id=None, # The main guild to count roles in
            game_role_mappings={}, # discord_role_id: django_game_name
            web_api_host="0.0.0.0", # Host for this cog's internal web API
            web_api_port=5001, # Port for this cog's internal web API
            web_api_key=None, # API key for Django to authenticate with this cog's web API
            activity_data={} 
        )
        
        self.web_app = web.Application()
        self.web_runner = None
        self.web_site = None
        
        # Internal web API routes for Django to query THIS BOT (these use underscores internally by convention)
        self.web_app.router.add_get(
            "/guilds/{guild_id}/roles/{role_id}/members", self.get_role_members_handler
        )
        self.web_app.router.add_get(
            "/health", self.health_check_handler
        )

    def cog_unload(self):
        asyncio.create_task(self._shutdown_web_server()) 
        if self.count_and_update.is_running():
            self.count_and_update.cancel()
        asyncio.create_task(self.session.close())

    async def _shutdown_web_server(self):
        """Helper to gracefully shut down the aiohttp web server."""
        if self.web_runner:
            log.info("Shutting down GameCounter web API server...")
            try:
                await self.web_app.shutdown()
                await self.web_runner.cleanup()
                log.info("GameCounter web API server shut down successfully.")
            except Exception as e:
                log.error(f"Error during web API server shutdown: {e}")
        self.web_runner = None
        self.web_site = None

    async def red_delete_data_for_user(self, *, requester: str, user_id: int) -> None:
        activity_data = await self.config.activity_data() # Assuming activity_data is global config
        if str(user_id) in activity_data:
            del activity_data[str(user_id)]
            await self.config.activity_data.set(activity_data)
        log.info(f"User data for {user_id} has been deleted.")
        return

    async def _authenticate_request(self, request: Request):
        """Authenticates incoming web API requests based on X-API-Key header."""
        expected_key = await self.config.web_api_key()
        if not expected_key:
            log.warning("Web API key is not set in config, all requests will fail authentication for this cog's internal API.")
            raise web.HTTPUnauthorized(reason="Web API Key not configured on RedBot.")
        
        provided_key = request.headers.get("X-API-Key")
        if not provided_key:
            raise web.HTTPUnauthorized(reason="X-API-Key header missing.")
        
        if provided_key != expected_key:
            raise web.HTTPForbidden(reason="Invalid API Key.")
        
        return True

    async def health_check_handler(self, request: Request):
        """Simple health check endpoint for the web API."""
        log.debug("Received health check request for GameCounter.")
        return web.Response(text="OK", status=200)

    async def get_role_members_handler(self, request: Request):
        """
        Web API handler to return members of a specific Discord role,
        including their live streaming status and Twitch URL.
        """
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
        log.warning("GameCounter: check_military_rank called. This logic should be in ActivityTracker.")
        return 

    async def update_member_activity(self, member, minutes_to_add=5):
        log.warning("GameCounter: update_member_activity called. This logic should be in ActivityTracker.")
        return

    @commands.Cog.listener()
    async def on_ready(self):
        """Start the counter loop and web server when the bot is ready."""
        await self.bot.wait_until_ready() 
        
        if not self.count_and_update.is_running():
            interval = await self.config.interval()
            self.count_and_update.change_interval(minutes=interval)
            self.count_and_update.start()
            log.info(f"Started game counter loop with {interval} minute interval")
        
        if not self.web_runner:
            host = await self.config.web_api_host()
            port = await self.config.web_api_port()
            try:
                self.web_runner = web.AppRunner(self.web_app)
                await self.web_runner.setup()
                self.web_site = web.TCPSite(self.web_runner, host, port)
                await self.web_site.start()
                log.info(f"GameCounter web API server started on {host}:{port}")
            except Exception as e:
                log.error(f"Failed to start web API server: {e}")

    @tasks.loop(minutes=5)
    async def count_and_update(self):
        """Periodically count users with specific roles and update the Django website."""
        log.info("Running GameCounter count_and_update loop.")
        try: # Outer try block starts here
            guild_id = await self.config.guild_id()
            if not guild_id:
                log.warning("GameCounter: Guild ID not set, skipping count_and_update.")
                return
            
            guild = self.bot.get_guild(guild_id)
            if not guild:
                log.error(f"GameCounter: Could not find guild with ID {guild_id}.")
                return
            
            if not guild.chunked:
                log.debug(f"GameCounter: Chunking guild {guild.id} for role counting.")
                try:
                    await guild.chunk()
                except discord.Forbidden:
                    log.error(f"GameCounter: Bot lacks permissions to chunk guild {guild.id}. Enable Server Members Intent.")
                    return
                except Exception as e:
                    log.error(f"GameCounter: Error chunking guild {guild.id}: {e}")
                    return
            
            mappings = await self.config.game_role_mappings()
            if not mappings:
                log.warning("No role-to-game mappings configured for GameCounter, skipping update.")
                return
            
            game_counts = {}
            
            for role_id_str, game_name in mappings.items():
                try:
                    role_id = int(role_id_str)
                    role = guild.get_role(role_id)
                    if not role:
                        log.warning(f"GameCounter: Could not find role with ID {role_id} for game '{game_name}'.")
                        continue
                    
                    member_count = len(role.members)
                    game_counts[game_name] = member_count
                    
                    log.debug(f"GameCounter: Counted {member_count} users with role '{role.name}' for game '{game_name}'")
                except Exception as e:
                    log.error(f"GameCounter: Error counting role {role_id_str}: {e}")
            
            api_url_config = await self.config.api_url() # Retrieve the configured API URL
            api_key = await self.config.api_key()
            
            if not api_url_config or not api_key:
                log.warning("GameCounter: Django API URL or API Key not set, skipping API update.")
                return
            
            headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
            payload = {"game_counts": game_counts}
            
            log.info(f"GameCounter: Sending game counts to {api_url_config} with payload: {payload}")
            
            try: # Inner try block for aiohttp request
                async with self.session.post(api_url_config, json=payload, headers=headers, timeout=10) as response:
                    log.info(f"GameCounter: API response status: {response.status}")
                    if response.status == 200:
                        response_data = await response.json()
                        log.info(f"GameCounter: Successfully sent game counts to API: {response_data}")
                    else:
                        error_text = await response.text()
                        log.error(f"GameCounter: Failed to send game counts to API. Status: {response.status}, Response: {error_text}")
            except asyncio.TimeoutError:
                log.error("GameCounter: API request timed out during count_and_update.")
            except aiohttp.ClientError as e:
                log.error(f"GameCounter: HTTP client error during count_and_update: {e}. Check URL and server accessibility.")
            except Exception as e: # Catch all other errors in inner try-block
                log.exception(f"GameCounter: Unhandled error during API request in count_and_update loop: {e}")

        except Exception as e: # Outer except block to catch anything not caught by inner tries
            log.exception(f"GameCounter: Unhandled error in count_and_update loop (outer block): {e}")

    # --- ADMIN/CONFIG COMMANDS for GameCounter ---

    @commands.hybrid_group(name="gamecounter", aliases=["gc"])
    async def gamecounter_settings(self, ctx: commands.Context):
        """Manage the GameCounter settings."""
        pass

    @gamecounter_settings.command(name="setwebhost")
    @commands.is_owner()
    @app_commands.describe(host="The host for the cog's web API (e.g., 0.0.0.0 for all interfaces, 127.0.0.1 for local).")
    async def set_web_host(self, ctx: commands.Context, host: str):
        """Sets the host for the cog's internal web API."""
        if ":" in host or "//" in host:
            return await ctx.send("Please provide just the host/IP address (e.g., `0.0.0.0` or `127.0.0.1`), not a full URL.")
        await self.config.web_api_host.set(host)
        await ctx.send(f"Web API host set to: `{host}`. Restart cog to apply changes.")
        log.info(f"Web API host set to {host} by {ctx.author}.")

    @gamecounter_settings.command(name="setwebport")
    @commands.is_owner()
    @app_commands.describe(port="The port for the cog's web API (e.g., 5001).")
    async def set_web_port(self, ctx: commands.Context, port: int):
        """Sets the port for the cog's internal web API."""
        if not (1024 <= port <= 65535):
            return await ctx.send("Please provide a port between 1024 and 65535.")
        await self.config.web_api_port.set(port)
        await ctx.send(f"Web API port set to: `{port}`. Restart cog to apply changes.")
        log.info(f"Web API port set to {port} by {ctx.author}.")

    @gamecounter_settings.command(name="setwebapikey")
    @commands.is_owner()
    @app_commands.describe(key="The secret API key for your Django website to authenticate with this cog's API.")
    async def set_web_api_key(self, ctx: commands.Context, key: str):
        """Sets the secret API key for your Django website to authenticate with this cog's API."""
        if len(key) < 16:
            return await ctx.send("Please provide a longer, more secure API key (e.g., 16+ characters).")
        await self.config.web_api_key.set(key)
        await ctx.send("Web API Key has been set. Keep this key secure!")
        log.info(f"Web API key set by {ctx.author}.")
    
    @gamecounter_settings.command(name="showwebapi")
    @commands.is_owner()
    async def show_web_api_settings(self, ctx: commands.Context):
        """Shows the current settings for the cog's internal web API."""
        host = await self.config.web_api_host()
        port = await self.config.web_api_port()
        key_set = "Yes" if await self.config.web_api_key() else "No"
        
        await ctx.send(
            f"**GameCounter Web API Settings:**\n"
            f"  Host: `{host}`\n"
            f"  Port: `{port}`\n"
            f"  API Key Set: `{key_set}`\n\n"
            "**Important:** If you changed host/port, you need to unload and load the cog for changes to take effect."
        )

    @gamecounter_settings.command(name="setapiurl")
    @commands.is_owner()
    @app_commands.describe(url="The Django API URL for sending game counts (e.g., https://your.site.com/api/update-game-counts/).")
    async def set_api_url(self, ctx: commands.Context, url: str):
        """Sets the Django API URL for sending game counts."""
        if not url.startswith("http://") and not url.startswith("https://"):
            return await ctx.send("Please provide a valid URL starting with `http://` or `https://`.")
        # This API URL is expected to be the FULL endpoint for update-game-counts,
        # not a base URL. So, no trailing slash enforcement is needed here.
        await self.config.api_url.set(url)
        await ctx.send(f"Django API URL set to: `{url}`")
        log.info(f"Django API URL set to {url} by {ctx.author}.")

    @gamecounter_settings.command(name="setapikey")
    @commands.is_owner()
    @app_commands.describe(key="The secret API key for your Django endpoint.")
    async def set_api_key(self, ctx: commands.Context, key: str):
        """Sets the secret API key for your Django endpoint."""
        await self.config.api_key.set(key)
        await ctx.send("Django API Key has been set.")
        log.info(f"Django API Key set by {ctx.author}.")

    @gamecounter_settings.command(name="setinterval")
    @commands.is_owner()
    @app_commands.describe(minutes="Interval in minutes for the counter to run (min 1).")
    async def set_interval(self, ctx: commands.Context, minutes: int):
        """Sets the interval (in minutes) for the counter to run."""
        if minutes < 1:
            return await ctx.send("Interval must be at least 1 minute.")
        await self.config.interval.set(minutes)
        if self.count_and_update.is_running():
            self.count_and_update.restart()
        else:
            self.count_and_update.start()
        await ctx.send(f"Counter interval set to `{minutes}` minutes. Loop restarted.")
        log.info(f"Counter interval set to {minutes} minutes by {ctx.author}. Loop restarted.")

    @gamecounter_settings.command(name="setguild")
    @commands.is_owner()
    @app_commands.describe(guild_id="The ID of the guild where roles should be counted.")
    async def set_guild(self, ctx: commands.Context, guild_id: int):
        """Sets the guild ID where game roles should be counted."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return await ctx.send(f"Could not find a guild with ID `{guild_id}`. Please ensure the bot is in that guild and the ID is correct.")
        view = ConfirmView(ctx.author) 
        view.message = await ctx.send(f"Are you sure you want to set the counting guild to **{guild.name}** (`{guild.id}`)?\nThis will stop counting roles in any previously configured guild.", view=view)
        await view.wait()
        if view.result:
            await self.config.guild_id.set(guild_id)
            await ctx.send(f"Counting guild set to **{guild.name}** (`{guild.id}`).")
            log.info(f"Counting guild set to {guild.name} ({guild.id}) by {ctx.author}.")
            if self.count_and_update.is_running():
                self.count_and_update.restart()
        else:
            await ctx.send("Guild setting cancelled.")
            log.info(f"Guild setting cancelled by {ctx.author}.")

    @gamecounter_settings.command(name="addmapping")
    @commands.is_owner()
    @app_commands.describe(discord_role_id="The Discord ID of the role (e.g., 'Minecraft Player' role ID).", django_game_name="The exact name of the GameCategory in your Django admin (e.g., 'Minecraft').")
    async def add_mapping(self, ctx: commands.Context, discord_role_id: int, django_game_name: str):
        """Adds a mapping between a Discord Role ID and a Django GameCategory name."""
        current_mappings = await self.config.game_role_mappings()
        if str(discord_role_id) in current_mappings and current_mappings[str(discord_role_id)] != django_game_name:
            view = ConfirmView(ctx.author)
            view.message = await ctx.send(f"Discord Role ID `{discord_role_id}` is already mapped to Django Game `{current_mappings[str(discord_role_id)]}`. Do you want to update it to `{django_game_name}`?", view=view)
            await view.wait()
            if not view.result:
                return await ctx.send("Mapping update cancelled.")
        current_mappings[str(discord_role_id)] = django_game_name
        await self.config.game_role_mappings.set(current_mappings)
        await ctx.send(f"Mapping added/updated: Discord Role ID `{discord_role_id}` -> Django Game `{django_game_name}`")
        log.info(f"Mapping added/updated: {discord_role_id} -> {django_game_name} by {ctx.author}.")
        self.count_and_update.restart()

    @gamecounter_settings.command(name="addmappingbyname")
    @commands.is_owner()
    @app_commands.describe(discord_role="The Discord role (mention, ID, or name). Its name will be used as the Django game name.")
    async def add_mapping_by_name(self, ctx: commands.Context, discord_role: discord.Role):
        """Adds a mapping using a Discord role's name as the Django GameCategory name."""
        if not discord_role.guild == ctx.guild:
            return await ctx.send("That role is not from this server. Please use `[p]gamecounter addmapping` with the ID if it's from another server.")
        role_id = discord_role.id
        django_game_name = discord_role.name
        current_mappings = await self.config.game_role_mappings()
        if str(role_id) in current_mappings and current_mappings[str(role_id)] == django_game_name:
            return await ctx.send(f"Mapping for `{discord_role.name}` (`{role_id}`) to Django Game `{django_game_name}` already exists.")
        if str(role_id) in current_mappings and current_mappings[str(role_id)] != django_game_name:
            view = ConfirmView(ctx.author)
            view.message = await ctx.send(f"Discord Role `{discord_role.name}` (`{role_id}`) is already mapped to Django Game `{current_mappings[str(role_id)]}`. Do you want to update it to `{django_game_name}`?", view=view)
            await view.wait()
            if not view.result:
                return await ctx.send("Mapping update cancelled.")
        for existing_role_id_str, existing_game_name in current_mappings.items():
            if existing_game_name == django_game_name and int(existing_role_id_str) != role_id:
                existing_role = ctx.guild.get_role(int(existing_role_id_str))
                existing_role_display = existing_role.name if existing_role else f"ID: {existing_role_id_str}"
                view = ConfirmView(ctx.author)
                view.message = await ctx.send(f"Warning: The Django game name `{django_game_name}` is already mapped to Discord Role `{existing_role_display}` (`{existing_role_id_str}`).\nAre you sure you want to map `{discord_role.name}` (`{role_id}`) to the *same* Django game name?\nThis is generally not recommended unless you are sure this is intended.", view=view)
                await view.wait()
                if not view.result:
                    return await ctx.send("Mapping update cancelled.")

        current_mappings[str(role_id)] = django_game_name
        await self.config.game_role_mappings.set(current_mappings)
        await ctx.send(f"Mapping added/updated: Discord Role `{discord_role.name}` (`{role_id}`) -> Django Game `{django_game_name}`")
        log.info(f"Mapping added/updated: {discord_role.name} ({role_id}) -> {django_game_name} by {ctx.author}.")
        self.count_and_update.restart()

    @gamecounter_settings.command(name="removemapping")
    @commands.is_owner()
    @app_commands.describe(discord_role_id="The Discord ID of the role to remove from mappings.")
    async def remove_mapping(self, ctx: commands.Context, discord_role_id: int):
        """Removes a mapping for a Discord Role ID."""
        current_mappings = await self.config.game_role_mappings()
        if str(discord_role_id) not in current_mappings:
            return await ctx.send(f"No mapping found for Discord Role ID `{discord_role_id}`.")
        django_game_name = current_mappings[str(discord_role_id)]
        view = ConfirmView(ctx.author)
        view.message = await ctx.send(f"Are you sure you want to remove the mapping for Discord Role ID `{discord_role_id}` -> Django Game `{django_game_name}`?", view=view)
        await view.wait()
        if not view.result:
            return await ctx.send("Mapping removal cancelled.")
        del current_mappings[str(discord_role_id)]
        await self.config.game_role_mappings.set(current_mappings)
        await ctx.send(f"Mapping removed for Discord Role ID `{discord_role_id}` -> Django Game `{django_game_name}`")
        log.info(f"Mapping removed for {discord_role_id} -> {django_game_name} by {ctx.author}.")
        self.count_and_update.restart()

    @gamecounter_settings.command(name="listmappings")
    @commands.is_owner()
    async def list_mappings(self, ctx: commands.Context):
        """Lists all current role-to-game mappings."""
        current_mappings = await self.config.game_role_mappings()
        if not current_mappings:
            return await ctx.send("No role-to-game mappings are currently configured.")
        
        guild_id = await self.config.guild_id()
        guild = self.bot.get_guild(guild_id) if guild_id else None
        
        embed = discord.Embed(
            title="GameCounter Role-to-Game Mappings",
            description=f"Counting guild: {guild.name if guild else 'Not set'} (`{guild_id if guild_id else 'Not set'}`)",
            color=discord.Color.blue()
        )
        
        for role_id_str, game_name in current_mappings.items():
            role_id = int(role_id_str)
            role = guild.get_role(role_id) if guild else None
            role_name = role.name if role else "Unknown Role"
            embed.add_field(
                name=f"{game_name}",
                value=f"Role: {role_name}\nID: `{role_id}`\nMembers: {len(role.members) if role else 'N/A'}",
                inline=True
            )
        
        await ctx.send(embed=embed)

    @gamecounter_settings.command(name="showconfig")
    @commands.is_owner()
    async def show_config(self, ctx: commands.Context):
        """Shows the current GameCounter configuration."""
        config_data = await self.config.all()
        
        # Mask API keys for security
        api_key_masked = "Set" if config_data.get("api_key") else "Not Set"
        web_api_key_masked = "Set" if config_data.get("web_api_key") else "Not Set"
        
        guild_id = config_data.get("guild_id")
        guild = self.bot.get_guild(guild_id) if guild_id else None
        
        embed = discord.Embed(
            title="GameCounter Configuration",
            color=discord.Color.blue()
        )
        
        embed.add_field(name="Django API URL", value=config_data.get("api_url") or "Not Set", inline=False)
        embed.add_field(name="Django API Key", value=api_key_masked, inline=True)
        embed.add_field(name="Update Interval", value=f"{config_data.get('interval')} minutes", inline=True)
        embed.add_field(name="Counting Guild", value=f"{guild.name if guild else 'Not Set'} (`{guild_id if guild_id else 'Not Set'}`)", inline=False)
        embed.add_field(name="Web API Host", value=config_data.get("web_api_host"), inline=True)
        embed.add_field(name="Web API Port", value=config_data.get("web_api_port"), inline=True)
        embed.add_field(name="Web API Key", value=web_api_key_masked, inline=True)
        
        # Add loop status
        loop_status = "Running" if self.count_and_update.is_running() else "Stopped"
        embed.add_field(name="Counter Loop Status", value=loop_status, inline=False)
        
        # Add web server status
        web_server_status = "Running" if self.web_runner else "Stopped"
        embed.add_field(name="Web Server Status", value=web_server_status, inline=True)
        
        await ctx.send(embed=embed)

    @gamecounter_settings.command(name="start")
    @commands.is_owner()
    async def start_counter(self, ctx: commands.Context):
        """Starts the game counter loop if it's not already running."""
        if self.count_and_update.is_running():
            return await ctx.send("The counter loop is already running.")
        
        # Check if required settings are configured
        guild_id = await self.config.guild_id()
        api_url = await self.config.api_url()
        api_key = await self.config.api_key()
        mappings = await self.config.game_role_mappings()
        
        missing_settings = []
        if not guild_id:
            missing_settings.append("Guild ID")
        if not api_url:
            missing_settings.append("API URL")
        if not api_key:
            missing_settings.append("API Key")
        if not mappings:
            missing_settings.append("Role-to-Game Mappings")
        
        if missing_settings:
            return await ctx.send(f"Cannot start counter loop. Missing required settings: {humanize_list(missing_settings)}")
        
        try:
            self.count_and_update.start()
            await ctx.send("Game counter loop started successfully.")
        except Exception as e:
            await ctx.send(f"Error starting counter loop: {e}")

    @gamecounter_settings.command(name="stop")
    @commands.is_owner()
    async def stop_counter(self, ctx: commands.Context):
        """Stops the game counter loop if it's running."""
        if not self.count_and_update.is_running():
            return await ctx.send("The counter loop is not running.")
        
        try:
            self.count_and_update.cancel()
            await ctx.send("Game counter loop stopped successfully.")
        except Exception as e:
            await ctx.send(f"Error stopping counter loop: {e}")

    @gamecounter_settings.command(name="startwebapi")
    @commands.is_owner()
    async def start_web_api(self, ctx: commands.Context):
        """Starts the web API server if it's not already running."""
        if self.web_runner:
            return await ctx.send("The web API server is already running.")
        
        host = await self.config.web_api_host()
        port = await self.config.web_api_port()
        
        try:
            self.web_runner = web.AppRunner(self.web_app)
            await self.web_runner.setup()
            self.web_site = web.TCPSite(self.web_runner, host, port)
            await self.web_site.start()
            log.info(f"GameCounter web API server started on {host}:{port}")
            await ctx.send(f"Web API server started successfully on `{host}:{port}`.")
        except Exception as e:
            log.error(f"Failed to start web API server: {e}")
            await ctx.send(f"Error starting web API server: {e}")

    @gamecounter_settings.command(name="stopwebapi")
    @commands.is_owner()
    async def stop_web_api(self, ctx: commands.Context):
        """Stops the web API server if it's running."""
        if not self.web_runner:
            return await ctx.send("The web API server is not running.")
        
        try:
            await self._shutdown_web_server()
            await ctx.send("Web API server stopped successfully.")
        except Exception as e:
            await ctx.send(f"Error stopping web API server: {e}")

    @gamecounter_settings.command(name="resetactivity")
    @commands.is_owner()
    async def reset_activity(self, ctx: commands.Context, user: discord.Member = None):
        """Reset activity data for a user or all users."""
        if user:
            # Reset for specific user
            activity_data = await self.config.activity_data()
            user_id = str(user.id)
            if user_id in activity_data:
                del activity_data[user_id]
                await self.config.activity_data.set(activity_data)
                await ctx.send(f"Activity data reset for {user.mention}.")
            else:
                await ctx.send(f"No activity data found for {user.mention}.")
        else:
            # Confirm before resetting all data
            view = ConfirmView(ctx.author)
            view.message = await ctx.send("Are you sure you want to reset ALL activity data for ALL users? This cannot be undone.", view=view)
            await view.wait()
            if view.result:
                await self.config.activity_data.set({})
                await ctx.send("All activity data has been reset.")
            else:
                await ctx.send("Reset cancelled.")

    @gamecounter_settings.command(name="viewactivity")
    @commands.is_owner()
    async def view_activity(self, ctx: commands.Context, user: discord.Member):
        """View activity data for a specific user."""
        activity_data = await self.config.activity_data()
        user_id = str(user.id)
        
        if user_id not in activity_data:
            return await ctx.send(f"No activity data found for {user.mention}.")
            
        minutes = activity_data[user_id].get("minutes", 0)
        hours = minutes / 60
        
        embed = discord.Embed(
            title=f"Activity Data for {user.display_name}",
            color=user.color
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="Total Time", value=f"{hours:.2f} hours ({minutes} minutes)", inline=False)
        
        # Find the user's current military rank
        military_ranks = [
            {"name": "Private", "role_id": 1274274605435060224, "minutes_required": 10 * 60},  # 10 hours
            {"name": "Private First Class", "role_id": 1274274696048934965, "minutes_required": 25 * 60},  # 25 hours
            {"name": "Corporal", "role_id": 1274771534119964813, "minutes_required": 50 * 60},  # 50 hours
            {"name": "Specialist", "role_id": 1274771654907658402, "minutes_required": 75 * 60},  # 75 hours
            {"name": "Sergeant", "role_id": 1274771991748022276, "minutes_required": 100 * 60},  # 100 hours
            {"name": "Staff Sergeant", "role_id": 1274772130424164384, "minutes_required": 150 * 60},  # 150 hours
            {"name": "Sergeant First Class", "role_id": 1274772191107485706, "minutes_required": 225 * 60},  # 225 hours
            {"name": "Master Sergeant", "role_id": 1274772252545519708, "minutes_required": 300 * 60},  # 300 hours
            {"name": "First Sergeant", "role_id": 1274772335689465978, "minutes_required": 375 * 60},  # 375 hours
            {"name": "Sergeant Major", "role_id": 1274772419927605299, "minutes_required": 450 * 60},  # 450 hours
            {"name": "Command Sergeant Major", "role_id": 1274772500164640830, "minutes_required": 550 * 60},  # 550 hours
            {"name": "Sergeant Major of the Army", "role_id": 1274772595031539787, "minutes_required": 650 * 60},  # 650 hours
            {"name": "Warrant Officer 1", "role_id": 1358212838631407797, "minutes_required": 750 * 60},  # 750 hours
            {"name": "Chief Warrant Officer 2", "role_id": 1358213159583875172, "minutes_required": 875 * 60},  # 875 hours
            {"name": "Chief Warrant Officer 3", "role_id": 1358213229112852721, "minutes_required": 1000 * 60},  # 1000 hours
            {"name": "Chief Warrant Officer 4", "role_id": 1358213408704430150, "minutes_required": 1200 * 60},  # 1200 hours
            {"name": "Chief Warrant Officer 5", "role_id": 1358213451289460847, "minutes_required": 1400 * 60},  # 1400 hours
            {"name": "Second Lieutenant", "role_id": 1358213662216814784, "minutes_required": 1600 * 60},  # 1600 hours
            {"name": "First Lieutenant", "role_id": 1358213759805554979, "minutes_required": 1850 * 60},  # 1850 hours
            {"name": "Captain", "role_id": 1358213809466118276, "minutes_required": 2100 * 60},  # 2100 hours
            {"name": "Major", "role_id": 1358213810598449163, "minutes_required": 2400 * 60},  # 2400 hours
            {"name": "Lieutenant Colonel", "role_id": 1358213812175503430, "minutes_required": 2750 * 60},  # 2750 hours
            {"name": "Colonel", "role_id": 1358213813140459520, "minutes_required": 3100 * 60},  # 3100 hours
            {"name": "Brigadier General", "role_id": 1358213814234906786, "minutes_required": 3500 * 60},  # 3500 hours
            {"name": "Major General", "role_id": 1358213815203795004, "minutes_required": 4000 * 60},  # 4000 hours
            {"name": "Lieutenant General", "role_id": 1358213817229770783, "minutes_required": 4500 * 60},  # 4500 hours
            {"name": "General", "role_id": 1358213815983935608, "minutes_required": 5000 * 60},  # 5000 hours
            {"name": "General of the Army", "role_id": 1358213816617275483, "minutes_required": 6000 * 60},  # 6000 hours
        ]
        
        current_rank = None
        next_rank = None
        
        for i, rank in enumerate(military_ranks):
            if minutes >= rank["minutes_required"]:
                current_rank = rank
                if i < len(military_ranks) - 1:
                    next_rank = military_ranks[i + 1]
            elif not next_rank:
                next_rank = rank
                if i > 0:
                    current_rank = military_ranks[i - 1]
                break
        
        if current_rank:
            embed.add_field(name="Current Rank", value=current_rank["name"], inline=True)
        else:
            embed.add_field(name="Current Rank", value="None", inline=True)
            
        if next_rank:
            minutes_needed = next_rank["minutes_required"] - minutes
            hours_needed = minutes_needed / 60
            embed.add_field(name="Next Rank", value=f"{next_rank['name']} (needs {hours_needed:.2f} more hours)", inline=True)
        else:
            embed.add_field(name="Next Rank", value="Maximum rank reached!", inline=True)
            
        await ctx.send(embed=embed)

    @gamecounter_settings.command(name="runnow")
    @commands.is_owner()
    async def run_now(self, ctx: commands.Context):
        """Manually trigger the count and update process."""
        await ctx.send("Running count and update process...")
        try:
            await self.count_and_update()
            await ctx.send("Count and update process completed successfully.")
        except Exception as e:
            await ctx.send(f"Error during count and update process: {e}")
            log.error(f"Manual count and update failed: {e}")

def setup(bot):
    bot.add_cog(GameCounter(bot))
