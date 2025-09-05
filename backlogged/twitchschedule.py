import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
import aiohttp
import datetime
from datetime import timedelta
import asyncio
import traceback
from PIL import Image, ImageDraw, ImageFont
import io
import os
import pytz
import re
import dateutil.parser
import hashlib
from typing import Optional, List, Dict, Any, Tuple
import json
from contextlib import asynccontextmanager

london_tz = pytz.timezone("Europe/London")

class RateLimiter:
    """Simple rate limiter for API calls"""
    def __init__(self, max_calls: int, time_window: int):
        self.max_calls = max_calls
        self.time_window = time_window
        self.calls = []
        self._lock = asyncio.Lock()
    
    async def acquire(self):
        async with self._lock:
            now = datetime.datetime.now()
            # Remove calls outside the time window
            self.calls = [call_time for call_time in self.calls 
                         if (now - call_time).total_seconds() < self.time_window]
            
            if len(self.calls) >= self.max_calls:
                # Calculate wait time
                oldest_call = min(self.calls)
                wait_time = self.time_window - (now - oldest_call).total_seconds()
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                    return await self.acquire()  # Recursive call after waiting
            
            self.calls.append(now)

class TwitchTokenManager:
    """Manages Twitch API token lifecycle"""
    def __init__(self, bot: Red):
        self.bot = bot
        self.access_token = None
        self.token_expires_at = None
        self._token_lock = asyncio.Lock()
    
    async def get_credentials(self) -> Optional[Tuple[str, str]]:
        """Get Twitch API credentials from bot config"""
        try:
            tokens = await self.bot.get_shared_api_tokens("twitch")
            if tokens.get("client_id") and tokens.get("client_secret"):
                return tokens["client_id"], tokens["client_secret"]
        except Exception:
            pass
        return None
    
    async def get_valid_token(self) -> Optional[str]:
        """Get a valid access token, refreshing if necessary"""
        async with self._token_lock:
            now = datetime.datetime.now()
            
            # Check if current token is still valid (with 5 minute buffer)
            if (self.access_token and self.token_expires_at and 
                now < self.token_expires_at - timedelta(minutes=5)):
                return self.access_token
            
            # Need to refresh token
            credentials = await self.get_credentials()
            if not credentials:
                return None
            
            client_id, client_secret = credentials
            
            try:
                async with aiohttp.ClientSession() as session:
                    url = "https://id.twitch.tv/oauth2/token"
                    params = {
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "grant_type": "client_credentials"
                    }
                    
                    async with session.post(url, params=params) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            self.access_token = data.get("access_token")
                            expires_in = data.get("expires_in", 3600)  # Default 1 hour
                            self.token_expires_at = now + timedelta(seconds=expires_in)
                            return self.access_token
                        else:
                            self.access_token = None
                            self.token_expires_at = None
                            return None
            except Exception:
                self.access_token = None
                self.token_expires_at = None
                return None

class ResourceManager:
    """Manages file downloads and caching with integrity checks"""
    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        self._download_locks = {}
        
        os.makedirs(cache_dir, exist_ok=True)
    
    def _get_file_hash(self, filepath: str) -> Optional[str]:
        """Get SHA256 hash of a file"""
        try:
            with open(filepath, 'rb') as f:
                return hashlib.sha256(f.read()).hexdigest()
        except Exception:
            return None
    
    async def download_file(self, url: str, save_path: str, expected_hash: Optional[str] = None) -> bool:
        """Download file with integrity checking and concurrent download protection"""
        # Use per-file locks to prevent concurrent downloads of the same file
        if save_path not in self._download_locks:
            self._download_locks[save_path] = asyncio.Lock()
        
        async with self._download_locks[save_path]:
            # Check if file already exists and is valid
            if os.path.exists(save_path):
                if expected_hash:
                    current_hash = self._get_file_hash(save_path)
                    if current_hash == expected_hash:
                        return True
                else:
                    # If no hash provided, assume existing file is valid
                    return True
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            content = await resp.read()
                            
                            # Validate hash if provided
                            if expected_hash:
                                actual_hash = hashlib.sha256(content).hexdigest()
                                if actual_hash != expected_hash:
                                    return False
                            
                            # Write to temporary file first, then move
                            temp_path = f"{save_path}.tmp"
                            with open(temp_path, 'wb') as f:
                                f.write(content)
                            
                            # Atomic move
                            os.rename(temp_path, save_path)
                            return True
                        else:
                            return False
            except Exception:
                # Clean up temp file if it exists
                temp_path = f"{save_path}.tmp"
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception:
                        pass
                return False
    
    def cleanup_old_files(self, max_age_days: int = 7):
        """Remove old cache files"""
        try:
            now = datetime.datetime.now()
            for filename in os.listdir(self.cache_dir):
                filepath = os.path.join(self.cache_dir, filename)
                if os.path.isfile(filepath):
                    file_time = datetime.datetime.fromtimestamp(os.path.getmtime(filepath))
                    if (now - file_time).days > max_age_days:
                        os.remove(filepath)
        except Exception:
            pass

