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

        # Removed global twitch client_id/secret/token, now using shared API tokens.
        default_global = {
            "streamers": {}, # Stores {"twitch_id": {"username": "", "discord_channel_id": int, "ping_role_ids": [int], "last_announced_stream_id": str, "is_live": bool}}
        }

        # Per-guild config for both Live Role and Schedule features
        default_guild = {
            "live_role_id": None, # Guild-specific live role (for Discord activity detection)
            
            # Schedule-related configurations
            "schedule_channel_id": None,
            "schedule_twitch_username": None, # This should likely be stored per streamer too, but for simplicity, matching original cog.
            "schedule_update_days": [], # List of weekdays (0=Monday, 6=Sunday)
            "schedule_update_time": None, # "HH:MM" format
            "schedule_message_id": None, # ID of the pinned schedule image message
            "schedule_notify_role_id": None,
            "schedule_event_count": 5, # Number of events to show on the image
            "schedule_timezone": "Europe/London" # Default timezone for schedule display and calculations
        }

        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)

        self.session = aiohttp.ClientSession()
        self.twitch_api_base_url = "https://api.twitch.tv/helix/"
        self.access_token = None
        self.token_expires_at = 0

        self.check_loop = self.bot.loop.create_task(self.check_streams_loop())
        self.schedule_task = self.bot.loop.create_task(self.schedule_update_loop()) # New schedule loop

        # Cache directory for fonts and templates
        self.cache_dir = data_manager.cog_data_path(self) / "cache"
        self.font_path = self.cache_dir / "P22.ttf"
        self.template_path = self.cache_dir / "schedule.png"
        
        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)


    async def red_delete_data_for_user(self, *, requester, user_id):
        """No user data to delete for Twitchy."""
        return

    def cog_unload(self):
        """Clean up when the cog is unloaded."""
        if self.check_loop:
            self.check_loop.cancel()
        if self.schedule_task: # Cancel the new schedule task
            self.schedule_task.cancel()
        if self.session:
            asyncio.create_task(self.session.close()) # Use asyncio.create_task for proper async cleanup
        self.bot.dispatch("twitchy_cog_unload") # Dispatch event for potential external listeners

    async def get_twitch_credentials(self):
        """Fetches Twitch API client ID and secret from Red's shared API tokens."""
        tokens = await self.bot.get_shared_api_tokens("twitch")
        client_id = tokens.get("client_id")
        client_secret = tokens.get("client_secret")
        if not client_id or not client_secret:
            self.log.warning("Twitch API client ID or secret not set. Use `[p]set api twitch client_id <id>` and `[p]set api twitch client_secret <secret>`.")
            return None, None
        return client_id, client_secret

    async def get_twitch_access_token(self):
        """Fetches and stores a new Twitch API access token."""
        client_id, client_secret = await self.get_twitch_credentials()
        if not client_id or not client_secret:
            return None

        if self.token_expires_at > time.time() + 60: # Token valid for at least 60 more seconds
            return self.access_token

        token_url = "https://id.twitch.tv/oauth2/token"
        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials"
        }

        try:
            async with self.session.post(token_url, data=payload) as response:
                response.raise_for_status()
                data = await response.json()
                access_token = data.get("access_token")
                expires_in = data.get("expires_in")

                if access_token:
                    self.access_token = access_token
                    self.token_expires_at = time.time() + expires_in
                    self.log.info("Twitchy: Successfully obtained new Twitch access token.")
                    return self.access_token
                else:
                    self.log.error("Twitchy: Failed to get access token from Twitch response.")
                    return None
        except aiohttp.ClientError as e:
            self.log.error(f"Twitchy: Failed to connect to Twitch for token: {e}")
            return None
        except Exception as e:
            self.log.error(f"Twitchy: An unexpected error occurred while getting token: {e}")
            return None

    async def get_twitch_user_info(self, username: str = None, user_id: str = None):
        """Fetches Twitch user info by username or user ID."""
        if not username and not user_id:
            return None

        token = await self.get_twitch_access_token()
        client_id, _ = await self.get_twitch_credentials()
        if not token or not client_id:
            self.log.warning("Twitchy: API keys or token missing for user info.")
            return None

        headers = {
            "Authorization": f"Bearer {token}",
            "Client-Id": client_id
        }
        params = {"login": username} if username else {"id": user_id}

        try:
            async with self.session.get(f"{self.twitch_api_base_url}users", headers=headers, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                users = data.get("data")
                if users:
                    return users[0]
                return None
        except aiohttp.ClientResponseError as e:
            self.log.error(f"Twitchy: API error fetching user {'login' if username else 'id'} {username or user_id}: {e.status} - {e.message}")
            return None
        except aiohttp.ClientError as e:
            self.log.error(f"Twitchy: Network error fetching user {'login' if username else 'id'} {username or user_id}: {e}")
            return None
        except Exception as e:
            self.log.error(f"Twitchy: An unexpected error occurred fetching user {'login' if username else 'id'} {username or user_id}: {e}")
            return None

    async def get_twitch_streams_info(self, twitch_ids: list):
        """Fetches live stream info for a list of Twitch IDs."""
        if not twitch_ids:
            return []

        token = await self.get_twitch_access_token()
        client_id, _ = await self.get_twitch_credentials()
        if not token or not client_id:
            self.log.warning("Twitchy: API keys or token missing for stream info.")
            return []

        headers = {
            "Authorization": f"Bearer {token}",
            "Client-Id": client_id
        }
        params = [("user_id", tid) for tid in twitch_ids]

        try:
            async with self.session.get(f"{self.twitch_api_base_url}streams", headers=headers, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                return data.get("data", [])
        except aiohttp.ClientResponseError as e:
            self.log.error(f"Twitchy: API error fetching streams: {e.status} - {e.message}")
            return []
        except aiohttp.ClientError as e:
            self.log.error(f"Twitchy: Network error fetching streams: {e}")
            return []
        except Exception as e:
            self.log.error(f"Twitchy: An unexpected error occurred fetching streams: {e}")
            return []

    async def send_stream_announcement(self, streamer_config: dict, stream_data: dict):
        """Constructs and sends a stream announcement embed."""
        channel_id = streamer_config.get("discord_channel_id")
        channel = self.bot.get_channel(channel_id)
        if not channel:
            self.log.warning(f"Twitchy: Discord channel {channel_id} not found for {streamer_config['username']}.")
            return

        ping_role_ids = streamer_config.get("ping_role_ids", [])
        pings = ""
        for role_id in ping_role_ids:
            role = channel.guild.get_role(role_id)
            if role:
                pings += f"{role.mention} "
            else:
                self.log.warning(f"Twitchy: Role {role_id} not found in guild {channel.guild.name}.")
        pings = pings.strip()

        stream_url = f"https://www.twitch.tv/{stream_data['user_login']}"
        subscribe_url = f"https://www.twitch.tv/subs/{stream_data['user_login']}"
        thumbnail_url = stream_data["thumbnail_url"].replace("{width}", "1280").replace("{height}", "720")
        
        thumbnail_url += f"?{int(time.time())}" # Add unique query param to avoid Discord caching issues

        embed = discord.Embed(
            title=f"🔴 {stream_data['user_name']} is now LIVE on Twitch!",
            url=stream_url,
            description=f"**{stream_data['title']}**\nPlaying: `{stream_data['game_name']}`",
            color=discord.Color.purple()
        )
        user_info = await self.get_twitch_user_info(user_id=stream_data["user_id"])
        profile_image_url = user_info["profile_image_url"] if user_info and "profile_image_url" in user_info else None
        
        embed.set_author(name=stream_data['user_name'], url=stream_url, icon_url=profile_image_url)
        embed.set_image(url=thumbnail_url)
        embed.set_footer(text="Twitchy Stream Alerts")
        embed.timestamp = discord.utils.utcnow()

        view = StreamButtons(stream_url, subscribe_url)

        try:
            await channel.send(pings, embed=embed, view=view)
            self.log.info(f"Twitchy: Announced {stream_data['user_login']} going live in #{channel.name}.")
        except discord.Forbidden:
            self.log.warning(f"Twitchy: Missing permissions to send message in {channel.name} for {stream_data['user_login']}.")
        except Exception as e:
            self.log.error(f"Twitchy: Failed to send announcement for {stream_data['user_login']}: {e}")

    async def check_streams_loop(self):
        await self.bot.wait_until_ready()
        while self is self.bot.get_cog("Twitchy"):
            try:
                streamers_config = await self.config.streamers()
                if not streamers_config:
                    await asyncio.sleep(60)
                    continue

                twitch_ids_to_check = list(streamers_config.keys())
                live_streams = await self.get_twitch_streams_info(twitch_ids_to_check)
                live_stream_ids = {stream["user_id"] for stream in live_streams}

                async with self.config.streamers() as streamers_to_update:
                    for twitch_id, streamer_data in streamers_to_update.items():
                        username = streamer_data["username"]
                        was_live = streamer_data.get("is_live", False)
                        
                        is_currently_live = twitch_id in live_stream_ids
                        current_stream_data = next((s for s in live_streams if s["user_id"] == twitch_id), None)
                        
                        if is_currently_live and not was_live:
                            if streamer_data.get("last_announced_stream_id") != current_stream_data["id"]:
                                await self.send_stream_announcement(streamer_data, current_stream_data)
                                streamer_data["last_announced_stream_id"] = current_stream_data["id"]
                                streamer_data["is_live"] = True
                                self.log.info(f"Twitchy: {username} went live! Announced.")
                            else:
                                streamer_data["is_live"] = True
                                self.log.debug(f"Twitchy: {username} is live, but already announced this stream.")

                        elif not is_currently_live and was_live:
                            streamer_data["is_live"] = False
                            streamer_data["last_announced_stream_id"] = None
                            self.log.info(f"Twitchy: {username} went offline.")

            except asyncio.CancelledError:
                self.log.info("Twitchy: Stream checking loop cancelled.")
                break
            except Exception as e:
                self.log.error(f"Twitchy: An error occurred in check_streams_loop: {e}", exc_info=True)

            await asyncio.sleep(60)

    @commands.Cog.listener()
    async def on_presence_update(self, before: discord.Member, after: discord.Member):
        """
        Listens for Discord presence updates to assign/remove 'Live' roles.
        """
        if after.bot:
            return

        guild = after.guild
        if not guild:
            return

        live_role_id = await self.config.guild(guild).live_role_id()
        if not live_role_id:
            return

        live_role = guild.get_role(live_role_id)
        if not live_role:
            await self.config.guild(guild).live_role_id.set(None)
            self.log.warning(f"Twitchy: Live role ID {live_role_id} not found in guild {guild.name}. Config cleared.")
            return

        is_streaming_now = any(isinstance(activity, discord.Streaming) for activity in after.activities)

        try:
            if is_streaming_now and live_role not in after.roles:
                await after.add_roles(live_role, reason="Twitchy: User is streaming on Discord.")
                self.log.info(f"Twitchy: Added '{live_role.name}' role to {after.display_name} in {guild.name}.")
            elif not is_streaming_now and live_role in after.roles:
                await after.remove_roles(live_role, reason="Twitchy: User stopped streaming on Discord.")
                self.log.info(f"Twitchy: Removed '{live_role.name}' role from {after.display_name} in {guild.name}.")
        except discord.Forbidden:
            self.log.warning(f"Twitchy: Missing permissions to manage roles for {after.display_name} in {guild.name}.")
        except Exception as e:
            self.log.error(f"Twitchy: An error occurred while managing live role for {after.display_name}: {e}", exc_info=True)


    @commands.group(name="twitchy")
    @commands.is_owner() # Only bot owner can set API keys, but other commands can be guild admin.
    async def twitchy(self, ctx):
        """Manages Twitch stream alerts and schedule announcements."""
        pass

    @twitchy.command(name="setup")
    async def twitchy_setup(self, ctx):
        """
        Setup instructions for Twitch API keys using Red's `[p]set api` command.
        """
        embed = discord.Embed(
            title="Twitchy Setup",
            description=(
                "Twitchy requires Twitch API credentials (`Client ID` and `Client Secret`) "
                "to function correctly.\n\n"
                "**How to set them up:**\n"
                "1. Go to the Twitch Developer Console: <https://dev.twitch.tv/console/apps>\n"
                "2. Log in with your Twitch account.\n"
                "3. Click `Register Your Application` (or `New Application`).\n"
                "4. Fill in the details:\n"
                "   - **Name:** `YourBotName` (or anything descriptive)\n"
                "   - **OAuth Redirect URLs:** Add `http://localhost` (this is generally sufficient for bot credentials).\n"
                "   - **Category:** Choose `Chat Bot`.\n"
                "5. After creating, copy your `Client ID`.\n"
                "6. Click `New Secret` to generate and copy your `Client Secret`.\n\n"
                "**Then, use Red's built-in API key setter:**\n"
                f"   `{ctx.clean_prefix}set api twitch client_id <YOUR_CLIENT_ID>`\n"
                f"   `{ctx.clean_prefix}set api twitch client_secret <YOUR_CLIENT_SECRET>`\n\n"
                "Once done, reload the cog with `[p]reload twitchy`."
            ),
            color=discord.Color.blue()
        )
        embed.set_footer(text="Your keys are stored securely by Red and not directly in Twitchy's config.")
        await ctx.send(embed=embed)


    @twitchy.command(name="addstreamer")
    async def twitchy_addstreamer(self, ctx, twitch_username: str, discord_channel: discord.TextChannel, *roles: discord.Role):
        """
        Adds a Twitch streamer to monitor for live announcements.
        Usage: [p]twitchy addstreamer <twitch_username> <#discord_channel> [role1] [role2]...
        Example: [p]twitchy addstreamer mycoolstreamer #stream-alerts @LiveRole @Everyone
        """
        twitch_username = twitch_username.lower()

        client_id, client_secret = await self.get_twitch_credentials()
        if not client_id or not client_secret:
            return await ctx.send(
                "❌ Twitch API keys are not set. Please run `[p]twitchy setup` first "
                "and then use `[p]set api` to configure your keys."
            )

        await ctx.send(f"Checking Twitch for user `{twitch_username}`...")
        twitch_user_info = await self.get_twitch_user_info(username=twitch_username)

        if not twitch_user_info:
            return await ctx.send(
                f"❌ Could not find Twitch user `{twitch_username}`. "
                "Please ensure the username is correct."
            )

        twitch_id = twitch_user_info["id"]
        actual_twitch_username = twitch_user_info["login"]

        async with self.config.streamers() as streamers:
            if twitch_id in streamers:
                return await ctx.send(
                    f"❌ `{actual_twitch_username}` is already being monitored. "
                    "Use `[p]twitchy removestreamer` to remove them first if you want to reconfigure."
                )

            ping_role_ids = [role.id for role in roles]
            
            streamers[twitch_id] = {
                "username": actual_twitch_username,
                "discord_channel_id": discord_channel.id,
                "ping_role_ids": ping_role_ids,
                "last_announced_stream_id": None,
                "is_live": False
            }

        ping_roles_names = humanize_list([role.name for role in roles]) if roles else "No roles"
        await ctx.send(
            f"✅ Successfully added Twitch streamer `{actual_twitch_username}`.\n"
            f"Announcements will be sent to `{discord_channel.name}`.\n"
            f"Roles to ping: {ping_roles_names}."
        )

    @twitchy.command(name="removestreamer")
    async def twitchy_removestreamer(self, ctx, twitch_username: str):
        """
        Removes a Twitch streamer from monitoring for live announcements.
        Usage: [p]twitchy removestreamer <twitch_username>
        Example: [p]twitchy removestreamer mycoolstreamer
        """
        twitch_username = twitch_username.lower()
        
        streamers = await self.config.streamers()
        found_id = None
        for twitch_id, data in streamers.items():
            if data["username"].lower() == twitch_username:
                found_id = twitch_id
                break
        
        if not found_id:
            return await ctx.send(f"❌ `{twitch_username}` is not currently being monitored.")

        async with self.config.streamers() as streamers_conf:
            del streamers_conf[found_id]
        
        await ctx.send(f"✅ Successfully removed `{twitch_username}` from monitoring.")

    @twitchy.command(name="liststreamers")
    async def twitchy_liststreamers(self, ctx):
        """Lists all Twitch streamers currently being monitored for live announcements."""
        streamers = await self.config.streamers()
        if not streamers:
            return await ctx.send("No Twitch streamers are currently being monitored for live alerts. Use `[p]twitchy addstreamer` to add some.")

        embed = discord.Embed(
            title="Monitored Twitch Streamers (Live Alerts)",
            color=discord.Color.blue()
        )
        
        description = []
        for twitch_id, data in streamers.items():
            username = data["username"]
            channel_id = data["discord_channel_id"]
            ping_role_ids = data["ping_role_ids"]
            is_live = data.get("is_live", False)

            channel = self.bot.get_channel(channel_id)
            channel_name = channel.name if channel else f"Unknown Channel ({channel_id})"

            roles_mention = []
            if ping_role_ids:
                for role_id in ping_role_ids:
                    role = ctx.guild.get_role(role_id) if ctx.guild else None
                    roles_mention.append(role.mention if role else f"<Role ID: {role_id}>")
            
            roles_text = humanize_list(roles_mention) if roles_mention else "None"
            live_status = "🔴 LIVE" if is_live else "⚪ Offline"
            
            description.append(
                f"**{username}** ({live_status})\n"
                f"  - Announce to: #{channel_name}\n"
                f"  - Ping roles: {roles_text}\n"
            )
        
        if description:
            for page in pagify("\n".join(description), shorten_by=0, page_length=1000):
                embed.description = page
                await ctx.send(embed=embed)
                embed.description = None # Clear description for subsequent pages
        else:
            embed.description = "No streamers configured."
            await ctx.send(embed=embed)

    @twitchy.command(name="setliverole")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_roles=True) # Added permission check
    async def twitchy_setliverole(self, ctx, role: discord.Role):
        """
        Sets the role that will be assigned to Discord members in this guild
        when Discord detects they are streaming on Twitch/YouTube.
        Usage: [p]twitchy setliverole <role_name_or_id>
        Example: [p]twitchy setliverole @Live
        """
        await self.config.guild(ctx.guild).live_role_id.set(role.id)
        await ctx.send(
            f"✅ The '{role.name}' role has been set as the 'Live' role for this server. "
            "Users who are visibly streaming on Discord will now automatically get this role."
        )

    @twitchy.command(name="check")
    async def twitchy_check(self, ctx, twitch_username: str = None):
        """
        Manually checks for a stream's live status and forces an announcement if live.
        Usage: [p]twitchy check [twitch_username]
        Example: [p]twitchy check mycoolstreamer (checks specific streamer)
        Example: [p]twitchy check (checks all monitored streamers)
        """
        streamers_config = await self.config.streamers()
        
        if not streamers_config:
            return await ctx.send("No streamers are configured to monitor. Use `[p]twitchy addstreamer`.")
        
        target_twitch_id = None
        if twitch_username:
            twitch_username = twitch_username.lower()
            found = False
            for twitch_id, data in streamers_config.items():
                if data["username"].lower() == twitch_username:
                    target_twitch_id = twitch_id
                    found = True
                    break
            if not found:
                return await ctx.send(f"❌ Streamer `{twitch_username}` is not configured for monitoring.")
        
        await ctx.send("🔄 Checking stream status now, please wait...")
        
        twitch_ids_to_check = [target_twitch_id] if target_twitch_id else list(streamers_config.keys())
        live_streams = await self.get_twitch_streams_info(twitch_ids_to_check)
        
        checked_count = 0
        announced_count = 0

        async with self.config.streamers() as streamers_to_update:
            for twitch_id in twitch_ids_to_check:
                if twitch_id not in streamers_to_update:
                    continue
                
                streamer_data = streamers_to_update[twitch_id]
                username = streamer_data["username"]
                was_live = streamer_data.get("is_live", False)
                
                is_currently_live = twitch_id in {s["user_id"] for s in live_streams}
                current_stream_data = next((s for s in live_streams if s["user_id"] == twitch_id), None)
                checked_count += 1

                if is_currently_live:
                    if not was_live or (target_twitch_id == twitch_id and streamer_data.get("last_announced_stream_id") != current_stream_data["id"]):
                        await self.send_stream_announcement(streamer_data, current_stream_data)
                        streamer_data["last_announced_stream_id"] = current_stream_data["id"]
                        announced_count += 1
                        
                    streamer_data["is_live"] = True
                else:
                    if was_live:
                        streamer_data["is_live"] = False
                        streamer_data["last_announced_stream_id"] = None

        status_msg = f"Finished checking {checked_count} streamer(s).\n"
        if announced_count > 0:
            status_msg += f"Announced {announced_count} new/forced live stream(s)."
        else:
            status_msg += "No new announcements were needed."

        await ctx.send(status_msg)

    ## Start of Twitch Schedule Integration ##

    async def get_guild_timezone(self, guild: discord.Guild):
        tz_name = await self.config.guild(guild).schedule_timezone()
        try:
            return pytz.timezone(tz_name)
        except pytz.UnknownTimeZoneError:
            self.log.warning(f"Unknown timezone '{tz_name}' configured for guild {guild.name}. Defaulting to Europe/London.")
            await self.config.guild(guild).schedule_timezone.set("Europe/London")
            return pytz.timezone("Europe/London")

    async def download_file(self, url: str, save_path: str) -> bool:
        """Downloads a file from a URL and saves it to a specified path."""
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    save_path.parent.mkdir(parents=True, exist_ok=True) # Ensure parent directory exists
                    with open(save_path, 'wb') as f:
                        f.write(data)
                    return True
                else:
                    self.log.error(f"Failed to download {url}: Status {resp.status}")
                    return False
        except Exception as e:
            self.log.error(f"Error downloading {url}: {e}", exc_info=True)
            return False

    async def ensure_schedule_resources(self):
        """Ensures font and template image files are present."""
        font_url = "https://zerolivesleft.net/notelkz/P22.ttf"
        template_url = "https://zerolivesleft.net/notelkz/schedule.png"
        
        font_exists = self.font_path.exists()
        template_exists = self.template_path.exists()

        if not font_exists:
            self.log.info(f"Downloading font from {font_url}")
            await self.download_file(font_url, self.font_path)
            font_exists = self.font_path.exists()
        
        if not template_exists:
            self.log.info(f"Downloading schedule template from {template_url}")
            await self.download_file(template_url, self.template_path)
            template_exists = self.template_path.exists()
            
        return font_exists and template_exists

    async def get_twitch_schedule_data(self, username: str, start_time: datetime.datetime = None, end_time: datetime.datetime = None):
        """Fetches Twitch schedule data for a given username and optional time range."""
        token = await self.get_twitch_access_token()
        client_id, _ = await self.get_twitch_credentials()
        if not token or not client_id:
            return None

        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {token}"
        }

        # Get broadcaster ID first
        user_info = await self.get_twitch_user_info(username=username)
        if not user_info:
            self.log.warning(f"Could not find Twitch user ID for schedule: {username}")
            return None
        broadcaster_id = user_info["id"]
        broadcaster_name = user_info["login"]

        params = {"broadcaster_id": broadcaster_id}
        if start_time:
            params["start_time"] = start_time.isoformat(timespec='seconds') + "Z"

        try:
            async with self.session.get(f"{self.twitch_api_base_url}schedule", headers=headers, params=params) as resp:
                if resp.status == 404: # Schedule not set up
                    self.log.info(f"Twitch schedule for {username} is not set up.")
                    return []
                resp.raise_for_status()
                data = await resp.json()
                segments = data.get("data", {}).get("segments", [])
                
                filtered_segments = []
                for seg in segments:
                    seg_start_time = dateutil.parser.isoparse(seg["start_time"])
                    if seg_start_time.tzinfo is None:
                        seg_start_time = seg_start_time.replace(tzinfo=datetime.timezone.utc)
                    
                    if end_time:
                        if seg_start_time <= end_time:
                            seg["broadcaster_name"] = broadcaster_name
                            filtered_segments.append(seg)
                    else: # If no end_time, include all future segments from now
                        seg["broadcaster_name"] = broadcaster_name
                        filtered_segments.append(seg)
                
                return filtered_segments
        except aiohttp.ClientResponseError as e:
            self.log.error(f"Twitchy: API error fetching schedule for {username}: {e.status} - {e.message}", exc_info=True)
            return None
        except aiohttp.ClientError as e:
            self.log.error(f"Twitchy: Network error fetching schedule for {username}: {e}", exc_info=True)
            return None
        except Exception as e:
            self.log.error(f"Twitchy: An unexpected error occurred fetching schedule for {username}: {e}", exc_info=True)
            return None

    async def get_category_info(self, category_id: str):
        """Fetches Twitch category (game) info by ID."""
        token = await self.get_twitch_access_token()
        client_id, _ = await self.get_twitch_credentials()
        if not token or not client_id:
            return None
        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {token}"
        }
        try:
            async with self.session.get(f"{self.twitch_api_base_url}games?id={category_id}", headers=headers) as resp:
                resp.raise_for_status()
                data = await resp.json()
                if data.get("data"):
                    return data["data"][0]
                return None
        except aiohttp.ClientError as e:
            self.log.error(f"Twitchy: Error fetching category info for ID {category_id}: {e}", exc_info=True)
            return None

    async def generate_schedule_image(self, schedule: list, guild: discord.Guild, start_date: datetime.datetime = None) -> io.BytesIO:
        """Generates a schedule image from template and schedule data."""
        if not await self.ensure_schedule_resources():
            self.log.error("Twitchy: Missing schedule image resources (font/template). Cannot generate image.")
            return None
        
        guild_tz = await self.get_guild_timezone(guild)
        
        img = Image.open(self.template_path)
        event_count = await self.config.guild(guild).schedule_event_count()
        actual_events = min(len(schedule), event_count)
        
        # Crop image if fewer events are displayed than template size
        if actual_events < event_count:
            width, height = img.size
            row_height = 150 # Height per event row in the template
            header_height = 350 # Height of the top header part of the template
            footer_height = height - (header_height + event_count * row_height) # Height of the bottom part of the template
            
            new_height = header_height + actual_events * row_height + footer_height
            new_img = Image.new(img.mode, (width, new_height))
            
            # Paste header
            new_img.paste(img.crop((0, 0, width, header_height)), (0, 0))
            
            # Paste event section if any events
            if actual_events > 0:
                event_section_crop_height = actual_events * row_height
                new_img.paste(img.crop((0, header_height, width, header_height + event_section_crop_height)), (0, header_height))
            
            # Paste footer
            new_img.paste(img.crop((0, header_height + event_count * row_height, width, height)), (0, header_height + actual_events * row_height))
            
            img = new_img
        
        draw = ImageDraw.Draw(img)
        # Use getbbox for text size calculation, getsize is deprecated
        title_font = ImageFont.truetype(str(self.font_path), 90)
        date_font = ImageFont.truetype(str(self.font_path), 40)
        schedule_font = ImageFont.truetype(str(self.font_path), 42)
        
        if start_date is None:
            today = datetime.datetime.now(guild_tz)
            days_since_sunday = today.weekday() + 1 # Monday=0, Sunday=6, adjust for 0=Sunday
            if days_since_sunday == 7: # If today is Sunday
                days_since_sunday = 0
            start_of_week = today - timedelta(days=days_since_sunday)
            start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start_of_week = start_date.astimezone(guild_tz).replace(hour=0, minute=0, second=0, microsecond=0) # Ensure it's in the target timezone
        
        date_text = start_of_week.strftime("%B %d")
        width, _ = img.size
        right_margin = 100
        
        week_of_text = "Week of"
        week_of_bbox = title_font.getbbox(week_of_text)
        week_of_width = week_of_bbox[2] - week_of_bbox[0]
        
        date_bbox = date_font.getbbox(date_text)
        date_width = date_bbox[2] - date_bbox[0]
        
        week_of_x = width - right_margin - week_of_width
        date_x = width - right_margin - date_width
        
        draw.text((week_of_x, 100), week_of_text, font=title_font, fill=(255, 255, 255))
        draw.text((date_x, 180), date_text, font=date_font, fill=(255, 255, 255))
        
        day_x_pos = 125 # Consistent X for day/time
        initial_y = 350 # Starting Y for the first event row
        row_height = 150
        day_offset = -45 # Offset for day/time text relative to row start
        game_offset = 15 # Offset for game title relative to row start
        
        for i, segment in enumerate(schedule):
            if i >= actual_events:
                break
            
            bar_y = initial_y + (i * row_height)
            day_text_y = bar_y + day_offset
            game_text_y = bar_y + game_offset
            
            start_time_utc = dateutil.parser.isoparse(segment["start_time"])
            if start_time_utc.tzinfo is None:
                start_time_utc = start_time_utc.replace(tzinfo=datetime.timezone.utc)
            start_time_local = start_time_utc.astimezone(guild_tz)
            
            day_time = start_time_local.strftime("%A // %I:%M%p").upper()
            title = segment["title"]
            
            draw.text((day_x_pos, day_text_y), day_time, font=schedule_font, fill=(255, 255, 255))
            draw.text((day_x_pos, game_text_y), title, font=schedule_font, fill=(255, 255, 255))
        
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    async def schedule_update_loop(self):
        """Periodically checks and updates the Twitch schedule for guilds."""
        await self.bot.wait_until_ready()
        while self is self.bot.get_cog("Twitchy"):
            try:
                for guild in self.bot.guilds:
                    guild_config = await self.config.guild(guild).all()
                    
                    update_days = guild_config["schedule_update_days"]
                    update_time_str = guild_config["schedule_update_time"]
                    channel_id = guild_config["schedule_channel_id"]
                    twitch_username = guild_config["schedule_twitch_username"]
                    
                    if not update_days or not update_time_str or not channel_id or not twitch_username:
                        continue # Skip if schedule settings are incomplete for this guild

                    guild_tz = await self.get_guild_timezone(guild)
                    now = datetime.datetime.now(guild_tz)
                    
                    current_day_of_week = now.weekday() # Monday is 0, Sunday is 6
                    current_time_fmt = now.strftime("%H:%M")

                    # Check if it's the configured day and time for update
                    if current_day_of_week in update_days and current_time_fmt == update_time_str:
                        channel = guild.get_channel(channel_id)
                        if channel:
                            # Calculate current week's schedule
                            start_of_this_week = now - timedelta(days=current_day_of_week) # Start of Monday
                            start_of_this_week = start_of_this_week.replace(hour=0, minute=0, second=0, microsecond=0)
                            end_of_this_week = start_of_this_week + timedelta(days=6, hours=23, minutes=59, seconds=59)

                            schedule = await self.get_twitch_schedule_data(twitch_username, start_time=start_of_this_week)
                            
                            # Filter schedule to only include segments within the current week
                            if schedule is not None:
                                filtered_schedule = []
                                for seg in schedule:
                                    seg_start_time_utc = dateutil.parser.isoparse(seg["start_time"]).replace(tzinfo=datetime.timezone.utc)
                                    seg_start_time_local = seg_start_time_utc.astimezone(guild_tz)
                                    if start_of_this_week <= seg_start_time_local <= end_of_this_week:
                                        filtered_schedule.append(seg)
                                
                                await self.post_schedule(channel, filtered_schedule, start_date=start_of_this_week)
                                self.log.info(f"Twitchy: Scheduled update posted for {guild.name} ({twitch_username}).")
                            else:
                                self.log.warning(f"Twitchy: Failed to fetch schedule for {twitch_username} in {guild.name} during automated update.")
                        else:
                            self.log.warning(f"Twitchy: Schedule channel {channel_id} not found in guild {guild.name} for automated update.")
                        await asyncio.sleep(61) # Sleep slightly longer to avoid multiple triggers within the same minute
            except asyncio.CancelledError:
                self.log.info("Twitchy: Schedule update loop cancelled.")
                break
            except Exception as e:
                self.log.error(f"Twitchy: An error occurred in schedule_update_loop: {e}", exc_info=True)

            await asyncio.sleep(60) # Check every minute

    async def post_schedule(self, channel: discord.TextChannel, schedule: list, start_date: datetime.datetime = None):
        """Posts the schedule image and detailed embeds to the channel."""
        try:
            notify_role_id = await self.config.guild(channel.guild).schedule_notify_role_id()
            notify_role = channel.guild.get_role(notify_role_id) if notify_role_id else None
            
            warning_content = "⚠️ Updating schedule - Previous schedule messages will be deleted in 10 seconds..."
            if notify_role:
                warning_content = f"{notify_role.mention}\n{warning_content}"
            
            try:
                warning_msg = await channel.send(warning_content)
                await asyncio.sleep(10)
                await warning_msg.delete()
            except discord.Forbidden:
                self.log.warning(f"Twitchy: Missing permissions to send/delete warning message in {channel.name}.")
            except Exception as e:
                self.log.error(f"Twitchy: Error with warning message in {channel.name}: {e}")

            # Delete previous schedule messages by the bot
            previous_schedule_msg_id = await self.config.guild(channel.guild).schedule_message_id()
            if previous_schedule_msg_id:
                try:
                    old_schedule_msg = await channel.fetch_message(previous_schedule_msg_id)
                    await old_schedule_msg.delete()
                    self.log.info(f"Twitchy: Deleted old pinned schedule message in {channel.name}.")
                except discord.NotFound:
                    self.log.info(f"Twitchy: Old pinned schedule message {previous_schedule_msg_id} not found in {channel.name}.")
                except discord.Forbidden:
                    self.log.warning(f"Twitchy: Missing permissions to delete old pinned message in {channel.name}.")
                except Exception as e:
                    self.log.error(f"Twitchy: Error deleting old pinned message {previous_schedule_msg_id} in {channel.name}: {e}")
                await self.config.guild(channel.guild).schedule_message_id.set(None) # Clear ID regardless

            # Also delete other recent bot messages to clean up embeds
            bot_messages = []
            async for message in channel.history(limit=30):
                if message.author == self.bot.user:
                    bot_messages.append(message)
                    if len(bot_messages) >= 10: # Limit to 10 recent bot messages
                        break
            
            for message in bot_messages:
                try:
                    await message.delete()
                    await asyncio.sleep(0.5) # Short delay to avoid rate limits
                except discord.NotFound:
                    pass # Already deleted
                except discord.Forbidden:
                    self.log.warning(f"Twitchy: Missing permissions to delete message {message.id} in {channel.name}.")
                    break
                except Exception as e:
                    self.log.error(f"Twitchy: Error deleting message {message.id} in {channel.name}: {e}")
                    break

            # Generate and send the schedule image
            image_buf = await self.generate_schedule_image(schedule, channel.guild, start_date)
            if image_buf:
                try:
                    schedule_message = await channel.send(
                        file=discord.File(image_buf, filename="schedule.png")
                    )
                    await schedule_message.pin()
                    await self.config.guild(channel.guild).schedule_message_id.set(schedule_message.id)
                    self.log.info(f"Twitchy: Posted and pinned new schedule image in {channel.name}.")
                except discord.Forbidden:
                    self.log.warning(f"Twitchy: Missing permissions to send files or pin messages in {channel.name}.")
                except Exception as e:
                    self.log.error(f"Twitchy: Error sending/pinning schedule image in {channel.name}: {e}", exc_info=True)
            else:
                self.log.error(f"Twitchy: Failed to generate schedule image for {channel.guild.name}.")
                await channel.send("❌ Failed to generate the schedule image. Check bot logs for details.")

            # Send individual embeds for each event
            event_count_limit = await self.config.guild(channel.guild).schedule_event_count()
            guild_tz = await self.get_guild_timezone(channel.guild)

            if schedule:
                for i, segment in enumerate(schedule):
                    if i >= event_count_limit:
                        break

                    start_time_utc = dateutil.parser.isoparse(segment["start_time"]).replace(tzinfo=datetime.timezone.utc)
                    start_time_local = start_time_utc.astimezone(guild_tz)
                    
                    title = segment["title"]
                    category = segment.get("category", {})
                    game_name = category.get("name", "No Category")
                    
                    boxart_url = None
                    if category and category.get("id"):
                        cat_info = await self.get_category_info(category["id"])
                        if cat_info and cat_info.get("box_art_url"):
                            boxart_url = cat_info["box_art_url"].replace("{width}", "285").replace("{height}", "380")

                    unix_ts = int(start_time_utc.timestamp()) # Unix timestamp for Discord's built-in time formatting
                    time_str = f"<t:{unix_ts}:F>" # Full date and time

                    end_time_iso = segment.get("end_time")
                    duration_str = "Unknown"
                    if end_time_iso:
                        end_dt_utc = datetime.datetime.fromisoformat(end_time_iso.replace("Z", "+00:00")).replace(tzinfo=datetime.timezone.utc)
                        duration = end_dt_utc - start_time_utc
                        total_minutes = int(duration.total_seconds() // 60)
                        hours, minutes = divmod(total_minutes, 60)
                        duration_parts = []
                        if hours > 0:
                            duration_parts.append(f"{hours}h")
                        if minutes > 0:
                            duration_parts.append(f"{minutes}m")
                        duration_str = " ".join(duration_parts) if duration_parts else "Less than 1m"

                    twitch_username = segment.get("broadcaster_name")
                    twitch_url = f"https://twitch.tv/{twitch_username}"
                    
                    embed = discord.Embed(
                        title=title,
                        url=twitch_url,
                        description=f"**[Watch Live Here]({twitch_url})**",
                        color=discord.Color.purple(),
                        timestamp=start_time_local # Use local time for embed timestamp
                    )
                    embed.add_field(name="🕒 Start Time", value=time_str, inline=True)
                    embed.add_field(name="⏳ Duration", value=duration_str, inline=True)
                    embed.add_field(name="🎮 Game", value=game_name, inline=True)
                    embed.set_footer(text=f"Scheduled Stream • {twitch_username}")
                    
                    if boxart_url:
                        embed.set_thumbnail(url=boxart_url)

                    try:
                        await channel.send(embed=embed)
                        await asyncio.sleep(0.5)
                    except discord.Forbidden:
                        self.log.warning(f"Twitchy: Missing permissions to send schedule embed in {channel.name}.")
                        break # Stop sending embeds if permissions are missing
                    except Exception as e:
                        self.log.error(f"Twitchy: Error sending schedule embed in {channel.name}: {e}", exc_info=True)
                        break
            else:
                embed = discord.Embed(
                    title="No Upcoming Streams",
                    description="The streamer has no schedule set or no upcoming streams this week. Check back later!",
                    color=discord.Color.purple()
                )
                try:
                    await channel.send(embed=embed)
                except discord.Forbidden:
                    self.log.warning(f"Twitchy: Missing permissions to send 'no streams' embed in {channel.name}.")

        except Exception as e:
            self.log.error(f"Twitchy: An error occurred in post_schedule: {e}", exc_info=True)


    @twitchy.group(name="schedule", aliases=["sched"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def twitchy_schedule(self, ctx):
        """Manages Twitch streaming schedule announcements for this guild."""
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(
                title="Twitchy Schedule Commands",
                color=discord.Color.purple(),
                description=(
                    f"`{ctx.clean_prefix}twitchy schedule setup` - Interactive setup process\n"
                    f"`{ctx.clean_prefix}twitchy schedule force [next|YYYY-MM-DD]` - Force an immediate schedule update for current/next week or specific date.\n"
                    f"`{ctx.clean_prefix}twitchy schedule notify [@role/none]` - Set or clear notification role for schedule updates\n"
                    f"`{ctx.clean_prefix}twitchy schedule events [number]` - Set number of events to show on the image (1-10)\n"
                    f"`{ctx.clean_prefix}twitchy schedule timezone [zone]` - Set the timezone for schedule display (e.g., `Europe/London`)\n"
                    f"`{ctx.clean_prefix}twitchy schedule list` - Show upcoming schedule in text-only format\n"
                    f"`{ctx.clean_prefix}twitchy schedule settings` - Show current schedule settings\n"
                    f"`{ctx.clean_prefix}twitchy schedule test [#channel]` - Test post schedule to a channel\n"
                    f"`{ctx.clean_prefix}twitchy schedule reload` - Redownload template image and font files\n"
                )
            )
            await ctx.send(embed=embed)

    @twitchy_schedule.command(name="force")
    async def schedule_force_update(self, ctx, date_or_option: str = None):
        """
        Force an immediate schedule update for the current week, next week, or a specific week.
        Usage:
        `[p]twitchy schedule force` (current week)
        `[p]twitchy schedule force next` (next week)
        `[p]twitchy schedule force YYYY-MM-DD` (specific week starting on this date)
        """
        channel_id = await self.config.guild(ctx.guild).schedule_channel_id()
        twitch_username = await self.config.guild(ctx.guild).schedule_twitch_username()
        guild_tz = await self.get_guild_timezone(ctx.guild)
        
        if not channel_id or not twitch_username:
            await ctx.send("❌ Please run `[p]twitchy schedule setup` first!")
            return
            
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("❌ The configured schedule channel was not found! Please update it with `[p]twitchy schedule setup`.")
            return
            
        await ctx.send("🔄 Forcing schedule update...")

        start_date = None
        end_date = None
        
        today_local = datetime.datetime.now(guild_tz)
        current_weekday = today_local.weekday() # Monday 0, Sunday 6

        if date_or_option and date_or_option.lower() == "next":
            # Calculate next week's start (next Monday)
            days_until_next_monday = (7 - current_weekday) % 7 # If Monday, 0 days to next Monday
            if days_until_next_monday == 0: # If today is Monday, next Monday is 7 days away
                days_until_next_monday = 7
            start_date = today_local + timedelta(days=days_until_next_monday)
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = start_date + timedelta(days=6, hours=23, minutes=59, seconds=59)
            
        elif date_or_option:
            try:
                # Parse specific date and find its Monday
                parsed_date = datetime.datetime.strptime(date_or_option, "%Y-%m-%d").replace(tzinfo=guild_tz)
                start_date = parsed_date - timedelta(days=parsed_date.weekday()) # Go back to Monday
                start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
                end_date = start_date + timedelta(days=6, hours=23, minutes=59, seconds=59)
            except ValueError:
                await ctx.send("❌ Invalid date format. Please use `YYYY-MM-DD` or `next`.")
                return
        else: # Current week
            start_date = today_local - timedelta(days=current_weekday)
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = start_date + timedelta(days=6, hours=23, minutes=59, seconds=59)

        schedule = await self.get_twitch_schedule_data(twitch_username, start_time=start_date)

        if schedule is not None:
            # Filter schedule to only include segments within the calculated week range
            filtered_schedule = []
            for seg in schedule:
                seg_start_time_utc = dateutil.parser.isoparse(seg["start_time"]).replace(tzinfo=datetime.timezone.utc)
                seg_start_time_local = seg_start_time_utc.astimezone(guild_tz)
                if start_date <= seg_start_time_local <= end_date:
                    filtered_schedule.append(seg)
            
            # Sort the filtered schedule by start time
            filtered_schedule.sort(key=lambda x: dateutil.parser.isoparse(x["start_time"]))

            await self.post_schedule(channel, filtered_schedule, start_date=start_date)
            await ctx.send("✅ Schedule updated!")
        else:
            await ctx.send("❌ Failed to fetch schedule from Twitch! Check the bot's console for errors or ensure the Twitch schedule is set up.")


    @twitchy_schedule.command(name="setup")
    async def schedule_setup(self, ctx):
        """Interactive setup process for Twitch schedule announcements."""
        try:
            await ctx.send("Starting schedule setup process... Please answer the following questions. (Type `cancel` at any point to stop)")
            
            # Channel
            await ctx.send("Which channel should I post the schedule in? (Mention the channel, e.g., #schedule)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=60.0
                )
                if msg.content.lower() == "cancel": return await ctx.send("Setup cancelled.")
                if not msg.channel_mentions:
                    await ctx.send("❌ No channel mentioned. Setup cancelled.")
                    return
                channel = msg.channel_mentions[0]
                await self.config.guild(ctx.guild).schedule_channel_id.set(channel.id)
            except asyncio.TimeoutError:
                await ctx.send("Setup timed out. Please try again.")
                return

            # Twitch Username
            await ctx.send("What's the Twitch username whose schedule you want to display?")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=60.0
                )
                if msg.content.lower() == "cancel": return await ctx.send("Setup cancelled.")
                username = msg.content.strip()
                # Validate Twitch username by fetching user info
                user_info = await self.get_twitch_user_info(username=username)
                if not user_info:
                    await ctx.send(f"❌ Could not find Twitch user `{username}`. Please ensure the username is correct. Setup cancelled.")
                    return
                await self.config.guild(ctx.guild).schedule_twitch_username.set(username)
            except asyncio.TimeoutError:
                await ctx.send("Setup timed out. Please try again.")
                return

            # Update Days
            await ctx.send("Which days should I update the schedule? (Send numbers: 0=Monday, 6=Sunday, separate with spaces, e.g., `0 6` for Mon/Sun)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=60.0
                )
                if msg.content.lower() == "cancel": return await ctx.send("Setup cancelled.")
                try:
                    days = [int(x) for x in msg.content.split()]
                    if not all(0 <= x <= 6 for x in days):
                        await ctx.send("❌ Invalid days. Must be numbers between 0 and 6. Setup cancelled.")
                        return
                    await self.config.guild(ctx.guild).schedule_update_days.set(days)
                except ValueError:
                    await ctx.send("❌ Invalid input. Must be numbers. Setup cancelled.")
                    return
            except asyncio.TimeoutError:
                await ctx.send("Setup timed out. Please try again.")
                return

            # Update Time
            await ctx.send("What time should I update the schedule? (Use 24-hour format, e.g., `14:00` for 2 PM)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=60.0
                )
                if msg.content.lower() == "cancel": return await ctx.send("Setup cancelled.")
                time_str = msg.content.strip()
                if not re.match(r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$", time_str):
                    await ctx.send("❌ Invalid time format. Must be HH:MM. Setup cancelled.")
                    return
                await self.config.guild(ctx.guild).schedule_update_time.set(time_str)
            except asyncio.TimeoutError:
                await ctx.send("Setup timed out. Please try again.")
                return
            
            # Timezone
            await ctx.send(
                "Finally, what timezone should the schedule be displayed in for this server? "
                "Examples: `Europe/London`, `America/New_York`, `Asia/Tokyo`.\n"
                "You can find a list here: <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones>\n"
                "(Type `skip` to use default: Europe/London)"
            )
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=90.0
                )
                if msg.content.lower() == "cancel": return await ctx.send("Setup cancelled.")
                if msg.content.lower() == "skip":
                    await ctx.send("Using default timezone: `Europe/London`.")
                else:
                    try:
                        pytz.timezone(msg.content.strip())
                        await self.config.guild(ctx.guild).schedule_timezone.set(msg.content.strip())
                        await ctx.send(f"Timezone set to: `{msg.content.strip()}`.")
                    except pytz.UnknownTimeZoneError:
                        await ctx.send("❌ Unknown timezone. Using default: `Europe/London`. You can change it later with `[p]twitchy schedule timezone`.")
                        await self.config.guild(ctx.guild).schedule_timezone.set("Europe/London")
            except asyncio.TimeoutError:
                await ctx.send("Setup timed out. Using default timezone: `Europe/London`.")
                await self.config.guild(ctx.guild).schedule_timezone.set("Europe/London")

            await ctx.send("✅ Schedule setup complete! The schedule will be updated on the specified days and time.")
            
        except Exception as e:
            self.log.error(f"Twitchy: An error occurred during schedule setup: {e}", exc_info=True)
            await ctx.send(f"❌ An error occurred during setup: `{str(e)}`")

    @twitchy_schedule.command(name="notify")
    async def schedule_set_notify_role(self, ctx, role: discord.Role = None):
        """Set or clear the role to notify for schedule updates."""
        if role is None:
            await self.config.guild(ctx.guild).schedule_notify_role_id.set(None)
            await ctx.send("✅ Schedule notification role cleared!")
        else:
            await self.config.guild(ctx.guild).schedule_notify_role_id.set(role.id)
            await ctx.send(f"✅ Schedule notification role set to {role.mention}!")

    @twitchy_schedule.command(name="events")
    async def schedule_set_event_count(self, ctx, count: int):
        """Set the number of events to show on the schedule image (1-10)."""
        if not 1 <= count <= 10:
            await ctx.send("❌ Event count must be between 1 and 10!")
            return
        await self.config.guild(ctx.guild).schedule_event_count.set(count)
        await ctx.send(f"✅ Event count set to {count}!")

    @twitchy_schedule.command(name="timezone")
    async def schedule_set_timezone(self, ctx, timezone_name: str):
        """
        Set the timezone for displaying the schedule.
        Usage: [p]twitchy schedule timezone <Timezone Name>
        Example: [p]twitchy schedule timezone Europe/London
        Find a list here: <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones>
        """
        try:
            pytz.timezone(timezone_name) # Validate timezone
            await self.config.guild(ctx.guild).schedule_timezone.set(timezone_name)
            await ctx.send(f"✅ Schedule timezone for this server set to `{timezone_name}`.")
        except pytz.UnknownTimeZoneError:
            await ctx.send("❌ Invalid timezone name. Please provide a valid timezone from the IANA Time Zone Database (e.g., `Europe/London`).")


    @twitchy_schedule.command(name="list")
    async def schedule_list_text(self, ctx, date_or_option: str = None):
        """
        Shows the upcoming schedule in a text-only format.
        Usage:
        `[p]twitchy schedule list` (current week)
        `[p]twitchy schedule list next` (next week)
        `[p]twitchy schedule list YYYY-MM-DD` (specific week starting on this date)
        """
        twitch_username = await self.config.guild(ctx.guild).schedule_twitch_username()
        if not twitch_username:
            return await ctx.send("❌ A Twitch username is not set for schedule. Please run `[p]twitchy schedule setup`.")

        guild_tz = await self.get_guild_timezone(ctx.guild)
        
        start_date = None
        end_date = None
        
        today_local = datetime.datetime.now(guild_tz)
        current_weekday = today_local.weekday()

        if date_or_option and date_or_option.lower() == "next":
            days_until_next_monday = (7 - current_weekday) % 7
            if days_until_next_monday == 0:
                days_until_next_monday = 7
            start_date = today_local + timedelta(days=days_until_next_monday)
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = start_date + timedelta(days=6, hours=23, minutes=59, seconds=59)
            
        elif date_or_option:
            try:
                parsed_date = datetime.datetime.strptime(date_or_option, "%Y-%m-%d").replace(tzinfo=guild_tz)
                start_date = parsed_date - timedelta(days=parsed_date.weekday())
                start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
                end_date = start_date + timedelta(days=6, hours=23, minutes=59, seconds=59)
            except ValueError:
                await ctx.send("❌ Invalid date format. Please use `YYYY-MM-DD` or `next`.")
                return
        else:
            start_date = today_local - timedelta(days=current_weekday)
            start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = start_date + timedelta(days=6, hours=23, minutes=59, seconds=59)

        await ctx.send("🔄 Fetching schedule in text format...")
        schedule = await self.get_twitch_schedule_data(twitch_username, start_time=start_date)

        if schedule is None:
            return await ctx.send("❌ Failed to fetch schedule from Twitch!")
        
        # Filter and sort
        filtered_schedule = []
        for seg in schedule:
            seg_start_time_utc = dateutil.parser.isoparse(seg["start_time"]).replace(tzinfo=datetime.timezone.utc)
            seg_start_time_local = seg_start_time_utc.astimezone(guild_tz)
            if start_date <= seg_start_time_local <= end_date:
                filtered_schedule.append(seg)
        filtered_schedule.sort(key=lambda x: dateutil.parser.isoparse(x["start_time"]))

        if not filtered_schedule:
            return await ctx.send(f"No upcoming streams found for `{twitch_username}` this week ({start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}).")

        description_lines = [f"__**Twitch Schedule for {twitch_username}**__\n"]
        description_lines.append(f"**Week of {start_date.strftime('%Y-%m-%d')} ({guild_tz.tzname(start_date)})**\n")

        for segment in filtered_schedule:
            start_time_utc = dateutil.parser.isoparse(segment["start_time"]).replace(tzinfo=datetime.timezone.utc)
            start_time_local = start_time_utc.astimezone(guild_tz)
            
            title = segment["title"]
            game_name = segment.get("category", {}).get("name", "No Category")
            
            end_time_iso = segment.get("end_time")
            duration_str = "Unknown"
            if end_time_iso:
                end_dt_utc = datetime.datetime.fromisoformat(end_time_iso.replace("Z", "+00:00")).replace(tzinfo=datetime.timezone.utc)
                duration = end_dt_utc - start_time_utc
                total_minutes = int(duration.total_seconds() // 60)
                hours, minutes = divmod(total_minutes, 60)
                duration_parts = []
                if hours > 0:
                    duration_parts.append(f"{hours}h")
                if minutes > 0:
                    duration_parts.append(f"{minutes}m")
                duration_str = " ".join(duration_parts) if duration_parts else "Less than 1m"

            description_lines.append(
                f"**• {start_time_local.strftime('%A, %B %d at %I:%M %p')}**\n"
                f"  `Title:` {title}\n"
                f"  `Game:` {game_name}\n"
                f"  `Duration:` {duration_str}\n"
            )
        
        output = "\n".join(description_lines)
        for page in pagify(output, ["\n"], page_length=1900):
            embed = discord.Embed(
                description=page,
                color=discord.Color.purple()
            )
            await ctx.send(embed=embed)


    @twitchy_schedule.command(name="settings")
    async def schedule_show_settings(self, ctx):
        """Show current schedule settings for this guild."""
        guild_config = await self.config.guild(ctx.guild).all()
        
        channel_id = guild_config["schedule_channel_id"]
        twitch_username = guild_config["schedule_twitch_username"]
        update_days = guild_config["schedule_update_days"]
        update_time = guild_config["schedule_update_time"]
        notify_role_id = guild_config["schedule_notify_role_id"]
        event_count = guild_config["schedule_event_count"]
        timezone = guild_config["schedule_timezone"]
        
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        notify_role = ctx.guild.get_role(notify_role_id) if notify_role_id else None
        
        days_map = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        update_days_str = ", ".join(days_map[day] for day in update_days) if update_days else "Not set"
        
        embed = discord.Embed(
            title="Twitch Schedule Settings",
            color=discord.Color.purple()
        )
        embed.add_field(name="Announcement Channel", value=channel.mention if channel else "Not set", inline=True)
        embed.add_field(name="Twitch Username", value=twitch_username or "Not set", inline=True)
        embed.add_field(name="Update Time (24h)", value=update_time or "Not set", inline=True)
        embed.add_field(name="Update Days", value=update_days_str, inline=True)
        embed.add_field(name="Notify Role", value=notify_role.mention if notify_role else "Not set", inline=True)
        embed.add_field(name="Events on Image", value=str(event_count), inline=True)
        embed.add_field(name="Display Timezone", value=timezone, inline=True)
        
        await ctx.send(embed=embed)

    @twitchy_schedule.command(name="test")
    async def schedule_test_post(self, ctx, channel: discord.TextChannel = None):
        """Test post schedule to a specific channel (or current channel)."""
        if channel is None:
            channel = ctx.channel
            
        twitch_username = await self.config.guild(ctx.guild).schedule_twitch_username()
        if not twitch_username:
            await ctx.send("❌ A Twitch username is not set for schedule. Please run `[p]twitchy schedule setup` first.")
            return
            
        await ctx.send(f"🔄 Testing schedule post for `{twitch_username}` to {channel.mention}...")
        
        guild_tz = await self.get_guild_timezone(ctx.guild)
        today_local = datetime.datetime.now(guild_tz)
        start_of_this_week = today_local - timedelta(days=today_local.weekday())
        start_of_this_week = start_of_this_week.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_this_week = start_of_this_week + timedelta(days=6, hours=23, minutes=59, seconds=59)

        schedule = await self.get_twitch_schedule_data(twitch_username, start_time=start_of_this_week)
        
        if schedule is not None:
            filtered_schedule = []
            for seg in schedule:
                seg_start_time_utc = dateutil.parser.isoparse(seg["start_time"]).replace(tzinfo=datetime.timezone.utc)
                seg_start_time_local = seg_start_time_utc.astimezone(guild_tz)
                if start_of_this_week <= seg_start_time_local <= end_of_this_week:
                    filtered_schedule.append(seg)
            filtered_schedule.sort(key=lambda x: dateutil.parser.isoparse(x["start_time"]))

            await self.post_schedule(channel, filtered_schedule, start_date=start_of_this_week)
            await ctx.send("✅ Test complete!")
        else:
            await ctx.send("❌ Failed to fetch schedule from Twitch! Check the bot's console for errors.")

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
            await ctx.send("✅ Successfully redownloaded schedule resources!")
        else:
            await ctx.send("❌ Failed to redownload some schedule resources. Check bot logs for details.")