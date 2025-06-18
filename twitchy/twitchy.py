import asyncio
import aiohttp
import discord
from redbot.core import commands, Config, data_manager
from redbot.core.utils.chat_formatting import humanize_list, pagify
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import MessagePredicate, ReactionPredicate
import time
import datetime
from datetime import timedelta
import io
import os
import pytz
import re
import dateutil.parser
import traceback
from PIL import Image, ImageDraw, ImageFont

# Define a custom view for the buttons
class StreamButtons(discord.ui.View):
    def __init__(self, watch_url: str, subscribe_url: str, timeout=180):
        super().__init__(timeout=timeout)
        self.add_item(discord.ui.Button(label="Watch Now", style=discord.ButtonStyle.link, url=watch_url))
        self.add_item(discord.ui.Button(label="Subscribe", style=discord.ButtonStyle.link, url=subscribe_url))

class Twitchy(commands.Cog):
    """
    Automatically announces when Twitch streams go live and manages 'Live' roles
    based on Discord activity and provides Twitch schedule functionality.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)

        # Default global settings (e.g., Twitch API credentials)
        self.config.register_global(
            twitch_client_id=None,
            twitch_client_secret=None,
            twitch_redirect_uri="http://localhost:8000/callback",
            twitch_access_token_info=None,
            custom_template_url=None,
            custom_font_url=None
        )

        # Default guild settings (for each Discord server)
        # 'streamers' now acts as a group to store all per-streamer data:
        # {"twitch_username": {"discord_channel_id": int, "role_ids": [int], "live_status": bool, "stream_message_id": int}}
        self.config.register_guild(
            live_role_id=None,
            streamers={}, # Unified storage for all streamer-specific data
            announcement_channel_id=None, # Central announcement channel (can be overridden per streamer)
            
            # Restored schedule-related settings for user configurability
            schedule_channel_id=None, # Channel to post schedule to
            schedule_twitch_username=None, # Twitch username whose schedule to track for this guild
            schedule_timezone="UTC", # Timezone for schedule display (e.g., "Europe/London", "America/New_York")
            schedule_update_days_in_advance=7, # How many days of schedule to fetch/display
            schedule_update_time="00:00", # Time of day to update schedule (HH:MM 24hr format)
            schedule_ping_role_id=None, # Role to ping when schedule updates
            schedule_event_count=5 # Number of events to show in schedule by default
        )
        
        self.session = aiohttp.ClientSession()
        self.live_check_loop = self.bot.loop.create_task(self.check_live_status_loop())
        self.token_refresh_loop = self.bot.loop.create_task(self.refresh_twitch_token_loop())
        self.schedule_update_loop = self.bot.loop.create_task(self.update_all_guild_schedules_loop())

        # Paths for schedule image resources
        self.font_path = data_manager.cog_data_path(self) / "Twitchy_Schedule_Font.ttf"
        self.template_path = data_manager.cog_data_path(self) / "Twitchy_Schedule_Template.png"


    def cog_unload(self):
        self.live_check_loop.cancel()
        self.token_refresh_loop.cancel()
        self.schedule_update_loop.cancel()
        self.bot.loop.create_task(self.session.close())

    async def get_twitch_headers(self):
        config_global = await self.config.get_global_settings()
        access_token_info = config_global["twitch_access_token_info"]
        client_id = config_global["twitch_client_id"]

        if not access_token_info or access_token_info.get("expires_at", 0) <= time.time():
            # Token is missing or expired, try to refresh
            await self.refresh_twitch_token()
            access_token_info = (await self.config.get_global_settings())["twitch_access_token_info"]
            if not access_token_info:
                raise commands.UserError("Twitch access token is missing or expired and could not be refreshed. Please re-authenticate.")

        return {
            "Authorization": f"Bearer {access_token_info['access_token']}",
            "Client-ID": client_id,
        }

    async def refresh_twitch_token(self):
        config_global = await self.config.get_global_settings()
        client_id = config_global["twitch_client_id"]
        client_secret = config_global["twitch_client_secret"]
        redirect_uri = config_global["twitch_redirect_uri"]
        access_token_info = config_global["twitch_access_token_info"]

        if not client_id or not client_secret:
            raise commands.UserError("Twitch Client ID and Client Secret must be set. Use `[p]twitchy set creds`.")

        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
        }

        if access_token_info and "refresh_token" in access_token_info:
            payload.update({
                "grant_type": "refresh_token",
                "refresh_token": access_token_info["refresh_token"],
            })
        else:
            # This path is usually for initial authentication or if refresh token is lost
            # It shouldn't be hit during refresh loop unless something is critically wrong
            self.log.warning("Attempting to refresh token without a refresh token. User might need to re-authenticate.")
            return

        async with self.session.post("https://id.twitch.tv/oauth2/token", data=payload) as resp:
            try:
                resp.raise_for_status()
                data = await resp.json()
                if "access_token" in data:
                    data["expires_at"] = time.time() + data["expires_in"] - 60  # Subtract 60s for buffer
                    await self.config.twitch_access_token_info.set(data)
                    self.log.info("Twitch access token refreshed successfully.")
                else:
                    self.log.error(f"Failed to refresh Twitch token: {data.get('message', 'Unknown error')}")
            except aiohttp.ClientResponseError as e:
                self.log.error(f"HTTP error refreshing Twitch token: {e.status} - {e.message}")
                self.log.error(f"Response: {await resp.text()}")
                raise commands.UserError(f"Failed to refresh Twitch token. HTTP Error: {e.status}")
            except Exception as e:
                self.log.error(f"An unexpected error occurred during Twitch token refresh: {e}")
                raise commands.UserError(f"An unexpected error occurred during Twitch token refresh.")

    async def refresh_twitch_token_loop(self):
        await self.bot.wait_until_ready()
        while self:
            try:
                await self.refresh_twitch_token()
                # Check token expiration more frequently than full refresh if needed
                access_token_info = (await self.config.get_global_settings())["twitch_access_token_info"]
                if access_token_info:
                    expires_in = access_token_info.get("expires_at", 0) - time.time()
                    sleep_time = max(3600, expires_in / 2) # Refresh at least once an hour, or halfway through expiry
                    if sleep_time < 0: # If already expired, refresh immediately next loop
                        sleep_time = 60 # wait a minute before next attempt
                    self.log.debug(f"Next Twitch token refresh in {int(sleep_time)} seconds.")
                    await asyncio.sleep(sleep_time)
                else:
                    self.log.debug("No Twitch token info, waiting for 1 hour before next refresh attempt.")
                    await asyncio.sleep(3600) # Wait 1 hour if no token is set
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log.exception(f"Error in Twitch token refresh loop: {e}")
                await asyncio.sleep(3600) # Wait before retrying after an error

    async def get_stream_info(self, usernames: list):
        headers = await self.get_twitch_headers()
        params = [("user_login", u) for u in usernames]
        async with self.session.get("https://api.twitch.tv/helix/streams", headers=headers, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["data"]

    async def get_user_info(self, usernames: list):
        headers = await self.get_twitch_headers()
        params = [("login", u) for u in usernames]
        async with self.session.get("https://api.twitch.tv/helix/users", headers=headers, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["data"]

    async def _send_announcement(self, streamer_username, guild: discord.Guild, stream_info):
        guild_config = self.config.guild(guild)
        streamer_settings = await guild_config.streamers.get_raw(streamer_username.lower(), {})
        
        # Determine the channel to send the announcement to
        channel_id = streamer_settings.get("discord_channel_id")
        if channel_id is None:
            channel_id = await guild_config.announcement_channel_id() # Fallback to guild-wide announcement channel

        channel = guild.get_channel(channel_id)
        if not channel:
            self.log.warning(f"Announcement channel not found for guild {guild.id} or streamer {streamer_username}.")
            return

        message_id = streamer_settings.get("stream_message_id")
        message_to_edit = None
        if message_id:
            try:
                message_to_edit = await channel.fetch_message(message_id)
            except discord.NotFound:
                self.log.info(f"Existing announcement message {message_id} not found, sending a new one.")
                message_to_edit = None
            except discord.Forbidden:
                self.log.warning(f"Bot missing permissions to fetch message {message_id} in channel {channel.id}.")
                message_to_edit = None

        embed = discord.Embed(
            title=f"{stream_info['user_name']} is now LIVE on Twitch!",
            url=f"https://www.twitch.tv/{stream_info['user_login']}",
            color=0x9146FF  # Twitch purple
        )
        embed.set_thumbnail(url=stream_info.get("profile_image_url", "https://static-cdn.jtvnw.net/emoticons/v1/555555555/1.0")) # Fallback
        embed.add_field(name="Game", value=stream_info.get("game_name", "N/A"), inline=True)
        embed.add_field(name="Viewers", value=f"{stream_info['viewer_count']:,}", inline=True)
        embed.set_image(url=stream_info["thumbnail_url"].replace("{width}", "1280").replace("{height}", "720") + f"?{time.time()}")
        embed.set_footer(text=f"Stream started at: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}")

        watch_url = f"https://www.twitch.tv/{stream_info['user_login']}"
        subscribe_url = f"https://www.twitch.tv/subs/{stream_info['user_login']}"
        view = StreamButtons(watch_url, subscribe_url)

        try:
            if message_to_edit:
                await message_to_edit.edit(embed=embed, view=view)
                message = message_to_edit
            else:
                message = await channel.send(embed=embed, view=view)
            
            # Save the message ID for future edits
            streamer_settings["stream_message_id"] = message.id
            await guild_config.streamers.set_raw(streamer_username.lower(), streamer_settings)

            # Assign live role
            live_role_id = await guild_config.live_role_id()
            if live_role_id:
                live_role = guild.get_role(live_role_id)
                if live_role:
                    # Get the Discord member linked to the Twitch user
                    # This requires manual linking or a system to know which Discord user maps to which Twitch user
                    # For now, let's assume the bot is in the server where the streamer is a member by username matching
                    member = discord.utils.get(guild.members, name=stream_info['user_name'])
                    if member and live_role not in member.roles:
                        await member.add_roles(live_role, reason="Twitch stream went live")
                else:
                    self.log.warning(f"Live role with ID {live_role_id} not found in guild {guild.name}.")
        except discord.Forbidden:
            self.log.warning(f"Bot missing permissions to send/edit messages in channel {channel.id} or manage roles in guild {guild.id}.")
        except Exception as e:
            self.log.exception(f"Error sending/editing announcement or assigning role for {streamer_username}: {e}")

    async def _remove_announcement_and_role(self, streamer_username, guild: discord.Guild):
        guild_config = self.config.guild(guild)
        streamer_settings = await guild_config.streamers.get_raw(streamer_username.lower(), {})

        channel_id = streamer_settings.get("discord_channel_id")
        if channel_id is None:
            channel_id = await guild_config.announcement_channel_id()

        channel = guild.get_channel(channel_id)
        if channel:
            message_id = streamer_settings.get("stream_message_id")
            if message_id:
                try:
                    message_to_delete = await channel.fetch_message(message_id)
                    await message_to_delete.delete()
                    streamer_settings["stream_message_id"] = None
                    await guild_config.streamers.set_raw(streamer_username.lower(), streamer_settings)
                except discord.NotFound:
                    self.log.info(f"Announcement message {message_id} already deleted for {streamer_username}.")
                    streamer_settings["stream_message_id"] = None
                    await guild_config.streamers.set_raw(streamer_username.lower(), streamer_settings)
                except discord.Forbidden:
                    self.log.warning(f"Bot missing permissions to delete message {message_id} in channel {channel.id}.")
                except Exception as e:
                    self.log.exception(f"Error deleting announcement message for {streamer_username}: {e}")

        # Remove live role
        live_role_id = await guild_config.live_role_id()
        if live_role_id:
            live_role = guild.get_role(live_role_id)
            if live_role:
                # Assuming the member mapping logic from _send_announcement
                member = discord.utils.get(guild.members, name=streamer_username) # This might need refinement for actual Discord user mapping
                if member and live_role in member.roles:
                    await member.remove_roles(live_role, reason="Twitch stream went offline")


    async def check_live_status_loop(self):
        await self.bot.wait_until_ready()
        while self:
            try:
                all_guilds_data = await self.config.all_guilds()
                all_streamers_to_check = {} # {username: [guild_id, ...]}

                for guild_id, guild_data in all_guilds_data.items():
                    if "streamers" in guild_data and guild_data["streamers"]:
                        for streamer_username_lower in guild_data["streamers"]:
                            # Streamers are stored as keys in the 'streamers' dict
                            all_streamers_to_check.setdefault(streamer_username_lower, []).append(guild_id)

                if not all_streamers_to_check:
                    await asyncio.sleep(60) # No streamers to check, wait
                    continue

                # Fetch stream info for all unique streamers across all guilds
                live_streams = {}
                try:
                    stream_info_list = await self.get_stream_info(list(all_streamers_to_check.keys()))
                    # Also fetch user info for profile images (optimization: only fetch if needed)
                    user_info_map = {u['login'].lower(): u for u in await self.get_user_info(list(all_streamers_to_check.keys()))}

                    for stream in stream_info_list:
                        live_streams[stream["user_login"].lower()] = {
                            **stream,
                            "profile_image_url": user_info_map.get(stream["user_login"].lower(), {}).get("profile_image_url", "https://static-cdn.jtvnw.net/emoticons/v1/555555555/1.0")
                        }
                except Exception as e:
                    self.log.error(f"Error fetching stream info: {e}")
                    # Continue with existing live status if API call fails
                    await asyncio.sleep(60) # Wait before retrying after API error
                    continue


                for guild_id, guild_config_data in all_guilds_data.items():
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        continue

                    current_guild_streamers_config = await self.config.guild(guild).streamers.all()

                    for streamer_username_lower in current_guild_streamers_config.keys(): # Iterate through keys of the dict
                        streamer_settings = current_guild_streamers_config[streamer_username_lower]
                        is_live_now = streamer_username_lower in live_streams

                        was_live_before = streamer_settings.get("live_status", False)

                        if is_live_now and not was_live_before:
                            # Stream just went live
                            streamer_settings["live_status"] = True
                            await self.config.guild(guild).streamers.set_raw(streamer_username_lower, streamer_settings)
                            self.bot.loop.create_task(self._send_announcement(streamer_username_lower, guild, live_streams[streamer_username_lower]))
                        elif not is_live_now and was_live_before:
                            # Stream just went offline
                            streamer_settings["live_status"] = False
                            await self.config.guild(guild).streamers.set_raw(streamer_username_lower, streamer_settings)
                            self.bot.loop.create_task(self._remove_announcement_and_role(streamer_username_lower, guild))
                        # If status hasn't changed, do nothing

                await asyncio.sleep(60) # Check every 60 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log.exception(f"Error in live status check loop: {e}")
                await asyncio.sleep(120) # Wait longer after an unexpected error

    @commands.group(invoke_without_command=True)
    async def twitchy(self, ctx):
        """Manages Twitch stream announcements and roles."""
        await ctx.send_help(ctx.command)

    @twitchy.command()
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def addstreamer(self, ctx, twitch_username: str, discord_channel: discord.TextChannel = None, *, roles: commands.Greedy[discord.Role] = None):
        """
        Adds a Twitch streamer to track.
        Provide a Discord channel for announcements and optional roles to assign when live.
        If no channel is provided, the guild's default announcement channel will be used.
        Example: `[p]twitchy addstreamer my_streamer #announcements @LiveRole`
        """
        guild = ctx.guild
        guild_config = self.config.guild(guild)
        username_lower = twitch_username.lower()

        # Check if streamer already exists
        all_streamers = await guild_config.streamers()
        if username_lower in all_streamers:
            return await ctx.send(f"Error: `{twitch_username}` is already being tracked.")

        # Get Twitch user ID to ensure valid streamer
        try:
            twitch_users = await self.get_user_info([username_lower])
            if not twitch_users:
                return await ctx.send(f"Error: Could not find Twitch user `{twitch_username}`.")
            twitch_user_id = twitch_users[0]["id"]
        except Exception as e:
            self.log.error(f"Error fetching Twitch user info for {twitch_username}: {e}")
            return await ctx.send(f"Error communicating with Twitch API: {e}")

        # Store streamer settings within the 'streamers' dictionary
        streamer_data = {
            "twitch_user_id": twitch_user_id, # Store ID for schedule fetching
            "discord_channel_id": discord_channel.id if discord_channel else None,
            "role_ids": [r.id for r in roles] if roles else [],
            "live_status": False, # Initialize as offline
            "stream_message_id": None # No announcement message yet
        }
        await guild_config.streamers.set_raw(username_lower, streamer_data)

        msg = f"Successfully added `{twitch_username}`."
        if discord_channel:
            msg += f" Announcements will go to {discord_channel.mention}."
        if roles:
            msg += f" The {humanize_list([r.name for r in roles])} role(s) will be assigned when live."
        else:
            msg += f" No specific roles will be assigned."
        await ctx.send(msg)

    @twitchy.command()
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def removestreamer(self, ctx, twitch_username: str):
        """Removes a Twitch streamer from tracking."""
        guild = ctx.guild
        guild_config = self.config.guild(guild)
        username_lower = twitch_username.lower()

        all_streamers = await guild_config.streamers()
        if username_lower not in all_streamers:
            return await ctx.send(f"Error: `{twitch_username}` is not currently being tracked.")

        # Clear the specific streamer's data from the 'streamers' group
        await guild_config.streamers.clear_raw(username_lower)
        
        # Also clean up any existing announcement message and role if the stream was live
        self.bot.loop.create_task(self._remove_announcement_and_role(username_lower, guild))

        await ctx.send(f"Successfully removed `{twitch_username}` from tracking.")

    @twitchy.command()
    @commands.guild_only()
    async def streamers(self, ctx):
        """Lists all tracked Twitch streamers for this guild."""
        guild_config = self.config.guild(ctx.guild)
        all_streamers_data = await guild_config.streamers.all()

        if not all_streamers_data:
            return await ctx.send("No Twitch streamers are currently being tracked in this guild.")

        msg = "Currently tracked Twitch streamers:\n"
        for username, data in all_streamers_data.items():
            channel_id = data.get("discord_channel_id")
            roles = [ctx.guild.get_role(r_id) for r_id in data.get("role_ids", []) if ctx.guild.get_role(r_id)]
            live_status = "LIVE" if data.get("live_status") else "OFFLINE"
            
            channel_mention = f"<#{channel_id}>" if channel_id else "Guild Default"
            roles_mention = humanize_list([r.name for r in roles]) if roles else "None"
            
            msg += f"- `{username}` ({live_status}) -> Channel: {channel_mention}, Roles: {roles_mention}\n"
        
        for page in pagify(msg):
            await ctx.send(page)

    @twitchy.command()
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def setchannel(self, ctx, discord_channel: discord.TextChannel):
        """
        Sets the default Discord channel for stream announcements for the guild.
        This will be used if no specific channel is set for an individual streamer.
        """
        await self.config.guild(ctx.guild).announcement_channel_id.set(discord_channel.id)
        await ctx.send(f"Default announcement channel set to {discord_channel.mention}.")

    @twitchy.command()
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def setliverole(self, ctx, role: discord.Role):
        """Sets the role to assign to users when their tracked Twitch stream is live."""
        await self.config.guild(ctx.guild).live_role_id.set(role.id)
        await ctx.send(f"Live role set to `{role.name}`.")

    @twitchy.command()
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def setcreds(self, ctx, client_id: str, client_secret: str, redirect_uri: str = "http://localhost:8000/callback"):
        """
        Sets your Twitch API Client ID, Client Secret, and Redirect URI.
        You need to register an application on Twitch Developers site.
        The Redirect URI defaults to `http://localhost:8000/callback`.
        """
        await self.config.twitch_client_id.set(client_id)
        await self.config.twitch_client_secret.set(client_secret)
        await self.config.twitch_redirect_uri.set(redirect_uri)
        await ctx.send(
            "Twitch API credentials saved. "
            "Now you need to get an access token. "
            f"Please visit: `https://id.twitch.tv/oauth2/authorize?response_type=code&client_id={client_id}&redirect_uri={redirect_uri}&scope=user:read:follows+channel:read:subscriptions+channel:read:schedule`\n"
            "After authorizing, you'll be redirected. Copy the `code` from the URL and use `[p]twitchy settoken <code>`."
        )

    @twitchy.command()
    @commands.is_owner()
    async def settoken(self, ctx, code: str):
        """
        Sets the Twitch access token using the authorization code.
        Use this after `[p]twitchy set creds` and visiting the authorization URL.
        """
        config_global = await self.config.get_global_settings()
        client_id = config_global["twitch_client_id"]
        client_secret = config_global["twitch_client_secret"]
        redirect_uri = config_global["twitch_redirect_uri"]

        if not all([client_id, client_secret, redirect_uri]):
            return await ctx.send("Please set your Twitch credentials first using `[p]twitchy set creds`.")

        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }

        async with self.session.post("https://id.twitch.tv/oauth2/token", data=payload) as resp:
            try:
                resp.raise_for_status()
                data = await resp.json()
                if "access_token" in data:
                    data["expires_at"] = time.time() + data["expires_in"] - 60 # Subtract 60s for buffer
                    await self.config.twitch_access_token_info.set(data)
                    await ctx.send("✅ Twitch access token obtained and saved successfully!")
                    self.bot.loop.create_task(self.refresh_twitch_token_loop()) # Ensure refresh loop starts/restarts
                else:
                    await ctx.send(f"❌ Failed to get Twitch access token: {data.get('message', 'Unknown error')}")
            except aiohttp.ClientResponseError as e:
                await ctx.send(f"❌ HTTP error getting Twitch token: {e.status} - {e.message}\n"
                               f"Response: {await resp.text()}")
            except Exception as e:
                await ctx.send(f"❌ An unexpected error occurred: {e}")

    async def _download_file(self, url, path):
        try:
            async with self.session.get(url) as response:
                response.raise_for_status()
                with open(path, "wb") as f:
                    f.write(await response.read())
            return True
        except Exception as e:
            self.log.error(f"Failed to download {url}: {e}")
            return False

    async def ensure_schedule_resources(self):
        config_global = await self.config.get_global_settings()
        custom_template_url = config_global.get("custom_template_url")
        custom_font_url = config_global.get("custom_font_url")

        success_template = False
        if custom_template_url:
            success_template = await self._download_file(custom_template_url, self.template_path)
        if not success_template and not self.template_path.exists():
            # Fallback to default if custom failed or not set
            success_template = await self._download_file("https://i.imgur.com/your_default_template.png", self.template_path) # REPLACE with actual default template URL!
            if not success_template:
                self.log.error("Failed to download default schedule template.")
                return False

        success_font = False
        if custom_font_url:
            success_font = await self._download_file(custom_font_url, self.font_path)
        if not success_font and not self.font_path.exists():
            # Fallback to default if custom failed or not set
            success_font = await self._download_file("https://raw.githubusercontent.com/googlefonts/roboto/main/src/hinted/Roboto-Regular.ttf", self.font_path) # Example Google Fonts URL
            if not success_font:
                self.log.error("Failed to download default schedule font.")
                return False
        
        return success_template and success_font

    async def fetch_twitch_schedule(self, broadcaster_id: str):
        headers = await self.get_twitch_headers()
        params = {"broadcaster_id": broadcaster_id}
        async with self.session.get("https://api.twitch.tv/helix/schedule", headers=headers, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["data"]

    async def generate_schedule_image(self, schedule_data: list, start_date: datetime.datetime, guild_name: str, guild_icon_url: str = None):
        if not self.template_path.exists() or not self.font_path.exists():
            if not await self.ensure_schedule_resources():
                self.log.error("Failed to ensure schedule resources are available for image generation.")
                return None

        try:
            template_img = Image.open(self.template_path).convert("RGBA")
            draw = ImageDraw.Draw(template_img)

            # Load font
            try:
                font_large = ImageFont.truetype(str(self.font_path), 40)
                font_medium = ImageFont.truetype(str(self.font_path), 30)
                font_small = ImageFont.truetype(str(self.font_path), 25)
            except IOError:
                self.log.error(f"Could not load font from {self.font_path}. Using default Pillow font.")
                font_large = ImageFont.load_default()
                font_medium = ImageFont.load_default()
                font_small = ImageFont.load_default()

            # Add guild name and icon (example placement)
            draw.text((50, 50), f"{guild_name} Twitch Schedule", fill=(255, 255, 255), font=font_large)
            if guild_icon_url:
                try:
                    async with self.session.get(guild_icon_url) as resp:
                        resp.raise_for_status()
                        icon_data = await resp.read()
                        icon_img = Image.open(io.BytesIO(icon_data)).convert("RGBA")
                        icon_img = icon_img.resize((60, 60))
                        template_img.paste(icon_img, (template_img.width - 100, 40), icon_img)
                except Exception as e:
                    self.log.warning(f"Failed to fetch guild icon: {e}")

            # Schedule details (example layout, needs adjustment for actual design)
            y_offset = 150
            for i, segment in enumerate(schedule_data[:await self.config.guild(self.bot.get_guild(schedule_data[0]['broadcaster_id'])).schedule_event_count() if schedule_data else 5]): # Use config limit
                segment_start_time_utc = dateutil.parser.isoparse(segment["start_time"]).replace(tzinfo=datetime.timezone.utc)
                # Ensure the guild timezone is correctly loaded from config
                guild_id = segment['broadcaster_id'] # Assuming broadcaster_id can be used to get guild config
                # Need to map broadcaster_id to guild_id to get guild_config
                # This is tricky as a streamer can be tracked in multiple guilds.
                # For image generation, if a specific timezone is needed, it must be passed in.
                # For now, let's use the start_date's timezone or assume UTC conversion if it's for one guild.
                # A more robust solution would be to pass the guild_id or guild_tz directly.
                # For now, we use start_date's tzinfo for segment display.
                
                # Use the timezone provided in start_date, which will come from guild_config.schedule_timezone()
                segment_start_time_local = segment_start_time_utc.astimezone(start_date.tzinfo) 

                title = segment.get("title", "No Title")
                category = segment.get("category", {}).get("name", "N/A")
                start_time_str = segment_start_time_local.strftime("%a, %b %d - %H:%M") # e.g., Mon, Jan 01 - 18:00

                draw.text((50, y_offset), f"{start_time_str} - {title}", fill=(255, 255, 255), font=font_medium)
                draw.text((70, y_offset + 30), f"Category: {category}", fill=(200, 200, 200), font=font_small)
                y_offset += 80 # Spacing for next event

            # Convert to BytesIO
            byte_arr = io.BytesIO()
            template_img.save(byte_arr, format="PNG")
            byte_arr.seek(0)
            return byte_arr

        except Exception as e:
            self.log.exception(f"Error generating schedule image: {e}")
            return None


    async def post_schedule(self, channel: discord.TextChannel, broadcaster_id: str, ping_role: discord.Role = None, start_date: datetime.datetime = None):
        """Fetches and posts the Twitch schedule to a Discord channel."""
        try:
            schedule_data_full = await self.fetch_twitch_schedule(broadcaster_id)
            if not schedule_data_full or not schedule_data_full.get("segments"):
                return await channel.send("Could not fetch schedule or schedule is empty for this streamer.")

            guild_config = self.config.guild(channel.guild)
            timezone_str = await guild_config.schedule_timezone()
            try:
                guild_tz = pytz.timezone(timezone_str)
            except pytz.UnknownTimeZoneError:
                self.log.warning(f"Unknown timezone: {timezone_str}. Falling back to UTC for schedule display.")
                guild_tz = pytz.utc # Fallback

            # Filter segments for the relevant period (e.g., next 7 days from start_date or current date)
            if not start_date:
                start_date = datetime.datetime.now(guild_tz).replace(hour=0, minute=0, second=0, microsecond=0)

            end_date = start_date + timedelta(days=await guild_config.schedule_update_days_in_advance())
            
            filtered_schedule = []
            for segment in schedule_data_full["segments"]:
                segment_start_time_utc = dateutil.parser.isoparse(segment["start_time"]).replace(tzinfo=datetime.timezone.utc)
                segment_start_time_local = segment_start_time_utc.astimezone(guild_tz)
                if start_date <= segment_start_time_local < end_date:
                    filtered_schedule.append(segment)
            
            if not filtered_schedule:
                return await channel.send(f"No schedule events found for the next {await guild_config.schedule_update_days_in_advance()} days.")


            filtered_schedule.sort(key=lambda x: dateutil.parser.isoparse(x["start_time"]))

            # Ensure image resources are ready
            image_buffer = await self.generate_schedule_image(
                filtered_schedule,
                start_date.astimezone(guild_tz), # Pass timezone-aware start_date
                channel.guild.name,
                channel.guild.icon.url if channel.guild.icon else None
            )

            if image_buffer:
                file = discord.File(image_buffer, filename="twitch_schedule.png")
                content = f"{ping_role.mention}" if ping_role else "Here's the upcoming Twitch schedule:"
                await channel.send(content=content, file=file)
            else:
                await channel.send("Failed to generate the schedule image.")

        except aiohttp.ClientResponseError as e:
            self.log.error(f"HTTP error fetching Twitch schedule: {e.status} - {e.message}")
            await channel.send(f"❌ Failed to fetch schedule from Twitch! HTTP Error: {e.status}")
        except Exception as e:
            self.log.exception(f"Error posting Twitch schedule: {e}")
            await channel.send("❌ An unexpected error occurred while posting the schedule.")


    async def update_all_guild_schedules_loop(self):
        await self.bot.wait_until_ready()
        while self:
            try:
                # Calculate sleep until next scheduled update time (e.g., next Monday 00:00)
                # This should ideally be configurable per guild, but for now, we'll use a fixed logic.
                # Get all guilds that have schedule_channel_id set
                all_guild_data = await self.config.all_guilds()
                
                next_update_times = []
                for guild_id, data in all_guild_data.items():
                    if data.get("schedule_channel_id") and data.get("schedule_twitch_username"):
                        guild_tz_str = data.get("schedule_timezone", "UTC") # Get timezone from config
                        try:
                            guild_tz = pytz.timezone(guild_tz_str)
                        except pytz.UnknownTimeZoneError:
                            self.log.warning(f"Unknown timezone {guild_tz_str} for guild {guild_id}. Using UTC.")
                            guild_tz = pytz.utc
                        
                        schedule_update_time_str = data.get("schedule_update_time", "00:00")
                        try:
                            hour, minute = map(int, schedule_update_time_str.split(':'))
                        except ValueError:
                            self.log.error(f"Invalid schedule_update_time '{schedule_update_time_str}' for guild {guild_id}. Using 00:00.")
                            hour, minute = 0, 0

                        now_local = datetime.datetime.now(guild_tz)
                        target_time_today = now_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
                        
                        # If target time has passed for today, set for tomorrow
                        if now_local >= target_time_today:
                            target_time_today += timedelta(days=1)
                        next_update_times.append(target_time_today)
                
                if not next_update_times:
                    self.log.debug("No guilds configured for automatic schedule updates. Sleeping for 1 hour.")
                    await asyncio.sleep(3600)
                    continue

                # Find the earliest next update time
                earliest_next_update = min(next_update_times)
                sleep_duration = (earliest_next_update - datetime.datetime.now(earliest_next_update.tzinfo)).total_seconds()
                
                if sleep_duration <= 0: # Should not happen if logic above is correct, but for safety
                    sleep_duration = 60 # Try again in 1 minute if somehow past time

                self.log.info(f"Next full schedule update in {int(sleep_duration)} seconds at {earliest_next_update.isoformat()}.")
                await asyncio.sleep(sleep_duration)

                # Time to update schedules
                for guild_id, data in all_guild_data.items():
                    if data.get("schedule_channel_id") and data.get("schedule_twitch_username"):
                        guild = self.bot.get_guild(guild_id)
                        if not guild:
                            continue
                        channel = guild.get_channel(data["schedule_channel_id"])
                        if not channel:
                            self.log.warning(f"Schedule channel {data['schedule_channel_id']} not found for guild {guild_id}.")
                            continue
                        
                        broadcaster_username = data["schedule_twitch_username"]
                        ping_role_id = data.get("schedule_ping_role_id")
                        ping_role = guild.get_role(ping_role_id) if ping_role_id else None

                        try:
                            twitch_user_info = await self.get_user_info([broadcaster_username])
                            if not twitch_user_info:
                                self.log.warning(f"Could not find Twitch user {broadcaster_username} for guild {guild_id}'s schedule.")
                                await channel.send(f"❌ Failed to update schedule: Twitch user `{broadcaster_username}` not found.")
                                continue
                            broadcaster_id = twitch_user_info[0]["id"]
                            await self.post_schedule(channel, broadcaster_id, ping_role)
                        except Exception as e:
                            self.log.error(f"Error updating schedule for guild {guild_id}: {e}")
                            await channel.send("❌ Failed to update schedule due to an unexpected error.")
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log.exception(f"Unhandled error in schedule update loop: {e}")
                await asyncio.sleep(300) # Wait 5 minutes after unhandled error

    @commands.group()
    @commands.guild_only()
    async def twitchy_schedule(self, ctx):
        """Commands for managing Twitch schedule display."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @twitchy_schedule.command(name="setchannel")
    @commands.has_permissions(manage_guild=True)
    async def schedule_set_channel(self, ctx, channel: discord.TextChannel):
        """Sets the Discord channel where the Twitch schedule will be posted."""
        await self.config.guild(ctx.guild).schedule_channel_id.set(channel.id)
        await ctx.send(f"Twitch schedule will now be posted in {channel.mention}.")

    @twitchy_schedule.command(name="setstreamer")
    @commands.has_permissions(manage_guild=True)
    async def schedule_set_streamer(self, ctx, twitch_username: str):
        """Sets the Twitch streamer whose schedule will be displayed for this guild."""
        try:
            # Verify streamer exists on Twitch
            twitch_users = await self.get_user_info([twitch_username.lower()])
            if not twitch_users:
                return await ctx.send(f"Error: Could not find Twitch user `{twitch_username}`.")
            
            await self.config.guild(ctx.guild).schedule_twitch_username.set(twitch_username.lower())
            await ctx.send(f"Schedule display streamer set to `{twitch_username}`.")
        except Exception as e:
            self.log.error(f"Error setting schedule streamer: {e}")
            await ctx.send(f"Error communicating with Twitch API: {e}")

    @twitchy_schedule.command(name="settimezone")
    @commands.has_permissions(manage_guild=True)
    async def schedule_set_timezone(self, ctx, timezone_str: str):
        """
        Sets the timezone for displaying the schedule (e.g., "Europe/London", "America/New_York").
        Valid timezones can be found at: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones
        """
        try:
            pytz.timezone(timezone_str) # Validate timezone string
            await self.config.guild(ctx.guild).schedule_timezone.set(timezone_str)
            await ctx.send(f"Schedule display timezone set to `{timezone_str}`.")
        except pytz.UnknownTimeZoneError:
            await ctx.send(f"Error: `{timezone_str}` is not a valid timezone. Please use a valid timezone string (e.g., 'America/New_York').")
        except Exception as e:
            self.log.error(f"Error setting schedule timezone: {e}")
            await ctx.send(f"An unexpected error occurred: {e}")

    @twitchy_schedule.command(name="setdays")
    @commands.has_permissions(manage_guild=True)
    async def schedule_set_days(self, ctx, days: int):
        """Sets how many days of the schedule to display (e.g., 7 for a week)."""
        if days < 1 or days > 30: # Limit to a reasonable range
            return await ctx.send("Please provide a number of days between 1 and 30.")
        await self.config.guild(ctx.guild).schedule_update_days_in_advance.set(days)
        await ctx.send(f"Schedule will display {days} days in advance.")

    @twitchy_schedule.command(name="settime")
    @commands.has_permissions(manage_guild=True)
    async def schedule_set_time(self, ctx, time_str: str):
        """Sets the 24-hour time (HH:MM) when the schedule automatically updates (e.g., "00:00")."""
        if not re.match(r"^(2[0-3]|[01]?[0-9]):([0-5]?[0-9])$", time_str):
            return await ctx.send("Please provide a time in HH:MM (24-hour) format, e.g., `00:00` or `14:30`.")
        await self.config.guild(ctx.guild).schedule_update_time.set(time_str)
        await ctx.send(f"Schedule auto-update time set to `{time_str}` local time.")

    @twitchy_schedule.command(name="setpingrole")
    @commands.has_permissions(manage_guild=True)
    async def schedule_set_ping_role(self, ctx, role: discord.Role = None):
        """
        Sets a role to ping when the schedule updates.
        Pass no role to clear the setting.
        """
        if role:
            await self.config.guild(ctx.guild).schedule_ping_role_id.set(role.id)
            await ctx.send(f"Schedule update ping role set to `{role.name}`.")
        else:
            await self.config.guild(ctx.guild).schedule_ping_role_id.set(None)
            await ctx.send("Schedule update ping role cleared.")

    @twitchy_schedule.command(name="seteventcount")
    @commands.has_permissions(manage_guild=True)
    async def schedule_set_event_count(self, ctx, count: int):
        """Sets the maximum number of schedule events to show in the image."""
        if count < 1 or count > 20: # Limit to a reasonable range
            return await ctx.send("Please provide an event count between 1 and 20.")
        await self.config.guild(ctx.guild).schedule_event_count.set(count)
        await ctx.send(f"Schedule image will display up to {count} events.")

    @twitchy_schedule.command(name="show")
    async def schedule_show(self, ctx):
        """Displays the current Twitch schedule for the configured streamer."""
        guild_config = self.config.guild(ctx.guild)
        broadcaster_username = await guild_config.schedule_twitch_username()
        schedule_channel_id = await guild_config.schedule_channel_id()

        if not broadcaster_username:
            return await ctx.send("No streamer set for schedule display. Use `[p]twitchy schedule setstreamer <username>`.")
        if not schedule_channel_id:
            return await ctx.send("No schedule channel set. Use `[p]twitchy schedule setchannel <channel>`.")
        
        twitch_user_info = await self.get_user_info([broadcaster_username])
        if not twitch_user_info:
            return await ctx.send(f"Could not find Twitch user `{broadcaster_username}`. Please check the username.")
        broadcaster_id = twitch_user_info[0]["id"]

        ping_role_id = await guild_config.schedule_ping_role_id()
        ping_role = ctx.guild.get_role(ping_role_id) if ping_role_id else None

        # Pass the current channel for immediate display
        await self.post_schedule(ctx.channel, broadcaster_id, ping_role)
        await ctx.send("✅ Schedule posted!")

    @twitchy_schedule.command(name="test")
    @commands.has_permissions(manage_guild=True)
    async def schedule_test(self, ctx):
        """Tests schedule fetching and image generation."""
        await ctx.send("🔄 Testing schedule fetching and image generation...")
        
        guild_config = self.config.guild(ctx.guild)
        broadcaster_username = await guild_config.schedule_twitch_username()
        
        if not broadcaster_username:
            return await ctx.send("No streamer set for schedule display. Use `[p]twitchy schedule setstreamer <username>` to configure for testing.")

        try:
            twitch_user_info = await self.get_user_info([broadcaster_username])
            if not twitch_user_info:
                return await ctx.send(f"Could not find Twitch user `{broadcaster_username}`. Please check the username.")
            broadcaster_id = twitch_user_info[0]["id"]
            
            # Use current guild's timezone for testing
            timezone_str = await guild_config.schedule_timezone()
            try:
                guild_tz = pytz.timezone(timezone_str)
            except pytz.UnknownTimeZoneError:
                guild_tz = pytz.utc # Fallback for test

            # For test, get schedule for next X days starting from today
            start_of_period = datetime.datetime.now(guild_tz).replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Fetch schedule data directly
            schedule_data_full = await self.fetch_twitch_schedule(broadcaster_id)
            if not schedule_data_full or not schedule_data_full.get("segments"):
                return await ctx.send("❌ Failed to fetch schedule or schedule is empty from Twitch! Check the bot's console for errors.")

            end_of_period = start_of_period + timedelta(days=await guild_config.schedule_update_days_in_advance())
            
            filtered_schedule = []
            for seg in schedule_data_full["segments"]:
                seg_start_time_utc = dateutil.parser.isoparse(seg["start_time"]).replace(tzinfo=datetime.timezone.utc)
                seg_start_time_local = seg_start_time_utc.astimezone(guild_tz)
                if start_of_period <= seg_start_time_local < end_of_period:
                    filtered_schedule.append(seg)
            filtered_schedule.sort(key=lambda x: dateutil.parser.isoparse(x["start_time"]))

            # Send to current context channel for testing
            await self.post_schedule(ctx.channel, broadcaster_id, start_date=start_of_period)
            await ctx.send("✅ Test complete!")
        except Exception as e:
            self.log.exception("Error during schedule test command:")
            await ctx.send(f"❌ Failed to fetch schedule from Twitch! An unexpected error occurred: {e}")

    @twitchy_schedule.command(name="reload")
    async def schedule_reload_resources(self, ctx):
        """Force redownload of the schedule template image and font files."""
        await ctx.send("🔄 Redownloading schedule resources...")
        
        # Delete existing files to force re-download
        if self.font_path.exists():
            os.remove(self.font_path)
        if self.template_path.exists():
            os.remove(self.template_path)
        
        success = await self.ensure_schedule_resources()
        
        if success:
            await ctx.send("✅ Successfully redownloaded schedule resources.")
        else:
            await ctx.send("❌ Failed to redownload schedule resources. Check bot console for details.")