class StreamerConfig:
    """Configuration for a single streamer"""
    def __init__(self, data: dict):
        self.twitch_username: str = data.get("twitch_username", "")
        self.schedule_channel_id: Optional[int] = data.get("schedule_channel_id")
        self.notification_channel_id: Optional[int] = data.get("notification_channel_id")
        self.notify_role_id: Optional[int] = data.get("notify_role_id")
        self.update_days: List[int] = data.get("update_days", [])
        self.update_time: Optional[str] = data.get("update_time")
        self.event_count: int = data.get("event_count", 5)
        self.weeks_to_show: int = data.get("weeks_to_show", 1)
        self.custom_template_url: Optional[str] = data.get("custom_template_url")
        self.custom_font_url: Optional[str] = data.get("custom_font_url")
        self.enabled: bool = data.get("enabled", True)
    
    def to_dict(self) -> dict:
        return {
            "twitch_username": self.twitch_username,
            "schedule_channel_id": self.schedule_channel_id,
            "notification_channel_id": self.notification_channel_id,
            "notify_role_id": self.notify_role_id,
            "update_days": self.update_days,
            "update_time": self.update_time,
            "event_count": self.event_count,
            "weeks_to_show": self.weeks_to_show,
            "custom_template_url": self.custom_template_url,
            "custom_font_url": self.custom_font_url,
            "enabled": self.enabled
        }
    
    @property
    def is_configured(self) -> bool:
        """Check if streamer is fully configured"""
        return (self.twitch_username and 
                self.schedule_channel_id and 
                self.update_days and 
                self.update_time and
                self.enabled)

