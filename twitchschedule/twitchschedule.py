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
from typing import Optional, List, Dict, Any

london_tz = pytz.timezone("Europe/London")

class TwitchAPIError(Exception):
    """Custom exception for Twitch API errors"""
    pass

class RateLimiter:
    """Simple rate limiter for API calls"""
    def __init__(self, calls_per_minute: int = 800):
        self.calls_per_minute = calls_per_minute
        self.calls = []
    
    async def wait_if_needed(self):
        now = datetime.datetime.now()
        self.calls = [call_time for call_time in self.calls if now - call_time < timedelta(minutes=1)]
        
        if len(self.calls) >= self.calls_per_minute:
            sleep_time = 60 - (now - self.calls[0]).total_seconds()
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)
        
        self.calls.append(now)

class TwitchSchedule(commands.Cog):
    """Sync Twitch streaming schedule to Discord"""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "channel_id": None,
            "twitch_username": None,
            "update_days": [],
            "update_time": None,
            "schedule_message_id": None,
            "notify_role_id": None,
            "event_count": 5,
            "timezone": None,
            "custom_template_url": None,
            "custom_font_url": None,
            "log_channel_id": None,
            "include_next_week": False,
            "weeks_to_show": 1,
            "story_enabled": False,
            "story_channel_id": None,
            "story_template_url": None,
            "story_event_count": 7
        }
        self.config.register_guild(**default_guild)
        
        self.task = None
        self.access_token = None
        self.token_expires_at = None
        self.rate_limiter = RateLimiter()
        self._update_lock = asyncio.Lock()
        
        self.cache_dir = os.path.join(os.path.dirname(__file__), "cache")
        self.font_path = os.path.join(self.cache_dir, "P22.ttf")
        self.template_path = os.path.join(self.cache_dir, "schedule.png")
        self.story_template_path = os.path.join(self.cache_dir, "story_schedule.png")
        
        self.max_file_size = 10 * 1024 * 1024  # 10MB limit
        
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir, exist_ok=True)
        
        self.bot.loop.create_task(self._start_when_ready())

    async def _start_when_ready(self):
        """Start the main task after bot is ready"""
        await self.bot.wait_until_ready()
        self.task = self.bot.loop.create_task(self.schedule_update_loop())

    def cog_unload(self):
        if self.task:
            self.task.cancel()

    async def get_credentials(self) -> Optional[tuple]:
        """Get Twitch API credentials with validation"""
        try:
            tokens = await self.bot.get_shared_api_tokens("twitch")
            client_id = tokens.get("client_id")
            client_secret = tokens.get("client_secret")
            
            if not client_id or not client_secret:
                return None
                
            return client_id, client_secret
        except Exception:
            return None

    async def get_twitch_token(self) -> Optional[str]:
        """Get or refresh Twitch access token with proper error handling"""
        try:
            if self.access_token and self.token_expires_at:
                if datetime.datetime.now() < self.token_expires_at - timedelta(minutes=5):
                    return self.access_token
            
            credentials = await self.get_credentials()
            if not credentials:
                raise TwitchAPIError("No valid Twitch API credentials found")
            
            client_id, client_secret = credentials
            
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                url = "https://id.twitch.tv/oauth2/token"
                params = {
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "client_credentials"
                }
                
                await self.rate_limiter.wait_if_needed()
                async with session.post(url, params=params) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        raise TwitchAPIError(f"Failed to get access token: {resp.status} - {error_text}")
                    
                    data = await resp.json()
                    self.access_token = data.get("access_token")
                    expires_in = data.get("expires_in", 3600)
                    self.token_expires_at = datetime.datetime.now() + timedelta(seconds=expires_in)
                    
                    return self.access_token
                    
        except Exception as e:
            self.access_token = None
            self.token_expires_at = None
            raise TwitchAPIError(f"Token acquisition failed: {str(e)}")

    async def download_file(self, url: str, save_path: str) -> bool:
        """Download file with proper validation and size limits"""
        try:
            if not (url.startswith("http://") or url.startswith("https://")):
                return False
            
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        return False
                    
                    content_length = resp.headers.get('content-length')
                    if content_length and int(content_length) > self.max_file_size:
                        return False
                    
                    data = b""
                    async for chunk in resp.content.iter_chunked(8192):
                        data += chunk
                        if len(data) > self.max_file_size:
                            return False
                    
                    # Basic file validation
                    if save_path.endswith('.png') and not data.startswith(b'\x89PNG'):
                        return False
                    
                    os.makedirs(os.path.dirname(save_path), exist_ok=True)
                    with open(save_path, 'wb') as f:
                        f.write(data)
                    
                    return True
                    
        except Exception:
            if os.path.exists(save_path):
                try:
                    os.remove(save_path)
                except:
                    pass
            return False

    async def ensure_resources(self, guild: discord.Guild) -> bool:
        """Ensure font and template files are available"""
        try:
            custom_font_url = await self.config.guild(guild).custom_font_url()
            font_url_to_use = custom_font_url if custom_font_url else "https://zerolivesleft.net/notelkz/P22.ttf"
            
            custom_template_url = await self.config.guild(guild).custom_template_url()
            template_url_to_use = custom_template_url if custom_template_url else "https://zerolivesleft.net/notelkz/schedule.png"
            
            font_ok = True
            template_ok = True

            if not os.path.exists(self.font_path):
                font_ok = await self.download_file(font_url_to_use, self.font_path)
                if not font_ok:
                    await self._log_error(guild, f"Failed to download font file from {font_url_to_use}")

            if not os.path.exists(self.template_path):
                template_ok = await self.download_file(template_url_to_use, self.template_path)
                if not template_ok:
                    await self._log_error(guild, f"Failed to download template from {template_url_to_use}")

            # Story template (only if story feature is enabled)
            story_enabled = await self.config.guild(guild).story_enabled()
            story_template_ok = True
            if story_enabled:
                story_template_url = await self.config.guild(guild).story_template_url()
                story_url_to_use = story_template_url if story_template_url else "https://zerolivesleft.net/notelkz/story_schedule.png"
                
                if not os.path.exists(self.story_template_path):
                    story_template_ok = await self.download_file(story_url_to_use, self.story_template_path)
                    if not story_template_ok:
                        await self._log_error(guild, f"Failed to download story template from {story_url_to_use}")

            return font_ok and template_ok and (story_template_ok or not story_enabled)
            
        except Exception as e:
            await self._log_error(guild, f"Error ensuring resources: {str(e)}")
            return False

    async def _make_twitch_request(self, url: str, headers: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Make Twitch API request with proper error handling"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                await self.rate_limiter.wait_if_needed()
                
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                    async with session.get(url, headers=headers) as resp:
                        if resp.status == 401:
                            if attempt == 0:
                                self.access_token = None
                                self.token_expires_at = None
                                new_token = await self.get_twitch_token()
                                if new_token:
                                    headers["Authorization"] = f"Bearer {new_token}"
                                    continue
                            raise TwitchAPIError("Authentication failed")
                        
                        elif resp.status == 429:
                            retry_after = int(resp.headers.get('Retry-After', 60))
                            await asyncio.sleep(min(retry_after, 300))
                            continue
                        
                        elif resp.status == 404:
                            return {"data": {"segments": []}}
                        
                        elif resp.status != 200:
                            error_text = await resp.text()
                            if attempt == max_retries - 1:
                                raise TwitchAPIError(f"API request failed: {resp.status} - {error_text}")
                            await asyncio.sleep(2 ** attempt)
                            continue
                        
                        return await resp.json()
                        
            except asyncio.TimeoutError:
                if attempt == max_retries - 1:
                    raise TwitchAPIError("Request timed out")
                await asyncio.sleep(2 ** attempt)
            except Exception as e:
                if attempt == max_retries - 1:
                    raise TwitchAPIError(f"Request failed: {str(e)}")
                await asyncio.sleep(2 ** attempt)
        
        return None

    async def get_schedule_for_range(self, username: str, start_date: datetime.datetime, end_date: datetime.datetime) -> Optional[List[Dict]]:
        """Get schedule with improved error handling"""
        try:
            if not username or len(username.strip()) == 0:
                raise TwitchAPIError("Invalid username provided")
            
            username = username.strip().lower()
            
            token = await self.get_twitch_token()
            if not token:
                raise TwitchAPIError("Failed to obtain access token")
            
            credentials = await self.get_credentials()
            if not credentials:
                raise TwitchAPIError("No valid credentials")
            
            client_id, _ = credentials
            headers = {
                "Client-ID": client_id,
                "Authorization": f"Bearer {token}"
            }
            
            # Get user ID first
            user_url = f"https://api.twitch.tv/helix/users?login={username}"
            user_data = await self._make_twitch_request(user_url, headers)
            
            if not user_data or not user_data.get("data"):
                raise TwitchAPIError(f"User '{username}' not found")
            
            broadcaster_id = user_data["data"][0]["id"]
            broadcaster_name = user_data["data"][0]["login"]
            
            # Get schedule
            schedule_url = f"https://api.twitch.tv/helix/schedule?broadcaster_id={broadcaster_id}"
            schedule_data = await self._make_twitch_request(schedule_url, headers)
            
            if not schedule_data:
                return []
            
            segments = schedule_data.get("data", {}).get("segments", [])
            
            filtered_segments = []
            for seg in segments:
                try:
                    start_time = dateutil.parser.isoparse(seg["start_time"])
                    if start_time.tzinfo is None:
                        start_time = start_time.replace(tzinfo=datetime.timezone.utc)
                    start_time_local = start_time.astimezone(london_tz)
                    
                    if start_date <= start_time_local <= end_date:
                        seg["broadcaster_name"] = broadcaster_name
                        filtered_segments.append(seg)
                except (ValueError, KeyError):
                    continue
                        
            return filtered_segments
            
        except TwitchAPIError:
            raise
        except Exception as e:
            raise TwitchAPIError(f"Unexpected error getting schedule: {str(e)}")

    async def get_category_info(self, category_id: str) -> Optional[Dict[str, Any]]:
        """Get category info with error handling"""
        try:
            if not category_id:
                return None
                
            token = await self.get_twitch_token()
            if not token:
                return None
            
            credentials = await self.get_credentials()
            if not credentials:
                return None
            
            client_id, _ = credentials
            headers = {
                "Client-ID": client_id,
                "Authorization": f"Bearer {token}"
            }
            
            url = f"https://api.twitch.tv/helix/games?id={category_id}"
            data = await self._make_twitch_request(url, headers)
            
            if data and data.get("data"):
                return data["data"][0]
            return None
            
        except Exception:
            return None

    async def get_vods_for_user(self, username: str, start_time: datetime.datetime, end_time: datetime.datetime) -> Optional[List[Dict]]:
        """Get VODs with improved matching logic"""
        try:
            token = await self.get_twitch_token()
            if not token:
                return None
            
            credentials = await self.get_credentials()
            if not credentials:
                return None
            
            client_id, _ = credentials
            headers = {
                "Client-ID": client_id,
                "Authorization": f"Bearer {token}"
            }
            
            user_url = f"https://api.twitch.tv/helix/users?login={username}"
            user_data = await self._make_twitch_request(user_url, headers)
            
            if not user_data or not user_data.get("data"):
                return None
            
            broadcaster_id = user_data["data"][0]["id"]
            
            vods_url = f"https://api.twitch.tv/helix/videos?user_id={broadcaster_id}&type=archive&first=5&period=month"
            vod_data = await self._make_twitch_request(vods_url, headers)
            
            if not vod_data:
                return None
            
            vods = []
            for vod in vod_data.get("data", []):
                try:
                    vod_created_at = dateutil.parser.isoparse(vod["created_at"])
                    if abs((vod_created_at - start_time.astimezone(datetime.timezone.utc)).total_seconds()) <= 3600:
                        vods.append(vod)
                except (ValueError, KeyError):
                    continue
            
            return vods
            
        except Exception:
            return None

    async def generate_schedule_image(self, schedule_for_image: list, guild: discord.Guild, start_date=None) -> Optional[io.BytesIO]:
        """Generate schedule image with better error handling"""
        try:
            if not await self.ensure_resources(guild):
                return None
            
            if not isinstance(schedule_for_image, list):
                return None
            
            with Image.open(self.template_path) as template_img:
                img = template_img.copy()
            
            event_count = await self.config.guild(guild).event_count()
            actual_events = min(len(schedule_for_image), event_count)
            
            # Adjust image height if fewer events
            if actual_events < event_count:
                width, height = img.size
                row_height = 150
                height_to_remove = (event_count - actual_events) * row_height
                new_height = max(height - height_to_remove, 400)
                new_img = Image.new(img.mode, (width, new_height), color=(0, 0, 0))
                
                header_height = min(350, height)
                new_img.paste(img.crop((0, 0, width, header_height)), (0, 0))
                
                if actual_events > 0:
                    event_section_height = actual_events * row_height
                    event_section_end = min(header_height + event_section_height, height)
                    if event_section_end > header_height:
                        new_img.paste(
                            img.crop((0, header_height, width, event_section_end)), 
                            (0, header_height)
                        )
                
                img = new_img
            
            draw = ImageDraw.Draw(img)
            
            # Load fonts with fallback
            try:
                title_font = ImageFont.truetype(self.font_path, 90)
                date_font = ImageFont.truetype(self.font_path, 40)
                schedule_font = ImageFont.truetype(self.font_path, 42)
            except Exception:
                title_font = ImageFont.load_default()
                date_font = ImageFont.load_default()
                schedule_font = ImageFont.load_default()
            
            # Calculate date text
            if start_date is None:
                today = datetime.datetime.now(london_tz)
                days_since_sunday = today.weekday() + 1
                if days_since_sunday == 7:
                    days_since_sunday = 0
                start_of_week = today - timedelta(days=days_since_sunday)
                start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
            else:
                start_of_week = start_date
            
            weeks_to_show = await self.config.guild(guild).weeks_to_show()
            if weeks_to_show > 1:
                end_week = start_of_week + timedelta(days=(weeks_to_show * 7) - 1)
                date_text = f"{start_of_week.strftime('%b %d')} - {end_week.strftime('%b %d')}"
            else:
                date_text = start_of_week.strftime("%B %d")
            
            width, _ = img.size
            right_margin = 100
            
            week_of_text = "Week of" if weeks_to_show == 1 else "Weeks of"
            
            # Draw header text
            try:
                week_of_bbox = title_font.getbbox(week_of_text)
                date_bbox = date_font.getbbox(date_text)
                week_of_width = week_of_bbox[2] - week_of_bbox[0]
                date_width = date_bbox[2] - date_bbox[0]
                
                week_of_x = max(0, width - right_margin - week_of_width)
                date_x = max(0, width - right_margin - date_width)
                
                draw.text((week_of_x, 100), week_of_text, font=title_font, fill=(255, 255, 255))
                draw.text((date_x, 180), date_text, font=date_font, fill=(255, 255, 255))
            except Exception:
                pass
            
            # Draw schedule items
            day_x = 125
            game_x = 125
            initial_y = 350
            row_height = 150
            day_offset = -45
            
            for i, segment in enumerate(schedule_for_image):
                if i >= actual_events:
                    break
                
                try:
                    bar_y = initial_y + (i * row_height)
                    day_y = bar_y + day_offset
                    game_y = bar_y + 15
                    
                    start_time_utc = dateutil.parser.isoparse(segment["start_time"])
                    if start_time_utc.tzinfo is None:
                        start_time_utc = start_time_utc.replace(tzinfo=datetime.timezone.utc)
                    start_time_london = start_time_utc.astimezone(london_tz)
                    
                    day_time = start_time_london.strftime("%A // %I:%M%p").upper()
                    
                    # Use game name if no title is provided
                    title = segment.get("title", "").strip()
                    if not title:
                        category = segment.get("category", {})
                        title = category.get("name", "Untitled Stream")
                    
                    title = str(title)[:50]
                    
                    draw.text((day_x, day_y), day_time, font=schedule_font, fill=(255, 255, 255))
                    draw.text((game_x, game_y), title, font=schedule_font, fill=(255, 255, 255))
                    
                except Exception:
                    continue
            
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
            buf.seek(0)
            return buf
            
        except Exception as e:
            await self._log_error(guild, f"Error generating schedule image: {str(e)}")
            return None

    async def schedule_update_loop(self):
        """Main update loop with better error handling"""
        await self.bot.wait_until_ready()
        
        while True:
            try:
                async with self._update_lock:
                    for guild in self.bot.guilds:
                        try:
                            await self._process_guild_update(guild)
                        except Exception as e:
                            await self._log_error(guild, f"Error processing guild {guild.id}: {str(e)}")
                
                await asyncio.sleep(60)
                
            except asyncio.CancelledError:
                break
            except Exception:
                await asyncio.sleep(60)

    async def _process_guild_update(self, guild: discord.Guild):
        """Process update for a single guild"""
        config = self.config.guild(guild)
        channel_id = await config.channel_id()
        twitch_username = await config.twitch_username()
        update_days = await config.update_days()
        update_time = await config.update_time()

        if not all([channel_id, twitch_username, update_days, update_time]):
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            await self._log_error(guild, f"Channel {channel_id} not found")
            return

        permissions = channel.permissions_for(guild.me)
        if not all([permissions.send_messages, permissions.read_messages, permissions.embed_links]):
            await self._log_error(guild, f"Missing permissions in {channel.name}")
            return

        now = datetime.datetime.now(london_tz)
        current_day = now.weekday()
        current_time = now.strftime("%H:%M")

        if current_day in update_days and current_time == update_time:
            try:
                weeks_to_show = await config.weeks_to_show()
                today_utc = datetime.datetime.now(datetime.timezone.utc)
                end_of_range = today_utc + timedelta(days=max(14, weeks_to_show * 7 + 7))
                
                all_upcoming_segments = await self.get_schedule_for_range(
                    twitch_username, today_utc.astimezone(london_tz), end_of_range.astimezone(london_tz)
                )

                if all_upcoming_segments is not None:
                    await self.post_schedule(channel, all_upcoming_segments)
                else:
                    await self._log_error(guild, f"Failed to fetch schedule for {twitch_username}")
                    
            except Exception as e:
                await self._log_error(guild, f"Error in automated update: {str(e)}")

    async def _log_error(self, guild: discord.Guild, error_message: str):
        """Enhanced error logging"""
        try:
            log_channel_id = await self.config.guild(guild).log_channel_id()
            if log_channel_id:
                log_channel = guild.get_channel(log_channel_id)
                if log_channel and log_channel.permissions_for(guild.me).send_messages:
                    
                    if len(error_message) > 1900:
                        error_message = error_message[:1900] + "... (truncated)"
                    
                    embed = discord.Embed(
                        title="Twitch Schedule Error",
                        description=f"```py\n{error_message}\n```",
                        color=discord.Color.red(),
                        timestamp=datetime.datetime.now(datetime.timezone.utc)
                    )
                    embed.set_footer(text=f"Guild: {guild.name}")
                    
                    await log_channel.send(embed=embed)
        except Exception:
            pass

    async def post_schedule(self, channel: discord.TextChannel, all_segments: list, dry_run: bool = False, start_date_for_image=None):
        """Post schedule with improved error handling"""
        try:
            if not isinstance(all_segments, list):
                raise ValueError("Invalid segments data")
            
            guild = channel.guild
            config = self.config.guild(guild)
            
            twitch_username = await config.twitch_username()
            event_count = await config.event_count()
            weeks_to_show = await config.weeks_to_show()

            now_utc = datetime.datetime.now(datetime.timezone.utc)
            future_segments = []
            
            for seg in all_segments:
                try:
                    start_time_utc = dateutil.parser.isoparse(seg["start_time"].replace("Z", "+00:00"))
                    if start_time_utc >= now_utc - timedelta(minutes=5):
                        future_segments.append(seg)
                except (ValueError, KeyError, TypeError):
                    continue
            
            future_segments.sort(key=lambda x: dateutil.parser.isoparse(x["start_time"]))

            permissions = channel.permissions_for(guild.me)
            if not dry_run and not permissions.manage_messages:
                await self._log_error(guild, f"Missing manage_messages permission in {channel.name}")
                return

            # Delete previous messages
            if not dry_run:
                notify_role_id = await config.notify_role_id()
                notify_role = guild.get_role(notify_role_id) if notify_role_id else None
                
                warning_content = "⚠️ Updating schedule - Previous messages will be deleted in 10 seconds..."
                if notify_role:
                    warning_content = f"{notify_role.mention}\n{warning_content}"
                
                try:
                    warning_msg = await channel.send(warning_content)
                    await asyncio.sleep(10)
                    await warning_msg.delete()
                except discord.errors.Forbidden:
                    await self._log_error(guild, f"Cannot send/delete messages in {channel.name}")
                    return
                
                # Delete bot messages
                try:
                    async for message in channel.history(limit=50):
                        if message.author == self.bot.user and message.id != warning_msg.id:
                            try:
                                await message.delete()
                                await asyncio.sleep(1.5)
                            except:
                                break
                except Exception as e:
                    await self._log_error(guild, f"Error during message cleanup: {str(e)}")

            # Generate content
            async with channel.typing():
                if start_date_for_image is None:
                    today_london = datetime.datetime.now(london_tz)
                    days_since_sunday = today_london.weekday() + 1
                    if days_since_sunday == 7:
                        days_since_sunday = 0
                    start_of_first_week = today_london - timedelta(days=days_since_sunday)
                    start_of_first_week = start_of_first_week.replace(hour=0, minute=0, second=0, microsecond=0)
                else:
                    start_of_first_week = start_date_for_image

                next_stream_posted = False
                first_message_for_pinning = None

                # Process each week
                for week_num in range(weeks_to_show):
                    week_start = start_of_first_week + timedelta(days=week_num * 7)
                    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
                    
                    week_streams = []
                    for stream in future_segments:
                        try:
                            stream_time = dateutil.parser.isoparse(stream["start_time"]).astimezone(london_tz)
                            if week_start <= stream_time <= week_end:
                                week_streams.append(stream)
                        except (ValueError, KeyError):
                            continue
                    
                    # Generate and post week image
                    if week_streams:
                        if dry_run:
                            await channel.send(f"🧪 Dry run: Generating week {week_num + 1} schedule image...")
                        
                        image_streams = week_streams[:event_count]
                        image_buf = await self.generate_schedule_image(image_streams, guild, start_date=week_start)
                        
                        if image_buf:
                            try:
                                week_message = await channel.send(
                                    file=discord.File(image_buf, filename=f"schedule_week_{week_num + 1}.png")
                                )
                                if first_message_for_pinning is None:
                                    first_message_for_pinning = week_message
                            except discord.errors.HTTPException as e:
                                await self._log_error(guild, f"Failed to send image: {str(e)}")
                        else:
                            embed_fallback = discord.Embed(
                                title=f"Week {week_num + 1} Schedule",
                                description="Image generation failed, but streams are listed below.",
                                color=discord.Color.orange()
                            )
                            if dry_run:
                                embed_fallback.set_author(name="DRY RUN PREVIEW")
                            week_message = await channel.send(embed=embed_fallback)
                            if first_message_for_pinning is None:
                                first_message_for_pinning = week_message
                    else:
                        week_title = f"Week of {week_start.strftime('%B %d')}"
                        embed_no_streams = discord.Embed(
                            title=f"No Streams Scheduled - {week_title}",
                            description="No streams currently scheduled for this week on Twitch.",
                            color=discord.Color.orange()
                        )
                        if dry_run:
                            embed_no_streams.set_author(name="DRY RUN PREVIEW")
                            embed_no_streams.color = discord.Color.dark_grey()
                        
                        week_message = await channel.send(embed=embed_no_streams)
                        if first_message_for_pinning is None:
                            first_message_for_pinning = week_message

                    # Post individual stream embeds
                    for stream in week_streams:
                        try:
                            embed = await self._create_stream_embed(
                                stream, twitch_username, future_segments, 
                                next_stream_posted, dry_run
                            )
                            if embed:
                                await channel.send(embed=embed)
                                if not next_stream_posted and stream == future_segments[0] if future_segments else False:
                                    next_stream_posted = True
                                await asyncio.sleep(0.5)
                        except Exception as e:
                            await self._log_error(guild, f"Error posting stream embed: {str(e)}")
                            continue

                # Handle case with no streams at all
                if not future_segments:
                    week_text = "week" if weeks_to_show == 1 else f"{weeks_to_show} weeks"
                    embed = discord.Embed(
                        title="No Upcoming Streams",
                        description=f"No streams currently scheduled on Twitch for the next {week_text}.",
                        color=discord.Color.red()
                    )
                    if dry_run:
                        embed.set_author(name="DRY RUN PREVIEW")
                        embed.color = discord.Color.dark_grey()
                    await channel.send(embed=embed)

                # Pin the first message
                if not dry_run and first_message_for_pinning and permissions.manage_messages:
                    try:
                        await first_message_for_pinning.pin()
                        await config.schedule_message_id.set(first_message_for_pinning.id)
                    except discord.errors.Forbidden:
                        await self._log_error(guild, f"Cannot pin messages in {channel.name}")
                    except Exception as e:
                        await self._log_error(guild, f"Error pinning message: {str(e)}")

        except Exception as e:
            await self._log_error(guild, f"Error in post_schedule: {str(e)}")
            error_msg = "❌ Error occurred while posting schedule. Check logs for details."
            if dry_run:
                error_msg = "❌ Dry run failed. Check logs for details."
            try:
                await channel.send(error_msg)
            except:
                pass

    async def _create_stream_embed(self, stream: dict, twitch_username: str, 
                                 all_streams: list, next_stream_posted: bool, 
                                 dry_run: bool) -> Optional[discord.Embed]:
        """Create embed for individual stream with validation"""
        try:
            start_time = dateutil.parser.isoparse(stream["start_time"].replace("Z", "+00:00"))
            title = str(stream.get("title", "Untitled Stream"))[:256]
            category = stream.get("category", {})
            game_name = str(category.get("name", "No Category"))[:1024]
            
            # Get category artwork
            boxart_url = None
            if category and category.get("id"):
                try:
                    cat_info = await self.get_category_info(category["id"])
                    if cat_info and cat_info.get("box_art_url"):
                        boxart_url = cat_info["box_art_url"].replace("{width}", "285").replace("{height}", "380")
                except:
                    pass

            unix_ts = int(start_time.timestamp())
            time_str_relative = f"<t:{unix_ts}:R>"
            time_str_full = f"<t:{unix_ts}:F>"
            
            # Calculate duration
            end_time = stream.get("end_time")
            duration_str = "Unknown"
            if end_time:
                try:
                    end_dt = dateutil.parser.isoparse(end_time.replace("Z", "+00:00"))
                    duration = end_dt - start_time
                    hours, remainder = divmod(duration.total_seconds(), 3600)
                    minutes = remainder // 60
                    duration_str = f"{int(hours)}h {int(minutes)}m"
                except:
                    pass

            twitch_url = f"https://twitch.tv/{twitch_username}"
            
            # Determine if this is the next stream
            is_next_stream = (not next_stream_posted and 
                            all_streams and 
                            stream == all_streams[0])
            
            # Create embed
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
            
            embed.set_footer(text=f"Twitch Stream • {twitch_username}")
            
            if boxart_url:
                embed.set_thumbnail(url=boxart_url)

            # Check for VODs if stream has ended
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            if end_time:
                try:
                    end_dt = dateutil.parser.isoparse(end_time.replace("Z", "+00:00"))
                    if end_dt < now_utc:
                        vods = await self.get_vods_for_user(twitch_username, start_time, end_dt)
                        if vods and len(vods) > 0:
                            vod_url = vods[0]["url"]
                            embed.add_field(name="🎥 Watch VOD", value=f"[Click Here]({vod_url})", inline=False)
                except:
                    pass

            if dry_run:
                embed.set_author(name="DRY RUN PREVIEW")
                embed.color = discord.Color.dark_grey()
                
            return embed
            
        except Exception:
            return None

    # Command group and commands
    @commands.group(aliases=["tsched"])
    @commands.admin_or_permissions(manage_guild=True)
    async def twitchschedule(self, ctx):
        """Twitch Schedule Management Commands"""
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(
                title="Twitch Schedule Commands",
                color=discord.Color.purple(),
                description=(
                    f"`{ctx.clean_prefix}tsched setup` - Interactive setup process\n"
                    f"`{ctx.clean_prefix}tsched force [next]` - Force immediate update\n"
                    f"`{ctx.clean_prefix}tsched notify [@role/none]` - Set notification role\n"
                    f"`{ctx.clean_prefix}tsched events [1-10]` - Set number of events to show\n"
                    f"`{ctx.clean_prefix}tsched weeks [1-2]` - Set weeks to display\n"
                    f"`{ctx.clean_prefix}tsched settings` - Show current settings\n"
                    f"`{ctx.clean_prefix}tsched test #channel` - Test post to channel\n"
                    f"`{ctx.clean_prefix}tsched reload [url]` - Redownload resources\n"
                    f"`{ctx.clean_prefix}tsched setfont [url/none]` - Set custom font\n"
                    f"`{ctx.clean_prefix}tsched dryrun [#channel]` - Test without changes\n"
                    f"`{ctx.clean_prefix}tsched setlogchannel [#channel/none]` - Set log channel"
                )
            )
            await ctx.send(embed=embed)

    @twitchschedule.command(name="weeks")
    async def set_weeks(self, ctx, weeks: int):
        """Set the number of weeks to show (1 or 2)"""
        if not 1 <= weeks <= 2:
            await ctx.send("❌ Weeks must be 1 or 2!")
            return
        
        await self.config.guild(ctx.guild).weeks_to_show.set(weeks)
        await self.config.guild(ctx.guild).include_next_week.set(weeks > 1)
        
        week_text = "week" if weeks == 1 else "weeks"
        await ctx.send(f"✅ Schedule will now show {weeks} {week_text}!")

    @twitchschedule.command(name="force")
    async def force_update(self, ctx, option: str = None):
        """Force immediate schedule update"""
        async with self._update_lock:
            config = self.config.guild(ctx.guild)
            channel_id = await config.channel_id()
            twitch_username = await config.twitch_username()
            
            if not channel_id or not twitch_username:
                await ctx.send("❌ Please run setup first!")
                return
                
            channel = ctx.guild.get_channel(channel_id)
            if not channel:
                await ctx.send("❌ Configured channel not found!")
                return
                
            perms = channel.permissions_for(ctx.guild.me)
            if not all([perms.send_messages, perms.embed_links]):
                await ctx.send("❌ Missing required permissions in target channel!")
                return
                
            async with ctx.channel.typing():
                await ctx.send("🔄 Forcing schedule update...")

                try:
                    today_london = datetime.datetime.now(london_tz)
                    start_date_for_image_param = None
                    weeks_to_show = await config.weeks_to_show()
                    
                    if option and option.lower() == "next":
                        days_until_next_sunday = (6 - today_london.weekday() + 7) % 7
                        if days_until_next_sunday == 0:
                            days_until_next_sunday = 7
                        start_date_for_image_param = today_london + timedelta(days=days_until_next_sunday)
                        start_date_for_image_param = start_date_for_image_param.replace(hour=0, minute=0, second=0, microsecond=0)
                        end_of_fetch_range = start_date_for_image_param + timedelta(days=weeks_to_show * 7 + 7)
                    else:
                        end_of_fetch_range = today_london + timedelta(days=max(14, weeks_to_show * 7 + 7))

                    all_segments = await self.get_schedule_for_range(
                        twitch_username, today_london, end_of_fetch_range
                    )

                    if all_segments is not None:
                        await self.post_schedule(channel, all_segments, start_date_for_image=start_date_for_image_param)
                        await ctx.send("✅ Schedule updated!")
                    else:
                        await ctx.send("❌ Failed to fetch schedule from Twitch!")
                        
                except TwitchAPIError as e:
                    await ctx.send(f"❌ Twitch API Error: {str(e)}")
                except Exception as e:
                    await ctx.send("❌ An unexpected error occurred. Check logs.")
                    await self._log_error(ctx.guild, f"Force update error: {str(e)}")

    @twitchschedule.command(name="setup")
    async def setup_schedule(self, ctx):
        """Interactive setup with enhanced validation"""
        await ctx.send("Starting setup process. You have 30 seconds for each response.")
        
        # Channel setup
        channel = None
        for attempt in range(3):
            await ctx.send(f"**Step 1/5:** Which channel for schedule posts? (Mention: #channel)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                
                if not msg.channel_mentions:
                    await ctx.send("❌ Please mention a channel. Try again.")
                    continue
                    
                channel = msg.channel_mentions[0]
                perms = channel.permissions_for(ctx.guild.me)
                missing_perms = []
                if not perms.send_messages:
                    missing_perms.append("Send Messages")
                if not perms.embed_links:
                    missing_perms.append("Embed Links")
                if not perms.attach_files:
                    missing_perms.append("Attach Files")
                if not perms.manage_messages:
                    missing_perms.append("Manage Messages")
                    
                if missing_perms:
                    await ctx.send(f"❌ Missing permissions in {channel.mention}: {', '.join(missing_perms)}")
                    continue
                    
                await self.config.guild(ctx.guild).channel_id.set(channel.id)
                break
                
            except asyncio.TimeoutError:
                await ctx.send("⌛ Setup timed out.")
                return
        else:
            await ctx.send("❌ Too many failed attempts.")
            return
        
        # Username setup
        username = None
        for attempt in range(3):
            await ctx.send("**Step 2/5:** Twitch username to track? (e.g., notelkz)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                
                username = msg.content.strip().lower()
                
                if not username or len(username) < 3 or len(username) > 25:
                    await ctx.send("❌ Username must be 3-25 characters.")
                    continue
                    
                if not re.match(r'^[a-zA-Z0-9_]+, username):
                    await ctx.send("❌ Username can only contain letters, numbers, and underscores.")
                    continue
                
                await self.config.guild(ctx.guild).twitch_username.set(username)
                break
                
            except asyncio.TimeoutError:
                await ctx.send("⌛ Setup timed out.")
                return
        else:
            await ctx.send("❌ Too many failed attempts.")
            return

        # Weeks setup
        weeks = None
        for attempt in range(3):
            await ctx.send("**Step 3/5:** How many weeks to show? (1 or 2)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                try:
                    weeks = int(msg.content.strip())
                    if weeks not in [1, 2]:
                        await ctx.send("❌ Please enter 1 or 2.")
                        continue
                    await self.config.guild(ctx.guild).weeks_to_show.set(weeks)
                    await self.config.guild(ctx.guild).include_next_week.set(weeks > 1)
                    break
                except ValueError:
                    await ctx.send("❌ Please enter a number.")
                    continue
            except asyncio.TimeoutError:
                await ctx.send("⌛ Setup timed out.")
                return
        else:
            await ctx.send("❌ Too many failed attempts.")
            return

        # Update days
        days = []
        for attempt in range(3):
            await ctx.send("**Step 4/5:** Update days? (0=Mon, 1=Tue...6=Sun, space-separated: `0 6`)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                try:
                    days = [int(x) for x in msg.content.split()]
                    if not all(0 <= x <= 6 for x in days):
                        await ctx.send("❌ Numbers must be 0-6.")
                        continue
                    await self.config.guild(ctx.guild).update_days.set(days)
                    break
                except ValueError:
                    await ctx.send("❌ Invalid format. Example: `0 6`")
                    continue
            except asyncio.TimeoutError:
                await ctx.send("⌛ Setup timed out.")
                return
        else:
            await ctx.send("❌ Too many failed attempts.")
            return

        # Update time
        update_time = None
        for attempt in range(3):
            await ctx.send("**Step 5/5:** Update time (UK time, 24h format: `14:30`)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                time_input = msg.content.strip()
                if not re.match(r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$", time_input):
                    await ctx.send("❌ Invalid format. Use HH:MM like `14:30`")
                    continue
                update_time = time_input
                await self.config.guild(ctx.guild).update_time.set(update_time)
                break
            except asyncio.TimeoutError:
                await ctx.send("⌛ Setup timed out.")
                return
        else:
            await ctx.send("❌ Too many failed attempts.")
            return
        
        # Confirmation
        embed = discord.Embed(
            title="Setup Complete!",
            description="Configuration saved successfully.",
            color=discord.Color.green()
        )
        embed.add_field(name="Channel", value=channel.mention, inline=True)
        embed.add_field(name="Username", value=username, inline=True)
        embed.add_field(name="Weeks", value=str(weeks), inline=True)
        
        await ctx.send(embed=embed)

    @twitchschedule.command(name="notify")
    async def set_notify_role(self, ctx, role: discord.Role = None):
        """Set or clear notification role"""
        if role is None:
            await self.config.guild(ctx.guild).notify_role_id.set(None)
            await ctx.send("✅ Notification role cleared!")
        else:
            await self.config.guild(ctx.guild).notify_role_id.set(role.id)
            await ctx.send(f"✅ Notification role set to {role.mention}!")

    @twitchschedule.command(name="events")
    async def set_event_count(self, ctx, count: int):
        """Set number of events to show (1-10)"""
        if not 1 <= count <= 10:
            await ctx.send("❌ Event count must be 1-10!")
            return
        await self.config.guild(ctx.guild).event_count.set(count)
        await ctx.send(f"✅ Event count set to {count}!")

    @twitchschedule.command(name="settings")
    async def show_settings(self, ctx):
        """Show current settings"""
        config = self.config.guild(ctx.guild)
        
        channel_id = await config.channel_id()
        twitch_username = await config.twitch_username()
        update_days = await config.update_days()
        update_time = await config.update_time()
        notify_role_id = await config.notify_role_id()
        event_count = await config.event_count()
        custom_template_url = await config.custom_template_url()
        custom_font_url = await config.custom_font_url()
        log_channel_id = await config.log_channel_id()
        weeks_to_show = await config.weeks_to_show()
        
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        notify_role = ctx.guild.get_role(notify_role_id) if notify_role_id else None
        log_channel = ctx.guild.get_channel(log_channel_id) if log_channel_id else None
        
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        update_days_str = ", ".join(days[day] for day in update_days) if update_days else "None"
        week_text = "week" if weeks_to_show == 1 else "weeks"
        
        embed = discord.Embed(
            title="Twitch Schedule Settings",
            color=discord.Color.purple()
        )
        
        embed.add_field(name="Schedule Channel", value=channel.mention if channel else "Not set", inline=True)
        embed.add_field(name="Twitch Username", value=twitch_username or "Not set", inline=True)
        embed.add_field(name="Weeks to Show", value=f"{weeks_to_show} {week_text}", inline=True)
        embed.add_field(name="Update Time (UK)", value=update_time or "Not set", inline=True)
        embed.add_field(name="Update Days (UK)", value=update_days_str, inline=True)
        embed.add_field(name="Notify Role", value=notify_role.mention if notify_role else "Not set", inline=True)
        embed.add_field(name="Event Count", value=str(event_count), inline=True)
        embed.add_field(name="Error Log Channel", value=log_channel.mention if log_channel else "Not set", inline=True)
        embed.add_field(name="Custom Template", value=custom_template_url or "Default", inline=True)
        embed.add_field(name="Custom Font", value=custom_font_url or "Default", inline=True)
        
        await ctx.send(embed=embed)

    @twitchschedule.command(name="test")
    async def test_post(self, ctx, channel: discord.TextChannel = None):
        """Test post schedule to a channel"""
        if channel is None:
            channel = ctx.channel
            
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        if not twitch_username:
            await ctx.send("❌ Please run setup first!")
            return
            
        async with ctx.channel.typing():
            await ctx.send("🔄 Testing schedule post...")
            try:
                today_london = datetime.datetime.now(london_tz)
                weeks_to_show = await self.config.guild(ctx.guild).weeks_to_show()
                end_of_range = today_london + timedelta(days=max(14, weeks_to_show * 7 + 7))
                all_segments = await self.get_schedule_for_range(twitch_username, today_london, end_of_range)

                if all_segments is not None:
                    await self.post_schedule(channel, all_segments)
                    await ctx.send("✅ Test complete!")
                else:
                    await ctx.send("❌ Failed to fetch schedule!")
            except Exception as e:
                await ctx.send("❌ Test failed. Check logs.")
                await self._log_error(ctx.guild, f"Test error: {str(e)}")

    @twitchschedule.command(name="reload")
    async def reload_resources(self, ctx, template_url: str = None):
        """Force redownload of resources"""
        async with ctx.channel.typing():
            await ctx.send("🔄 Redownloading resources...")
            
            # Clear existing files
            for path in [self.font_path, self.template_path, self.story_template_path]:
                if os.path.exists(path):
                    try:
                        os.remove(path)
                    except Exception as e:
                        await self._log_error(ctx.guild, f"Failed to remove {path}: {e}")
            
            if template_url:
                await self.config.guild(ctx.guild).custom_template_url.set(template_url)
                await ctx.send(f"Set custom template URL to: {template_url}")
            else:
                await self.config.guild(ctx.guild).custom_template_url.set(None)
                await ctx.send("Reverting to default template URL.")
            
            resources_ready = await self.ensure_resources(ctx.guild)
            
            if resources_ready:
                await ctx.send("✅ Successfully redownloaded resources!")
            else:
                await ctx.send("❌ Failed to redownload some resources. Check logs.")

    @twitchschedule.command(name="setfont")
    async def set_font_url(self, ctx, font_url: str = None):
        """Set or clear custom font URL"""
        async with ctx.channel.typing():
            if font_url and font_url.lower() == "none":
                font_url = None

            if font_url:
                if not (font_url.startswith("http://") or font_url.startswith("https://")):
                    await ctx.send("❌ Invalid URL. Please provide full HTTP/HTTPS URL.")
                    return
                await self.config.guild(ctx.guild).custom_font_url.set(font_url)
                await ctx.send(f"✅ Font URL set to: {font_url}")
            else:
                await self.config.guild(ctx.guild).custom_font_url.set(None)
                await ctx.send("✅ Font URL cleared. Using default.")
            
            if os.path.exists(self.font_path):
                try:
                    os.remove(self.font_path)
                except Exception as e:
                    await self._log_error(ctx.guild, f"Failed to remove font: {e}")

            resources_ready = await self.ensure_resources(ctx.guild)
            if resources_ready:
                await ctx.send("✅ Font updated successfully!")
            else:
                await ctx.send("❌ Failed to download font. Check URL and logs.")

    @twitchschedule.command(name="dryrun")
    async def dry_run_schedule(self, ctx, channel: discord.TextChannel = None):
        """Test schedule post without changes"""
        if channel is None:
            channel = ctx.channel
        
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        if not twitch_username:
            await ctx.send("❌ Please run setup first!")
            return
        
        async with ctx.channel.typing():
            await ctx.send("🧪 Starting dry run...")
            try:
                today_london = datetime.datetime.now(london_tz)
                weeks_to_show = await self.config.guild(ctx.guild).weeks_to_show()
                end_of_range = today_london + timedelta(days=max(14, weeks_to_show * 7 + 7))
                all_segments = await self.get_schedule_for_range(twitch_username, today_london, end_of_range)

                if all_segments is not None:
                    await self.post_schedule(channel, all_segments, dry_run=True)
                    await ctx.send("✅ Dry run complete!")
                else:
                    await ctx.send("❌ Failed to fetch schedule!")
            except Exception as e:
                await ctx.send("❌ Dry run failed. Check logs.")
                await self._log_error(ctx.guild, f"Dry run error: {str(e)}")

    @twitchschedule.command(name="setlogchannel")
    async def set_log_channel(self, ctx, channel: discord.TextChannel = None):
        """Set log channel for errors"""
        if channel:
            await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
            await ctx.send(f"✅ Error logs will now be sent to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).log_channel_id.set(None)
            await ctx.send("✅ Error log channel cleared.")


async def setup(bot: Red):
    await bot.add_cog(TwitchSchedule(bot))