# Updated __init__.py with new commands added
# Here's your complete file with the new commands integrated:

import asyncio
import logging
from aiohttp import web
import aiohttp 
import discord
import os
from datetime import datetime

from redbot.core import commands, Config
from redbot.core.bot import Red

# Import individual logic modules
from .webapi import WebApiManager
from .rolecount import RoleCountingLogic
from .activity_tracking import ActivityTrackingLogic
from .calendar_sync import CalendarSyncLogic
from .application_roles import ApplicationRolesLogic
from .role_menus import RoleMenuLogic
from . import role_menus

log = logging.getLogger("red.Elkz.zerolivesleft")

class Zerolivesleft(commands.Cog):
    """
    A consolidated cog for Zero Lives Left website integration.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=6789012345, force_registration=True)
        
        # ✅ --- CORRECTED: All configs registered in their respective logic classes ---

        self.session = aiohttp.ClientSession()
        self.web_app = web.Application()
        self.web_runner = None
        self.web_site = None
        
        self.web_manager = WebApiManager(self)
        self.role_counting_logic = RoleCountingLogic(self)
        self.activity_tracking_logic = ActivityTrackingLogic(self)
        self.calendar_sync_logic = CalendarSyncLogic(self)
        self.application_roles_logic = ApplicationRolesLogic(self)
        self.role_menu_logic = RoleMenuLogic(self)

        self.bot.add_view(role_menus.AutoRoleView())
        self.view_init_task = self.bot.loop.create_task(self.initialize_persistent_views())
        self.web_manager.register_all_routes()
        asyncio.create_task(self.initialize_webserver())
        self.role_counting_logic.start_tasks()
        self.calendar_sync_logic.start_tasks()
        self.activity_tracking_logic.start_tasks()

    async def initialize_persistent_views(self):
        await self.bot.wait_until_ready()
        log.info("Initializing persistent role menu views...")
        all_guild_data = await self.config.all_guilds()
        for guild_id, data in all_guild_data.items():
            guild = self.bot.get_guild(guild_id)
            if not guild or "role_menus" not in data: continue
            for menu_name, menu_data in data["role_menus"].items():
                if menu_data.get("message_id") and menu_data.get("channel_id"):
                    try:
                        components = await self.role_menu_logic._build_menu_components(guild, menu_name)
                        if components:
                            _, view = components
                            self.bot.add_view(view, message_id=menu_data["message_id"])
                            log.info(f"Re-registered view for menu '{menu_name}' in guild {guild_id}.")
                    except Exception as e:
                        log.error(f"Failed to re-register view for menu '{menu_name}' in guild {guild_id}: {e}")

    async def initialize_webserver(self):
        await self.bot.wait_until_ready()
        if not self.web_runner:
            host, port = await self.config.webserver_host(), await self.config.webserver_port()
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
        self.role_counting_logic.stop_tasks()
        self.calendar_sync_logic.stop_tasks()
        self.activity_tracking_logic.stop_tasks()
        self.application_roles_logic.stop_tasks()
        if hasattr(self, 'view_init_task'): self.view_init_task.cancel()
        if self.web_runner: asyncio.create_task(self.shutdown_webserver())
        asyncio.create_task(self.session.close())
        log.info("Zerolivesleft cog unloaded.")

    async def shutdown_webserver(self):
        log.info("Shutting down central web server...")
        try:
            if self.web_app: await self.web_app.shutdown()
            if self.web_runner: await self.web_runner.cleanup()
            log.info("Central web server shut down successfully.")
        except Exception as e:
            log.error(f"Error during web server shutdown: {e}")
        finally:
            self.web_runner, self.web_site = None, None

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot: return
        await self.activity_tracking_logic.handle_voice_state_update(member, before, after)

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
    async def webserver_set_host(self, ctx, host: str): await self.web_manager.set_host_command(ctx, host)
    @webserver_group.command(name="setport")
    async def webserver_set_port(self, ctx, port: int): await self.web_manager.set_port_command(ctx, port)
    @webserver_group.command(name="setapikey")
    async def webserver_set_apikey(self, ctx, *, api_key: str): await self.web_manager.set_apikey_command(ctx, api_key=api_key)
    @webserver_group.command(name="restart")
    async def webserver_restart(self, ctx): await self.web_manager.restart_server_command(ctx)
    @webserver_group.command(name="showconfig")
    async def webserver_show_config(self, ctx): await self.web_manager.show_config_command(ctx)

    @zerolivesleft_group.group(name="rolecounter", aliases=["rc"])
    async def rolecounter_group(self, ctx: commands.Context):
        """Manage the RoleCounter settings."""
        if ctx.invoked_subcommand is None: await ctx.send_help(ctx.command)
    @rolecounter_group.command(name="setapiurl")
    async def rolecounter_set_api_url(self, ctx, url: str): await self.role_counting_logic.set_api_url(ctx, url)
    @rolecounter_group.command(name="setapikey")
    async def rolecounter_set_api_key(self, ctx, *, key: str): await self.role_counting_logic.set_api_key(ctx, key=key)
    @rolecounter_group.command(name="setinterval")
    async def rolecounter_set_interval(self, ctx, minutes: int): await self.role_counting_logic.set_interval(ctx, minutes)
    @rolecounter_group.command(name="setguild")
    async def rolecounter_set_guild(self, ctx, guild: discord.Guild): await self.role_counting_logic.set_guild(ctx, guild)
    @rolecounter_group.command(name="addmapping")
    async def rolecounter_add_mapping(self, ctx, role: discord.Role, *, game_name: str): await self.role_counting_logic.add_mapping(ctx, role, game_name=game_name)
    @rolecounter_group.command(name="removemapping")
    async def rolecounter_remove_mapping(self, ctx, role: discord.Role): await self.role_counting_logic.remove_mapping(ctx, role)
    @rolecounter_group.command(name="listmappings")
    async def rolecounter_list_mappings(self, ctx): await self.role_counting_logic.list_mappings(ctx)
    @rolecounter_group.command(name="showconfig")
    async def rolecounter_show_config(self, ctx): await self.role_counting_logic.show_config_command(ctx)

    @zerolivesleft_group.group(name="activityset", aliases=["atset"])
    async def activityset_group(self, ctx: commands.Context):
        """Manage ActivityTracker settings."""
        if ctx.invoked_subcommand is None: await ctx.send_help(ctx.command)
    @activityset_group.command()
    async def roles(self, ctx, recruit: discord.Role, member: discord.Role): await self.activity_tracking_logic.roles(ctx, recruit, member)
    @activityset_group.command()
    async def threshold(self, ctx, hours: float): await self.activity_tracking_logic.threshold(ctx, hours)
    @activityset_group.command(name="api")
    async def activity_set_api(self, ctx, url: str, key: str): await self.activity_tracking_logic.set_api(ctx, url, key)
    @activityset_group.command(name="promotionurl")
    async def activity_set_promotion_url(self, ctx, url: str): await self.activity_tracking_logic.set_promotion_url(ctx, url)
    @activityset_group.command(name="militaryrankurl")
    async def activity_set_military_rank_url(self, ctx, url: str): await self.activity_tracking_logic.set_military_rank_url(ctx, url)
    @activityset_group.command(name="promotionchannel")
    async def activity_set_promotion_channel(self, ctx, channel: discord.TextChannel): await self.activity_tracking_logic.set_promotion_channel(ctx, channel)
    @activityset_group.command(name="debug")
    async def activity_debug_info(self, ctx): await self.activity_tracking_logic.debug_info(ctx)
    @activityset_group.command(name="forcesync")
    async def activity_force_sync(self, ctx): await self.activity_tracking_logic.force_sync(ctx)

    @commands.hybrid_command(name="myvoicetime")
    async def myvoicetime(self, ctx): await self.activity_tracking_logic.myvoicetime(ctx)
    @commands.hybrid_command(name="status")
    async def status(self, ctx, member: discord.Member = None): await self.activity_tracking_logic.status(ctx, member)
    
    # NEW: Application status check command
    @commands.hybrid_command(name="appstatus", aliases=["application", "checkapp"])
    async def check_application_status(self, ctx, member: discord.Member = None):
        """Check your application status or another member's status (if you have permissions)"""
        await self.application_roles_logic.check_application_status(ctx, member)

    @zerolivesleft_group.group(name="militaryranks")
    async def militaryranks_group(self, ctx):
        """Manage military ranks (add, remove, list, clear)."""
        if ctx.invoked_subcommand is None: await ctx.send_help(ctx.command)
    @militaryranks_group.command(name="add")
    async def militaryranks_add_rank(self, ctx, role: discord.Role, required_hours: float): await self.activity_tracking_logic.add_rank(ctx, role, required_hours)
    @militaryranks_group.command(name="remove")
    async def militaryranks_remove_rank(self, ctx, role_or_name: str): await self.activity_tracking_logic.remove_rank(ctx, role_or_name)
    @militaryranks_group.command(name="clear")
    async def militaryranks_clear_ranks(self, ctx): await self.activity_tracking_logic.clear_ranks(ctx)
    @militaryranks_group.command(name="list")
    async def militaryranks_list_ranks(self, ctx): await self.activity_tracking_logic.list_ranks(ctx)

    @zerolivesleft_group.group(name="calendar", aliases=["cal"])
    async def calendar_group(self, ctx):
        """Calendar management commands."""
        if ctx.invoked_subcommand is None: await ctx.send_help(ctx.command)
    @calendar_group.command(name="setapiurl")
    async def calendar_set_api_url(self, ctx, url: str): await self.calendar_sync_logic.set_api_url(ctx, url)
    @calendar_group.command(name="setapikey")
    async def calendar_set_api_key(self, ctx, api_key: str): await self.calendar_sync_logic.set_api_key(ctx, api_key)
    @calendar_group.command(name="showconfig")
    async def calendar_show_config(self, ctx): await self.calendar_sync_logic.show_config(ctx)
    @calendar_group.command(name="sync")
    async def calendar_sync_command(self, ctx): await self.calendar_sync_logic.calendar_sync(ctx)
    @calendar_group.command(name="list")
    async def calendar_list_command(self, ctx): await self.calendar_sync_logic.calendar_list(ctx)

    @zerolivesleft_group.group(name="approles", aliases=["ar"])
    async def approles_group(self, ctx):
        """Manage application role assignment settings."""
        if ctx.invoked_subcommand is None: await ctx.send_help(ctx.command)
    @approles_group.command(name="setapiurl")
    async def approles_set_api_url(self, ctx, url: str): await self.application_roles_logic.set_api_url(ctx, url)
    @approles_group.command(name="setapikey")
    async def approles_set_api_key(self, ctx, *, key: str): await self.application_roles_logic.set_api_key(ctx, key=key)
    @approles_group.command(name="setpendingrole")
    async def approles_set_pending_role(self, ctx, role: discord.Role): await self.application_roles_logic.set_pending_role(ctx, role)
    @approles_group.command(name="setmemberrole")
    async def approles_set_member_role(self, ctx, role: discord.Role): await self.application_roles_logic.set_member_role(ctx, role)
    @approles_group.command(name="setunverifiedrole")
    async def approles_set_unverified_role(self, ctx, role: discord.Role): await self.application_roles_logic.set_unverified_role(ctx, role)
    @approles_group.command(name="setwelcomechannel")
    async def approles_set_welcome_channel(self, ctx, channel: discord.TextChannel): await self.application_roles_logic.set_welcome_channel(ctx, channel)
    
    @approles_group.command(name="setunverifiedchannel")
    async def approles_set_unverified_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the channel where new members with the Unverified role receive their welcome message."""
        await self.application_roles_logic.set_unverified_channel(ctx, channel)

    @approles_group.command(name="setpendingchannel")
    async def approles_set_pending_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the channel where members with a Pending application role receive their updates."""
        await self.application_roles_logic.set_pending_channel(ctx, channel)
        
    # NEW: Notifications channel command
    @approles_group.command(name="setnotificationschannel")
    async def approles_set_notifications_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the channel where application status notifications are sent."""
        await self.application_roles_logic.set_notifications_channel(ctx, channel)

    @approles_group.command(name="addregion")
    async def approles_add_region(self, ctx, region: str, role: discord.Role): await self.application_roles_logic.add_region_role(ctx, region, role)
    @approles_group.command(name="removeregion")
    async def approles_remove_region(self, ctx, region: str): await self.application_roles_logic.remove_region_role(ctx, region)
    @approles_group.command(name="listregions")
    async def approles_list_regions(self, ctx): await self.application_roles_logic.list_region_roles(ctx)
    @approles_group.command(name="showconfig")
    async def approles_show_config(self, ctx): await self.application_roles_logic.show_config(ctx)
    @approles_group.command(name="setdefaultguild")
    async def approles_set_default_guild(self, ctx, guild: discord.Guild): await self.application_roles_logic.set_default_guild(ctx, guild)
        
    @zerolivesleft_group.group(name="message", aliases=["msg"])
    async def message_group(self, ctx: commands.Context):
        """Manage the automated messages sent to new members."""
        if ctx.invoked_subcommand is None: await ctx.send_help(ctx.command)
    @message_group.command(name="public")
    async def toggle_public(self, ctx, enabled: bool):
        """[On|Off] :: Enable or disable sending public welcome embeds."""
        await self.application_roles_logic.config.ar_send_public_welcome.set(enabled)
        await ctx.send(f"Public welcome embeds are now **{'ENABLED' if enabled else 'DISABLED'}**.")
    @message_group.command(name="private")
    async def toggle_private(self, ctx, enabled: bool):
        """[On|Off] :: Enable or disable sending private welcome DMs."""
        await self.application_roles_logic.config.ar_send_private_welcome.set(enabled)
        await ctx.send(f"Private welcome DMs are now **{'ENABLED' if enabled else 'DISABLED'}**.")
    @message_group.command(name="setunverified")
    async def set_unverified_message(self, ctx, *, message: str):
        """Sets the welcome DM sent to new, unverified members."""
        await self.application_roles_logic.config.ar_unverified_message.set(message)
        await ctx.send(f"The 'Unverified' welcome message has been updated.")
    @message_group.command(name="setpending")
    async def set_pending_message(self, ctx, *, message: str):
        """Sets the confirmation DM sent after an application is submitted."""
        await self.application_roles_logic.config.ar_pending_message.set(message)
        await ctx.send(f"The 'Pending' confirmation message has been updated.")
        
    @zerolivesleft_group.group(name="rolemenu")
    @commands.admin_or_permissions(manage_guild=True)
    async def rolemenu_group(self, ctx: commands.Context):
        """Manage and post dynamic role-selector menus."""
        pass
    @rolemenu_group.command(name="create")
    async def menu_create(self, ctx, name: str): await self.role_menu_logic.create_menu(ctx, name)
    @rolemenu_group.command(name="delete")
    async def menu_delete(self, ctx, name: str): await self.role_menu_logic.delete_menu(ctx, name)
    @rolemenu_group.command(name="list")
    async def menu_list(self, ctx): await self.role_menu_logic.list_menus(ctx)
    @rolemenu_group.command(name="edit")
    async def menu_edit(self, ctx, name: str, setting: str, *, value: str): await self.role_menu_logic.edit_menu(ctx, name, setting, value)
    @rolemenu_group.command(name="image")
    async def menu_image(self, ctx, name: str, image_url: str): await self.role_menu_logic.edit_menu(ctx, name, "image_url", image_url)
    @rolemenu_group.command(name="addrole")
    async def rolemenu_add_role(self, ctx, menu_name: str, role: discord.Role, *, label: str = None): await self.role_menu_logic.add_role_to_menu(ctx, menu_name, role, label)
    @rolemenu_group.command(name="removerole")
    async def rolemenu_remove_role(self, ctx, menu_name: str, role: discord.Role): await self.role_menu_logic.remove_role_from_menu(ctx, menu_name, role)
    @rolemenu_group.command(name="post")
    async def rolemenu_post(self, ctx, menu_name: str, channel: discord.TextChannel = None): await self.role_menu_logic.post_menu(ctx, menu_name, channel)
    @rolemenu_group.command(name="update")
    async def rolemenu_update(self, ctx, menu_name: str): await self.role_menu_logic.update_menu_message(ctx, menu_name)
    @rolemenu_group.command(name="autoroles")
    async def rolemenu_autoroles(self, ctx, channel: discord.TextChannel = None): await self.role_menu_logic.send_autoroles_menu(ctx, channel)
        
async def setup(bot: Red):
    cog = Zerolivesleft(bot)
    await bot.add_cog(cog)