# zerolivesleft/webapi.py
# Complete, updated file

import logging
import os
import json
from aiohttp import web
import discord
from datetime import datetime

log = logging.getLogger("red.Elkz.zerolivesleft.webapi")

class WebApiManager:
    """Manages the aiohttp web server and API endpoints for the Zerolivesleft cog."""

    def __init__(self, cog_instance):
        self.cog = cog_instance
        self.web_app = cog_instance.web_app

    def register_all_routes(self):
        """Register all web API routes from various functionalities."""
        log.info("Registering all web API routes for Zerolivesleft cog.")
        try:
            # --- General Routes ---
            self.web_app.router.add_get("/health", self.health_check_handler)
            self.web_app.router.add_get("/guilds/{guild_id}/roles/{role_id}/members", self.get_role_members_handler)
            
            # --- Application Routes (from application_roles.py) ---
            self.web_app.router.add_post("/api/applications/update-status", self.cog.application_roles_logic.handle_application_update)
            self.web_app.router.add_post("/api/applications/submitted", self.cog.application_roles_logic.handle_application_submitted)

            # --- ActivityTracker Routes (handled within this manager) ---
            self.web_app.router.add_post("/api/assign-initial-role", self.assign_initial_role_handler)
            self.web_app.router.add_get("/api/get-military-ranks", self.get_military_ranks_handler)
            self.web_app.router.add_get("/api/get-all-activity", self.get_all_activity_handler)

            # --- User Profile Routes (from user_profile.py) ---
            # CHANGE: The /api/user/{user_id}/details endpoint will now be at /api/user/{user_id}
            self.web_app.router.add_get("/api/user/{user_id}", self.get_user_details_handler) # <--- UPDATED ROUTE

            # NEW: Route for getting just user roles
            self.web_app.router.add_get("/api/user/{user_id}/roles", self.get_user_roles_handler) # <--- NEW ROUTE ADDED

            log.info(f"Successfully registered routes for web_app.")
        except RuntimeError as e:
            log.critical(f"Failed to register web routes: {e}. Router might be frozen prematurely. This is a critical error.", exc_info=True)
        except Exception as e:
            log.critical(f"An unexpected error occurred during web route registration: {e}", exc_info=True)

    async def get_user_roles_handler(self, request: web.Request): # <--- NEW FUNCTION ADDED
        log.info("--- BOT DEBUG: /api/user/.../roles endpoint hit ---")
        try:
            await self._authenticate_request_webserver_key(request)
        except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
            log.error("BOT DEBUG: FAILED. Authentication failed.")
            return e

        user_id_str = request.match_info.get("user_id")
        log.info(f"BOT DEBUG: Received roles request for user ID: {user_id_str}")
        
        try:
            user_id = int(user_id_str)
        except (ValueError, TypeError):
            log.error("BOT DEBUG: FAILED. Invalid user_id format for roles.")
            raise web.HTTPBadRequest(reason="Invalid user_id format.")

        guild_id_from_config = await self.cog.config.ar_default_guild_id() # Assuming this is your main guild ID config
        if not guild_id_from_config:
            log.error("BOT DEBUG: FAILED. ar_default_guild_id is not set for roles.")
            raise web.HTTPInternalServerError(reason="Default Guild ID not configured on bot.")

        guild = self.cog.bot.get_guild(int(guild_id_from_config))
        if not guild:
            log.error(f"BOT DEBUG: FAILED. Bot could not find guild with ID {guild_id_from_config} for roles. Is the bot in this server?")
            raise web.HTTPNotFound(reason=f"Bot is not in the configured default guild.")

        member = guild.get_member(user_id)
        if not member:
            log.warning(f"BOT DEBUG: guild.get_member({user_id}) returned None for roles. User may not be in server or cache is incomplete.")
            log.warning("BOT DEBUG: Returning 404 Not Found for roles.")
            raise web.HTTPNotFound(reason=f"Member with ID {user_id} not found in the guild.")

        # Extract role data from the member
        role_data = [
            {"id": str(role.id), "name": role.name, "color": f"#{role.color.value:06x}"}
            for role in member.roles if role.name != "@everyone"
        ]
        
        log.info(f"BOT DEBUG: Successfully built role_data for {member.name}. Returning {len(role_data)} roles.")
        return web.json_response(role_data)
    
    async def _authenticate_request_webserver_key(self, request: web.Request):
        """Authenticates incoming web API requests using the WebServer's API key."""
        expected_key = await self.cog.config.webserver_api_key()
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
        expected_key = guild_settings.get("at_api_key")
        
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
            await self._authenticate_request_webserver_key(request)
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

        guild = self.cog.bot.get_guild(guild_id)
        if not guild:
            log.warning(f"NotFound: Guild with ID {guild_id} not found for role members request.")
            raise web.HTTPNotFound(reason=f"Guild with ID {guild_id} not found.")

        if not guild.chunked:
            await guild.chunk()

        role = guild.get_role(role_id)
        if not role:
            log.warning(f"NotFound: Role with ID {role_id} not found in guild {guild.id} for role members request.")
            raise web.HTTPNotFound(reason=f"Role with ID {role_id} not found in guild {guild.id}.")

        members_data = []
        for member in role.members:
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
        guild_id_str = os.environ.get("DISCORD_GUILD_ID")
        if not guild_id_str:
            log.critical("DISCORD_GUILD_ID environment variable not set. Cannot assign initial role.")
            raise web.HTTPInternalServerError(reason="DISCORD_GUILD_ID not set.")
        main_guild = self.cog.bot.get_guild(int(guild_id_str))
        if not main_guild:
            log.critical(f"Main guild with ID {guild_id_str} not found. Cannot assign initial role.")
            raise web.HTTPInternalServerError(reason="Main Discord guild not found.")

        try:
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
            await self._authenticate_request_guild_key(request, main_guild.id)
        except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
            return e
            
        military_ranks = await self.cog.config.guild(main_guild).at_military_ranks()
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
            await self._authenticate_request_guild_key(request, main_guild.id)
        except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
            return e
        
        user_activity_config = await self.cog.config.guild(main_guild).at_user_activity()
        
        activity_data = []
        if self.cog.activity_tracking_logic and hasattr(self.cog.activity_tracking_logic, 'voice_tracking'):
            voice_tracking_data = self.cog.activity_tracking_logic.voice_tracking
        else:
            voice_tracking_data = {}

        for user_id_str, minutes in user_activity_config.items():
            user_id = int(user_id_str)
            total_minutes = minutes
            
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

    # --- COMMANDS ---
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
        await self.cog.shutdown_webserver()
        await self.cog.initialize_webserver()
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

