import discord
import asyncio
import aiohttp
import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union

from redbot.core import commands, Config
from redbot.core.bot import Red
from aiohttp import web
from redbot.core.utils.chat_formatting import box, humanize_list

import logging

log = logging.getLogger("red.GameCounter")

class GameCounter(commands.Cog):
    """
    Track game activity of users and expose statistics via API.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        
        default_guild = {
            "game_stats": {},  # {user_id: {game_name: minutes}}
            "api_key": None,   # API key for authentication
            "last_updated": {} # {user_id: timestamp}
        }
        
        self.config.register_guild(**default_guild)
        
        # For tracking currently playing users
        self.currently_playing = {}  # {guild_id: {user_id: {"game": game_name, "start_time": datetime}}}
        
        # Setup web server for API
        self.web_app = web.Application()
        self.web_runner = None
        self.web_site = None
        
        # Define API routes
        self.web_app.router.add_get("/api/stats", self.get_stats_handler)
        self.web_app.router.add_get("/api/user/{user_id}", self.get_user_stats_handler)
        self.web_app.router.add_get("/api/game/{game_name}", self.get_game_stats_handler)
        self.web_app.router.add_get("/health", self.health_check_handler)
        
        # Start the web server
        self.bot.loop.create_task(self.initialize_webserver())
        
        # Start background task to update game time periodically for users still playing
        self.bg_task = self.bot.loop.create_task(self.update_game_time_periodically())

    async def initialize_webserver(self):
        """Initialize the web server for API endpoints."""
        await self.bot.wait_until_ready()
        
        try:
            self.web_runner = web.AppRunner(self.web_app)
            await self.web_runner.setup()
            host = os.environ.get("GAME_COUNTER_HOST", "0.0.0.0")
            port = int(os.environ.get("GAME_COUNTER_PORT", 5003))
            self.web_site = web.TCPSite(self.web_runner, host, port)
            await self.web_site.start()
            log.info(f"GameCounter API server started on http://{host}:{port}/")
        except Exception as e:
            log.error(f"Failed to start GameCounter web API server: {e}")
            self.web_runner = None
            self.web_site = None

    def cog_unload(self):
        """Clean up when cog is unloaded."""
        if self.web_runner:
            asyncio.create_task(self._shutdown_web_server())
        
        if self.bg_task:
            self.bg_task.cancel()
        
        # Update game time for all currently playing users before unloading
        for guild_id, users in self.currently_playing.items():
            guild = self.bot.get_guild(guild_id)
            if guild:
                for user_id, data in users.items():
                    asyncio.create_task(self._update_game_time(
                        guild, 
                        user_id, 
                        data["game"], 
                        (datetime.utcnow() - data["start_time"]).total_seconds() / 60
                    ))

    async def _shutdown_web_server(self):
        """Shut down the web server."""
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

    async def _authenticate_api_request(self, request: web.Request) -> bool:
        """Authenticate API requests using the X-API-Key header."""
        api_key = request.headers.get("X-API-Key")
        if not api_key:
            raise web.HTTPUnauthorized(reason="X-API-Key header missing")
        
        # Get the guild ID from the request or environment
        guild_id = int(os.environ.get("DISCORD_GUILD_ID", 0))
        if not guild_id:
            raise web.HTTPInternalServerError(reason="Guild ID not configured")
        
        guild = self.bot.get_guild(guild_id)
        if not guild:
            raise web.HTTPInternalServerError(reason="Guild not found")
        
        expected_key = await self.config.guild(guild).api_key()
        if not expected_key:
            raise web.HTTPInternalServerError(reason="API key not configured")
        
        if api_key != expected_key:
            raise web.HTTPForbidden(reason="Invalid API key")
        
        return True

    async def health_check_handler(self, request: web.Request):
        """Health check endpoint."""
        return web.Response(text="OK", status=200)

    async def get_stats_handler(self, request: web.Request):
        """API endpoint to get all game statistics."""
        try:
            await self._authenticate_api_request(request)
        except web.HTTPException as e:
            return e
        
        guild_id = int(os.environ.get("DISCORD_GUILD_ID", 0))
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return web.HTTPInternalServerError(reason="Guild not found")
        
        game_stats = await self.config.guild(guild).game_stats()
        
        # Format the response
        response_data = {
            "guild_id": str(guild.id),
            "guild_name": guild.name,
            "user_stats": {}
        }
        
        for user_id, games in game_stats.items():
            member = guild.get_member(int(user_id))
            if member:
                response_data["user_stats"][user_id] = {
                    "username": member.name,
                    "display_name": member.display_name,
                    "games": games
                }
        
        return web.json_response(response_data)

    async def get_user_stats_handler(self, request: web.Request):
        """API endpoint to get game statistics for a specific user."""
        try:
            await self._authenticate_api_request(request)
        except web.HTTPException as e:
            return e
        
        user_id = request.match_info.get("user_id")
        if not user_id or not user_id.isdigit():
            return web.HTTPBadRequest(reason="Invalid user ID")
        
        guild_id = int(os.environ.get("DISCORD_GUILD_ID", 0))
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return web.HTTPInternalServerError(reason="Guild not found")
        
        member = guild.get_member(int(user_id))
        if not member:
            return web.HTTPNotFound(reason="User not found in guild")
        
        game_stats = await self.config.guild(guild).game_stats()
        user_stats = game_stats.get(user_id, {})
        
        response_data = {
            "user_id": user_id,
            "username": member.name,
            "display_name": member.display_name,
            "games": user_stats,
            "total_minutes": sum(user_stats.values())
        }
        
        return web.json_response(response_data)

    async def get_game_stats_handler(self, request: web.Request):
        """API endpoint to get statistics for a specific game."""
        try:
            await self._authenticate_api_request(request)
        except web.HTTPException as e:
            return e
        
        game_name = request.match_info.get("game_name")
        if not game_name:
            return web.HTTPBadRequest(reason="Game name not provided")
        
        guild_id = int(os.environ.get("DISCORD_GUILD_ID", 0))
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return web.HTTPInternalServerError(reason="Guild not found")
        
        game_stats = await self.config.guild(guild).game_stats()
        
        # Find all users who played this game
        game_data = {
            "game_name": game_name,
            "users": [],
            "total_minutes": 0
        }
        
        for user_id, games in game_stats.items():
            # Case-insensitive match for game name
            for g_name, minutes in games.items():
                if g_name.lower() == game_name.lower():
                    member = guild.get_member(int(user_id))
                    if member:
                        game_data["users"].append({
                            "user_id": user_id,
                            "username": member.name,
                            "display_name": member.display_name,
                            "minutes": minutes
                        })
                        game_data["total_minutes"] += minutes
        
        # Sort users by playtime (descending)
        game_data["users"].sort(key=lambda x: x["minutes"], reverse=True)
        
        return web.json_response(game_data)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        """Track when users start or stop playing games."""
        if before.bot or after.bot:
            return
        
        # Get the game activity
        before_game = next((activity.name for activity in before.activities 
                           if activity.type == discord.ActivityType.playing), None)
        after_game = next((activity.name for activity in after.activities 
                          if activity.type == discord.ActivityType.playing), None)
        
        guild = after.guild
        user_id = str(after.id)
        
        # Initialize guild in tracking dict if needed
        if guild.id not in self.currently_playing:
            self.currently_playing[guild.id] = {}
        
        # User started playing a game
        if not before_game and after_game:
            self.currently_playing[guild.id][user_id] = {
                "game": after_game,
                "start_time": datetime.utcnow()
            }
            log.debug(f"{after.name} started playing {after_game}")
        
        # User stopped playing a game
        elif before_game and not after_game:
            if user_id in self.currently_playing[guild.id]:
                start_time = self.currently_playing[guild.id][user_id]["start_time"]
                game = self.currently_playing[guild.id][user_id]["game"]
                minutes_played = (datetime.utcnow() - start_time).total_seconds() / 60
                
                if minutes_played >= 1:  # Only count if played for at least a minute
                    await self._update_game_time(guild, user_id, game, minutes_played)
                    log.debug(f"{after.name} stopped playing {game} after {minutes_played:.2f} minutes")
                
                del self.currently_playing[guild.id][user_id]
        
        # User switched games
        elif before_game and after_game and before_game != after_game:
            if user_id in self.currently_playing[guild.id]:
                start_time = self.currently_playing[guild.id][user_id]["start_time"]
                game = self.currently_playing[guild.id][user_id]["game"]
                minutes_played = (datetime.utcnow() - start_time).total_seconds() / 60
                
                if minutes_played >= 1:  # Only count if played for at least a minute
                    await self._update_game_time(guild, user_id, game, minutes_played)
                    log.debug(f"{after.name} switched from {game} to {after_game} after {minutes_played:.2f} minutes")
                
                # Update with new game
                self.currently_playing[guild.id][user_id] = {
                    "game": after_game,
                    "start_time": datetime.utcnow()
                }

    async def update_game_time_periodically(self):
        """Periodically update game time for users who are still playing."""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                # Update every 5 minutes
                current_time = datetime.utcnow()
                
                for guild_id, users in self.currently_playing.items():
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        continue
                    
                    for user_id, data in list(users.items()):
                        # Check if it's been at least 5 minutes since they started playing
                        if (current_time - data["start_time"]).total_seconds() >= 300:  # 5 minutes
                            # Update the game time
                            minutes_played = 5  # Add 5 minutes
                            await self._update_game_time(guild, user_id, data["game"], minutes_played)
                            
                            # Reset the start time to now
                            self.currently_playing[guild_id][user_id]["start_time"] = current_time
                            
                            log.debug(f"Periodic update: Added 5 minutes to {data['game']} for user {user_id}")
            
            except Exception as e:
                log.error(f"Error in periodic game time update: {e}")
            
            await asyncio.sleep(300)  # Run every 5 minutes

    async def _update_game_time(self, guild: discord.Guild, user_id: str, game: str, minutes: float):
        """Update the game time for a user."""
        async with self.config.guild(guild).game_stats() as game_stats:
            if user_id not in game_stats:
                game_stats[user_id] = {}
            
            if game not in game_stats[user_id]:
                game_stats[user_id][game] = 0
            
            game_stats[user_id][game] += minutes
        
        # Update last updated timestamp
        async with self.config.guild(guild).last_updated() as last_updated:
            last_updated[user_id] = datetime.utcnow().timestamp()

    @commands.group(name="gamestats")
    @commands.guild_only()
    async def gamestats(self, ctx: commands.Context):
        """Commands for viewing game statistics."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @gamestats.command(name="user")
    async def gamestats_user(self, ctx: commands.Context, member: discord.Member = None):
        """Show game statistics for a user."""
        member = member or ctx.author
        
        game_stats = await self.config.guild(ctx.guild).game_stats()
        user_stats = game_stats.get(str(member.id), {})
        
        if not user_stats:
            return await ctx.send(f"{member.display_name} has no recorded game activity.")
        
        # Sort games by playtime
        sorted_games = sorted(user_stats.items(), key=lambda x: x[1], reverse=True)
        
        total_minutes = sum(user_stats.values())
        total_hours = total_minutes / 60
        
        embed = discord.Embed(
            title=f"Game Statistics for {member.display_name}",
            description=f"Total playtime: **{total_hours:.1f}** hours",
            color=member.color
        )
        
        # Add top 10 games
        games_text = ""
        for i, (game, minutes) in enumerate(sorted_games[:10], 1):
            hours = minutes / 60
            games_text += f"{i}. **{game}**: {hours:.1f} hours\n"
        
        embed.add_field(name="Top Games", value=games_text or "No games recorded", inline=False)
        
        # Add user avatar
        embed.set_thumbnail(url=member.display_avatar.url)
        
        await ctx.send(embed=embed)

    @gamestats.command(name="game")
    async def gamestats_game(self, ctx: commands.Context, *, game_name: str):
        """Show statistics for a specific game."""
        game_stats = await self.config.guild(ctx.guild).game_stats()
        
        # Find all users who played this game (case insensitive)
        users_played = []
        total_minutes = 0
        
        for user_id, games in game_stats.items():
            for g_name, minutes in games.items():
                if g_name.lower() == game_name.lower():
                    member = ctx.guild.get_member(int(user_id))
                    if member:
                        users_played.append((member, minutes))
                        total_minutes += minutes
                    break
        
        if not users_played:
            return await ctx.send(f"No statistics found for game '{game_name}'.")
        
        # Sort users by playtime
        users_played.sort(key=lambda x: x[1], reverse=True)
        
        total_hours = total_minutes / 60
        
        embed = discord.Embed(
            title=f"Statistics for {game_name}",
            description=f"Total playtime across all users: **{total_hours:.1f}** hours",
            color=discord.Color.blue()
        )
        
        # Add top 10 users
        users_text = ""
        for i, (member, minutes) in enumerate(users_played[:10], 1):
            hours = minutes / 60
            users_text += f"{i}. **{member.display_name}**: {hours:.1f} hours\n"
        
        embed.add_field(name="Top Players", value=users_text, inline=False)
        embed.set_footer(text=f"Total players: {len(users_played)}")
        
        await ctx.send(embed=embed)

    @gamestats.command(name="top")
    async def gamestats_top(self, ctx: commands.Context, count: int = 10):
        """Show top games by total playtime."""
        if count < 1:
            return await ctx.send("Count must be at least 1.")
        
        game_stats = await self.config.guild(ctx.guild).game_stats()
        
        # Aggregate playtime for each game
        games_total = {}
        for user_id, games in game_stats.items():
            for game, minutes in games.items():
                if game not in games_total:
                    games_total[game] = 0
                games_total[game] += minutes
        
        if not games_total:
            return await ctx.send("No game statistics recorded yet.")
        
        # Sort games by total playtime
        sorted_games = sorted(games_total.items(), key=lambda x: x[1], reverse=True)
        
        embed = discord.Embed(
            title="Top Games by Playtime",
            color=discord.Color.blue()
        )
        
        games_text = ""
        for i, (game, minutes) in enumerate(sorted_games[:count], 1):
            hours = minutes / 60
            games_text += f"{i}. **{game}**: {hours:.1f} hours\n"
        
        embed.add_field(name=f"Top {min(count, len(sorted_games))} Games", value=games_text, inline=False)
        
        await ctx.send(embed=embed)

    @gamestats.command(name="topusers")
    async def gamestats_topusers(self, ctx: commands.Context, count: int = 10):
        """Show users with the most total playtime."""
        if count < 1:
            return await ctx.send("Count must be at least 1.")
        
        game_stats = await self.config.guild(ctx.guild).game_stats()
        
        # Calculate total playtime for each user
        user_totals = []
        for user_id, games in game_stats.items():
            total_minutes = sum(games.values())
            member = ctx.guild.get_member(int(user_id))
            if member:
                user_totals.append((member, total_minutes))
        
        if not user_totals:
            return await ctx.send("No game statistics recorded yet.")
        
        # Sort users by total playtime
        user_totals.sort(key=lambda x: x[1], reverse=True)
        
        embed = discord.Embed(
            title="Top Users by Total Playtime",
            color=discord.Color.blue()
        )
        
        users_text = ""
        for i, (member, minutes) in enumerate(user_totals[:count], 1):
            hours = minutes / 60
            users_text += f"{i}. **{member.display_name}**: {hours:.1f} hours\n"
        
        embed.add_field(name=f"Top {min(count, len(user_totals))} Users", value=users_text, inline=False)
        
        await ctx.send(embed=embed)

    @commands.group(name="gameconfig")
    @commands.admin_or_permissions(manage_guild=True)
    async def gameconfig(self, ctx: commands.Context):
        """Configure the GameCounter cog."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @gameconfig.command(name="apikey")
    async def gameconfig_apikey(self, ctx: commands.Context, api_key: str = None):
        """Set or view the API key for authentication."""
        if api_key is None:
            current_key = await self.config.guild(ctx.guild).api_key()
            if current_key:
                await ctx.send("An API key is currently set. To view it, use this command in a DM.")
            else:
                await ctx.send("No API key is currently set.")
            return
        
        await self.config.guild(ctx.guild).api_key.set(api_key)
        await ctx.send("API key has been set.")
        
        # Delete the command message for security if possible
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass

    @gameconfig.command(name="reset")
    async def gameconfig_reset(self, ctx: commands.Context, confirmation: bool = False):
        """Reset all game statistics."""
        if not confirmation:
            await ctx.send("This will delete ALL game statistics. To confirm, use `!gameconfig reset yes`.")
            return
        
        await self.config.guild(ctx.guild).game_stats.set({})
        await self.config.guild(ctx.guild).last_updated.set({})
        await ctx.send("All game statistics have been reset.")

def setup(bot):
    bot.add_cog(GameCounter(bot))
