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
            "update_interval": 3600,  # Update every hour by default
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

    async def download_file(self, url: str, save_path: str) -> bool:
        """Download a file if it doesn't exist."""
        if os.path.exists(save_path):
            return True
            
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        with open(save_path, 'wb') as f:
                            f.write(data)
                        return True
        except Exception as e:
            print(f"Error downloading file: {e}")
        return False

    async def ensure_resources(self):
        """Ensure template and font files are available."""
        font_url = "https://zerolivesleft.net/notelkz/P22.ttf"
        template_url = "https://zerolivesleft.net/notelkz/schedule.png"
        
        font_ok = await self.download_file(font_url, self.font_path)
        template_ok = await self.download_file(template_url, self.template_path)
        
        return font_ok and template_ok

    def get_next_sunday(self):
        """Get the date of the next Sunday."""
        today = datetime.datetime.now()
        days_ahead = 6 - today.weekday()  # Sunday is 6
        if days_ahead <= 0:  # If today is Sunday, get next Sunday
            days_ahead += 7
        next_sunday = today + timedelta(days=days_ahead)
        return next_sunday
    
    async def get_game_boxart(self, game_id, headers):
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

            # Load font
            date_font = ImageFont.truetype(self.font_path, 32)
            schedule_font = ImageFont.truetype(self.font_path, 24)

            # Add next week's date
            next_sunday = self.get_next_sunday()
            date_text = next_sunday.strftime("Week of %B %d")
            # Position in top right (adjust coordinates as needed)
            draw.text((500, 20), date_text, font=date_font, fill=(255, 255, 255))

            # Add schedule items
            y_start = 150  # Starting Y position for schedule items
            y_spacing = 50  # Space between items

            for segment in schedule:
                start_time = datetime.datetime.fromisoformat(segment["start_time"].replace("Z", "+00:00"))
                title = segment["title"]
                game = segment.get("category", {}).get("name", "")
                
                # Format: "DAY // TIME"
                day_time = start_time.strftime("%A // %I:%M%p").upper()
                
                # Draw the schedule line
                draw.text((50, y_start), day_time, font=schedule_font, fill=(255, 255, 255))
                draw.text((50, y_start + 25), f"{title}", font=schedule_font, fill=(255, 255, 255))
                
                y_start += y_spacing

            # Save to buffer
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return buf

        except Exception as e:
            print(f"Error generating schedule image: {e}")
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
        """Loop to periodically update the schedule"""
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild in self.bot.guilds:
                    channel_id = await self.config.guild(guild).channel_id()
                    twitch_username = await self.config.guild(guild).twitch_username()
                    update_interval = await self.config.guild(guild).update_interval()

                    if channel_id and twitch_username:
                        channel = guild.get_channel(channel_id)
                        if channel:
                            schedule = await self.get_schedule(twitch_username)
                            if schedule is not None:
                                await self.post_schedule(channel, schedule)
                            else:
                                print(f"Could not fetch schedule for {twitch_username}")

                await asyncio.sleep(update_interval)
            except Exception as e:
                tb = traceback.format_exc()
                print(f"Error in schedule update loop: {e}\n{tb}")
                await asyncio.sleep(60)

    async def post_schedule(self, channel: discord.TextChannel, schedule: list):
        """Post schedule with both image and embeds."""
        try:
            # First, update the schedule image
            await self.update_schedule_image(channel, schedule)

            # Delete previous schedule embeds (but not the pinned image)
            message_id = await self.config.guild(channel.guild).schedule_message_id()
            async for message in channel.history(limit=20):
                if message.author == self.bot.user and message.embeds:
                    if not message_id or message.id != message_id:
                        await message.delete()

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

    @twitchschedule.command(name="settings")
    async def settings(self, ctx):
        """Show current settings."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        update_interval = await self.config.guild(ctx.guild).update_interval()
        schedule_message_id = await self.config.guild(ctx.guild).schedule_message_id()

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
        embed.add_field(
            name="Update Interval",
            value=f"{update_interval // 3600} hours" if update_interval else "Not set",
            inline=False
        )
        embed.add_field(
            name="Schedule Image",
            value="Pinned" if schedule_message_id else "Not set",
            inline=False
        )

        await ctx.send(embed=embed)

    @twitchschedule.command(name="updateimage")
    async def updateimage(self, ctx):
        """Force update of just the schedule image."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        twitch_username = await self.config.guild(ctx.guild).twitch_username()

        if not channel_id or not twitch_username:
            await ctx.send("Please set both channel and username first!")
            return

        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("Cannot find the configured channel!")
            return

        status = await ctx.send("🔄 Updating schedule image...")
        schedule = await self.get_schedule(twitch_username)
        
        if schedule is not None:
            if await self.update_schedule_image(channel, schedule):
                await status.edit(content="✅ Schedule image updated!")
            else:
                await status.edit(content="❌ Failed to update schedule image")
        else:
            await status.edit(content="❌ Could not fetch schedule data")

    @twitchschedule.command(name="forceupdate")
    async def forceupdate(self, ctx):
        """Force an immediate schedule update."""
        try:
            print("\n=== FORCE UPDATE REQUESTED ===")
            channel_id = await self.config.guild(ctx.guild).channel_id()
            twitch_username = await self.config.guild(ctx.guild).twitch_username()

            print(f"Channel ID: {channel_id}")
            print(f"Twitch Username: {twitch_username}")

            if not channel_id or not twitch_username:
                await ctx.send("Please set both channel and username first!")
                print("❌ Missing channel or username configuration")
                return

            channel = ctx.guild.get_channel(channel_id)
            if not channel:
                await ctx.send("Cannot find the configured channel!")
                print("❌ Channel not found")
                return

            credentials = await self.get_credentials()
            if not credentials:
                await ctx.send("Twitch API credentials are not set! Please use `[p]settwitchcreds` to set them.")
                print("❌ No credentials found")
                return

            status_message = await ctx.send(f"🔄 Checking schedule for {twitch_username}...")
            print(f"Attempting fetch for user: {twitch_username}")

            schedule = await self.get_schedule(twitch_username)

            if schedule is not None:  # None means error, empty list means no schedule
                if len(schedule) > 0:
                    await self.post_schedule(channel, schedule)
                    await status_message.edit(content="✅ Schedule has been updated!")
                    print("✅ Schedule updated successfully")
                else:
                    await status_message.edit(
                        content=f"ℹ️ No upcoming scheduled streams found for {twitch_username}\n"
                        "This could mean:\n"
                        "1. The streamer hasn't set up any scheduled streams\n"
                        "2. All scheduled streams have already passed\n"
                        "3. The schedule is currently empty"
                    )
                    print("ℹ️ No scheduled streams found")
            else:
                error_msg = (
                    "❌ Error fetching schedule:\n"
                    f"1. Verified '{twitch_username}' exists? (Check capitalization)\n"
                    "2. API credentials are working (✓ confirmed)\n"
                    "3. The Twitch API is responding properly\n\n"
                    "Current settings:\n"
                    f"• Username: {twitch_username}\n"
                    f"• Channel: {channel.mention}\n\n"
                    "Check bot logs for detailed error information."
                )
                await status_message.edit(content=error_msg)
                print("❌ Failed to fetch schedule")

            print("=== END FORCE UPDATE ===\n")
        except Exception as e:
            tb = traceback.format_exc()
            print(f"Exception in forceupdate: {e}\n{tb}")
            await ctx.send(f"An error occurred: `{e}`\n```py\n{tb}```")

def setup(bot: Red):
    bot.add_cog(TwitchSchedule(bot))
