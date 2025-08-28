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
            "channel_id": None,          # The single channel for all schedule posts
            "twitch_username": None,
            "update_days": [],
            "update_time": None,
            "schedule_message_id": None, # Used to pin the main image/first embed
            "notify_role_id": None,
            "event_count": 5,            # Number of events to show on the image and as embeds
            "timezone": None,            # Kept for potential future use or if existing functionality uses it
            "custom_template_url": None,
            "custom_font_url": None,     # New: Custom font URL
            "log_channel_id": None,      # Channel for error reporting/logging
            "include_next_week": False,  # NEW: Toggle for including next week's schedule
            "weeks_to_show": 1          # NEW: Number of weeks to show (1 or 2)
        }
        self.config.register_guild(**default_guild)
        
        # Only one main task now, responsible for all updates
        self.task = self.bot.loop.create_task(self.schedule_update_loop())

        self.access_token = None

        self.cache_dir = os.path.join(os.path.dirname(__file__), "cache")
        self.font_path = os.path.join(self.cache_dir, "P22.ttf") # Default font filename
        self.template_path = os.path.join(self.cache_dir, "schedule.png") # Default template filename
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)

    def cog_unload(self):
        self.task.cancel()

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
        # Determine font URL
        custom_font_url = await self.config.guild(guild).custom_font_url()
        font_url_to_use = custom_font_url if custom_font_url else "https://zerolivesleft.net/notelkz/P22.ttf"
        
        # Determine template URL
        custom_template_url = await self.config.guild(guild).custom_template_url()
        template_url_to_use = custom_template_url if custom_template_url else "https://zerolivesleft.net/notelkz/schedule.png"
        
        font_downloaded = True
        template_downloaded = True

        if not os.path.exists(self.font_path):
            font_downloaded = await self.download_file(font_url_to_use, self.font_path)
            if not font_downloaded:
                await self._log_error(guild, f"Failed to download font file from {font_url_to_use}. Check URL if custom.")

        if not os.path.exists(self.template_path):
            template_downloaded = await self.download_file(template_url_to_use, self.template_path)
            if not template_downloaded:
                await self._log_error(guild, f"Failed to download schedule template from {template_url_to_use}. Check URL if custom.")

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
                
                filtered_segments = []
                for seg in segments:
                    start_time = dateutil.parser.isoparse(seg["start_time"])
                    if start_time.tzinfo is None:
                        start_time = start_time.replace(tzinfo=datetime.timezone.utc)
                    seg["broadcaster_name"] = broadcaster_name
                    filtered_segments.append(seg)
                        
                return filtered_segments


    async def get_schedule_for_range(self, username: str, start_date: datetime.datetime, end_date: datetime.datetime):
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
                    start_time_local = start_time.astimezone(london_tz) # Convert to local timezone for comparison
                    
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
        
        vods_url = f"https://api.twitch.tv/helix/videos?user_id={broadcaster_id}&type=archive&first=5&period=month"
        
        try:
            async with session.get(vods_url, headers=headers) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                vods = []
                for vod in data.get("data", []):
                    vod_created_at = dateutil.parser.isoparse(vod["created_at"])
                    # Heuristic: check if VOD creation time is within scheduled stream time + a buffer
                    if vod_created_at >= start_time.astimezone(datetime.timezone.utc) - timedelta(hours=2) and \
                       vod_created_at <= end_time.astimezone(datetime.timezone.utc) + timedelta(hours=2):
                        vods.append(vod)
                return vods
        except Exception:
            return None


    async def generate_schedule_image(self, schedule_for_image: list, guild: discord.Guild, start_date=None) -> io.BytesIO:
        if not await self.ensure_resources(guild):
            return None
        
        img = Image.open(self.template_path)
        event_count = await self.config.guild(guild).event_count()
        actual_events = min(len(schedule_for_image), event_count)
        
        # Adjust image height if fewer events than expected
        if actual_events < event_count:
            width, height = img.size
            row_height = 150 # Estimated height per event line in template
            height_to_remove = (event_count - actual_events) * row_height
            new_height = height - height_to_remove
            new_img = Image.new(img.mode, (width, new_height))
            
            # Copy top part (header)
            new_img.paste(img.crop((0, 0, width, 350)), (0, 0)) # Assuming 350px is below title
            
            # Copy event part if there are events
            if actual_events > 0:
                event_section_height = actual_events * row_height
                new_img.paste(img.crop((0, 350, width, 350 + event_section_height)), (0, 350))
            
            # Copy bottom part (footer, if any space left after removing rows)
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
            if days_since_sunday == 7: # If today is Sunday, it's the start of the current week
                days_since_sunday = 0
            start_of_week = today - timedelta(days=days_since_sunday)
            start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            start_of_week = start_date
        
        # Check if we're showing multiple weeks
        weeks_to_show = await self.config.guild(guild).weeks_to_show()
        if weeks_to_show > 1:
            end_week = start_of_week + timedelta(days=(weeks_to_show * 7) - 1)
            date_text = f"{start_of_week.strftime('%b %d')} - {end_week.strftime('%b %d')}"
        else:
            date_text = start_of_week.strftime("%B %d")
        
        width, _ = img.size
        right_margin = 100
        
        week_of_text = "Week of" if weeks_to_show == 1 else "Weeks of"
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
        
        for i, segment in enumerate(schedule_for_image):
            if i >= actual_events: # Ensure we only draw up to event_count
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
                    channel_id = await self.config.guild(guild).channel_id()
                    twitch_username = await self.config.guild(guild).twitch_username()
                    update_days = await self.config.guild(guild).update_days()
                    update_time = await self.config.guild(guild).update_time()

                    if not (channel_id and twitch_username and update_days and update_time):
                        continue # Skip if main schedule not fully configured

                    channel = guild.get_channel(channel_id)
                    if not channel:
                        await self._log_error(guild, f"Configured schedule channel (ID: {channel_id}) not found for guild {guild.id}.")
                        continue

                    now = datetime.datetime.now(london_tz)
                    current_day = now.weekday()
                    current_time = now.strftime("%H:%M")

                    if current_day in update_days and current_time == update_time:
                        try:
                            # Fetch schedule range based on weeks_to_show setting
                            weeks_to_show = await self.config.guild(guild).weeks_to_show()
                            today_utc = datetime.datetime.now(datetime.timezone.utc)
                            # Look ahead based on weeks setting (minimum 2 weeks to ensure we catch next streams)
                            end_of_range = today_utc + timedelta(days=max(14, weeks_to_show * 7 + 7))
                            
                            all_upcoming_segments = await self.get_schedule_for_range(
                                twitch_username, today_utc.astimezone(london_tz), end_of_range.astimezone(london_tz)
                            )

                            if all_upcoming_segments is not None:
                                await self.post_schedule(channel, all_upcoming_segments)
                            else:
                                await self._log_error(guild, f"Failed to fetch schedule for {twitch_username} during automated update.")
                        except Exception as e:
                            await self._log_error(guild, f"Error in schedule_update_loop for guild {guild.id}: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(60) # Check every minute
            except Exception:
                await asyncio.sleep(60) # Wait before retrying after any uncaught errors in the loop itself


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

    async def post_schedule(self, channel: discord.TextChannel, all_segments: list, dry_run: bool = False, start_date_for_image=None):
        try:
            twitch_username = await self.config.guild(channel.guild).twitch_username()
            event_count = await self.config.guild(channel.guild).event_count()
            weeks_to_show = await self.config.guild(channel.guild).weeks_to_show()

            # Filter for current and future streams, and sort them
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            future_segments = []
            for seg in all_segments:
                start_time_utc = dateutil.parser.isoparse(seg["start_time"].replace("Z", "+00:00"))
                if start_time_utc >= now_utc - timedelta(minutes=5): # Small buffer for recently started streams
                    future_segments.append(seg)
            
            future_segments.sort(key=lambda x: dateutil.parser.isoparse(x["start_time"]))

            # --- Delete previous bot messages ---
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
                async for message in channel.history(limit=50): # Increased limit for multiple weeks
                    if message.author == self.bot.user and message.id != warning_msg.id:
                        bot_messages.append(message)
                
                for message in bot_messages:
                    try:
                        await message.delete()
                        await asyncio.sleep(1.5) # Small delay to avoid rate limits
                    except discord.errors.NotFound:
                        pass
                    except discord.errors.Forbidden:
                        await self._log_error(channel.guild, f"Missing permissions to delete messages in {channel.name}.")
                        break
                    except Exception as e:
                        await self._log_error(channel.guild, f"Error deleting message: {e}\n{traceback.format_exc()}")
                        break

            async with channel.typing():
                # Determine starting week
                if start_date_for_image is None:
                    today_london = datetime.datetime.now(london_tz)
                    days_since_sunday = today_london.weekday() + 1
                    if days_since_sunday == 7:
                        days_since_sunday = 0
                    start_of_first_week = today_london - timedelta(days=days_since_sunday)
                    start_of_first_week = start_of_first_week.replace(hour=0, minute=0, second=0, microsecond=0)
                else:
                    start_of_first_week = start_date_for_image

                # Track if we've posted the "next up" stream
                next_stream_posted = False
                first_message_for_pinning = None

                # Loop through each week
                for week_num in range(weeks_to_show):
                    week_start = start_of_first_week + timedelta(days=week_num * 7)
                    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
                    
                    # Filter streams for this specific week
                    week_streams = [
                        s for s in future_segments
                        if week_start <= dateutil.parser.isoparse(s["start_time"]).astimezone(london_tz) <= week_end
                    ]
                    
                    # Generate and post week image
                    if week_streams:
                        if dry_run:
                            await channel.send(f"🧪 Dry run: Generating week {week_num + 1} schedule image...")
                        
                        # Take only up to event_count streams for the image
                        image_streams = week_streams[:event_count]
                        image_buf = await self.generate_schedule_image(image_streams, channel.guild, start_date=week_start)
                        if image_buf:
                            week_message = await channel.send(
                                file=discord.File(image_buf, filename=f"schedule_week_{week_num + 1}.png")
                            )
                            if first_message_for_pinning is None:
                                first_message_for_pinning = week_message
                    else:
                        # No streams for this week
                        week_title = f"Week of {week_start.strftime('%B %d')}"
                        embed_no_streams = discord.Embed(
                            title=f"No Streams Scheduled - {week_title}",
                            description="There are no streams currently scheduled for this week on Twitch.",
                            color=discord.Color.orange()
                        )
                        if dry_run:
                            embed_no_streams.set_author(name="DRY RUN PREVIEW")
                            embed_no_streams.color = discord.Color.dark_grey()
                        
                        week_message = await channel.send(embed=embed_no_streams)
                        if first_message_for_pinning is None:
                            first_message_for_pinning = week_message

                    # Post individual stream embeds for this week
                    for stream in week_streams:
                        start_time = datetime.datetime.fromisoformat(stream["start_time"].replace("Z", "+00:00"))
                        title = stream["title"]
                        category = stream.get("category", {})
                        game_name = category.get("name", "No Category")
                        
                        boxart_url = None
                        if category and category.get("id"):
                            cat_info = await self.get_category_info(category["id"])
                            if cat_info and cat_info.get("box_art_url"):
                                boxart_url = cat_info["box_art_url"].replace("{width}", "285").replace("{height}", "380")

                        unix_ts = int(start_time.timestamp())
                        time_str_relative = f"<t:{unix_ts}:R>"
                        time_str_full = f"<t:{unix_ts}:F>"
                        
                        end_time = stream.get("end_time")
                        duration_str = "Unknown"
                        if end_time:
                            end_dt = datetime.datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                            duration = end_dt - start_time
                            hours, remainder = divmod(duration.seconds, 3600)
                            minutes = remainder // 60
                            duration_str = f"{hours}h {minutes}m"

                        twitch_url = f"https://twitch.tv/{twitch_username}"
                        
                        # Check if this is the next upcoming stream overall
                        is_next_stream = not next_stream_posted and stream == future_segments[0] if future_segments else False
                        
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
                            next_stream_posted = True
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

                        # Check for VODs if stream has passed
                        if end_time:
                            end_dt = datetime.datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                            if end_dt < now_utc:
                                vods = await self.get_vods_for_user(twitch_username, start_time, end_dt)
                                if vods and len(vods) > 0:
                                    vod_url = vods[0]["url"]
                                    embed.add_field(name="🎥 Watch VOD", value=f"[Click Here]({vod_url})", inline=False)

                        if dry_run:
                            embed.set_author(name="DRY RUN PREVIEW")
                            embed.color = discord.Color.dark_grey()
                            
                        await channel.send(embed=embed)
                        await asyncio.sleep(0.5)

                # Handle case where no streams found at all
                if not future_segments:
                    week_text = "week" if weeks_to_show == 1 else f"{weeks_to_show} weeks"
                    embed = discord.Embed(
                        title="No Upcoming Streams",
                        description=f"There are currently no streams scheduled on Twitch for the next {week_text}.",
                        color=discord.Color.red()
                    )
                    if dry_run:
                        embed.set_author(name="DRY RUN PREVIEW")
                        embed.color = discord.Color.dark_grey()
                    await channel.send(embed=embed)

                # --- Pin the first message ---
                if not dry_run and first_message_for_pinning:
                    try:
                        await first_message_for_pinning.pin()
                        await self.config.guild(channel.guild).schedule_message_id.set(first_message_for_pinning.id)
                    except discord.errors.Forbidden:
                        await self._log_error(channel.guild, f"Missing permissions to pin messages in {channel.name}.")
                    except Exception as e:
                        await self._log_error(channel.guild, f"Error pinning message: {e}\n{traceback.format_exc()}")

        except Exception as e:
            await self._log_error(channel.guild, f"Error in post_schedule: {e}\n{traceback.format_exc()}")
            if dry_run:
                await channel.send(f"❌ Dry run failed due to an error. Check logs for details.")
            else:
                await channel.send(f"❌ An error occurred while posting the schedule. Please check the logs.")


    @commands.group(aliases=["tsched"])
    @commands.admin_or_permissions(manage_guild=True)
    async def twitchschedule(self, ctx):
        """Twitch Schedule Management Commands"""
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(
                title="Twitch Schedule Commands",
                color=discord.Color.purple(),
                description=(
                    f"`{ctx.clean_prefix}tsched setup` - Interactive setup process for the schedule channel and update times.\n"
                    f"`{ctx.clean_prefix}tsched force [next]` - Force an immediate schedule update.\n"
                    f"`{ctx.clean_prefix}tsched notify [@role/none]` - Set or clear notification role for schedule updates.\n"
                    f"`{ctx.clean_prefix}tsched events [number]` - Set number of events to show (1-10) on the image and as individual embeds.\n"
                    f"`{ctx.clean_prefix}tsched weeks [1-2]` - Set number of weeks to show in schedule (1 or 2).\n"
                    f"`{ctx.clean_prefix}tsched settings` - Show all current settings.\n"
                    f"`{ctx.clean_prefix}tsched test #channel` - Test post schedule to a channel.\n"
                    f"`{ctx.clean_prefix}tsched reload [url]` - Redownload template image and font files (optional: set custom template URL).\n"
                    f"`{ctx.clean_prefix}tsched setfont [url/none]` - Set or clear custom font URL for the schedule image.\n"
                    f"`{ctx.clean_prefix}tsched dryrun [#channel]` - Test post schedule without deleting/pinning messages.\n"
                    f"`{ctx.clean_prefix}tsched setlogchannel [#channel/none]` - Set channel for bot error logs.\n"
                )
            )
            await ctx.send(embed=embed)

    @twitchschedule.command(name="weeks")
    async def set_weeks(self, ctx, weeks: int):
        """Set the number of weeks to show in the schedule (1 or 2)."""
        if not 1 <= weeks <= 2:
            await ctx.send("❌ Weeks must be 1 or 2!")
            return
        await self.config.guild(ctx.guild).weeks_to_show.set(weeks)
        await self.config.guild(ctx.guild).include_next_week.set(weeks > 1)
        week_text = "week" if weeks == 1 else "weeks"
        await ctx.send(f"✅ Schedule will now show {weeks} {week_text}!")

    @twitchschedule.command(name="force")
    async def force_update(self, ctx, option: str = None):
        """Force an immediate schedule update to the configured channel. Use 'next' to show next week's image schedule."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        
        if not channel_id or not twitch_username:
            await ctx.send("❌ Please run setup first to configure the schedule channel and Twitch username!")
            return
            
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("❌ Configured schedule channel not found! Please re-run setup or check permissions.")
            return
            
        async with ctx.channel.typing():
            await ctx.send("🔄 Forcing schedule update... This might take a moment.")

            today_london = datetime.datetime.now(london_tz)
            start_date_for_image_param = None
            weeks_to_show = await self.config.guild(ctx.guild).weeks_to_show()
            
            if option and option.lower() == "next":
                # Calculate next week's Sunday for the image start date
                days_until_next_sunday = (6 - today_london.weekday() + 7) % 7
                if days_until_next_sunday == 0:
                    days_until_next_sunday = 7 # Ensures it's always the *next* Sunday
                start_date_for_image_param = today_london + timedelta(days=days_until_next_sunday)
                start_date_for_image_param = start_date_for_image_param.replace(hour=0, minute=0, second=0, microsecond=0)
                
                # Fetch streams up to the end of the specified weeks from next Sunday
                end_of_fetch_range = start_date_for_image_param + timedelta(days=weeks_to_show * 7 + 7)
            else:
                # Default behavior: image for current weeks, fetch streams for current + next week
                end_of_fetch_range = today_london + timedelta(days=max(14, weeks_to_show * 7 + 7))


            all_segments = await self.get_schedule_for_range(twitch_username, today_london, end_of_fetch_range)

            if all_segments is not None:
                await self.post_schedule(channel, all_segments, start_date_for_image=start_date_for_image_param)
            else:
                await ctx.send("❌ Failed to fetch schedule from Twitch! Check bot logs for details.")
                await self._log_error(ctx.guild, f"Force update failed for {twitch_username}. No schedule fetched.")

            if all_segments is not None: # Only confirm success if we actually got data
                await ctx.send("✅ Schedule updated!")

    @twitchschedule.command(name="setup")
    async def setup_schedule(self, ctx):
        """Interactive setup process for the Twitch schedule in a single channel."""
        await ctx.send("Starting interactive setup process for the **all-in-one schedule**... Please answer the following questions. You will have 30 seconds to respond to each question, or the setup will cancel.")
        
        # Channel ID
        channel = None
        for i in range(3):
            await ctx.send(f"**Step {i+1}/5:** Which channel should I post **all schedule updates (image, next stream, and individual streams)** in? (Mention the channel, e.g., `#schedule`)")
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
            await ctx.send(f"**Step {i+1}/5:** What's the Twitch username of the streamer whose schedule I should track? (e.g., `notelkz`)")
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

        # Number of weeks
        weeks = None
        for i in range(3):
            await ctx.send(f"**Step {i+1}/5:** How many weeks should the schedule show? (Enter `1` for current week only, or `2` for current and next week)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                try:
                    weeks = int(msg.content.strip())
                    if weeks not in [1, 2]:
                        await ctx.send("❌ Please enter either 1 or 2. Try again.")
                        continue
                    await self.config.guild(ctx.guild).weeks_to_show.set(weeks)
                    await self.config.guild(ctx.guild).include_next_week.set(weeks > 1)
                    break
                except ValueError:
                    await ctx.send("❌ Please enter a valid number (1 or 2). Try again.")
                    continue
            except asyncio.TimeoutError:
                await ctx.send("⌛ Setup timed out. Please try again from the beginning with `!tsched setup`.")
                return
        else:
            await ctx.send("Too many invalid attempts to set weeks. Setup cancelled.")
            return

        # Update Days
        days = []
        for i in range(3):
            await ctx.send(f"**Step {i+1}/5:** On which days should I automatically update the schedule? (Send numbers: `0=Monday, 1=Tuesday, ..., 6=Sunday`. Separate multiple days with spaces, e.g., `0 6` for Monday and Sunday)")
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
            await ctx.send(f"**Step {i+1}/5:** At what time (in London/UK time) should I update the schedule on the chosen days? (Use 24-hour format, e.g., `14:00` for 2 PM)")
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
            title="Schedule Setup Summary",
            description="Please confirm these settings:",
            color=discord.Color.blue()
        )
        days_map = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        update_days_str = ", ".join(days_map[d] for d in days) if days else "None"
        week_text = "week" if weeks == 1 else "weeks"

        confirm_embed.add_field(name="Schedule Channel", value=channel.mention if channel else "Not set", inline=False)
        confirm_embed.add_field(name="Twitch Username", value=username or "Not set", inline=False)
        confirm_embed.add_field(name="Weeks to Show", value=f"{weeks} {week_text}", inline=False)
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
                await ctx.send("✅ Setup complete! The schedule will be updated on the specified days and time in the single channel.")
            else:
                await ctx.send("❌ Setup cancelled by user.")
        except asyncio.TimeoutError:
            await ctx.send("⌛ Confirmation timed out. Setup cancelled.")
        except Exception as e:
            await ctx.send(f"❌ An error occurred during setup: {str(e)}")
            await self._log_error(ctx.guild, f"Error during setup confirmation: {e}\n{traceback.format_exc()}")


    @twitchschedule.command(name="notify")
    async def set_notify_role(self, ctx, role: discord.Role = None):
        """Set or clear the role to notify for schedule updates."""
        if role is None:
            await self.config.guild(ctx.guild).notify_role_id.set(None)
            await ctx.send("✅ Notification role cleared!")
        else:
            await self.config.guild(ctx.guild).notify_role_id.set(role.id)
            await ctx.send(f"✅ Notification role set to {role.mention}!")

    @twitchschedule.command(name="events")
    async def set_event_count(self, ctx, count: int):
        """Set the number of events to show (1-10) on the schedule image and as individual embeds."""
        if not 1 <= count <= 10:
            await ctx.send("❌ Event count must be between 1 and 10!")
            return
        await self.config.guild(ctx.guild).event_count.set(count)
        await ctx.send(f"✅ Event count set to {count}!")

    @twitchschedule.command(name="settings")
    async def show_settings(self, ctx):
        """Show current settings."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        update_days = await self.config.guild(ctx.guild).update_days()
        update_time = await self.config.guild(ctx.guild).update_time()
        notify_role_id = await self.config.guild(ctx.guild).notify_role_id()
        event_count = await self.config.guild(ctx.guild).event_count()
        custom_template_url = await self.config.guild(ctx.guild).custom_template_url()
        custom_font_url = await self.config.guild(ctx.guild).custom_font_url()
        log_channel_id = await self.config.guild(ctx.guild).log_channel_id()
        weeks_to_show = await self.config.guild(ctx.guild).weeks_to_show()
        
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
        embed.add_field(name="Event Count (on image & embeds)", value=str(event_count), inline=True)
        embed.add_field(name="Custom Template URL", value=custom_template_url or "Not set (using default)", inline=True)
        embed.add_field(name="Custom Font URL", value=custom_font_url or "Not set (using default)", inline=True)
        embed.add_field(name="Error Log Channel", value=log_channel.mention if log_channel else "Not set", inline=True)
        
        await ctx.send(embed=embed)

    @twitchschedule.command(name="test")
    async def test_post(self, ctx, channel: discord.TextChannel = None):
        """Test post schedule to a channel."""
        if channel is None:
            channel = ctx.channel
            
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        if not twitch_username:
            await ctx.send("❌ Please run setup first to configure the Twitch username!")
            return
            
        async with ctx.channel.typing():
            await ctx.send("🔄 Testing schedule post... This will post the current schedule.")
            today_london = datetime.datetime.now(london_tz)
            weeks_to_show = await self.config.guild(ctx.guild).weeks_to_show()
            end_of_fetch_range = today_london + timedelta(days=max(14, weeks_to_show * 7 + 7))
            all_segments = await self.get_schedule_for_range(twitch_username, today_london, end_of_fetch_range)

            if all_segments is not None:
                await self.post_schedule(channel, all_segments)
                await ctx.send("✅ Test complete!")
            else:
                await ctx.send("❌ Failed to fetch schedule from Twitch! Check bot logs for details.")
                await self._log_error(ctx.guild, f"Test post failed for {twitch_username}. No schedule fetched.")

    @twitchschedule.command(name="reload")
    async def reload_resources(self, ctx, template_url: str = None):
        """Force redownload of the template image and font files. Provide a URL to set a custom template."""
        async with ctx.channel.typing():
            await ctx.send("🔄 Redownloading resources...")
            
            # Clear existing files to force re-download
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
            
            # Update custom template URL if provided
            if template_url:
                await self.config.guild(ctx.guild).custom_template_url.set(template_url)
                await ctx.send(f"Set custom template URL to: {template_url}")
            else:
                await self.config.guild(ctx.guild).custom_template_url.set(None)
                await ctx.send("Reverting to default template URL.")
            
            # Now call ensure_resources to handle download based on current config
            resources_ready = await self.ensure_resources(ctx.guild)
            
            if resources_ready:
                await ctx.send("✅ Successfully redownloaded resources!")
            else:
                await ctx.send("❌ Failed to redownload some resources. Check logs for details.")

    @twitchschedule.command(name="setfont")
    async def set_font_url(self, ctx, font_url: str = None):
        """Set or clear the custom font URL for the schedule image. Use 'none' to revert to default."""
        async with ctx.channel.typing():
            if font_url and font_url.lower() == "none":
                font_url = None # Treat 'none' as clearing the URL

            if font_url:
                # Basic URL validation (can be more robust if needed)
                if not (font_url.startswith("http://") or font_url.startswith("https://")):
                    await ctx.send("❌ Invalid URL. Please provide a full HTTP or HTTPS URL.")
                    return
                await self.config.guild(ctx.guild).custom_font_url.set(font_url)
                await ctx.send(f"Set custom font URL to: {font_url}. Attempting to download font...")
            else:
                await self.config.guild(ctx.guild).custom_font_url.set(None)
                await ctx.send("Cleared custom font URL. Reverting to default font. Attempting to download default font...")
            
            # Clear existing font file to force re-download
            if os.path.exists(self.font_path):
                try:
                    os.remove(self.font_path)
                except Exception as e:
                    await self._log_error(ctx.guild, f"Failed to remove existing font file during setfont: {e}")

            # Now call ensure_resources to handle download based on current config
            resources_ready = await self.ensure_resources(ctx.guild)

            if resources_ready:
                await ctx.send("✅ Font updated successfully!")
            else:
                await ctx.send("❌ Failed to download font. Please check the URL and bot permissions. Check logs for details.")
                await self._log_error(ctx.guild, f"Font update failed via setfont command. URL: {font_url}")


    @twitchschedule.command(name="dryrun")
    async def dry_run_schedule(self, ctx, channel: discord.TextChannel = None):
        """Perform a dry run of the schedule post without deleting/pinning messages."""
        if channel is None:
            channel = ctx.channel
            
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        if not twitch_username:
            await ctx.send("❌ Please run setup first to configure the Twitch username!")
            return
            
        async with ctx.channel.typing():
            await ctx.send("🧪 Starting dry run... No messages will be deleted or pinned.")
            today_london = datetime.datetime.now(london_tz)
            weeks_to_show = await self.config.guild(ctx.guild).weeks_to_show()
            end_of_fetch_range = today_london + timedelta(days=max(14, weeks_to_show * 7 + 7))
            all_segments = await self.get_schedule_for_range(twitch_username, today_london, end_of_fetch_range)

            if all_segments is not None:
                await self.post_schedule(channel, all_segments, dry_run=True)
                await ctx.send("✅ Dry run complete! Check the specified channel for a preview. No actual changes were made.")
            else:
                await ctx.send("❌ Failed to fetch schedule from Twitch for dry run! Check bot logs for details.")
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


async def setup(bot: Red):
    await bot.add_cog(TwitchSchedule(bot))