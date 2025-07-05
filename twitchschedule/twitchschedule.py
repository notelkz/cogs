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
    async def generate_schedule_image(self, schedule: list, guild, start_date=None) -> io.BytesIO:
        if not await self.ensure_resources():
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
        week_of_width, week_of_height = title_font.getsize(week_of_text) if hasattr(title_font, 'getsize') else title_font.getbbox(week_of_text)[2:4]
        date_width, date_height = date_font.getsize(date_text) if hasattr(date_font, 'getsize') else date_font.getbbox(date_text)[2:4]
        
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
                                # Calculate next week's date range
                                today = datetime.datetime.now(london_tz)
                                # (6 - today.weekday()) % 7 gives days until next Sunday if today is not Sunday
                                # If today is Sunday (weekday 6), (6-6)%7 = 0, so it would be current Sunday.
                                # To get *next* Sunday, we add 7 days to this calculation if it's already Sunday, or just add 1 day if it's currently Saturday.
                                # A more robust way to get next Sunday:
                                days_until_next_sunday = (6 - today.weekday() + 7) % 7
                                if days_until_next_sunday == 0: # if today is Sunday, we want next Sunday, so add 7 days
                                    days_until_next_sunday = 7
                                next_sunday = today + timedelta(days=days_until_next_sunday)
                                next_sunday = next_sunday.replace(hour=0, minute=0, second=0, microsecond=0)
                                next_saturday = next_sunday + timedelta(days=6)
                                next_saturday = next_saturday.replace(hour=23, minute=59, second=59)

                                schedule = await self.get_schedule_for_range(twitch_username, next_sunday, next_saturday)
                                if schedule is not None:
                                    await self.post_schedule(channel, schedule, start_date=next_sunday) # Pass start_date
                await asyncio.sleep(60)
            except Exception:
                await asyncio.sleep(60)

    async def post_schedule(self, channel: discord.TextChannel, schedule: list, start_date=None):
        try:
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
                    break
                except Exception as e:
                    print(f"Error deleting message: {e}")
                    break

            image_buf = await self.generate_schedule_image(schedule, channel.guild, start_date)
            if image_buf:
                schedule_message = await channel.send(
                    file=discord.File(image_buf, filename="schedule.png")
                )
                try:
                    await schedule_message.pin()
                    await self.config.guild(channel.guild).schedule_message_id.set(schedule_message.id)
                except:
                    pass

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

                await channel.send(embed=embed)
                await asyncio.sleep(0.5)

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
                    f"`{ctx.clean_prefix}tsched force [next]` - Force an immediate schedule update\n"
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

    @twitchschedule.command(name="force")
    async def force_update(self, ctx, option: str = None):
        """Force an immediate schedule update. Use 'next' to show next week's schedule."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        
        if not channel_id or not twitch_username:
            await ctx.send("❌ Please run setup first!")
            return
            
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("❌ Schedule channel not found!")
            return
            
        await ctx.send("🔄 Forcing schedule update...")

        if option and option.lower() == "next":
            # Calculate next week's date range
            today = datetime.datetime.now(london_tz)
            days_until_next_sunday = (6 - today.weekday()) % 7 + 1
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
            await ctx.send("✅ Schedule updated!")
        else:
            await ctx.send("❌ Failed to fetch schedule from Twitch!")

    @twitchschedule.command(name="setup")
    async def setup_schedule(self, ctx):
        """Interactive setup process for Twitch schedule."""
        try:
            await ctx.send("Starting setup process... Please answer the following questions.")
            
            await ctx.send("Which channel should I post the schedule in? (Mention the channel, e.g., #schedule)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                if not msg.channel_mentions:
                    await ctx.send("❌ No channel mentioned. Setup cancelled.")
                    return
                channel = msg.channel_mentions[0]
                await self.config.guild(ctx.guild).channel_id.set(channel.id)
            except asyncio.TimeoutError:
                await ctx.send("Setup timed out. Please try again.")
                return

            await ctx.send("What's your Twitch username?")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                username = msg.content.strip()
                await self.config.guild(ctx.guild).twitch_username.set(username)
            except asyncio.TimeoutError:
                await ctx.send("Setup timed out. Please try again.")
                return

            await ctx.send("Which days should I update the schedule? (Send numbers: 0=Monday, 6=Sunday, separate with spaces)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                try:
                    days = [int(x) for x in msg.content.split()]
                    if not all(0 <= x <= 6 for x in days):
                        await ctx.send("❌ Invalid days. Must be numbers between 0 and 6. Setup cancelled.")
                        return
                    await self.config.guild(ctx.guild).update_days.set(days)
                except ValueError:
                    await ctx.send("❌ Invalid input. Must be numbers. Setup cancelled.")
                    return
            except asyncio.TimeoutError:
                await ctx.send("Setup timed out. Please try again.")
                return

            await ctx.send("What time should I update the schedule? (Use 24-hour format, e.g., 14:00)")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                time = msg.content.strip()
                if not re.match(r"^([01]?[0-9]|2[0-3]):[0-5][0-9]$", time):
                    await ctx.send("❌ Invalid time format. Must be HH:MM. Setup cancelled.")
                    return
                await self.config.guild(ctx.guild).update_time.set(time)
            except asyncio.TimeoutError:
                await ctx.send("Setup timed out. Please try again.")
                return

            await ctx.send("✅ Setup complete! The schedule will be updated on the specified days and time.")
            
        except Exception as e:
            await ctx.send(f"❌ An error occurred during setup: {str(e)}")

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
        """Set the number of events to show (1-10)."""
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
        
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        notify_role = ctx.guild.get_role(notify_role_id) if notify_role_id else None
        
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        update_days_str = ", ".join(days[day] for day in update_days) if update_days else "None"
        
        embed = discord.Embed(
            title="Twitch Schedule Settings",
            color=discord.Color.purple()
        )
        embed.add_field(name="Channel", value=channel.mention if channel else "Not set", inline=True)
        embed.add_field(name="Twitch Username", value=twitch_username or "Not set", inline=True)
        embed.add_field(name="Update Time", value=update_time or "Not set", inline=True)
        embed.add_field(name="Update Days", value=update_days_str, inline=True)
        embed.add_field(name="Notify Role", value=notify_role.mention if notify_role else "Not set", inline=True)
        embed.add_field(name="Event Count", value=str(event_count), inline=True)
        
        await ctx.send(embed=embed)

    @twitchschedule.command(name="test")
    async def test_post(self, ctx, channel: discord.TextChannel = None):
        """Test post schedule to a channel."""
        if channel is None:
            channel = ctx.channel
            
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        if not twitch_username:
            await ctx.send("❌ Please run setup first!")
            return
            
        await ctx.send("🔄 Testing schedule post...")
        schedule = await self.get_schedule(twitch_username)
        if schedule is not None:
            await self.post_schedule(channel, schedule)
            await ctx.send("✅ Test complete!")
        else:
            await ctx.send("❌ Failed to fetch schedule from Twitch!")

    @twitchschedule.command(name="reload")
    async def reload_resources(self, ctx, template_url: str = None):
        """Force redownload of the template image and font files."""
        await ctx.send("🔄 Redownloading resources...")
        
        if os.path.exists(self.font_path):
            os.remove(self.font_path)
        if os.path.exists(self.template_path):
            os.remove(self.template_path)
        
        font_url = "https://zerolivesleft.net/notelkz/P22.ttf"
        default_template_url = "https://zerolivesleft.net/notelkz/schedule.png"
        
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

async def setup(bot: Red):
    await bot.add_cog(TwitchSchedule(bot))
