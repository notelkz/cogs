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
import logging
import contextlib
from typing import Optional, Dict, List, Any

# Define a custom view for the buttons
class StreamButtons(discord.ui.View):
    def __init__(self, watch_url: str, subscribe_url: str, timeout=180):
        super().__init__(timeout=timeout)
        self.add_item(discord.ui.Button(label="Watch Now", style=discord.ButtonStyle.link, url=watch_url))
        self.add_item(discord.ui.Button(label="Subscribe", style=discord.ButtonStyle.link, url=subscribe_url))

class RateLimiter:
    """Simple rate limiter to prevent API abuse"""
    def __init__(self, calls_per_minute: int = 120):
        self.calls_per_minute = calls_per_minute
        self.reset_time = time.time() + 60
        self.remaining_calls = calls_per_minute
        self.lock = asyncio.Lock()
    
    async def acquire(self):
        """Acquire permission to make an API call"""
        async with self.lock:
            current_time = time.time()
            # Reset counter if a minute has passed
            if current_time > self.reset_time:
                self.remaining_calls = self.calls_per_minute
                self.reset_time = current_time + 60
            
            # If no calls remaining, wait until reset
            if self.remaining_calls <= 0:
                wait_time = self.reset_time - current_time
                await asyncio.sleep(wait_time)
                self.remaining_calls = self.calls_per_minute
                self.reset_time = time.time() + 60
            
            self.remaining_calls -= 1

