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
        # WebServer settings (previously from WebServer cog)
        self.config.register_global(
            webserver_host="0.0.0.0", 
            webserver_port=5000, 
            webserver_api_key=None # Key for Django to authenticate with RedBot
        )
        # GameCounter/RoleCounter settings (previously from GameCounter cog)
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
            cal_api_key=None,                                    # Key for RedBot to auth with Django for calendar
            cal_interval=15                                      # Added interval setting for calendar sync
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
                self.web_runner = None # Ensure state is reset on failure

    def cog_unload(self):
        """Cleanup when the cog is unloaded."""
        # Cancel all periodic tasks
        self.role_counting_logic.stop_tasks()
        self.calendar_sync_logic.stop_tasks()
        self.activity_tracking_logic.stop_tasks() # Handles its own cleanup

        # Shutdown web server
        if self.web_runner:
            asyncio.create_task(self.shutdown_webserver())
        
        # Close aiohttp session
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
        """Show all current ZeroLivesleft cog configurations."""
        await self.web_manager.show_config_command(ctx)
        await self.role_counting_logic.show_config_command(ctx)
        await self.activity_tracking_logic.show_config_command(ctx)
        await self.calendar_sync_logic.show_config(ctx)

    # --- WEBSERVER SUBCOMMANDS ---
    @zerolivesleft_group.group(name="webserver", aliases=["ws"])
    async def webserver_group(self, ctx: commands.Context):
        """Commands to manage the central web server."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)
    
    @webserver_group.command(name="sethost")
    async def webserver_set_host(self, ctx, host: str):
        await self.web_manager.set_host_command(ctx, host)

    @webserver_group.command(name="setport")
    async def webserver_set_port(self, ctx, port: int):
        await self.web_manager.set_port_command(ctx, port)

    @webserver_group.command(name="setapikey")
    async def webserver_set_apikey(self, ctx, *, api_key: str):
        await self.web_manager.set_apikey_command(ctx, api_key=api_key)

    @webserver_group.command(name="restart")
    async def webserver_restart(self, ctx):
        await self.web_manager.restart_server_command(ctx)
    
    @webserver_group.command(name="showconfig")
    async def webserver_show_config(self, ctx):
        await self.web_manager.show_config_command(ctx)

    # --- ROLECOUNTER SUBCOMMANDS ---
    @zerolivesleft_group.group(name="rolecounter", aliases=["rc"])
    async def rolecounter_group(self, ctx: commands.Context):
        """Manage the RoleCounter settings."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @rolecounter_group.command(name="setapiurl")
    async def rolecounter_set_api_url(self, ctx: commands.Context, url: str):
        await self.role_counting_logic.set_api_url(ctx, url)

    @rolecounter_group.command(name="setapikey")
    async def rolecounter_set_api_key(self, ctx: commands.Context, *, key: str):
        await self.role_counting_logic.set_api_key(ctx, key=key)

    @rolecounter_group.command(name="setinterval")
    async def rolecounter_set_interval(self, ctx: commands.Context, minutes: int):
        await self.role_counting_logic.set_interval(ctx, minutes)

    @rolecounter_group.command(name="setguild")
    async def rolecounter_set_guild(self, ctx: commands.Context, guild: discord.Guild):
        await self.role_counting_logic.set_guild(ctx, guild)

    @rolecounter_group.command(name="addmapping")
    async def rolecounter_add_mapping(self, ctx: commands.Context, role: discord.Role, *, game_name: str):
        await self.role_counting_logic.add_mapping(ctx, role, game_name=game_name)

    @rolecounter_group.command(name="removemapping")
    async def rolecounter_remove_mapping(self, ctx: commands.Context, role: discord.Role):
        await self.role_counting_logic.remove_mapping(ctx, role)

    @rolecounter_group.command(name="listmappings")
    async def rolecounter_list_mappings(self, ctx: commands.Context):
        await self.role_counting_logic.list_mappings(ctx)
    
    @rolecounter_group.command(name="showconfig")
    async def rolecounter_show_config(self, ctx: commands.Context):
        await self.role_counting_logic.show_config_command(ctx)

    # --- ACTIVITYTRACKER SUBCOMMANDS ---
    @zerolivesleft_group.group(name="activityset", aliases=["atset"])
    async def activityset_group(self, ctx: commands.Context):
        """Manage ActivityTracker settings."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @activityset_group.command()
    async def roles(self, ctx, recruit: discord.Role, member: discord.Role):
        await self.activity_tracking_logic.roles(ctx, recruit, member)

    @activityset_group.command()
    async def threshold(self, ctx, hours: float):
        await self.activity_tracking_logic.th

async def setup(bot: Red):
    """Set up the Zerolivesleft cog."""
    try:
        cog = Zerolivesleft(bot)
        await bot.add_cog(cog)
        log.info("Zerolivesleft cog loaded successfully")
    except Exception as e:
        log.error(f"Error loading Zerolivesleft cog: {e}", exc_info=True)
        raise