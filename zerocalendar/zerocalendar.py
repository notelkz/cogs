import discord
import asyncio
import aiohttp
import logging
import datetime
from discord.ext import commands, tasks
from typing import Optional, Dict, List, Any
from redbot.core import Config, checks

log = logging.getLogger("red.zerocogs.zerocalendar")


class ZeroCalendar(commands.Cog):
    """
    Calendar integration for Zero Lives Left
    
    This cog synchronizes events between Discord and the website.
    """
    
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.api_url = "https://zerolives.gg/api/events/"
        self.api_key = None
        self.sync_task.start()
    
    def cog_unload(self):
        self.sync_task.cancel()
    
    async def initialize(self):
        """Initialize the cog with stored configuration"""
        api_tokens = await self.bot.get_shared_api_tokens("zerolives")
        self.api_key = api_tokens.get("api_key") if api_tokens else None
        if not self.api_key:
            log.warning("API key not set. Use [p]set api zerolives api_key,YOUR_API_KEY to set it.")
    
    @tasks.loop(minutes=15)
    async def sync_task(self):
        """Periodically sync events between Discord and the website"""
        await self.bot.wait_until_ready()
        if not self.api_key:
            await self.initialize()
        
        try:
            await self.sync_events()
        except Exception as e:
            log.error(f"Error in sync task: {e}")
    
    async def sync_events(self):
        """Sync events between Discord and the website"""
        await self.pull_events_from_website()
        await self.push_events_to_website()
    
    async def pull_events_from_website(self):
        """Pull events from the website and create them in Discord if needed"""
        if not self.api_key:
            return
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.api_url,
                    headers={"Authorization": f"Token {self.api_key}"}
                ) as resp:
                    if resp.status != 200:
                        log.error(f"Failed to pull events from website: {resp.status}")
                        return
                    
                    data = await resp.json()
                    
                    for event_data in data:
                        if event_data.get("discord_event_id"):
                            continue
                        
                        discord_event = await self.create_discord_event(event_data)
                        
                        if discord_event:
                            await self.update_website_event(
                                event_data["uuid"], 
                                {"discord_event_id": str(discord_event.id)}
                            )
        except Exception as e:
            log.error(f"Error pulling events from website: {e}")
    
    async def push_events_to_website(self):
        """Push Discord events to the website if they don't exist there"""
        if not self.api_key:
            return
        
        try:
            for guild in self.bot.guilds:
                events = await guild.fetch_scheduled_events()
                
                for event in events:
                    exists = await self.check_event_exists_on_website(str(event.id))
                    
                    if not exists:
                        await self.create_website_event(event)
        except Exception as e:
            log.error(f"Error pushing events to website: {e}")
    
    async def check_event_exists_on_website(self, discord_event_id: str) -> bool:
        """Check if an event with the given Discord ID exists on the website"""
        if not self.api_key:
            return False
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_url}?discord_event_id={discord_event_id}",
                    headers={"Authorization": f"Token {self.api_key}"}
                ) as resp:
                    if resp.status != 200:
                        return False
                    
                    data = await resp.json()
                    return len(data) > 0
        except Exception as e:
            log.error(f"Error checking event exists: {e}")
            return False
    
    async def create_discord_event(self, event_data: Dict[str, Any]) -> Optional[discord.ScheduledEvent]:
        """Create a Discord scheduled event from website event data"""
        try:
            guild_id = event_data.get("discord_guild_id")
            if guild_id:
                guild = self.bot.get_guild(int(guild_id))
            else:
                guild = self.bot.guilds[0] if self.bot.guilds else None
            
            if not guild:
                log.error("No guild found for event")
                return None
            
            start_time = datetime.datetime.fromisoformat(event_data["start_time"].replace('Z', '+00:00'))
            
            end_time = None
            if event_data.get("end_time"):
                end_time = datetime.datetime.fromisoformat(event_data["end_time"].replace('Z', '+00:00'))
            else:
                end_time = start_time + datetime.timedelta(hours=1)
            
            event = await guild.create_scheduled_event(
                name=event_data["title"],
                description=event_data.get("description", ""),
                start_time=start_time,
                end_time=end_time,
                location=event_data.get("location", "Online"),
                privacy_level=discord.PrivacyLevel.guild_only
            )
            return event
        except Exception as e:
            log.error(f"Failed to create Discord event: {e}")
            return None
    
    async def create_website_event(self, discord_event: discord.ScheduledEvent):
        """Create a website event from a Discord scheduled event"""
        if not self.api_key:
            return
        
        try:
            event_data = {
                "title": discord_event.name,
                "description": discord_event.description or "",
                "start_time": discord_event.start_time.isoformat(),
                "end_time": discord_event.end_time.isoformat() if discord_event.end_time else None,
                "location": discord_event.location or "Online",
                "discord_event_id": str(discord_event.id),
                "discord_channel_id": str(discord_event.channel_id) if discord_event.channel_id else None,
                "event_type": "community",
                "status": "scheduled"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.api_url,
                    headers={
                        "Authorization": f"Token {self.api_key}",
                        "Content-Type": "application/json",
                        "X-API-Key": self.api_key
                    },
                    json=event_data
                ) as resp:
                    if resp.status not in (200, 201):
                        log.error(f"Failed to create website event: {resp.status}")
                        log.error(await resp.text())
                        return
                    
                    log.info(f"Created website event for Discord event {discord_event.id}")
        except Exception as e:
            log.error(f"Error creating website event: {e}")
    
    async def update_website_event(self, uuid: str, data: Dict[str, Any]):
        """Update a website event with the given data"""
        if not self.api_key:
            return
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.patch(
                    f"{self.api_url}{uuid}/",
                    headers={
                        "Authorization": f"Token {self.api_key}",
                        "Content-Type": "application/json",
                        "X-API-Key": self.api_key
                    },
                    json=data
                ) as resp:
                    if resp.status != 200:
                        log.error(f"Failed to update website event: {resp.status}")
                        log.error(await resp.text())
                        return
                    
                    log.info(f"Updated website event {uuid}")
        except Exception as e:
            log.error(f"Error updating website event: {e}")
    
    @commands.group(name="calendar")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def calendar(self, ctx):
        """Calendar management commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)
    
    @calendar.command(name="sync")
    async def calendar_sync(self, ctx):
        """Manually trigger a sync between Discord and the website"""
        await ctx.send("Syncing events between Discord and the website...")
        try:
            await self.sync_events()
            await ctx.send("Sync completed successfully!")
        except Exception as e:
            await ctx.send(f"Error during sync: {e}")
    
    @calendar.command(name="list")
    async def calendar_list(self, ctx):
        """List upcoming events"""
        try:
            events = await ctx.guild.fetch_scheduled_events()
            
            if not events:
                await ctx.send("No upcoming events found.")
                return
            
            events = sorted(events, key=lambda e: e.start_time)
            
            embed = discord.Embed(
                title="Upcoming Events",
                color=discord.Color.blue()
            )
            
            for event in events[:10]:
                start_time = event.start_time.strftime("%Y-%m-%d %H:%M UTC")
                embed.add_field(
                    name=event.name,
                    value=f"**When:** {start_time}\n**Where:** {event.location or 'Online'}\n[View Event](https://discord.com/events/{ctx.guild.id}/{event.id})",
                    inline=False
                )
            
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(f"Error fetching events: {e}")
    
    @commands.Cog.listener()
    async def on_scheduled_event_create(self, event):
        """When a Discord event is created, sync it to the website"""
        try:
            exists = await self.check_event_exists_on_website(str(event.id))
            
            if not exists:
                await self.create_website_event(event)
        except Exception as e:
            log.error(f"Error handling event creation: {e}")
    
    @commands.Cog.listener()
    async def on_scheduled_event_update(self, before, after):
        """When a Discord event is updated, sync the changes to the website"""
        try:
            if not self.api_key:
                return
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_url}?discord_event_id={after.id}",
                    headers={"Authorization": f"Token {self.api_key}"}
                ) as resp:
                    if resp.status != 200:
                        return
                    
                    data = await resp.json()
                    if not data:
                        return
                    
                    website_event = data[0]
                    
                    update_data = {
                        "title": after.name,
                        "description": after.description or "",
                        "start_time": after.start_time.isoformat(),
                        "end_time": after.end_time.isoformat() if after.end_time else None,
                        "location": after.location or "Online",
                    }
                    
                    await self.update_website_event(website_event["uuid"], update_data)
        except Exception as e:
            log.error(f"Error handling event update: {e}")
    
    @commands.Cog.listener()
    async def on_scheduled_event_delete(self, event):
        """When a Discord event is deleted, update the website event status"""
        try:
            if not self.api_key:
                return
            
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_url}?discord_event_id={event.id}",
                    headers={"Authorization": f"Token {self.api_key}"}
                ) as resp:
                    if resp.status != 200:
                        return
                    
                    data = await resp.json()
                    if not data:
                        return
                    
                    website_event = data[0]
                    
                    await self.update_website_event(
                        website_event["uuid"], 
                        {"status": "canceled"}
                    )
        except Exception as e:
            log.error(f"Error handling event deletion: {e}")


async def setup(bot):
    await bot.add_cog(ZeroCalendar(bot))