class TwitchSchedule(commands.Cog):
    """Multi-streamer Twitch scheduling bot with enhanced reliability"""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567891)  # Changed ID for new schema
        
        # New multi-streamer configuration structure
        default_guild = {
            "streamers": {},  # Dict[str, StreamerConfig.to_dict()]
            "log_channel_id": None,
            "global_enabled": True
        }
        self.config.register_guild(**default_guild)
        
        # Initialize managers
        self.cache_dir = os.path.join(os.path.dirname(__file__), "cache")
        self.resource_manager = ResourceManager(self.cache_dir)
        self.token_manager = TwitchTokenManager(bot)
        self.rate_limiter = RateLimiter(max_calls=30, time_window=60)  # Conservative rate limiting
        
        # Default resource URLs and paths
        self.default_font_url = "https://zerolivesleft.net/notelkz/P22.ttf"
        self.default_template_url = "https://zerolivesleft.net/notelkz/schedule.png"
        
        # Start background tasks
        self.schedule_task = None
        self.cleanup_task = None
        self._start_background_tasks()
    
    def _start_background_tasks(self):
        """Start background tasks with proper error handling"""
        if self.schedule_task is None or self.schedule_task.done():
            self.schedule_task = asyncio.create_task(self._schedule_update_loop())
        
        if self.cleanup_task is None or self.cleanup_task.done():
            self.cleanup_task = asyncio.create_task(self._cleanup_loop())
    
    def cog_unload(self):
        """Clean shutdown of background tasks"""
        if self.schedule_task and not self.schedule_task.done():
            self.schedule_task.cancel()
        if self.cleanup_task and not self.cleanup_task.done():
            self.cleanup_task.cancel()
    
    async def _cleanup_loop(self):
        """Periodic cleanup of old cache files"""
        await self.bot.wait_until_ready()
        while True:
            try:
                self.resource_manager.cleanup_old_files()
                await asyncio.sleep(3600)  # Clean up every hour
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(3600)
    
    async def _log_error(self, guild: discord.Guild, error_message: str, streamer_name: str = None):
        """Log errors to configured log channel"""
        log_channel_id = await self.config.guild(guild).log_channel_id()
        if log_channel_id:
            log_channel = guild.get_channel(log_channel_id)
            if log_channel:
                embed = discord.Embed(
                    title="Twitch Schedule Error",
                    description=f"```py\n{error_message[:1900]}\n```",  # Truncate for Discord limits
                    color=discord.Color.red(),
                    timestamp=datetime.datetime.now(datetime.timezone.utc)
                )
                if streamer_name:
                    embed.add_field(name="Streamer", value=streamer_name, inline=True)
                
                try:
                    await log_channel.send(embed=embed)
                except Exception:
                    pass  # Don't let logging errors crash the bot
    
    def _get_resource_paths(self, streamer_name: str) -> Tuple[str, str]:
        """Get file paths for streamer's resources"""
        font_path = os.path.join(self.cache_dir, f"{streamer_name}_font.ttf")
        template_path = os.path.join(self.cache_dir, f"{streamer_name}_template.png")
        return font_path, template_path
    
    async def _ensure_resources(self, guild: discord.Guild, streamer_config: StreamerConfig) -> bool:
        """Ensure required resources are available for a streamer"""
        font_path, template_path = self._get_resource_paths(streamer_config.twitch_username)
        
        # Determine URLs to use
        font_url = streamer_config.custom_font_url or self.default_font_url
        template_url = streamer_config.custom_template_url or self.default_template_url
        
        # Download resources concurrently
        font_task = self.resource_manager.download_file(font_url, font_path)
        template_task = self.resource_manager.download_file(template_url, template_path)
        
        try:
            font_success, template_success = await asyncio.gather(font_task, template_task)
        except Exception as e:
            await self._log_error(guild, f"Resource download failed: {e}", streamer_config.twitch_username)
            return False
        
        if not font_success:
            await self._log_error(guild, f"Failed to download font from {font_url}", streamer_config.twitch_username)
        
        if not template_success:
            await self._log_error(guild, f"Failed to download template from {template_url}", streamer_config.twitch_username)
        
        return font_success and template_success
    
    @asynccontextmanager
    async def _api_request(self):
        """Context manager for API requests with rate limiting"""
        await self.rate_limiter.acquire()
        token = await self.token_manager.get_valid_token()
        if not token:
            raise Exception("Failed to obtain valid Twitch API token")
        
        credentials = await self.token_manager.get_credentials()
        if not credentials:
            raise Exception("Twitch API credentials not configured")
        
        client_id, _ = credentials
        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {token}"
        }
        
        yield headers
    
    async def _get_user_id(self, username: str) -> Optional[str]:
        """Get Twitch user ID from username"""
        try:
            async with self._api_request() as headers:
                async with aiohttp.ClientSession() as session:
                    url = f"https://api.twitch.tv/helix/users?login={username}"
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("data"):
                                return data["data"][0]["id"]
        except Exception:
            pass
        return None
    
    async def _get_schedule_for_range(self, username: str, start_date: datetime.datetime, 
                                    end_date: datetime.datetime) -> Optional[List[dict]]:
        """Get schedule for a specific date range"""
        try:
            user_id = await self._get_user_id(username)
            if not user_id:
                return None
            
            async with self._api_request() as headers:
                async with aiohttp.ClientSession() as session:
                    url = f"https://api.twitch.tv/helix/schedule?broadcaster_id={user_id}"
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 404:
                            return []  # No schedule set up
                        elif resp.status != 200:
                            return None
                        
                        data = await resp.json()
                        segments = data.get("data", {}).get("segments", [])
                        
                        filtered_segments = []
                        for seg in segments:
                            start_time = dateutil.parser.isoparse(seg["start_time"])
                            if start_time.tzinfo is None:
                                start_time = start_time.replace(tzinfo=datetime.timezone.utc)
                            
                            start_time_local = start_time.astimezone(london_tz)
                            
                            if start_date <= start_time_local <= end_date:
                                seg["broadcaster_name"] = username
                                filtered_segments.append(seg)
                        
                        return filtered_segments
        except Exception:
            return None
    
    async def _get_category_info(self, category_id: str) -> Optional[dict]:
        """Get game/category information"""
        try:
            async with self._api_request() as headers:
                async with aiohttp.ClientSession() as session:
                    url = f"https://api.twitch.tv/helix/games?id={category_id}"
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data.get("data"):
                                return data["data"][0]
        except Exception:
            pass
        return None
    
    async def _generate_schedule_image(self, schedule_segments: List[dict], guild: discord.Guild, 
                                     streamer_config: StreamerConfig, start_date: datetime.datetime = None) -> Optional[io.BytesIO]:
        """Generate schedule image with proper error handling and resource management"""
        if not await self._ensure_resources(guild, streamer_config):
            return None
        
        font_path, template_path = self._get_resource_paths(streamer_config.twitch_username)
        
        try:
            img = Image.open(template_path)
            actual_events = min(len(schedule_segments), streamer_config.event_count)
            
            # Dynamic image resizing logic (simplified for reliability)
            if actual_events < streamer_config.event_count:
                width, height = img.size
                row_height = 150
                height_reduction = (streamer_config.event_count - actual_events) * row_height
                new_height = max(height - height_reduction, 400)  # Minimum height
                
                # Create new image with adjusted height
                new_img = Image.new(img.mode, (width, new_height), (0, 0, 0))
                
                # Copy header
                header_height = min(350, height)
                new_img.paste(img.crop((0, 0, width, header_height)), (0, 0))
                
                # Copy events section if there are events
                if actual_events > 0:
                    events_height = actual_events * row_height
                    events_start = 350
                    if events_start + events_height <= height:
                        new_img.paste(
                            img.crop((0, events_start, width, events_start + events_height)), 
                            (0, events_start)
                        )
                
                img = new_img
            
            draw = ImageDraw.Draw(img)
            
            # Load fonts with fallback
            try:
                title_font = ImageFont.truetype(font_path, 90)
                date_font = ImageFont.truetype(font_path, 40)
                schedule_font = ImageFont.truetype(font_path, 42)
            except Exception:
                # Fallback to default font
                title_font = ImageFont.load_default()
                date_font = ImageFont.load_default()
                schedule_font = ImageFont.load_default()
            
            # Calculate date range
            if start_date is None:
                today = datetime.datetime.now(london_tz)
                days_since_sunday = today.weekday() + 1
                if days_since_sunday == 7:
                    days_since_sunday = 0
                start_of_week = today - timedelta(days=days_since_sunday)
                start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                start_of_week = start_date
            
            # Generate date text
            if streamer_config.weeks_to_show > 1:
                end_week = start_of_week + timedelta(days=(streamer_config.weeks_to_show * 7) - 1)
                date_text = f"{start_of_week.strftime('%b %d')} - {end_week.strftime('%b %d')}"
                week_text = "Weeks of"
            else:
                date_text = start_of_week.strftime("%B %d")
                week_text = "Week of"
            
            # Draw date information
            width, _ = img.size
            right_margin = 100
            
            try:
                week_bbox = title_font.getbbox(week_text)
                date_bbox = date_font.getbbox(date_text)
                week_width = week_bbox[2] - week_bbox[0]
                date_width = date_bbox[2] - date_bbox[0]
            except Exception:
                # Fallback for older PIL versions
                week_width, _ = title_font.getsize(week_text)
                date_width, _ = date_font.getsize(date_text)
            
            week_x = width - right_margin - week_width
            date_x = width - right_margin - date_width
            
            draw.text((week_x, 100), week_text, font=title_font, fill=(255, 255, 255))
            draw.text((date_x, 180), date_text, font=date_font, fill=(255, 255, 255))
            
            # Draw schedule events
            day_x = 125
            game_x = 125
            initial_y = 350
            row_height = 150
            day_offset = -45
            
            for i, segment in enumerate(schedule_segments[:actual_events]):
                bar_y = initial_y + (i * row_height)
                day_y = bar_y + day_offset
                game_y = bar_y + 15
                
                start_time_utc = dateutil.parser.isoparse(segment["start_time"])
                if start_time_utc.tzinfo is None:
                    start_time_utc = start_time_utc.replace(tzinfo=datetime.timezone.utc)
                
                start_time_london = start_time_utc.astimezone(london_tz)
                day_time = start_time_london.strftime("%A // %I:%M%p").upper()
                title = segment.get("title", "Untitled Stream")
                
                # Truncate long titles
                if len(title) > 50:
                    title = title[:47] + "..."
                
                draw.text((day_x, day_y), day_time, font=schedule_font, fill=(255, 255, 255))
                draw.text((game_x, game_y), title, font=schedule_font, fill=(255, 255, 255))
            
            # Convert to BytesIO
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf
            
        except Exception as e:
            await self._log_error(guild, f"Image generation failed: {e}", streamer_config.twitch_username)
            return None
    
    async def _bulk_delete_messages(self, channel: discord.TextChannel, message_ids: List[int]):
        """Bulk delete messages with proper error handling and rate limiting"""
        if not message_ids:
            return
        
        # Discord bulk delete limit is 100 messages, and they must be less than 14 days old
        chunk_size = 100
        for i in range(0, len(message_ids), chunk_size):
            chunk = message_ids[i:i + chunk_size]
            try:
                if len(chunk) == 1:
                    # Single message deletion
                    message = await channel.fetch_message(chunk[0])
                    await message.delete()
                else:
                    # Bulk deletion
                    await channel.delete_messages([discord.Object(id=msg_id) for msg_id in chunk])
                
                # Rate limit protection
                await asyncio.sleep(1)
                
            except discord.NotFound:
                # Message already deleted
                continue
            except discord.Forbidden:
                # No permission to delete
                break
            except discord.HTTPException as e:
                if e.status == 429:  # Rate limited
                    await asyncio.sleep(e.retry_after or 5)
                    continue
                else:
                    break
            except Exception:
                continue
    
    async def _post_schedule(self, guild: discord.Guild, streamer_config: StreamerConfig, 
                           all_segments: List[dict], dry_run: bool = False) -> bool:
        """Post schedule for a single streamer"""
        try:
            schedule_channel = guild.get_channel(streamer_config.schedule_channel_id)
            if not schedule_channel:
                await self._log_error(guild, f"Schedule channel not found", streamer_config.twitch_username)
                return False
            
            # Get current and future streams
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            future_segments = [
                seg for seg in all_segments
                if dateutil.parser.isoparse(seg["start_time"].replace("Z", "+00:00")) >= now_utc - timedelta(minutes=5)
            ]
            future_segments.sort(key=lambda x: dateutil.parser.isoparse(x["start_time"]))
            
            if not dry_run:
                # Delete previous bot messages
                bot_message_ids = []
                async for message in schedule_channel.history(limit=100):
                    if message.author == self.bot.user:
                        bot_message_ids.append(message.id)
                
                if bot_message_ids:
                    # Send warning
                    notify_role = guild.get_role(streamer_config.notify_role_id) if streamer_config.notify_role_id else None
                    warning_content = f"Updating {streamer_config.twitch_username}'s schedule - Previous messages will be deleted in 10 seconds..."
                    if notify_role:
                        warning_content = f"{notify_role.mention}\n{warning_content}"
                    
                    warning_msg = await schedule_channel.send(warning_content)
                    await asyncio.sleep(10)
                    
                    # Delete warning and previous messages
                    await warning_msg.delete()
                    await self._bulk_delete_messages(schedule_channel, bot_message_ids)
            
            async with schedule_channel.typing():
                first_message = None
                
                # Generate and post schedule images for each week
                today_london = datetime.datetime.now(london_tz)
                days_since_sunday = today_london.weekday() + 1
                if days_since_sunday == 7:
                    days_since_sunday = 0
                start_of_first_week = today_london - timedelta(days=days_since_sunday)
                start_of_first_week = start_of_first_week.replace(hour=0, minute=0, second=0, microsecond=0)
                
                for week_num in range(streamer_config.weeks_to_show):
                    week_start = start_of_first_week + timedelta(days=week_num * 7)
                    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
                    
                    # Filter streams for this week
                    week_streams = [
                        s for s in future_segments
                        if week_start <= dateutil.parser.isoparse(s["start_time"]).astimezone(london_tz) <= week_end
                    ]
                    
                    if week_streams:
                        # Generate image for this week
                        image_streams = week_streams[:streamer_config.event_count]
                        image_buf = await self._generate_schedule_image(image_streams, guild, streamer_config, week_start)
                        
                        if image_buf:
                            embed = discord.Embed(
                                title=f"{streamer_config.twitch_username}'s Schedule - Week {week_num + 1}",
                                color=discord.Color.purple()
                            )
                            if dry_run:
                                embed.set_author(name="DRY RUN PREVIEW")
                                embed.color = discord.Color.dark_grey()
                            
                            message = await schedule_channel.send(
                                embed=embed,
                                file=discord.File(image_buf, filename=f"{streamer_config.twitch_username}_schedule_week_{week_num + 1}.png")
                            )
                            if first_message is None:
                                first_message = message
                    
                    # Post individual stream embeds
                    for i, stream in enumerate(week_streams):
                        start_time = datetime.datetime.fromisoformat(stream["start_time"].replace("Z", "+00:00"))
                        title = stream.get("title", "Untitled Stream")
                        category = stream.get("category", {})
                        game_name = category.get("name", "No Category")
                        
                        # Get category artwork
                        boxart_url = None
                        if category and category.get("id"):
                            try:
                                cat_info = await self._get_category_info(category["id"])
                                if cat_info and cat_info.get("box_art_url"):
                                    boxart_url = cat_info["box_art_url"].replace("{width}", "285").replace("{height}", "380")
                            except Exception:
                                pass
                        
                        # Format timestamps
                        unix_ts = int(start_time.timestamp())
                        time_str_relative = f"<t:{unix_ts}:R>"
                        time_str_full = f"<t:{unix_ts}:F>"
                        
                        # Calculate duration
                        duration_str = "Unknown"
                        end_time = stream.get("end_time")
                        if end_time:
                            end_dt = datetime.datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                            duration = end_dt - start_time
                            hours, remainder = divmod(duration.seconds, 3600)
                            minutes = remainder // 60
                            duration_str = f"{hours}h {minutes}m"
                        
                        twitch_url = f"https://twitch.tv/{streamer_config.twitch_username}"
                        
                        # Determine if this is the next upcoming stream
                        is_next_stream = i == 0 and week_num == 0 and future_segments
                        
                        if is_next_stream:
                            embed = discord.Embed(
                                title=f"📣 NEXT UP: {title}",
                                url=twitch_url,
                                description=f"**[Watch Live Here!]({twitch_url})**\n\nStarting {time_str_relative} on {time_str_full}",
                                color=discord.Color.green(),
                                timestamp=start_time
                            )
                            embed.add_field(name="🎮 Game", value=game_name, inline=True)
                            embed.add_field(name="⏳ Expected Duration", value=duration_str, inline=True)
                        else:
                            embed = discord.Embed(
                                title=title,
                                url=twitch_url,
                                description=f"**[Watch Live Here]({twitch_url})**",
                                color=discord.Color.purple(),
                                timestamp=start_time
                            )
                            embed.add_field(name="🕒 Start Time", value=time_str_full, inline=True)
                            embed.add_field(name="⏳ Duration", value=duration_str, inline=True)
                            embed.add_field(name="🎮 Game", value=game_name, inline=True)
                        
                        embed.set_footer(text=f"Twitch Stream • {streamer_config.twitch_username}")
                        
                        if boxart_url:
                            embed.set_thumbnail(url=boxart_url)
                        
                        if dry_run:
                            embed.set_author(name="DRY RUN PREVIEW")
                            embed.color = discord.Color.dark_grey()
                        
                        await schedule_channel.send(embed=embed)
                        await asyncio.sleep(0.5)  # Rate limiting
                
                # Handle no streams case
                if not future_segments:
                    week_text = "week" if streamer_config.weeks_to_show == 1 else f"{streamer_config.weeks_to_show} weeks"
                    embed = discord.Embed(
                        title=f"No Upcoming Streams - {streamer_config.twitch_username}",
                        description=f"There are currently no streams scheduled on Twitch for the next {week_text}.",
                        color=discord.Color.orange()
                    )
                    if dry_run:
                        embed.set_author(name="DRY RUN PREVIEW")
                        embed.color = discord.Color.dark_grey()
                    
                    message = await schedule_channel.send(embed=embed)
                    if first_message is None:
                        first_message = message
                
                # Pin first message
                if not dry_run and first_message:
                    try:
                        await first_message.pin()
                    except discord.Forbidden:
                        await self._log_error(guild, f"Missing permissions to pin messages in {schedule_channel.name}", streamer_config.twitch_username)
                    except Exception as e:
                        await self._log_error(guild, f"Error pinning message: {e}", streamer_config.twitch_username)
                
                # Send notification if configured
                if not dry_run and streamer_config.notification_channel_id and future_segments:
                    notification_channel = guild.get_channel(streamer_config.notification_channel_id)
                    if notification_channel:
                        try:
                            next_stream = future_segments[0]
                            start_time = datetime.datetime.fromisoformat(next_stream["start_time"].replace("Z", "+00:00"))
                            unix_ts = int(start_time.timestamp())
                            
                            notify_role = guild.get_role(streamer_config.notify_role_id) if streamer_config.notify_role_id else None
                            mention_text = notify_role.mention if notify_role else ""
                            
                            notification_embed = discord.Embed(
                                title=f"📅 Schedule Updated: {streamer_config.twitch_username}",
                                description=f"**Next stream:** {next_stream.get('title', 'Untitled Stream')}\n**Starting:** <t:{unix_ts}:R>",
                                color=discord.Color.blue(),
                                url=f"https://twitch.tv/{streamer_config.twitch_username}"
                            )
                            
                            content = f"{mention_text}\n" if mention_text else ""
                            await notification_channel.send(content=content, embed=notification_embed)
                        except Exception as e:
                            await self._log_error(guild, f"Failed to send notification: {e}", streamer_config.twitch_username)
            
            return True
            
        except Exception as e:
            await self._log_error(guild, f"Error in _post_schedule: {e}\n{traceback.format_exc()}", streamer_config.twitch_username)
            return False
    
    async def _schedule_update_loop(self):
        """Main background loop for automatic schedule updates"""
        await self.bot.wait_until_ready()
        
        while True:
            try:
                for guild in self.bot.guilds:
                    guild_config = await self.config.guild(guild).all()
                    
                    if not guild_config.get("global_enabled", True):
                        continue
                    
                    streamers_data = guild_config.get("streamers", {})
                    
                    for username, streamer_data in streamers_data.items():
                        try:
                            streamer_config = StreamerConfig(streamer_data)
                            
                            if not streamer_config.is_configured:
                                continue
                            
                            # Check if it's time to update this streamer
                            now = datetime.datetime.now(london_tz)
                            current_day = now.weekday()
                            current_time = now.strftime("%H:%M")
                            
                            if (current_day in streamer_config.update_days and 
                                current_time == streamer_config.update_time):
                                
                                # Fetch schedule
                                today_utc = datetime.datetime.now(datetime.timezone.utc)
                                end_of_range = today_utc + timedelta(days=max(14, streamer_config.weeks_to_show * 7 + 7))
                                
                                all_segments = await self._get_schedule_for_range(
                                    username, 
                                    today_utc.astimezone(london_tz), 
                                    end_of_range.astimezone(london_tz)
                                )
                                
                                if all_segments is not None:
                                    await self._post_schedule(guild, streamer_config, all_segments)
                                else:
                                    await self._log_error(guild, f"Failed to fetch schedule during automatic update", username)
                        
                        except Exception as e:
                            await self._log_error(guild, f"Error updating {username}: {e}\n{traceback.format_exc()}", username)
                
                await asyncio.sleep(60)  # Check every minute
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                # Log critical loop errors but don't crash
                print(f"Critical error in schedule update loop: {e}")
                await asyncio.sleep(60)
    
    # Commands
    @commands.group(aliases=["tsched", "ts"])
    @commands.admin_or_permissions(manage_guild=True)
    async def twitchschedule(self, ctx):
        """Multi-streamer Twitch Schedule Management"""
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(
                title="Multi-Streamer Twitch Schedule Commands",
                color=discord.Color.purple(),
                description=(
                    f"**Streamer Management:**\n"
                    f"`{ctx.clean_prefix}ts add <username>` - Add a new streamer\n"
                    f"`{ctx.clean_prefix}ts setup <username>` - Configure a streamer\n"
                    f"`{ctx.clean_prefix}ts remove <username>` - Remove a streamer\n"
                    f"`{ctx.clean_prefix}ts list` - List all configured streamers\n"
                    f"`{ctx.clean_prefix}ts enable/disable <username>` - Toggle streamer\n\n"
                    f"**Schedule Operations:**\n"
                    f"`{ctx.clean_prefix}ts force <username>` - Force update for streamer\n"
                    f"`{ctx.clean_prefix}ts test <username> [#channel]` - Test post schedule\n"
                    f"`{ctx.clean_prefix}ts dryrun <username> [#channel]` - Preview without posting\n\n"
                    f"**Configuration:**\n"
                    f"`{ctx.clean_prefix}ts settings [username]` - Show settings\n"
                    f"`{ctx.clean_prefix}ts setlogchannel [#channel]` - Set error log channel\n"
                    f"`{ctx.clean_prefix}ts reload <username>` - Reload streamer resources\n"
                )
            )
            await ctx.send(embed=embed)
    
    @twitchschedule.command(name="add")
    async def add_streamer(self, ctx, username: str):
        """Add a new streamer (basic setup required afterward)"""
        username = username.lower().strip()
        
        if not re.match(r'^[a-zA-Z0-9_]{4,25}, username):
            await ctx.send("❌ Invalid Twitch username format!")
            return
        
        streamers = await self.config.guild(ctx.guild).streamers()
        
        if username in streamers:
            await ctx.send(f"❌ Streamer `{username}` is already configured!")
            return
        
        # Verify username exists on Twitch
        user_id = await self._get_user_id(username)
        if not user_id:
            await ctx.send(f"❌ Twitch user `{username}` not found! Please check the username.")
            return
        
        # Add basic streamer config
        new_config = StreamerConfig({
            "twitch_username": username,
            "enabled": False  # Disabled until setup is complete
        })
        
        streamers[username] = new_config.to_dict()
        await self.config.guild(ctx.guild).streamers.set(streamers)
        
        await ctx.send(f"✅ Added streamer `{username}`! Now run `{ctx.clean_prefix}ts setup {username}` to configure them.")
    
    @twitchschedule.command(name="remove")
    async def remove_streamer(self, ctx, username: str):
        """Remove a streamer completely"""
        username = username.lower().strip()
        
        streamers = await self.config.guild(ctx.guild).streamers()
        
        if username not in streamers:
            await ctx.send(f"❌ Streamer `{username}` not found!")
            return
        
        # Confirmation
        embed = discord.Embed(
            title="Confirm Removal",
            description=f"Are you sure you want to remove `{username}` and all their configuration?",
            color=discord.Color.red()
        )
        embed.set_footer(text="Type 'yes' to confirm or 'no' to cancel.")
        
        await ctx.send(embed=embed)
        
        try:
            msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ["yes", "no"],
                timeout=30.0
            )
            
            if msg.content.lower() == "yes":
                del streamers[username]
                await self.config.guild(ctx.guild).streamers.set(streamers)
                
                # Clean up cache files
                font_path, template_path = self._get_resource_paths(username)
                for path in [font_path, template_path]:
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except Exception:
                            pass
                
                await ctx.send(f"✅ Removed streamer `{username}` and cleaned up their files.")
            else:
                await ctx.send("❌ Removal cancelled.")
                
        except asyncio.TimeoutError:
            await ctx.send("⌛ Confirmation timed out. Removal cancelled.")
    
    @twitchschedule.command(name="setup")
    async def setup_streamer(self, ctx, username: str):
        """Interactive setup for a streamer"""
        username = username.lower().strip()
        
        streamers = await self.config.guild(ctx.guild).streamers()
        
        if username not in streamers:
            await ctx.send(f"❌ Streamer `{username}` not found! Add them first with `{ctx.clean_prefix}ts add {username}`")
            return
        
        streamer_config = StreamerConfig(streamers[username])
        
        await ctx.send(f"Starting setup for **{username}**... You have 30 seconds to respond to each question.")
        
        # Schedule Channel
        for attempt in range(3):
            await ctx.send(f"**Step 1/6:** Which channel should I post {username}'s schedule in? (Mention the channel)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                if msg.channel_mentions:
                    streamer_config.schedule_channel_id = msg.channel_mentions[0].id
                    break
                else:
                    await ctx.send("❌ Please mention a channel. Try again.")
            except asyncio.TimeoutError:
                await ctx.send("⌛ Setup timed out.")
                return
        else:
            await ctx.send("❌ Too many failed attempts.")
            return
        
        # Notification Channel (Optional)
        await ctx.send(f"**Step 2/6:** Which channel should I send notifications in? (Mention channel or type 'none')")
        try:
            msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                timeout=30.0
            )
            if msg.content.lower() == "none":
                streamer_config.notification_channel_id = None
            elif msg.channel_mentions:
                streamer_config.notification_channel_id = msg.channel_mentions[0].id
            else:
                await ctx.send("Using schedule channel for notifications.")
                streamer_config.notification_channel_id = streamer_config.schedule_channel_id
        except asyncio.TimeoutError:
            await ctx.send("⌛ Timeout - using schedule channel for notifications.")
            streamer_config.notification_channel_id = streamer_config.schedule_channel_id
        
        # Notification Role (Optional)
        await ctx.send(f"**Step 3/6:** Which role should I mention for updates? (Mention role or type 'none')")
        try:
            msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                timeout=30.0
            )
            if msg.content.lower() == "none":
                streamer_config.notify_role_id = None
            elif msg.role_mentions:
                streamer_config.notify_role_id = msg.role_mentions[0].id
            else:
                streamer_config.notify_role_id = None
        except asyncio.TimeoutError:
            streamer_config.notify_role_id = None
        
        # Weeks to show
        for attempt in range(3):
            await ctx.send(f"**Step 4/6:** How many weeks should the schedule show? (1 or 2)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                weeks = int(msg.content.strip())
                if weeks in [1, 2]:
                    streamer_config.weeks_to_show = weeks
                    break
                else:
                    await ctx.send("❌ Please enter 1 or 2.")
            except (ValueError, asyncio.TimeoutError):
                await ctx.send("❌ Invalid input.")
        else:
            await ctx.send("❌ Too many failed attempts.")
            return
        
        # Update Days
        for attempt in range(3):
            await ctx.send(f"**Step 5/6:** Which days should I update? (0=Monday, 1=Tuesday, ..., 6=Sunday. Separate with spaces)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                days = [int(x) for x in msg.content.split()]
                if all(0 <= x <= 6 for x in days):
                    streamer_config.update_days = days
                    break
                else:
                    await ctx.send("❌ All numbers must be 0-6.")
            except (ValueError, asyncio.TimeoutError):
                await ctx.send("❌ Invalid input.")
        else:
            await ctx.send("❌ Too many failed attempts.")
            return
        
        # Update Time
        for attempt in range(3):
            await ctx.send(f"**Step 6/6:** What time should I update? (24-hour format, e.g., 14:00)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                time_input = msg.content.strip()
                if re.match(r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$", time_input):
                    streamer_config.update_time = time_input
                    break
                else:
                    await ctx.send("❌ Use HH:MM format (e.g., 09:30).")
            except asyncio.TimeoutError:
                await ctx.send("❌ Invalid input.")
        else:
            await ctx.send("❌ Too many failed attempts.")
            return
        
        # Enable and save
        streamer_config.enabled = True
        streamers[username] = streamer_config.to_dict()
        await self.config.guild(ctx.guild).streamers.set(streamers)
        
        await ctx.send(f"✅ Setup complete for **{username}**! They are now enabled and will update automatically.")
    
    @twitchschedule.command(name="list")
    async def list_streamers(self, ctx):
        """List all configured streamers"""
        streamers = await self.config.guild(ctx.guild).streamers()
        
        if not streamers:
            await ctx.send(f"No streamers configured yet! Add one with `{ctx.clean_prefix}ts add <username>`")
            return
        
        embed = discord.Embed(
            title="Configured Streamers",
            color=discord.Color.purple()
        )
        
        for username, data in streamers.items():
            config = StreamerConfig(data)
            status = "✅ Enabled" if config.enabled else "❌ Disabled"
            configured = "✅ Complete" if config.is_configured else "⚠️ Incomplete"
            
            schedule_channel = ctx.guild.get_channel(config.schedule_channel_id) if config.schedule_channel_id else None
            channel_text = schedule_channel.mention if schedule_channel else "Not set"
            
            embed.add_field(
                name=f"{username}",
                value=f"Status: {status}\nSetup: {configured}\nChannel: {channel_text}",
                inline=True
            )
        
        await ctx.send(embed=embed)
    
    @twitchschedule.command(name="enable")
    async def enable_streamer(self, ctx, username: str):
        """Enable a streamer"""
        username = username.lower().strip()
        streamers = await self.config.guild(ctx.guild).streamers()
        
        if username not in streamers:
            await ctx.send(f"❌ Streamer `{username}` not found!")
            return
        
        config = StreamerConfig(streamers[username])
        if not config.is_configured:
            await ctx.send(f"❌ Complete setup for `{username}` first!")
            return
        
        config.enabled = True
        streamers[username] = config.to_dict()
        await self.config.guild(ctx.guild).streamers.set(streamers)
        
        await ctx.send(f"✅ Enabled `{username}`!")
    
    @twitchschedule.command(name="disable")
    async def disable_streamer(self, ctx, username: str):
        """Disable a streamer"""
        username = username.lower().strip()
        streamers = await self.config.guild(ctx.guild).streamers()
        
        if username not in streamers:
            await ctx.send(f"❌ Streamer `{username}` not found!")
            return
        
        config = StreamerConfig(streamers[username])
        config.enabled = False
        streamers[username] = config.to_dict()
        await self.config.guild(ctx.guild).streamers.set(streamers)
        
        await ctx.send(f"✅ Disabled `{username}`!")
    
    @twitchschedule.command(name="force")
    async def force_update(self, ctx, username: str):
        """Force immediate schedule update for a streamer"""
        username = username.lower().strip()
        streamers = await self.config.guild(ctx.guild).streamers()
        
        if username not in streamers:
            await ctx.send(f"❌ Streamer `{username}` not found!")
            return
        
        config = StreamerConfig(streamers[username])
        if not config.is_configured:
            await ctx.send(f"❌ Complete setup for `{username}` first!")
            return
        
        async with ctx.typing():
            await ctx.send(f"🔄 Forcing update for {username}...")
            
            today_london = datetime.datetime.now(london_tz)
            end_of_range = today_london + timedelta(days=max(14, config.weeks_to_show * 7 + 7))
            
            all_segments = await self._get_schedule_for_range(username, today_london, end_of_range)
            
            if all_segments is not None:
                success = await self._post_schedule(ctx.guild, config, all_segments)
                if success:
                    await ctx.send(f"✅ Updated schedule for {username}!")
                else:
                    await ctx.send(f"❌ Failed to post schedule for {username}!")
            else:
                await ctx.send(f"❌ Failed to fetch schedule for {username}!")
    
    @twitchschedule.command(name="settings")
    async def show_settings(self, ctx, username: str = None):
        """Show settings for a streamer or global settings"""
        if username:
            username = username.lower().strip()
            streamers = await self.config.guild(ctx.guild).streamers()
            
            if username not in streamers:
                await ctx.send(f"❌ Streamer `{username}` not found!")
                return
            
            config = StreamerConfig(streamers[username])
            
            schedule_channel = ctx.guild.get_channel(config.schedule_channel_id) if config.schedule_channel_id else None
            notification_channel = ctx.guild.get_channel(config.notification_channel_id) if config.notification_channel_id else None
            notify_role = ctx.guild.get_role(config.notify_role_id) if config.notify_role_id else None
            
            days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            update_days_str = ", ".join(days[day] for day in config.update_days) if config.update_days else "None"
            
            embed = discord.Embed(
                title=f"Settings for {username}",
                color=discord.Color.purple()
            )
            embed.add_field(name="Enabled", value="✅ Yes" if config.enabled else "❌ No", inline=True)
            embed.add_field(name="Schedule Channel", value=schedule_channel.mention if schedule_channel else "Not set", inline=True)
            embed.add_field(name="Notification Channel", value=notification_channel.mention if notification_channel else "Not set", inline=True)
            embed.add_field(name="Notify Role", value=notify_role.mention if notify_role else "Not set", inline=True)
            embed.add_field(name="Weeks to Show", value=str(config.weeks_to_show), inline=True)
            embed.add_field(name="Event Count", value=str(config.event_count), inline=True)
            embed.add_field(name="Update Days", value=update_days_str, inline=False)
            embed.add_field(name="Update Time (UK)", value=config.update_time or "Not set", inline=True)
            embed.add_field(name="Custom Template", value="Yes" if config.custom_template_url else "No", inline=True)
            embed.add_field(name="Custom Font", value="Yes" if config.custom_font_url else "No", inline=True)
            
            await ctx.send(embed=embed)
        else:
            # Global settings
            guild_config = await self.config.guild(ctx.guild).all()
            log_channel = ctx.guild.get_channel(guild_config.get("log_channel_id")) if guild_config.get("log_channel_id") else None
            
            embed = discord.Embed(
                title="Global Settings",
                color=discord.Color.blue()
            )
            embed.add_field(name="Global Enabled", value="✅ Yes" if guild_config.get("global_enabled", True) else "❌ No", inline=True)
            embed.add_field(name="Log Channel", value=log_channel.mention if log_channel else "Not set", inline=True)
            embed.add_field(name="Total Streamers", value=str(len(guild_config.get("streamers", {}))), inline=True)
            
            await ctx.send(embed=embed)
    
    @twitchschedule.command(name="setlogchannel")
    async def set_log_channel(self, ctx, channel: discord.TextChannel = None):
        """Set global error log channel"""
        if channel:
            await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
            await ctx.send(f"✅ Error logs will be sent to {channel.mention}")
        else:
            await self.config.guild(ctx.guild).log_channel_id.set(None)
            await ctx.send("✅ Error logging disabled")
    
    @twitchschedule.command(name="status")
    async def status(self, ctx):
        """Show overall system status"""
        guild_config = await self.config.guild(ctx.guild).all()
        streamers = guild_config.get("streamers", {})
        
        total_streamers = len(streamers)
        enabled_streamers = sum(1 for s in streamers.values() if StreamerConfig(s).enabled)
        configured_streamers = sum(1 for s in streamers.values() if StreamerConfig(s).is_configured)
        
        embed = discord.Embed(
            title="Twitch Schedule System Status",
            color=discord.Color.green() if guild_config.get("global_enabled", True) else discord.Color.red()
        )
        
        embed.add_field(name="Global Status", 
                       value="🟢 Enabled" if guild_config.get("global_enabled", True) else "🔴 Disabled", 
                       inline=True)
        embed.add_field(name="Total Streamers", value=str(total_streamers), inline=True)
        embed.add_field(name="Enabled", value=str(enabled_streamers), inline=True)
        embed.add_field(name="Fully Configured", value=str(configured_streamers), inline=True)
        
        # Background task status
        schedule_status = "🟢 Running" if (self.schedule_task and not self.schedule_task.done()) else "🔴 Stopped"
        cleanup_status = "🟢 Running" if (self.cleanup_task and not self.cleanup_task.done()) else "🔴 Stopped"
        
        embed.add_field(name="Schedule Task", value=schedule_status, inline=True)
        embed.add_field(name="Cleanup Task", value=cleanup_status, inline=True)
        
        log_channel = ctx.guild.get_channel(guild_config.get("log_channel_id")) if guild_config.get("log_channel_id") else None
        embed.add_field(name="Error Logging", 
                       value=log_channel.mention if log_channel else "Not configured", 
                       inline=True)
        
        # Token status
        token_valid = await self.token_manager.get_valid_token() is not None
        embed.add_field(name="Twitch API", 
                       value="🟢 Connected" if token_valid else "🔴 No valid token", 
                       inline=True)
        
        await ctx.send(embed=embed)