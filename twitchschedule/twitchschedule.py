import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import Optional
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
            "update_interval": 3600,  # Keep this for backward compatibility
            "update_days": [],        # List of days to update on (0-6, where 0 is Monday)
            "update_time": None,      # Time to update in HH:MM format
            "use_interval": True,     # Whether to use interval or specific days
            "schedule_message_id": None  # Store the pinned schedule message ID
        }
        self.config.register_guild(**default_guild)
        self.task = self.bot.loop.create_task(self.schedule_update_loop())
        self.access_token = None
        
        # Cache paths
        self.cache_dir = os.path.join(os.path.dirname(__file__), "cache")
        self.font_path = os.path.join(self.cache_dir, "P22.ttf")
        self.template_path = os.path.join(self.cache_dir, "schedule.png")
        
        # Create cache directory if it doesn't exist
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)

    def cog_unload(self):
        self.task.cancel()

    def get_next_sunday(self):
        """Get current Sunday if today is Sunday, otherwise get next Sunday."""
        today = datetime.datetime.now()
        days_until_sunday = (6 - today.weekday()) % 7  # 6 = Sunday
        return today + timedelta(days=days_until_sunday)
    async def get_credentials(self) -> Optional[tuple[str, str]]:
        """Get stored Twitch credentials"""
        tokens = await self.bot.get_shared_api_tokens("twitch")
        if tokens.get("client_id") and tokens.get("client_secret"):
            return tokens["client_id"], tokens["client_secret"]
        return None

    async def get_twitch_token(self):
        """Get OAuth token from Twitch"""
        print("\n=== GETTING TWITCH TOKEN ===")
        credentials = await self.get_credentials()
        if not credentials:
            print("❌ No credentials found")
            return None

        client_id, client_secret = credentials
        async with aiohttp.ClientSession() as session:
            url = "https://id.twitch.tv/oauth2/token"
            params = {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials"
            }
            try:
                async with session.post(url, params=params) as resp:
                    print(f"Token request status: {resp.status}")
                    if resp.status != 200:
                        error_text = await resp.text()
                        print(f"❌ Token error response: {error_text}")
                        return None
                    data = await resp.json()
                    if "access_token" not in data:
                        print(f"❌ No access token in response: {data}")
                        return None
                    print("✅ Successfully obtained access token")
                    return data.get("access_token")
            except Exception as e:
                print(f"❌ Error getting token: {str(e)}")
                return None
            finally:
                print("=== END TOKEN REQUEST ===\n")

    async def download_file(self, url: str, save_path: str) -> bool:
        """Download a file if it doesn't exist."""
        try:
            print(f"Attempting to download: {url}")
            print(f"Saving to: {save_path}")
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    print(f"Download status: {resp.status}")
                    if resp.status == 200:
                        data = await resp.read()
                        os.makedirs(os.path.dirname(save_path), exist_ok=True)
                        with open(save_path, 'wb') as f:
                            f.write(data)
                        print(f"Successfully downloaded to {save_path}")
                        return True
                    else:
                        print(f"Failed to download. Status: {resp.status}")
                        return False
        except Exception as e:
            print(f"Error downloading file: {e}")
            traceback.print_exc()
            return False

    async def ensure_resources(self):
        """Ensure template and font files are available."""
        print("\n=== DOWNLOADING RESOURCES ===")
        font_url = "https://zerolivesleft.net/notelkz/P22.ttf"
        template_url = "https://zerolivesleft.net/notelkz/schedule.png"
        
        # Force redownload by removing existing files
        try:
            if os.path.exists(self.font_path):
                os.remove(self.font_path)
                print(f"Removed existing font file: {self.font_path}")
            if os.path.exists(self.template_path):
                os.remove(self.template_path)
                print(f"Removed existing template file: {self.template_path}")
        except Exception as e:
            print(f"Error removing existing files: {e}")
        
        print(f"Cache directory: {self.cache_dir}")
        print(f"Font path: {self.font_path}")
        print(f"Template path: {self.template_path}")
        
        font_ok = await self.download_file(font_url, self.font_path)
        template_ok = await self.download_file(template_url, self.template_path)
        
        print(f"Font download success: {font_ok}")
        print(f"Template download success: {template_ok}")
        print("=== END DOWNLOADING RESOURCES ===\n")
        
        return font_ok and template_ok
    async def get_schedule(self, username: str):
        """Fetch schedule from Twitch API"""
        print("\n=== FETCHING TWITCH SCHEDULE ===")
        print(f"Username: {username}")

        credentials = await self.get_credentials()
        if not credentials:
            print("❌ No credentials found")
            return None

        if not self.access_token:
            print("Getting new access token...")
            self.access_token = await self.get_twitch_token()
            if not self.access_token:
                print("❌ Failed to get access token")
                return None

        client_id, _ = credentials
        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {self.access_token}"
        }

        # First, verify the user exists
        async with aiohttp.ClientSession() as session:
            user_url = f"https://api.twitch.tv/helix/users?login={username}"
            print(f"Verifying user exists: {user_url}")

            try:
                async with session.get(user_url, headers=headers) as resp:
                    print(f"User check status: {resp.status}")
                    user_data = await resp.json()
                    print(f"User data: {user_data}")

                    if resp.status != 200 or not user_data.get("data"):
                        print(f"❌ User {username} not found")
                        return None

                    broadcaster_id = user_data["data"][0]["id"]
                    broadcaster_name = user_data["data"][0]["login"]
                    print(f"Found broadcaster ID: {broadcaster_id}")

            except Exception as e:
                print(f"❌ Error checking user: {str(e)}")
                return None

        # Now fetch the schedule using broadcaster ID
        async with aiohttp.ClientSession() as session:
            url = f"https://api.twitch.tv/helix/schedule?broadcaster_id={broadcaster_id}"
            print(f"Requesting schedule: {url}")

            try:
                async with session.get(url, headers=headers) as resp:
                    print(f"Schedule response status: {resp.status}")
                    response_text = await resp.text()
                    print(f"Schedule response: {response_text}")

                    if resp.status == 404:
                        print(f"✓ User exists but has no schedule")
                        return []
                    elif resp.status != 200:
                        print(f"❌ Error response: {response_text}")
                        return None

                    try:
                        data = await resp.json()
                        print(f"Parsed schedule data: {data}")

                        if "data" not in data:
                            print("❌ No data field in response")
                            return []

                        segments = data.get("data", {}).get("segments", [])
                        print(f"Found {len(segments)} schedule segments")

                        # Attach broadcaster_name to each segment for later use
                        for seg in segments:
                            seg["broadcaster_name"] = broadcaster_name

                        if not segments:
                            print("✓ No scheduled streams found")
                            return []

                        return segments

                    except Exception as e:
                        print(f"❌ Error parsing response: {str(e)}")
                        return None

            except Exception as e:
                print(f"❌ Network error: {str(e)}")
                return None
            finally:
                print("=== END FETCH ===\n")

    async def get_game_boxart(self, game_id: str, headers: dict) -> Optional[str]:
        """Fetch the box art URL for a game from Twitch API."""
        if not game_id or not headers:
            return None
        url = f"https://api.twitch.tv/helix/games?id={game_id}"
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    if "data" in data and data["data"]:
                        boxart_url = data["data"][0].get("box_art_url")
                        # Twitch returns URLs with {width}x{height} placeholders
                        if boxart_url:
                            return boxart_url.replace("{width}", "285").replace("{height}", "380")
            except Exception as e:
                print(f"Error fetching boxart: {e}")
        return None
    async def generate_schedule_image(self, schedule: list) -> Optional[io.BytesIO]:
        """Generate schedule image using template."""
        try:
            # Ensure we have the required files
            if not await self.ensure_resources():
                print("Failed to download required resources")
                return None

            # Open template image
            img = Image.open(self.template_path)
            draw = ImageDraw.Draw(img)

            # Load fonts - adjusted for 1920x1080
            date_font = ImageFont.truetype(self.font_path, 40)  # For date
            schedule_font = ImageFont.truetype(self.font_path, 42)  # For schedule items

            # Add just the date text underneath the template's "Week of" text
            next_sunday = self.get_next_sunday()
            date_text = next_sunday.strftime("%B %d")  # Just the date

            # Position date text underneath "Week of" in template
            draw.text((1600, 180), date_text, font=date_font, fill=(255, 255, 255))

            # Schedule positioning for 1920x1080
            day_x = 125        # X position for day/time
            game_x = 125       # X position for game title
            initial_y = 350    # Starting Y position for first day
            row_height = 150   # Space between each row's start
            day_offset = -45   # How far above the bar the day text should be
            bar_height = 70    # Height of the semi-transparent bar

            # Add schedule items
            for i, segment in enumerate(schedule):
                if i >= 5:  # Only show up to 5 days
                    break

                bar_y = initial_y + (i * row_height)  # Position of the purple bar
                day_y = bar_y + day_offset           # Position day text above bar
                game_y = bar_y + 15                  # Position game text inside bar
                
                # Format the day and time
                start_time = datetime.datetime.fromisoformat(segment["start_time"].replace("Z", "+00:00"))
                day_time = start_time.strftime("%A // %I:%M%p").upper()
                title = segment["title"]

                # Draw day and time (above the purple bar)
                draw.text((day_x, day_y), day_time, font=schedule_font, fill=(255, 255, 255))
                
                # Draw game title (inside the purple bar)
                draw.text((game_x, game_y), title, font=schedule_font, fill=(255, 255, 255))

            # Save to buffer
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf

        except Exception as e:
            print(f"Error generating schedule image: {e}")
            traceback.print_exc()
            return None

    async def update_schedule_image(self, channel: discord.TextChannel, schedule: list):
        """Update or create the pinned schedule image."""
        try:
            # Generate new image
            image_buf = await self.generate_schedule_image(schedule)
            if not image_buf:
                return False

            # Get existing message ID
            message_id = await self.config.guild(channel.guild).schedule_message_id()
            
            try:
                if message_id:
                    # Try to get and delete old message
                    try:
                        old_message = await channel.fetch_message(message_id)
                        await old_message.delete()
                    except:
                        pass
                
                # Send new message
                new_message = await channel.send(
                    file=discord.File(image_buf, filename="schedule.png")
                )
                
                # Pin the new message
                try:
                    await new_message.pin()
                except:
                    print("Failed to pin schedule message")
                
                # Store the new message ID
                await self.config.guild(channel.guild).schedule_message_id.set(new_message.id)
                return True
                
            except Exception as e:
                print(f"Error updating schedule message: {e}")
                return False
                
        except Exception as e:
            print(f"Error in update_schedule_image: {e}")
            return False
    async def schedule_update_loop(self):
        """Loop to check for updates based on interval or specific days."""
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild in self.bot.guilds:
                    use_interval = await self.config.guild(guild).use_interval()
                    
                    if use_interval:
                        # Use the old interval-based update
                        update_interval = await self.config.guild(guild).update_interval()
                        await asyncio.sleep(update_interval)
                    else:
                        # Check if we should update based on day and time
                        update_days = await self.config.guild(guild).update_days()
                        update_time = await self.config.guild(guild).update_time()
                        
                        if not update_days or not update_time:
                            continue
                            
                        now = datetime.datetime.now()
                        current_day = now.weekday()
                        current_time = now.strftime("%H:%M")
                        
                        # If it's the right day and time, update
                        if current_day in update_days and current_time == update_time:
                            channel_id = await self.config.guild(guild).channel_id()
                            twitch_username = await self.config.guild(guild).twitch_username()
                            
                            if channel_id and twitch_username:
                                channel = guild.get_channel(channel_id)
                                if channel:
                                    schedule = await self.get_schedule(twitch_username)
                                    if schedule is not None:
                                        await self.post_schedule(channel, schedule)
                
                # Check every minute for day-based updates
                await asyncio.sleep(60)
                
            except Exception as e:
                tb = traceback.format_exc()
                print(f"Error in schedule update loop: {e}\n{tb}")
                await asyncio.sleep(60)

    async def post_schedule(self, channel: discord.TextChannel, schedule: list):
        """Post schedule with both image and embeds."""
        try:
            # First, warn about message deletion
            warning_msg = await channel.send("⚠️ Updating schedule - Previous schedule messages will be deleted in 10 seconds...")
            await asyncio.sleep(10)  # Wait 10 seconds

            # Delete the warning message
            await warning_msg.delete()

            # Delete all previous messages in the channel
            async for message in channel.history(limit=100):  # Increase limit if needed
                if message.author == self.bot.user:
                    await message.delete()

            # First, update the schedule image
            await self.update_schedule_image(channel, schedule)

            # Now continue with posting new schedule embeds
            credentials = await self.get_credentials()
            headers = None
            if credentials and self.access_token:
                client_id, _ = credentials
                headers = {
                    "Client-ID": client_id,
                    "Authorization": f"Bearer {self.access_token}"
                }

            future_streams = 0
            for segment in schedule:
                start_time = datetime.datetime.fromisoformat(segment["start_time"].replace("Z", "+00:00"))
                if start_time <= datetime.datetime.now(datetime.timezone.utc):
                    continue  # Skip past streams

                title = segment["title"]
                category = segment.get("category", {})
                game_name = category.get("name", "No Category")
                game_id = category.get("id")
                unix_ts = int(start_time.timestamp())
                time_str = f"<t:{unix_ts}:F>"

                # Duration
                end_time = segment.get("end_time")
                if end_time:
                    end_dt = datetime.datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                    duration = end_dt - start_time
                    hours, remainder = divmod(duration.seconds, 3600)
                    minutes = remainder // 60
                    duration_str = f"{hours}h {minutes}m"
                else:
                    duration_str = "Unknown"

                boxart_url = await self.get_game_boxart(game_id, headers) if headers and game_id else None

                # Twitch channel link
                twitch_username = segment.get("broadcaster_name")
                if not twitch_username:
                    twitch_username = await self.config.guild(channel.guild).twitch_username()
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
                if boxart_url:
                    embed.set_thumbnail(url=boxart_url)
                embed.set_footer(text=f"Scheduled Stream • {twitch_username}")

                await channel.send(embed=embed)
                future_streams += 1

            if future_streams == 0:
                embed = discord.Embed(
                    title="No Upcoming Streams",
                    description="Check back later for new streams!",
                    color=discord.Color.purple()
                )
                await channel.send(embed=embed)

        except Exception as e:
            print(f"Error in post_schedule: {e}")
            traceback.print_exc()
    @commands.command()
    async def testsend(self, ctx):
        """Test if the bot can send messages in this channel."""
        await ctx.send("Test message! If you see this, the bot can send messages here.")

    @commands.command()
    @commands.is_owner()
    async def settwitchcreds(self, ctx, client_id: str, client_secret: str):
        """Set Twitch API credentials. Only bot owner can use this."""
        await ctx.message.delete(delay=5)
        await self.bot.set_shared_api_tokens("twitch", 
            client_id=client_id,
            client_secret=client_secret
        )
        await ctx.send("Twitch API credentials have been set!", delete_after=5)

    @commands.command()
    @commands.is_owner()
    async def checktwitchcreds(self, ctx):
        """Check if Twitch credentials are properly set and working."""
        print("\n=== CHECKING TWITCH CREDENTIALS ===")
        await ctx.message.delete(delay=5)
        credentials = await self.get_credentials()
        if not credentials:
            await ctx.send("❌ No Twitch credentials found! Use `[p]settwitchcreds` to set them.", delete_after=10)
            print("❌ No credentials found")
            return

        client_id, client_secret = credentials
        masked_id = client_id[:6] + "*" * (len(client_id) - 6)
        masked_secret = client_secret[:6] + "*" * (len(client_secret) - 6)
        print(f"Testing credentials - Client ID: {masked_id}")

        token = await self.get_twitch_token()
        print(f"Token generation: {'Success' if token else 'Failed'}")

        embed = discord.Embed(
            title="Twitch Credentials Status",
            color=discord.Color.blue() if token else discord.Color.red()
        )
        embed.add_field(name="Client ID", value=f"Set: {masked_id}", inline=False)
        embed.add_field(name="Client Secret", value=f"Set: {masked_secret}", inline=False)
        embed.add_field(name="Token Generation", value="✅ Success" if token else "❌ Failed", inline=False)

        if token:
            test_url = "https://api.twitch.tv/helix/users?login=ninja"
            headers = {
                "Client-ID": client_id,
                "Authorization": f"Bearer {token}"
            }
            print(f"Testing API with headers: {headers}")

            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(test_url, headers=headers) as resp:
                        status = resp.status
                        response_text = await resp.text()
                        print(f"API Test Status: {status}")
                        print(f"API Test Response: {response_text}")

                        if status == 200:
                            embed.add_field(
                                name="API Test",
                                value="✅ API Connection Successful",
                                inline=False
                            )
                        elif status == 401:
                            embed.add_field(
                                name="API Test",
                                value="❌ Authentication Failed - Invalid Credentials",
                                inline=False
                            )
                        elif status == 400:
                            embed.add_field(
                                name="API Test",
                                value="❌ Bad Request - Please verify Client ID format",
                                inline=False
                            )
                        else:
                            embed.add_field(
                                name="API Test",
                                value=f"❌ Failed (Status: {status})\nResponse: {response_text[:100]}",
                                inline=False
                            )
                except Exception as e:
                    print(f"API Test Error: {str(e)}")
                    embed.add_field(
                        name="API Test",
                        value=f"❌ Connection Error: {str(e)}",
                        inline=False
                    )

        print("=== END CREDENTIALS CHECK ===\n")
        await ctx.send(embed=embed, delete_after=15)

    @commands.group(aliases=["tsched"])
    @commands.admin_or_permissions(manage_guild=True)
    async def twitchschedule(self, ctx):
        """Manage Twitch schedule settings."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @twitchschedule.command(name="setchannel")
    async def setchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel for schedule updates."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"Schedule updates will be posted in {channel.mention}")

    @twitchschedule.command(name="setuser")
    async def setuser(self, ctx, username: str):
        """Set the Twitch username to track."""
        await self.config.guild(ctx.guild).twitch_username.set(username.lower())
        await ctx.send(f"Now tracking schedule for {username}")

    @twitchschedule.command(name="setinterval")
    async def setinterval(self, ctx, hours: int):
        """Set how often to update the schedule (in hours)."""
        if hours < 1:
            await ctx.send("Interval must be at least 1 hour")
            return
        await self.config.guild(ctx.guild).update_interval.set(hours * 3600)
        await ctx.send(f"Schedule will update every {hours} hours")

    @twitchschedule.command(name="setdays")
    async def setdays(self, ctx, *days: str):
        """Set which days to update the schedule. Use MON, TUE, WED, THU, FRI, SAT, SUN"""
        day_map = {
            "MON": 0, "TUE": 1, "WED": 2, "THU": 3,
            "FRI": 4, "SAT": 5, "SUN": 6
        }
        
        update_days = []
        for day in days:
            day = day.upper()
            if day in day_map:
                update_days.append(day_map[day])
            else:
                await ctx.send(f"Invalid day: {day}. Use MON, TUE, WED, THU, FRI, SAT, SUN")
                return
        
        await self.config.guild(ctx.guild).update_days.set(update_days)
        await self.config.guild(ctx.guild).use_interval.set(False)
        
        day_names = [list(day_map.keys())[list(day_map.values()).index(d)] for d in update_days]
        await ctx.send(f"Schedule will update on: {', '.join(day_names)}")

    @twitchschedule.command(name="settime")
    async def settime(self, ctx, time: str):
        """Set what time to update the schedule. Use 24-hour format (HH:MM)"""
        try:
            # Validate time format
            datetime.datetime.strptime(time, "%H:%M")
            await self.config.guild(ctx.guild).update_time.set(time)
            await self.config.guild(ctx.guild).use_interval.set(False)
            await ctx.send(f"Schedule will update at {time}")
        except ValueError:
            await ctx.send("Invalid time format. Please use HH:MM (24-hour format)")

    @twitchschedule.command(name="useinterval")
    async def useinterval(self, ctx, use_interval: bool):
        """Switch between interval-based and day-based updates"""
        await self.config.guild(ctx.guild).use_interval.set(use_interval)
        if use_interval:
            interval = await self.config.guild(ctx.guild).update_interval()
            await ctx.send(f"Using interval-based updates every {interval // 3600} hours")
        else:
            days = await self.config.guild(ctx.guild).update_days()
            time = await self.config.guild(ctx.guild).update_time()
            if days and time:
                day_map = {
                    0: "MON", 1: "TUE", 2: "WED", 3: "THU",
                    4: "FRI", 5: "SAT", 6: "SUN"
                }
                day_names = [day_map[d] for d in days]
                await ctx.send(f"Using day-based updates on {', '.join(day_names)} at {time}")
            else:
                await ctx.send("Please set update days and time using `setdays` and `settime`")

    @twitchschedule.command(name="settings")
    async def settings(self, ctx):
        """Show current settings."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        use_interval = await self.config.guild(ctx.guild).use_interval()
        
        channel = ctx.guild.get_channel(channel_id) if channel_id else None

        embed = discord.Embed(
            title="Twitch Schedule Settings",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="Channel",
            value=channel.mention if channel else "Not set",
            inline=False
        )
        embed.add_field(
            name="Twitch Username",
            value=twitch_username or "Not set",
            inline=False
        )
        
        if use_interval:
            update_interval = await self.config.guild(ctx.guild).update_interval()
            embed.add_field(
                name="Update Method",
                value=f"Interval: Every {update_interval // 3600} hours",
                inline=False
            )
        else:
            update_days = await self.config.guild(ctx.guild).update_days()
            update_time = await self.config.guild(ctx.guild).update_time()
            day_map = {
                0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday",
                4: "Friday", 5: "Saturday", 6: "Sunday"
            }
            day_names = [day_map[d] for d in update_days] if update_days else ["Not set"]
            embed.add_field(
                name="Update Method",
                value=f"Days: {', '.join(day_names)}\nTime: {update_time or 'Not set'}",
                inline=False
            )

        await ctx.send(embed=embed)

async def setup(bot: Red):
    await bot.add_cog(TwitchSchedule(bot))
