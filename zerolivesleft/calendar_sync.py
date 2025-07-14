# zerolivesleft/calendar_sync.py

import discord
import asyncio
import aiohttp
import logging
import datetime
from discord.ext import commands, tasks
from typing import Optional, Dict, List, Any

log = logging.getLogger("red.Elkz.zerolivesleft.calendar_sync")

class CalendarSyncLogic:
    """
    Manages calendar event synchronization between Discord and the website.
    """
    
    def __init__(self, cog_instance):
        self.cog = cog_instance # Reference to the main Zerolivesleft cog
        
        # Access central config and session
        self.config = cog_instance.config
        self.session = cog_instance.session

        # Task will be started via start_tasks() method
        self.sync_loop = None

    def start_tasks(self):
        """Starts the periodic event sync task."""
        if not self.sync_loop or not self.sync_loop.is_running():
            self.sync_loop = self.cog.bot.loop.create_task(self._start_sync_loop())

    def stop_tasks(self):
        """Stops the periodic event sync task."""
        if self.sync_loop and self.sync_loop.is_running():
            self.sync_loop.cancel()
    
    async def _start_sync_loop(self):
        """Internal method to start the sync loop task."""
        await self.cog.bot.wait_until_ready()
        
        # Access interval from main config (using gc_interval for now, but could be separate)
        # Assuming a default interval if not explicitly set for calendar sync
        interval_minutes = await self.config.cal_interval() if hasattr(self.config, 'cal_interval') else 15
        
        self.sync_loop_task = tasks.loop(minutes=interval_minutes)(self.sync_events_periodic)
        self.sync_loop_task.start()
        log.info(f"CalendarSync: Started sync loop with {interval_minutes} minute interval.")

    @tasks.loop(minutes=15) # Actual interval set by _start_sync_loop
    async def sync_events_periodic(self):
        """Periodically sync events between Discord and the website."""
        await self.cog.bot.wait_until_ready()
        
        api_url = await self.config.cal_api_url()
        api_key = await self.config.cal_api_key()

        if not api_url or not api_key:
            log.warning("CalendarSync: API URL or API key not set. Cannot sync events.")
            return

        try:
            log.info(f"CalendarSync: Starting sync with API URL: {api_url}")
            
            log.info("CalendarSync: Pulling events from website...")
            website_events = await self.pull_events_from_website()
            log.info(f"CalendarSync: Found {len(website_events) if website_events else 0} events on website")
            
            log.info("CalendarSync: Pushing Discord events to website...")
            discord_events_count = await self.push_events_to_website()
            log.info(f"CalendarSync: Pushed {discord_events_count} Discord events to website")
            
        except Exception as e:
            log.error(f"CalendarSync: Error in sync task: {e}", exc_info=True)
    
    async def pull_events_from_website(self):
        """Pull events from the website and create them in Discord if needed."""
        api_url = await self.config.cal_api_url()
        api_key = await self.config.cal_api_key()

        if not api_url or not api_key:
            return []
        
        try:
            async with self.session.get( # Use central session
                api_url,
                headers={"Authorization": f"Token {api_key}", "X-API-Key": api_key}
            ) as resp:
                if resp.status != 200:
                    log.error(f"CalendarSync: Failed to pull events from website: {resp.status}, Response: {await resp.text()}")
                    return []
                
                data = await resp.json()
                
                events_created = 0
                for event_data in data:
                    if event_data.get("discord_event_id"):
                        continue
                    
                    try:
                        discord_event = await self.create_discord_event(event_data)
                        
                        if discord_event:
                            await self.update_website_event(
                                event_data["uuid"], 
                                {"discord_event_id": str(discord_event.id)}
                            )
                            events_created += 1
                        else:
                            log.error(f"CalendarSync: Failed to create Discord event for '{event_data.get('title')}' (create_discord_event returned None)")
                    except Exception as e:
                        log.error(f"CalendarSync: Error creating Discord event for '{event_data.get('title')}': {e}")
                
                log.info(f"CalendarSync: Created {events_created} new Discord events from website data.")
                return data
        except Exception as e:
            log.error(f"CalendarSync: Exception during pull_events_from_website: {e}", exc_info=True)
            return []
    
    async def push_events_to_website(self):
        """Push Discord events to the website if they don't exist there."""
        api_url = await self.config.cal_api_url()
        api_key = await self.config.cal_api_key()

        if not api_url or not api_key:
            return 0
        
        events_created = 0
        
        for guild in self.cog.bot.guilds: # Use main cog's bot instance
            try:
                events = await guild.fetch_scheduled_events()
                
                for event in events:
                    exists = await self.check_event_exists_on_website(str(event.id))
                    
                    if not exists:
                        await self.create_website_event(event)
                        events_created += 1
            except Exception as e:
                log.error(f"CalendarSync: Error fetching or creating Discord events for guild '{guild.name}': {e}", exc_info=True)
        
        log.info(f"CalendarSync: Created {events_created} new website events from Discord data.")
        return events_created
    
    async def check_event_exists_on_website(self, discord_event_id: str) -> bool:
        """Check if an event with the given Discord ID exists on the website."""
        api_url = await self.config.cal_api_url()
        api_key = await self.config.cal_api_key()
        if not api_url or not api_key:
            return False
        
        try:
            async with self.session.get( # Use central session
                f"{api_url}?discord_event_id={discord_event_id}",
                headers={"Authorization": f"Token {api_key}", "X-API-Key": api_key}
            ) as resp:
                if resp.status != 200:
                    log.warning(f"CalendarSync: Failed to check event existence for {discord_event_id}: {resp.status}, Response: {await resp.text()}")
                    return False
                
                data = await resp.json()
                return len(data) > 0
        except Exception as e:
            log.error(f"CalendarSync: Exception checking event existence for {discord_event_id}: {e}", exc_info=True)
            return False
    
    async def create_discord_event(self, event_data: Dict[str, Any]) -> Optional[discord.ScheduledEvent]:
        """Create a Discord scheduled event from website event data."""
        # This will need DISCORD_GUILD_ID from env, or assume bot is only in one guild.
        # For a single-guild bot, it's often bot.guilds[0] or from a config setting.
        guild_id_str = os.environ.get("DISCORD_GUILD_ID") # Re-using this env var as ActivityTracker did
        guild = self.cog.bot.get_guild(int(guild_id_str)) if guild_id_str else self.cog.bot.guilds[0] if self.cog.bot.guilds else None
        
        if not guild:
            log.error("CalendarSync: No guild found to create Discord event in.")
            return None
        
        start_time = datetime.datetime.fromisoformat(event_data["start"].replace('Z', '+00:00')) # Adjusted key from 'start_time' to 'start' for FullCalendar API compatibility
        
        end_time = None
        if event_data.get("end"): # Adjusted key from 'end_time' to 'end'
            end_time = datetime.datetime.fromisoformat(event_data["end"].replace('Z', '+00:00'))
        else:
            end_time = start_time + datetime.timedelta(hours=1)
        
        try:
            event = await guild.create_scheduled_event(
                name=event_data["title"],
                description=event_data.get("description", ""), # Description can be optional
                start_time=start_time,
                end_time=end_time,
                location=event_data.get("location", "Online"),
                privacy_level=discord.PrivacyLevel.guild_only
            )
            log.info(f"CalendarSync: Created Discord event '{event.name}' ({event.id}).")
            return event
        except discord.HTTPException as e:
            log.error(f"CalendarSync: Failed to create Discord event: {e}", exc_info=True)
            return None
    
    async def create_website_event(self, discord_event: discord.ScheduledEvent):
        """Create a website event from a Discord scheduled event."""
        api_url = await self.config.cal_api_url()
        api_key = await self.config.cal_api_key()
        if not api_url or not api_key:
            return
        
        event_data = {
            "title": discord_event.name,
            "description": discord_event.description or "",
            "start": discord_event.start_time.isoformat(), # Adjusted key for FullCalendar API compatibility
            "end": discord_event.end_time.isoformat() if discord_event.end_time else None, # Adjusted key
            "location": discord_event.location or "Online",
            "discord_event_id": str(discord_event.id),
            "discord_channel_id": str(discord_event.channel_id) if discord_event.channel_id else None,
            "type": "community",  # Default type, adjusted key from 'event_type'
            "status": "scheduled"
        }
        
        try:
            async with self.session.post( # Use central session
                api_url,
                headers={
                    "Authorization": f"Token {api_key}",
                    "Content-Type": "application/json",
                    "X-API-Key": api_key
                },
                json=event_data
            ) as resp:
                if resp.status not in (200, 201):
                    log.error(f"CalendarSync: Failed to create website event for {discord_event.id}: {resp.status}, Response: {await resp.text()}")
                    return
                
                log.info(f"CalendarSync: Created website event for Discord event {discord_event.id}")
        except Exception as e:
            log.error(f"CalendarSync: Exception creating website event for {discord_event.id}: {e}", exc_info=True)
    
    async def update_website_event(self, uuid: str, data: Dict[str, Any]):
        """Update a website event with the given data."""
        api_url = await self.config.cal_api_url()
        api_key = await self.config.cal_api_key()
        if not api_url or not api_key:
            return
        
        try:
            async with self.session.patch( # Use central session
                f"{api_url}{uuid}/",
                headers={
                    "Authorization": f"Token {api_key}",
                    "Content-Type": "application/json",
                    "X-API-Key": api_key
                },
                json=data
            ) as resp:
                if resp.status != 200:
                    log.error(f"CalendarSync: Failed to update website event {uuid}: {resp.status}, Response: {await resp.text()}")
                    return
                
                log.info(f"CalendarSync: Updated website event {uuid}")
        except Exception as e:
            log.error(f"CalendarSync: Exception updating website event {uuid}: {e}", exc_info=True)
    
    # --- DISCORD LISTENERS ---
    
    @commands.Cog.listener()
    async def on_scheduled_event_create(self, event):
        """When a Discord event is created, sync it to the website."""
        log.info(f"CalendarSync: Discord event created: {event.name} ({event.id}). Syncing to website.")
        try:
            exists = await self.check_event_exists_on_website(str(event.id))
            if not exists:
                await self.create_website_event(event)
        except Exception as e:
            log.error(f"CalendarSync: Error handling event creation for {event.id}: {e}", exc_info=True)
    
    @commands.Cog.listener()
    async def on_scheduled_event_update(self, before, after):
        """When a Discord event is updated, sync the changes to the website."""
        log.info(f"CalendarSync: Discord event updated: {before.name} -> {after.name} ({after.id}). Syncing to website.")
        try:
            async with self.session.get( # Use central session
                f"{await self.config.cal_api_url()}?discord_event_id={after.id}",
                headers={"Authorization": f"Token {await self.config.cal_api_key()}", "X-API-Key": await self.config.cal_api_key()}
            ) as resp:
                if resp.status != 200:
                    log.warning(f"CalendarSync: Failed to find website event for update {after.id}: {resp.status}")
                    return
                
                data = await resp.json()
                if not data:
                    log.warning(f"CalendarSync: No website event found to update for Discord event {after.id}.")
                    return
                
                website_event = data[0]
                
                update_data = {
                    "title": after.name,
                    "description": after.description or "",
                    "start": after.start_time.isoformat(),
                    "end": after.end_time.isoformat() if after.end_time else None,
                    "location": after.location or "Online",
                }
                
                await self.update_website_event(website_event["uuid"], update_data)
        except Exception as e:
            log.error(f"CalendarSync: Error handling event update for {after.id}: {e}", exc_info=True)
    
    @commands.Cog.listener()
    async def on_scheduled_event_delete(self, event):
        """When a Discord event is deleted, update the website event status."""
        log.info(f"CalendarSync: Discord event deleted: {event.name} ({event.id}). Syncing status to website.")
        try:
            async with self.session.get( # Use central session
                f"{await self.config.cal_api_url()}?discord_event_id={event.id}",
                headers={"Authorization": f"Token {await self.config.cal_api_key()}", "X-API-Key": await self.config.cal_api_key()}
            ) as resp:
                if resp.status != 200:
                    log.warning(f"CalendarSync: Failed to find website event for deletion {event.id}: {resp.status}")
                    return
                
                data = await resp.json()
                if not data:
                    log.warning(f"CalendarSync: No website event found to mark as deleted for Discord event {event.id}.")
                    return
                
                website_event = data[0]
                
                await self.update_website_event(
                    website_event["uuid"], 
                    {"status": "canceled"} # Mark as canceled on the website
                )
        except Exception as e:
            log.error(f"CalendarSync: Error handling event deletion for {event.id}: {e}", exc_info=True)

    # --- Commands (these will be added as subcommands to the main cog's group) ---
    
    @commands.group(name="calendar", aliases=["cal"])
    async def calendar_group(self, ctx):
        """Calendar management commands for Zerolivesleft."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)
    
    @calendar_group.command(name="setapiurl")
    @commands.is_owner()
    async def set_api_url(self, ctx, url: str):
        """Set the API URL for the calendar integration."""
        if not url.startswith("http"):
            await ctx.send("URL must start with http:// or https://")
            return
            
        await self.config.cal_api_url.set(url) # Use central config
        await ctx.send(f"API URL set to: {url}")
    
    @calendar_group.command(name="setapikey")
    @commands.is_owner()
    async def set_api_key(self, ctx, api_key: str):
        """Set the API key for the calendar integration."""
        await self.config.cal_api_key.set(api_key) # Use central config
        await ctx.send("API key has been set.")
        
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass # Bot doesn't have permissions to delete
        except discord.HTTPException:
            pass # Other HTTP errors during deletion
    
    @calendar_group.command(name="showconfig")
    @commands.is_owner()
    async def show_config(self, ctx):
        """Show the current calendar configuration."""
        api_url = await self.config.cal_api_url() # Use central config
        api_key = await self.config.cal_api_key() # Use central config
        
        try:
            await ctx.author.send(
                f"**Calendar Configuration**\n"
                f"API URL: `{api_url}`\n"
                f"API Key: `{api_key if api_key else 'Not set'}`\n\n"
                f"Use `{ctx.prefix}zll calendar setapiurl` and `{ctx.prefix}zll calendar setapikey` to update these settings." # Updated command help
            )
            await ctx.send("Configuration sent to your DMs.")
        except discord.Forbidden:
            await ctx.send(
                f"**Calendar Configuration**\n"
                f"API URL: `{api_url}`\n"
                f"API Key: `{'✓ Set' if api_key else '✗ Not set'}`\n\n"
                f"Use `{ctx.prefix}zll calendar setapiurl` and `{ctx.prefix}zll calendar setapikey` to update these settings." # Updated command help
            )
    
    @calendar_group.command(name="sync")
    async def calendar_sync(self, ctx):
        """Manually trigger a sync between Discord and the website."""
        api_url = await self.config.cal_api_url()
        api_key = await self.config.cal_api_key()

        if not api_url or not api_key:
            await ctx.send(f"API URL or API key not set. Use `{ctx.prefix}zll calendar setapiurl` and `{ctx.prefix}zll calendar setapikey` first.")
            return
            
        await ctx.send("Syncing events between Discord and the website...")
        try:
            result = await self.sync_events_periodic() # Call the periodic sync method
            await ctx.send(f"Sync completed successfully!\n"
                         f"• Found {result.get('website_events', 0)} events on website\n"
                         f"• Pushed {result.get('discord_events_pushed', 0)} Discord events to website")
        except Exception as e:
            await ctx.send(f"Error during sync: {e}")
    
    @calendar_group.command(name="list")
    async def calendar_list(self, ctx):
        """List upcoming events."""
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