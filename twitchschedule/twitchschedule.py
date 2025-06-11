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
london_tz = pytz.timezone("Europe/London")
import dateutil.parser

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
            "timezone": None
        }
        self.config.register_guild(**default_guild)
        self.task = self.bot.loop.create_task(self.schedule_update_loop())
        self.access_token = None

        self.cache_dir = os.path.join(os.path.dirname(__file__), "cache")
        self.font_path = os.path.join(self.cache_dir, "P22.ttf")
        self.template_path = os.path.join(self.cache_dir, "schedule.png")
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

    async def ensure_resources(self):
        font_url = "https://zerolivesleft.net/notelkz/P22.ttf"
        template_url = "https://zerolivesleft.net/notelkz/schedule.png"
        if not os.path.exists(self.font_path):
            await self.download_file(font_url, self.font_path)
        if not os.path.exists(self.template_path):
            await self.download_file(template_url, self.template_path)
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
                
                # Filter segments to only include those before end of week (Saturday 11:59 PM)
                end_of_week = self.get_end_of_week()
                
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
        """Get the Twitch schedule for a specific date range."""
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
                    return data["data"][0]  # Contains 'box_art_url'
        return None

    async def generate_schedule_image(self, schedule: list, guild) -> io.BytesIO:
        if not await self.ensure_resources():
            return None
        
        # Open the template image
        img = Image.open(self.template_path)
        
        # Calculate how many events we'll actually display
        event_count = await self.config.guild(guild).event_count()
        actual_events = min(len(schedule), event_count)
        
        # If we have fewer than the maximum events, resize the image
        if actual_events < event_count:
            # Get original dimensions
            width, height = img.size
            
            # Calculate how much height to remove
            row_height = 150  # This matches your row_height in the drawing code
            height_to_remove = (event_count - actual_events) * row_height
            
            # Create a new image with adjusted height
            new_height = height - height_to_remove
            new_img = Image.new(img.mode, (width, new_height))
            
            # Copy the top portion of the template
            new_img.paste(img.crop((0, 0, width, 350)), (0, 0))
            
            # Copy only the needed rows for the events
            if actual_events > 0:
                event_section_height = actual_events * row_height
                new_img.paste(img.crop((0, 350, width, 350 + event_section_height)), (0, 350))
            
            # Copy the bottom portion of the template if needed
            if height > 350 + event_count * row_height:
                bottom_start = 350 + event_count * row_height
                bottom_height = height - bottom_start
                new_img.paste(img.crop((0, bottom_start, width, height)), (0, 350 + actual_events * row_height))
            
            # Use the resized image
            img = new_img
        
        draw = ImageDraw.Draw(img)
        title_font = ImageFont.truetype(self.font_path, 90)  # Increased to match "Schedule" size
        date_font = ImageFont.truetype(self.font_path, 40)   # Keep date size the same
        schedule_font = ImageFont.truetype(self.font_path, 42)
        
        # Get today's date and calculate the start of the week (Sunday)
        today = datetime.datetime.now(london_tz)
        days_since_sunday = today.weekday() + 1
        if days_since_sunday == 7:
            days_since_sunday = 0
        start_of_week = today - timedelta(days=days_since_sunday)
        start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Format the date text
        date_text = start_of_week.strftime("%B %d")
        
        # Calculate positions for right-aligned text
        width, _ = img.size
        right_margin = 100  # Distance from right edge
        
        # Get text widths to align properly
        week_of_text = "Week of"  # Changed from "WEEK OF" to "Week of"
        week_of_width, week_of_height = title_font.getsize(week_of_text) if hasattr(title_font, 'getsize') else title_font.getbbox(week_of_text)[2:4]
        date_width, date_height = date_font.getsize(date_text) if hasattr(date_font, 'getsize') else date_font.getbbox(date_text)[2:4]
        
        # Calculate positions (right-aligned)
        week_of_x = width - right_margin - week_of_width
        date_x = width - right_margin - date_width
        
        # Draw the text with adjusted Y positions
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

    async def generate_schedule_image_for_range(self, schedule: list, guild, start_date, end_date) -> io.BytesIO:
        """Generate a schedule image for a specific date range."""
        if not await self.ensure_resources():
            return None
        
        # Open the template image
        img = Image.open(self.template_path)
        
        # Calculate how many events we'll actually display
        event_count = await self.config.guild(guild).event_count()
        actual_events = min(len(schedule), event_count)
        
        # If we have fewer than the maximum events, resize the image
        if actual_events < event_count:
            # Get original dimensions
            width, height = img.size
            
            # Calculate how much height to remove
            row_height = 150  # This matches your row_height in the drawing code
            height_to_remove = (event_count - actual_events) * row_height
            
            # Create a new image with adjusted height
            new_height = height - height_to_remove
            new_img = Image.new(img.mode, (width, new_height))
            
            # Copy the top portion of the template
            new_img.paste(img.crop((0, 0, width, 350)), (0, 0))
            
            # Copy only the needed rows for the events
            if actual_events > 0:
                event_section_height = actual_events * row_height
                new_img.paste(img.crop((0, 350, width, 350 + event_section_height)), (0, 350))
            
            # Copy the bottom portion of the template if needed
            if height > 350 + event_count * row_height:
                bottom_start = 350 + event_count * row_height
                bottom_height = height - bottom_start
                new_img.paste(img.crop((0, bottom_start, width, height)), (0, 350 + actual_events * row_height))
            
            # Use the resized image
            img = new_img
        
        draw = ImageDraw.Draw(img)
        title_font = ImageFont.truetype(self.font_path, 90)  # Increased to match "Schedule" size
        date_font = ImageFont.truetype(self.font_path, 40)   # Keep date size the same
        schedule_font = ImageFont.truetype(self.font_path, 42)
        
        # Format the date text
        date_text = start_date.strftime("%B %d")
        
        # Calculate positions for right-aligned text
        width, _ = img.size
        right_margin = 100  # Distance from right edge
        
        # Get text widths to align properly
        week_of_text = "Week of"  # Changed from "WEEK OF" to "Week of"
        week_of_width, week_of_height = title_font.getsize(week_of_text) if hasattr(title_font, 'getsize') else title_font.getbbox(week_of_text)[2:4]
        date_width, date_height = date_font.getsize(date_text) if hasattr(date_font, 'getsize') else date_font.getbbox(date_text)[2:4]
        
        # Calculate positions (right-aligned)
        week_of_x = width - right_margin - week_of_width
        date_x = width - right_margin - date_width
        
        # Draw the text with adjusted Y positions
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
                                schedule = await self.get_schedule(twitch_username)
                                if schedule is not None:
                                    await self.post_schedule(channel, schedule)
                await asyncio.sleep(60)
            except Exception:
                await asyncio.sleep(60)

    async def post_schedule(self, channel: discord.TextChannel, schedule: list):
        try:
            notify_role_id = await self.config.guild(channel.guild).notify_role_id()
            notify_role = channel.guild.get_role(notify_role_id) if notify_role_id else None
            warning_content = "⚠️ Updating schedule - Previous schedule messages will be deleted in 10 seconds..."
            if notify_role:
                warning_content = f"{notify_role.mention}\n{warning_content}"
            warning_msg = await channel.send(warning_content)
            await asyncio.sleep(10)
            await warning_msg.delete()
            
            # Get only the last 10 messages from the bot
            bot_messages = []
            async for message in channel.history(limit=30):
                if message.author == self.bot.user and message.id != warning_msg.id:
                    bot_messages.append(message)
                    if len(bot_messages) >= 10:
                        break
            
            # Delete messages with a delay to avoid rate limits
            for message in bot_messages:
                try:
                    await message.delete()
                    await asyncio.sleep(1.5)  # 1.5 second delay between deletions
                except discord.errors.NotFound:
                    pass  # Message was already deleted
                except discord.errors.Forbidden:
                    break  # No permission to delete
                except Exception as e:
                    print(f"Error deleting message: {e}")
                    break
            
            await self.update_schedule_image(channel, schedule)
            event_count = await self.config.guild(channel.guild).event_count()
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
                if end_time:
                    end_dt = datetime.datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                    duration = end_dt - start_time
                    hours, remainder = divmod(duration.seconds, 3600)
                    minutes = remainder // 60
                    duration_str = f"{hours}h {minutes}m"
                else:
                    duration_str = "Unknown"
                twitch_username = segment.get("broadcaster_name")
                twitch_url = f"https://twitch.tv/{twitch_username}"
                embed = discord.Embed(
                    title=f"{title}",
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
                await channel.send(embed=embed)
                await asyncio.sleep(0.5)  # Small delay between sending embeds
                
            if not schedule:
                embed = discord.Embed(
                    title="No Upcoming Streams",
                    description="Check back later for new streams!",
                    color=discord.Color.purple()
                )
                await channel.send(embed=embed)
        except Exception as e:
            print(f"Error in post_schedule: {e}")
            traceback.print_exc()
    async def update_schedule_image(self, channel: discord.TextChannel, schedule: list):
        try:
            image_buf = await self.generate_schedule_image(schedule, channel.guild)
            if not image_buf:
                return False
            message_id = await self.config.guild(channel.guild).schedule_message_id()
            try:
                if message_id:
                    try:
                        old_message = await channel.fetch_message(message_id)
                        await old_message.delete()
                    except:
                        pass
                new_message = await channel.send(
                    file=discord.File(image_buf, filename="schedule.png")
                )
                try:
                    await new_message.pin()
                except:
                    pass
                await self.config.guild(channel.guild).schedule_message_id.set(new_message.id)
                return True
            except Exception as e:
                print(f"Error updating schedule message: {e}")
                return False
        except Exception as e:
            print(f"Error in update_schedule_image: {e}")
            return False

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
                    f"`{ctx.clean_prefix}tsched force` - Force an immediate schedule update\n"
                    f"`{ctx.clean_prefix}tsched notify [@role/none]` - Set or clear notification role\n"
                    f"`{ctx.clean_prefix}tsched events [number]` - Set number of events to show (1-10)\n"
                    f"`{ctx.clean_prefix}tsched settings` - Show current settings\n"
                    f"`{ctx.clean_prefix}tsched test #channel` - Test post schedule to a channel\n"
                    f"`{ctx.clean_prefix}tsched imgr DD/MM[/YYYY] [#channel]` - Generate schedule for specific week\n"
                    f"`{ctx.clean_prefix}tsched next [#channel]` - Show next week's schedule\n"
                    f"`{ctx.clean_prefix}tsched timezone [zone]` - Set timezone for list view\n"
                    f"`{ctx.clean_prefix}tsched list` - Show text-only schedule\n"
                    f"`{ctx.clean_prefix}tsched reload [url]` - Redownload template image and font files\n"
                )
            )
            await ctx.send(embed=embed)

    @twitchschedule.command(name="reload")
    async def reload_resources(self, ctx, template_url: str = None):
        """Force redownload of the template image and font files."""
        await ctx.send("🔄 Redownloading resources...")
        
        # Delete existing files if they exist
        if os.path.exists(self.font_path):
            os.remove(self.font_path)
        if os.path.exists(self.template_path):
            os.remove(self.template_path)
        
        # Use default URLs or custom URL if provided
        font_url = "https://zerolivesleft.net/notelkz/P22.ttf"
        default_template_url = "https://zerolivesleft.net/notelkz/schedule.png"
        
        # Use custom template URL if provided
        if template_url:
            await ctx.send(f"Using custom template URL: {template_url}")
        else:
            template_url = default_template_url
        
        font_success = await self.download_file(font_url, self.font_path)
        template_success = await self.download_file(template_url, self.template_path)
        
        if font_success and template_success:
            await ctx.send("✅ Successfully redownloaded resources!")
        else:
            await ctx.send("❌ Failed to redownload some resources. Please check the URLs and try again.")

    @twitchschedule.command(name="next")
    async def next_week_schedule(self, ctx, channel: discord.TextChannel = None):
        """Generate schedule image for next week."""
        if channel is None:
            channel = ctx.channel
            
        # Calculate next week's Sunday
        today = datetime.datetime.now(london_tz)
        days_until_next_sunday = (6 - today.weekday()) % 7 + 1
        next_sunday = today + timedelta(days=days_until_next_sunday)
        next_sunday = next_sunday.replace(hour=0, minute=0, second=0, microsecond=0)
        
        await self.image_range(ctx, next_sunday.strftime("%d/%m/%Y"), channel)

    @twitchschedule.command(name="timezone")
    async def set_timezone(self, ctx, timezone: str = None):
        """Set your preferred timezone for viewing stream times."""
        if timezone is None:
            current_tz = await self.config.guild(ctx.guild).timezone()
            await ctx.send(f"Current timezone is: {current_tz or 'BST (Default)'}")
            return
            
        if timezone.lower() == "list":
            common_tz = ["US/Eastern", "US/Central", "US/Pacific", "Europe/London", "Europe/Paris", 
                        "Australia/Sydney", "Asia/Tokyo", "Europe/Berlin"]
            await ctx.send("Common timezones:\n" + "\n".join(common_tz))
            return
            
        try:
            pytz.timezone(timezone)
            await self.config.guild(ctx.guild).timezone.set(timezone)
            await ctx.send(f"✅ Timezone set to: {timezone}")
        except pytz.exceptions.UnknownTimeZoneError:
            await ctx.send("❌ Invalid timezone. Use !tsched timezone list for common options.")

    @twitchschedule.command(name="list")
    async def list_schedule(self, ctx):
        """Show a text-only version of the schedule."""
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        if not twitch_username:
            await ctx.send("❌ Please run `[p]tsched setup` first.")
            return
            
        schedule = await self.get_schedule(twitch_username)
        if not schedule:
            await ctx.send("No scheduled streams found.")
            return
            
        embed = discord.Embed(
            title="Upcoming Streams",
            color=discord.Color.purple(),
            url=f"https://twitch.tv/{twitch_username}/schedule"
        )
        
        # Get user's timezone preference
        user_tz = await self.config.guild(ctx.guild).timezone()
        if user_tz:
            try:
                timezone = pytz.timezone(user_tz)
            except:
                timezone = london_tz
        else:
            timezone = london_tz
        
        for stream in schedule[:10]:  # Limit to 10 streams
            start_time = dateutil.parser.isoparse(stream["start_time"])
            start_time_local = start_time.astimezone(timezone)
            date_str = start_time_local.strftime("%A, %B %d at %I:%M %p")
            game = stream.get("category", {}).get("name", "No Category")
            
            # Calculate duration if end time is available
            duration_str = ""
            if stream.get("end_time"):
                end_time = dateutil.parser.isoparse(stream["end_time"])
                duration = end_time - start_time
                hours, remainder = divmod(duration.seconds, 3600)
                minutes = remainder // 60
                duration_str = f" ({hours}h {minutes}m)"
            
            embed.add_field(
                name=f"{date_str}{duration_str}",
                value=f"**{stream['title']}**\n{game}",
                inline=False
            )
        
        timezone_name = user_tz if user_tz else "BST (Default)"
        embed.set_footer(text=f"Times shown in {timezone_name}")
        await ctx.send(embed=embed)

    @twitchschedule.command(name="imgr")
    async def image_range(self, ctx, date_str: str, channel: discord.TextChannel = None):
        """Generate a schedule image for a specific week containing the given date."""
        if channel is None:
            channel = ctx.channel
            
        # Parse the date
        try:
            # Split the date string
            parts = date_str.split('/')
            
            # If only day and month provided, use current year
            if len(parts) == 2:
                day, month = map(int, parts)
                year = datetime.datetime.now(london_tz).year
            elif len(parts) == 3:
                day, month, year = map(int, parts)
            else:
                raise ValueError
                
            target_date = datetime.datetime(year, month, day, tzinfo=london_tz)
            
        except ValueError:
            await ctx.send("❌ Invalid date format. Please use DD/MM or DD/MM/YYYY (e.g., 08/06 or 08/06/2025)")
            return
            
        # Calculate the start of the week (Sunday)
        days_since_sunday = target_date.weekday() + 1
        if days_since_sunday == 7:
            days_since_sunday = 0
        start_date = target_date - timedelta(days=days_since_sunday)
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Calculate the end of the week (Saturday at 11:59 PM)
        end_date = start_date + timedelta(days=6)
        end_date = end_date.replace(hour=23, minute=59, second=59)
        
        # Get the Twitch username
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        if not twitch_username:
            await ctx.send("❌ Please run `[p]tsched setup` first.")
            return
            
        await ctx.send(f"🔄 Generating schedule for week of {start_date.strftime('%d/%m/%Y')}...")
        
        schedule = await self.get_schedule_for_range(twitch_username, start_date, end_date)
        
        if schedule is None:
            await ctx.send("❌ Could not fetch schedule from Twitch.")
            return
            
        if not schedule:
            await ctx.send(f"⚠️ No scheduled events found for the week of {start_date.strftime('%d/%m/%Y')}.")
            return
            
        image_buf = await self.generate_schedule_image_for_range(schedule, ctx.guild, start_date, end_date)
        if not image_buf:
            await ctx.send("❌ Failed to generate schedule image.")
            return
            
        await channel.send(
            f"📅 Schedule for week of {start_date.strftime('%d/%m/%Y')}:",
            file=discord.File(image_buf, filename="schedule.png")
        )
        await ctx.send(f"✅ Schedule image for week of {start_date.strftime('%d/%m/%Y')} posted to {channel.mention}!")

async def setup(bot: Red):
    await bot.add_cog(TwitchSchedule(bot))
