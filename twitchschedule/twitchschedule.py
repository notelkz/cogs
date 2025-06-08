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
            "notify_role_id": None
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
        today = datetime.datetime.now()
        days_until_sunday = (6 - today.weekday()) % 7
        return today + timedelta(days=days_until_sunday)

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
                for seg in segments:
                    seg["broadcaster_name"] = broadcaster_name
                return segments

    async def generate_schedule_image(self, schedule: list) -> io.BytesIO:
        if not await self.ensure_resources():
            return None
        img = Image.open(self.template_path)
        draw = ImageDraw.Draw(img)
        date_font = ImageFont.truetype(self.font_path, 40)
        schedule_font = ImageFont.truetype(self.font_path, 42)
        next_sunday = self.get_next_sunday()
        date_text = next_sunday.strftime("%B %d")
        draw.text((1600, 180), date_text, font=date_font, fill=(255, 255, 255))
        day_x = 125
        game_x = 125
        initial_y = 350
        row_height = 150
        day_offset = -45
        for i, segment in enumerate(schedule):
            if i >= 5:
                break
            bar_y = initial_y + (i * row_height)
            day_y = bar_y + day_offset
            game_y = bar_y + 15
            start_time = datetime.datetime.fromisoformat(segment["start_time"].replace("Z", "+00:00"))
            day_time = start_time.strftime("%A // %I:%M%p").upper()
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
                    now = datetime.datetime.now()
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
            async for message in channel.history(limit=100):
                if message.author == self.bot.user:
                    await message.delete()
            await self.update_schedule_image(channel, schedule)
            # Post embeds for each schedule item
            for segment in schedule:
                start_time = datetime.datetime.fromisoformat(segment["start_time"].replace("Z", "+00:00"))
                title = segment["title"]
                category = segment.get("category", {})
                game_name = category.get("name", "No Category")
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
                await channel.send(embed=embed)
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
            image_buf = await self.generate_schedule_image(schedule)
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

        # Check if already set up
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        channel_id = await self.config.guild(ctx.guild).channel_id()
        update_days = await self.config.guild(ctx.guild).update_days()
        update_time = await self.config.guild(ctx.guild).update_time()
        if twitch_username and channel_id and update_days and update_time:
            channel = ctx.guild.get_channel(channel_id)
            day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            days_str = ", ".join(day_names[d] for d in update_days)
            await ctx.send(
                f"❌ Setup has already been completed for this server!\n"
                f"**Twitch:** `{twitch_username}`\n"
                f"**Channel:** {channel.mention if channel else channel_id}\n"
                f"**Days:** {days_str}\n"
                f"**Time:** {update_time}\n"
                f"Use `[p]tsched force` to update, or `[p]tsched notify` to change notification role."
            )
            return

        await ctx.send("🔄 Starting setup process...")

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
            "1. Monday\n"
            "2. Tuesday\n"
            "3. Wednesday\n"
            "4. Thursday\n"
            "5. Friday\n"
            "6. Saturday\n"
            "7. Sunday"
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
            msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                timeout=30.0
            )
            try:
                datetime.datetime.strptime(msg.content, "%H:%M")
                await self.config.guild(ctx.guild).update_time.set(msg.content)
                await ctx.send(f"✅ Update time set to: {msg.content}")
            except ValueError:
                await ctx.send("❌ Invalid time format. Please use HH:MM (e.g., 14:30)")
                return
        except asyncio.TimeoutError:
            await ctx.send("❌ Setup timed out. Please try again.")
            return

        # 5. Set Notification Role (Optional)
        await ctx.send("Would you like to ping a role when the schedule updates? Reply with 'yes' or 'no':")
        try:
            msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ['yes', 'no'],
                timeout=30.0
            )
            
            if msg.content.lower() == 'yes':
                await ctx.send("Please mention the role to ping (or type 'cancel' to skip):")
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                
                if msg.content.lower() != 'cancel':
                    try:
                        role = msg.role_mentions[0]
                        await self.config.guild(ctx.guild).notify_role_id.set(role.id)
                        await ctx.send(f"✅ Notification role set to: {role.mention}")
                    except (IndexError, AttributeError):
                        await ctx.send("❌ No valid role mentioned. Notifications will be disabled.")
                        await self.config.guild(ctx.guild).notify_role_id.set(None)
                else:
                    await self.config.guild(ctx.guild).notify_role_id.set(None)
            else:
                await self.config.guild(ctx.guild).notify_role_id.set(None)
                
        except asyncio.TimeoutError:
            await ctx.send("Skipping notification setup due to timeout.")
            await self.config.guild(ctx.guild).notify_role_id.set(None)

        # Setup Complete
        embed = discord.Embed(
            title="✅ Setup Complete!",
            color=discord.Color.green(),
            description="Your schedule updates have been configured."
        )
        embed.add_field(
            name="Settings",
            value=(
                f"📺 Twitch Channel: {username}\n"
                f"📝 Discord Channel: {msg.channel_mentions[0].mention}\n"
                f"📅 Update Day: {day_names[update_day]}\n"
                f"⏰ Update Time: {msg.content}"
            ),
            inline=False
        )
        embed.add_field(
            name="Next Steps",
            value=(
                "• Use `[p]tsched force` to generate your first schedule\n"
                "• The schedule will automatically update every week"
            ),
            inline=False
        )
        await ctx.send(embed=embed)

    @twitchschedule.command(name="force")
    async def force_update(self, ctx):
        """Force an immediate schedule update."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        twitch_username = await self.config.guild(ctx.guild).twitch_username()

        if not channel_id or not twitch_username:
            await ctx.send("❌ Schedule not configured! Use `[p]tsched setup` first.")
            return

        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("❌ Cannot find the configured channel!")
            return

        status = await ctx.send("🔄 Updating schedule...")
        schedule = await self.get_schedule(twitch_username)
        
        if schedule is not None:
            await self.post_schedule(channel, schedule)
            await status.edit(content="✅ Schedule has been updated!")
        else:
            await status.edit(content="❌ Could not fetch schedule data")

    @twitchschedule.command(name="notify")
    async def set_notify(self, ctx, role: discord.Role = None):
        """Set or remove the role to ping for schedule updates.
        
        Examples:
        [p]tsched notify @Schedule-Updates  - Set role to ping
        [p]tsched notify                    - Remove notification role
        """
        await self.config.guild(ctx.guild).notify_role_id.set(role.id if role else None)
        if role:
            await ctx.send(f"✅ Schedule updates will ping {role.mention}")
        else:
            await ctx.send("✅ Schedule updates will not ping any role")

    @twitchschedule.command(name="help")
    async def show_help(self, ctx):
        """Show detailed help for Twitch Schedule commands."""
        embed = discord.Embed(
            title="Twitch Schedule Help",
            color=discord.Color.blue(),
            description="Available commands:"
        )
        
        embed.add_field(
            name="🔧 Setup",
            value="`[p]tsched setup` - Interactive setup process",
            inline=False
        )
        
        embed.add_field(
            name="🔄 Updates",
            value="`[p]tsched force` - Force an immediate schedule update",
            inline=False
        )
        
        embed.add_field(
            name="🔔 Notifications",
            value="`[p]tsched notify [@role]` - Set/remove notification role",
            inline=False
        )
        
        embed.add_field(
            name="ℹ️ Help",
            value="`[p]tsched help` - Show this help message",
            inline=False
        )

        await ctx.send(embed=embed)

async def setup(bot: Red):
    await bot.add_cog(TwitchSchedule(bot))
