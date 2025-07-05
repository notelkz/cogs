# gamecounter.py

import discord
import asyncio
import json
import aiohttp
from redbot.core import commands, Config, app_commands
from redbot.core.utils.menus import DEFAULT_CONTROLS # Might not be strictly needed for this cog's current commands
from redbot.core.utils.chat_formatting import humanize_list # Might not be strictly needed for this cog's current commands
from redbot.core.utils.views import ConfirmView
from redbot.core.bot import Red
from redbot.core.tasks import loop # Explicitly import loop from tasks
from redbot.internal.tasks import loop


# Optional: If you want logging for debugging the cog
# Uncomment these lines to enable basic logging
# import logging
# log = logging.getLogger("red.Elkz.gamecounter")
# Ensure your RedBot logging configuration (via `[p]set logging level debug`) allows DEBUG level for this cog

class GameCounter(commands.Cog):
    """
    Periodically counts users with specific Discord roles and sends the data to a Django website API.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        # Unique identifier for this cog's configuration
        self.config = Config.get_conf(
            self, identifier=123456789012345, force_registration=True # Changed identifier for better uniqueness
        )
        self.config.register_global(
            api_url=None,
            api_key=None,
            interval=15,  # Default interval in minutes
            guild_id=None, # The specific guild ID to count roles in
            game_role_mappings={} # Dictionary: { "discord_role_id_str": "Django_Game_Name_str" }
        )
        # Start the loop only if the bot is ready. It will also be started by the on_ready listener.
        # This prevents potential issues if the cog loads before the bot is fully initialized.
        if self.bot.is_ready():
            self.counter_loop.start()

    def cog_unload(self):
        """Called when the cog is unloaded."""
        # It's crucial to clean up any running tasks and close the aiohttp session.
        if self.counter_loop.is_running():
            self.counter_loop.cancel()
        # Use asyncio.create_task to ensure the session is closed even if the cog is unloaded quickly.
        asyncio.create_task(self.session.close())

    async def red_delete_data_for_user(self, *, requester: str, user_id: int) -> None:
        """This method is required by RedBot's data policy.
        This cog does not store any user-specific data; it only counts anonymous role assignments.
        """
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
        if not url.startswith("http"):
            return await ctx.send("Please provide a valid URL starting with `http://` or `https://`.")
        await self.config.api_url.set(url)
        await ctx.send(f"Django API URL set to: `{url}`")

    @gamecounter_settings.command(name="setapikey")
    @commands.is_owner()
    @app_commands.describe(key="The secret API key for your Django endpoint.")
    async def set_api_key(self, ctx: commands.Context, key: str):
        """Sets the secret API key for your Django endpoint."""
        # For security, avoid echoing the key back directly.
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
        # Restart the loop to apply the new interval immediately
        # This will also trigger an immediate run.
        if self.counter_loop.is_running():
            self.counter_loop.restart()
        else:
            self.counter_loop.start() # Start if it was stopped
        await ctx.send(f"Counter interval set to `{minutes}` minutes. Loop restarted.")

    @gamecounter_settings.command(name="setguild")
    @commands.is_owner()
    @app_commands.describe(guild_id="The ID of the guild where roles should be counted.")
    async def set_guild(self, ctx: commands.Context, guild_id: int):
        """Sets the guild ID where game roles should be counted."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return await ctx.send(
                f"Could not find a guild with ID `{guild_id}`. "
                "Please ensure the bot is in that guild and the ID is correct."
            )
        
        view = ConfirmView(ctx.author, disable_on_timeout=True)
        view.message = await ctx.send(
            f"Are you sure you want to set the counting guild to **{guild.name}** (`{guild.id}`)?\n"
            "This will stop counting roles in any previously configured guild.",
            view=view
        )
        await view.wait()
        if view.result:
            await self.config.guild_id.set(guild_id)
            await ctx.send(f"Counting guild set to **{guild.name}** (`{guild.id}`).")
            # Trigger an immediate run if the guild changed
            self.counter_loop.restart()
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
        current_mappings[str(discord_role_id)] = django_game_name # Store role ID as string for JSON key consistency
        await self.config.game_role_mappings.set(current_mappings)
        await ctx.send(f"Mapping added: Discord Role ID `{discord_role_id}` -> Django Game `{django_game_name}`")
        # Trigger an immediate update after adding a mapping
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
            # Trigger an immediate update after removing a mapping
            self.counter_loop.restart()
        else:
            await ctx.send(f"No mapping found for Discord Role ID `{discord_role_id}`.")

    @gamecounter_settings.command(name="mappings")
    async def show_mappings(self, ctx: commands.Context):
        """Shows all configured Discord Role ID to Django Game mappings."""
        mappings = await self.config.game_role_mappings()
        if not mappings:
            return await ctx.send("No game role mappings configured.")

        # Try to get the guild if set, for better role name display
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

        status_msg = (
            f"**GameCounter Status:**\n"
            f"  API URL: `{api_url or 'Not set'}`\n"
            f"  API Key Set: `{api_key_set}`\n"
            f"  Update Interval: `{interval} minutes`\n"
            f"  Counting Guild: `{guild.name}` (`{guild.id}`)" if guild else "`Not set`"
        )

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
            # If you enabled logging, uncomment this:
            # log.exception("Error during forced game count update") 

    async def _get_game_counts(self, guild: discord.Guild):
        """Counts members per configured game role."""
        game_counts = {}
        role_mappings = await self.config.game_role_mappings()

        # Ensure guild members are cached/fetched.
        # This is crucial for accurate counting, especially in large guilds.
        # `guild.chunk()` is a network operation and should only be called if necessary.
        if not guild.chunked:
            # If you enabled logging, uncomment this:
            # log.debug(f"Chunking guild {guild.name}...")
            await guild.chunk() # Ensures all members are loaded into cache

        for role_id_str, game_name in role_mappings.items():
            role_id = int(role_id_str)
            role = guild.get_role(role_id)
            if role:
                # `len(role.members)` is the most efficient way to get members with that role
                member_count = len(role.members)
                game_counts[game_name] = member_count
                # If you enabled logging, uncomment this:
                # log.debug(f"Counted {member_count} for role {role.name} ({game_name})")
            else:
                # If you enabled logging, uncomment this:
                # log.warning(f"Configured Discord role with ID {role_id_str} not found in guild {guild.name}. Skipping.")
                pass # Role not found in guild, skip it

        return game_counts

    async def _send_counts_to_django(self, game_counts: dict):
        """Sends the game counts to the Django API endpoint."""
        api_url = await self.config.api_url()
        api_key = await self.config.api_key()

        if not api_url or not api_key:
            # If you enabled logging, uncomment this:
            # log.error("Django API URL or API Key is not set in GameCounter cog config. Cannot send data.")
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
                # If you enabled logging, uncomment this:
                # log.info(f"Successfully sent game counts to Django: {response_json}")
                return True
        except aiohttp.ClientError as e:
            # If you enabled logging, uncomment this:
            # log.error(f"Failed to send game counts to Django API: {e}")
            return False
        except Exception as e:
            # If you enabled logging, uncomment this:
            # log.exception(f"An unexpected error occurred while sending data: {e}")
            return False

    async def _run_update(self):
        """Fetches counts and sends them to Django."""
        guild_id = await self.config.guild_id()
        if not guild_id:
            # If you enabled logging, uncomment this:
            # log.warning("No guild ID configured for GameCounter. Skipping update.")
            return

        guild = self.bot.get_guild(guild_id)
        if not guild:
            # If you enabled logging, uncomment this:
            # log.error(f"Guild with ID {guild_id} not found. Bot might not be in it or cache not ready.")
            return

        # Ensure members are loaded for accurate counting
        if not guild.chunked:
            await guild.chunk() # Ensures all members are loaded into cache

        game_counts = await self._get_game_counts(guild)
        if game_counts:
            success = await self._send_counts_to_django(game_counts)
            if not success:
                # If you enabled logging, uncomment this:
                # log.error("Failed to update game counts on Django site. Check Django server logs for details.")
                pass
        else:
            # If you enabled logging, uncomment this:
            # log.info("No game counts to send based on current mappings/roles.")
            pass

    # --- Listeners to react to Discord events (optional, for more immediate updates) ---
    # These listeners will automatically re-trigger the counter loop when relevant changes occur.

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Triggers an update if a member's roles change in the configured guild."""
        guild_id = await self.config.guild_id()
        if after.guild.id == guild_id and before.roles != after.roles:
            # Restarting the loop triggers an immediate run and resets the timer.
            # For very large, active guilds, consider a more sophisticated debouncing/rate-limiting
            # mechanism to avoid excessive API calls if many role changes happen rapidly.
            self.counter_loop.restart()
            # If you enabled logging, uncomment this:
            # log.debug(f"Roles changed for {after.name} in guild {after.guild.name}, restarting counter loop.")

    @commands.Cog.listener()
    async def on_ready(self):
        """Ensures the loop starts when the bot is fully ready."""
        # This listener ensures the loop starts even if the cog loads before the bot is fully ready.
        if not self.counter_loop.is_running():
            # If you enabled logging, uncomment this:
            # log.info("GameCounter loop starting via on_ready listener.")
            self.counter_loop.start()

    # --- Task Loop ---
    # The `loop` decorator manages the interval and automatic restarting.
    @loop(minutes=None) # Start with None, actual interval will be set dynamically from config.
    async def counter_loop(self):
        """Main loop that periodically updates game counts."""
        await self.bot.wait_until_ready() # Ensure bot is logged in and ready before running.

        # Get the configured interval from settings
        interval = await self.config.interval()
        if interval is None:
            # If interval is not set, log a warning and wait a bit before retrying.
            # If you enabled logging, uncomment this:
            # log.warning("GameCounter interval is not set in config. Loop cannot run. Please set it via `[p]gamecounter setinterval`.")
            await asyncio.sleep(60) # Wait 1 minute before checking config again.
            return
        
        # Dynamically change the loop interval if it's different from the configured value.
        # This allows updating the interval without reloading the cog.
        if self.counter_loop.minutes != interval:
            self.counter_loop.change_interval(minutes=interval)
            # If you enabled logging, uncomment this:
            # log.debug(f"GameCounter loop interval changed to {interval} minutes.")
        
        # Execute the actual update logic.
        await self._run_update()

    @counter_loop.before_loop
    async def before_counter_loop(self):
        """Hook that runs before the first iteration of the loop."""
        await self.bot.wait_until_ready()
        # If you enabled logging, uncomment this:
        # log.info("GameCounter loop waiting for bot readiness before starting.")

# This is the crucial function that RedBot calls to load your cog.
# It MUST be at the very root level (no indentation) of your gamecounter.py file.
async def setup(bot: Red):
    """Adds the GameCounter cog to the bot."""
    await bot.add_cog(GameCounter(bot))