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

london_tz = pytz.timezone("Europe/London")

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
            "timezone": None, # Kept for potential future use or if existing functionality uses it
            "custom_template_url": None,
            "log_channel_id": None,
            "daily_upcoming_channel_id": None, # New: Channel for daily upcoming stream
            "daily_upcoming_time": None,       # New: Time for daily update (e.g., "08:00")
            "upcoming_message_id": None,       # New: Message ID for the daily upcoming stream post
            "daily_upcoming_last_run_date": None # To prevent multiple runs on the same day
        }
        self.config.register_guild(**default_guild)
        
        # Initialize tasks
        self.task = self.bot.loop.create_task(self.schedule_update_loop())
        self.daily_task = self.bot.loop.create_task(self.daily_upcoming_stream_loop())

        self.access_token = None

        self.cache_dir = os.path.join(os.path.dirname(__file__), "cache")
        self.font_path = os.path.join(self.cache_dir, "P22.ttf")
        self.template_path = os.path.join(self.cache_dir, "schedule.png")
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)

    def cog_unload(self):
        self.task.cancel()
        self.daily_task.cancel()

    def get_next_sunday(self):
        today = datetime.datetime.now(london_tz)
        days_until_sunday = (6 - today.weekday()) % 7
        next_sunday = today + timedelta(days=days_until_sunday)
        return next_sunday.replace(hour=0, minute=0, second=0, microsecond=0)

    def get_end_of_week(self):
        today = datetime.datetime.now(london_tz)
        days_until_saturday = (5 - today.weekday()) % 7
        end_of_week = today + timedelta(days=days_until_saturday)
        return end_of_week.replace(hour=23, minute=59, second=59, microsecond=0)

    async def get_credentials(self):
        tokens = await self.bot.get_shared_api_tokens("twitch")
        if tokens.get("client_id") and tokens.get("client_secret"):
            return tokens["client_id"], tokens["client_secret"]
        return None

    async def get_twitch_token(self):
        credentials = await self.get_credentials()
        if not credentials:
            return None
        client_id, client_secret = credentials
        async with aiohttp.ClientSession() as session:
            url = "https://id.twitch.tv/oauth2/token"
            params = {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials"
            }
            async with session.post(url, params=params) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("access_token")

    async def download_file(self, url: str, save_path: str) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        os.makedirs(os.path.dirname(save_path), exist_ok=True)
                        with open(save_path, 'wb') as f:
                            f.write(data)
                        return True
                    else:
                        return False
        except Exception:
            return False

    async def ensure_resources(self, guild: discord.Guild):
        font_url = "https://zerolivesleft.net/notelkz/P22.ttf"
        
        custom_template_url = await self.config.guild(guild).custom_template_url()
        template_url = custom_template_url if custom_template_url else "https://zerolivesleft.net/notelkz/schedule.png"
        
        font_downloaded = True
        template_downloaded = True

        if not os.path.exists(self.font_path):
            font_downloaded = await self.download_file(font_url, self.font_path)
            if not font_downloaded:
                await self._log_error(guild, "Failed to download default font file.")

        if not os.path.exists(self.template_path):
            template_downloaded = await self.download_file(template_url, self.template_path)
            if not template_downloaded:
                await self._log_error(guild, f"Failed to download schedule template from {template_url}. Check URL if custom.")

        return os.path.exists(self.font_path) and os.path.exists(self.template_path)

    async def get_schedule(self, username: str):
        credentials = await self.get_credentials()
        if not credentials:
            return None
        if not self.access_token:
            self.access_token = await self.get_twitch_token()
            if not self.access_token:
                return None
        client_id, _ = credentials
        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {self.access_token}"
        }
        async with aiohttp.ClientSession() as session:
            user_url = f"https://api.twitch.tv/helix/users?login={username}"
            async with session.get(user_url, headers=headers) as resp:
                user_data = await resp.json()
                if resp.status != 200 or not user_data.get("data"):
                    return None
                broadcaster_id = user_data["data"][0]["id"]
                broadcaster_name = user_data["data"][0]["login"]
        async with aiohttp.ClientSession() as session:
            url = f"https://api.twitch.tv/helix/schedule?broadcaster_id={broadcaster_id}"
            async with session.get(url, headers=headers) as resp:
                if resp.status == 404:
                    return []
                elif resp.status != 200:
                    return None
                data = await resp.json()
                segments = data.get("data", {}).get("segments", [])
                
                end_of_week = self.get_end_of_week() # Filters to current week by default
                
                filtered_segments = []
                for seg in segments:
                    start_time = dateutil.parser.isoparse(seg["start_time"])
                    if start_time.tzinfo is None:
                        start_time = start_time.replace(tzinfo=datetime.timezone.utc)
                    start_time_local = start_time.astimezone(london_tz)
                    
                    if start_time_local <= end_of_week:
                        seg["broadcaster_name"] = broadcaster_name
                        filtered_segments.append(seg)
                        
                return filtered_segments

    async def get_schedule_for_range(self, username: str, start_date, end_date):
        credentials = await self.get_credentials()
        if not credentials:
            return None
        if not self.access_token:
            self.access_token = await self.get_twitch_token()
            if not self.access_token:
                return None
        client_id, _ = credentials
        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {self.access_token}"
        }
        async with aiohttp.ClientSession() as session:
            user_url = f"https://api.twitch.tv/helix/users?login={username}"
            async with session.get(user_url, headers=headers) as resp:
                user_data = await resp.json()
                if resp.status != 200 or not user_data.get("data"):
                    return None
                broadcaster_id = user_data["data"][0]["id"]
                broadcaster_name = user_data["data"][0]["login"]
        async with aiohttp.ClientSession() as session:
            url = f"https://api.twitch.tv/helix/schedule?broadcaster_id={broadcaster_id}"
            async with session.get(url, headers=headers) as resp:
                if resp.status == 404:
                    return []
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
                        seg["broadcaster_name"] = broadcaster_name
                        filtered_segments.append(seg)
                        
                return filtered_segments

    async def get_category_info(self, category_id: str):
        credentials = await self.get_credentials()
        if not credentials:
            return None
        if not self.access_token:
            self.access_token = await self.get_twitch_token()
            if not self.access_token:
                return None
        client_id, _ = credentials
        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {self.access_token}"
        }
        async with aiohttp.ClientSession() as session:
            url = f"https://api.twitch.tv/helix/games?id={category_id}"
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                if data.get("data"):
                    return data["data"][0]
        return None

    async def get_vods_for_user(self, username: str, start_time: datetime.datetime, end_time: datetime.datetime):
        credentials = await self.get_credentials()
        if not credentials:
            return None
        if not self.access_token:
            self.access_token = await self.get_twitch_token()
            if not self.access_token:
                return None
        client_id, _ = credentials
        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {self.access_token}"
        }
        async with aiohttp.ClientSession() as session:
            user_url = f"https://api.twitch.tv/helix/users?login={username}"
            async with session.get(user_url, headers=headers) as resp:
                user_data = await resp.json()
                if resp.status != 200 or not user_data.get("data"):
                    return None
                broadcaster_id = user_data["data"][0]["id"]
        
        # Twitch API dates are ISO 8601, UTC
        # started_at = start_time.astimezone(datetime.timezone.utc).isoformat(timespec='seconds') + 'Z'
        # ended_at = end_time.astimezone(datetime.timezone.utc).isoformat(timespec='seconds') + 'Z'

        vods_url = f"https://api.twitch.tv/helix/videos?user_id={broadcaster_id}&type=archive&first=5&period=month" # period=month to ensure it covers
        
        try:
            async with session.get(vods_url, headers=headers) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                vods = []
                for vod in data.get("data", []):
                    vod_created_at = dateutil.parser.isoparse(vod["created_at"])
                    # Consider the actual duration of the VOD and the scheduled stream
                    # A simple check: if VOD creation time is within scheduled stream time + a buffer
                    # This is a heuristic, as Twitch doesn't directly link schedule segments to VODs.
                    if vod_created_at >= start_time.astimezone(datetime.timezone.utc) - timedelta(hours=2) and \
                       vod_created_at <= end_time.astimezone(datetime.timezone.utc) + timedelta(hours=2):
                        vods.append(vod)
                return vods
        except Exception:
            return None


    async def generate_schedule_image(self, schedule: list, guild: discord.Guild, start_date=None) -> io.BytesIO:
        if not await self.ensure_resources(guild):
            return None
        
        img = Image.open(self.template_path)
        event_count = await self.config.guild(guild).event_count()
        actual_events = min(len(schedule), event_count)
        
        if actual_events < event_count:
            width, height = img.size
            row_height = 150
            height_to_remove = (event_count - actual_events) * row_height
            new_height = height - height_to_remove
            new_img = Image.new(img.mode, (width, new_height))
            new_img.paste(img.crop((0, 0, width, 350)), (0, 0))
            
            if actual_events > 0:
                event_section_height = actual_events * row_height
                new_img.paste(img.crop((0, 350, width, 350 + event_section_height)), (0, 350))
            
            if height > 350 + event_count * row_height:
                bottom_start = 350 + event_count * row_height
                bottom_height = height - bottom_start
                new_img.paste(img.crop((0, bottom_start, width, height)), (0, 350 + actual_events * row_height))
            
            img = new_img
        
        draw = ImageDraw.Draw(img)
        title_font = ImageFont.truetype(self.font_path, 90)
        date_font = ImageFont.truetype(self.font_path, 40)
        schedule_font = ImageFont.truetype(self.font_path, 42)
        
        if start_date is None:
            today = datetime.datetime.now(london_tz)
            days_since_sunday = today.weekday() + 1
            if days_since_sunday == 7:
                days_since_sunday = 0
            start_of_week = today - timedelta(days=days_since_sunday)
            start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start_of_week = start_date
        
        date_text = start_of_week.strftime("%B %d")
        width, _ = img.size
        right_margin = 100
        
        week_of_text = "Week of"
        week_of_width, week_of_height = title_font.getbbox(week_of_text)[2:4]
        date_width, date_height = date_font.getbbox(date_text)[2:4]
        
        week_of_x = width - right_margin - week_of_width
        date_x = width - right_margin - date_width
        
        draw.text((week_of_x, 100), week_of_text, font=title_font, fill=(255, 255, 255))
        draw.text((date_x, 180), date_text, font=date_font, fill=(255, 255, 255))
        
        day_x = 125
        game_x = 125
        initial_y = 350
        row_height = 150
        day_offset = -45
        
        for i, segment in enumerate(schedule):
            if i >= actual_events:
                break
            bar_y = initial_y + (i * row_height)
            day_y = bar_y + day_offset
            game_y = bar_y + 15
            start_time_utc = dateutil.parser.isoparse(segment["start_time"])
            if start_time_utc.tzinfo is None:
                start_time_utc = start_time_utc.replace(tzinfo=datetime.timezone.utc)
            start_time_london = start_time_utc.astimezone(london_tz)
            day_time = start_time_london.strftime("%A // %I:%M%p").upper()
            title = segment["title"]
            draw.text((day_x, day_y), day_time, font=schedule_font, fill=(255, 255, 255))
            draw.text((game_x, game_y), title, font=schedule_font, fill=(255, 255, 255))
        
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    async def schedule_update_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild in self.bot.guilds:
                    update_days = await self.config.guild(guild).update_days()
                    update_time = await self.config.guild(guild).update_time()
                    if not update_days or not update_time:
                        continue
                    now = datetime.datetime.now(london_tz)
                    current_day = now.weekday()
                    current_time = now.strftime("%H:%M")
                    if current_day in update_days and current_time == update_time:
                        channel_id = await self.config.guild(guild).channel_id()
                        twitch_username = await self.config.guild(guild).twitch_username()
                        if channel_id and twitch_username:
                            channel = guild.get_channel(channel_id)
                            if channel:
                                try:
                                    # Calculate next week's date range
                                    today = datetime.datetime.now(london_tz)
                                    days_until_next_sunday = (6 - today.weekday() + 7) % 7
                                    if days_until_next_sunday == 0:
                                        days_until_next_sunday = 7 # Ensures it's always the *next* Sunday
                                    next_sunday = today + timedelta(days=days_until_next_sunday)
                                    next_sunday = next_sunday.replace(hour=0, minute=0, second=0, microsecond=0)
                                    next_saturday = next_sunday + timedelta(days=6)
                                    next_saturday = next_saturday.replace(hour=23, minute=59, second=59)
                                    
                                    # Use get_schedule_for_range for next week's schedule
                                    schedule = await self.get_schedule_for_range(twitch_username, next_sunday, next_saturday)
                                    if schedule is not None:
                                        await self.post_schedule(channel, schedule, start_date=next_sunday) # Pass start_date
                                    else:
                                        await self._log_error(guild, f"Failed to fetch schedule for {twitch_username} during automated update.")
                                except Exception as e:
                                    await self._log_error(guild, f"Error in schedule_update_loop for guild {guild.id}: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(60) # Check every minute
            except Exception: # Catch any uncaught errors in the loop itself
                await asyncio.sleep(60)

    async def daily_upcoming_stream_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild in self.bot.guilds:
                    channel_id = await self.config.guild(guild).daily_upcoming_channel_id()
                    update_time = await self.config.guild(guild).daily_upcoming_time()
                    
                    if not channel_id or not update_time:
                        continue # Skip if not configured
                    
                    channel = guild.get_channel(channel_id)
                    if not channel:
                        await self._log_error(guild, f"Daily upcoming stream channel (ID: {channel_id}) not found.")
                        continue

                    now = datetime.datetime.now(london_tz)
                    current_time = now.strftime("%H:%M")
                    
                    # Ensure it only runs once per day at the set time
                    last_run_date = await self.config.guild(guild).daily_upcoming_last_run_date()
                    
                    if current_time == update_time and (last_run_date is None or last_run_date != now.date().isoformat()):
                        twitch_username = await self.config.guild(guild).twitch_username()
                        if not twitch_username:
                            continue

                        await self.post_upcoming_stream(channel, twitch_username)
                        await self.config.guild(guild).daily_upcoming_last_run_date.set(now.date().isoformat())
                
                await asyncio.sleep(60) # Check every minute
            except Exception as e:
                await self._log_error(guild, f"Error in daily_upcoming_stream_loop for guild {guild.id}: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(60) # Wait before retrying after an error

    async def _log_error(self, guild: discord.Guild, error_message: str):
        log_channel_id = await self.config.guild(guild).log_channel_id()
        if log_channel_id:
            log_channel = guild.get_channel(log_channel_id)
            if log_channel:
                embed = discord.Embed(
                    title="Twitch Schedule Error",
                    description=f"```py\n{error_message}\n```",
                    color=discord.Color.red(),
                    timestamp=datetime.datetime.now(datetime.timezone.utc)
                )
                try:
                    await log_channel.send(embed=embed)
                except Exception:
                    pass # Fallback if logging fails

    async def post_schedule(self, channel: discord.TextChannel, schedule: list, start_date=None, dry_run: bool = False):
        try:
            if not dry_run:
                notify_role_id = await self.config.guild(channel.guild).notify_role_id()
                notify_role = channel.guild.get_role(notify_role_id) if notify_role_id else None
                warning_content = "⚠️ Updating schedule - Previous schedule messages will be deleted in 10 seconds..."
                if notify_role:
                    warning_content = f"{notify_role.mention}\n{warning_content}"
                
                warning_msg = await channel.send(warning_content)
                await asyncio.sleep(10)
                await warning_msg.delete()
                
                bot_messages = []
                async for message in channel.history(limit=30):
                    if message.author == self.bot.user and message.id != warning_msg.id:
                        bot_messages.append(message)
                        if len(bot_messages) >= 10:
                            break
                
                for message in bot_messages:
                    try:
                        await message.delete()
                        await asyncio.sleep(1.5)
                    except discord.errors.NotFound:
                        pass
                    except discord.errors.Forbidden:
                        await self._log_error(channel.guild, f"Missing permissions to delete messages in {channel.name}.")
                        break
                    except Exception as e:
                        await self._log_error(channel.guild, f"Error deleting message: {e}\n{traceback.format_exc()}")
                        break

            async with channel.typing():
                if dry_run:
                    await channel.send("🧪 Dry run: Generating schedule image...")
                image_buf = await self.generate_schedule_image(schedule, channel.guild, start_date)
                if image_buf:
                    schedule_message = await channel.send(
                        file=discord.File(image_buf, filename="schedule.png")
                    )
                    if not dry_run:
                        try:
                            await schedule_message.pin()
                            await self.config.guild(channel.guild).schedule_message_id.set(schedule_message.id)
                        except discord.errors.Forbidden:
                            await self._log_error(channel.guild, f"Missing permissions to pin messages in {channel.name}.")
                        except Exception as e:
                            await self._log_error(channel.guild, f"Error pinning message: {e}\n{traceback.format_exc()}")

                event_count = await self.config.guild(channel.guild).event_count()
                twitch_username = await self.config.guild(channel.guild).twitch_username()

                for i, segment in enumerate(schedule):
                    if i >= event_count:
                        break

                    start_time = datetime.datetime.fromisoformat(segment["start_time"].replace("Z", "+00:00"))
                    title = segment["title"]
                    category = segment.get("category", {})
                    game_name = category.get("name", "No Category")
                    
                    boxart_url = None
                    if category and category.get("id"):
                        cat_info = await self.get_category_info(category["id"])
                        if cat_info and cat_info.get("box_art_url"):
                            boxart_url = cat_info["box_art_url"].replace("{width}", "285").replace("{height}", "380")

                    unix_ts = int(start_time.timestamp())
                    time_str = f"<t:{unix_ts}:F>"
                    
                    end_time = segment.get("end_time")
                    end_dt = None
                    if end_time:
                        end_dt = datetime.datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                        duration = end_dt - start_time
                        hours, remainder = divmod(duration.seconds, 3600)
                        minutes = remainder // 60
                        duration_str = f"{hours}h {minutes}m"
                    else:
                        duration_str = "Unknown"

                    twitch_url = f"https://twitch.tv/{twitch_username}"
                    
                    embed = discord.Embed(
                        title=title,
                        url=twitch_url,
                        description=f"**[Watch Live Here]({twitch_url})**",
                        color=discord.Color.purple(),
                        timestamp=start_time
                    )
                    embed.add_field(name="🕒 Start Time", value=time_str, inline=True)
                    embed.add_field(name="⏳ Duration", value=duration_str, inline=True)
                    embed.add_field(name="🎮 Game", value=game_name, inline=True)
                    embed.set_footer(text=f"Scheduled Stream • {twitch_username}")
                    
                    if boxart_url:
                        embed.set_thumbnail(url=boxart_url)

                    if end_dt and end_dt < datetime.datetime.now(datetime.timezone.utc):
                        vods = await self.get_vods_for_user(twitch_username, start_time, end_dt)
                        if vods and len(vods) > 0:
                            vod_url = vods[0]["url"]
                            embed.add_field(name="🎥 Watch VOD", value=f"[Click Here]({vod_url})", inline=False)


                    if dry_run:
                        embed.set_author(name="DRY RUN PREVIEW")
                        embed.color = discord.Color.dark_grey()
                        
                    await channel.send(embed=embed)
                    await asyncio.sleep(0.5)

                if not schedule:
                    embed = discord.Embed(
                        title="No Upcoming Streams",
                        description="Check back later for new streams!",
                        color=discord.Color.purple()
                    )
                    if dry_run:
                        embed.set_author(name="DRY RUN PREVIEW")
                        embed.color = discord.Color.dark_grey()
                    await channel.send(embed=embed)

        except Exception as e:
            await self._log_error(channel.guild, f"Error in post_schedule: {e}\n{traceback.format_exc()}")
            if dry_run:
                await channel.send(f"❌ Dry run failed due to an error. Check logs for details.")
            else:
                await channel.send(f"❌ An error occurred while posting the schedule. Please check the logs.")

    async def post_upcoming_stream(self, channel: discord.TextChannel, twitch_username: str):
        try:
            today = datetime.datetime.now(london_tz)
            end_of_future = today + timedelta(days=14) # Look 14 days ahead for upcoming streams
            schedule_segments = await self.get_schedule_for_range(twitch_username, today, end_of_future)
            
            next_stream = None
            if schedule_segments:
                schedule_segments.sort(key=lambda x: dateutil.parser.isoparse(x["start_time"]))
                
                for segment in schedule_segments:
                    start_time_utc = dateutil.parser.isoparse(segment["start_time"].replace("Z", "+00:00"))
                    if start_time_utc > datetime.datetime.now(datetime.timezone.utc): # Ensure it's truly in the future
                        next_stream = segment
                        break

            if next_stream:
                start_time = datetime.datetime.fromisoformat(next_stream["start_time"].replace("Z", "+00:00"))
                title = next_stream["title"]
                category = next_stream.get("category", {})
                game_name = category.get("name", "No Category")
                
                boxart_url = None
                if category and category.get("id"):
                    cat_info = await self.get_category_info(category["id"])
                    if cat_info and cat_info.get("box_art_url"):
                        boxart_url = cat_info["box_art_url"].replace("{width}", "285").replace("{height}", "380")

                unix_ts = int(start_time.timestamp())
                time_str_relative = f"<t:{unix_ts}:R>"
                time_str_full = f"<t:{unix_ts}:F>"
                
                end_time = next_stream.get("end_time")
                duration_str = "Unknown"
                if end_time:
                    end_dt = datetime.datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                    duration = end_dt - start_time
                    hours, remainder = divmod(duration.seconds, 3600)
                    minutes = remainder // 60
                    duration_str = f"{hours}h {minutes}m"

                twitch_url = f"https://twitch.tv/{twitch_username}"
                
                embed = discord.Embed(
                    title=f"📣 Next Stream: {title}",
                    url=twitch_url,
                    description=f"**[Watch Live Here]({twitch_url})**\n\nStarting {time_str_relative} on {time_str_full}",
                    color=discord.Color.green(),
                    timestamp=start_time
                )
                embed.add_field(name="🎮 Game", value=game_name, inline=True)
                embed.add_field(name="⏳ Expected Duration", value=duration_str, inline=True)
                embed.set_footer(text=f"Twitch Stream • {twitch_username}")
                if boxart_url:
                    embed.set_thumbnail(url=boxart_url)
                
                content = f"Hey @everyone! Here's the **NEXT** stream happening soon from {twitch_username}!" # Consider making @everyone configurable
            else:
                content = "📣 No upcoming streams found for the next two weeks!"
                embed = discord.Embed(
                    title="No Upcoming Streams",
                    description="The streamer currently has no upcoming events on Twitch's schedule for the next two weeks.",
                    color=discord.Color.red()
                )

            message_id = await self.config.guild(channel.guild).upcoming_message_id()
            if message_id:
                try:
                    old_message = await channel.fetch_message(message_id)
                    await old_message.delete()
                except discord.errors.NotFound:
                    pass
                except discord.errors.Forbidden:
                    await self._log_error(channel.guild, f"Missing permissions to delete old upcoming message in {channel.name}.")
                    pass

            new_message = await channel.send(content, embed=embed)
            await self.config.guild(channel.guild).upcoming_message_id.set(new_message.id)

        except Exception as e:
            await self._log_error(channel.guild, f"Error posting upcoming stream: {e}\n{traceback.format_exc()}")
            await channel.send("❌ An error occurred while posting the upcoming stream. Check logs.")


    @commands.group(aliases=["tsched"])
    @commands.admin_or_permissions(manage_guild=True)
    async def twitchschedule(self, ctx):
        """Twitch Schedule Management Commands"""
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(
                title="Twitch Schedule Commands",
                color=discord.Color.purple(),
                description=(
                    f"`{ctx.clean_prefix}tsched setup` - Interactive setup process for main schedule\n"
                    f"`{ctx.clean_prefix}tsched force [next]` - Force an immediate main schedule update\n"
                    f"`{ctx.clean_prefix}tsched notify [@role/none]` - Set or clear notification role for main schedule\n"
                    f"`{ctx.clean_prefix}tsched events [number]` - Set number of events to show (1-10) on main schedule\n"
                    f"`{ctx.clean_prefix}tsched settings` - Show all current settings\n"
                    f"`{ctx.clean_prefix}tsched test #channel` - Test post main schedule to a channel\n"
                    f"`{ctx.clean_prefix}tsched reload [url]` - Redownload template image and font files\n"
                    f"`{ctx.clean_prefix}tsched dryrun [#channel]` - Test post main schedule without deleting/pinning\n"
                    f"`{ctx.clean_prefix}tsched setlogchannel [#channel/none]` - Set channel for error logs\n"
                    f"\n**Daily Upcoming Stream Commands:**\n"
                    f"`{ctx.clean_prefix}tsched setupcomingchannel [#channel/none]` - Set channel for daily next stream post\n"
                    f"`{ctx.clean_prefix}tsched setupcomingtime [HH:MM]` - Set time for daily next stream post\n"
                    f"`{ctx.clean_prefix}tsched forceupcoming` - Force an immediate next stream post\n"
                    f"\n(Other commands like `imgr`, `next`, `timezone`, `list` are available if you add them back from previous versions.)"
                )
            )
            await ctx.send(embed=embed)

    @twitchschedule.command(name="force")
    async def force_update(self, ctx, option: str = None):
        """Force an immediate schedule update. Use 'next' to show next week's schedule."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        
        if not channel_id or not twitch_username:
            await ctx.send("❌ Please run setup first to configure the main schedule channel and Twitch username!")
            return
            
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("❌ Schedule channel not found! Please re-run setup or check permissions.")
            return
            
        async with ctx.channel.typing():
            await ctx.send("🔄 Forcing main schedule update... This might take a moment.")

            if option and option.lower() == "next":
                today = datetime.datetime.now(london_tz)
                days_until_next_sunday = (6 - today.weekday() + 7) % 7
                if days_until_next_sunday == 0:
                    days_until_next_sunday = 7
                next_sunday = today + timedelta(days=days_until_next_sunday)
                next_sunday = next_sunday.replace(hour=0, minute=0, second=0, microsecond=0)
                next_saturday = next_sunday + timedelta(days=6)
                next_saturday = next_saturday.replace(hour=23, minute=59, second=59)
                
                schedule = await self.get_schedule_for_range(twitch_username, next_sunday, next_saturday)
                if schedule is not None:
                    await self.post_schedule(channel, schedule, start_date=next_sunday)
            else:
                schedule = await self.get_schedule(twitch_username)
                if schedule is not None:
                    await self.post_schedule(channel, schedule)

            if schedule is not None:
                await ctx.send("✅ Main schedule updated!")
            else:
                await ctx.send("❌ Failed to fetch main schedule from Twitch! Check bot logs for details.")
                await self._log_error(ctx.guild, f"Force update failed for {twitch_username}. No schedule fetched.")

    @twitchschedule.command(name="setup")
    async def setup_schedule(self, ctx):
        """Interactive setup process for Twitch schedule."""
        await ctx.send("Starting interactive setup process for the **main weekly schedule**... Please answer the following questions. You will have 30 seconds to respond to each question, or the setup will cancel.")
        
        # Channel ID
        channel = None
        for i in range(3):
            await ctx.send(f"**Step {i+1}/4:** Which channel should I post the **main weekly schedule** in? (Mention the channel, e.g., `#schedule`)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                if not msg.channel_mentions:
                    await ctx.send("❌ That doesn't look like a channel mention. Please try again.")
                    continue
                channel = msg.channel_mentions[0]
                await self.config.guild(ctx.guild).channel_id.set(channel.id)
                break
            except asyncio.TimeoutError:
                await ctx.send("⌛ Setup timed out. Please try again from the beginning with `!tsched setup`.")
                return
        else:
            await ctx.send("Too many invalid attempts to set the channel. Setup cancelled.")
            return
        
        # Twitch Username
        username = None
        for i in range(3):
            await ctx.send(f"**Step {i+1}/4:** What's the Twitch username of the streamer whose schedule I should track? (e.g., `notelkz`)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                username = msg.content.strip()
                if not username:
                    await ctx.send("❌ Twitch username cannot be empty. Please try again.")
                    continue
                await self.config.guild(ctx.guild).twitch_username.set(username)
                break
            except asyncio.TimeoutError:
                await ctx.send("⌛ Setup timed out. Please try again from the beginning with `!tsched setup`.")
                return
        else:
            await ctx.send("Too many invalid attempts to set the Twitch username. Setup cancelled.")
            return

        # Update Days
        days = []
        for i in range(3):
            await ctx.send(f"**Step {i+1}/4:** On which days should I automatically update the **main weekly schedule**? (Send numbers: `0=Monday, 1=Tuesday, ..., 6=Sunday`. Separate multiple days with spaces, e.g., `0 6` for Monday and Sunday)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                try:
                    days = [int(x) for x in msg.content.split()]
                    if not all(0 <= x <= 6 for x in days):
                        await ctx.send("❌ Invalid days. All numbers must be between 0 and 6. Please try again.")
                        continue
                    await self.config.guild(ctx.guild).update_days.set(days)
                    break
                except ValueError:
                    await ctx.send("❌ Invalid input. Please enter numbers separated by spaces. Example: `0 6`. Try again.")
                    continue
            except asyncio.TimeoutError:
                await ctx.send("⌛ Setup timed out. Please try again from the beginning with `!tsched setup`.")
                return
        else:
            await ctx.send("Too many invalid attempts to set update days. Setup cancelled.")
            return

        # Update Time
        update_time = None
        for i in range(3):
            await ctx.send(f"**Step {i+1}/4:** At what time (in London/UK time) should I update the **main weekly schedule** on the chosen days? (Use 24-hour format, e.g., `14:00` for 2 PM)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                time_input = msg.content.strip()
                if not re.match(r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$", time_input):
                    await ctx.send("❌ Invalid time format. Please use HH:MM (24-hour). Example: `09:30` or `23:00`. Try again.")
                    continue
                update_time = time_input
                await self.config.guild(ctx.guild).update_time.set(update_time)
                break
            except asyncio.TimeoutError:
                await ctx.send("⌛ Setup timed out. Please try again from the beginning with `!tsched setup`.")
                return
        else:
            await ctx.send("Too many invalid attempts to set update time. Setup cancelled.")
            return
        
        # Final Confirmation
        confirm_embed = discord.Embed(
            title="Main Schedule Setup Summary",
            description="Please confirm these settings:",
            color=discord.Color.blue()
        )
        days_map = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        update_days_str = ", ".join(days_map[d] for d in days) if days else "None"

        confirm_embed.add_field(name="Schedule Channel", value=channel.mention if channel else "Not set", inline=False)
        confirm_embed.add_field(name="Twitch Username", value=username or "Not set", inline=False)
        confirm_embed.add_field(name="Update Days (London/UK Time)", value=update_days_str, inline=False)
        confirm_embed.add_field(name="Update Time (London/UK Time)", value=update_time or "Not set", inline=False)
        confirm_embed.set_footer(text="Type 'yes' to confirm or 'no' to cancel.")

        await ctx.send(embed=confirm_embed)
        try:
            msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ["yes", "no"],
                timeout=30.0
            )
            if msg.content.lower() == "yes":
                await ctx.send("✅ Main schedule setup complete! The schedule will be updated on the specified days and time.")
            else:
                await ctx.send("❌ Main schedule setup cancelled by user.")
        except asyncio.TimeoutError:
            await ctx.send("⌛ Confirmation timed out. Main schedule setup cancelled.")
        except Exception as e:
            await ctx.send(f"❌ An error occurred during setup: {str(e)}")
            await self._log_error(ctx.guild, f"Error during setup confirmation: {e}\n{traceback.format_exc()}")


    @twitchschedule.command(name="notify")
    async def set_notify_role(self, ctx, role: discord.Role = None):
        """Set or clear the role to notify for main schedule updates."""
        if role is None:
            await self.config.guild(ctx.guild).notify_role_id.set(None)
            await ctx.send("✅ Notification role for main schedule cleared!")
        else:
            await self.config.guild(ctx.guild).notify_role_id.set(role.id)
            await ctx.send(f"✅ Notification role for main schedule set to {role.mention}!")

    @twitchschedule.command(name="events")
    async def set_event_count(self, ctx, count: int):
        """Set the number of events to show (1-10) on the main schedule image."""
        if not 1 <= count <= 10:
            await ctx.send("❌ Event count must be between 1 and 10!")
            return
        await self.config.guild(ctx.guild).event_count.set(count)
        await ctx.send(f"✅ Event count for main schedule set to {count}!")

    @twitchschedule.command(name="settings")
    async def show_settings(self, ctx):
        """Show all current settings."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        update_days = await self.config.guild(ctx.guild).update_days()
        update_time = await self.config.guild(ctx.guild).update_time()
        notify_role_id = await self.config.guild(ctx.guild).notify_role_id()
        event_count = await self.config.guild(ctx.guild).event_count()
        custom_template_url = await self.config.guild(ctx.guild).custom_template_url()
        log_channel_id = await self.config.guild(ctx.guild).log_channel_id()
        daily_upcoming_channel_id = await self.config.guild(ctx.guild).daily_upcoming_channel_id()
        daily_upcoming_time = await self.config.guild(ctx.guild).daily_upcoming_time()
        
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        notify_role = ctx.guild.get_role(notify_role_id) if notify_role_id else None
        log_channel = ctx.guild.get_channel(log_channel_id) if log_channel_id else None
        daily_upcoming_channel = ctx.guild.get_channel(daily_upcoming_channel_id) if daily_upcoming_channel_id else None
        
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        update_days_str = ", ".join(days[day] for day in update_days) if update_days else "None"
        
        embed = discord.Embed(
            title="Twitch Schedule Settings",
            color=discord.Color.purple()
        )
        embed.add_field(name="Main Schedule Channel", value=channel.mention if channel else "Not set", inline=True)
        embed.add_field(name="Twitch Username", value=twitch_username or "Not set", inline=True)
        embed.add_field(name="Main Update Time (UK)", value=update_time or "Not set", inline=True)
        embed.add_field(name="Main Update Days (UK)", value=update_days_str, inline=True)
        embed.add_field(name="Main Notify Role", value=notify_role.mention if notify_role else "Not set", inline=True)
        embed.add_field(name="Main Event Count", value=str(event_count), inline=True)
        embed.add_field(name="Custom Template URL", value=custom_template_url or "Not set (using default)", inline=True)
        embed.add_field(name="Error Log Channel", value=log_channel.mention if log_channel else "Not set", inline=True)
        embed.add_field(name="Daily Upcoming Channel", value=daily_upcoming_channel.mention if daily_upcoming_channel else "Not set", inline=True)
        embed.add_field(name="Daily Upcoming Time (UK)", value=daily_upcoming_time or "Not set", inline=True)
        
        await ctx.send(embed=embed)

    @twitchschedule.command(name="test")
    async def test_post(self, ctx, channel: discord.TextChannel = None):
        """Test post main schedule to a channel."""
        if channel is None:
            channel = ctx.channel
            
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        if not twitch_username:
            await ctx.send("❌ Please run setup first to configure the Twitch username!")
            return
            
        async with ctx.channel.typing():
            await ctx.send("🔄 Testing main schedule post...")
            schedule = await self.get_schedule(twitch_username)
            if schedule is not None:
                await self.post_schedule(channel, schedule)
                await ctx.send("✅ Test complete!")
            else:
                await ctx.send("❌ Failed to fetch main schedule from Twitch! Check bot logs for details.")
                await self._log_error(ctx.guild, f"Test post failed for {twitch_username}. No schedule fetched.")

    @twitchschedule.command(name="reload")
    async def reload_resources(self, ctx, template_url: str = None):
        """Force redownload of the template image and font files. Provide a URL to set a custom template."""
        async with ctx.channel.typing():
            await ctx.send("🔄 Redownloading resources...")
            
            if os.path.exists(self.font_path):
                try:
                    os.remove(self.font_path)
                except Exception as e:
                    await self._log_error(ctx.guild, f"Failed to remove existing font file: {e}")
            if os.path.exists(self.template_path):
                try:
                    os.remove(self.template_path)
                except Exception as e:
                    await self._log_error(ctx.guild, f"Failed to remove existing template file: {e}")
            
            font_url = "https://zerolivesleft.net/notelkz/P22.ttf"
            default_template_url = "https://zerolivesleft.net/notelkz/schedule.png"
            
            if template_url:
                await self.config.guild(ctx.guild).custom_template_url.set(template_url)
                await ctx.send(f"Attempting to download custom template from: {template_url}")
            else:
                await self.config.guild(ctx.guild).custom_template_url.set(None)
                await ctx.send("Using default template URL.")
            
            font_success = await self.download_file(font_url, self.font_path)
            template_success = await self.download_file(
                template_url if template_url else default_template_url, self.template_path
            )
            
            if font_success and template_success:
                await ctx.send("✅ Successfully redownloaded resources!")
            else:
                error_msg = "❌ Failed to redownload some resources. "
                if not font_success:
                    error_msg += "Font download failed. "
                if not template_success:
                    error_msg += "Template download failed. "
                error_msg += "Please check the URLs and bot permissions."
                await ctx.send(error_msg)
                await self._log_error(ctx.guild, error_msg + f"\nFont URL: {font_url}, Template URL: {template_url if template_url else default_template_url}")

    @twitchschedule.command(name="dryrun")
    async def dry_run_schedule(self, ctx, channel: discord.TextChannel = None):
        """Perform a dry run of the main schedule post without deleting/pinning messages."""
        if channel is None:
            channel = ctx.channel
            
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        if not twitch_username:
            await ctx.send("❌ Please run setup first to configure the Twitch username!")
            return
            
        async with ctx.channel.typing():
            await ctx.send("🧪 Starting dry run for main schedule... No messages will be deleted or pinned.")
            schedule = await self.get_schedule(twitch_username)
            if schedule is not None:
                await self.post_schedule(channel, schedule, dry_run=True)
                await ctx.send("✅ Dry run complete! Check the specified channel for a preview. No actual changes were made.")
            else:
                await ctx.send("❌ Failed to fetch main schedule from Twitch for dry run! Check bot logs for details.")
                await self._log_error(ctx.guild, f"Dry run failed for {twitch_username}. No schedule fetched.")

    @twitchschedule.command(name="setlogchannel")
    async def set_log_channel(self, ctx, channel: discord.TextChannel = None):
        """Set the channel for bot error logs. Use `none` to clear."""
        if channel:
            await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
            await ctx.send(f"✅ Error logs will now be sent to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).log_channel_id.set(None)
            await ctx.send("✅ Error log channel cleared. No errors will be reported via Discord.")

    @twitchschedule.command(name="setupcomingchannel")
    async def set_upcoming_channel(self, ctx, channel: discord.TextChannel = None):
        """Set the channel for the daily 'next upcoming stream' post. Use `none` to disable."""
        if channel:
            await self.config.guild(ctx.guild).daily_upcoming_channel_id.set(channel.id)
            await ctx.send(f"✅ Daily upcoming stream will now be posted in {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).daily_upcoming_channel_id.set(None)
            await ctx.send("✅ Daily upcoming stream channel cleared. This feature is now disabled.")

    @twitchschedule.command(name="setupcomingtime")
    async def set_upcoming_time(self, ctx, time_str: str):
        """Set the time (HH:MM London/UK time) for the daily 'next upcoming stream' post."""
        if not re.match(r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$", time_str):
            await ctx.send("❌ Invalid time format. Please use HH:MM (24-hour, e.g., `09:30` or `23:00`).")
            return
        await self.config.guild(ctx.guild).daily_upcoming_time.set(time_str)
        await ctx.send(f"✅ Daily upcoming stream will update at `{time_str}` London/UK time.")

    @twitchschedule.command(name="forceupcoming")
    async def force_upcoming_update(self, ctx):
        """Force an immediate update of the 'next upcoming stream' post."""
        channel_id = await self.config.guild(ctx.guild).daily_upcoming_channel_id()
        twitch_username = await self.config.guild(ctx.guild).twitch_username()

        if not channel_id or not twitch_username:
            await ctx.send("❌ Please set up the upcoming stream channel and Twitch username first using `!tsched setupcomingchannel`.")
            return

        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("❌ The configured upcoming stream channel no longer exists! Please re-set it.")
            return
        
        async with ctx.channel.typing():
            await ctx.send("🔄 Forcing update of upcoming stream...")
            await self.post_upcoming_stream(channel, twitch_username)
            await ctx.send("✅ Upcoming stream updated!")


async def setup(bot: Red):
    await bot.add_cog(TwitchSchedule(bot))