# /home/elkz/.local/share/Red-DiscordBot/data/zerolivesleft/cogs/CogManager/cogs/gamecounter/gamecounter.py

import discord
import asyncio
import json
import aiohttp
from urllib.parse import urljoin
from aiohttp import web
from redbot.core import commands, Config, app_commands
from redbot.core.utils.chat_formatting import humanize_list 
from redbot.core.utils.views import ConfirmView
from redbot.core.bot import Red
from discord.ext import tasks
import logging

log = logging.getLogger("red.Elkz.gamecounter")

class GameCounter(commands.Cog):
    """
    Periodically counts users with specific game roles and sends the data to a Django website API.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        self.config = Config.get_conf(
            self, identifier=123456789012345, force_registration=True
        )
        # --- SIMPLIFIED CONFIG ---
        # Only the settings needed for counting and reporting are kept.
        self.config.register_global(
            api_base_url=None,  # e.g., https://zerolivesleft.net/api/
            api_key=None,       # The one key your Django site expects (REDBOT_API_KEY)
            interval=15,
            guild_id=None,
            game_role_mappings={}
        )
        
        self.count_and_update.start()
        # Removed: asyncio.create_task(self.initialize()) as it's no longer needed.

    # Removed: async def initialize(self): - This function is no longer part of the class.

    def cog_unload(self):
        """Cleanup when the cog is unloaded."""
        if self.count_and_update.is_running():
            self.count_and_update.cancel()
        asyncio.create_task(self.session.close())

    async def _authenticate_request(self, request: web.Request):
        """Authenticates incoming web API requests using the WebServer cog's API key."""
        webserver_cog = self.bot.get_cog("WebServer")
        if not webserver_cog:
            raise web.HTTPInternalServerError(reason="Authentication service is unavailable.")

        expected_key = await webserver_cog.config.api_key()
        if not expected_key:
            raise web.HTTPUnauthorized(reason="Web API Key not configured on RedBot.")
        
        provided_key = request.headers.get("X-API-Key")
        if not provided_key or provided_key != expected_key:
            raise web.HTTPForbidden(reason="Invalid API Key.")
        
        return True

    async def get_role_members_handler(self, request: web.Request):
        """Web API handler to return members of a specific Discord role."""
        try:
            await self._authenticate_request(request)
        except (web.HTTPUnauthorized, web.HTTPForbidden, web.HTTPInternalServerError) as e:
            log.warning(f"Authentication failed for /guilds/roles/members endpoint: {e.reason}")
            return e

        guild_id_str = request.match_info.get("guild_id")
        role_id_str = request.match_info.get("role_id")

        try:
            guild_id = int(guild_id_str)
            role_id = int(role_id_str)
        except (ValueError, TypeError):
            raise web.HTTPBadRequest(reason="Invalid guild_id or role_id format.")

        guild = self.bot.get_guild(guild_id)
        if not guild:
            raise web.HTTPNotFound(reason=f"Guild with ID {guild_id} not found.")

        if not guild.chunked:
            await guild.chunk()

        role = guild.get_role(role_id)
        if not role:
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
        
        return web.json_response(members_data)

    @tasks.loop(minutes=15)
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
            for role_id_str, game_name in mappings.items():
                role = guild.get_role(int(role_id_str))
                if role:
                    game_counts[game_name] = len(role.members)
            
            api_base_url = await self.config.api_base_url()
            api_key = await self.config.api_key()
            
            if api_base_url and api_key and game_counts:
                update_url = urljoin(api_base_url, "update-game-counts/")
                headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
                payload = {"game_counts": game_counts}
                async with self.session.post(update_url, json=payload, headers=headers) as response:
                    if response.status != 200:
                        log.error(f"Failed to send game counts. Status: {response.status}, Response: {await response.text()}")
                    else:
                        log.info(f"Successfully sent game counts to website: {game_counts}")

        except Exception as e:
            log.error(f"Error in count_and_update loop: {e}", exc_info=True)

    # --- Commands ---
    
    @commands.hybrid_group(name="gamecounter", aliases=["gc"])
    @commands.is_owner()
    async def gamecounter_settings(self, ctx: commands.Context):
        """Manage the GameCounter settings."""
        pass

    @gamecounter_settings.command(name="setapiurl")
    async def set_api_url(self, ctx: commands.Context, url: str):
        """Sets the base Django API URL (e.g., https://zerolivesleft.net/api/)."""
        if not url.startswith("http"):
            return await ctx.send("Please provide a valid URL starting with `http://` or `https://`.")
        if not url.endswith('/'):
            url += '/'
        await self.config.api_base_url.set(url)
        await ctx.send(f"Django API Base URL set to: `{url}`")

    @gamecounter_settings.command(name="setapikey")
    async def set_api_key(self, ctx: commands.Context, *, key: str):
        """Sets the secret API key for authenticating with your Django endpoint."""
        await self.config.api_key.set(key)
        await ctx.send("Django API Key has been set.")
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @gamecounter_settings.command(name="setinterval")
    async def set_interval(self, ctx: commands.Context, minutes: int):
        """Sets the interval (in minutes) for the counter to run."""
        if minutes < 1:
            return await ctx.send("Interval must be at least 1 minute.")
        await self.config.interval.set(minutes)
        self.count_and_update.change_interval(minutes=minutes)
        await ctx.send(f"Counter interval set to `{minutes}` minutes. Loop restarted.")

    @gamecounter_settings.command(name="setguild")
    async def set_guild(self, ctx: commands.Context, guild: discord.Guild):
        """Sets the guild where game roles should be counted."""
        await self.config.guild_id.set(guild.id)
        await ctx.send(f"Counting guild set to **{guild.name}** (`{guild.id}`).")

    @gamecounter_settings.command(name="addmapping")
    async def add_mapping(self, ctx: commands.Context, role: discord.Role, *, game_name: str):
        """Adds a mapping between a Discord Role and a Django GameCategory name."""
        async with self.config.game_role_mappings() as mappings:
            mappings[str(role.id)] = game_name
        await ctx.send(f"Mapping added: Role `{role.name}` -> Game `{game_name}`")

    @gamecounter_settings.command(name="removemapping")
    async def remove_mapping(self, ctx: commands.Context, role: discord.Role):
        """Removes a mapping for a Discord Role."""
        async with self.config.game_role_mappings() as mappings:
            if str(role.id) in mappings:
                del mappings[str(role.id)]
                await ctx.send(f"Mapping removed for role `{role.name}`.")
            else:
                await ctx.send("No mapping found for that role.")

    @gamecounter_settings.command(name="listmappings")
    async def list_mappings(self, ctx: commands.Context):
        """Lists all current role-to-game mappings."""
        mappings = await self.config.game_role_mappings()
        if not mappings:
            return await ctx.send("No mappings configured.")
        
        guild_id = await self.config.guild_id()
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return await ctx.send("Counting guild not set or not found. Please set it with `[p]gamecounter setguild`.")

        msg = "**Current Role to Game Mappings:**\n"
        for role_id, game_name in mappings.items():
            role = guild.get_role(int(role_id))
            role_name = f"`{role.name}`" if role else "`Unknown Role (ID not found in server)`"
            msg += f"- {role_name} (ID: `{role_id}`) -> `{game_name}`\n"
        await ctx.send(msg)

    @gamecounter_settings.command(name="showconfig")
    async def show_config(self, ctx: commands.Context):
        """Shows the current GameCounter configuration."""
        config_data = await self.config.all()
        
        api_key_masked = "Set" if config_data.get("api_key") else "Not Set"
        guild_id = config_data.get("guild_id")
        guild = self.bot.get_guild(guild_id) if guild_id else None
        
        embed = discord.Embed(
            title="GameCounter Configuration",
            description="Settings for counting game roles and reporting to your website.",
            color=discord.Color.blue()
        )
        
        embed.add_field(name="API Base URL", value=config_data.get("api_base_url") or "Not Set", inline=False)
        embed.add_field(name="API Key", value=api_key_masked, inline=True)
        embed.add_field(name="Update Interval", value=f"{config_data.get('interval')} minutes", inline=True)
        embed.add_field(name="Counting Guild", value=f"{guild.name if guild else 'Not Set'} (`{guild_id if guild_id else 'Not Set'}`)", inline=False)
        
        loop_status = "Running" if self.count_and_update.is_running() else "Stopped"
        embed.add_field(name="Counter Loop Status", value=loop_status, inline=False)
        
        await ctx.send(embed=embed)

async def setup(bot: Red):
    """Set up the GameCounter cog."""
    cog = GameCounter(bot)
    webserver_cog = bot.get_cog("WebServer")
    if webserver_cog:
        routes = [
            web.get("/guilds/{guild_id}/roles/{role_id}/members", cog.get_role_members_handler),
        ]
        # This will now call WebServer's modified add_routes which queues routes
        # if the router is already frozen.
        webserver_cog.add_routes(routes)
        log.info("Attempted to register GameCounter routes with the WebServer cog.") # Log reflects direct attempt
    else:
        log.error("WebServer cog not found. GameCounter API endpoints will not be available.")
    await bot.add_cog(cog)