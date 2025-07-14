# /home/elkz/.local/share/Red-DiscordBot/data/zerolivesleft/cogs/CogManager/cogs/gamecounter/gamecounter.py

import discord
import asyncio
import json
import aiohttp
import os
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
    Periodically counts users with specific Discord roles and sends the data to a Django website API.
    Also serves a read-only API for Discord role members for the website.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        self.config = Config.get_conf(
            self, identifier=123456789012345, force_registration=True
        )
        # --- SIMPLIFIED CONFIG ---
        # We now only use one base URL and one key for all communication TO the website.
        self.config.register_global(
            api_base_url=None,  # e.g., https://zerolivesleft.net/api/
            api_key=None,       # The one key your Django site expects (REDBOT_API_KEY)
            interval=15,
            guild_id=None,
            game_role_mappings={},
            activity_data={}
        )
        
        self.count_and_update.start()
        asyncio.create_task(self.initialize())

    async def initialize(self):
        """Waits for the bot to be ready and then registers API routes."""
        await self.bot.wait_until_ready()
        
        webserver_cog = self.bot.get_cog("WebServer")
        if not webserver_cog:
            log.error("WebServer cog not found. GameCounter API endpoints will not be available.")
            return

        routes = [
            web.get("/guilds/{guild_id}/roles/{role_id}/members", self.get_role_members_handler),
            web.get("/api/get_time_ranks/", self.get_time_ranks_handler)
        ]
        
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

    async def get_time_ranks_handler(self, request: web.Request):
        """Handler for the time ranks API endpoint."""
        try:
            await self._authenticate_request(request)
        except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
            log.warning(f"Authentication failed for /api/get_time_ranks/ endpoint: {e.reason}")
            return e
            
        military_ranks = [
            {"name": "Private", "role_id": "1274274605435060224", "minutes_required": 10 * 60},
            {"name": "Private First Class", "role_id": "1274274696048934965", "minutes_required": 25 * 60},
            {"name": "Corporal", "role_id": "1274771534119964813", "minutes_required": 50 * 60},
            {"name": "Specialist", "role_id": "1274771654907658402", "minutes_required": 75 * 60},
            {"name": "Sergeant", "role_id": "1274771991748022276", "minutes_required": 100 * 60},
            {"name": "Staff Sergeant", "role_id": "1274772130424164384", "minutes_required": 150 * 60},
            {"name": "Sergeant First Class", "role_id": "1274772191107485706", "minutes_required": 225 * 60},
            {"name": "Master Sergeant", "role_id": "1274772252545519708", "minutes_required": 300 * 60},
            {"name": "First Sergeant", "role_id": "1274772335689465978", "minutes_required": 375 * 60},
            {"name": "Sergeant Major", "role_id": "1274772419927605299", "minutes_required": 450 * 60},
            {"name": "Command Sergeant Major", "role_id": "1274772500164640830", "minutes_required": 550 * 60},
            {"name": "Sergeant Major of the Army", "role_id": "1274772595031539787", "minutes_required": 650 * 60},
            {"name": "Warrant Officer 1", "role_id": "1358212838631407797", "minutes_required": 750 * 60},
            {"name": "Chief Warrant Officer 2", "role_id": "1358213159583875172", "minutes_required": 875 * 60},
            {"name": "Chief Warrant Officer 3", "role_id": "1358213229112852721", "minutes_required": 1000 * 60},
            {"name": "Chief Warrant Officer 4", "role_id": "1358213408704430150", "minutes_required": 1200 * 60},
            {"name": "Chief Warrant Officer 5", "role_id": "1358213451289460847", "minutes_required": 1400 * 60},
            {"name": "Second Lieutenant", "role_id": "1358213662216814784", "minutes_required": 1600 * 60},
            {"name": "First Lieutenant", "role_id": "1358213759805554979", "minutes_required": 1850 * 60},
            {"name": "Captain", "role_id": "1358213809466118276", "minutes_required": 2100 * 60},
            {"name": "Major", "role_id": "1358213810598449163", "minutes_required": 2400 * 60},
            {"name": "Lieutenant Colonel", "role_id": "1358213812175503430", "minutes_required": 2750 * 60},
            {"name": "Colonel", "role_id": "1358213813140459520", "minutes_required": 3100 * 60},
            {"name": "Brigadier General", "role_id": "1358213814234906786", "minutes_required": 3500 * 60},
            {"name": "Major General", "role_id": "1358213815203795004", "minutes_required": 4000 * 60},
            {"name": "Lieutenant General", "role_id": "1358213817229770783", "minutes_required": 4500 * 60},
            {"name": "General", "role_id": "1358213815983935608", "minutes_required": 5000 * 60},
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
            await guild.chunk()

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
        
        return web.json_response(members_with_status)

    async def check_military_rank(self, member, minutes):
        """Check and assign military rank based on playtime."""
        try:
            if not member or not member.guild:
                return
            
            military_ranks = [
                {"name": "Private", "role_id": "1274274605435060224", "minutes_required": 10 * 60},
                {"name": "Private First Class", "role_id": "1274274696048934965", "minutes_required": 25 * 60},
                {"name": "Corporal", "role_id": "1274771534119964813", "minutes_required": 50 * 60},
                {"name": "Specialist", "role_id": "1274771654907658402", "minutes_required": 75 * 60},
                {"name": "Sergeant", "role_id": "1274771991748022276", "minutes_required": 100 * 60},
                {"name": "Staff Sergeant", "role_id": "1274772130424164384", "minutes_required": 150 * 60},
                {"name": "Sergeant First Class", "role_id": "1274772191107485706", "minutes_required": 225 * 60},
                {"name": "Master Sergeant", "role_id": "1274772252545519708", "minutes_required": 300 * 60},
                {"name": "First Sergeant", "role_id": "1274772335689465978", "minutes_required": 375 * 60},
                {"name": "Sergeant Major", "role_id": "1274772419927605299", "minutes_required": 450 * 60},
                {"name": "Command Sergeant Major", "role_id": "1274772500164640830", "minutes_required": 550 * 60},
                {"name": "Sergeant Major of the Army", "role_id": "1274772595031539787", "minutes_required": 650 * 60},
                {"name": "Warrant Officer 1", "role_id": "1358212838631407797", "minutes_required": 750 * 60},
                {"name": "Chief Warrant Officer 2", "role_id": "1358213159583875172", "minutes_required": 875 * 60},
                {"name": "Chief Warrant Officer 3", "role_id": "1358213229112852721", "minutes_required": 1000 * 60},
                {"name": "Chief Warrant Officer 4", "role_id": "1358213408704430150", "minutes_required": 1200 * 60},
                {"name": "Chief Warrant Officer 5", "role_id": "1358213451289460847", "minutes_required": 1400 * 60},
                {"name": "Second Lieutenant", "role_id": "1358213662216814784", "minutes_required": 1600 * 60},
                {"name": "First Lieutenant", "role_id": "1358213759805554979", "minutes_required": 1850 * 60},
                {"name": "Captain", "role_id": "1358213809466118276", "minutes_required": 2100 * 60},
                {"name": "Major", "role_id": "1358213810598449163", "minutes_required": 2400 * 60},
                {"name": "Lieutenant Colonel", "role_id": "1358213812175503430", "minutes_required": 2750 * 60},
                {"name": "Colonel", "role_id": "1358213813140459520", "minutes_required": 3100 * 60},
                {"name": "Brigadier General", "role_id": "1358213814234906786", "minutes_required": 3500 * 60},
                {"name": "Major General", "role_id": "1358213815203795004", "minutes_required": 4000 * 60},
                {"name": "Lieutenant General", "role_id": "1358213817229770783", "minutes_required": 4500 * 60},
                {"name": "General", "role_id": "1358213815983935608", "minutes_required": 5000 * 60},
                {"name": "General of the Army", "role_id": "1358213816617275483", "minutes_required": 6000 * 60},
            ]
            
            eligible_rank = None
            for rank in military_ranks:
                if minutes >= rank["minutes_required"]:
                    if not eligible_rank or rank["minutes_required"] > eligible_rank["minutes_required"]:
                        eligible_rank = rank
            
            if not eligible_rank:
                return
            
            role = member.guild.get_role(int(eligible_rank["role_id"]))
            if not role or role in member.roles:
                return
            
            for rank_to_remove in military_ranks:
                existing_role = member.guild.get_role(int(rank_to_remove["role_id"]))
                if existing_role and existing_role in member.roles:
                    await member.remove_roles(existing_role)
            
            await member.add_roles(role)
            log.info(f"Assigned {eligible_rank['name']} rank to {member.name}")
            
            api_base_url = await self.config.api_base_url()
            api_key = await self.config.api_key()
            
            if api_base_url and api_key:
                update_url = urljoin(api_base_url, "update-role/")
                payload = {"discord_id": str(member.id), "rank_name": eligible_rank["name"]}
                headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
                async with self.session.post(update_url, json=payload, headers=headers) as response:
                    if response.status != 200:
                        log.error(f"API ERROR: Failed to update military rank on website for {member.id}: {response.status} - {await response.text()}")
        except Exception as e:
            log.error(f"MILITARY ERROR: Failed to check/assign military rank for {member.name}: {str(e)}")

    async def update_member_activity(self, member, minutes_to_add=5):
        """Update a member's activity time and check for promotions."""
        if not member or member.bot:
            return
        async with self.config.activity_data() as activity_data:
            user_id = str(member.id)
            if user_id not in activity_data:
                activity_data[user_id] = {"minutes": 0}
            activity_data[user_id]["minutes"] += minutes_to_add
            total_minutes = activity_data[user_id]["minutes"]
        
        await self.check_military_rank(member, total_minutes)

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
            
            api_base_url = await self.config.api_base_url()
            api_key = await self.config.api_key()
            
            if api_base_url and api_key:
                update_url = urljoin(api_base_url, "update-game-counts/")
                headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
                payload = {"game_counts": game_counts}
                async with self.session.post(update_url, json=payload, headers=headers) as response:
                    if response.status != 200:
                        log.error(f"Failed to send game counts. Status: {response.status}, Response: {await response.text()}")
        except Exception as e:
            log.error(f"Error in count_and_update: {e}", exc_info=True)

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
    async def set_guild(self, ctx: commands.Context, guild_id: int):
        """Sets the guild ID where game roles should be counted."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return await ctx.send(f"Could not find a guild with ID `{guild_id}`.")
        await self.config.guild_id.set(guild_id)
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
        
        guild = ctx.guild
        msg = "**Current Role to Game Mappings:**\n"
        for role_id, game_name in mappings.items():
            role = guild.get_role(int(role_id))
            role_name = f"`{role.name}`" if role else "`Unknown Role`"
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
            description="Settings for communication from the Bot TO your website.",
            color=discord.Color.blue()
        )
        
        embed.add_field(name="API Base URL", value=config_data.get("api_base_url") or "Not Set", inline=False)
        embed.add_field(name="API Key", value=api_key_masked, inline=True)
        embed.add_field(name="Update Interval", value=f"{config_data.get('interval')} minutes", inline=True)
        embed.add_field(name="Counting Guild", value=f"{guild.name if guild else 'Not Set'} (`{guild_id if guild_id else 'Not Set'}`)", inline=False)
        
        loop_status = "Running" if self.count_and_update.is_running() else "Stopped"
        embed.add_field(name="Counter Loop Status", value=loop_status, inline=False)
        
        await ctx.send(embed=embed)

    @gamecounter_settings.command(name="resetactivity")
    async def reset_activity(self, ctx: commands.Context, user: discord.Member = None):
        """Reset activity data for a user or all users."""
        if user:
            async with self.config.activity_data() as activity_data:
                if str(user.id) in activity_data:
                    del activity_data[str(user.id)]
                    await ctx.send(f"Activity data reset for {user.mention}.")
                else:
                    await ctx.send(f"No activity data found for {user.mention}.")
        else:
            view = ConfirmView(ctx.author)
            await view.prompt(ctx, "Are you sure you want to reset ALL activity data? This cannot be undone.")
            if view.result:
                await self.config.activity_data.set({})
                await ctx.send("All activity data has been reset.")
            else:
                await ctx.send("Reset cancelled.")

    @gamecounter_settings.command(name="viewactivity")
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
        
        military_ranks = [
            {"name": "Private", "role_id": 1274274605435060224, "minutes_required": 10 * 60},
            {"name": "Private First Class", "role_id": 1274274696048934965, "minutes_required": 25 * 60},
            {"name": "Corporal", "role_id": 1274771534119964813, "minutes_required": 50 * 60},
            {"name": "Specialist", "role_id": 1274771654907658402, "minutes_required": 75 * 60},
            {"name": "Sergeant", "role_id": 1274771991748022276, "minutes_required": 100 * 60},
            {"name": "Staff Sergeant", "role_id": 1274772130424164384, "minutes_required": 150 * 60},
            {"name": "Sergeant First Class", "role_id": 1274772191107485706, "minutes_required": 225 * 60},
            {"name": "Master Sergeant", "role_id": 1274772252545519708, "minutes_required": 300 * 60},
            {"name": "First Sergeant", "role_id": 1274772335689465978, "minutes_required": 375 * 60},
            {"name": "Sergeant Major", "role_id": 1274772419927605299, "minutes_required": 450 * 60},
            {"name": "Command Sergeant Major", "role_id": 1274772500164640830, "minutes_required": 550 * 60},
            {"name": "Sergeant Major of the Army", "role_id": 1274772595031539787, "minutes_required": 650 * 60},
            {"name": "Warrant Officer 1", "role_id": 1358212838631407797, "minutes_required": 750 * 60},
            {"name": "Chief Warrant Officer 2", "role_id": 1358213159583875172, "minutes_required": 875 * 60},
            {"name": "Chief Warrant Officer 3", "role_id": 1358213229112852721, "minutes_required": 1000 * 60},
            {"name": "Chief Warrant Officer 4", "role_id": 1358213408704430150, "minutes_required": 1200 * 60},
            {"name": "Chief Warrant Officer 5", "role_id": 1358213451289460847, "minutes_required": 1400 * 60},
            {"name": "Second Lieutenant", "role_id": 1358213662216814784, "minutes_required": 1600 * 60},
            {"name": "First Lieutenant", "role_id": 1358213759805554979, "minutes_required": 1850 * 60},
            {"name": "Captain", "role_id": 1358213809466118276, "minutes_required": 2100 * 60},
            {"name": "Major", "role_id": 1358213810598449163, "minutes_required": 2400 * 60},
            {"name": "Lieutenant Colonel", "role_id": 1358213812175503430, "minutes_required": 2750 * 60},
            {"name": "Colonel", "role_id": 1358213813140459520, "minutes_required": 3100 * 60},
            {"name": "Brigadier General", "role_id": 1358213814234906786, "minutes_required": 3500 * 60},
            {"name": "Major General", "role_id": 1358213815203795004, "minutes_required": 4000 * 60},
            {"name": "Lieutenant General", "role_id": 1358213817229770783, "minutes_required": 4500 * 60},
            {"name": "General", "role_id": 1358213815983935608, "minutes_required": 5000 * 60},
            {"name": "General of the Army", "role_id": 1358213816617275483, "minutes_required": 6000 * 60},
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

async def setup(bot: Red):
    """Set up the GameCounter cog."""
    cog = GameCounter(bot)
    await bot.add_cog(cog)
