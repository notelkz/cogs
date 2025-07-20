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
from .application_roles import ApplicationRolesLogic

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
            cal_api_key=None,
            cal_interval=15
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
        self.application_roles_logic = ApplicationRolesLogic(self)

        # --- WEB SERVER SETUP ---
        self.web_app.router.add_get("/health", self.web_manager.health_check_handler)
        self.web_app.router.add_post("/api/applications/approved", self.application_roles_logic.handle_application_approved)
        self.web_app.router.add_post("/api/applications/update-status", self.application_roles_logic.handle_application_update)
        self.web_app.router.add_post("/api/ranked-members", self.web_manager.get_members_by_roles)
        self.web_app.router.add_post("/api/applications/submitted", self.application_roles_logic.handle_application_submitted) # NEW
        self.web_manager.register_all_routes()

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
        self.application_roles_logic.stop_tasks()

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

    @commands.hybrid_group(name="zll", aliases=["zerolivesleft"])
    @commands.is_owner()
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
        await self.application_roles_logic.show_config(ctx)

    @zerolivesleft_group.group(name="webserver", aliases=["ws"])
    async def webserver_group(self, ctx: commands.Context):
        """Commands to manage the central web server."""
        if ctx.invoked_subcommand is None: await ctx.send_help(ctx.command)
    
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

    @zerolivesleft_group.group(name="rolecounter", aliases=["rc"])
    async def rolecounter_group(self, ctx: commands.Context):
        """Manage the RoleCounter settings."""
        if ctx.invoked_subcommand is None: await ctx.send_help(ctx.command)

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

    @zerolivesleft_group.group(name="activityset", aliases=["atset"])
    async def activityset_group(self, ctx: commands.Context):
        """Manage ActivityTracker settings."""
        if ctx.invoked_subcommand is None: await ctx.send_help(ctx.command)

    @activityset_group.command()
    async def roles(self, ctx, recruit: discord.Role, member: discord.Role):
        await self.activity_tracking_logic.roles(ctx, recruit, member)

    @activityset_group.command()
    async def threshold(self, ctx, hours: float):
        await self.activity_tracking_logic.threshold(ctx, hours)

    @activityset_group.command(name="api")
    async def activity_set_api(self, ctx, url: str, key: str):
        await self.activity_tracking_logic.set_api(ctx, url, key)

    @activityset_group.command(name="promotionurl")
    async def activity_set_promotion_url(self, ctx, url: str):
        await self.activity_tracking_logic.set_promotion_url(ctx, url)

    @activityset_group.command(name="militaryrankurl")
    async def activity_set_military_rank_url(self, ctx, url: str):
        await self.activity_tracking_logic.set_military_rank_url(ctx, url)

    @activityset_group.command(name="promotionchannel")
    async def activity_set_promotion_channel(self, ctx, channel: discord.TextChannel):
        await self.activity_tracking_logic.set_promotion_channel(ctx, channel)
    
    @activityset_group.command(name="debug")
    async def activity_debug_info(self, ctx):
        await self.activity_tracking_logic.debug_info(ctx)

    @activityset_group.command(name="forcesync")
    async def activity_force_sync(self, ctx):
        await self.activity_tracking_logic.force_sync(ctx)

    @commands.hybrid_command(name="myvoicetime")
    async def myvoicetime(self, ctx):
        await self.activity_tracking_logic.myvoicetime(ctx)
    
    @commands.hybrid_command(name="status")
    async def status(self, ctx, member: discord.Member = None):
        await self.activity_tracking_logic.status(ctx, member)

    @zerolivesleft_group.group(name="militaryranks")
    async def militaryranks_group(self, ctx):
        """Manage military ranks (add, remove, list, clear)."""
        if ctx.invoked_subcommand is None: await ctx.send_help(ctx.command)

    @militaryranks_group.command(name="add")
    async def militaryranks_add_rank(self, ctx, role: discord.Role, required_hours: float):
        await self.activity_tracking_logic.add_rank(ctx, role, required_hours)

    @militaryranks_group.command(name="remove")
    async def militaryranks_remove_rank(self, ctx, role_or_name: str):
        await self.activity_tracking_logic.remove_rank(ctx, role_or_name)

    @militaryranks_group.command(name="clear")
    async def militaryranks_clear_ranks(self, ctx):
        await self.activity_tracking_logic.clear_ranks(ctx)

    @militaryranks_group.command(name="list")
    async def militaryranks_list_ranks(self, ctx):
        await self.activity_tracking_logic.list_ranks(ctx)

    @zerolivesleft_group.group(name="calendar", aliases=["cal"])
    async def calendar_group(self, ctx):
        """Calendar management commands."""
        if ctx.invoked_subcommand is None: await ctx.send_help(ctx.command)

    @calendar_group.command(name="setapiurl")
    async def calendar_set_api_url(self, ctx, url: str):
        await self.calendar_sync_logic.set_api_url(ctx, url)

    @calendar_group.command(name="setapikey")
    async def calendar_set_api_key(self, ctx, api_key: str):
        await self.calendar_sync_logic.set_api_key(ctx, api_key)

    @calendar_group.command(name="showconfig")
    async def calendar_show_config(self, ctx):
        await self.calendar_sync_logic.show_config(ctx)
    
    @calendar_group.command(name="sync")
    async def calendar_sync_command(self, ctx):
        await self.calendar_sync_logic.calendar_sync(ctx)

    @calendar_group.command(name="list")
    async def calendar_list_command(self, ctx):
        await self.calendar_sync_logic.calendar_list(ctx)

    @zerolivesleft_group.group(name="approles", aliases=["ar"])
    async def approles_group(self, ctx):
        """Manage application role assignment settings."""
        if ctx.invoked_subcommand is None: await ctx.send_help(ctx.command)

    @approles_group.command(name="setapiurl")
    async def approles_set_api_url(self, ctx, url: str):
        """Set the API URL for checking application statuses."""
        await self.application_roles_logic.set_api_url(ctx, url)

    @approles_group.command(name="setapikey")
    async def approles_set_api_key(self, ctx, *, key: str):
        """Set the API key for authenticating with the website."""
        await self.application_roles_logic.set_api_key(ctx, key=key)
        
    @approles_group.command(name="setpendingrole")
    async def approles_set_pending_role(self, ctx, role: discord.Role):
        """Set the role for members with a PENDING application."""
        await self.application_roles_logic.set_pending_role(ctx, role)

    @approles_group.command(name="setmemberrole")
    async def approles_set_member_role(self, ctx, role: discord.Role):
        """Set the main role for APPROVED members."""
        await self.application_roles_logic.set_member_role(ctx, role)

    @approles_group.command(name="setunverifiedrole")
    async def approles_set_unverified_role(self, ctx, role: discord.Role):
        """Set the role for new members who have NOT applied."""
        await self.application_roles_logic.set_unverified_role(ctx, role)

    @approles_group.command(name="setwelcomechannel")
    async def approles_set_welcome_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel for welcome messages."""
        await self.application_roles_logic.set_welcome_channel(ctx, channel)

    @approles_group.command(name="setwelcomemessage")
    async def approles_set_welcome_message(self, ctx, *, message: str):
        """Set the welcome message. Use {mention} to ping the user."""
        await self.application_roles_logic.set_welcome_message(ctx, message=message)

    @approles_group.command(name="addregion")
    async def approles_add_region(self, ctx, region: str, role: discord.Role):
        """Add a mapping from a region code to a Discord role."""
        await self.application_roles_logic.add_region_role(ctx, region, role)

    @approles_group.command(name="removeregion")
    async def approles_remove_region(self, ctx, region: str):
        """Remove a region role mapping."""
        await self.application_roles_logic.remove_region_role(ctx, region)

    @approles_group.command(name="listregions")
    async def approles_list_regions(self, ctx):
        """List all configured region-to-role mappings."""
        await self.application_roles_logic.list_region_roles(ctx)

    @approles_group.command(name="showconfig")
    async def approles_show_config(self, ctx):
        """Show the current configuration for the application module."""
        await self.application_roles_logic.show_config(ctx)

    @approles_group.command(name="setdefaultguild")
    async def approles_set_default_guild(self, ctx, guild: discord.Guild):
        """Set the default guild for all application actions."""
        await self.application_roles_logic.set_default_guild(ctx, guild)
        
async def setup(bot: Red):
    cog = Zerolivesleft(bot)
    await bot.add_cog(cog)