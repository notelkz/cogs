# zerolivesleft/webapi.py

import logging
import os
import json
from aiohttp import web
import discord # Import discord for type hints in commands
from datetime import datetime  # Added missing import

log = logging.getLogger("red.Elkz.zerolivesleft.webapi")

class WebApiManager:
    """Manages the aiohttp web server and API endpoints for the Zerolivesleft cog."""

    # In zerolivesleft/webapi.py

    async def get_members_by_roles(self, request):
        """
        Takes a POST request with a list of role IDs and returns all members
        who have at least one of those roles.
        """
        if request.headers.get("X-API-Key") != await self.cog.config.webserver_api_key():
            return web.json_response({"error": "Unauthorized"}, status=401)
        
        try:
            data = await request.json()
            role_ids_to_find = [int(r_id) for r_id in data.get("role_ids", [])]
        except (ValueError, TypeError, json.JSONDecodeError):
            return web.json_response({"error": "Invalid payload format"}, status=400)

        if not role_ids_to_find:
            return web.json_response({"error": "No role_ids provided"}, status=400)
            
        guild_id = await self.cog.config.ar_default_guild_id() # Using the application guild for this
        guild = self.cog.bot.get_guild(int(guild_id))
        if not guild:
            return web.json_response({"error": "Guild not found"}, status=500)
            
        ranked_members = []
        for member in guild.members:
            if member.bot:
                continue
            
            # Find the member's highest role from the provided list
            member_role_ids = {r.id for r in member.roles}
            matching_role_ids = member_role_ids.intersection(role_ids_to_find)

            if matching_role_ids:
                ranked_members.append({
                    "id": member.id,
                    "name": member.name,
                    "display_name": member.display_name,
                    "avatar_url": str(member.display_avatar.url),
                })
        
        return web.json_response(ranked_members)

    def __init__(self, cog_instance):
        self.cog = cog_instance # Reference to the main Zerolivesleft cog
        self.web_app = cog_instance.web_app # Reference to the central aiohttp.web.Application

    def register_all_routes(self):
        """Register all web API routes from various functionalities."""
        log.info("Registering all web API routes for Zerolivesleft cog.")
        try:
            # Routes from GameCounter (for roster fetching)
            self.web_app.router.add_get(
                "/guilds/{guild_id}/roles/{role_id}/members", 
                self.get_role_members_handler
            )

            # Routes from ActivityTracker (for initial role, military ranks, all activity)
            self.web_app.router.add_post(
                "/api/assign-initial-role", 
                self.assign_initial_role_handler
            )
            self.web_app.router.add_get(
                "/api/get-military-ranks", 
                self.get_military_ranks_handler
            )
            self.web_app.router.add_get(
                "/api/get-all-activity", 
                self.get_all_activity_handler
            )

            # Health check route (from WebServer)
            self.web_app.router.add_get("/health", self.health_check_handler)

            log.info(f"Successfully registered routes for web_app.")
        except RuntimeError as e:
            log.critical(f"Failed to register web routes: {e}. Router might be frozen prematurely. This is a critical error.", exc_info=True)
        except Exception as e:
            log.critical(f"An unexpected error occurred during web route registration: {e}", exc_info=True)


    async def _authenticate_request_webserver_key(self, request: web.Request):
        """Authenticates incoming web API requests using the WebServer's API key."""
        expected_key = await self.cog.config.webserver_api_key() # Use central config
        if not expected_key:
            log.warning("Web API Key not configured in Zerolivesleft cog for incoming requests.")
            raise web.HTTPUnauthorized(reason="Web API Key not configured on RedBot.")
        
        provided_key = request.headers.get("X-API-Key")
        if not provided_key or provided_key != expected_key:
            log.warning(f"Invalid API Key provided for incoming request: {provided_key}. Expected: {expected_key}")
            raise web.HTTPForbidden(reason="Invalid API Key.")
        
        return True

    async def _authenticate_request_guild_key(self, request: web.Request, guild_id: int):
        """Authenticates incoming web API requests using the guild-specific API key (for ActivityTracker)."""
        guild_settings = await self.cog.config.guild(self.cog.bot.get_guild(guild_id)).all()
        expected_key = guild_settings.get("at_api_key") # Use ActivityTracker's API key from central config
        
        if not expected_key:
            log.warning(f"ActivityTracker API Key not configured for guild {guild_id} for incoming requests.")
            raise web.HTTPUnauthorized(reason="ActivityTracker API Key not configured on RedBot for this guild.")
        
        provided_key = request.headers.get("X-API-Key")
        if not provided_key or provided_key != expected_key:
            log.warning(f"Invalid ActivityTracker API Key provided for incoming request: {provided_key}. Expected: {expected_key}")
            raise web.HTTPForbidden(reason="Invalid API Key.")
        
        return True


    # --- API HANDLERS ---

    async def health_check_handler(self, request: web.Request):
        """Handles /health endpoint."""
        return web.Response(text="OK", status=200)

    async def get_role_members_handler(self, request: web.Request):
        """Web API handler to return members of a specific Discord role (from RoleCounter)."""
        try:
            await self._authenticate_request_webserver_key(request) # Authenticate using WebServer's key
        except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
            return e

        guild_id_str = request.match_info.get("guild_id")
        role_id_str = request.match_info.get("role_id")

        try:
            guild_id = int(guild_id_str)
            role_id = int(role_id_str)
        except (ValueError, TypeError):
            log.warning(f"BadRequest: Invalid guild_id ({guild_id_str}) or role_id ({role_id_str}) format.")
            raise web.HTTPBadRequest(reason="Invalid guild_id or role_id format.")

        guild = self.cog.bot.get_guild(guild_id) # Use main cog's bot instance
        if not guild:
            log.warning(f"NotFound: Guild with ID {guild_id} not found for role members request.")
            raise web.HTTPNotFound(reason=f"Guild with ID {guild_id} not found.")

        if not guild.chunked:
            await guild.chunk() # Ensure guild members are cached

        role = guild.get_role(role_id)
        if not role:
            log.warning(f"NotFound: Role with ID {role_id} not found in guild {guild.id} for role members request.")
            raise web.HTTPNotFound(reason=f"Role with ID {role_id} not found in guild {guild.id}.")

        members_data = []
        for member in role.members:
            # Check for streaming activity (from ActivityTracker logic if it were integrated, but simplified for direct data)
            streaming_activity = next((a for a in member.activities if isinstance(a, discord.Streaming)), None)
            members_data.append({
                "id": str(member.id),
                "name": member.name,
                "display_name": member.display_name,
                "avatar_url": str(member.display_avatar.url) if member.display_avatar else None,
                "is_live": streaming_activity is not None,
                "twitch_url": streaming_activity.url if streaming_activity else f"https://www.twitch.tv/{member.name}"
            })
        
        log.info(f"Successfully returned {len(members_data)} members for role {role_id} in guild {guild_id}.")
        return web.json_response(members_data)

    async def assign_initial_role_handler(self, request: web.Request):
        """Assigns an initial role to a new member based on website request (from ActivityTracker)."""
        guild_id_str = os.environ.get("DISCORD_GUILD_ID") # Rely on env for main guild ID
        if not guild_id_str:
            log.critical("DISCORD_GUILD_ID environment variable not set. Cannot assign initial role.")
            raise web.HTTPInternalServerError(reason="DISCORD_GUILD_ID not set.")
        main_guild = self.cog.bot.get_guild(int(guild_id_str))
        if not main_guild:
            log.critical(f"Main guild with ID {guild_id_str} not found. Cannot assign initial role.")
            raise web.HTTPInternalServerError(reason="Main Discord guild not found.")

        try:
            # Authenticate using the guild-specific API key (from ActivityTracker's settings)
            await self._authenticate_request_guild_key(request, main_guild.id)
        except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
            return e

        try:
            data = await request.json()
            discord_id = int(data.get("discord_id"))
        except (ValueError, TypeError, json.JSONDecodeError):
            log.warning("BadRequest: Invalid request data for assign_initial_role_handler.")
            raise web.HTTPBadRequest(reason="Invalid request data.")

        recruit_role_id = await self.cog.config.guild(main_guild).at_recruit_role_id()
        if not recruit_role_id:
            log.error("Recruit role not configured in Zerolivesleft cog. Cannot assign initial role.")
            raise web.HTTPInternalServerError(reason="Recruit role not configured.")
        
        member = main_guild.get_member(discord_id)
        recruit_role = main_guild.get_role(recruit_role_id)

        if member and recruit_role:
            try:
                if recruit_role not in member.roles:
                    await member.add_roles(recruit_role, reason="Initial role assignment from website.")
                    log.info(f"Assigned recruit role to {member.name} ({member.id}).")
                else:
                    log.info(f"{member.name} ({member.id}) already has recruit role. Skipping assignment.")
                return web.Response(text="Role assigned/already present successfully", status=200)
            except discord.Forbidden:
                log.error(f"Bot lacks permissions to assign role {recruit_role.name} to {member.name} in guild {main_guild.name}.")
                raise web.HTTPServiceUnavailable(reason="Bot missing permissions to assign role.")
            except Exception as e:
                log.exception(f"Error assigning initial role to {member.name}: {e}")
                raise web.HTTPInternalServerError(reason="Internal server error during role assignment.")
        else:
            log.warning(f"NotFound: Member ({discord_id}) or recruit role ({recruit_role_id}) not found for initial role assignment.")
            raise web.HTTPNotFound(reason="Member or recruit role not found.")


    async def get_military_ranks_handler(self, request: web.Request):
        """Returns configured military ranks (from ActivityTracker)."""
        guild_id_str = os.environ.get("DISCORD_GUILD_ID")
        if not guild_id_str:
            log.critical("DISCORD_GUILD_ID environment variable not set. Cannot get military ranks.")
            raise web.HTTPInternalServerError(reason="DISCORD_GUILD_ID not set.")
        main_guild = self.cog.bot.get_guild(int(guild_id_str))
        if not main_guild:
            log.critical(f"Main guild with ID {guild_id_str} not found. Cannot get military ranks.")
            raise web.HTTPInternalServerError(reason="Main Discord guild not found.")

        try:
            # Authenticate using the guild-specific API key (from ActivityTracker's settings)
            await self._authenticate_request_guild_key(request, main_guild.id)
        except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
            return e
            
        military_ranks = await self.cog.config.guild(main_guild).at_military_ranks() # Use central config
        if not military_ranks:
            return web.json_response([], status=200)
        try:
            sorted_ranks = sorted(
                [r for r in military_ranks if 'required_hours' in r and isinstance(r['required_hours'], (int, float))],
                key=lambda x: x['required_hours']
            )
        except Exception as e:
            log.exception(f"Internal Server Error: Malformed rank data in config for guild {main_guild.id}: {e}")
            raise web.HTTPInternalServerError(reason="Internal Server Error: Malformed rank data.")
        log.info(f"Successfully returned {len(sorted_ranks)} military ranks for guild {main_guild.id}.")
        return web.json_response(sorted_ranks)

    async def get_all_activity_handler(self, request: web.Request):
        """API endpoint to get all user activity data (from ActivityTracker)."""
        guild_id_str = os.environ.get("DISCORD_GUILD_ID")
        if not guild_id_str:
            log.critical("DISCORD_GUILD_ID not set in environment for get_all_activity_handler.")
            raise web.HTTPInternalServerError(reason="DISCORD_GUILD_ID not set")
        main_guild = self.cog.bot.get_guild(int(guild_id_str))
        if not main_guild:
            log.critical(f"Main guild with ID {guild_id_str} not found for get_all_activity_handler.")
            raise web.HTTPInternalServerError(reason="Main Discord guild not found")
        
        try:
            # Authenticate using the guild-specific API key (from ActivityTracker's settings)
            await self._authenticate_request_guild_key(request, main_guild.id)
        except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
            return e
        
        user_activity_config = await self.cog.config.guild(main_guild).at_user_activity()
        
        activity_data = []
        # Ensure cog.activity_tracking_logic is initialized before accessing voice_tracking
        if self.cog.activity_tracking_logic and hasattr(self.cog.activity_tracking_logic, 'voice_tracking'):
            voice_tracking_data = self.cog.activity_tracking_logic.voice_tracking
        else:
            voice_tracking_data = {} # Fallback if not initialized or attribute missing

        for user_id_str, minutes in user_activity_config.items():
            user_id = int(user_id_str)
            total_minutes = minutes
            
            # Add current session time if user is in voice (from ActivityTrackingLogic)
            if main_guild.id in voice_tracking_data and user_id in voice_tracking_data[main_guild.id]:
                join_time = voice_tracking_data[main_guild.id][user_id]
                current_session_minutes = int((datetime.utcnow() - join_time).total_seconds() / 60)
                if current_session_minutes >= 1:
                    total_minutes += current_session_minutes
            
            activity_data.append({
                "discord_id": user_id_str,
                "minutes": total_minutes
            })
        
        log.info(f"Successfully returned {len(activity_data)} activity records for guild {main_guild.id}.")
        return web.json_response(activity_data)

    # --- COMMANDS (These are not @commands.command() directly, but are called by main cog) ---
    # These methods are designed to be called from the main cog's command definitions.

    async def set_host_command(self, ctx, host: str):
        """Set the host for the web server."""
        await self.cog.config.webserver_host.set(host)
        await ctx.send(f"Web server host set to {host}. Reload the cog for changes to take effect.")

    async def set_port_command(self, ctx, port: int):
        """Set the port for the web server."""
        if not (1024 <= port <= 65535):
            return await ctx.send("Port must be between 1024 and 65535.")
        await self.cog.config.webserver_port.set(port)
        await ctx.send(f"Web server port set to {port}. Reload the cog for changes to take effect.")

    async def set_apikey_command(self, ctx, *, api_key: str):
        """Set the API key for the web server."""
        await self.cog.config.webserver_api_key.set(api_key)
        await ctx.send("API key set. This will be used for all cogs that use the web server.")
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    async def restart_server_command(self, ctx):
        """Restart the web server."""
        await ctx.send("Restarting web server...")
        await self.cog.shutdown_webserver() # Call main cog's shutdown
        await self.cog.initialize_webserver() # Call main cog's initialize
        await ctx.send("Web server restarted.")

    async def show_config_command(self, ctx):
        """Show the current web server configuration."""
        host = await self.cog.config.webserver_host()
        port = await self.cog.config.webserver_port()
        api_key = await self.cog.config.webserver_api_key()
        try:
            await ctx.author.send(f"**Web Server Configuration**\n- Host: `{host}`\n- Port: `{port}`\n- API Key: `{api_key if api_key else 'Not set'}`")
            await ctx.send("Configuration sent to your DMs.")
        except discord.Forbidden:
            await ctx.send(f"**Web Server Configuration**\n- Host: `{host}`\n- Port: `{port}`\n- API Key: `{'Set' if api_key else 'Not set'}`")
