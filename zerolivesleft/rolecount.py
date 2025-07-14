# zerolivesleft/rolecount.py

import discord
import asyncio
import json
import aiohttp
from urllib.parse import urljoin
from redbot.core import commands
from redbot.core.bot import Red
from discord.ext import tasks
import logging

# Changed logger name to reflect new module name
log = logging.getLogger("red.Elkz.zerolivesleft.rolecount")

class RoleCountingLogic: # Class name remains the same as it's descriptive
    """Manages game role counting and sending data to Django website."""

    def __init__(self, cog_instance):
        self.cog = cog_instance # Reference to the main Zerolivesleft cog
        
        # Access config and session from the main cog
        self.config = cog_instance.config
        self.session = cog_instance.session

        # Task will be started via start_tasks() method
        self.count_loop = None 
        
    def start_tasks(self):
        """Starts the periodic game counting task."""
        if not self.count_loop or not self.count_loop.is_running():
            self.count_loop = self.cog.bot.loop.create_task(self._start_count_loop())

    def stop_tasks(self):
        """Stops the periodic game counting task."""
        if self.count_loop and self.count_loop.is_running():
            self.count_loop.cancel()

    async def _start_count_loop(self):
        """Internal method to start the count loop task."""
        await self.cog.bot.wait_until_ready()
        
        # Access interval from main config
        interval_minutes = await self.config.gc_interval() # Uses 'gc_interval' from central config
        if interval_minutes < 1:
            log.warning("RoleCounting: Interval is less than 1 minute, defaulting to 1 minute.")
            interval_minutes = 1
            
        self.count_loop_task = tasks.loop(minutes=interval_minutes)(self.count_and_update)
        self.count_loop_task.start()
        log.info(f"RoleCounting: Started count_and_update loop with {interval_minutes} minute interval.")

    @tasks.loop(minutes=15) # This decorator is here, but the actual interval is set dynamically below
    async def count_and_update(self):
        """Periodically count users with specific roles and update the Django website."""
        await self.cog.bot.wait_until_ready()
        try:
            guild_id = await self.config.gc_counting_guild_id() # Use central config
            if not guild_id:
                if self.count_loop_task.current_loop == 0:
                    log.warning("RoleCounting: Guild ID not set. The loop will not run until it is set.")
                return
            
            guild = self.cog.bot.get_guild(guild_id) # Use main cog's bot instance
            if not guild:
                log.error(f"RoleCounting: Could not find guild with ID {guild_id}.")
                return
            
            if not guild.chunked:
                await guild.chunk()

            mappings = await self.config.gc_game_role_mappings() # Use central config
            if not mappings:
                log.info("RoleCounting: No game role mappings configured.")
                return

            game_counts = {}
            for role_id_str, game_name in mappings.items():
                role = guild.get_role(int(role_id_str))
                if role:
                    game_counts[game_name] = len(role.members)
            
            api_base_url = await self.config.gc_api_base_url() # Use central config
            api_key = await self.config.gc_api_key()           # Use central config
            
            if api_base_url and api_key and game_counts:
                update_url = urljoin(api_base_url, "update-game-counts/")
                headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
                payload = {"game_counts": game_counts}
                async with self.session.post(update_url, json=payload, headers=headers) as response:
                    if response.status != 200:
                        log.error(f"RoleCounting: Failed to send game counts. Status: {response.status}, Response: {await response.text()}")
                    else:
                        log.info(f"RoleCounting: Successfully sent game counts to website: {game_counts}")
            elif not api_base_url:
                log.warning("RoleCounting: API Base URL not set. Cannot send game counts.")
            elif not api_key:
                log.warning("RoleCounting: API Key not set. Cannot send game counts.")
            elif not game_counts:
                log.info("RoleCounting: No active game counts to send.")

        except Exception as e:
            log.error(f"Error in RoleCounting loop: {e}", exc_info=True)

    # --- Commands (these will be added as subcommands to the main cog's group) ---
    
    @commands.group(name="rolecounter", aliases=["rc"]) # Updated command group name to rolecounter
    async def rolecounter_settings(self, ctx: commands.Context):
        """Manage the RoleCounter settings for Zerolivesleft."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @rolecounter_settings.command(name="setapiurl")
    async def set_api_url(self, ctx: commands.Context, url: str):
        """Sets the base Django API URL (e.g., https://zerolivesleft.net/api/)."""
        if not url.startswith("http"):
            return await ctx.send("Please provide a valid URL starting with `http://` or `https://`.")
        if not url.endswith('/'):
            url += '/'
        await self.config.gc_api_base_url.set(url) # Using 'gc_api_base_url' from central config
        await ctx.send(f"Django API Base URL set to: `{url}`")

    @rolecounter_settings.command(name="setapikey")
    async def set_api_key(self, ctx: commands.Context, *, key: str):
        """Sets the secret API key for authenticating with your Django endpoint."""
        await self.config.gc_api_key.set(key) # Using 'gc_api_key' from central config
        await ctx.send("Django API Key has been set.")
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @rolecounter_settings.command(name="setinterval")
    async def set_interval(self, ctx: commands.Context, minutes: int):
        """Sets the interval (in minutes) for the counter to run."""
        if minutes < 1:
            return await ctx.send("Interval must be at least 1 minute.")
        await self.config.gc_interval.set(minutes) # Using 'gc_interval' from central config
        
        # Stop and restart the loop with the new interval
        if self.count_loop_task and self.count_loop_task.is_running(): # Check if task exists before trying to stop
            self.count_loop_task.stop()
        self.count_loop_task.change_interval(minutes=minutes)
        self.count_loop_task.start()
        
        await ctx.send(f"Counter interval set to `{minutes}` minutes. Loop restarted.")

    @rolecounter_settings.command(name="setguild")
    async def set_guild(self, ctx: commands.Context, guild: discord.Guild):
        """Sets the guild where game roles should be counted."""
        await self.config.gc_counting_guild_id.set(guild.id) # Using 'gc_counting_guild_id' from central config
        await ctx.send(f"Counting guild set to **{guild.name}** (`{guild.id}`).")

    @rolecounter_settings.command(name="addmapping")
    async def add_mapping(self, ctx: commands.Context, role: discord.Role, *, game_name: str):
        """Adds a mapping between a Discord Role and a Django GameCategory name."""
        async with self.config.gc_game_role_mappings() as mappings: # Using 'gc_game_role_mappings' from central config
            mappings[str(role.id)] = game_name
        await ctx.send(f"Mapping added: Role `{role.name}` -> Game `{game_name}`")

    @rolecounter_settings.command(name="removemapping")
    async def remove_mapping(self, ctx: commands.Context, role: discord.Role):
        """Removes a mapping for a Discord Role."""
        async with self.config.gc_game_role_mappings() as mappings: # Using 'gc_game_role_mappings' from central config
            if str(role.id) in mappings:
                del mappings[str(role.id)]
                await ctx.send(f"Mapping removed for role `{role.name}`.")
            else:
                await ctx.send("No mapping found for that role.")

    @rolecounter_settings.command(name="listmappings")
    async def list_mappings(self, ctx: commands.Context):
        """Lists all current role-to-game mappings."""
        mappings = await self.config.gc_game_role_mappings() # Using 'gc_game_role_mappings' from central config
        if not mappings:
            return await ctx.send("No mappings configured.")
        
        guild_id = await self.config.gc_counting_guild_id() # Using 'gc_counting_guild_id' from central config
        guild = self.cog.bot.get_guild(guild_id) # Use main cog's bot instance
        if not guild:
            return await ctx.send("Counting guild not set or not found. Please set it with `[p]zll rolecounter setguild`.") # Updated command help

        msg = "**Current Role to Game Mappings:**\n"
        for role_id, game_name in mappings.items():
            role = guild.get_role(int(role_id))
            role_name = f"`{role.name}`" if role else "`Unknown Role (ID not found in server)`"
            msg += f"- {role_name} (ID: `{role_id}`) -> `{game_name}`\n"
        await ctx.send(msg)

    async def show_config_command(self, ctx: commands.Context):
        """Shows the current RoleCounter configuration."""
        config_data = await self.config.all() # Get all config data (global)
        
        # Access specific fields by their new prefixed names
        api_base_url = config_data.get("gc_api_base_url")
        api_key = config_data.get("gc_api_key")
        interval = config_data.get("gc_interval")
        guild_id = config_data.get("gc_counting_guild_id")

        api_key_masked = "Set" if api_key else "Not Set"
        guild = self.cog.bot.get_guild(guild_id) if guild_id else None
        
        embed = discord.Embed(
            title="RoleCounter Configuration", # Updated title
            description="Settings for counting game roles and reporting to your website.",
            color=discord.Color.blue()
        )
        
        embed.add_field(name="API Base URL", value=api_base_url or "Not Set", inline=False)
        embed.add_field(name="API Key", value=api_key_masked, inline=True)
        embed.add_field(name="Update Interval", value=f"{interval} minutes", inline=True)
        embed.add_field(name="Counting Guild", value=f"{guild.name if guild else 'Not Set'} (`{guild_id if guild_id else 'Not Set'}`)", inline=False)
        
        # Check the status of the internal task loop
        loop_status = "Running" if self.count_loop_task and self.count_loop_task.is_running() else "Stopped"
        embed.add_field(name="Counter Loop Status", value=loop_status, inline=False)
        
        await ctx.send(embed=embed)