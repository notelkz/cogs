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
        self.config.register_global(
            api_url=None,
            api_key=None,
            interval=15,
            guild_id=None,
            game_role_mappings={},
            web_api_host="0.0.0.0",
            web_api_port=5001,
            web_api_key=None,
            activity_data={}  # Store user activity data
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
        
        # Add route for getting time ranks
        self.web_app.router.add_get(
            "/api/get_time_ranks/", self.get_time_ranks_handler
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
        activity_data = await self.config.activity_data()
        if str(user_id) in activity_data:
            del activity_data[str(user_id)]
            await self.config.activity_data.set(activity_data)
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

    async def get_time_ranks_handler(self, request: web.Request):
        """Handler for the time ranks API endpoint."""
        try:
            await self._authenticate_request(request)
        except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
            log.warning(f"Authentication failed for /api/get_time_ranks/ endpoint: {e.reason}")
            return e
            
        # Define military ranks with their requirements
        military_ranks = [
            {"name": "Private", "role_id": "1274274605435060224", "minutes_required": 10 * 60},  # 10 hours
            {"name": "Private First Class", "role_id": "1274274696048934965", "minutes_required": 25 * 60},  # 25 hours
            {"name": "Corporal", "role_id": "1274771534119964813", "minutes_required": 50 * 60},  # 50 hours
            {"name": "Specialist", "role_id": "1274771654907658402", "minutes_required": 75 * 60},  # 75 hours
            {"name": "Sergeant", "role_id": "1274771991748022276", "minutes_required": 100 * 60},  # 100 hours
            {"name": "Staff Sergeant", "role_id": "1274772130424164384", "minutes_required": 150 * 60},  # 150 hours
            {"name": "Sergeant First Class", "role_id": "1274772191107485706", "minutes_required": 225 * 60},  # 225 hours
            {"name": "Master Sergeant", "role_id": "1274772252545519708", "minutes_required": 300 * 60},  # 300 hours
            {"name": "First Sergeant", "role_id": "1274772335689465978", "minutes_required": 375 * 60},  # 375 hours
            {"name": "Sergeant Major", "role_id": "1274772419927605299", "minutes_required": 450 * 60},  # 450 hours
            {"name": "Command Sergeant Major", "role_id": "1274772500164640830", "minutes_required": 550 * 60},  # 550 hours
            {"name": "Sergeant Major of the Army", "role_id": "1274772595031539787", "minutes_required": 650 * 60},  # 650 hours
            {"name": "Warrant Officer 1", "role_id": "1358212838631407797", "minutes_required": 750 * 60},  # 750 hours
            {"name": "Chief Warrant Officer 2", "role_id": "1358213159583875172", "minutes_required": 875 * 60},  # 875 hours
            {"name": "Chief Warrant Officer 3", "role_id": "1358213229112852721", "minutes_required": 1000 * 60},  # 1000 hours
            {"name": "Chief Warrant Officer 4", "role_id": "1358213408704430150", "minutes_required": 1200 * 60},  # 1200 hours
            {"name": "Chief Warrant Officer 5", "role_id": "1358213451289460847", "minutes_required": 1400 * 60},  # 1400 hours
            {"name": "Second Lieutenant", "role_id": "1358213662216814784", "minutes_required": 1600 * 60},  # 1600 hours
            {"name": "First Lieutenant", "role_id": "1358213759805554979", "minutes_required": 1850 * 60},  # 1850 hours
            {"name": "Captain", "role_id": "1358213809466118276", "minutes_required": 2100 * 60},  # 2100 hours
            {"name": "Major", "role_id": "1358213810598449163", "minutes_required": 2400 * 60},  # 2400 hours
            {"name": "Lieutenant Colonel", "role_id": "1358213812175503430", "minutes_required": 2750 * 60},  # 2750 hours
            {"name": "Colonel", "role_id": "1358213813140459520", "minutes_required": 3100 * 60},  # 3100 hours
            {"name": "Brigadier General", "role_id": "1358213814234906786", "minutes_required": 3500 * 60},  # 3500 hours
            {"name": "Major General", "role_id": "1358213815203795004", "minutes_required": 4000 * 60},  # 4000 hours
            {"name": "Lieutenant General", "role_id": "1358213817229770783", "minutes_required": 4500 * 60},  # 4500 hours
            {"name": "General", "role_id": "1358213815983935608", "minutes_required": 5000 * 60},  # 5000 hours
            {"name": "General of the Army", "role_id": "1358213816617275483", "minutes_required": 6000 * 60},  # 6000 hours
        ]
        
        return web.json_response(military_ranks)

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

    async def check_military_rank(self, member, minutes):
        """Check and assign military rank based on playtime."""
        try:
            # Safety check for member object
            if not member or not member.guild:
                log.error(f"Invalid member object for military rank check")
                return
                
            log.debug(f"Starting military rank check for {member.name} with {minutes} minutes ({minutes/60:.2f} hours)")
            
            # Define military ranks directly in the code with your actual role IDs
            military_ranks = [
                {"name": "Private", "role_id": "1274274605435060224", "minutes_required": 10 * 60},  # 10 hours
                {"name": "Private First Class", "role_id": "1274274696048934965", "minutes_required": 25 * 60},  # 25 hours
                {"name": "Corporal", "role_id": "1274771534119964813", "minutes_required": 50 * 60},  # 50 hours
                {"name": "Specialist", "role_id": "1274771654907658402", "minutes_required": 75 * 60},  # 75 hours
                {"name": "Sergeant", "role_id": "1274771991748022276", "minutes_required": 100 * 60},  # 100 hours
                {"name": "Staff Sergeant", "role_id": "1274772130424164384", "minutes_required": 150 * 60},  # 150 hours
                {"name": "Sergeant First Class", "role_id": "1274772191107485706", "minutes_required": 225 * 60},  # 225 hours
                {"name": "Master Sergeant", "role_id": "1274772252545519708", "minutes_required": 300 * 60},  # 300 hours
                {"name": "First Sergeant", "role_id": "1274772335689465978", "minutes_required": 375 * 60},  # 375 hours
                {"name": "Sergeant Major", "role_id": "1274772419927605299", "minutes_required": 450 * 60},  # 450 hours
                {"name": "Command Sergeant Major", "role_id": "1274772500164640830", "minutes_required": 550 * 60},  # 550 hours
                {"name": "Sergeant Major of the Army", "role_id": "1274772595031539787", "minutes_required": 650 * 60},  # 650 hours
                {"name": "Warrant Officer 1", "role_id": "1358212838631407797", "minutes_required": 750 * 60},  # 750 hours
                {"name": "Chief Warrant Officer 2", "role_id": "1358213159583875172", "minutes_required": 875 * 60},  # 875 hours
                {"name": "Chief Warrant Officer 3", "role_id": "1358213229112852721", "minutes_required": 1000 * 60},  # 1000 hours
                {"name": "Chief Warrant Officer 4", "role_id": "1358213408704430150", "minutes_required": 1200 * 60},  # 1200 hours
                {"name": "Chief Warrant Officer 5", "role_id": "1358213451289460847", "minutes_required": 1400 * 60},  # 1400 hours
                {"name": "Second Lieutenant", "role_id": "1358213662216814784", "minutes_required": 1600 * 60},  # 1600 hours
                {"name": "First Lieutenant", "role_id": "1358213759805554979", "minutes_required": 1850 * 60},  # 1850 hours
                {"name": "Captain", "role_id": "1358213809466118276", "minutes_required": 2100 * 60},  # 2100 hours
                {"name": "Major", "role_id": "1358213810598449163", "minutes_required": 2400 * 60},  # 2400 hours
                {"name": "Lieutenant Colonel", "role_id": "1358213812175503430", "minutes_required": 2750 * 60},  # 2750 hours
                {"name": "Colonel", "role_id": "1358213813140459520", "minutes_required": 3100 * 60},  # 3100 hours
                {"name": "Brigadier General", "role_id": "1358213814234906786", "minutes_required": 3500 * 60},  # 3500 hours
                {"name": "Major General", "role_id": "1358213815203795004", "minutes_required": 4000 * 60},  # 4000 hours
                {"name": "Lieutenant General", "role_id": "1358213817229770783", "minutes_required": 4500 * 60},  # 4500 hours
                {"name": "General", "role_id": "1358213815983935608", "minutes_required": 5000 * 60},  # 5000 hours
                {"name": "General of the Army", "role_id": "1358213816617275483", "minutes_required": 6000 * 60},  # 6000 hours
            ]
            
            # Find the highest rank the user qualifies for
            eligible_rank = None
            for rank in military_ranks:
                if minutes >= rank["minutes_required"]:
                    if not eligible_rank or rank["minutes_required"] > eligible_rank["minutes_required"]:
                        eligible_rank = rank
                        
            if not eligible_rank:
                log.debug(f"User {member.name} does not qualify for any military rank")
                return
                
            log.debug(f"User {member.name} qualifies for military rank: {eligible_rank['name']} with {minutes} minutes ({minutes/60:.2f} hours)")
            
            # Check if the user already has this rank
            role = member.guild.get_role(int(eligible_rank["role_id"]))
            if not role:
                log.error(f"Role with ID {eligible_rank['role_id']} not found in guild {member.guild.name}")
                return
                
            if role in member.roles:
                log.debug(f"User {member.name} already has the {eligible_rank['name']} rank")
                return
                
            # Remove any existing military ranks
            for rank in military_ranks:
                existing_role = member.guild.get_role(int(rank["role_id"]))
                if existing_role and existing_role in member.roles:
                    await member.remove_roles(existing_role)
                    log.debug(f"Removed {rank['name']} role from {member.name}")
                    
            # Add the new rank
            await member.add_roles(role)
            log.info(f"MILITARY SUCCESS: Assigned {eligible_rank['name']} rank to {member.name}")
            
            # Notify the website about the role change
            try:
                api_url = "http://87.106.44.164:8000/api/update_role/"
                async with aiohttp.ClientSession() as session:
                    async with session.post(api_url, json={
                        "user_id": str(member.id),
                        "role_name": eligible_rank["name"],
                        "api_key": os.getenv("WEBSITE_API_KEY", "")
                    }) as response:
                        if response.status != 200:
                            log.error(f"API ERROR: Failed to update military rank on website for {member.id}: {response.status} - {await response.text()}")
            except Exception as e:
                log.error(f"API ERROR: Failed to update military rank on website for {member.id}: {str(e)}")
                
        except Exception as e:
            log.error(f"MILITARY ERROR: Failed to check/assign military rank for {member.name}: {str(e)}")

    async def update_member_activity(self, member, minutes_to_add=5):
        """Update a member's activity time and check for promotions."""
        if not member or member.bot:
            return
            
        activity_data = await self.config.activity_data()
        user_id = str(member.id)
        
        if user_id not in activity_data:
            activity_data[user_id] = {"minutes": 0, "last_updated": 0}
            
        # Add activity time
        activity_data[user_id]["minutes"] += minutes_to_add
        activity_data[user_id]["last_updated"] = int(asyncio.get_event_loop().time())
        
        total_minutes = activity_data[user_id]["minutes"]
        await self.config.activity_data.set(activity_data)
        
        log.debug(f"Updated activity for {member.name}: {total_minutes} minutes total")
        
        # Check for military rank promotion
        await self.check_military_rank(member, total_minutes)

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
            view.message = await ctx.send(f"Discord Role `{discord_role.name}` (`{role_id}`) is already mapped to Django Game `{current_mappings[str(role_id)]}`. Do you want to update it to `{django_game_name}`?", view=view)
            await view.wait()
            if not view.result:
                return await ctx.send("Mapping update cancelled.")
        for existing_role_id_str, existing_game_name in current_mappings.items():
            if existing_game_name == django_game_name and int(existing_role_id_str) != role_id:
                existing_role = ctx.guild.get_role(int(existing_role_id_str))
                existing_role_display = existing_role.name if existing_role else f"ID: {existing_role_id_str}"
                view = ConfirmView(ctx.author)
                view.message = await ctx.send(f"Warning: The Django game name `{django_game_name}` is already mapped to Discord Role `{existing_role_display}` (`{existing_role_id_str}`).\nAre you sure you want to map `{discord_role.name}` (`{role_id}`) to the *same* Django game name?\nThis is unusual and might lead to conflicting counts if both roles represent the same game.\nConfirm to proceed.", view=view)
                await view.wait
