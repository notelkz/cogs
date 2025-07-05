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
from discord.ext import tasks

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
        self.config = Config.get_conf(
            self, identifier=123456789012345, force_registration=True
        )
        self.config.register_global(
            api_url=None,
            api_key=None,
            interval=15,
            guild_id=None,
            game_role_mappings={}
        )
        if self.bot.is_ready():
            self.counter_loop.start()

    def cog_unload(self):
        if self.counter_loop.is_running():
            self.counter_loop.cancel()
        asyncio.create_task(self.session.close())

    async def red_delete_data_for_user(self, *, requester: str, user_id: int) -> None:
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
            return await ctx.send(
                f"Could not find a guild with ID `{guild_id}`. "
                "Please ensure the bot is in that guild and the ID is correct."
            )
        
        view = ConfirmView(ctx.author) 
        view.message = await ctx.send(
            f"Are you sure you want to set the counting guild to **{guild.name}** (`{guild.id}`)?\n"
            "This will stop counting roles in any previously configured guild.",
            view=view
        )
        await view.wait()
        if view.result:
            await self.config.guild_id.set(guild_id)
            await ctx.send(f"Counting guild set to **{guild.name}** (`{guild.id}`).")
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
        if str(discord_role_id) in current_mappings and current_mappings[str(discord_role_id)] != django_game_name:
            view = ConfirmView(ctx.author)
            view.message = await ctx.send(
                f"Discord Role ID `{discord_role_id}` is already mapped to Django Game "
                f"`{current_mappings[str(discord_role_id)]}`. Do you want to update it to "
                f"`{django_game_name}`?",
                view=view
            )
            await view.wait()
            if not view.result:
                return await ctx.send("Mapping update cancelled.")

        current_mappings[str(discord_role_id)] = django_game_name
        await self.config.game_role_mappings.set(current_mappings)
        await ctx.send(f"Mapping added/updated: Discord Role ID `{discord_role_id}` -> Django Game `{django_game_name}`")
        self.counter_loop.restart()


    @gamecounter_settings.command(name="addmappingbyname")
    @commands.is_owner()
    @app_commands.describe(
        discord_role="The Discord role (mention, ID, or name). Its name will be used as the Django game name."
    )
    async def add_mapping_by_name(self, ctx: commands.Context, discord_role: discord.Role):
        """
        Adds a mapping using a Discord role's name as the Django GameCategory name.

        Use `[p]gamecounter addmultiplemappingsbyname` for multiple roles.
        The Discord role name will be used directly as the `django_game_name`.
        """
        if not discord_role.guild == ctx.guild:
            return await ctx.send("That role is not from this server. Please use `[p]gamecounter addmapping` with the ID if it's from another server.")

        role_id = discord_role.id
        django_game_name = discord_role.name

        current_mappings = await self.config.game_role_mappings()

        if str(role_id) in current_mappings and current_mappings[str(role_id)] == django_game_name:
            return await ctx.send(f"Mapping for `{discord_role.name}` (`{role_id}`) to Django Game `{django_game_name}` already exists.")

        if str(role_id) in current_mappings and current_mappings[str(role_id)] != django_game_name:
            view = ConfirmView(ctx.author)
            view.message = await ctx.send(
                f"Discord Role `{discord_role.name}` (`{role_id}`) is already mapped to Django Game "
                f"`{current_mappings[str(role_id)]}`. Do you want to update it to "
                f"`{django_game_name}`?",
                view=view
            )
            await view.wait()
            if not view.result:
                return await ctx.send("Mapping update cancelled.")
        
        for existing_role_id_str, existing_game_name in current_mappings.items():
            if existing_game_name == django_game_name and int(existing_role_id_str) != role_id:
                existing_role = ctx.guild.get_role(int(existing_role_id_str))
                existing_role_display = existing_role.name if existing_role else f"ID: {existing_role_id_str}"

                view = ConfirmView(ctx.author)
                view.message = await ctx.send(
                    f"Warning: The Django game name `{django_game_name}` is already mapped "
                    f"to Discord Role `{existing_role_display}` (`{existing_role_id_str}`).\n"
                    f"Are you sure you want to map `{discord_role.name}` (`{role_id}`) to the *same* Django game name?\n"
                    "This is unusual and might lead to conflicting counts if both roles represent the same game."
                    "Confirm to proceed.",
                    view=view
                )
                await view.wait()
                if not view.result:
                    return await ctx.send("Mapping cancelled to avoid potential conflict.")
                break

        current_mappings[str(role_id)] = django_game_name
        await self.config.game_role_mappings.set(current_mappings)
        await ctx.send(f"Mapping added/updated: Discord Role `{discord_role.name}` (`{role_id}`) -> Django Game `{django_game_name}`")
        self.counter_loop.restart()

    # --- NEW COMMAND FOR MULTIPLE ROLES (PREFIX-ONLY) ---
    @gamecounter_settings.command(name="addmultiplemappingsbyname", hidden=False) # Not hidden for user to see it
    @commands.is_owner()
    async def add_multiple_mappings_by_name(self, ctx: commands.Context, *discord_roles: discord.Role):
        """
        Adds multiple mappings using Discord roles' names as Django GameCategory names.

        This is a prefix-only command. Provide multiple roles separated by spaces
        (e.g., `[p]gc addmultiplemappingsbyname @Role1 "Role 2" 123456789`).
        """
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

            # Check for existing mapping for this role ID
            if str(role_id) in current_mappings and current_mappings[str(role_id)] == django_game_name:
                skipped_mappings.append(f"`{discord_role.name}` (already mapped with same name)")
                continue

            # Check if this role ID is already mapped to a *different* name
            if str(role_id) in current_mappings and current_mappings[str(role_id)] != django_game_name:
                view = ConfirmView(ctx.author)
                view.message = await ctx.send(
                    f"Discord Role `{discord_role.name}` (`{role_id}`) is already mapped to Django Game "
                    f"`{current_mappings[str(role_id)]}`. Do you want to update it to "
                    f"`{django_game_name}`? (This will interrupt the current batch if cancelled.)",
                    view=view
                )
                await view.wait()
                if not view.result:
                    skipped_mappings.append(f"`{discord_role.name}` (update cancelled)")
                    continue

            # Check for name collision with a *different* role ID
            for existing_role_id_str, existing_game_name in current_mappings.items():
                if existing_game_name == django_game_name and int(existing_role_id_str) != role_id:
                    existing_role = ctx.guild.get_role(int(existing_role_id_str))
                    existing_role_display = existing_role.name if existing_role else f"ID: {existing_role_id_str}"

                    view = ConfirmView(ctx.author)
                    view.message = await ctx.send(
                        f"Warning: The Django game name `{django_game_name}` is already mapped "
                        f"to Discord Role `{existing_role_display}` (`{existing_role_id_str}`).\n"
                        f"Are you sure you want to map `{discord_role.name}` (`{role_id}`) to the *same* Django game name?\n"
                        "This is unusual and might lead to conflicting counts if both roles represent the same game."
                        "Confirm to proceed. (This will interrupt the current batch if cancelled.)",
                        view=view
                    )
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

    async def _get_game_counts(self, guild: discord.Guild):
        """Counts members per configured game role."""
        game_counts = {}
        role_mappings = await self.config.game_role_mappings()

        if not guild.chunked:
            await guild.chunk()

        for role_id_str, game_name in role_mappings.items():
            role_id = int(role_id_str)
            role = guild.get_role(role_id)
            if role:
                member_count = len(role.members)
                game_counts[game_name] = member_count
            else:
                pass

        return game_counts

    async def _send_counts_to_django(self, game_counts: dict):
        """Sends the game counts to the Django API endpoint."""
        api_url = await self.config.api_url()
        api_key = await self.config.api_key()

        if not api_url or not api_key:
            return False

        headers = {
            "X-API-Key": api_key,
            "Content-Type": "application/json"
        }
        payload = {"game_counts": game_counts}

        try:
            async with self.session.post(api_url, headers=headers, json=payload) as response:
                response.raise_for_status()
                response_json = await response.json()
                return True
        except aiohttp.ClientError as e:
            return False
        except Exception as e:
            return False

    async def _run_update(self):
        """Fetches counts and sends them to Django."""
        guild_id = await self.config.guild_id()
        if not guild_id:
            return

        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        if not guild.chunked:
            await guild.chunk()

        game_counts = await self._get_game_counts(guild)
        if game_counts:
            success = await self._send_counts_to_django(game_counts)
            if not success:
                pass
        else:
            pass

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Triggers an update if a member's roles change in the configured guild."""
        guild_id = await self.config.guild_id()
        if after.guild.id == guild_id and before.roles != after.roles:
            self.counter_loop.restart()

    @commands.Cog.listener()
    async def on_ready(self):
        """Ensures the loop starts when the bot is fully ready."""
        if not self.counter_loop.is_running():
            self.counter_loop.start()

    @tasks.loop(minutes=None)
    async def counter_loop(self):
        """Main loop that periodically updates game counts."""
        await self.bot.wait_until_ready()

        interval = await self.config.interval()
        if interval is None:
            await asyncio.sleep(60)
            return
        
        if self.counter_loop.minutes != interval:
            self.counter_loop.change_interval(minutes=interval)
        
        await self._run_update()

    @counter_loop.before_loop
    async def before_counter_loop(self):
        """Hook that runs before the first iteration of the loop."""
        await self.bot.wait_until_ready()

async def setup(bot: Red):
    """Adds the GameCounter cog to the bot."""
    await bot.add_cog(GameCounter(bot))