# zerolivesleft/__init__.py
import logging
import aiohttp
from redbot.core import commands, Config
from redbot.core.bot import Red

# Import component modules
from .rolecount import RoleCountingLogic
from .webapi import WebApiManager
from .activity_tracking import ActivityTrackingLogic
from .calendar_sync import CalendarSyncLogic

log = logging.getLogger("red.Elkz.zerolivesleft")

class Zerolivesleft(commands.Cog):
    """Zero Lives Left integration cog."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=6789012345, force_registration=True)
        
        # Register default configuration values
        default_global = {
            # RoleCounter config
            "gc_api_base_url": None,
            "gc_api_key": None,
            "gc_interval": 15,
            "gc_counting_guild_id": None,
            "gc_game_role_mappings": {},
            
            # WebServer config
            "webserver_host": "0.0.0.0",
            "webserver_port": 8000,
            "webserver_api_key": None,
            
            # CalendarSync config
            "cal_api_url": None,
            "cal_api_key": None,
            "cal_interval": 15
        }
        
        default_guild = {
            # ActivityTracker config
            "at_api_url": None,
            "at_api_key": None,
            "at_recruit_role_id": None,
            "at_member_role_id": None,
            "at_promotion_threshold_hours": 10,
            "at_promotion_channel_id": None,
            "at_promotion_update_url": None,
            "at_military_rank_update_url": None,
            "at_military_ranks": [],
            "at_user_activity": {}
        }
        
        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)
        
        # Initialize aiohttp session
        self.session = aiohttp.ClientSession()
        
        # Initialize web server components
        self.web_app = None
        self.web_runner = None
        self.web_site = None
        
        # Initialize component modules
        self.role_counting_logic = RoleCountingLogic(self)
        self.web_api_manager = WebApiManager(self)
        self.activity_tracking_logic = ActivityTrackingLogic(self)
        self.calendar_sync_logic = CalendarSyncLogic(self)
        
        log.info("Zerolivesleft cog initialized successfully!")

    def cog_unload(self):
        # Stop all tasks
        self.role_counting_logic.stop_tasks()
        self.activity_tracking_logic.stop_tasks()
        self.calendar_sync_logic.stop_tasks()
        
        # Close aiohttp session
        if self.session:
            self.bot.loop.create_task(self.session.close())
        
        # Shutdown web server
        if self.web_site:
            self.bot.loop.create_task(self.shutdown_webserver())
            
        log.info("Zerolivesleft cog unloaded.")

    async def initialize_webserver(self):
        # Implementation for starting the web server
        pass

    async def shutdown_webserver(self):
        # Implementation for shutting down the web server
        pass
