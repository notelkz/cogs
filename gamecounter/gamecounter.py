import discord
import asyncio
import json
import aiohttp
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
        self.config.register_global(
            api_url=None,
            api_key=None,
            interval=15,
            guild_id=None,
            game_role_mappings={},
            web_api_host="0.0.0.0",
            web_api_port=5001,
            web_api_key=None
        )
        
        self.web_app = web.Application()
        self.web_runner = None
        self.web_site = None
        
        self.web_app.router.add_get(
            "/guilds/{guild_id}/roles/{role_id}/members", self.get_role_members_handler
        )
        self.web_app.router.add_get(
            "/health", self.health_check_handler
        )

    def cog_unload(self):
        asyncio.create_task(self._shutdown_web_server()) 
        if self.counter_loop.is_running():
            self.counter_loop.cancel()
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
        # This cog does not store any end-user data.
        return

    async def _authenticate_request(self, request: web.Request):
        """Authenticates incoming web API requests based on X-API-Key header."""
        expected_key = await self.config.web_api_key()
        if not expected_key:
            log.warning("Web API key is not set in config, all requests will fail authentication.")
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

    async def get_role_members_handler(self, request: web.Request):
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
            
            # Find the streaming activity, if it exists
            streaming_activity = next((a for a in member.activities if isinstance(a, discord.Streaming)), None)
            
            is_live = streaming_activity is not None
            # Use the real Twitch URL if available, otherwise fall back to guessing from their username
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
    @app_commands.describe(url="The Django API endpoint URL (e.g., http://your.site:8000/api/update_game_counts/)")
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
        if self.counter_loop.is_running():
            self.counter_loop.restart()
        else:
            self.counter_loop.start()
        await ctx.send(f"Counter interval set to `{minutes}` minutes. Loop restarted.")

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
            self.counter_loop.restart()
        else:
            await ctx.send("Guild setting cancelled.")

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
        self.counter_loop.restart()

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
            view.message = await ctx.send(f"Discord Role `{discord_role.name}` (`{role_id}`) is already mapped to Django Game `{current_mappings[str(role_id)]}`. Do you want to update it to `{django_game_name}`? (This will interrupt the current batch if cancelled.)", view=view)
            await view.wait()
            if not view.result:
                return await ctx.send("Mapping update cancelled.")
        for existing_role_id_str, existing_game_name in current_mappings.items():
            if existing_game_name == django_game_name and int(existing_role_id_str) != role_id:
                existing_role = ctx.guild.get_role(int(existing_role_id_str))
                existing_role_display = existing_role.name if existing_role else f"ID: {existing_role_id_str}"
                view = ConfirmView(ctx.author)
                view.message = await ctx.send(f"Warning: The Django game name `{django_game_name}` is already mapped to Discord Role `{existing_role_display}` (`{existing_role_id_str}`).\nAre you sure you want to map `{discord_role.name}` (`{role_id}`) to the *same* Django game name?\nThis is unusual and might lead to conflicting counts if both roles represent the same game.\nConfirm to proceed. (This will interrupt the current batch if cancelled.)", view=view)
                await view.wait()
                if not view.result:
                    return await ctx.send("Mapping cancelled to avoid potential conflict.")
                break
        current_mappings[str(role_id)] = django_game_name
        await self.config.game_role_mappings.set(current_mappings)
        await ctx.send(f"Mapping added/updated: Discord Role `{discord_role.name}` (`{role_id}`) -> Django Game `{django_game_name}`")
        self.counter_loop.restart()

    @commands.command(name="addmultiplemappingsbyname", hidden=False) 
    @commands.is_owner()
    async def add_multiple_mappings_by_name(self, ctx: commands.Context, *discord_roles: discord.Role):
        """Adds multiple mappings using Discord roles' names as Django GameCategory names."""
        if not discord_roles:
            return await ctx.send("Please provide at least one Discord role to map.")
        current_mappings = await self.config.game_role_mappings()
        successful_mappings = []
        skipped_mappings = []
        for discord_role in discord_roles:
            if not discord_role.guild == ctx.guild:
                skipped_mappings.append(f"`{discord_role.name}` (from another server)")
                continue
            role_id = discord_role.id
            django_game_name = discord_role.name
            if str(role_id) in current_mappings and current_mappings[str(role_id)] == django_game_name:
                skipped_mappings.append(f"`{discord_role.name}` (already mapped with same name)")
                continue
            if str(role_id) in current_mappings and current_mappings[str(role_id)] != django_game_name:
                view = ConfirmView(ctx.author)
                view.message = await ctx.send(f"Discord Role `{discord_role.name}` (`{role_id}`) is already mapped to Django Game `{current_mappings[str(role_id)]}`. Do you want to update it to `{django_game_name}`? (This will interrupt the current batch if cancelled.)", view=view)
                await view.wait()
                if not view.result:
                    skipped_mappings.append(f"`{discord_role.name}` (update cancelled)")
                    continue
            for existing_role_id_str, existing_game_name in current_mappings.items():
                if existing_game_name == django_game_name and int(existing_role_id_str) != role_id:
                    existing_role = ctx.guild.get_role(int(existing_role_id_str))
                    existing_role_display = existing_role.name if existing_role else f"ID: {existing_role_id_str}"
                    view = ConfirmView(ctx.author)
                    view.message = await ctx.send(f"Warning: The Django game name `{django_game_name}` is already mapped to Discord Role `{existing_role_display}` (`{existing_role_id_str}`).\nAre you sure you want to map `{discord_role.name}` (`{role_id}`) to the *same* Django game name?\nThis is unusual and might lead to conflicting counts if both roles represent the same game.\nConfirm to proceed. (This will interrupt the current batch if cancelled.)", view=view)
                    await view.wait()
                    if not view.result:
                        skipped_mappings.append(f"`{discord_role.name}` (conflict cancelled)")
                        continue
                    break
            current_mappings[str(role_id)] = django_game_name
            successful_mappings.append(f"`{discord_role.name}` (`{role_id}`)")
        await self.config.game_role_mappings.set(current_mappings)
        response_msg = ""
        if successful_mappings:
            response_msg += "Successfully added/updated mappings for:\n" + humanize_list(successful_mappings) + "\n"
        if skipped_mappings:
            response_msg += "Skipped mappings for:\n" + humanize_list(skipped_mappings) + "\n"
        if not response_msg:
            response_msg = "No mappings were added or updated."
        await ctx.send(response_msg)
        if successful_mappings:
            self.counter_loop.restart()

    @gamecounter_settings.command(name="removemapping")
    @commands.is_owner()
    @app_commands.describe(discord_role_id="The Discord ID of the role to remove from mapping.")
    async def remove_mapping(self, ctx: commands.Context, discord_role_id: int):
        """Removes a mapping by Discord Role ID."""
        current_mappings = await self.config.game_role_mappings()
        if str(discord_role_id) in current_mappings:
            del current_mappings[str(discord_role_id)]
            await self.config.game_role_mappings.set(current_mappings)
            await ctx.send(f"Mapping for Discord Role ID `{discord_role_id}` removed.")
            self.counter_loop.restart()
        else:
            await ctx.send(f"No mapping found for Discord Role ID `{discord_role_id}`.")

    @gamecounter_settings.command(name="mappings")
    async def show_mappings(self, ctx: commands.Context):
        """Shows all configured Discord Role ID to Django Game mappings."""
        mappings = await self.config.game_role_mappings()
        if not mappings:
            return await ctx.send("No game role mappings configured.")
        guild_id = await self.config.guild_id()
        guild = self.bot.get_guild(guild_id) if guild_id else None
        msg = "**Configured Game Role Mappings:**\n"
        for role_id_str, game_name in mappings.items():
            role_id = int(role_id_str)
            role = guild.get_role(role_id) if guild and guild.get_role(role_id) else None
            role_name = role.name if role else f"ID: {role_id_str} (Role not found in guild)"
            msg += f"`{role_name}` -> Django Game: **{game_name}**\n"
        await ctx.send(msg)

    @gamecounter_settings.command(name="status")
    async def show_status(self, ctx: commands.Context):
        """Shows the current GameCounter settings and status."""
        api_url = await self.config.api_url()
        api_key_set = "Yes" if await self.config.api_key() else "No"
        interval = await self.config.interval()
        guild_id = await self.config.guild_id()
        guild = self.bot.get_guild(guild_id) if guild_id else None
        mappings = await self.config.game_role_mappings()
        web_api_host = await self.config.web_api_host()
        web_api_port = await self.config.web_api_port()
        web_api_key_set = "Yes" if await self.config.web_api_key() else "No"
        status_msg = (f"**GameCounter Status:**\n"
            f"  API URL (RedBot->Django): `{api_url or 'Not set'}`\n"
            f"  API Key Set (RedBot->Django): `{api_key_set}`\n"
            f"  Web API Host (Django->RedBot): `{web_api_host}`\n"
            f"  Web API Port (Django->RedBot): `{web_api_port}`\n"
            f"  Web API Key Set (Django->RedBot): `{web_api_key_set}`\n"
            f"  Update Interval: `{interval} minutes`\n"
            f"  Counting Guild: `{guild.name}` (`{guild.id}`)" if guild else "`Not set`")
        if mappings:
            status_msg += "\n\n**Configured Mappings:**\n"
            for role_id_str, game_name in mappings.items():
                role_id = int(role_id_str)
                role = guild.get_role(role_id) if guild and guild.get_role(role_id) else None
                role_display = role.name if role else f"ID: {role_id_str}"
                status_msg += f"  - Discord Role: `{role_display}` -> Django Game: **{game_name}**\n"
        else:
            status_msg += "\n\nNo game role mappings configured."
        await ctx.send(status_msg)

    @gamecounter_settings.command(name="forcerun")
    @commands.is_owner()
    async def force_run(self, ctx: commands.Context):
        """Forces an immediate run of the game counter and updates the website."""
        await ctx.send("Forcing immediate game count update...")
        try:
            await self._run_update()
            await ctx.send("Game count update forced successfully!")
        except Exception as e:
            await ctx.send(f"An error occurred during force update: `{e}`")

    async def _get_game_counts(self, guild: discord.Guild):
        """Counts members per configured game role."""
        game_counts = {}
        role_mappings = await self.config.game_role_mappings()
        if not guild.chunked:
            try:
                await guild.chunk()
            except asyncio.TimeoutError:
                log.error(f"Failed to chunk guild {guild.id} for game count update within timeout.")
                return {}
            except Exception as e:
                log.error(f"Error chunking guild {guild.id} for game count update: {e}")
                return {}
        for role_id_str, game_name in role_mappings.items():
            role = guild.get_role(int(role_id_str))
            if role:
                member_count = len(role.members)
                game_counts[game_name] = member_count
            else:
                log.warning(f"Role with ID {role_id_str} not found in guild {guild.id}. Skipping count.")
        return game_counts

    async def _send_counts_to_django(self, game_counts: dict):
        """Sends the game counts to the Django API endpoint."""
        api_url = await self.config.api_url()
        api_key = await self.config.api_key()
        if not api_url or not api_key:
            log.warning("Django API URL or Key not configured. Skipping sending counts.")
            return False
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {"game_counts": game_counts}
        try:
            async with self.session.post(api_url, headers=headers, json=payload, timeout=10) as response:
                response.raise_for_status()
                response_json = await response.json()
                log.info(f"Successfully sent game counts to Django. Response: {response_json}")
                return True
        except aiohttp.ClientError as e:
            log.error(f"Error sending counts to Django API at {api_url}: {e}")
            return False
        except Exception as e:
            log.error(f"An unexpected error occurred sending counts to Django API: {e}")
            return False

    async def _run_update(self):
        """Fetches counts and sends them to Django."""
        guild_id = await self.config.guild_id()
        if not guild_id:
            log.debug("No guild ID configured for game counter. Skipping update.")
            return
        guild = self.bot.get_guild(guild_id)
        if not guild:
            log.warning(f"Configured guild with ID {guild_id} not found. Skipping update.")
            return
        if not self.bot.intents.members:
            log.error("Bot does not have the 'members' intent enabled! Cannot count members.")
            return
        game_counts = await self._get_game_counts(guild)
        if game_counts:
            success = await self._send_counts_to_django(game_counts)
            if not success:
                log.error("Failed to send game counts to Django.")
        else:
            log.debug("No game counts to send or error getting counts.")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Triggers an update if a member's roles change in the configured guild."""
        guild_id = await self.config.guild_id()
        if after.guild.id == guild_id and before.roles != after.roles:
            log.debug(f"Roles changed for member {after.display_name}. Restarting counter loop.")
            self.counter_loop.restart()

    @commands.Cog.listener()
    async def on_ready(self):
        """Ensures loops and web server start when the bot is fully ready."""
        log.info("GameCounter cog is ready.")
        if not self.counter_loop.is_running():
            self.counter_loop.start()
            log.info("GameCounter update loop started.")
        web_api_host = await self.config.web_api_host()
        web_api_port = await self.config.web_api_port()
        if not self.web_runner:
            try:
                self.web_runner = web.AppRunner(self.web_app)
                await self.web_runner.setup()
                self.web_site = web.TCPSite(self.web_runner, host=web_api_host, port=web_api_port)
                await self.web_site.start()
                log.info(f"GameCounter web API server started on http://{web_api_host}:{web_api_port}/")
            except Exception as e:
                log.error(f"Failed to start GameCounter web API server: {e}")
        else:
            log.debug("GameCounter web API server already running.")

    @tasks.loop(minutes=None)
    async def counter_loop(self):
        """Main loop that periodically updates game counts."""
        await self.bot.wait_until_ready()
        interval = await self.config.interval()
        if interval is None:
            log.debug("Counter interval not set, waiting 60s.")
            await asyncio.sleep(60) 
            return
        if self.counter_loop.minutes != interval:
            self.counter_loop.change_interval(minutes=interval)
            log.info(f"Counter loop interval changed to {interval} minutes.")
        log.debug(f"Running game count update (interval: {interval} mins).")
        await self._run_update()

    @counter_loop.before_loop
    async def before_counter_loop(self):
        """Hook that runs before the first iteration of the loop."""
        await self.bot.wait_until_ready()

async def setup(bot: Red):
    """Adds the GameCounter cog to the bot."""
    # To detect streaming status, both Members and Presences intents are required.
    if not bot.intents.members or not bot.intents.presences:
        log.critical("Members and Presences intents are NOT enabled! GameCounter cannot detect streaming status. Please enable them in your bot's application settings.")
        raise RuntimeError("Members and Presences intents are not enabled.")
    await bot.add_cog(GameCounter(bot))