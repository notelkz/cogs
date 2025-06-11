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
            "event_count": 5
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
        date_font = ImageFont.truetype(self.font_path, 40)
        schedule_font = ImageFont.truetype(self.font_path, 42)
        
        # Get today's date and end of week date for the header
        today = datetime.datetime.now(london_tz)
        end_of_week = self.get_end_of_week()
        
        # Format the date range for display
        if today.month == end_of_week.month:
            # Same month (e.g., "March 1-5")
            date_text = f"{today.strftime('%B %d')}-{end_of_week.strftime('%d')}"
        else:
            # Different months (e.g., "March 29-April 4")
            date_text = f"{today.strftime('%B %d')}-{end_of_week.strftime('%B %d')}"
        
        draw.text((1600, 180), date_text, font=date_font, fill=(255, 255, 255))
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
            # Use dateutil.parser for robust ISO8601 parsing
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
            await ctx.send_help()

    @twitchschedule.command(name="setup")
    async def setup_schedule(self, ctx):
        """Interactive setup process for Twitch schedule."""
        if isinstance(ctx.channel, discord.DMChannel):
            await ctx.send("❌ Setup must be started in a server channel!")
            return

        await ctx.send("🔄 Starting setup process...")

        # 1. Set Twitch Username
        await ctx.send("Please enter the Twitch username to track:")
        try:
            msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                timeout=30.0
            )
            username = msg.content.lower()
            await self.config.guild(ctx.guild).twitch_username.set(username)
            await ctx.send(f"✅ Twitch username set to: {username}")
        except asyncio.TimeoutError:
            await ctx.send("❌ Setup timed out. Please try again.")
            return

        # 2. Set Discord Channel
        await ctx.send("Please mention the Discord channel where updates should be posted:")
        try:
            channel_msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                timeout=30.0
            )
            try:
                channel_id = channel_msg.channel_mentions[0].id
                update_channel_mention = channel_msg.channel_mentions[0].mention
                await self.config.guild(ctx.guild).channel_id.set(channel_id)
                await ctx.send(f"✅ Update channel set to: {update_channel_mention}")
            except (IndexError, AttributeError):
                await ctx.send("❌ Invalid channel. Please mention a channel like #channel-name")
                return
        except asyncio.TimeoutError:
            await ctx.send("❌ Setup timed out. Please try again.")
            return

        # 3. Set Update Day
        days_text = (
            "Which day should the schedule update? Type the number:\n"
            "1. Monday\n2. Tuesday\n3. Wednesday\n4. Thursday\n5. Friday\n6. Saturday\n7. Sunday"
        )
        await ctx.send(days_text)
        try:
            msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel and m.content in "1234567",
                timeout=30.0
            )
            update_day = int(msg.content) - 1
            await self.config.guild(ctx.guild).update_days.set([update_day])
            day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            await ctx.send(f"✅ Schedule will update every {day_names[update_day]}")
        except asyncio.TimeoutError:
            await ctx.send("❌ Setup timed out. Please try again.")
            return

        # 4. Set Update Time
        await ctx.send("What time should the schedule update? Use 24-hour format (e.g., 14:30):")
        try:
            time_msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                timeout=30.0
            )
            try:
                datetime.datetime.strptime(time_msg.content, "%H:%M")
                update_time_str = time_msg.content
                await self.config.guild(ctx.guild).update_time.set(update_time_str)
                await ctx.send(f"✅ Update time set to: {update_time_str}")
            except ValueError:
                await ctx.send("❌ Invalid time format. Please use HH:MM (e.g., 14:30)")
                return
        except asyncio.TimeoutError:
            await ctx.send("❌ Setup timed out. Please try again.")
            return

        # 5. Set Notification Role (Optional)
        await ctx.send("Optionally, mention a role to ping for schedule updates (or type `none` to skip):")
        try:
            role_msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                timeout=30.0
            )
            if role_msg.content.lower() == "none":
                await self.config.guild(ctx.guild).notify_role_id.set(None)
                await ctx.send("✅ No role will be pinged for schedule updates.")
            elif role_msg.role_mentions:
                role_id = role_msg.role_mentions[0].id
                await self.config.guild(ctx.guild).notify_role_id.set(role_id)
                await ctx.send(f"✅ Will ping <@&{role_id}> for schedule updates.")
            else:
                await ctx.send("❌ Invalid input. No role will be pinged.")
                await self.config.guild(ctx.guild).notify_role_id.set(None)
        except asyncio.TimeoutError:
            await ctx.send("⏰ No response, skipping notification role.")
            await self.config.guild(ctx.guild).notify_role_id.set(None)

        # 6. Set number of events to display
        await ctx.send("How many upcoming events should be listed in the schedule image? (1-10, default is 5):")
        try:
            msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                timeout=30.0
            )
            try:
                count = int(msg.content)
                if not 1 <= count <= 10:
                    raise ValueError
            except ValueError:
                count = 5
            await self.config.guild(ctx.guild).event_count.set(count)
            await ctx.send(f"✅ Will show up to {count} events in the schedule image.")
        except asyncio.TimeoutError:
            await self.config.guild(ctx.guild).event_count.set(5)
            await ctx.send("⏰ No response, defaulting to 5 events.")

        await ctx.send("✅ Setup complete! Use `[p]tsched force` to generate your first schedule.")

    @twitchschedule.command(name="force")
    async def force_update(self, ctx):
        """Force an immediate schedule update."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        if not channel_id or not twitch_username:
            await ctx.send("❌ Please run `[p]tsched setup` first.")
            return
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("❌ The configured channel no longer exists. Please run `[p]tsched setup` again.")
            return
        schedule = await self.get_schedule(twitch_username)
        if schedule is None:
            await ctx.send("❌ Could not fetch schedule from Twitch. Check your Twitch credentials and username.")
            return
        await self.post_schedule(channel, schedule)
        await ctx.send("✅ Schedule updated!")

    @twitchschedule.command(name="notify")
    async def set_notify(self, ctx, role: discord.Role = None):
        """Set or remove the role to ping for schedule updates."""
        await self.config.guild(ctx.guild).notify_role_id.set(role.id if role else None)
        if role:
            await ctx.send(f"✅ Schedule updates will ping {role.mention}")
        else:
            await ctx.send("✅ Schedule updates will not ping any role")

    @twitchschedule.command(name="events")
    async def set_event_count(self, ctx, count: int = None):
        """Set how many events to show in the schedule image (1-10)."""
        if count is None:
            current = await self.config.guild(ctx.guild).event_count()
            await ctx.send(f"Currently showing up to **{current}** events. Use `[p]tsched events <1-10>` to change.")
            return
        if not 1 <= count <= 10:
            await ctx.send("❌ Please choose a number between 1 and 10.")
            return
        await self.config.guild(ctx.guild).event_count.set(count)
        await ctx.send(f"✅ Will show up to {count} events in the schedule image.")

    @twitchschedule.command(name="help")
    async def show_help(self, ctx):
        """Show detailed help for Twitch Schedule commands."""
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="Twitch Schedule Help",
            color=discord.Color.purple(),
            description=(
                f"**{prefix}tsched setup** - Interactive setup\n"
                f"**{prefix}tsched force** - Force an immediate schedule update\n"
                f"**{prefix}tsched notify [@role/none]** - Set or clear notification role\n"
                f"**{prefix}tsched events [number]** - Set number of events to show\n"
                f"**{prefix}tsched settings** - Show current settings\n"
                f"**{prefix}tsched test #channel** - Test post schedule to a specific channel\n"
            )
        )
        await ctx.send(embed=embed)

    @twitchschedule.command(name="settings")
    async def settings(self, ctx):
        """Show current settings."""
        data = await self.config.guild(ctx.guild).all()
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        channel = ctx.guild.get_channel(data["channel_id"]) if data["channel_id"] else None
        role = ctx.guild.get_role(data["notify_role_id"]) if data["notify_role_id"] else None
        days = ", ".join(day_names[d] for d in data["update_days"]) if data["update_days"] else "Not set"
        embed = discord.Embed(
            title="Twitch Schedule Settings",
            color=discord.Color.purple()
        )
        embed.add_field(name="Twitch Username", value=data["twitch_username"] or "Not set", inline=False)
        embed.add_field(name="Channel", value=channel.mention if channel else "Not set", inline=False)
        embed.add_field(name="Update Days", value=days, inline=False)
        embed.add_field(name="Update Time", value=data["update_time"] or "Not set", inline=False)
        embed.add_field(name="Notify Role", value=role.mention if role else "None", inline=False)
        embed.add_field(name="Events Shown", value=data["event_count"], inline=False)
        await ctx.send(embed=embed)

    @twitchschedule.command(name="testsend")
    async def testsend(self, ctx):
        """Test if the bot can send messages in this channel."""
        await ctx.send("Test message! If you see this, the bot can send messages here.")
        
    @twitchschedule.command(name="test")
    async def test_schedule(self, ctx, channel: discord.TextChannel):
        """Test post the schedule to a specific channel."""
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        if not twitch_username:
            await ctx.send("❌ Please run `[p]tsched setup` first to set a Twitch username.")
            return
            
        await ctx.send(f"🔄 Fetching schedule for {twitch_username} and posting to {channel.mention}...")
        
        # Create a test schedule with varying number of events
        test_events = await self.get_schedule(twitch_username)
        
        if test_events is None:
            await ctx.send("❌ Could not fetch schedule from Twitch. Check your Twitch credentials and username.")
            return
            
        if not test_events:
            await ctx.send("⚠️ No scheduled events found for this Twitch channel.")
            
        # Post the schedule to the specified channel
        await self.post_schedule(channel, test_events)
        await ctx.send(f"✅ Test schedule posted to {channel.mention}!")

async def setup(bot: Red):
    await bot.add_cog(TwitchSchedule(bot))
