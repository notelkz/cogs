import asyncio
import re
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional, Set, Dict, Any, List
import hashlib

import aiohttp
import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box, pagify

log = logging.getLogger("red.bl4shift")

class BL4ShiftCodes(commands.Cog):
    """Monitor multiple sources for Borderlands 4 SHIFT codes and post to Discord."""
    
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        
        # Default settings
        default_guild = {
            "channel_id": None,
            "check_interval": 300,  # 5 minutes
            "keywords": ["shift", "code", "borderlands 4", "bl4", "golden key"],
            "posted_codes": {},  # Track posted codes to avoid duplicates
            "use_forum": False,  # Whether to use forum channels
            "thread_name_template": "SHIFT Codes - {date}",  # Template for thread names
            "create_new_thread_daily": False,  # Create new thread each day
            "active_thread_id": None,  # Current active thread
            "enabled_sources": {
                "gearbox_rss": True,
                "borderlands_rss": True,
                "shift_codes_rss": True,
                "gaming_news_rss": True,
                "twitter_rss": True
            },
            "last_check_times": {}  # Track last check time per source
        }
        
        self.config.register_guild(**default_guild)
        
        # SHIFT code patterns (improved)
        self.shift_patterns = [
            r'[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}',  # Standard format
            r'(?:SHIFT|CODE)[\s:]*([A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5})',
            r'(?:SHIFT|CODE)[\s:]*([A-Z0-9]{25})',  # No dashes
            r'\b[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{5}\b'  # Word boundary
        ]
        
        # Sources to monitor
        self.sources = {
            "gearbox_rss": {
                "name": "Gearbox Official Blog",
                "url": "https://gearboxsoftware.com/feed/",
                "type": "rss"
            },
            "borderlands_rss": {
                "name": "Borderlands Reddit RSS",
                "url": "https://www.reddit.com/r/borderlands.rss",
                "type": "rss"
            },
            "shift_codes_rss": {
                "name": "BorderlandsShiftCodes Reddit RSS", 
                "url": "https://www.reddit.com/r/BorderlandsShiftCodes.rss",
                "type": "rss"
            },
            "gaming_news_rss": {
                "name": "PC Gamer RSS",
                "url": "https://www.pcgamer.com/rss/",
                "type": "rss"
            },
            "twitter_rss": {
                "name": "Gearbox Twitter RSS",
                "url": "https://rsshub.app/twitter/user/GearboxOfficial",
                "type": "rss"
            }
        }
        
        self.session: Optional[aiohttp.ClientSession] = None
        self.monitor_task: Optional[asyncio.Task] = None
        
    async def cog_load(self):
        """Initialize the cog."""
        self.session = aiohttp.ClientSession()
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
        
        self.monitor_task = asyncio.create_task(self._monitor_sources())
        
    async def _fetch_rss_feed(self, url: str, source_name: str) -> List[Dict[str, Any]]:
        """Fetch and parse RSS feed."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/rss+xml, application/xml, text/xml, text/plain, */*"
        }
        
        try:
            async with self.session.get(url, headers=headers, timeout=30) as response:
                if response.status == 200:
                    content = await response.text()
                    return self._parse_rss(content, source_name)
                else:
                    log.warning(f"RSS fetch failed for {source_name}: HTTP {response.status}")
                    return []
        except asyncio.TimeoutError:
            log.warning(f"RSS fetch timeout for {source_name}")
            return []
        except Exception as e:
            log.error(f"RSS fetch error for {source_name}: {e}")
            return []
    
    def _parse_rss(self, content: str, source_name: str) -> List[Dict[str, Any]]:
        """Parse RSS XML content."""
        try:
            root = ET.fromstring(content)
            items = []
            
            # Handle different RSS formats
            for item in root.findall('.//item'):
                title_elem = item.find('title')
                description_elem = item.find('description')
                link_elem = item.find('link')
                pubdate_elem = item.find('pubDate')
                
                title = title_elem.text if title_elem is not None else ""
                description = description_elem.text if description_elem is not None else ""
                link = link_elem.text if link_elem is not None else ""
                pubdate = pubdate_elem.text if pubdate_elem is not None else ""
                
                # Create unique ID for this item
                item_id = hashlib.md5(f"{title}{link}{source_name}".encode()).hexdigest()
                
                items.append({
                    "id": item_id,
                    "title": title,
                    "description": description,
                    "link": link,
                    "pubdate": pubdate,
                    "source": source_name
                })
            
            log.info(f"Parsed {len(items)} items from {source_name}")
            return items
            
        except ET.ParseError as e:
            log.error(f"XML parse error for {source_name}: {e}")
            return []
        except Exception as e:
            log.error(f"RSS parse error for {source_name}: {e}")
            return []
    
    def _extract_shift_codes(self, text: str) -> Set[str]:
        """Extract SHIFT codes from text using regex patterns."""
        codes = set()
        text_upper = text.upper()
        
        for pattern in self.shift_patterns:
            matches = re.findall(pattern, text_upper, re.IGNORECASE)
            for match in matches:
                # Handle tuple results from capture groups
                if isinstance(match, tuple):
                    match = match[0] if match else ""
                
                # Remove existing dashes and add them back in standard format
                clean_code = re.sub(r'[^A-Z0-9]', '', match)
                if len(clean_code) == 25:
                    formatted_code = f"{clean_code[:5]}-{clean_code[5:10]}-{clean_code[10:15]}-{clean_code[15:20]}-{clean_code[20:25]}"
                    codes.add(formatted_code)
                    
        return codes
    
    def _is_bl4_related(self, title: str, text: str, keywords: list) -> bool:
        """Check if content is related to Borderlands 4."""
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
                    if create_new_daily:
                        thread_date = thread.created_at.date()
                        today = datetime.now(timezone.utc).date()
                        
                        if thread_date < today:
                            try:
                                await thread.edit(archived=True)
                            except:
                                pass
                            thread = None
                        else:
                            return thread
                    else:
                        return thread
            except:
                pass
        
        # Create new thread
        try:
            current_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            thread_name = thread_template.format(date=current_date)
            
            embed = discord.Embed(
                title="🔑 Borderlands 4 SHIFT Codes",
                description="This thread will be updated with new SHIFT codes as they're found from multiple sources!",
                color=discord.Color.gold()
            )
            embed.add_field(
                name="Monitored Sources",
                value="• Gearbox Official Blog\n• Reddit Communities\n• Gaming News Sites\n• Official Twitter",
                inline=False
            )
            embed.add_field(
                name="How to Redeem",
                value="Visit [shift.gearboxsoftware.com](https://shift.gearboxsoftware.com) or use the in-game menu",
                inline=False
            )
            embed.set_footer(text="Auto-managed by BL4 SHIFT Monitor")
            
            thread = await channel.create_thread(
                name=thread_name,
                embed=embed,
                reason="Auto-created for SHIFT code monitoring"
            )
            
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
            thread = await self._get_or_create_thread(guild, channel, guild_config)
            if thread:
                await thread.send(embed=embed)
                return thread
            else:
                log.error(f"Could not get or create thread in forum {channel.id}")
                return None
        else:
            await channel.send(embed=embed)
            return channel
    
    async def _create_embed(self, item_data: dict, codes: Set[str], source_name: str) -> discord.Embed:
        """Create a Discord embed for the SHIFT code post."""
        title = item_data.get("title", "SHIFT Code Found")
        link = item_data.get("link", "")
        
        embed = discord.Embed(
            title="🔑 Borderlands 4 SHIFT Code(s) Found!",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )
        
        embed.add_field(name="Source", value=f"[{title}]({link})" if link else title, inline=False)
        embed.add_field(name="Found via", value=source_name, inline=True)
        
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
    
    async def _monitor_sources(self):
        """Main monitoring loop for all sources."""
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed():
            try:
                for guild in self.bot.guilds:
                    guild_config = self.config.guild(guild)
                    
                    channel_id = await guild_config.channel_id()
                    if not channel_id:
                        continue
                        
                    channel = guild.get_channel(channel_id)
                    if not channel:
                        continue
                    
                    keywords = await guild_config.keywords()
                    posted_codes = await guild_config.posted_codes()
                    enabled_sources = await guild_config.enabled_sources()
                    last_check_times = await guild_config.last_check_times()
                    
                    # Check each enabled source
                    for source_id, source_info in self.sources.items():
                        if not enabled_sources.get(source_id, True):
                            continue
                        
                        try:
                            log.info(f"Checking {source_info['name']} for guild {guild.name}")
                            items = await self._fetch_rss_feed(source_info['url'], source_info['name'])
                            
                            for item in items:
                                item_id = item.get("id", "")
                                title = item.get("title", "")
                                description = item.get("description", "")
                                
                                # Skip if already processed
                                if item_id in posted_codes:
                                    continue
                                
                                # Check if BL4 related
                                if not self._is_bl4_related(title, description, keywords):
                                    continue
                                
                                # Extract SHIFT codes
                                codes = self._extract_shift_codes(f"{title} {description}")
                                
                                if codes:
                                    try:
                                        embed = await self._create_embed(item, codes, source_info['name'])
                                        result = await self._post_to_channel_or_thread(guild, channel, embed, guild_config)
                                        
                                        if result:
                                            # Mark as posted
                                            posted_codes[item_id] = {
                                                "codes": list(codes),
                                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                                "source": source_info['name']
                                            }
                                            await guild_config.posted_codes.set(posted_codes)
                                            
                                            log.info(f"Posted SHIFT codes from {source_info['name']} to {guild.name}: {codes}")
                                            
                                    except Exception as e:
                                        log.error(f"Error posting codes from {source_info['name']}: {e}")
                        
                        except Exception as e:
                            log.error(f"Error checking {source_info['name']}: {e}")
                        
                        # Small delay between sources
                        await asyncio.sleep(2)
                
                # Wait before next check cycle
                interval = 300  # Default 5 minutes
                if self.bot.guilds:
                    for guild in self.bot.guilds:
                        interval = await self.config.guild(guild).check_interval()
                        if interval:
                            break
                
                log.info(f"Completed check cycle, waiting {interval} seconds")
                await asyncio.sleep(interval)
                
            except Exception as e:
                log.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(60)
    
    @commands.group(name="bl4shift")
    @checks.admin_or_permissions(manage_guild=True)
    async def bl4shift(self, ctx):
        """Borderlands 4 SHIFT code monitoring commands."""
        pass
    
    @bl4shift.command(name="setchannel")
    async def set_channel(self, ctx, *, channel_input: str):
        """Set the channel to post SHIFT codes to. Can be a text channel or forum channel."""
        
        channel = None
        
        try:
            channel_input = channel_input.strip().lstrip('#')
            
            if channel_input.isdigit():
                channel = ctx.guild.get_channel(int(channel_input))
            
            if not channel:
                for ch in ctx.guild.channels:
                    if ch.name.lower() == channel_input.lower():
                        channel = ch
                        break
            
            if not channel and channel_input.startswith('<#') and channel_input.endswith('>'):
                channel_id = channel_input[2:-1]
                if channel_id.isdigit():
                    channel = ctx.guild.get_channel(int(channel_id))
                    
        except (ValueError, AttributeError):
            pass
        
        if not channel:
            await ctx.send(f"❌ Could not find channel: `{channel_input}`")
            return
        
        if not isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
            await ctx.send(f"❌ `{channel.name}` is not a text channel or forum channel.")
            return
            
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        
        is_forum = isinstance(channel, discord.ForumChannel)
        await self.config.guild(ctx.guild).use_forum.set(is_forum)
        
        if is_forum:
            await ctx.send(f"✅ SHIFT codes will now be posted to forum {channel.mention}")
        else:
            await ctx.send(f"✅ SHIFT codes will now be posted to {channel.mention}")
        
        await self._start_monitoring_tasks()
    
    @bl4shift.command(name="sources")
    async def manage_sources(self, ctx, source_name: str = None, enabled: bool = None):
        """Enable/disable monitoring sources or show current status."""
        enabled_sources = await self.config.guild(ctx.guild).enabled_sources()
        
        if source_name is None:
            # Show all sources
            embed = discord.Embed(title="Monitoring Sources", color=discord.Color.blue())
            
            for source_id, source_info in self.sources.items():
                status = "✅ Enabled" if enabled_sources.get(source_id, True) else "❌ Disabled"
                embed.add_field(
                    name=source_info['name'],
                    value=f"{status}\n`{source_id}`",
                    inline=True
                )
            
            embed.set_footer(text="Use: !bl4shift sources <source_id> <true/false> to toggle")
            await ctx.send(embed=embed)
            return
        
        if source_name not in self.sources:
            await ctx.send(f"❌ Unknown source: `{source_name}`\nAvailable: {', '.join(self.sources.keys())}")
            return
        
        if enabled is None:
            # Show specific source status
            status = "✅ Enabled" if enabled_sources.get(source_name, True) else "❌ Disabled"
            await ctx.send(f"**{self.sources[source_name]['name']}:** {status}")
            return
        
        # Toggle source
        enabled_sources[source_name] = enabled
        await self.config.guild(ctx.guild).enabled_sources.set(enabled_sources)
        
        status = "enabled" if enabled else "disabled"
        await ctx.send(f"✅ {self.sources[source_name]['name']} {status}")
        
        await self._start_monitoring_tasks()
    
    @bl4shift.command(name="setinterval")
    async def set_interval(self, ctx, minutes: int):
        """Set how often to check for new codes (in minutes). Minimum 1 minute."""
        if minutes < 1:
            await ctx.send("❌ Interval must be at least 1 minute.")
            return
            
        await self.config.guild(ctx.guild).check_interval.set(minutes * 60)
        await ctx.send(f"✅ Check interval set to {minutes} minute(s)")
        
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
            use_forum = await self.config.guild(ctx.guild).use_forum()
            channel_id = await self.config.guild(ctx.guild).channel_id()
            channel = ctx.guild.get_channel(channel_id) if channel_id else None
            
            status = "✅ Enabled" if use_forum else "❌ Disabled"
            channel_type = "Forum" if isinstance(channel, discord.ForumChannel) else "Text"
            
            embed = discord.Embed(title="Forum Mode Status", color=discord.Color.blue())
            embed.add_field(name="Forum Mode", value=status, inline=True)
            embed.add_field(name="Channel Type", value=channel_type, inline=True)
            embed.add_field(name="Channel", value=channel.mention if channel else "Not set", inline=True)
            
            await ctx.send(embed=embed)
        else:
            await self.config.guild(ctx.guild).use_forum.set(enabled)
            status = "enabled" if enabled else "disabled"
            await ctx.send(f"✅ Forum mode {status}")
    
    @bl4shift.command(name="dailythreads")
    async def toggle_daily_threads(self, ctx, enabled: bool):
        """Enable/disable creating new threads daily."""
        await self.config.guild(ctx.guild).create_new_thread_daily.set(enabled)
        status = "enabled" if enabled else "disabled"
        await ctx.send(f"✅ Daily thread creation {status}")
    
    @bl4shift.command(name="settings")
    async def show_settings(self, ctx):
        """Show current configuration."""
        guild_config = self.config.guild(ctx.guild)
        
        channel_id = await guild_config.channel_id()
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        interval = await guild_config.check_interval()
        keywords = await guild_config.keywords()
        use_forum = await guild_config.use_forum()
        enabled_sources = await guild_config.enabled_sources()
        
        embed = discord.Embed(title="BL4 SHIFT Monitor Settings", color=discord.Color.blue())
        
        channel_type = "Forum" if isinstance(channel, discord.ForumChannel) else "Text"
        embed.add_field(name="Channel", value=f"{channel.mention} ({channel_type})" if channel else "Not set", inline=True)
        embed.add_field(name="Check Interval", value=f"{interval // 60} minute(s)", inline=True)
        embed.add_field(name="Forum Mode", value="✅ Yes" if use_forum else "❌ No", inline=True)
        embed.add_field(name="Keywords", value=", ".join(keywords), inline=False)
        
        # Show enabled sources
        enabled_count = sum(1 for enabled in enabled_sources.values() if enabled)
        total_count = len(self.sources)
        embed.add_field(name="Active Sources", value=f"{enabled_count}/{total_count}", inline=True)
        embed.add_field(name="Status", value="✅ Active" if channel else "❌ Inactive", inline=True)
        
        await ctx.send(embed=embed)
    
    @bl4shift.command(name="check")
    async def manual_check(self, ctx):
        """Manually trigger a check for new SHIFT codes from all sources."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        if not channel_id:
            await ctx.send("❌ No channel configured. Use `setchannel` first.")
            return
            
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("❌ Configured channel not found.")
            return
        
        await ctx.send("🔍 Checking all sources for new SHIFT codes...")
        
        guild_config = self.config.guild(ctx.guild)
        keywords = await guild_config.keywords()
        posted_codes = await guild_config.posted_codes()
        enabled_sources = await guild_config.enabled_sources()
        
        found_new = False
        total_codes = set()
        sources_checked = 0
        
        for source_id, source_info in self.sources.items():
            if not enabled_sources.get(source_id, True):
                continue
                
            sources_checked += 1
            
            try:
                items = await self._fetch_rss_feed(source_info['url'], source_info['name'])
                
                for item in items[:5]:  # Check recent 5 items per source
                    item_id = item.get("id", "")
                    title = item.get("title", "")
                    description = item.get("description", "")
                    
                    if item_id in posted_codes:
                        continue
                    
                    if not self._is_bl4_related(title, description, keywords):
                        continue
                    
                    codes = self._extract_shift_codes(f"{title} {description}")
                    
                    if codes:
                        embed = await self._create_embed(item, codes, source_info['name'])
                        result = await self._post_to_channel_or_thread(ctx.guild, channel, embed, guild_config)
                        
                        if result:
                            posted_codes[item_id] = {
                                "codes": list(codes),
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "source": source_info['name']
                            }
                            await guild_config.posted_codes.set(posted_codes)
                            
                            found_new = True
                            total_codes.update(codes)
            
            except Exception as e:
                await ctx.send(f"❌ Error checking {source_info['name']}: {e}")
        
        if found_new:
            codes_list = ", ".join(f"`{code}`" for code in sorted(total_codes))
            await ctx.send(f"✅ Found and posted new SHIFT codes: {codes_list}")
        else:
            await ctx.send(f"ℹ️ No new SHIFT codes found across {sources_checked} sources.")
    
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
    
    @bl4shift.command(name="debug")
    async def debug_sources(self, ctx, source_name: str = None):
        """Debug what's being found from each source."""
        keywords = await self.config.guild(ctx.guild).keywords()
        enabled_sources = await self.config.guild(ctx.guild).enabled_sources()
        
        if source_name:
            # Debug specific source
            if source_name not in self.sources:
                await ctx.send(f"❌ Unknown source: `{source_name}`\nAvailable: {', '.join(self.sources.keys())}")
                return
            sources_to_check = {source_name: self.sources[source_name]}
        else:
            # Debug all enabled sources
            sources_to_check = {k: v for k, v in self.sources.items() if enabled_sources.get(k, True)}
        
        await ctx.send(f"🔍 Debugging {len(sources_to_check)} source(s)...")
        
        for source_id, source_info in sources_to_check.items():
            await ctx.send(f"\n**🔍 Checking {source_info['name']}**")
            
            try:
                items = await self._fetch_rss_feed(source_info['url'], source_info['name'])
                
                if not items:
                    await ctx.send(f"❌ No items retrieved from {source_info['name']}")
                    continue
                
                await ctx.send(f"✅ Retrieved {len(items)} items")
                
                # Check first 3 items in detail
                for i, item in enumerate(items[:3]):
                    title = item.get("title", "No title")
                    description = item.get("description", "No description")
                    
                    # Check BL4 relevance
                    is_bl4 = self._is_bl4_related(title, description, keywords)
                    
                    # Extract codes
                    codes = self._extract_shift_codes(f"{title} {description}")
                    
                    status_parts = []
                    if is_bl4:
                        status_parts.append("✅ BL4 Related")
                    else:
                        status_parts.append("❌ Not BL4")
                    
                    if codes:
                        status_parts.append(f"🔑 {len(codes)} codes")
                    else:
                        status_parts.append("❌ No codes")
                    
                    status = " | ".join(status_parts)
                    
                    # Truncate long text for display
                    display_title = title[:80] + "..." if len(title) > 80 else title
                    display_desc = description[:100] + "..." if len(description) > 100 else description
                    
                    item_info = f"**Item {i+1}:** {display_title}\n"
                    item_info += f"**Status:** {status}\n"
                    
                    if codes:
                        item_info += f"**Codes:** {', '.join([f'`{code}`' for code in codes])}\n"
                    
                    # Show first bit of description for context
                    item_info += f"**Preview:** {display_desc}\n"
                    
                    await ctx.send(item_info)
                
            except Exception as e:
                await ctx.send(f"❌ Error with {source_info['name']}: {e}")
        
        # Show current filter settings
        await ctx.send(f"\n**Current Keywords:** {', '.join(keywords)}")
        
    @bl4shift.command(name="testsource")
    async def test_source_url(self, ctx, *, url: str):
        """Test fetching from a specific RSS URL."""
        await ctx.send(f"🧪 Testing RSS feed: {url}")
        
        try:
            items = await self._fetch_rss_feed(url, "Manual Test")
            
            if not items:
                await ctx.send("❌ No items retrieved or RSS parsing failed")
                return
            
            await ctx.send(f"✅ Successfully parsed {len(items)} items")
            
            # Show first item details
            if items:
                item = items[0]
                title = item.get("title", "No title")
                description = item.get("description", "No description")
                link = item.get("link", "No link")
                
                embed = discord.Embed(title="First Item Found", color=discord.Color.blue())
                embed.add_field(name="Title", value=title[:1000], inline=False)
                embed.add_field(name="Description", value=description[:1000], inline=False)
                if link:
                    embed.add_field(name="Link", value=link, inline=False)
                
                await ctx.send(embed=embed)
                
                # Test code extraction
                codes = self._extract_shift_codes(f"{title} {description}")
                if codes:
                    await ctx.send(f"🔑 **Codes found:** {', '.join([f'`{code}`' for code in codes])}")
                else:
                    await ctx.send("❌ No SHIFT codes detected in this content")
        
        except Exception as e:
            await ctx.send(f"❌ Error testing RSS feed: {e}")
    
    @bl4shift.command(name="addsource")
    async def add_custom_source(self, ctx, source_id: str, *, url: str):
        """Add a custom RSS source. Format: !bl4shift addsource my_source_name https://example.com/rss"""
        # Validate URL
        if not url.startswith(('http://', 'https://')):
            await ctx.send("❌ URL must start with http:// or https://")
            return
        
        # Test the RSS feed first
        await ctx.send(f"🧪 Testing RSS feed: {url}")
        
        try:
            items = await self._fetch_rss_feed(url, f"Test - {source_id}")
            
            if not items:
                await ctx.send("❌ Could not retrieve items from this RSS feed")
                return
            
            await ctx.send(f"✅ RSS feed works! Found {len(items)} items")
            
            # Add to sources temporarily (this won't persist across bot restarts)
            self.sources[source_id] = {
                "name": f"Custom - {source_id}",
                "url": url,
                "type": "rss"
            }
            
            # Enable it by default
            enabled_sources = await self.config.guild(ctx.guild).enabled_sources()
            enabled_sources[source_id] = True
            await self.config.guild(ctx.guild).enabled_sources.set(enabled_sources)
            
            await ctx.send(f"✅ Added custom source: `{source_id}`\n**URL:** {url}")
            await ctx.send("⚠️ Note: Custom sources won't persist across bot restarts")
            
        except Exception as e:
            await ctx.send(f"❌ Error testing RSS feed: {e}")
    
    @bl4shift.command(name="testpost")
    async def test_reddit_post(self, ctx, *, url: str):
        """Test a specific Reddit post for SHIFT codes. Use the .json URL."""
        await ctx.send(f"🧪 Testing Reddit post: {url}")
        
        # Convert regular Reddit URL to JSON if needed
        if "/comments/" in url and not url.endswith(".json"):
            url = url.rstrip("/") + ".json"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }
        
        try:
            async with self.session.get(url, headers=headers, timeout=30) as response:
                await ctx.send(f"**Status Code:** {response.status}")
                
                if response.status == 200:
                    try:
                        data = await response.json()
                        
                        # Reddit post JSON structure
                        if isinstance(data, list) and len(data) > 0:
                            post_data = data[0].get("data", {}).get("children", [])
                            if post_data:
                                post = post_data[0].get("data", {})
                                title = post.get("title", "")
                                selftext = post.get("selftext", "")
                                
                                await ctx.send(f"**Title:** {title}")
                                await ctx.send(f"**Content preview:** {selftext[:500]}...")
                                
                                # Test code extraction
                                all_text = f"{title} {selftext}"
                                codes = self._extract_shift_codes(all_text)
                                
                                if codes:
                                    codes_list = ", ".join([f"`{code}`" for code in codes])
                                    await ctx.send(f"🔑 **Codes found:** {codes_list}")
                                else:
                                    await ctx.send("❌ No SHIFT codes detected")
                                    
                                    # Show what patterns we're looking for
                                    await ctx.send("**Looking for patterns like:**")
                                    await ctx.send("• `ABCDE-12345-FGHIJ-67890-KLMNO`")
                                    await ctx.send("• `ABCDEFGHIJKLMNOPQRSTUVWXY` (25 chars)")
                                    
                                    # Test BL4 relevance
                                    keywords = await self.config.guild(ctx.guild).keywords()
                                    is_bl4 = self._is_bl4_related(title, selftext, keywords)
                                    await ctx.send(f"**BL4 Related:** {'✅ Yes' if is_bl4 else '❌ No'}")
                                    await ctx.send(f"**Keywords:** {', '.join(keywords)}")
                                
                            else:
                                await ctx.send("❌ No post data found in JSON")
                        else:
                            await ctx.send("❌ Unexpected JSON structure")
                            
                    except Exception as json_err:
                        text = await response.text()
                        await ctx.send(f"❌ JSON Error: {json_err}")
                        await ctx.send(f"**Raw response:** ```{text[:500]}```")
                        
                elif response.status == 403:
                    await ctx.send("❌ **403 Forbidden** - Reddit is blocking this request")
                    
                else:
                    text = await response.text()
                    await ctx.send(f"❌ **Error {response.status}**")
                    await ctx.send(f"**Response:** ```{text[:300]}```")
                    
        except Exception as e:
            await ctx.send(f"❌ **Connection Error:** {e}")

async def setup(bot: Red):
    """Set up the cog."""
    cog = BL4ShiftCodes(bot)
    await bot.add_cog(cog)