# Replace the existing function with this one
async def get_user_details_handler(self, request: web.Request):
    log.info("--- BOT DEBUG: /api/user/.../details endpoint hit ---")
    try:
        await self._authenticate_request_webserver_key(request)
    except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
        log.error("BOT DEBUG: FAILED. Authentication failed.")
        return e

    user_id_str = request.match_info.get("user_id")
    log.info(f"BOT DEBUG: Received request for user ID: {user_id_str}")
    
    try:
        user_id = int(user_id_str)
    except (ValueError, TypeError):
        log.error("BOT DEBUG: FAILED. Invalid user_id format.")
        raise web.HTTPBadRequest(reason="Invalid user_id format.")

    guild_id_from_config = await self.cog.config.ar_default_guild_id()
    log.info(f"BOT DEBUG: Default guild ID from config is: {guild_id_from_config}")

    if not guild_id_from_config:
        log.error("BOT DEBUG: FAILED. ar_default_guild_id is not set.")
        raise web.HTTPInternalServerError(reason="Default Guild ID not configured on bot.")

    guild = self.cog.bot.get_guild(int(guild_id_from_config))
    if not guild:
        log.error(f"BOT DEBUG: FAILED. Bot could not find guild with ID {guild_id_from_config}. Is the bot in this server?")
        raise web.HTTPNotFound(reason=f"Bot is not in the configured default guild.")

    log.info(f"BOT DEBUG: Found guild: '{guild.name}' ({guild.id})")

    # --- This is the most important check ---
    member = guild.get_member(user_id)
    if not member:
        log.warning(f"BOT DEBUG: guild.get_member({user_id}) returned None. User may not be in server or cache is incomplete.")
        log.warning("BOT DEBUG: Returning 404 Not Found.")
        raise web.HTTPNotFound(reason=f"Member with ID {user_id} not found in the guild.")

    log.info(f"BOT DEBUG: Found member: '{member.name}' ({member.id})")

    role_data = [
        {"id": str(role.id), "name": role.name, "color": f"#{role.color.value:06x}"}
        for role in member.roles if role.name != "@everyone"
    ]
    user_data = {
        "id": str(member.id), "name": member.name, "display_name": member.display_name,
        "avatar_url": str(member.display_avatar.url) if member.display_avatar else None,
        "roles": role_data
    }
    log.info(f"BOT DEBUG: Successfully built user_data. Returning 200 OK.")
    return web.json_response(user_data)