class Twitchy(commands.Cog):
    """
    Automatically announces when Twitch streams go live and manages 'Live' roles
    based on Discord activity and provides Twitch schedule functionality.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        self.log = logging.getLogger("red.Twitchy")
        self.log.setLevel(logging.INFO)

        # Default global settings
        default_global = {
            "twitch_client_id": None,
            "twitch_client_secret": None,
            "twitch_access_token": None,
            "twitch_token_expires_at": 0,
        }

        # Default guild settings
        default_guild = {
            "stream_channels": [],
            "live_role": None,
            "message_text": "{streamer} is now LIVE on Twitch! Go watch at {url}",
            "streamers": {},
            "streamer_status_data": {},
            "stream_message_id": None,

            # Schedule specific settings
            "schedule_twitch_username": None,
            "schedule_channel": None,
            "schedule_timezone": "UTC",
            "schedule_update_days_in_advance": 7,
            "schedule_update_time": "00:00",
            "schedule_ping_role_id": None,
            "schedule_event_count": 5
        }

        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)

        # Initialize rate limiter
        self.rate_limiter = RateLimiter(calls_per_minute=100)  # Twitch API limit is 800/minute, being conservative
        
        # Initialize tasks as None to avoid AttributeError if cog_unload is called before tasks are created
        self.check_streams_task = None
        self.update_schedule_task = None
        self.session = None
        
        # Start tasks and session in a controlled manner
        self.bot.loop.create_task(self.initialize())

        # Paths for schedule image resources
        self.data_path = data_manager.cog_data_path(self)
        self.font_path = self.data_path / "Roboto-Regular.ttf"
        self.template_path = self.data_path / "schedule_template.png"
        
        # Track failed API calls to implement circuit breaker pattern
        self.consecutive_api_failures = 0
        self.api_circuit_open = False
        self.circuit_reset_time = 0

    async def initialize(self):
        """Initialize tasks and session after bot is ready"""
        await self.bot.wait_until_ready()
        try:
            self.session = aiohttp.ClientSession()
            self.check_streams_task = self.bot.loop.create_task(self.check_streams_loop())
            self.update_schedule_task = self.bot.loop.create_task(self.schedule_update_loop())
            self.log.info("Twitchy cog initialized successfully")
        except Exception as e:
            self.log.error(f"Error initializing Twitchy cog: {e}")

    async def cog_unload(self):
        """Properly clean up resources when cog is unloaded"""
        self.log.info("Unloading Twitchy cog and cleaning up resources")
        
        # Cancel and await tasks
        if self.check_streams_task:
            self.check_streams_task.cancel()
            try:
                # Wait for task to be cancelled with a timeout
                await asyncio.wait_for(self.check_streams_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
            
        if self.update_schedule_task:
            self.update_schedule_task.cancel()
            try:
                # Wait for task to be cancelled with a timeout
                await asyncio.wait_for(self.update_schedule_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass
        
        # Close session properly
        if self.session:
            try:
                await self.session.close()
                # Give it time to close properly
                await asyncio.sleep(0.25)
            except Exception as e:
                self.log.error(f"Error closing aiohttp session: {e}")

    async def get_access_token(self):
        """Fetches and stores a new Twitch API access token."""
        global_config = await self.config.all()
        client_id = global_config["twitch_client_id"]
        client_secret = global_config["twitch_client_secret"]

        if not client_id or not client_secret:
            self.log.warning("Missing Twitch API credentials")
            return None

        token_url = "https://id.twitch.tv/oauth2/token"
        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials"
        }
        try:
            # Apply rate limiting
            await self.rate_limiter.acquire()
            
            async with self.session.post(token_url, data=payload) as resp:
                if resp.status == 429:  # Too Many Requests
                    retry_after = int(resp.headers.get('Retry-After', '60'))
                    self.log.warning(f"Rate limited by Twitch API. Retrying after {retry_after} seconds")
                    await asyncio.sleep(retry_after)
                    return await self.get_access_token()  # Retry after waiting
                
                resp.raise_for_status()
                data = await resp.json()
                access_token = data.get("access_token")
                expires_in = data.get("expires_in", 3600)  # Default to 1 hour if not provided
                token_expires_at = time.time() + expires_in - 300  # 5 min buffer

                await self.config.twitch_access_token.set(access_token)
                await self.config.twitch_token_expires_at.set(token_expires_at)
                
                # Reset API failure counter on success
                self.consecutive_api_failures = 0
                self.api_circuit_open = False
                
                return access_token
        except aiohttp.ClientResponseError as e:
            self.log.error(f"HTTP error getting Twitch access token: {e.status} - {e.message}")
            self._increment_api_failure()
            return None
        except aiohttp.ClientError as e:
            self.log.error(f"Network error getting Twitch access token: {e}")
            self._increment_api_failure()
            return None
        except asyncio.TimeoutError:
            self.log.error("Timeout while getting Twitch access token")
            self._increment_api_failure()
            return None
        except Exception as e:
            self.log.error(f"Unexpected error getting Twitch access token: {e}")
            self._increment_api_failure()
            return None

    def _increment_api_failure(self):
        """Increment API failure counter and implement circuit breaker pattern"""
        self.consecutive_api_failures += 1
        
        # If too many consecutive failures, open the circuit
        if self.consecutive_api_failures >= 5:
            if not self.api_circuit_open:
                self.log.warning("Circuit breaker activated due to multiple API failures")
                self.api_circuit_open = True
                self.circuit_reset_time = time.time() + 300  # Try again after 5 minutes

    async def _check_circuit_breaker(self) -> bool:
        """Check if circuit breaker is active and handle reset"""
        if not self.api_circuit_open:
            return True
            
        current_time = time.time()
        if current_time >= self.circuit_reset_time:
            self.log.info("Circuit breaker reset, attempting API calls again")
            self.api_circuit_open = False
            self.consecutive_api_failures = 0
            return True
        
        return False

    async def twitch_api_request(self, endpoint, params=None):
        """Makes a request to the Twitch API, handling token refreshing."""
        # Check circuit breaker
        if not await self._check_circuit_breaker():
            self.log.warning(f"Circuit breaker active, skipping API request to {endpoint}")
            return None
            
        global_config = await self.config.all()
        access_token = global_config["twitch_access_token"]
        token_expires_at = global_config["twitch_token_expires_at"]
        client_id = global_config["twitch_client_id"]

        if not client_id:
            self.log.warning("Twitch Client ID not set.")
            return None

        if not access_token or time.time() >= token_expires_at:
            self.log.info("Access token expired or not set, getting new token")
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
            # Apply rate limiting
            await self.rate_limiter.acquire()
            
            async with self.session.get(url, headers=headers, params=params, timeout=10) as resp:
                if resp.status == 429:  # Too Many Requests
                    retry_after = int(resp.headers.get('Retry-After', '60'))
                    self.log.warning(f"Rate limited by Twitch API. Retrying after {retry_after} seconds")
                    await asyncio.sleep(retry_after)
                    return await self.twitch_api_request(endpoint, params)  # Retry after waiting
                
                if resp.status == 401:  # Unauthorized - token might be invalid
                    self.log.warning("Unauthorized API response, refreshing token")
                    # Force token refresh
                    await self.config.twitch_access_token.set(None)
                    await self.config.twitch_token_expires_at.set(0)
                    # Retry with new token
                    return await self.twitch_api_request(endpoint, params)
                
                resp.raise_for_status()
                data = await resp.json()
                
                # Reset API failure counter on success
                self.consecutive_api_failures = 0
                self.api_circuit_open = False
                
                return data
        except aiohttp.ClientResponseError as e:
            self.log.error(f"HTTP error in Twitch API request to {endpoint}: {e.status} - {e.message}")
            self._increment_api_failure()
            return None
        except aiohttp.ClientError as e:
            self.log.error(f"Network error in Twitch API request to {endpoint}: {e}")
            self._increment_api_failure()
            return None
        except asyncio.TimeoutError:
            self.log.error(f"Timeout in Twitch API request to {endpoint}")
            self._increment_api_failure()
            return None
        except Exception as e:
            self.log.error(f"Unexpected error in Twitch API request to {endpoint}: {e}")
            self._increment_api_failure()
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
        """Background task that checks for live streams"""
        await self.bot.wait_until_ready()
        
        # Add initial delay to ensure everything is properly initialized
        await asyncio.sleep(10)
        
        self.log.info("Stream checking loop started")
        
        while True:
            try:
                # Check if circuit breaker is active
                if not await self._check_circuit_breaker():
                    self.log.warning("Circuit breaker active, skipping stream check cycle")
                    await asyncio.sleep(60)
                    continue
                
                guilds_data = await self.config.all_guilds()
                all_twitch_usernames = set()
                
                for guild_id, guild_config in guilds_data.items():
                    all_twitch_usernames.update(guild_config["streamers"].keys())

                if not all_twitch_usernames:
                    await asyncio.sleep(60)  # Sleep if no streamers configured
                    continue

                # Fetch user IDs and update streamer_status_data
                user_info = await self.get_twitch_user_info(list(all_twitch_usernames))
                if user_info is None:  # API error
                    self.log.warning("Failed to fetch user info, will retry next cycle")
                    await asyncio.sleep(60)
                    continue
                    
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
                    # Process in smaller chunks to avoid request size limits
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
                                await self._send_stream_announcement(
                                    guild=guild,
                                    stream_channels=stream_channels,
                                    stream=stream,
                                    username=username,
                                    display_name=display_name,
                                    profile_image_url=profile_image_url,
                                    message_text=message_text
                                )

                                # Assign live role
                                if live_role and streamer_data.get("discord_user_id"):
                                    await self._assign_live_role(
                                        guild=guild,
                                        member_id=streamer_data["discord_user_id"],
                                        live_role=live_role,
                                        assign=True
                                    )

                        elif not is_live and current_status == "live":
                            # Streamer just went offline
                            await self.config.guild(guild).streamers.set_attr(username, "current_status", "offline")

                            # Remove live role
                            if live_role and streamer_data.get("discord_user_id"):
                                await self._assign_live_role(
                                    guild=guild,
                                    member_id=streamer_data["discord_user_id"],
                                    live_role=live_role,
                                    assign=False
                                )

                            # Edit old announcement message to mark as offline
                            await self._update_offline_announcement(
                                guild=guild,
                                stream_channels=stream_channels,
                                display_name=display_name
                            )

            except asyncio.CancelledError:
                self.log.info("Stream checking loop cancelled")
                break
            except Exception as e:
                self.log.error(f"Error in check_streams_loop: {traceback.format_exc()}")
            finally:
                await asyncio.sleep(60)  # Check every minute

    async def _send_stream_announcement(self, guild, stream_channels, stream, username, display_name, profile_image_url, message_text):
        """Send stream announcement to configured channels"""
        try:
            embed = discord.Embed(
                title=stream["title"],
                url=f"https://www.twitch.tv/{username}",
                description=f"{display_name} is now streaming **{stream.get('game_name', 'Unknown Game')}**!",
                color=0x6441a5  # Twitch purple
            )
            
            # Add thumbnail if available
            thumbnail_url = stream.get("thumbnail_url")
            if thumbnail_url:
                thumbnail_url = thumbnail_url.replace("{width}", "1280").replace("{height}", "720")
                embed.set_image(url=thumbnail_url)
                
            embed.set_author(name=display_name, url=f"https://www.twitch.tv/{username}", icon_url=profile_image_url)
            embed.set_footer(text="Twitch Stream")
            embed.timestamp = datetime.datetime.now(datetime.timezone.utc)

            watch_url = f"https://www.twitch.tv/{username}"
            subscribe_url = f"https://www.twitch.tv/subs/{username}"
            view = StreamButtons(watch_url, subscribe_url)

            for channel in stream_channels:
                try:
                    msg = await channel.send(
                        content=message_text.format(streamer=display_name, url=watch_url),
                        embed=embed,
                        view=view
                    )
                    await self.config.guild(guild).stream_message_id.set(msg.id)
                except discord.Forbidden:
                    self.log.warning(f"Missing permissions to send messages in {channel.name} ({channel.id}) in guild {guild.name} ({guild.id})")
                except discord.HTTPException as e:
                    self.log.error(f"HTTP error sending stream announcement in {guild.name}: {e}")
                except Exception as e:
                    self.log.error(f"Error sending stream announcement in {guild.name}: {e}")
        except Exception as e:
            self.log.error(f"Error creating stream announcement for {username} in {guild.name}: {e}")

    async def _assign_live_role(self, guild, member_id, live_role, assign=True):
        """Assign or remove live role from a member"""
        try:
            member = guild.get_member(member_id)
            if not member:
                return
                
            if assign and live_role not in member.roles:
                await member.add_roles(live_role)
                self.log.debug(f"Added live role to {member.display_name} in {guild.name}")
            elif not assign and live_role in member.roles:
                await member.remove_roles(live_role)
                self.log.debug(f"Removed live role from {member.display_name} in {guild.name}")
        except discord.Forbidden:
            self.log.warning(f"Missing permissions to manage roles in guild {guild.name}")
        except discord.HTTPException as e:
            self.log.error(f"HTTP error managing live role in {guild.name}: {e}")
        except Exception as e:
            self.log.error(f"Error managing live role in {guild.name}: {e}")

    async def _update_offline_announcement(self, guild, stream_channels, display_name):
        """Update stream announcement when streamer goes offline"""
        message_id = await self.config.guild(guild).stream_message_id()
        if not message_id:
            return
            
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
                    await msg.edit(embed=embed, view=None)  # Remove buttons too
                    await self.config.guild(guild).stream_message_id.set(None)  # Clear stored message ID
                    break  # Only need to update one message
            except discord.NotFound:
                self.log.debug(f"Stream announcement message {message_id} not found in channel {channel.id}.")
            except discord.Forbidden:
                self.log.warning(f"Missing permissions to edit messages in {channel.name} in guild {guild.name}")
            except discord.HTTPException as e:
                self.log.error(f"HTTP error editing stream announcement in {guild.name}: {e}")
            except Exception as e:
                self.log.error(f"Error editing stream announcement in {guild.name}: {e}")
                
        # Clear message ID if we couldn't find it in any channel
        await self.config.guild(guild).stream_message_id.set(None)

    async def schedule_update_loop(self):
        """Background task that updates Twitch schedules"""
        await self.bot.wait_until_ready()
        
        # Add initial delay to ensure everything is properly initialized
        await asyncio.sleep(15)
        
        self.log.info("Schedule update loop started")
        
        while True:
            try:
                # Check if circuit breaker is active
                if not await self._check_circuit_breaker():
                    self.log.warning("Circuit breaker active, skipping schedule update cycle")
                    await asyncio.sleep(60 * 10)  # 10 minutes
                    continue
                    
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
                    # Use a small buffer (e.g., 5 minutes) to ensure it triggers correctly
                    time_until_update = (next_update_time_utc - now_utc).total_seconds()
                    if 0 <= time_until_update <= 300:  # Within 5 minutes of scheduled time
                        channel = guild.get_channel(schedule_channel_id)
                        if channel:
                            self.log.info(f"Updating schedule for guild {guild.name} in channel {channel.name}")
                            await self.post_twitch_schedule(guild)
                            # After posting, sleep for a while to avoid duplicate posts
                            await asyncio.sleep(600)  # Sleep for 10 minutes after posting

            except asyncio.CancelledError:
                self.log.info("Schedule update loop cancelled")
                break
            except Exception as e:
                self.log.error(f"Error in schedule_update_loop: {traceback.format_exc()}")
            finally:
                # Sleep for a reasonable interval before checking again (e.g., 5 minutes)
                await asyncio.sleep(60 * 5)

    async def ensure_schedule_resources(self):
        """Ensures font and template image files exist, downloading if necessary."""
        self.data_path.mkdir(parents=True, exist_ok=True)

        font_url = "https://github.com/google/fonts/raw/main/apache/robotoslab/RobotoSlab-Regular.ttf"
        template_url = "https://raw.githubusercontent.com/notelkz/Red-DiscordBot-Cogs/main/twitchy/data/schedule_template.png"

        tasks = []
        if not self.font_path.exists():
            tasks.append(self._download_file(font_url, self.font_path, "font"))
        if not self.template_path.exists():
            tasks.append(self._download_file(template_url, self.template_path, "template image"))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            # Check for exceptions in results
            success = True
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    self.log.error(f"Error downloading resource {i}: {result}")
                    success = False
                elif result is False:
                    success = False
            return success
        return True  # No downloads needed, files already exist

    async def _download_file(self, url: str, path, description: str):
        """Helper to download a file with proper error handling."""
        try:
            async with self.session.get(url, timeout=30) as resp:
                if resp.status != 200:
                    self.log.error(f"Failed to download {description} from {url}: HTTP {resp.status}")
                    return False
                    
                data = await resp.read()
                if not data:
                    self.log.error(f"Downloaded empty file for {description}")
                    return False
                    
                # Write to a temporary file first, then rename to avoid partial files
                temp_path = f"{path}.temp"
                with open(temp_path, "wb") as f:
                    f.write(data)
                    
                # Verify file integrity
                if description == "font" and not self._verify_font_file(temp_path):
                    self.log.error(f"Downloaded font file is invalid: {temp_path}")
                    os.remove(temp_path)
                    return False
                elif description == "template image" and not self._verify_image_file(temp_path):
                    self.log.error(f"Downloaded image file is invalid: {temp_path}")
                    os.remove(temp_path)
                    return False
                    
                # Rename to final path
                os.replace(temp_path, path)
                
            self.log.info(f"Successfully downloaded {description} to {path}")
            return True
        except aiohttp.ClientError as e:
            self.log.error(f"Network error downloading {description} from {url}: {e}")
            return False
        except asyncio.TimeoutError:
            self.log.error(f"Timeout downloading {description} from {url}")
            return False
        except OSError as e:
            self.log.error(f"OS error saving {description} to {path}: {e}")
            return False
        except Exception as e:
            self.log.error(f"Unexpected error downloading {description}: {e}")
            return False

    def _verify_font_file(self, path):
        """Verify that the downloaded font file is valid"""
        try:
            # Try to load the font to verify it's valid
            with open(path, "rb") as f:
                font_data = f.read(4)  # Read first 4 bytes to check signature
                # Check for common font file signatures
                return font_data.startswith(b"\x00\x01\x00\x00") or font_data.startswith(b"OTTO") or font_data.startswith(b"true") or font_data.startswith(b"ttcf")
        except Exception as e:
            self.log.error(f"Error verifying font file: {e}")
            return False

    def _verify_image_file(self, path):
        """Verify that the downloaded image file is valid"""
        try:
            # Try to open the image to verify it's valid
            with Image.open(path) as img:
                img.verify()
            return True
        except Exception as e:
            self.log.error(f"Error verifying image file: {e}")
            return False

    async def get_twitch_schedule(self, twitch_username: str):
        """Fetches the Twitch schedule for a given username."""
        user_info = await self.get_twitch_user_info([twitch_username])
        if not user_info or twitch_username not in user_info:
            self.log.error(f"Could not find Twitch user info for {twitch_username}")
            return None

        broadcaster_id = user_info[twitch_username]["id"]
        params = {"broadcaster_id": broadcaster_id}
        data = await self.twitch_api_request("schedule", params=params)

        if data and "data" in data and "segments" in data["data"]:
            return data["data"]["segments"]
        elif data and "data" in data:
            # Schedule exists but has no segments
            self.log.info(f"No schedule segments found for {twitch_username}")
            return []
        else:
            self.log.error(f"Failed to fetch schedule for {twitch_username}")
            return None

    async def generate_schedule_image(self, guild: discord.Guild, schedule_segments: list, start_date: datetime.datetime):
        """Generates a schedule image from given segments with proper resource management."""
        if not await self.ensure_schedule_resources():
            self.log.error("Missing schedule image resources. Cannot generate image.")
            return None

        # Use context manager to ensure image resources are properly closed
        with contextlib.ExitStack() as stack:
            try:
                template = stack.enter_context(Image.open(self.template_path))
                draw = ImageDraw.Draw(template)
                font_size_header = 40
                font_size_event = 30
                font_size_small = 25

                # Load fonts
                try:
                    font_header = ImageFont.truetype(str(self.font_path), font_size_header)
                    font_event = ImageFont.truetype(str(self.font_path), font_size_event)
                    font_small = ImageFont.truetype(str(self.font_path), font_size_small)
                except IOError as e:
                    self.log.error(f"Error loading font file: {e}")
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

                        try:
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
                        except (ValueError, KeyError) as e:
                            self.log.error(f"Error processing schedule segment: {e}")
                            continue

                # Save image to bytes buffer
                img_byte_arr = io.BytesIO()
                template.save(img_byte_arr, format="PNG")
                img_byte_arr.seek(0)
                
                # Create Discord file object
                return discord.File(img_byte_arr, filename="twitch_schedule.png")

            except FileNotFoundError as e:
                self.log.error(f"File not found error generating schedule image: {e}")
                return None
            except OSError as e:
                self.log.error(f"OS error generating schedule image: {e}")
                return None
            except Exception as e:
                self.log.error(f"Error generating schedule image: {traceback.format_exc()}")
                return None

    async def post_twitch_schedule(self, guild: discord.Guild):
        """Fetches and posts the Twitch schedule for a guild's configured streamer."""
        try:
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
                try:
                    seg_start_time_utc = dateutil.parser.isoparse(seg["start_time"]).replace(tzinfo=datetime.timezone.utc)
                    seg_start_time_local = seg_start_time_utc.astimezone(guild_tz)
                    if start_of_period <= seg_start_time_local < end_of_period:
                        filtered_schedule.append(seg)
                except (ValueError, KeyError) as e:
                    self.log.error(f"Error processing schedule segment: {e}")
                    continue
                    
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
                    self.log.warning(f"Missing permissions to send messages/files in {channel.name} in guild {guild.name}")
                except discord.HTTPException as e:
                    self.log.error(f"HTTP error posting schedule image in {guild.name}: {e}")
                except Exception as e:
                    self.log.error(f"Error posting schedule image in {guild.name}: {e}")
            else:
                await channel.send("Failed to generate the schedule image. Check bot logs for details.")
        except Exception as e:
            self.log.error(f"Error in post_twitch_schedule for guild {guild.name}: {traceback.format_exc()}")


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
            linked_status = f"(Linked to {discord_user.display_name})" if discord_user else "(Not linked to a Discord user.)"
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
    @twitchy.group()
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
            pytz.timezone(timezone_str)  # Test if timezone is valid
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
        await ctx.defer()  # Acknowledge command to prevent timeout
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
        except Exception as e:
            self.log.error(f"Error in schedule_show command: {e}")
            await ctx.send(f"❌ An error occurred: {str(e)}")
        finally:
            # Revert schedule_channel to original setting
            await self.config.guild(ctx.guild).schedule_channel.set(original_channel_id)

    @schedule.command(name="test")
    async def schedule_test_generation(self, ctx):
        """Tests schedule image generation and resource downloading.
        (Does not post to the configured schedule channel automatically, uses current channel.)
        """
        await ctx.defer()  # Acknowledge command to prevent timeout
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
            try:
                seg_start_time_utc = dateutil.parser.isoparse(seg["start_time"]).replace(tzinfo=datetime.timezone.utc)
                seg_start_time_local = seg_start_time_utc.astimezone(guild_tz)
                if start_of_period <= seg_start_time_local < end_of_period:
                    filtered_schedule.append(seg)
            except (ValueError, KeyError) as e:
                self.log.error(f"Error processing schedule segment: {e}")
                continue
                
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

    @twitchy.command(name="status")
    @commands.is_owner()
    async def twitchy_status(self, ctx):
        """Shows the current status of the Twitchy cog and API connection."""
        embed = discord.Embed(
            title="Twitchy Status",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        
        # Check API credentials
        global_config = await self.config.all()
        client_id = global_config["twitch_client_id"]
        client_secret = global_config["twitch_client_secret"]
        access_token = global_config["twitch_access_token"]
        token_expires_at = global_config["twitch_token_expires_at"]
        
        # API status
        if not client_id or not client_secret:
            api_status = "❌ Missing API credentials"
        elif not access_token:
            api_status = "⚠️ No access token (will be generated on next API call)"
        elif time.time() >= token_expires_at:
            api_status = "⚠️ Access token expired (will be refreshed on next API call)"
        else:
            minutes_remaining = int((token_expires_at - time.time()) / 60)
            api_status = f"✅ Connected (token expires in {minutes_remaining} minutes)"
        
        embed.add_field(name="API Status", value=api_status, inline=False)
        
        # Circuit breaker status
        if self.api_circuit_open:
            reset_in = int(self.circuit_reset_time - time.time())
            circuit_status = f"⚠️ Open (resets in {reset_in} seconds)"
        else:
            circuit_status = "✅ Closed"
        
        embed.add_field(name="Circuit Breaker", value=circuit_status, inline=True)
        embed.add_field(name="API Failures", value=str(self.consecutive_api_failures), inline=True)
        
        # Background tasks
        check_streams_status = "✅ Running" if self.check_streams_task and not self.check_streams_task.done() else "❌ Not running"
        update_schedule_status = "✅ Running" if self.update_schedule_task and not self.update_schedule_task.done() else "❌ Not running"
        
        embed.add_field(name="Check Streams Task", value=check_streams_status, inline=True)
        embed.add_field(name="Update Schedule Task", value=update_schedule_status, inline=True)
        
        # Resource status
        font_status = "✅ Present" if self.font_path.exists() else "❌ Missing"
        template_status = "✅ Present" if self.template_path.exists() else "❌ Missing"
        
        embed.add_field(name="Font File", value=font_status, inline=True)
        embed.add_field(name="Template Image", value=template_status, inline=True)
        
        # Guild stats
        guild_count = 0
        streamer_count = 0
        for guild_id in await self.config.all_guilds():
            guild = self.bot.get_guild(guild_id)
            if guild:
                guild_count += 1
                guild_data = await self.config.guild(guild).all()
                streamer_count += len(guild_data.get("streamers", {}))
        
        embed.add_field(name="Active Guilds", value=str(guild_count), inline=True)
        embed.add_field(name="Tracked Streamers", value=str(streamer_count), inline=True)
        
        await ctx.send(embed=embed)

    @twitchy.command(name="debug")
    @commands.is_owner()
    async def twitchy_debug(self, ctx, action: str = None):
        """Debug commands for troubleshooting.
        
        Actions:
        - reset_circuit: Reset the circuit breaker
        - restart_tasks: Restart background tasks
        - check_api: Test API connection
        """
        if action == "reset_circuit":
            self.consecutive_api_failures = 0
            self.api_circuit_open = False
            await ctx.send("✅ Circuit breaker reset.")
            
        elif action == "restart_tasks":
            # Cancel existing tasks
            if self.check_streams_task:
                self.check_streams_task.cancel()
            if self.update_schedule_task:
                self.update_schedule_task.cancel()
                
            # Create new tasks
            self.check_streams_task = self.bot.loop.create_task(self.check_streams_loop())
            self.update_schedule_task = self.bot.loop.create_task(self.schedule_update_loop())
            await ctx.send("✅ Background tasks restarted.")
            
        elif action == "check_api":
            await ctx.send("🔄 Testing API connection...")
            token = await self.get_access_token()
            if token:
                await ctx.send("✅ Successfully connected to Twitch API and obtained access token.")
            else:
                await ctx.send("❌ Failed to connect to Twitch API. Check your credentials and logs.")
                
        else:
            await ctx.send("Please specify a valid debug action: `reset_circuit`, `restart_tasks`, or `check_api`.")

    @twitchy.command(name="clearcache")
    @commands.has_permissions(manage_guild=True)
    async def twitchy_clear_cache(self, ctx):
        """Clears cached stream data for this guild."""
        # Reset stream message ID
        await self.config.guild(ctx.guild).stream_message_id.set(None)
        
        # Reset all streamers to offline status
        async with self.config.guild(ctx.guild).streamers() as streamers:
            for username in streamers:
                streamers[username]["current_status"] = "offline"
                streamers[username]["last_stream_id"] = None
                
        await ctx.send("✅ Stream cache cleared. All streamers reset to offline status.")
