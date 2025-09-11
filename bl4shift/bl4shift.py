import asyncio
import re
import logging
from datetime import datetime, timezone
from typing import Optional, Set, Dict, Any

import aiohttp
import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, pagify

log = logging.getLogger("red.bl4shift")

class BL4ShiftCodes(commands.Cog):
    """Monitor subreddit for Borderlands 4 SHIFT codes and post to Discord."""
    
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        
        # Default settings
        default_guild = {
            "channel_id": None,
            "subreddit": "borderlands",
            "check_interval": 300,  # 5 minutes
            "keywords": ["shift", "code", "borderlands 4", "bl4", "golden key"],
            "posted_codes": {},  # Track posted codes to avoid duplicates
            "use_forum": False,  # Whether to use forum channels
            "thread_name_template": "SHIFT Codes - {date}",  # Template for thread names
            "create_new_thread_daily": True,  # Create new thread each day
            "active_thread_id": None  # Current active thread
        }
        
        self.config.register_guild(**default_guild)
        
        # SHIFT code patterns (common formats)
        self.shift_patterns = [
            r'[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}',  # Standard format
            r'[A-Z0-9]{25}',  # No dashes
            r'(?:SHIFT|CODE)[\s:]*([A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5})',
            r'(?:SHIFT|CODE)[\s:]*([A-Z0-9]{25})'
        ]
        
        self.session: Optional[aiohttp.ClientSession] = None
        self.monitor_task: Optional[asyncio.Task] = None
        
    async def cog_load(self):
        """Initialize the cog."""
        self.session = aiohttp.ClientSession()
        # Start monitoring for all guilds that have it configured
        await self._start_monitoring_tasks()
        
    async def cog_unload(self):
        """Clean up when cog is unloaded."""
        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
        if self.session and not self.session.closed:
            await self.session.close()
            
    async def _start_monitoring_tasks(self):
        """Start monitoring tasks for configured guilds."""
        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
        
        self.monitor_task = asyncio.create_task(self._monitor_reddit())
        
    async def _get_reddit_posts(self, subreddit: str, limit: int = 25) -> list:
        """Fetch recent posts from subreddit using Reddit JSON API."""
        url = f"https://www.reddit.com/r/{subreddit}/new.json?limit={limit}"
        headers = {"User-Agent": "RedBot SHIFT Code Monitor 1.0"}
        
        try:
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("data", {}).get("children", [])
                else:
                    log.warning(f"Reddit API returned status {response.status}")
                    return []
        except Exception as e:
            log.error(f"Error fetching Reddit posts: {e}")
            return []
    
    def _extract_shift_codes(self, text: str) -> Set[str]:
        """Extract SHIFT codes from text using regex patterns."""
        codes = set()
        text_upper = text.upper()
        
        for pattern in self.shift_patterns:
            matches = re.findall(pattern, text_upper, re.IGNORECASE)
            for match in matches:
                # Normalize code format (add dashes if missing)
                if isinstance(match, tuple):
                    match = match[0] if match else ""
                
                # Remove existing dashes and add them back in standard format
                clean_code = re.sub(r'[^A-Z0-9]', '', match)
                if len(clean_code) == 25:
                    formatted_code = f"{clean_code[:5]}-{clean_code[5:10]}-{clean_code[10:15]}-{clean_code[15:20]}-{clean_code[20:25]}"
                    codes.add(formatted_code)
                    
        return codes
    
    def _is_bl4_related(self, title: str, text: str, keywords: list) -> bool:
        """Check if post is related to Borderlands 4."""
        combined_text = f"{title} {text}".lower()
        bl4_keywords = ["borderlands 4", "bl4", "borderlands4"]
        
        # Must contain BL4 reference AND shift/code keywords
        has_bl4 = any(keyword in combined_text for keyword in bl4_keywords)
        has_shift = any(keyword in combined_text for keyword in keywords)
        
        return has_bl4 and has_shift
    
    async def _get_or_create_thread(self, guild, channel, guild_config) -> Optional[discord.Thread]:
        """Get existing thread or create new one for posting codes."""
        use_forum = await guild_config.use_forum()
        if not use_forum:
            return None
            
        # Check if channel is a forum
        if not isinstance(channel, discord.ForumChannel):
            log.warning(f"Channel {channel.id} is not a forum channel but forum mode is enabled")
            return None
            
        active_thread_id = await guild_config.active_thread_id()
        create_new_daily = await guild_config.create_new_thread_daily()
        thread_template = await guild_config.thread_name_template()
        
        # Try to get existing active thread
        if active_thread_id:
            try:
                thread = channel.get_thread(active_thread_id)
                if thread and not thread.archived:
                    # Check if we should create a new thread (daily option)
                    if create_new_daily:
                        # Get thread creation date
                        thread_date = thread.created_at.date()
                        today = datetime.now(timezone.utc).date()
                        
                        if thread_date < today:
                            # Archive old thread and create new one
                            try:
                                await thread.edit(archived=True)
                            except:
                                pass  # Might not have permissions
                            thread = None
                        else:
                            return thread
                    else:
                        return thread
            except:
                # Thread might not exist anymore
                pass
        
        # Create new thread
        try:
            current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            thread_name = thread_template.format(date=current_date)
            
            # Create initial embed for the thread
            embed = discord.Embed(
                title="🔑 Borderlands 4 SHIFT Codes",
                description="This thread will be updated with new SHIFT codes as they're found!",
                color=discord.Color.gold()
            )
            embed.add_field(
                name="How to Redeem",
                value="Visit [shift.gearboxsoftware.com](https://shift.gearboxsoftware.com) or use the in-game menu",
                inline=False
            )
            embed.set_footer(text="Auto-managed by BL4 SHIFT Monitor")
            
            # Create thread with initial message
            thread = await channel.create_thread(
                name=thread_name,
                embed=embed,
                reason="Auto-created for SHIFT code monitoring"
            )
            
            # Update config with new thread ID
            await guild_config.active_thread_id.set(thread.id)
            
            log.info(f"Created new SHIFT codes thread: {thread.name} ({thread.id})")
            return thread
            
        except Exception as e:
            log.error(f"Failed to create thread in forum {channel.id}: {e}")
            return None
    
    async def _post_to_channel_or_thread(self, guild, channel, embed: discord.Embed, guild_config):
        """Post embed to either regular channel or forum thread."""
        use_forum = await guild_config.use_forum()
        
        if use_forum and isinstance(channel, discord.ForumChannel):
            # Post to forum thread
            thread = await self._get_or_create_thread(guild, channel, guild_config)
            if thread:
                await thread.send(embed=embed)
                return thread
            else:
                log.error(f"Could not get or create thread in forum {channel.id}")
                return None
        else:
            # Post to regular channel
            await channel.send(embed=embed)
            return channel
    
    async def _create_embed(self, post_data: dict, codes: Set[str]) -> discord.Embed:
        """Create a Discord embed for the SHIFT code post."""
        post = post_data.get("data", {})
        title = post.get("title", "SHIFT Code Found")
        author = post.get("author", "Unknown")
        url = f"https://reddit.com{post.get('permalink', '')}"
        created_utc = post.get("created_utc", 0)
        
        embed = discord.Embed(
            title="🔑 Borderlands 4 SHIFT Code(s) Found!",
            color=discord.Color.gold(),
            timestamp=datetime.fromtimestamp(created_utc, timezone.utc)
        )
        
        embed.add_field(name="Reddit Post", value=f"[{title}]({url})", inline=False)
        embed.add_field(name="Author", value=f"u/{author}", inline=True)
        
        # Add codes
        codes_text = "\n".join(f"`{code}`" for code in sorted(codes))
        embed.add_field(name="SHIFT Codes", value=codes_text, inline=False)
        
        embed.add_field(
            name="How to Redeem", 
            value="Go to [shift.gearboxsoftware.com](https://shift.gearboxsoftware.com) or use the in-game menu",
            inline=False
        )
        
        embed.set_footer(text="Auto-posted by BL4 SHIFT Monitor")
        return embed
    
    async def _monitor_reddit(self):
        """Main monitoring loop."""
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed():
            try:
                # Check all configured guilds
                for guild in self.bot.guilds:
                    guild_config = self.config.guild(guild)
                    
                    channel_id = await guild_config.channel_id()
                    if not channel_id:
                        continue
                        
                    channel = guild.get_channel(channel_id)
                    if not channel:
                        continue
                    
                    subreddit = await guild_config.subreddit()
                    keywords = await guild_config.keywords()
                    posted_codes = await guild_config.posted_codes()
                    
                    # Fetch recent posts
                    posts = await self._get_reddit_posts(subreddit)
                    
                    for post_data in posts:
                        post = post_data.get("data", {})
                        post_id = post.get("id", "")
                        title = post.get("title", "")
                        selftext = post.get("selftext", "")
                        
                        # Skip if already processed
                        if post_id in posted_codes:
                            continue
                        
                        # Check if BL4 related
                        if not self._is_bl4_related(title, selftext, keywords):
                            continue
                        
                        # Extract SHIFT codes
                        codes = self._extract_shift_codes(f"{title} {selftext}")
                        
                        if codes:
                            # Check if we've already posted these specific codes
                            new_codes = codes - set(posted_codes.get(post_id, {}).get("codes", []))
                            
                            if new_codes:
                                try:
                                    embed = await self._create_embed(post_data, codes)
                                    result = await self._post_to_channel_or_thread(guild, channel, embed, guild_config)
                                    
                                    if result:  # Successfully posted
                                        # Mark as posted
                                        posted_codes[post_id] = {
                                            "codes": list(codes),
                                            "timestamp": datetime.now(timezone.utc).isoformat()
                                        }
                                        await guild_config.posted_codes.set(posted_codes)
                                        
                                        log.info(f"Posted SHIFT codes to {guild.name}: {codes}")
                                    
                                except Exception as e:
                                    log.error(f"Error posting to channel {channel_id}: {e}")
                
                # Wait before next check
                interval = 300  # Default 5 minutes
                if self.bot.guilds:
                    # Use the first configured guild's interval
                    for guild in self.bot.guilds:
                        interval = await self.config.guild(guild).check_interval()
                        if interval:
                            break
                
                await asyncio.sleep(interval)
                
            except Exception as e:
                log.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(60)  # Wait 1 minute on error
    
    @commands.group(name="bl4shift")
    @checks.admin_or_permissions(manage_guild=True)
    async def bl4shift(self, ctx):
        """Borderlands 4 SHIFT code monitoring commands."""
        pass
    
    @bl4shift.command(name="listchannels")
    async def list_channels(self, ctx):
        """List all channels the bot can see to help with setup."""
        text_channels = []
        forum_channels = []
        
        for channel in ctx.guild.channels:
            if isinstance(channel, discord.TextChannel):
                text_channels.append(f"• {channel.mention} (ID: {channel.id})")
            elif isinstance(channel, discord.ForumChannel):
                forum_channels.append(f"• {channel.mention} (ID: {channel.id})")
        
        embed = discord.Embed(title="Available Channels", color=discord.Color.blue())
        
        if text_channels:
            text_list = "\n".join(text_channels[:10])  # Limit to 10 to avoid embed limits
            if len(text_channels) > 10:
                text_list += f"\n... and {len(text_channels) - 10} more"
            embed.add_field(name="Text Channels", value=text_list, inline=False)
        
        if forum_channels:
            forum_list = "\n".join(forum_channels[:10])
            if len(forum_channels) > 10:
                forum_list += f"\n... and {len(forum_channels) - 10} more"
            embed.add_field(name="Forum Channels", value=forum_list, inline=False)
        
        if not text_channels and not forum_channels:
            embed.description = "No text or forum channels found that the bot can access."
        
        await ctx.send(embed=embed)
    
    @bl4shift.command(name="setchannel")
    async def set_channel(self, ctx, *, channel_input: str):
        """Set the channel to post SHIFT codes to. Can be a text channel or forum channel.
        Use channel mention (#channel), channel ID, or channel name."""
        
        channel = None
        
        # Try to find the channel by various methods
        try:
            # Remove # if present and clean the input
            channel_input = channel_input.strip().lstrip('#')
            
            # Try by ID first (if it's all digits)
            if channel_input.isdigit():
                channel = ctx.guild.get_channel(int(channel_input))
            
            # If not found, try by name
            if not channel:
                for ch in ctx.guild.channels:
                    if ch.name.lower() == channel_input.lower():
                        channel = ch
                        break
            
            # If still not found, try by mention format <#1234567890>
            if not channel and channel_input.startswith('<#') and channel_input.endswith('>'):
                channel_id = channel_input[2:-1]
                if channel_id.isdigit():
                    channel = ctx.guild.get_channel(int(channel_id))
                    
        except (ValueError, AttributeError):
            pass
        
        if not channel:
            await ctx.send(f"❌ Could not find channel: `{channel_input}`\n"
                          f"Try using:\n"
                          f"• Channel mention: `#channel-name`\n"
                          f"• Channel ID: `1234567890123456789`\n"
                          f"• Channel name: `channel-name`")
            return
        
        # Check if it's a valid channel type
        if not isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
            await ctx.send(f"❌ `{channel.name}` is not a text channel or forum channel.\n"
                          f"Channel type: {type(channel).__name__}")
            return
            
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        
        # Set forum mode based on channel type
        is_forum = isinstance(channel, discord.ForumChannel)
        await self.config.guild(ctx.guild).use_forum.set(is_forum)
        
        if is_forum:
            await ctx.send(f"✅ SHIFT codes will now be posted to forum {channel.mention}")
        else:
            await ctx.send(f"✅ SHIFT codes will now be posted to {channel.mention}")
        
        # Restart monitoring with new config
        await self._start_monitoring_tasks()
    
    @bl4shift.command(name="setsubreddit")
    async def set_subreddit(self, ctx, subreddit: str):
        """Set the subreddit to monitor (without r/)."""
        subreddit = subreddit.replace("r/", "").replace("/", "")
        await self.config.guild(ctx.guild).subreddit.set(subreddit)
        await ctx.send(f"✅ Now monitoring r/{subreddit}")
        
        # Restart monitoring
        await self._start_monitoring_tasks()
    
    @bl4shift.command(name="setinterval")
    async def set_interval(self, ctx, minutes: int):
        """Set how often to check for new codes (in minutes). Minimum 1 minute."""
        if minutes < 1:
            await ctx.send("❌ Interval must be at least 1 minute.")
            return
            
        await self.config.guild(ctx.guild).check_interval.set(minutes * 60)
        await ctx.send(f"✅ Check interval set to {minutes} minute(s)")
        
        # Restart monitoring
        await self._start_monitoring_tasks()
    
    @bl4shift.command(name="addkeyword")
    async def add_keyword(self, ctx, *, keyword: str):
        """Add a keyword to search for in posts."""
        keywords = await self.config.guild(ctx.guild).keywords()
        if keyword.lower() not in [k.lower() for k in keywords]:
            keywords.append(keyword.lower())
            await self.config.guild(ctx.guild).keywords.set(keywords)
            await ctx.send(f"✅ Added keyword: {keyword}")
        else:
            await ctx.send("❌ Keyword already exists.")
    
    @bl4shift.command(name="removekeyword")
    async def remove_keyword(self, ctx, *, keyword: str):
        """Remove a keyword from the search list."""
        keywords = await self.config.guild(ctx.guild).keywords()
        keywords = [k for k in keywords if k.lower() != keyword.lower()]
        await self.config.guild(ctx.guild).keywords.set(keywords)
        await ctx.send(f"✅ Removed keyword: {keyword}")
    
    @bl4shift.command(name="forum")
    async def forum_settings(self, ctx, enabled: bool = None):
        """Enable/disable forum mode or show current status."""
        if enabled is None:
            # Show current status
            use_forum = await self.config.guild(ctx.guild).use_forum()
            channel_id = await self.config.guild(ctx.guild).channel_id()
            channel = ctx.guild.get_channel(channel_id) if channel_id else None
            
            status = "✅ Enabled" if use_forum else "❌ Disabled"
            channel_type = "Forum" if isinstance(channel, discord.ForumChannel) else "Text"
            
            embed = discord.Embed(title="Forum Mode Status", color=discord.Color.blue())
            embed.add_field(name="Forum Mode", value=status, inline=True)
            embed.add_field(name="Channel Type", value=channel_type, inline=True)
            embed.add_field(name="Channel", value=channel.mention if channel else "Not set", inline=True)
            
            if use_forum:
                thread_template = await self.config.guild(ctx.guild).thread_name_template()
                create_daily = await self.config.guild(ctx.guild).create_new_thread_daily()
                active_thread_id = await self.config.guild(ctx.guild).active_thread_id()
                
                embed.add_field(name="Thread Template", value=f"`{thread_template}`", inline=False)
                embed.add_field(name="Daily Threads", value="✅ Yes" if create_daily else "❌ No", inline=True)
                
                if active_thread_id and isinstance(channel, discord.ForumChannel):
                    thread = channel.get_thread(active_thread_id)
                    embed.add_field(
                        name="Active Thread", 
                        value=thread.mention if thread else "None/Archived", 
                        inline=True
                    )
            
            await ctx.send(embed=embed)
        else:
            # Set forum mode
            await self.config.guild(ctx.guild).use_forum.set(enabled)
            status = "enabled" if enabled else "disabled"
            await ctx.send(f"✅ Forum mode {status}")
            
            if enabled:
                channel_id = await self.config.guild(ctx.guild).channel_id()
                channel = ctx.guild.get_channel(channel_id) if channel_id else None
                if channel and not isinstance(channel, discord.ForumChannel):
                    await ctx.send("⚠️ Warning: Current channel is not a forum channel. Use `setchannel` with a forum channel.")
    
    @bl4shift.command(name="threadtemplate")
    async def set_thread_template(self, ctx, *, template: str):
        """Set the template for thread names. Use {date} for current date."""
        await self.config.guild(ctx.guild).thread_name_template.set(template)
        await ctx.send(f"✅ Thread name template set to: `{template}`")
        
        # Show example
        example_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        example_name = template.format(date=example_date)
        await ctx.send(f"Example thread name: `{example_name}`")
    
    @bl4shift.command(name="dailythreads")
    async def toggle_daily_threads(self, ctx, enabled: bool):
        """Enable/disable creating new threads daily."""
        await self.config.guild(ctx.guild).create_new_thread_daily.set(enabled)
        status = "enabled" if enabled else "disabled"
        await ctx.send(f"✅ Daily thread creation {status}")
        
        if not enabled:
            await ctx.send("ℹ️ SHIFT codes will continue using the same thread until manually changed.")
    
    @bl4shift.command(name="newthread")
    async def create_new_thread(self, ctx):
        """Manually create a new thread (forum mode only)."""
        use_forum = await self.config.guild(ctx.guild).use_forum()
        if not use_forum:
            await ctx.send("❌ Forum mode is not enabled. Use `[p]bl4shift forum true` first.")
            return
            
        channel_id = await self.config.guild(ctx.guild).channel_id()
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        
        if not channel or not isinstance(channel, discord.ForumChannel):
            await ctx.send("❌ No forum channel configured. Use `[p]bl4shift setchannel` with a forum channel.")
            return
        
        guild_config = self.config.guild(ctx.guild)
        
        # Archive current thread if exists
        active_thread_id = await guild_config.active_thread_id()
        if active_thread_id:
            try:
                old_thread = channel.get_thread(active_thread_id)
                if old_thread and not old_thread.archived:
                    await old_thread.edit(archived=True)
                    await ctx.send(f"📁 Archived old thread: {old_thread.name}")
            except:
                pass
        
        # Create new thread
        thread = await self._get_or_create_thread(ctx.guild, channel, guild_config)
        if thread:
            await ctx.send(f"✅ Created new thread: {thread.mention}")
        else:
            await ctx.send("❌ Failed to create new thread.")
    
    @bl4shift.command(name="settings")
    async def show_settings(self, ctx):
        """Show current configuration."""
        guild_config = self.config.guild(ctx.guild)
        
        channel_id = await guild_config.channel_id()
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        subreddit = await guild_config.subreddit()
        interval = await guild_config.check_interval()
        keywords = await guild_config.keywords()
        use_forum = await guild_config.use_forum()
        
        embed = discord.Embed(title="BL4 SHIFT Monitor Settings", color=discord.Color.blue())
        
        # Basic settings
        channel_type = "Forum" if isinstance(channel, discord.ForumChannel) else "Text"
        embed.add_field(name="Channel", value=f"{channel.mention} ({channel_type})" if channel else "Not set", inline=True)
        embed.add_field(name="Subreddit", value=f"r/{subreddit}", inline=True)
        embed.add_field(name="Check Interval", value=f"{interval // 60} minute(s)", inline=True)
        embed.add_field(name="Keywords", value=", ".join(keywords), inline=False)
        
        # Forum settings
        if use_forum:
            thread_template = await guild_config.thread_name_template()
            create_daily = await guild_config.create_new_thread_daily()
            active_thread_id = await guild_config.active_thread_id()
            
            embed.add_field(name="Forum Mode", value="✅ Enabled", inline=True)
            embed.add_field(name="Thread Template", value=f"`{thread_template}`", inline=True)
            embed.add_field(name="Daily Threads", value="✅ Yes" if create_daily else "❌ No", inline=True)
            
            if active_thread_id and isinstance(channel, discord.ForumChannel):
                thread = channel.get_thread(active_thread_id)
                embed.add_field(
                    name="Active Thread", 
                    value=thread.mention if thread else "None/Archived", 
                    inline=False
                )
        else:
            embed.add_field(name="Forum Mode", value="❌ Disabled", inline=True)
        
        embed.add_field(name="Status", value="✅ Active" if channel else "❌ Inactive", inline=True)
        
        await ctx.send(embed=embed)
    
    @bl4shift.command(name="test")
    async def test_extraction(self, ctx, *, text: str):
        """Test SHIFT code extraction from text."""
        codes = self._extract_shift_codes(text)
        
        if codes:
            codes_list = "\n".join(f"• `{code}`" for code in sorted(codes))
            await ctx.send(f"**Found codes:**\n{codes_list}")
        else:
            await ctx.send("❌ No SHIFT codes found in the provided text.")
    
    @bl4shift.command(name="clearcache")
    async def clear_cache(self, ctx):
        """Clear the cache of posted codes."""
        await self.config.guild(ctx.guild).posted_codes.set({})
        await ctx.send("✅ Posted codes cache cleared.")
    
    @bl4shift.command(name="check")
    async def manual_check(self, ctx):
        """Manually trigger a check for new SHIFT codes."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        if not channel_id:
            await ctx.send("❌ No channel configured. Use `setchannel` first.")
            return
            
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("❌ Configured channel not found.")
            return
        
        await ctx.send("🔍 Checking for new SHIFT codes...")
        
        try:
            guild_config = self.config.guild(ctx.guild)
            subreddit = await guild_config.subreddit()
            keywords = await guild_config.keywords()
            posted_codes = await guild_config.posted_codes()
            
            # Fetch recent posts
            posts = await self._get_reddit_posts(subreddit)
            
            found_new = False
            codes_found = set()
            
            for post_data in posts:
                post = post_data.get("data", {})
                post_id = post.get("id", "")
                title = post.get("title", "")
                selftext = post.get("selftext", "")
                
                # Skip if already processed
                if post_id in posted_codes:
                    continue
                
                # Check if BL4 related
                if not self._is_bl4_related(title, selftext, keywords):
                    continue
                
                # Extract SHIFT codes
                codes = self._extract_shift_codes(f"{title} {selftext}")
                
                if codes:
                    # Check if we've already posted these specific codes
                    new_codes = codes - set(posted_codes.get(post_id, {}).get("codes", []))
                    
                    if new_codes:
                        try:
                            embed = await self._create_embed(post_data, codes)
                            result = await self._post_to_channel_or_thread(ctx.guild, channel, embed, guild_config)
                            
                            if result:  # Successfully posted
                                # Mark as posted
                                posted_codes[post_id] = {
                                    "codes": list(codes),
                                    "timestamp": datetime.now(timezone.utc).isoformat()
                                }
                                await guild_config.posted_codes.set(posted_codes)
                                
                                found_new = True
                                codes_found.update(codes)
                                
                        except Exception as e:
                            await ctx.send(f"❌ Error posting codes: {e}")
                            return
            
            if found_new:
                codes_list = ", ".join(f"`{code}`" for code in sorted(codes_found))
                await ctx.send(f"✅ Found and posted new SHIFT codes: {codes_list}")
            else:
                await ctx.send("ℹ️ No new SHIFT codes found.")
                
        except Exception as e:
            await ctx.send(f"❌ Error during manual check: {e}")

async def setup(bot: Red):
    """Set up the cog."""
    cog = BL4ShiftCodes(bot)
    await bot.add_cog(cog)