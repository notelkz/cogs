import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
import aiohttp
import asyncio
from datetime import datetime, timedelta
import traceback
from typing import Optional
import pytz
london_tz = pytz.timezone("Europe/London")
import dateutil.parser
from PIL import Image, ImageDraw, ImageFont
import io
import os

class TwitchManager(commands.Cog):
    """Comprehensive Twitch integration for Discord - Live announcements and schedule management"""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        
        # Combined default settings
        default_guild = {
            # Announcer settings
            "announcement_channel": None,
            "ping_roles": [],
            "streamers": {},  # {twitch_name: {"discord_id": id, "last_announced": timestamp}}
            "client_id": None,
            "client_secret": None,
            "access_token": None,
            "token_expires": None,
            "check_frequency": 300,  # Default 5 minutes
            
            # Schedule settings
            "schedule_channel_id": None,
            "twitch_username": None,
            "update_days": [],
            "update_time": None,
            "schedule_message_id": None,
            "notify_role_id": None,
            "event_count": 5
        }
        
        self.config.register_guild(**default_guild)
        
        # Start background tasks
        self.check_streams_task = self.bot.loop.create_task(self.check_streams_loop())
        self.schedule_update_task = self.bot.loop.create_task(self.schedule_update_loop())
        
        # Rate limiting
        self.rate_limiter = commands.CooldownMapping.from_cooldown(
            1, 1, commands.BucketType.guild
        )  # 1 request per second per guild
        
        # Schedule resources
        self.cache_dir = os.path.join(os.path.dirname(__file__), "cache")
        self.font_path = os.path.join(self.cache_dir, "P22.ttf")
        self.template_path = os.path.join(self.cache_dir, "schedule.png")
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)

    def cog_unload(self):
        if self.check_streams_task:
            self.check_streams_task.cancel()
        if self.schedule_update_task:
            self.schedule_update_task.cancel()

    #
    # SHARED API METHODS
    #
    
    async def get_credentials(self, guild):
        """Get Twitch API credentials either from guild config or shared API tokens"""
        # First try guild-specific credentials
        client_id = await self.config.guild(guild).client_id()
        client_secret = await self.config.guild(guild).client_secret()
        
        # If not available, try shared API tokens
        if not client_id or not client_secret:
            tokens = await self.bot.get_shared_api_tokens("twitch")
            if tokens.get("client_id") and tokens.get("client_secret"):
                client_id = tokens["client_id"]
                client_secret = tokens["client_secret"]
        
        if client_id and client_secret:
            return client_id, client_secret
        return None, None

    async def get_twitch_headers(self, guild):
        """Get valid Twitch API headers."""
        try:
            now = datetime.utcnow().timestamp()
            token_expires = await self.config.guild(guild).token_expires()
            access_token = await self.config.guild(guild).access_token()
            client_id, client_secret = await self.get_credentials(guild)

            if not client_id or not client_secret:
                print(f"[DEBUG] Missing client ID or secret for guild {guild.id}")
                return None

            # Always get a new token if we don't have one or if it's expired
            if not token_expires or not access_token or now >= token_expires:
                print(f"[DEBUG] Getting new token for guild {guild.id}")
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        "https://id.twitch.tv/oauth2/token",
                        params={
                            "client_id": client_id,
                            "client_secret": client_secret,
                            "grant_type": "client_credentials"
                        }
                    ) as resp:
                        if resp.status != 200:
                            error_text = await resp.text()
                            print(f"[DEBUG] Token request failed: Status {resp.status}, Response: {error_text}")
                            return None
                        
                        data = await resp.json()
                        access_token = data["access_token"]
                        expires_in = data["expires_in"]
                        
                        # Store the new token
                        await self.config.guild(guild).access_token.set(access_token)
                        await self.config.guild(guild).token_expires.set(now + expires_in)
                        print(f"[DEBUG] New token obtained for guild {guild.id}")

            headers = {
                "Client-ID": client_id,
                "Authorization": f"Bearer {access_token}"
            }
            
            return headers

        except Exception as e:
            print(f"[DEBUG] Error in get_twitch_headers: {str(e)}")
            return None

    #
    # LIVE ANNOUNCER METHODS
    #
    
    async def check_streams_loop(self):
        """Loop to check if streamers are live."""
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild in self.bot.guilds:
                    await self.check_guild_streams(guild)
                
                # Sleep after checking all guilds
                await asyncio.sleep(60)  # Check every minute which guild to process
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                print(f"Network error in stream check loop: {e}")
                await asyncio.sleep(60)
            except Exception as e:
                print(f"Unexpected error in stream check loop: {e}")
                await asyncio.sleep(60)

    async def check_guild_streams(self, guild):
        """Check streams for a specific guild."""
        try:
            streamers = await self.config.guild(guild).streamers()
            if not streamers:
                return

            headers = await self.get_twitch_headers(guild)
            if not headers:
                print(f"[DEBUG] No valid headers for guild {guild.id}")
                return

            # Apply rate limiting
            bucket = self.rate_limiter.get_bucket(discord.Object(id=guild.id))
            retry_after = bucket.update_rate_limit()
            if retry_after:
                await asyncio.sleep(retry_after)

            check_frequency = await self.config.guild(guild).check_frequency()
            
            async with aiohttp.ClientSession() as session:
                for twitch_name in streamers:
                    try:
                        url = f"https://api.twitch.tv/helix/streams?user_login={twitch_name}"
                        
                        async with session.get(url, headers=headers) as resp:
                            if resp.status == 401:
                                print("[DEBUG] Got 401 - Clearing token and retrying")
                                # Clear the token so it will be refreshed next time
                                await self.config.guild(guild).access_token.set(None)
                                await self.config.guild(guild).token_expires.set(None)
                                return
                                
                            if resp.status != 200:
                                print(f"[DEBUG] Twitch API error for {twitch_name}: {resp.status}")
                                continue
                                
                            data = await resp.json()
                            
                            is_live = bool(data["data"])
                            last_announced = streamers[twitch_name].get("last_announced", 0)
                            
                            if is_live and data["data"][0]["started_at"] != last_announced:
                                await self.announce_stream(guild, twitch_name, data["data"][0])
                                streamers[twitch_name]["last_announced"] = data["data"][0]["started_at"]
                                await self.config.guild(guild).streamers.set(streamers)
                                
                    except Exception as e:
                        print(f"[DEBUG] Error checking stream {twitch_name}: {str(e)}")
                        continue
                        
            # Sleep for the configured check frequency before checking the next guild
            await asyncio.sleep(check_frequency)
            
        except Exception as e:
            print(f"[DEBUG] Error in check_guild_streams: {str(e)}")
    
    async def announce_stream(self, guild, twitch_name, stream_data):
        """Announce a live stream."""
        channel_id = await self.config.guild(guild).announcement_channel()
        if not channel_id:
            print(f"[DEBUG] No announcement channel set for guild {guild.id}")
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            print(f"[DEBUG] Could not find channel with ID {channel_id} in guild {guild.id}")
            return

        # Get roles to ping
        ping_roles = await self.config.guild(guild).ping_roles()
        
        # Debug logging for role mentions
        print(f"[DEBUG] Announcing stream for {twitch_name}")
        print(f"[DEBUG] Ping roles: {ping_roles}")
        
        # Check if roles exist and are mentionable
        valid_roles = []
        for role_id in ping_roles:
            role = guild.get_role(role_id)
            if role:
                print(f"[DEBUG] Found role {role.name} (ID: {role_id})")
                if role.mentionable:
                    print(f"[DEBUG] Role {role.name} is mentionable")
                    valid_roles.append(role_id)
                else:
                    print(f"[DEBUG] Role {role.name} is NOT mentionable")
            else:
                print(f"[DEBUG] Could not find role with ID {role_id}")
        
        role_mentions = " ".join(f"<@&{role_id}>" for role_id in valid_roles)
        print(f"[DEBUG] Role mentions string: '{role_mentions}'")

        embed = discord.Embed(
            title=stream_data["title"],
            url=f"https://twitch.tv/{twitch_name}",
            color=discord.Color.purple(),
            timestamp=datetime.now()
        )
        
        embed.set_author(
            name=f"{twitch_name} is now live on Twitch!",
            icon_url="https://static.twitchcdn.net/assets/favicon-32-d6025c14e900565d6177.png"
        )
        
        embed.add_field(
            name="Playing",
            value=stream_data.get("game_name", "Unknown"),
            inline=True
        )
        
        embed.add_field(
            name="Viewers",
            value=str(stream_data.get("viewer_count", 0)),
            inline=True
        )

        if stream_data.get("thumbnail_url"):
            try:
                thumbnail = stream_data["thumbnail_url"]
                timestamp = int(datetime.now().timestamp())
                
                resolutions = [
                    ("1280", "720"),
                    ("640", "360"),
                    ("480", "270")
                ]
                
                thumbnail_set = False
                for width, height in resolutions:
                    try:
                        current_thumbnail = thumbnail.replace("{width}", width).replace("{height}", height)
                        current_thumbnail = f"{current_thumbnail}?t={timestamp}"
                        
                        async with aiohttp.ClientSession() as session:
                            async with session.head(current_thumbnail) as resp:
                                if resp.status == 200:
                                    embed.set_image(url=current_thumbnail)
                                    thumbnail_set = True
                                    break
                    except:
                        continue
                
                if not thumbnail_set:
                    preview_url = f"https://static-cdn.jtvnw.net/previews-ttv/live_user_{twitch_name}-1280x720.jpg"
                    embed.set_image(url=f"{preview_url}?t={timestamp}")
                    
            except Exception as e:
                print(f"Error setting stream thumbnail: {e}")
                preview_url = f"https://static-cdn.jtvnw.net/previews-ttv/live_user_{twitch_name}-1280x720.jpg"
                embed.set_image(url=f"{preview_url}?t={timestamp}")

        view = StreamView(twitch_name)
        
        try:
            if role_mentions:
                print(f"[DEBUG] Sending announcement with role mentions: {role_mentions}")
                await channel.send(role_mentions, embed=embed, view=view)
            else:
                print(f"[DEBUG] Sending announcement without role mentions")
                await channel.send(embed=embed, view=view)
        except discord.HTTPException as e:
            print(f"Error sending announcement: {e}")

    #
    # SCHEDULE METHODS
    #
    
    def get_next_sunday(self):
        today = datetime.now()
        days_until_sunday = (6 - today.weekday()) % 7
        return today + timedelta(days=days_until_sunday)

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

    async def get_schedule(self, username: str, guild):
        headers = await self.get_twitch_headers(guild)
        if not headers:
            return None
            
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

    async def get_category_info(self, category_id: str, guild):
        headers = await self.get_twitch_headers(guild)
        if not headers:
            return None
            
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
        event_count = await self.config.guild(guild).event_count()
        for i, segment in enumerate(schedule):
            if i >= event_count:
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
                    now = datetime.utcnow()
                    current_day = now.weekday()
                    current_time = now.strftime("%H:%M")
                    if current_day in update_days and current_time == update_time:
                        channel_id = await self.config.guild(guild).schedule_channel_id()
                        twitch_username = await self.config.guild(guild).twitch_username()
                        if channel_id and twitch_username:
                            channel = guild.get_channel(channel_id)
                            if channel:
                                schedule = await self.get_schedule(twitch_username, guild)
                                if schedule is not None:
                                    await self.post_schedule(channel, schedule)
                await asyncio.sleep(60)
            except Exception as e:
                print(f"Error in schedule update loop: {e}")
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
            event_count = await self.config.guild(channel.guild).event_count()
            for i, segment in enumerate(schedule):
                if i >= event_count:
                    break
                start_time = datetime.fromisoformat(segment["start_time"].replace("Z", "+00:00"))
                title = segment["title"]
                category = segment.get("category", {})
                game_name = category.get("name", "No Category")
                boxart_url = None
                if category and category.get("id"):
                    cat_info = await self.get_category_info(category["id"], channel.guild)
                    if cat_info and cat_info.get("box_art_url"):
                        boxart_url = cat_info["box_art_url"].replace("{width}", "285").replace("{height}", "380")
                unix_ts = int(start_time.timestamp())
                time_str = f"<t:{unix_ts}:F>"
                end_time = segment.get("end_time")
                if end_time:
                    end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
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

    #
    # COMMANDS - GENERAL
    #
    
    @commands.group(aliases=["twitch", "twiman"])
    @commands.guild_only()
    async def twitchmanager(self, ctx):
        """Twitch Manager - Announcements and Schedule"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    #
    # COMMANDS - LIVE ANNOUNCER
    #
    
    @twitchmanager.group(aliases=["live", "twitchlive"])
    @commands.guild_only()
    async def liveannouncer(self, ctx):
        """Manage live stream announcements"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @liveannouncer.command(name="setup")
    @checks.admin_or_permissions(manage_guild=True)
    async def setup_announcer(self, ctx, channel: discord.TextChannel):
        """Set up the Twitch announcer."""
        await self.config.guild(ctx.guild).announcement_channel.set(channel.id)
        await ctx.send(f"Announcement channel set to {channel.mention}")

    @liveannouncer.command(name="addstreamer")
    @checks.admin_or_permissions(manage_guild=True)
    async def add_streamer(self, ctx, twitch_name: str, discord_member: Optional[discord.Member] = None):
        """Add a Twitch streamer to announce."""
        async with self.config.guild(ctx.guild).streamers() as streamers:
            streamers[twitch_name.lower()] = {
                "discord_id": discord_member.id if discord_member else None,
                "last_announced": None
            }
        await ctx.send(f"Added {twitch_name} to announcement list.")

    @liveannouncer.command(name="removestreamer")
    @checks.admin_or_permissions(manage_guild=True)
    async def remove_streamer(self, ctx, twitch_name: str):
        """Remove a Twitch streamer from announcements."""
        async with self.config.guild(ctx.guild).streamers() as streamers:
            if twitch_name.lower() in streamers:
                del streamers[twitch_name.lower()]
                await ctx.send(f"Removed {twitch_name} from announcement list.")
            else:
                await ctx.send("Streamer not found in list.")

    @liveannouncer.command(name="liststreamers")
    async def list_streamers(self, ctx):
        """List all tracked streamers."""
        streamers = await self.config.guild(ctx.guild).streamers()
        if not streamers:
            await ctx.send("No streamers in list.")
            return

        msg = "**Tracked Streamers:**\n"
        for twitch_name, data in streamers.items():
            discord_id = data.get("discord_id")
            if discord_id:
                member = ctx.guild.get_member(discord_id)
                msg += f"- {twitch_name} ({member.mention if member else 'Unknown member'})\n"
            else:
                msg += f"- {twitch_name}\n"
        
        await ctx.send(msg)

    @liveannouncer.command(name="listroles")
    async def list_ping_roles(self, ctx):
        """List all roles that will be pinged for stream announcements."""
        ping_roles = await self.config.guild(ctx.guild).ping_roles()
        if not ping_roles:
            await ctx.send("No roles configured for pinging.")
            return
            
        msg = "**Roles that will be pinged:**\n"
        for role_id in ping_roles:
            role = ctx.guild.get_role(role_id)
            if role:
                msg += f"- {role.name} (ID: {role_id})\n"
            else:
                msg += f"- Unknown role (ID: {role_id})\n"
        
        await ctx.send(msg)

    @liveannouncer.command(name="checkrole")
    async def check_role_settings(self, ctx, role: discord.Role):
        """Check if a role can be properly pinged by the bot."""
        embed = discord.Embed(
            title=f"Role Check: {role.name}",
            color=discord.Color.blue()
        )
        
        # Check if role is mentionable
        embed.add_field(
            name="Mentionable",
            value="✅ Yes" if role.mentionable else "❌ No - Role cannot be mentioned",
            inline=False
        )
        
        # Check bot permissions
        channel_id = await self.config.guild(ctx.guild).announcement_channel()
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        
        if channel:
            bot_member = ctx.guild.get_member(self.bot.user.id)
            permissions = channel.permissions_for(bot_member)
            
            embed.add_field(
                name="Announcement Channel",
                value=f"{channel.mention}",
                inline=True
            )
            
            embed.add_field(
                name="Mention Permissions",
                value="✅ Yes" if permissions.mention_everyone else "❌ No - Bot cannot mention roles",
                inline=True
            )
        else:
            embed.add_field(
                name="Announcement Channel",
                value="❌ Not set",
                inline=False
            )
        
        # Check if role is in ping_roles
        ping_roles = await self.config.guild(ctx.guild).ping_roles()
        in_ping_roles = role.id in ping_roles
        
        embed.add_field(
            name="In Ping Roles List",
            value="✅ Yes" if in_ping_roles else "❌ No - Role not configured for pinging",
            inline=False
        )
        
        # Test ping
        test_message = await ctx.send(f"Testing ping for {role.mention}...")
        
        embed.add_field(
            name="Test Ping",
            value=f"✅ Sent - Check if you received a notification for the message above",
            inline=False
        )
        
        embed.add_field(
            name="User Settings Note",
            value="If users aren't receiving pings, ask them to check their notification settings in Discord.",
            inline=False
        )
        
        await ctx.send(embed=embed)

