# zerolivesleft/__init__.py

import asyncio
import logging
from aiohttp import web
import aiohttp 
import discord
import os

from redbot.core import commands, Config
from redbot.core.bot import Red

# Import individual logic modules
from .webapi import WebApiManager
from .rolecount import RoleCountingLogic
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
        # WebServer settings
        self.config.register_global(
            webserver_host="0.0.0.0", 
            webserver_port=5000, 
            webserver_api_key=None
        )
        # GameCounter/RoleCounter settings
        self.config.register_global(
            gc_api_base_url=None, 
            gc_api_key=None,      
            gc_interval=1,        
            gc_counting_guild_id=None,
            gc_game_role_mappings={}
        )
        # ActivityTracker settings
        self.config.register_guild(
            at_user_activity={},  
            at_recruit_role_id=None,
            at_member_role_id=None,
            at_promotion_threshold_hours=24.0,
            at_military_ranks=[],  
            at_api_url=None,       
            at_api_key=None,       
            at_promotion_update_url=None,
            at_military_rank_update_url=None,
            at_promotion_channel_id=None,
        )
        # Calendar Sync settings
        self.config.register_global(
            cal_api_url="https://zerolivesleft.net/api/events/", 
            cal_api_key=None                                    
        )

        # --- SHARED RESOURCES ---
        self.session = aiohttp.ClientSession()
        self.web_app = web.Application()
        self.web_runner = None
        self.web_site = None
        
        # --- INSTANTIATE LOGIC MANAGERS ---
        self.web_manager = WebApiManager(self)
        self.role_counting_logic = RoleCountingLogic(self)
        self.activity_tracking_logic = ActivityTrackingLogic(self)
        self.calendar_sync_logic = CalendarSyncLogic(self)

        # --- WEB SERVER SETUP ---
        self.web_app.router.add_get("/health", self.web_manager.health_check_handler)
        self.web_manager.register_all_routes() # This method will add all routes from all modules to self.web_app

        # Start the web server initialization task
        asyncio.create_task(self.initialize_webserver())

        # Start periodic tasks
        self.role_counting_logic.start_tasks()
        self.calendar_sync_logic.start_tasks()
        self.activity_tracking_logic.start_tasks()

    async def initialize_webserver(self):
        """Start the web server."""
        await self.bot.wait_until_ready()
        if not self.web_runner:
            host = await self.config.webserver_host()
            port = await self.config.webserver_port()
            try:
                self.web_runner = web.AppRunner(self.web_app)
                await self.web_runner.setup()
                
                self.web_site = web.TCPSite(self.web_runner, host, port)
                await self.web_site.start()
                log.info(f"Central web server started on http://{host}:{port}")
            except Exception as e:
                log.error(f"Failed to start central web server: {e}")
                self.web_runner = None

    def cog_unload(self):
        """Cleanup when the cog is unloaded."""
        self.role_counting_logic.stop_tasks()
        self.calendar_sync_logic.stop_tasks()
        self.activity_tracking_logic.stop_tasks()

        if self.web_runner:
            asyncio.create_task(self.shutdown_webserver())
        
        asyncio.create_task(self.session.close())
        log.info("Zerolivesleft cog unloaded.")

    async def shutdown_webserver(self):
        """Shutdown the central web server."""
        log.info("Shutting down central web server...")
        try:
            if self.web_app:
                await self.web_app.shutdown()
            if self.web_runner:
                await self.web_runner.cleanup()
            log.info("Central web server shut down successfully.")
        except Exception as e:
            log.error(f"Error during web server shutdown: {e}")
        finally:
            self.web_runner = None
            self.web_site = None

    # --- Commands (Centralized under 'zll') ---
    
    @commands.hybrid_group(name="zll", aliases=["zerolivesleft"])
    @commands.is_owner() # Only bot owner can use this top-level group
    async def zerolivesleft_group(self, ctx: commands.Context):
        """Central commands for Zero Lives Left website integration."""
        pass

    @zerolivesleft_group.command(name="showconfig")
    async def show_all_config(self, ctx: commands.Context):
        """Show all current ZeroLivesLeft cog configurations."""
        await self.web_manager.show_config_command(ctx)
        await self.role_counting_logic.show_config_command(ctx)
        await self.activity_tracking_logic.show_config_command(ctx)
        await self.calendar_sync_logic.show_config_command(ctx)

    # --- REGISTER SUBCOMMAND GROUPS ---
    # We now add the command groups from the logic managers directly to the main zll group.
    # The logic managers must define their command groups as methods that return the group object.
    
    def add_all_subcommand_groups(self):
        """Adds command groups from logic modules to the main zll group."""
        log.info("Adding subcommand groups to !zll group.")
        self.zerolivesleft_group.add_command(self.web_manager.get_commands_group())
        self.zerolivesleft_group.add_command(self.role_counting_logic.get_commands_group())
        self.zerolivesleft_group.add_command(self.activity_tracking_logic.get_commands_group())
        self.zerolivesleft_group.add_command(self.calendar_sync_logic.get_commands_group())
        log.info("All subcommand groups added.")

async def setup(bot: Red):
    """Set up the Zerolivesleft cog."""
    cog = Zerolivesleft(bot)
    await bot.add_cog(cog)
    # After cog is added, its commands are registered.
    # Now, explicitly add subcommands (groups) from manager classes.
    cog.add_all_subcommand_groups()