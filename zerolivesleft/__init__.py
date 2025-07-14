# zerolivesleft/__init__.py

import asyncio
import logging
from aiohttp import web
import aiohttp # Import aiohttp here for ClientSession
import discord
import os

from redbot.core import commands, Config
from redbot.core.bot import Red

# Import individual logic modules
from .webapi import WebApiManager
from .game_counting import GameCountingLogic
from .activity_tracking import ActivityTrackingLogic
from .calendar_sync import CalendarSyncLogic

log = logging.getLogger("red.Elkz.zerolivesleft") # Main cog logger

class Zerolivesleft(commands.Cog):
    """
    A consolidated cog for Zero Lives Left website integration,
    including web APIs, game counting, activity tracking, and calendar sync.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        
        # --- CENTRALIZED CONFIG ---
        self.config = Config.get_conf(self, identifier=6789012345, force_registration=True)
        # WebServer settings (previously from WebServer cog)
        self.config.register_global(
            webserver_host="0.0.0.0", 
            webserver_port=5000, 
            webserver_api_key=None # Key for Django to authenticate with RedBot
        )
        # GameCounter settings (previously from GameCounter cog)
        self.config.register_global(
            gc_api_base_url=None, # Django's public API URL for game counts
            gc_api_key=None,      # Key for RedBot to authenticate with Django for game counts
            gc_interval=1,        # 1 minute for faster debugging
            gc_counting_guild_id=None,
            gc_game_role_mappings={}
        )
        # ActivityTracker settings (previously from ActivityTracker cog)
        self.config.register_guild(
            at_user_activity={},  # user_id: total_minutes
            at_recruit_role_id=None,
            at_member_role_id=None,
            at_promotion_threshold_hours=24.0,
            at_military_ranks=[],  # list of dicts: {name, discord_role_id, required_hours}
            at_api_url=None,       # Django's public API URL for activity/promotions
            at_api_key=None,       # Key for RedBot to auth with Django for activity/promotions
            at_promotion_update_url=None,
            at_military_rank_update_url=None,
            at_promotion_channel_id=None,
        )
        # Calendar Sync settings (previously from ZeroCalendar cog)
        self.config.register_global(
            cal_api_url="https://zerolivesleft.net/api/events/", # Django's public API URL for calendar
            cal_api_key=None                                    # Key for RedBot to auth with Django for calendar
        )

        # --- SHARED RESOURCES ---
        self.session = aiohttp.ClientSession() # Single session for all outgoing HTTP requests
        self.web_app = web.Application()       # Central aiohttp web application for all incoming API routes
        self.web_runner = None
        self.web_site = None
        
        # --- INSTANTIATE LOGIC MANAGERS ---
        # These managers hold the methods for their specific functionality
        # They are passed a reference to this main cog instance ('self')
        self.web_manager = WebApiManager(self)
        self.game_counting_logic = GameCountingLogic(self)
        self.activity_tracking_logic = ActivityTrackingLogic(self)
        self.calendar_sync_logic = CalendarSyncLogic(self)

        # --- WEB SERVER SETUP (from WebServer cog) ---
        # Add core routes
        self.web_app.router.add_get("/health", self.web_manager.health_check_handler)
        
        # Add routes from other parts of the cog (handled by WebApiManager)
        self.web_manager.register_all_routes() # This method will add all routes from all modules

        # Start the web server initialization task
        asyncio.create_task(self.initialize_webserver())

        # Start periodic tasks
        self.game_counting_logic.start_tasks()
        self.calendar_sync_logic.start_tasks()
        self.activity_tracking_logic.start_tasks() # This handles its own internal scheduling

    async def initialize_webserver(self):
        """Start the web server."""
        await self.bot.wait_until_ready()
        if not self.web_runner:
            host = await self.config.webserver_host()
            port = await self.config.webserver_port()
            try:
                # Routes are already added to self.web_app.router by WebApiManager.register_all_routes()
                self.web_runner = web.AppRunner(self.web_app)
                await self.web_runner.setup()
                
                self.web_site = web.TCPSite(self.web_runner, host, port)
                await self.web_site.start()
                log.info(f"Central web server started on http://{host}:{port}")
            except Exception as e:
                log.error(f"Failed to start central web server: {e}")
                self.web_runner = None # Ensure state is reset on failure

    def cog_unload(self):
        """Cleanup when the cog is unloaded."""
        # Cancel