w   
    @liveannouncer.command(name="setfrequency")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def set_check_frequency(self, ctx, seconds: int):
        """Set how frequently to check for live streams (in seconds)."""
        if seconds < 30:
            await ctx.send("❌ Check frequency cannot be less than 30 seconds to avoid API rate limits.")
            return
            
        streamer_count = len(await self.config.guild(ctx.guild).streamers())
        requests_per_minute = (60 / seconds) * streamer_count
        
        if requests_per_minute > 50:
            await ctx.send(f"⚠️ Warning: With {streamer_count} streamers, checking every {seconds} seconds "
                          f"will make approximately {requests_per_minute:.1f} requests per minute to the Twitch API. "
                          "This might cause rate limit issues.")
        
        await self.config.guild(ctx.guild).check_frequency.set(seconds)
        await ctx.send(f"✅ Stream check frequency set to {seconds} seconds.")

    @liveannouncer.command(name="showfrequency")
    @commands.guild_only()
    async def show_check_frequency(self, ctx):
        """Show the current check frequency for live streams."""
        frequency = await self.config.guild(ctx.guild).check_frequency()
        streamer_count = len(await self.config.guild(ctx.guild).streamers())
        requests_per_minute = (60 / frequency) * streamer_count
        
        embed = discord.Embed(
            title="Twitch Announcer Settings",
            color=discord.Color.purple()
        )
        embed.add_field(name="Check Frequency", value=f"{frequency} seconds", inline=True)
        embed.add_field(name="Tracked Streamers", value=str(streamer_count), inline=True)
        embed.add_field(name="Requests per Minute", value=f"{requests_per_minute:.1f}", inline=True)
        
        await ctx.send(embed=embed)

    @liveannouncer.command(name="addrole")
    @checks.admin_or_permissions(manage_guild=True)
    async def add_ping_role(self, ctx, role: discord.Role):
        """Add a role to ping for stream announcements."""
        async with self.config.guild(ctx.guild).ping_roles() as roles:
            if role.id not in roles:
                roles.append(role.id)
        await ctx.send(f"Added {role.name} to announcement pings.")

    @liveannouncer.command(name="removerole")
    @checks.admin_or_permissions(manage_guild=True)
    async def remove_ping_role(self, ctx, role: discord.Role):
        """Remove a role from stream announcements."""
        async with self.config.guild(ctx.guild).ping_roles() as roles:
            if role.id in roles:
                roles.remove(role.id)
        await ctx.send(f"Removed {role.name} from announcement pings.")

    @liveannouncer.command(name="setauth")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def set_twitch_auth(self, ctx):
        """Set Twitch API authentication."""
        await ctx.send("Please check your DMs for the setup process.")
        
        def check(m):
            return m.author == ctx.author and m.channel.type == discord.ChannelType.private

        try:
            await ctx.author.send("Please enter your Twitch Client ID:")
            client_id_msg = await self.bot.wait_for('message', check=check, timeout=60)
            
            await ctx.author.send("Please enter your Twitch Client Secret:")
            client_secret_msg = await self.bot.wait_for('message', check=check, timeout=60)

            client_id = client_id_msg.content
            client_secret = client_secret_msg.content

            # Test the credentials
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://id.twitch.tv/oauth2/token",
                    params={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "grant_type": "client_credentials"
                    }
                ) as resp:
                    if resp.status != 200:
                        await ctx.author.send("❌ Invalid credentials! Please check your Client ID and Secret.")
                        return
                    
                    data = await resp.json()
                    access_token = data["access_token"]
                    expires_in = data["expires_in"]
                    now = datetime.utcnow().timestamp()
                    
                    await self.config.guild(ctx.guild).client_id.set(client_id)
                    await self.config.guild(ctx.guild).client_secret.set(client_secret)
                    await self.config.guild(ctx.guild).access_token.set(access_token)
                    await self.config.guild(ctx.guild).token_expires.set(now + expires_in)
                    
                    await ctx.author.send("✅ Twitch API authentication successfully set and verified!")
                    if ctx.channel.type != discord.ChannelType.private:
                        await ctx.send("✅ Twitch API authentication has been set up via DM.")

        except asyncio.TimeoutError:
            await ctx.author.send("Setup timed out. Please try again.")
        except discord.Forbidden:
            await ctx.send("I couldn't send you a DM. Please enable DMs and try again.")
        except Exception as e:
            await ctx.author.send(f"An error occurred: {str(e)}")

    @liveannouncer.command(name="checkauth")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def check_auth(self, ctx):
        """Check the status of Twitch API authentication."""
        client_id = await self.config.guild(ctx.guild).client_id()
        client_secret = await self.config.guild(ctx.guild).client_secret()
        access_token = await self.config.guild(ctx.guild).access_token()
        token_expires = await self.config.guild(ctx.guild).token_expires()
        
        if not client_id or not client_secret:
            await ctx.send("❌ Client ID or Client Secret not set. Please use `setauth` to configure them.")
            return

        # If we don't have a token, try to get one
        if not access_token:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        "https://id.twitch.tv/oauth2/token",
                        params={
                            "client_id": client_id,
                            "client_secret": client_secret,
                            "grant_type": "client_credentials"
                        }
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            access_token = data["access_token"]
                            expires_in = data["expires_in"]
                            now = datetime.utcnow().timestamp()
                            
                            await self.config.guild(ctx.guild).access_token.set(access_token)
                            await self.config.guild(ctx.guild).token_expires.set(now + expires_in)
                            
                            token_expires = now + expires_in
                            await ctx.send("✅ Successfully generated new access token!")
                        else:
                            error_text = await resp.text()
                            await ctx.send(f"❌ Failed to generate token. Status: {resp.status}, Error: {error_text}")
            except Exception as e:
                await ctx.send(f"❌ Error generating token: {str(e)}")
            
        now = datetime.utcnow().timestamp()
        
        embed = discord.Embed(
            title="Twitch API Authentication Status",
            color=discord.Color.purple()
        )
        
        embed.add_field(
            name="Client ID",
            value="✅ Set" if client_id else "❌ Not Set",
            inline=True
        )
        
        embed.add_field(
            name="Client Secret",
            value="✅ Set" if client_secret else "❌ Not Set",
            inline=True
        )
        
        embed.add_field(
            name="Access Token",
            value="✅ Set" if access_token else "❌ Not Set",
            inline=True
        )
        
        if token_expires:
            if now >= token_expires:
                status = "❌ Expired"
            else:
                remaining = int(token_expires - now)
                status = f"✅ Valid for {remaining} seconds"
        else:
            status = "❌ Not Set"
            
        embed.add_field(
            name="Token Status",
            value=status,
            inline=False
        )

        # Test the current token if we have one
        if access_token:
            try:
                headers = {
                    "Client-ID": client_id,
                    "Authorization": f"Bearer {access_token}"
                }
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        "https://api.twitch.tv/helix/users",
                        headers=headers
                    ) as resp:
                        if resp.status == 200:
                            embed.add_field(
                                name="Token Test",
                                value="✅ Token working correctly",
                                inline=False
                            )
                        else:
                            error_text = await resp.text()
                            embed.add_field(
                                name="Token Test",
                                value=f"❌ Token not working (Status {resp.status}): {error_text}",
                                inline=False
                            )
            except Exception as e:
                embed.add_field(
                    name="Token Test",
                    value=f"❌ Error testing token: {str(e)}",
                    inline=False
                )
        
        await ctx.send(embed=embed)

    @liveannouncer.command(name="test")
    @checks.admin_or_permissions(manage_guild=True)
    async def test_announcement(self, ctx, twitch_name: str):
        """Test stream announcement for a specific streamer."""
        headers = await self.get_twitch_headers(ctx.guild)
        if not headers:
            await ctx.send("Twitch API authentication not set up!")
            return

        async with aiohttp.ClientSession() as session:
            url = f"https://api.twitch.tv/helix/streams?user_login={twitch_name}"
            async with session.get(url, headers=headers) as resp:
                text = await resp.text()
                print(f"[DEBUG] Twitch API status: {resp.status}")
                print(f"[DEBUG] Twitch API response: {text}")
                if resp.status != 200:
                    await ctx.send(f"Failed to fetch stream data. Twitch API returned {resp.status}: {text}")
                    return
                    
                data = await resp.json()
                if not data["data"]:
                    await ctx.send(f"{twitch_name} is not live. Creating test announcement anyway...")
                    test_data = {
                        "title": "Test Stream",
                        "game_name": "Just Chatting",
                        "viewer_count": 0,
                        "started_at": datetime.utcnow().isoformat(),
                        "thumbnail_url": None
                    }
                    await self.announce_stream(ctx.guild, twitch_name, test_data)
                else:
                    await self.announce_stream(ctx.guild, twitch_name, data["data"][0])

    #
    # COMMANDS - SCHEDULE
    #
    
    @twitchmanager.group(aliases=["schedule", "sched"])
    @commands.guild_only()
    async def twitchschedule(self, ctx):
        """Manage Twitch schedule integration"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @twitchschedule.command(name="setup")
    @checks.admin_or_permissions(manage_guild=True)
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
                await self.config.guild(ctx.guild).schedule_channel_id.set(channel_id)
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
                datetime.strptime(time_msg.content, "%H:%M")
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

        await ctx.send("✅ Setup complete! Use `!twiman sched force` to generate your first schedule.")

    @twitchschedule.command(name="force")
    @checks.admin_or_permissions(manage_guild=True)
    async def force_update(self, ctx):
        """Force an immediate schedule update."""
        channel_id = await self.config.guild(ctx.guild).schedule_channel_id()
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        if not channel_id or not twitch_username:
            await ctx.send("❌ Please run `!twiman sched setup` first.")
            return
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("❌ The configured channel no longer exists. Please run `!twiman sched setup` again.")
            return
        schedule = await self.get_schedule(twitch_username, ctx.guild)
        if schedule is None:
            await ctx.send("❌ Could not fetch schedule from Twitch. Check your Twitch credentials and username.")
            return
        await self.post_schedule(channel, schedule)
        await ctx.send("✅ Schedule updated!")

    @twitchschedule.command(name="notify")
    @checks.admin_or_permissions(manage_guild=True)
    async def set_notify(self, ctx, role: discord.Role = None):
        """Set or remove the role to ping for schedule updates."""
        await self.config.guild(ctx.guild).notify_role_id.set(role.id if role else None)
        if role:
            await ctx.send(f"✅ Schedule updates will ping {role.mention}")
        else:
            await ctx.send("✅ Schedule updates will not ping any role")

    @twitchschedule.command(name="events")
    @checks.admin_or_permissions(manage_guild=True)
    async def set_event_count(self, ctx, count: int = None):
        """Set how many events to show in the schedule image (1-10)."""
        if count is None:
            current = await self.config.guild(ctx.guild).event_count()
            await ctx.send(f"Currently showing up to **{current}** events. Use `!twiman sched events <1-10>` to change.")
            return
        if not 1 <= count <= 10:
            await ctx.send("❌ Please choose a number between 1 and 10.")
            return
        await self.config.guild(ctx.guild).event_count.set(count)
        await ctx.send(f"✅ Will show up to {count} events in the schedule image.")

    @twitchschedule.command(name="settings")
    async def schedule_settings(self, ctx):
        """Show current schedule settings."""
        data = await self.config.guild(ctx.guild).all()
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        channel = ctx.guild.get_channel(data["schedule_channel_id"]) if data["schedule_channel_id"] else None
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
    @checks.admin_or_permissions(manage_guild=True)
    async def testsend(self, ctx):
        """Test if the bot can send messages in this channel."""
        await ctx.send("Test message! If you see this, the bot can send messages here.")


class StreamView(discord.ui.View):
    def __init__(self, twitch_name):
        super().__init__(timeout=None)
        self.twitch_name = twitch_name
        
        self.watch_button = discord.ui.Button(
            label="Watch Stream",
            url=f"https://twitch.tv/{twitch_name}",
            style=discord.ButtonStyle.url
        )
        self.subscribe_button = discord.ui.Button(
            label="Subscribe",
            url=f"https://twitch.tv/{twitch_name}/subscribe",
            style=discord.ButtonStyle.url
        )
        self.add_item(self.watch_button)
        self.add_item(self.subscribe_button)
