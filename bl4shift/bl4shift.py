@bl4shift.command(name="clearcache")
    async def clear_cache(self, ctx):
        """Clear the cache of posted codes."""
        await self.config.guild(ctx.guild).posted_codes.set({})
        await ctx.send("✅ Posted codes cache cleared.")import asyncio
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
            "posted_codes": {}  # Track posted codes to avoid duplicates
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
                                    await channel.send(embed=embed)
                                    
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
    
    @bl4shift.command(name="setchannel")
    async def set_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel to post SHIFT codes to."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
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
    
    @bl4shift.command(name="settings")
    async def show_settings(self, ctx):
        """Show current configuration."""
        guild_config = self.config.guild(ctx.guild)
        
        channel_id = await guild_config.channel_id()
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        subreddit = await guild_config.subreddit()
        interval = await guild_config.check_interval()
        keywords = await guild_config.keywords()
        
        embed = discord.Embed(title="BL4 SHIFT Monitor Settings", color=discord.Color.blue())
        embed.add_field(name="Channel", value=channel.mention if channel else "Not set", inline=True)
        embed.add_field(name="Subreddit", value=f"r/{subreddit}", inline=True)
        embed.add_field(name="Check Interval", value=f"{interval // 60} minute(s)", inline=True)
        embed.add_field(name="Keywords", value=", ".join(keywords), inline=False)
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

async def setup(bot: Red):
    """Set up the cog."""
    cog = BL4ShiftCodes(bot)
    await bot.add_cog(cog)