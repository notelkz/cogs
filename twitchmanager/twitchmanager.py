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
    
    @commands.group(aliases=["twitch"])
    @commands.guild_only()
    async def twitchmanager(self, ctx):
        """Twitch Manager - Announcements and Schedule"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    #
    # COMMANDS - LIVE ANNOUNCER
    #
    
    @twitchmanager.group(aliases=["live", "announcer"])
    @commands.guild_only()
    async def announcer(self, ctx):
        """Manage live stream announcements"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @announcer.command(name="setup")
    @checks.admin_or_permissions(manage_guild=True)
    async def setup_announcer(self, ctx, channel: discord.TextChannel):
        """Set up the Twitch announcer."""
        await self.config.guild(ctx.guild).announcement_channel.set(channel.id)
        await ctx.send(f"Announcement channel set to {channel.mention}")

    @announcer.command(name="addstreamer")
    @checks.admin_or_permissions(manage_guild=True)
    async def add_streamer(self, ctx, twitch_name: str, discord_member: Optional[discord.Member] = None):
        """Add a Twitch streamer to announce."""
        async with self.config.guild(ctx.guild).streamers() as streamers:
            streamers[twitch_name.lower()] = {
                "discord_id": discord_member.id if discord_member else None,
                "last_announced": None
            }
        await ctx.send(f"Added {twitch_name} to announcement list.")

    @announcer.command(name="removestreamer")
    @checks.admin_or_permissions(manage_guild=True)
    async def remove_streamer(self, ctx, twitch_name: str):
        """Remove a Twitch streamer from announcements."""
        async with self.config.guild(ctx.guild).streamers() as streamers:
            if twitch_name.lower() in streamers:
                del streamers[twitch_name.lower()]
                await ctx.send(f"Removed {twitch_name} from announcement list.")
            else:
                await ctx.send("Streamer not found in list.")

    @announcer.command(name="liststreamers")
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

    @announcer.command(name="listroles")
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

    @announcer.command(name="checkrole")
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

    @announcer.command(name="setfrequency")
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
            await ctx.
