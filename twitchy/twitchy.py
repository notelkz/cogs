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

        # Default global settings (for new bot users or after config reset)
        default_global = {
            "twitch_client_id": None,
            "twitch_client_secret": None,
            "twitch_access_token": None,
            "twitch_token_expires_at": 0,
        }

        # Default guild settings
        default_guild = {
            "stream_channels": [],  # Channels where announcements are posted
            "live_role": None,      # Role to assign when live
            "message_text": "{streamer} is now LIVE on Twitch! Go watch at {url}",
            "streamers": {},        # Dictionary of streamers: {"twitch_username": {"discord_user_id": None, "current_status": "offline", "last_stream_id": None}}
            "streamer_status_data": {}, # Stores detailed data for each streamer: {"twitch_username": {"display_name": "", "profile_image_url": ""}}
            "stream_message_id": None, # For tracking the message that was sent

            # Schedule specific settings
            "schedule_twitch_username": None, # The Twitch username whose schedule to display
            "schedule_channel": None, # Channel for schedule posts
            "schedule_timezone": "UTC", # Timezone for schedule display
            "schedule_update_days_in_advance": 7, # How many days in advance to display schedule
            "schedule_update_time": "00:00", # Time of day (HH:MM) to update schedule image
            "schedule_ping_role_id": None, # Role to ping for schedule updates
            "schedule_event_count": 5 # Number of events to show in schedule image
        }

        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)

        # Ensure 'streamers' is a Group and 'streamer_status_data' is a Dict at the guild level
        self.config.init_custom("GUILD", "streamers", {"name": str}) # name is just a placeholder, actual structure is {"twitch_username": {"discord_user_id": None, ...}}
        self.config.init_custom("GUILD", "streamer_status_data", {}) # No specific key needed for dict initialization

        self.check_streams_task = self.bot.loop.create_task(self.check_streams_loop())
        self.update_schedule_task = self.bot.loop.create_task(self.schedule_update_loop())
        self.session = aiohttp.ClientSession()

        # Paths for schedule image resources
        self.data_path = data_manager.cog_data_path(self)
        self.font_path = self.data_path / "Roboto-Regular.ttf"
        self.template_path = self.data_path / "schedule_template.png"

    def cog_unload(self):
        if self.check_streams_task:
            self.check_streams_task.cancel()
        if self.update_schedule_task:
            self.update_schedule_task.cancel()
        if self.session:
            self.bot.loop.create_task(self.session.close())

    async def get_access_token(self):
        """Fetches and stores a new Twitch API access token."""
        global_config = await self.config.all()
        client_id = global_config["twitch_client_id"]
        client_secret = global_config["twitch_client_secret"]

        if not client_id or not client_secret:
            return None

        token_url = "https://id.twitch.tv/oauth2/token"
        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials"
        }
        try:
            async with self.session.post(token_url, data=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()
                access_token = data.get("access_token")
                expires_in = data.get("expires_in", 3600) # Default to 1 hour if not provided
                token_expires_at = time.time() + expires_in - 300 # 5 min buffer

                await self.config.twitch_access_token.set(access_token)
                await self.config.twitch_token_expires_at.set(token_expires_at)
                return access_token
        except aiohttp.ClientError as e:
            self.log.error(f"Failed to get Twitch access token: {e}")
            return None

    async def twitch_api_request(self, endpoint, params=None):
        """Makes a request to the Twitch API, handling token refreshing."""
        global_config = await self.config.all()
        access_token = global_config["twitch_access_token"]
        token_expires_at = global_config["twitch_token_expires_at"]
        client_id = global_config["twitch_client_id"]

        if not client_id:
            self.log.warning("Twitch Client ID not set.")
            return None

        if not access_token or time.time() >= token_expires_at:
            access_token = await self.get_access_token()
            if not access_token:
                self.log.error("Could not obtain a valid Twitch access token.")
                return None

        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {access_token}"
        }
        url = f"https://api.twitch.tv/helix/{endpoint}"
        try:
            async with self.session.get(url, headers=headers, params=params) as resp:
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientResponseError as e:
            self.log.error(f"Twitch API request failed for {endpoint}: {e} - Response: {await e.response.text()}")
            return None
        except aiohttp.ClientError as e:
            self.log.error(f"Twitch API request failed for {endpoint}: {e}")
            return None

    async def get_twitch_user_info(self, usernames: list):
        """Fetches user information (including IDs) for given Twitch usernames."""
        if not usernames:
            return {}
        params = [("login", u) for u in usernames]
        data = await self.twitch_api_request("users", params=params)
        users_info = {}
        if data and "data" in data:
            for user in data["data"]:
                users_info[user["login"].lower()] = {
                    "id": user["id"],
                    "display_name": user["display_name"],
                    "profile_image_url": user["profile_image_url"]
                }
        return users_info

    async def check_streams_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                guilds_data = await self.config.all_guilds()
                all_twitch_usernames = set()
                for guild_id, guild_config in guilds_data.items():
                    all_twitch_usernames.update(guild_config["streamers"].keys())

                if not all_twitch_usernames:
                    await asyncio.sleep(60) # Sleep if no streamers configured
                    continue

                # Fetch user IDs and update streamer_status_data
                user_info = await self.get_twitch_user_info(list(all_twitch_usernames))
                for guild_id in guilds_data:
                    guild = self.bot.get_guild(guild_id)
                    if guild:
                        current_streamer_status_data = await self.config.guild(guild).streamer_status_data()
                        updated = False
                        for username in all_twitch_usernames:
                            if username in user_info:
                                if (username not in current_streamer_status_data or
                                    current_streamer_status_data[username].get("display_name") != user_info[username]["display_name"] or
                                    current_streamer_status_data[username].get("profile_image_url") != user_info[username]["profile_image_url"]):
                                    current_streamer_status_data[username] = {
                                        "id": user_info[username]["id"],
                                        "display_name": user_info[username]["display_name"],
                                        "profile_image_url": user_info[username]["profile_image_url"]
                                    }
                                    updated = True
                            elif username in current_streamer_status_data:
                                # Remove if user info not found (e.g., deleted Twitch account)
                                del current_streamer_status_data[username]
                                updated = True
                        if updated:
                            await self.config.guild(guild).streamer_status_data.set(current_streamer_status_data)

                # Fetch live streams
                streamer_ids_to_check = [info["id"] for info in user_info.values() if "id" in info]
                live_streams = {}
                if streamer_ids_to_check:
                    chunks = [streamer_ids_to_check[i:i + 100] for i in range(0, len(streamer_ids_to_check), 100)]
                    for chunk in chunks:
                        params = [("user_id", sid) for sid in chunk]
                        data = await self.twitch_api_request("streams", params=params)
                        if data and "data" in data:
                            for stream in data["data"]:
                                live_streams[stream["user_login"].lower()] = stream

                for guild_id, guild_config in guilds_data.items():
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        continue

                    tracked_streamers = guild_config["streamers"]
                    stream_channels = [guild.get_channel(cid) for cid in guild_config["stream_channels"] if guild.get_channel(cid)]
                    live_role = guild.get_role(guild_config["live_role"]) if guild_config["live_role"] else None
                    message_text = guild_config["message_text"]

                    for username, streamer_data in tracked_streamers.items():
                        is_live = username in live_streams
                        current_status = streamer_data.get("current_status", "offline")
                        last_stream_id = streamer_data.get("last_stream_id")

                        # Update streamer info if fetched successfully
                        if username in user_info:
                            full_streamer_data = user_info[username]
                            display_name = full_streamer_data.get("display_name", username)
                            profile_image_url = full_streamer_data.get("profile_image_url")
                        else:
                            # Fallback to current config data if not found in fresh fetch
                            current_guild_streamer_status_data = await self.config.guild(guild).streamer_status_data()
                            full_streamer_data = current_guild_streamer_status_data.get(username, {})
                            display_name = full_streamer_data.get("display_name", username)
                            profile_image_url = full_streamer_data.get("profile_image_url")

                        if is_live and current_status == "offline":
                            # Streamer just went live
                            stream = live_streams[username]
                            stream_id = stream["id"]

                            # Check if this is a new stream or a repeated notification
                            if stream_id != last_stream_id:
                                # Update status in config
                                await self.config.guild(guild).streamers.set_attr(username, "current_status", "live")
                                await self.config.guild(guild).streamers.set_attr(username, "last_stream_id", stream_id)

                                # Send announcement
                                embed = discord.Embed(
                                    title=stream["title"],
                                    url=f"https://www.twitch.tv/{username}",
                                    description=f"{display_name} is now streaming **{stream['game_name']}**!",
                                    color=0x6441a5 # Twitch purple
                                )
                                if stream["thumbnail_url"]:
                                    thumbnail_url = stream["thumbnail_url"].replace("{width}", "1280").replace("{height}", "720")
                                    embed.set_image(url=thumbnail_url)
                                embed.set_author(name=display_name, url=f"https://www.twitch.tv/{username}", icon_url=profile_image_url)
                                embed.set_footer(text="Twitch Stream")
                                embed.timestamp = datetime.datetime.now(datetime.timezone.utc)

                                watch_url = f"https://www.twitch.tv/{username}"
                                subscribe_url = f"https://www.twitch.tv/subs/{username}" # This URL might not be universally correct for subscription.
                                view = StreamButtons(watch_url, subscribe_url)

                                for channel in stream_channels:
                                    try:
                                        msg = await channel.send(
                                            content=message_text.format(streamer=display_name, url=watch_url),
                                            embed=embed,
                                            view=view
                                        )
                                        await self.config.guild(guild).stream_message_id.set(msg.id) # Store the message ID
                                    except discord.Forbidden:
                                        self.log.warning(f"Missing permissions to send messages in {channel.name} ({channel.id}) in guild {guild.name} ({guild.id})")
                                    except Exception as e:
                                        self.log.error(f"Error sending stream announcement in {guild.name} ({guild.id}): {e}")

                                # Assign live role
                                if live_role and streamer_data.get("discord_user_id"):
                                    member = guild.get_member(streamer_data["discord_user_id"])
                                    if member and live_role not in member.roles:
                                        try:
                                            await member.add_roles(live_role)
                                        except discord.Forbidden:
                                            self.log.warning(f"Missing permissions to assign role {live_role.name} in guild {guild.name}")
                                        except Exception as e:
                                            self.log.error(f"Error assigning live role to {member.name} in {guild.name}: {e}")

                        elif not is_live and current_status == "live":
                            # Streamer just went offline
                            await self.config.guild(guild).streamers.set_attr(username, "current_status", "offline")
                            # Optionally, clear last_stream_id if you want to allow re-announcement of the same stream after a short break

                            # Remove live role
                            if live_role and streamer_data.get("discord_user_id"):
                                member = guild.get_member(streamer_data["discord_user_id"])
                                if member and live_role in member.roles:
                                    try:
                                        await member.remove_roles(live_role)
                                    except discord.Forbidden:
                                        self.log.warning(f"Missing permissions to remove role {live_role.name} in guild {guild.name}")
                                    except Exception as e:
                                        self.log.error(f"Error removing live role from {member.name} in {guild.name}: {e}")

                            # Edit old announcement message to mark as offline
                            message_id = await self.config.guild(guild).stream_message_id()
                            if message_id:
                                for channel in stream_channels:
                                    try:
                                        msg = await channel.fetch_message(message_id)
                                        if msg and msg.embeds:
                                            embed = msg.embeds[0]
                                            embed.title = f"OFFLINE: {embed.title}"
                                            embed.color = discord.Color.dark_grey()
                                            embed.description = f"{display_name} has gone offline."
                                            embed.set_footer(text="Stream has ended")
                                            embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
                                            await msg.edit(embed=embed, view=None) # Remove buttons too
                                            await self.config.guild(guild).stream_message_id.set(None) # Clear stored message ID
                                    except discord.NotFound:
                                        self.log.info(f"Stream announcement message {message_id} not found in channel {channel.id}.")
                                        await self.config.guild(guild).stream_message_id.set(None) # Clear stale ID
                                    except discord.Forbidden:
                                        self.log.warning(f"Missing permissions to edit messages in {channel.name} ({channel.id}) in guild {guild.name} ({guild.id})")
                                    except Exception as e:
                                        self.log.error(f"Error editing stream announcement in {guild.name} ({guild.id}): {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log.error(f"Error in check_streams_loop: {traceback.format_exc()}")
            finally:
                await asyncio.sleep(60) # Check every minute

    async def schedule_update_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                now_utc = datetime.datetime.now(datetime.timezone.utc)
                for guild_id, guild_config in (await self.config.all_guilds()).items():
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        continue

                    schedule_channel_id = guild_config["schedule_channel"]
                    schedule_time_str = guild_config["schedule_update_time"]
                    schedule_twitch_username = guild_config["schedule_twitch_username"]

                    if not schedule_channel_id or not schedule_time_str or not schedule_twitch_username:
                        continue

                    try:
                        update_hour, update_minute = map(int, schedule_time_str.split(":"))
                    except ValueError:
                        self.log.error(f"Invalid schedule_update_time format for guild {guild.name}: {schedule_time_str}")
                        continue

                    guild_tz_str = guild_config["schedule_timezone"]
                    try:
                        guild_tz = pytz.timezone(guild_tz_str)
                    except pytz.UnknownTimeZoneError:
                        self.log.error(f"Unknown timezone for guild {guild.name}: {guild_tz_str}")
                        continue

                    # Convert current UTC time to guild's local time
                    now_local = now_utc.astimezone(guild_tz)

                    # Calculate next update time in guild's local time
                    next_update_time_local = now_local.replace(hour=update_hour, minute=update_minute, second=0, microsecond=0)

                    # If the calculated time is in the past, set it for the next day
                    if next_update_time_local < now_local:
                        next_update_time_local += timedelta(days=1)

                    # Convert next update time to UTC
                    next_update_time_utc = next_update_time_local.astimezone(datetime.timezone.utc)

                    # Check if it's time to update
                    # Use a small buffer (e.g., 5 seconds) to ensure it triggers correctly
                    if now_utc >= next_update_time_utc - timedelta(seconds=5): # Fire slightly before or exactly at the time
                        channel = guild.get_channel(schedule_channel_id)
                        if channel:
                            self.log.info(f"Attempting to update schedule for guild {guild.name} in channel {channel.name}.")
                            await self.post_twitch_schedule(guild)
                        # After posting, set the task to run for the next day
                        # This avoids continuous re-triggering if the loop runs very fast
                        await asyncio.sleep(60 * 60 * 24) # Wait for approximately 24 hours after a successful update

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log.error(f"Error in schedule_update_loop: {traceback.format_exc()}")
            finally:
                # Sleep for a reasonable interval before checking again (e.g., 10 minutes)
                await asyncio.sleep(60 * 10)

    async def ensure_schedule_resources(self):
        """Ensures font and template image files exist, downloading if necessary."""
        self.data_path.mkdir(parents=True, exist_ok=True)

        font_url = "https://github.com/google/fonts/raw/main/apache/robotoslab/RobotoSlab-Regular.ttf"
        template_url = "https://raw.githubusercontent.com/notelkz/Red-DiscordBot-Cogs/main/twitchy/schedule_template.png"

        tasks = []
        if not self.font_path.exists():
            tasks.append(self._download_file(font_url, self.font_path, "font"))
        if not self.template_path.exists():
            tasks.append(self._download_file(template_url, self.template_path, "template image"))

        if tasks:
            results = await asyncio.gather(*tasks)
            return all(results) # True if all downloads successful
        return True # No downloads needed, files already exist

    async def _download_file(self, url: str, path, description: str):
        """Helper to download a file."""
        try:
            async with self.session.get(url) as resp:
                resp.raise_for_status()
                with open(path, "wb") as f:
                    f.write(await resp.read())
            self.log.info(f"Successfully downloaded {description} to {path}")
            return True
        except aiohttp.ClientError as e:
            self.log.error(f"Failed to download {description} from {url}: {e}")
            return False
        except Exception as e:
            self.log.error(f"Error saving {description} to {path}: {e}")
            return False

    async def get_twitch_schedule(self, twitch_username: str):
        """Fetches the Twitch schedule for a given username."""
        user_info = await self.get_twitch_user_info([twitch_username])
        if not user_info or twitch_username not in user_info:
            return None

        broadcaster_id = user_info[twitch_username]["id"]
        params = {"broadcaster_id": broadcaster_id}
        data = await self.twitch_api_request("schedule", params=params)

        if data and "data" in data and "segments" in data["data"]:
            return data["data"]["segments"]
        return None

    async def generate_schedule_image(self, guild: discord.Guild, schedule_segments: list, start_date: datetime.datetime):
        """Generates a schedule image from given segments."""
        if not await self.ensure_schedule_resources():
            self.log.error("Missing schedule image resources. Cannot generate image.")
            return None

        try:
            template = Image.open(self.template_path)
            draw = ImageDraw.Draw(template)
            font_size_header = 40
            font_size_event = 30
            font_size_small = 25

            # Ensure fonts are loaded, handling potential errors
            try:
                font_header = ImageFont.truetype(str(self.font_path), font_size_header)
                font_event = ImageFont.truetype(str(self.font_path), font_size_event)
                font_small = ImageFont.truetype(str(self.font_path), font_size_small)
            except IOError as e:
                self.log.error(f"Error loading font file: {e}. Please ensure '{self.font_path}' is a valid TrueType font.")
                return None

            guild_config = await self.config.guild(guild).all()
            guild_tz_str = guild_config["schedule_timezone"]
            try:
                guild_tz = pytz.timezone(guild_tz_str)
            except pytz.UnknownTimeZoneError:
                self.log.error(f"Unknown timezone '{guild_tz_str}' for guild {guild.name}. Using UTC.")
                guild_tz = pytz.utc

            # Title
            draw.text((50, 50), "Upcoming Schedule", font=font_header, fill=(255, 255, 255))

            y_offset = 150
            event_count = guild_config["schedule_event_count"]
            displayed_events = 0

            if not schedule_segments:
                draw.text((50, y_offset), "No upcoming events scheduled.", font=font_event, fill=(255, 255, 255))
            else:
                for segment in schedule_segments:
                    if displayed_events >= event_count:
                        break

                    start_time_utc = dateutil.parser.isoparse(segment["start_time"]).replace(tzinfo=datetime.timezone.utc)
                    end_time_utc = dateutil.parser.isoparse(segment["end_time"]).replace(tzinfo=datetime.timezone.utc)

                    start_time_local = start_time_utc.astimezone(guild_tz)
                    end_time_local = end_time_utc.astimezone(guild_tz)

                    date_str = start_time_local.strftime("%A, %B %d")
                    time_str = start_time_local.strftime("%I:%M %p") + " - " + end_time_local.strftime("%I:%M %p")

                    category = segment.get("category", {}).get("name", "N/A")
                    title = segment.get("title", "No Title")

                    draw.text((50, y_offset), f"{date_str} - {time_str} ({guild_tz.tzname(start_time_local)})", font=font_event, fill=(255, 255, 255))
                    draw.text((70, y_offset + 35), f"Category: {category}", font=font_small, fill=(200, 200, 200))
                    draw.text((70, y_offset + 60), f"Title: {title}", font=font_small, fill=(200, 200, 200))
                    y_offset += 110
                    displayed_events += 1

            img_byte_arr = io.BytesIO()
            template.save(img_byte_arr, format="PNG")
            img_byte_arr.seek(0)
            return discord.File(img_byte_arr, filename="twitch_schedule.png")

        except FileNotFoundError:
            self.log.error(f"Image template or font file not found. Paths: {self.template_path}, {self.font_path}")
            return None
        except Exception as e:
            self.log.error(f"Error generating schedule image: {traceback.format_exc()}")
            return None

    async def post_twitch_schedule(self, guild: discord.Guild):
        """Fetches and posts the Twitch schedule for a guild's configured streamer."""
        guild_config = await self.config.guild(guild).all()
        channel_id = guild_config["schedule_channel"]
        twitch_username = guild_config["schedule_twitch_username"]
        ping_role_id = guild_config["schedule_ping_role_id"]
        days_in_advance = guild_config["schedule_update_days_in_advance"]

        channel = guild.get_channel(channel_id)
        if not channel:
            self.log.warning(f"Schedule channel not found for guild {guild.name}. ID: {channel_id}")
            return

        if not twitch_username:
            await channel.send("No Twitch username configured for schedule display. Use `!twitchy schedule setstreamer <username>`.")
            return

        segments = await self.get_twitch_schedule(twitch_username)
        if segments is None:
            await channel.send(f"Failed to fetch schedule for {twitch_username}. Please check the username and your API credentials.")
            return

        guild_tz_str = guild_config["schedule_timezone"]
        try:
            guild_tz = pytz.timezone(guild_tz_str)
        except pytz.UnknownTimeZoneError:
            self.log.error(f"Unknown timezone '{guild_tz_str}' for guild {guild.name}. Using UTC.")
            guild_tz = pytz.utc

        now_local = datetime.datetime.now(guild_tz)
        # Define the period for filtering (from start of today for X days)
        start_of_period = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_period = start_of_period + timedelta(days=days_in_advance)

        filtered_schedule = []
        for seg in segments:
            seg_start_time_utc = dateutil.parser.isoparse(seg["start_time"]).replace(tzinfo=datetime.timezone.utc)
            seg_start_time_local = seg_start_time_utc.astimezone(guild_tz)
            if start_of_period <= seg_start_time_local < end_of_period: # Use < for end_of_period to exclude events exactly at midnight of the last day+1
                filtered_schedule.append(seg)
        filtered_schedule.sort(key=lambda x: dateutil.parser.isoparse(x["start_time"]))

        if not filtered_schedule:
            await channel.send(f"No upcoming Twitch schedule events for {twitch_username} in the next {days_in_advance} days.")
            return

        schedule_file = await self.generate_schedule_image(guild, filtered_schedule, start_of_period)
        if schedule_file:
            ping_role = guild.get_role(ping_role_id) if ping_role_id else None
            content = f"{ping_role.mention}\nHere's the upcoming Twitch schedule for **{twitch_username}**:" if ping_role else f"Here's the upcoming Twitch schedule for **{twitch_username}**:"
            try:
                await channel.send(content=content, file=schedule_file)
            except discord.Forbidden:
                self.log.warning(f"Missing permissions to send messages/files in {channel.name} ({channel.id}) in guild {guild.name} ({guild.id})")
            except Exception as e:
                self.log.error(f"Error posting schedule image in {guild.name} ({guild.id}): {e}")
        else:
            await channel.send("Failed to generate the schedule image. Check bot logs for details.")


    # Main Twitchy Commands
    @commands.group(invoke_without_command=True)
    async def twitchy(self, ctx):
        """Manages Twitch stream announcements and roles."""
        await ctx.send_help(ctx.command)

    @twitchy.command(name="setcreds")
    @commands.is_owner()
    async def twitchy_set_credentials(self, ctx, client_id: str, client_secret: str):
        """Sets your Twitch API Client ID and Client Secret."""
        await self.config.twitch_client_id.set(client_id)
        await self.config.twitch_client_secret.set(client_secret)
        # Invalidate current token to force a new one
        await self.config.twitch_access_token.set(None)
        await self.config.twitch_token_expires_at.set(0)
        await ctx.send("✅ Twitch API credentials set successfully. A new access token will be fetched shortly.")

    @twitchy.command(name="settoken")
    @commands.is_owner()
    async def twitchy_set_token_manual(self, ctx):
        """Manually refreshes Twitch API access token (usually not needed)."""
        await ctx.send("Attempting to refresh Twitch access token...")
        token = await self.get_access_token()
        if token:
            await ctx.send("✅ Twitch access token refreshed successfully.")
        else:
            await ctx.send("❌ Failed to refresh Twitch access token. Check your Client ID and Client Secret.")

    @twitchy.command(name="addstreamer")
    @commands.has_permissions(manage_guild=True)
    async def twitchy_add_streamer(self, ctx, twitch_username: str, discord_user: discord.Member = None):
        """Adds a Twitch streamer to track. Optionally link to a Discord user."""
        twitch_username = twitch_username.lower()
        async with self.config.guild(ctx.guild).streamers() as streamers:
            if twitch_username in streamers:
                await ctx.send(f"❌ Streamer `{twitch_username}` is already being tracked.")
                return

            user_info = await self.get_twitch_user_info([twitch_username])
            if not user_info or twitch_username not in user_info:
                await ctx.send(f"❌ Could not find Twitch user `{twitch_username}`. Please check the username.")
                return

            streamers[twitch_username] = {
                "discord_user_id": discord_user.id if discord_user else None,
                "current_status": "offline",
                "last_stream_id": None
            }
            # Also ensure streamer_status_data is updated
            async with self.config.guild(ctx.guild).streamer_status_data() as status_data:
                status_data[twitch_username] = {
                    "id": user_info[twitch_username]["id"],
                    "display_name": user_info[twitch_username]["display_name"],
                    "profile_image_url": user_info[twitch_username]["profile_image_url"]
                }

            await ctx.send(f"✅ Now tracking Twitch streamer `{twitch_username}`. "
                           f"{'Linked to Discord user ' + discord_user.display_name if discord_user else 'Not linked to a Discord user.'}")

    @twitchy.command(name="removestreamer")
    @commands.has_permissions(manage_guild=True)
    async def twitchy_remove_streamer(self, ctx, twitch_username: str):
        """Removes a Twitch streamer from tracking."""
        twitch_username = twitch_username.lower()
        async with self.config.guild(ctx.guild).streamers() as streamers:
            if twitch_username not in streamers:
                await ctx.send(f"❌ Streamer `{twitch_username}` is not being tracked.")
                return
            del streamers[twitch_username]
            # Also remove from streamer_status_data
            async with self.config.guild(ctx.guild).streamer_status_data() as status_data:
                if twitch_username in status_data:
                    del status_data[twitch_username]

            await ctx.send(f"✅ Stopped tracking Twitch streamer `{twitch_username}`.")

    @twitchy.command(name="streamers")
    @commands.guild_only()
    async def twitchy_list_streamers(self, ctx):
        """Lists all tracked Twitch streamers for this guild."""
        streamers = await self.config.guild(ctx.guild).streamers()
        if not streamers:
            await ctx.send("No Twitch streamers are currently being tracked for this guild.")
            return

        msg = "Currently tracking the following Twitch streamers:\n"
        for username, data in streamers.items():
            discord_user_id = data.get("discord_user_id")
            discord_user = ctx.guild.get_member(discord_user_id) if discord_user_id else None
            linked_status = f"(Linked to {discord_user.display_name})" if discord_user else "(Not linked to Discord user)"
            current_status = data.get("current_status", "unknown")
            msg += f"- `{username}` ({current_status}) {linked_status}\n"

        for page in pagify(msg):
            await ctx.send(page)

    @twitchy.command(name="setchannel")
    @commands.has_permissions(manage_guild=True)
    async def twitchy_set_channel(self, ctx, channel: discord.TextChannel):
        """Sets the default Discord channel for stream announcements."""
        async with self.config.guild(ctx.guild).stream_channels() as channels:
            if channel.id not in channels:
                channels.append(channel.id)
                await ctx.send(f"✅ Stream announcements will now be posted in {channel.mention}.")
            else:
                await ctx.send(f"That channel is already set for stream announcements.")

    @twitchy.command(name="removechannel")
    @commands.has_permissions(manage_guild=True)
    async def twitchy_remove_channel(self, ctx, channel: discord.TextChannel):
        """Removes a Discord channel from stream announcements."""
        async with self.config.guild(ctx.guild).stream_channels() as channels:
            if channel.id in channels:
                channels.remove(channel.id)
                await ctx.send(f"✅ Stream announcements will no longer be posted in {channel.mention}.")
            else:
                await ctx.send(f"That channel is not currently set for stream announcements.")

    @twitchy.command(name="setliverole")
    @commands.has_permissions(manage_guild=True)
    async def twitchy_set_live_role(self, ctx, role: discord.Role = None):
        """Sets the role to assign to users when their tracked streamer goes live.
        Leave blank to disable.
        """
        await self.config.guild(ctx.guild).live_role.set(role.id if role else None)
        if role:
            await ctx.send(f"✅ Live role set to `{role.name}`. Make sure the bot's role is above this role in hierarchy.")
        else:
            await ctx.send("✅ Live role disabled.")

    @twitchy.command(name="setmessage")
    @commands.has_permissions(manage_guild=True)
    async def twitchy_set_message(self, ctx, *, message: str):
        """Sets the custom message for stream announcements.
        Use {streamer} for streamer's display name and {url} for Twitch stream URL.
        Example: `!twitchy setmessage {streamer} is live! Tune in at {url}`
        """
        await self.config.guild(ctx.guild).message_text.set(message)
        await ctx.send(f"✅ Stream announcement message set to: `{message}`")

    # Schedule Commands (subgroup of twitchy)
    @twitchy.group() # THIS WAS THE FIX! Changed from @commands.group()
    @commands.guild_only()
    async def schedule(self, ctx):
        """Commands for managing Twitch schedule display."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @schedule.command(name="setchannel")
    @commands.has_permissions(manage_guild=True)
    async def schedule_set_channel(self, ctx, channel: discord.TextChannel):
        """Sets the Discord channel where the Twitch schedule will be posted."""
        await self.config.guild(ctx.guild).schedule_channel.set(channel.id)
        await ctx.send(f"✅ Twitch schedule will now be posted in {channel.mention}.")

    @schedule.command(name="setstreamer")
    @commands.has_permissions(manage_guild=True)
    async def schedule_set_streamer(self, ctx, twitch_username: str):
        """Sets the Twitch username whose schedule should be displayed."""
        twitch_username = twitch_username.lower()
        user_info = await self.get_twitch_user_info([twitch_username])
        if not user_info or twitch_username not in user_info:
            await ctx.send(f"❌ Could not find Twitch user `{twitch_username}`. Please check the username.")
            return

        await self.config.guild(ctx.guild).schedule_twitch_username.set(twitch_username)
        await ctx.send(f"✅ Schedule display username set to `{user_info[twitch_username]['display_name']}`.")

    @schedule.command(name="settimezone")
    @commands.has_permissions(manage_guild=True)
    async def schedule_set_timezone(self, ctx, timezone_str: str):
        """Sets the timezone for displaying the schedule (e.g., 'America/New_York', 'Europe/London').
        You can find a list of valid timezones at: <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones>
        """
        try:
            pytz.timezone(timezone_str) # Test if timezone is valid
            await self.config.guild(ctx.guild).schedule_timezone.set(timezone_str)
            await ctx.send(f"✅ Schedule timezone set to `{timezone_str}`.")
        except pytz.UnknownTimeZoneError:
            await ctx.send(f"❌ Invalid timezone string: `{timezone_str}`. "
                           "Please use a valid TZ database name (e.g., 'America/New_York'). "
                           "Refer to <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones> for a list.")

    @schedule.command(name="setdays")
    @commands.has_permissions(manage_guild=True)
    async def schedule_set_days_in_advance(self, ctx, days: int):
        """Sets how many days of the schedule to display in advance (1-14)."""
        if not 1 <= days <= 14:
            await ctx.send("❌ Number of days must be between 1 and 14.")
            return
        await self.config.guild(ctx.guild).schedule_update_days_in_advance.set(days)
        await ctx.send(f"✅ Schedule will now display {days} days in advance.")

    @schedule.command(name="settime")
    @commands.has_permissions(manage_guild=True)
    async def schedule_set_update_time(self, ctx, time_str: str):
        """Sets the daily time (HH:MM) to update the schedule image (in local schedule timezone).
        Example: `09:00` for 9 AM, `17:30` for 5:30 PM.
        """
        if not re.match(r"^(?:2[0-3]|[01]?[0-9]):(?:[0-5]?[0-9])$", time_str):
            await ctx.send("❌ Invalid time format. Please use HH:MM (e.g., `09:00` or `17:30`).")
            return
        await self.config.guild(ctx.guild).schedule_update_time.set(time_str)
        await ctx.send(f"✅ Daily schedule update time set to `{time_str}` in the configured schedule timezone.")

    @schedule.command(name="setpingrole")
    @commands.has_permissions(manage_guild=True)
    async def schedule_set_ping_role(self, ctx, role: discord.Role = None):
        """Sets a role to ping when the schedule is updated.
        Leave blank to disable pings.
        """
        await self.config.guild(ctx.guild).schedule_ping_role_id.set(role.id if role else None)
        if role:
            await ctx.send(f"✅ Schedule update ping role set to `{role.name}`.")
        else:
            await ctx.send("✅ Schedule update ping role disabled.")

    @schedule.command(name="seteventcount")
    @commands.has_permissions(manage_guild=True)
    async def schedule_set_event_count(self, ctx, count: int):
        """Sets the maximum number of events to show in the schedule image (1-10)."""
        if not 1 <= count <= 10:
            await ctx.send("❌ Event count must be between 1 and 10.")
            return
        await self.config.guild(ctx.guild).schedule_event_count.set(count)
        await ctx.send(f"✅ Schedule image will show up to {count} events.")

    @schedule.command(name="show")
    async def schedule_show(self, ctx):
        """Displays the upcoming Twitch schedule for the configured streamer."""
        await ctx.defer() # Acknowledge command to prevent timeout
        guild_config = await self.config.guild(ctx.guild).all()
        twitch_username = guild_config["schedule_twitch_username"]
        channel_id = guild_config["schedule_channel"]

        if not twitch_username:
            await ctx.send("No Twitch username configured for schedule display. Use `!twitchy schedule setstreamer <username>`.")
            return
        if not channel_id:
            await ctx.send("No schedule channel configured. Use `!twitchy schedule setchannel <channel>`.")
            return

        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send(f"Configured schedule channel (ID: {channel_id}) not found or is inaccessible.")
            return

        # Temporarily override schedule_channel to post in current context for 'show' command
        original_channel_id = await self.config.guild(ctx.guild).schedule_channel()
        await self.config.guild(ctx.guild).schedule_channel.set(ctx.channel.id)

        try:
            await ctx.send(f"🔄 Fetching and generating schedule for {twitch_username}...")
            await self.post_twitch_schedule(ctx.guild)
        finally:
            # Revert schedule_channel to original setting
            await self.config.guild(ctx.guild).schedule_channel.set(original_channel_id)


    @schedule.command(name="test")
    async def schedule_test_generation(self, ctx):
        """Tests schedule image generation and resource downloading.
        (Does not post to the configured schedule channel automatically, uses current channel.)
        """
        await ctx.defer() # Acknowledge command to prevent timeout
        await ctx.send("🔄 Attempting to fetch and generate schedule image for testing...")

        guild_config = await self.config.guild(ctx.guild).all()
        twitch_username = guild_config["schedule_twitch_username"]

        if not twitch_username:
            await ctx.send("No Twitch username configured for schedule display. Use `!twitchy schedule setstreamer <username>`.")
            return

        segments = await self.get_twitch_schedule(twitch_username)
        if segments is None:
            await ctx.send(f"❌ Failed to fetch schedule from Twitch for {twitch_username}! Check the bot's console for errors or ensure credentials are set.")
            return

        guild_tz_str = guild_config["schedule_timezone"]
        try:
            guild_tz = pytz.timezone(guild_tz_str)
        except pytz.UnknownTimeZoneError:
            self.log.error(f"Unknown timezone '{guild_tz_str}' for guild {ctx.guild.name}. Using UTC for test.")
            guild_tz = pytz.utc

        now_local = datetime.datetime.now(guild_tz)
        start_of_period = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_period = start_of_period + timedelta(days=guild_config["schedule_update_days_in_advance"])

        filtered_schedule = []
        for seg in segments:
            seg_start_time_utc = dateutil.parser.isoparse(seg["start_time"]).replace(tzinfo=datetime.timezone.utc)
            seg_start_time_local = seg_start_time_utc.astimezone(guild_tz)
            if start_of_period <= seg_start_time_local < end_of_period:
                filtered_schedule.append(seg)
        filtered_schedule.sort(key=lambda x: dateutil.parser.isoparse(x["start_time"]))

        if not filtered_schedule:
            await ctx.send(f"No upcoming Twitch schedule events for {twitch_username} in the next {guild_config['schedule_update_days_in_advance']} days for testing.")
            return

        schedule_file = await self.generate_schedule_image(ctx.guild, filtered_schedule, start_of_period)

        if schedule_file:
            await ctx.send(f"✅ Test schedule image generated for {twitch_username}:", file=schedule_file)
        else:
            await ctx.send("❌ Failed to generate the schedule image during test! Check the bot's console for errors.")

    @schedule.command(name="reload")
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
            await ctx.send("❌ Failed to redownload schedule resources. Check bot's console.")