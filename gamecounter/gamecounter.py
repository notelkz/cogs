# gamecounter.py

import discord
import asyncio
import json
import aiohttp
from redbot.core import commands, Config, app_commands
from redbot.core.utils.menus import DEFAULT_CONTROLS
from redbot.core.utils.chat_formatting import humanize_list
from redbot.core.utils.views import ConfirmView
from redbot.core.bot import Red

# Optional: If you want logging
# import logging
# log = logging.getLogger("red.Elkz.gamecounter")

class GameCounter(commands.Cog):
    """
    Periodically counts users with specific Discord roles and sends the data to a Django website API.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        self.config = Config.get_conf(
            self, identifier=1234567890, force_registration=True
        )
        self.config.register_global(
            api_url=None,
            api_key=None,
            interval=15,  # Interval in minutes
            guild_id=None, # The specific guild ID to count roles in
            game_role_mappings={} # { "discord_role_id": "Django_Game_Name" }
        )
        self.counter_loop.start()

    def cog_unload(self):
        if self.counter_loop.is_running():
            self.counter_loop.cancel()
        asyncio.create_task(self.session.close())

    async def red_delete_data_for_user(self, *, requester: str, user_id: int) -> None:
        """No data is stored for users."""
        return

    @commands.hybrid_group(name="gamecounter", aliases=["gc"])
    async def gamecounter_settings(self, ctx: commands.Context):
        """Manage the GameCounter settings."""
        pass

    @gamecounter_settings.command(name="setapiurl")
    @commands.is_owner()
    @app_commands.describe(url="The Django API endpoint URL (e.g., http://your.site:8000/api/update_game_counts/)")
    async def set_api_url(self, ctx: commands.Context, url: str):
        """Sets the Django API endpoint URL."""
        await self.config.api_url.set(url)
        await ctx.send(f"Django API URL set to: `{url}`")

    @gamecounter_settings.command(name="setapikey")
    @commands.is_owner()
    @app_commands.describe(key="The secret API key for your Django endpoint.")
    async def set_api_key(self, ctx: commands.Context, key: str):
        """Sets the secret API key for your Django endpoint."""
        # Consider masking key in output for security
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
        self.counter_loop.restart()
        await ctx.send(f"Counter interval set to `{minutes}` minutes. Loop restarted.")

    @gamecounter_settings.command(name="setguild")
    @commands.is_owner()
    @app_commands.describe(guild_id="The ID of the guild where roles should be counted.")
    async def set_guild(self, ctx: commands.Context, guild_id: int):
        """Sets the guild ID where game roles should be counted."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return await ctx.send(f"Could not find a guild with ID `{guild_id}`. Please ensure the bot is in that guild.")

        view = ConfirmView(ctx.author, disable_on_timeout=True)
        view.message = await ctx.send(
            f"Are you sure you want to set the counting guild to **{guild.name}** (`{guild.id}`)?\n"
            f"This will stop counting roles in any previously configured guild.",
            view=view
        )
        await view.wait()
        if view.result:
            await self.config.guild_id.set(guild_id)
            await ctx.send(f"Counting guild set to **{guild.name}** (`{guild_id}`).")
        else:
            await ctx.send("Guild setting cancelled.")


    @gamecounter_settings.command(name="addmapping")
    @commands.is_owner()
    @app_commands.describe(
        discord_role_id="The Discord ID of the role (e.g., 'Minecraft Player' role ID).",
        django_game_name="The exact name of the GameCategory in your Django admin (e.g., 'Minecraft')."
    )
    async def add_mapping(self, ctx: commands.Context, discord_role_id: int, django_game_name: str):
        """Adds a mapping between a Discord Role ID and a Django GameCategory name.

        The `django_game_name` must exactly match the 'Name' field in your Django GameCategory.
        """
        current_mappings = await self.config.game_role_mappings()
        current_mappings[str(discord_role_id)] = django_game_name
        await self.config.game_role_mappings.set(current_mappings)
        await ctx.send(f"Mapping added: Discord Role ID `{discord_role_id}` -> Django Game `{django_game_name}`")

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
        else:
            await ctx.send(f"No mapping found for Discord Role ID `{discord_role_id}`.")

    @gamecounter_settings.command(name="mappings")
    async def show_mappings(self, ctx: commands.Context):
        """Shows all configured Discord Role ID to Django Game mappings."""
        mappings = await self.config.game_role_mappings()
        if not mappings:
            return await ctx.send("No game role mappings configured.")

        msg = "Configured Game Role Mappings:\n"
        for role_id, game_name in mappings.items():
            role = ctx.guild.get_role(int(role_id)) if ctx.guild else None
            role_name = role.name if role else f"Unknown Role ({role_id})"
            msg += f"`{role_name}` (ID: `{role_id}`) -> Django Game: **{game_name}**\n"
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

        status_msg = (
            f"**GameCounter Status:**\n"
            f"  API URL: `{api_url or 'Not set'}`\n"
            f"  API Key Set: `{api_key_set}`\n"
            f"  Update Interval: `{interval} minutes`\n"
            f"  Counting Guild: `{guild.name}` (`{guild.id}`)" if guild else "`Not set`"
        )

        if mappings:
            status_msg += "\n\n**Configured Mappings:**\n"
            for role_id, game_name in mappings.items():
                role = guild.get_role(int(role_id)) if guild and guild.get_role(int(role_id)) else None
                role_display = role.name if role else f"ID: {role_id}"
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
            # log.exception("Error during forced game count update") # If you enabled logging

    async def _get_game_counts(self, guild: discord.Guild):
        """Counts members per configured game role."""
        game_counts = {}
        role_mappings = await self.config.game_role_mappings()

        # Ensure guild members are cached/fetched
        if not guild.chunked:
            await guild.chunk() # Ensures all members are loaded

        for role_id_str, game_name in role_mappings.items():
            role_id = int(role_id_str)
            role = guild.get_role(role_id)
            if role:
                # Count members with the specific role
                member_count = len([m for m in guild.members if role in m.roles])
                game_counts[game_name] = member_count
                # log.debug(f"Counted {member_count} for role {role.name} ({game_name})") # If you enabled logging
            else:
                # log.warning(f"Configured Discord role with ID {role_id_str} not found in guild {guild.name}.") # If you enabled logging
                pass # Role not found, skip it

        return game_counts

    async def _send_counts_to_django(self, game_counts: dict):
        """Sends the game counts to the Django API endpoint."""
        api_url = await self.config.api_url()
        api_key = await self.config.api_key()

        if not api_url or not api_key:
            # log.error("Django API URL or API Key is not set in GameCounter cog config.") # If you enabled logging
            return False

        headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json"
        }
        payload = {"game_counts": game_counts}

        try:
            async with self.session.post(api_url, headers=headers, json=payload) as response:
                response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
                response_json = await response.json()
                # log.info(f"Successfully sent game counts to Django: {response_json}") # If you enabled logging
                return True
        except aiohttp.ClientError as e:
            # log.error(f"Failed to send game counts to Django API: {e}") # If you enabled logging
            return False
        except Exception as e:
            # log.exception(f"An unexpected error occurred while sending data: {e}") # If you enabled logging
            return False

    async def _run_update(self):
        """Fetches counts and sends them to Django."""
        guild_id = await self.config.guild_id()
        if not guild_id:
            # log.warning("No guild ID configured for GameCounter. Skipping update.") # If you enabled logging
            return

        guild = self.bot.get_guild(guild_id)
        if not guild:
            # log.error(f"Guild with ID {guild_id} not found. Bot might not be in it.") # If you enabled logging
            return

        # Fetch members if not already cached. Required for accurate role counting.
        if not guild.chunked:
            await guild.chunk()

        game_counts = await self._get_game_counts(guild)
        if game_counts:
            success = await self._send_counts_to_django(game_counts)
            if not success:
                # log.error("Failed to update game counts on Django site.") # If you enabled logging
                pass
        else:
            # log.info("No game counts to send based on current mappings/roles.") # If you enabled logging
            pass


    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        # If a relevant role is updated (e.g. name change, which could affect _get_game_counts if based on name)
        # Or if a role is deleted, we might want to trigger an update.
        # For now, just rely on the loop. More advanced logic can be added later.
        pass

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        # If a member's roles change, this is a good trigger for an update
        # to make the website counts more immediate.
        if before.roles != after.roles:
            guild_id = await self.config.guild_id()
            if after.guild.id == guild_id: # Only update for the configured guild
                # This could be aggressive if many role changes happen.
                # Consider a debounce or rate-limiting for production.
                self.counter_loop.restart() # Restart the loop for an immediate run + reset timer
                # log.debug(f"Roles changed for {after.name}, restarting counter loop.") # If you enabled logging


    @commands.Cog.listener()
    async def on_ready(self):
        # Ensure the loop starts if it wasn't running for some reason
        if not self.counter_loop.is_running():
            self.counter_loop.start()

    @tasks.loop(minutes=None) # Interval will be set by self.config.interval
    async def counter_loop(self):
        await self.bot.wait_until_ready() # Wait until bot is fully ready
        interval = await self.config.interval()
        if interval is None:
            # log.warning("GameCounter interval is not set. Skipping loop.") # If you enabled logging
            return

        # Update the loop's interval dynamically
        if self.counter_loop.minutes != interval:
            self.counter_loop.change_interval(minutes=interval)

        await self._run_update()

    @counter_loop.before_loop
    async def before_counter_loop(self):
        await self.bot.wait_until_ready()

async def setup(bot: Red):
    await bot.add_cog(GameCounter(bot))