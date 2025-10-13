import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, pagify
import aiohttp
import asyncio
from datetime import datetime, timedelta
from typing import Optional, List, Dict
import logging

log = logging.getLogger("red.twitchclips")


class TwitchClips(commands.Cog):
    """
    Scan for Twitch clips and post them to a Discord forum gallery.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890123, force_registration=True)
        
        default_guild = {
            "forum_channel_id": None,
            "tracked_users": {},  # {username: {last_clip_id: str, last_check: timestamp, user_id: str, was_live: bool}}
            "scan_interval": 3600,  # 1 hour in seconds
            "clips_per_scan": 5,
            "auto_scan_enabled": False,
            "scan_on_stream_end": False,
            "stream_check_interval": 300,  # 5 minutes in seconds
        }
        
        default_global = {
            "client_id": None,
            "client_secret": None,
            "access_token": None,
            "token_expires": 0,
        }
        
        self.config.register_guild(**default_guild)
        self.config.register_global(**default_global)
        
        self.scan_tasks = {}
        self.stream_monitor_tasks = {}
        self.session: Optional[aiohttp.ClientSession] = None
        
    async def cog_load(self):
        """Initialize the cog."""
        self.session = aiohttp.ClientSession()
        # Start scan tasks for guilds with auto-scan enabled
        for guild in self.bot.guilds:
            if await self.config.guild(guild).auto_scan_enabled():
                self.start_scan_task(guild)
            if await self.config.guild(guild).scan_on_stream_end():
                self.start_stream_monitor_task(guild)
    
    async def cog_unload(self):
        """Clean up when cog is unloaded."""
        for task in self.scan_tasks.values():
            task.cancel()
        for task in self.stream_monitor_tasks.values():
            task.cancel()
        if self.session:
            await self.session.close()
    
    def start_scan_task(self, guild: discord.Guild):
        """Start the automatic scan task for a guild."""
        if guild.id in self.scan_tasks:
            self.scan_tasks[guild.id].cancel()
        
        self.scan_tasks[guild.id] = self.bot.loop.create_task(
            self.scan_loop(guild)
        )
        log.info(f"Started scan task for guild {guild.id}")
    
    def stop_scan_task(self, guild: discord.Guild):
        """Stop the automatic scan task for a guild."""
        if guild.id in self.scan_tasks:
            self.scan_tasks[guild.id].cancel()
            del self.scan_tasks[guild.id]
            log.info(f"Stopped scan task for guild {guild.id}")
    
    def start_stream_monitor_task(self, guild: discord.Guild):
        """Start the stream monitoring task for a guild."""
        if guild.id in self.stream_monitor_tasks:
            self.stream_monitor_tasks[guild.id].cancel()
        
        self.stream_monitor_tasks[guild.id] = self.bot.loop.create_task(
            self.stream_monitor_loop(guild)
        )
        log.info(f"Started stream monitor task for guild {guild.id}")
    
    def stop_stream_monitor_task(self, guild: discord.Guild):
        """Stop the stream monitoring task for a guild."""
        if guild.id in self.stream_monitor_tasks:
            self.stream_monitor_tasks[guild.id].cancel()
            del self.stream_monitor_tasks[guild.id]
            log.info(f"Stopped stream monitor task for guild {guild.id}")
    
    async def scan_loop(self, guild: discord.Guild):
        """Continuously scan for clips at the configured interval."""
        await self.bot.wait_until_ready()
        
        while True:
            try:
                interval = await self.config.guild(guild).scan_interval()
                await asyncio.sleep(interval)
                
                if not await self.config.guild(guild).auto_scan_enabled():
                    break
                
                await self.scan_and_post_clips(guild)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in scan loop for guild {guild.id}: {e}", exc_info=True)
                await asyncio.sleep(300)  # Wait 5 minutes on error
    
    async def stream_monitor_loop(self, guild: discord.Guild):
        """Monitor streams and scan when they end."""
        await self.bot.wait_until_ready()
        
        while True:
            try:
                interval = await self.config.guild(guild).stream_check_interval()
                await asyncio.sleep(interval)
                
                if not await self.config.guild(guild).scan_on_stream_end():
                    break
                
                # Check stream status for all tracked users
                tracked_users = await self.config.guild(guild).tracked_users()
                
                for username, user_data in tracked_users.items():
                    try:
                        user_id = user_data.get("user_id")
                        if not user_id:
                            # Get user_id if we don't have it
                            user_id = await self.get_user_id(username)
                            if user_id:
                                user_data["user_id"] = user_id
                            else:
                                continue
                        
                        is_live = await self.get_stream_status(user_id)
                        was_live = user_data.get("was_live", False)
                        
                        # Detect stream end (was live, now offline)
                        if was_live and not is_live:
                            log.info(f"Stream ended for {username}, scanning for clips")
                            
                            # Scan for clips from this specific user
                            clips_per_scan = await self.config.guild(guild).clips_per_scan()
                            clips = await self.get_clips(user_id, clips_per_scan)
                            last_clip_id = user_data.get("last_clip_id")
                            
                            new_clips = []
                            for clip in clips:
                                if clip["id"] == last_clip_id:
                                    break
                                new_clips.append(clip)
                            
                            if new_clips:
                                # Post clips in order (oldest to newest)
                                for clip in reversed(new_clips):
                                    await self.post_clip_to_forum(guild, clip, username)
                                    await asyncio.sleep(2)
                                
                                # Update last clip ID
                                user_data["last_clip_id"] = new_clips[0]["id"]
                            elif clips and not last_clip_id:
                                # First time checking, just save the latest clip ID
                                user_data["last_clip_id"] = clips[0]["id"]
                        
                        # Update live status
                        user_data["was_live"] = is_live
                        user_data["last_check"] = datetime.now().timestamp()
                        
                        # Save updated data
                        async with self.config.guild(guild).tracked_users() as users:
                            users[username] = user_data
                        
                    except Exception as e:
                        log.error(f"Error monitoring stream for {username}: {e}", exc_info=True)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"Error in stream monitor loop for guild {guild.id}: {e}", exc_info=True)
                await asyncio.sleep(300)  # Wait 5 minutes on error
    
    async def get_stream_status(self, user_id: str) -> bool:
        """Check if a user is currently live. Returns True if live, False otherwise."""
        access_token = await self.get_access_token()
        client_id = await self.config.client_id()
        
        if not access_token or not client_id:
            return False
        
        url = "https://api.twitch.tv/helix/streams"
        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {access_token}"
        }
        params = {"user_id": user_id}
        
        try:
            async with self.session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # If data array is not empty, user is live
                    return len(data.get("data", [])) > 0
                return False
        except Exception as e:
            log.error(f"Error checking stream status for user {user_id}: {e}")
            return False
    
    async def get_access_token(self) -> Optional[str]:
        """Get a valid Twitch API access token."""
        token_expires = await self.config.token_expires()
        current_time = datetime.now().timestamp()
        
        # Check if current token is still valid
        if token_expires > current_time:
            return await self.config.access_token()
        
        # Request new token
        client_id = await self.config.client_id()
        client_secret = await self.config.client_secret()
        
        if not client_id or not client_secret:
            log.error("Twitch API credentials not configured")
            return None
        
        url = "https://id.twitch.tv/oauth2/token"
        params = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials"
        }
        
        try:
            async with self.session.post(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    access_token = data["access_token"]
                    expires_in = data["expires_in"]
                    
                    # Store token and expiration time
                    await self.config.access_token.set(access_token)
                    await self.config.token_expires.set(
                        current_time + expires_in - 300  # Refresh 5 min early
                    )
                    
                    log.info("Successfully obtained new Twitch access token")
                    return access_token
                else:
                    log.error(f"Failed to get Twitch token: {resp.status}")
                    return None
        except Exception as e:
            log.error(f"Error getting Twitch access token: {e}")
            return None
    
    async def get_user_id(self, username: str) -> Optional[str]:
        """Get Twitch user ID from username."""
        access_token = await self.get_access_token()
        client_id = await self.config.client_id()
        
        if not access_token or not client_id:
            return None
        
        url = "https://api.twitch.tv/helix/users"
        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {access_token}"
        }
        params = {"login": username}
        
        try:
            async with self.session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data["data"]:
                        return data["data"][0]["id"]
                return None
        except Exception as e:
            log.error(f"Error getting user ID for {username}: {e}")
            return None
    
    async def get_clips(self, user_id: str, limit: int = 5) -> List[Dict]:
        """Get recent clips for a Twitch user."""
        access_token = await self.get_access_token()
        client_id = await self.config.client_id()
        
        if not access_token or not client_id:
            return []
        
        url = "https://api.twitch.tv/helix/clips"
        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {access_token}"
        }
        
        # Get clips from the last 7 days
        started_at = (datetime.now() - timedelta(days=7)).isoformat() + "Z"
        
        params = {
            "broadcaster_id": user_id,
            "first": limit,
            "started_at": started_at
        }
        
        try:
            async with self.session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("data", [])
                else:
                    log.error(f"Failed to get clips: {resp.status}")
                    return []
        except Exception as e:
            log.error(f"Error getting clips for user {user_id}: {e}")
            return []
    
    async def post_clip_to_forum(
        self, 
        guild: discord.Guild, 
        clip: Dict, 
        username: str
    ) -> bool:
        """Post a clip to the Discord forum channel."""
        forum_id = await self.config.guild(guild).forum_channel_id()
        
        if not forum_id:
            log.error(f"Forum channel not configured for guild {guild.id}")
            return False
        
        forum_channel = guild.get_channel(forum_id)
        
        if not forum_channel or not isinstance(forum_channel, discord.ForumChannel):
            log.error(f"Invalid forum channel for guild {guild.id}")
            return False
        
        try:
            # Create embed for the clip
            embed = discord.Embed(
                title=clip["title"],
                url=clip["url"],
                color=discord.Color.purple(),
                timestamp=datetime.fromisoformat(clip["created_at"].replace("Z", "+00:00"))
            )
            
            embed.set_author(name=f"{username} on Twitch")
            embed.set_image(url=clip["thumbnail_url"])
            
            embed.add_field(
                name="Views",
                value=f"{clip['view_count']:,}",
                inline=True
            )
            embed.add_field(
                name="Duration",
                value=f"{clip['duration']:.1f}s",
                inline=True
            )
            embed.add_field(
                name="Creator",
                value=clip["creator_name"],
                inline=True
            )
            
            # Create forum post
            thread_name = f"{username}: {clip['title'][:80]}"
            
            thread = await forum_channel.create_thread(
                name=thread_name,
                embed=embed,
                content=clip["url"]
            )
            
            log.info(f"Posted clip {clip['id']} to forum in guild {guild.id}")
            return True
            
        except discord.Forbidden:
            log.error(f"Missing permissions to post in forum channel {forum_id}")
            return False
        except Exception as e:
            log.error(f"Error posting clip to forum: {e}", exc_info=True)
            return False
    
    async def scan_and_post_clips(self, guild: discord.Guild, force: bool = False) -> Dict[str, int]:
        """Scan for new clips and post them. Returns stats."""
        tracked_users = await self.config.guild(guild).tracked_users()
        clips_per_scan = await self.config.guild(guild).clips_per_scan()
        
        stats = {"checked": 0, "new_clips": 0, "posted": 0, "errors": 0}
        
        for username, user_data in tracked_users.items():
            stats["checked"] += 1
            
            try:
                user_id = await self.get_user_id(username)
                if not user_id:
                    log.warning(f"Could not find user ID for {username}")
                    stats["errors"] += 1
                    continue
                
                clips = await self.get_clips(user_id, clips_per_scan)
                last_clip_id = user_data.get("last_clip_id")
                
                new_clips = []
                for clip in clips:
                    if clip["id"] == last_clip_id:
                        break
                    new_clips.append(clip)
                
                if new_clips:
                    stats["new_clips"] += len(new_clips)
                    
                    # Post clips in order (oldest to newest)
                    # This way the newest clip appears at the top of the forum
                    for clip in reversed(new_clips):
                        success = await self.post_clip_to_forum(guild, clip, username)
                        if success:
                            stats["posted"] += 1
                        else:
                            stats["errors"] += 1
                        
                        # Small delay between posts
                        await asyncio.sleep(2)
                    
                    # Update last clip ID
                    user_data["last_clip_id"] = new_clips[0]["id"]
                    user_data["last_check"] = datetime.now().timestamp()
                    
                    async with self.config.guild(guild).tracked_users() as users:
                        users[username] = user_data
                elif clips and not last_clip_id:
                    # First time checking this user, just save the latest clip ID
                    user_data["last_clip_id"] = clips[0]["id"]
                    user_data["last_check"] = datetime.now().timestamp()
                    
                    async with self.config.guild(guild).tracked_users() as users:
                        users[username] = user_data
                
            except Exception as e:
                log.error(f"Error scanning clips for {username}: {e}", exc_info=True)
                stats["errors"] += 1
        
        return stats
    
    @commands.group(aliases=["tclips"])
    @commands.guild_only()
    async def twitchclips(self, ctx: commands.Context):
        """Manage Twitch clips scanning and posting."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)
    
    @twitchclips.command(name="setup")
    @checks.admin_or_permissions(manage_guild=True)
    async def setup_api(self, ctx: commands.Context, client_id: str, client_secret: str):
        """
        Set up Twitch API credentials (Admin only).
        
        Get your credentials at: https://dev.twitch.tv/console/apps
        
        **This command will delete your message for security.**
        """
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            await ctx.send("⚠️ I couldn't delete your message. Please delete it manually to protect your credentials!")
        
        await self.config.client_id.set(client_id)
        await self.config.client_secret.set(client_secret)
        await self.config.access_token.set(None)
        await self.config.token_expires.set(0)
        
        # Test the credentials
        token = await self.get_access_token()
        
        if token:
            await ctx.send("✅ Twitch API credentials saved and verified!")
        else:
            await ctx.send("❌ Failed to verify credentials. Please check them and try again.")
    
    @twitchclips.command(name="setchannel")
    @checks.admin_or_permissions(manage_channels=True)
    async def set_channel(self, ctx: commands.Context, channel: discord.ForumChannel):
        """Set the forum channel where clips will be posted."""
        await self.config.guild(ctx.guild).forum_channel_id.set(channel.id)
        await ctx.send(f"✅ Clips will now be posted to {channel.mention}")
    
    @twitchclips.command(name="adduser")
    @checks.admin_or_permissions(manage_guild=True)
    async def add_user(self, ctx: commands.Context, username: str):
        """Add a Twitch user to track for clips."""
        username = username.lower().strip()
        
        # Verify user exists
        user_id = await self.get_user_id(username)
        if not user_id:
            await ctx.send(f"❌ Could not find Twitch user: {username}")
            return
        
        async with self.config.guild(ctx.guild).tracked_users() as users:
            if username in users:
                await ctx.send(f"ℹ️ {username} is already being tracked.")
                return
            
            users[username] = {
                "last_clip_id": None,
                "last_check": 0,
                "user_id": user_id,
                "was_live": False
            }
        
        await ctx.send(f"✅ Now tracking clips for: {username}")
    
    @twitchclips.command(name="removeuser", aliases=["deluser"])
    @checks.admin_or_permissions(manage_guild=True)
    async def remove_user(self, ctx: commands.Context, username: str):
        """Remove a Twitch user from tracking."""
        username = username.lower().strip()
        
        async with self.config.guild(ctx.guild).tracked_users() as users:
            if username in users:
                del users[username]
                await ctx.send(f"✅ Stopped tracking: {username}")
            else:
                await ctx.send(f"❌ {username} is not being tracked.")
    
    @twitchclips.command(name="listusers", aliases=["list"])
    async def list_users(self, ctx: commands.Context):
        """List all tracked Twitch users."""
        tracked_users = await self.config.guild(ctx.guild).tracked_users()
        
        if not tracked_users:
            await ctx.send("No users are currently being tracked.")
            return
        
        user_list = "\n".join([f"• {username}" for username in sorted(tracked_users.keys())])
        
        embed = discord.Embed(
            title="Tracked Twitch Users",
            description=user_list,
            color=discord.Color.purple()
        )
        embed.set_footer(text=f"Total: {len(tracked_users)} users")
        
        await ctx.send(embed=embed)
    
    @twitchclips.command(name="scan", aliases=["force"])
    @checks.admin_or_permissions(manage_guild=True)
    async def force_scan(self, ctx: commands.Context):
        """Force an immediate scan for new clips."""
        forum_id = await self.config.guild(ctx.guild).forum_channel_id()
        
        if not forum_id:
            await ctx.send("❌ Please set up a forum channel first with `!twitchclips setchannel`")
            return
        
        tracked_users = await self.config.guild(ctx.guild).tracked_users()
        
        if not tracked_users:
            await ctx.send("❌ No users are being tracked. Add users with `!twitchclips adduser`")
            return
        
        async with ctx.typing():
            stats = await self.scan_and_post_clips(ctx.guild, force=True)
        
        embed = discord.Embed(
            title="Scan Complete",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        embed.add_field(name="Users Checked", value=stats["checked"], inline=True)
        embed.add_field(name="New Clips Found", value=stats["new_clips"], inline=True)
        embed.add_field(name="Clips Posted", value=stats["posted"], inline=True)
        
        if stats["errors"]:
            embed.add_field(name="Errors", value=stats["errors"], inline=True)
            embed.color = discord.Color.orange()
        
        await ctx.send(embed=embed)
    
    @twitchclips.command(name="autoscan")
    @checks.admin_or_permissions(manage_guild=True)
    async def toggle_autoscan(self, ctx: commands.Context, enabled: bool):
        """Enable or disable automatic scanning."""
        await self.config.guild(ctx.guild).auto_scan_enabled.set(enabled)
        
        if enabled:
            self.start_scan_task(ctx.guild)
            interval = await self.config.guild(ctx.guild).scan_interval()
            await ctx.send(f"✅ Automatic scanning enabled! Scanning every {interval // 60} minutes.")
        else:
            self.stop_scan_task(ctx.guild)
            await ctx.send("✅ Automatic scanning disabled.")
    
    @twitchclips.command(name="scanonstreamend", aliases=["streamend"])
    @checks.admin_or_permissions(manage_guild=True)
    async def toggle_stream_end_scan(self, ctx: commands.Context, enabled: bool):
        """Enable or disable scanning when tracked streams go offline."""
        await self.config.guild(ctx.guild).scan_on_stream_end.set(enabled)
        
        if enabled:
            self.start_stream_monitor_task(ctx.guild)
            interval = await self.config.guild(ctx.guild).stream_check_interval()
            await ctx.send(f"✅ Stream-end scanning enabled! Checking stream status every {interval // 60} minutes.")
        else:
            self.stop_stream_monitor_task(ctx.guild)
            await ctx.send("✅ Stream-end scanning disabled.")
    
    @twitchclips.command(name="streamcheckinterval")
    @checks.admin_or_permissions(manage_guild=True)
    async def set_stream_check_interval(self, ctx: commands.Context, minutes: int):
        """Set how often to check stream status in minutes (minimum 1)."""
        if minutes < 1:
            await ctx.send("❌ Interval must be at least 1 minute.")
            return
        
        seconds = minutes * 60
        await self.config.guild(ctx.guild).stream_check_interval.set(seconds)
        
        # Restart stream monitor task if it's running
        if await self.config.guild(ctx.guild).scan_on_stream_end():
            self.start_stream_monitor_task(ctx.guild)
        
        await ctx.send(f"✅ Stream check interval set to {minutes} minutes.")
    
    @twitchclips.command(name="interval")
    @checks.admin_or_permissions(manage_guild=True)
    async def set_interval(self, ctx: commands.Context, minutes: int):
        """Set the automatic scan interval in minutes (minimum 10)."""
        if minutes < 10:
            await ctx.send("❌ Interval must be at least 10 minutes.")
            return
        
        seconds = minutes * 60
        await self.config.guild(ctx.guild).scan_interval.set(seconds)
        
        # Restart scan task if it's running
        if await self.config.guild(ctx.guild).auto_scan_enabled():
            self.start_scan_task(ctx.guild)
        
        await ctx.send(f"✅ Scan interval set to {minutes} minutes.")
    
    @twitchclips.command(name="clipsperuser")
    @checks.admin_or_permissions(manage_guild=True)
    async def set_clips_per_scan(self, ctx: commands.Context, count: int):
        """Set how many recent clips to check per user (1-20)."""
        if not 1 <= count <= 20:
            await ctx.send("❌ Count must be between 1 and 20.")
            return
        
        await self.config.guild(ctx.guild).clips_per_scan.set(count)
        await ctx.send(f"✅ Will check the {count} most recent clips per user.")
    
    @twitchclips.command(name="settings")
    async def show_settings(self, ctx: commands.Context):
        """Show current settings for this server."""
        forum_id = await self.config.guild(ctx.guild).forum_channel_id()
        interval = await self.config.guild(ctx.guild).scan_interval()
        clips_per = await self.config.guild(ctx.guild).clips_per_scan()
        auto_enabled = await self.config.guild(ctx.guild).auto_scan_enabled()
        stream_end_enabled = await self.config.guild(ctx.guild).scan_on_stream_end()
        stream_check_interval = await self.config.guild(ctx.guild).stream_check_interval()
        tracked_users = await self.config.guild(ctx.guild).tracked_users()
        
        forum_channel = ctx.guild.get_channel(forum_id) if forum_id else None
        
        embed = discord.Embed(
            title="Twitch Clips Settings",
            color=discord.Color.purple()
        )
        
        embed.add_field(
            name="Forum Channel",
            value=forum_channel.mention if forum_channel else "Not set",
            inline=False
        )
        embed.add_field(
            name="Auto-scan",
            value="✅ Enabled" if auto_enabled else "❌ Disabled",
            inline=True
        )
        embed.add_field(
            name="Scan Interval",
            value=f"{interval // 60} minutes",
            inline=True
        )
        embed.add_field(
            name="Clips per User",
            value=str(clips_per),
            inline=True
        )
        embed.add_field(
            name="Scan on Stream End",
            value="✅ Enabled" if stream_end_enabled else "❌ Disabled",
            inline=True
        )
        embed.add_field(
            name="Stream Check Interval",
            value=f"{stream_check_interval // 60} minutes",
            inline=True
        )
        embed.add_field(
            name="Tracked Users",
            value=str(len(tracked_users)),
            inline=True
        )
        
        # Check API setup
        client_id = await self.config.client_id()
        embed.add_field(
            name="API Configured",
            value="✅ Yes" if client_id else "❌ No",
            inline=True
        )
        
        await ctx.send(embed=embed)


async def setup(bot: Red):
    """Load the TwitchClips cog."""
    await bot.add_cog(TwitchClips(bot))