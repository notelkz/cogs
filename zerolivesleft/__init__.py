# zerolivesleft-alpha/__init__.py
# Clean version with organized XP-based activity tracking commands and Application Ping system

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
from .application_ping import ApplicationPingLogic  # NEW
from .role_menus import RoleMenuLogic
from .gamertags import GamertagsLogic # NEW
from .lfg_logic import LFGLogic # NEW
from .report_logic import ReportLogic, ReportModerationView # NEW
from . import role_menus

log = logging.getLogger("red.Elkz.zerolivesleft")

class Zerolivesleft(commands.Cog):
    """
    A consolidated cog for Zero Lives Left website integration with XP-based activity tracking.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=6789012345, force_registration=True)
        
        # Register default config for webserver
        default_global = {
            "webserver_host": "0.0.0.0",
            "webserver_port": 8080,
            "webserver_api_key": None,
            # Role counting global config (from rolecount.py)
            "gc_api_base_url": None,
            "gc_api_key": None,
            "gc_interval": 15,
            "gc_counting_guild_id": None,
            "gc_game_role_mappings": {}
        }
        self.config.register_global(**default_global)

        # Register default guild config including new Django webhook settings
        default_guild = {
            "django_webhook_url": None,
            "django_webhook_secret": None,
            # Role Counter settings
            "rc_role_mappings": {},
            "rc_api_url": None,
            "rc_api_key": None,
            "rc_interval_minutes": 5,
            "rc_target_guild_id": None,
            # Activity Tracker settings (add commonly used ones)
            "at_api_key": None,
            "at_api_url": None,
            "at_recruit_role_id": None,
            "at_member_role_id": None,
            "at_military_ranks": [],
            "at_user_activity": {},
            # Role Menus settings - THIS WAS MISSING!
            "role_menus": {},
            # LFG System settings
            "lfg_forum_id": None,
            "lfg_required_role": "Recruit", 
            "lfg_cleanup_hours": 24,
            "lfg_max_players": 10,
            # Report System settings
            "report_channel": None,
            "report_cooldown": 300,  # 5 minutes
            "report_allowed_roles": [],  # Empty means everyone can report
            "report_log_enabled": True,
        }
        self.config.register_guild(**default_guild)

        # Register default user config for gamertags
        default_user = {
            "gamertags": {}
        }
        self.config.register_user(**default_user)

        # Register default channel config for LFG data
        default_channel = {
            "lfg_data": {}
        }
        self.config.register_channel(**default_channel)

        self.session = aiohttp.ClientSession()
        self.web_app = web.Application()
        self.web_runner = None
        self.web_site = None
        
        self.web_manager = WebApiManager(self)
        self.role_counting_logic = RoleCountingLogic(self)
        self.activity_tracking_logic = ActivityTrackingLogic(self)
        self.calendar_sync_logic = CalendarSyncLogic(self)
        self.application_roles_logic = ApplicationRolesLogic(self)
        self.application_ping_logic = ApplicationPingLogic(self)  # NEW
        self.role_menu_logic = RoleMenuLogic(self)
        self.gamertags_logic = GamertagsLogic(self) # NEW
        self.lfg_logic = LFGLogic(self) # NEW
        self.report_logic = ReportLogic(self) # NEW

        self.bot.add_view(role_menus.AutoRoleView())
        self.view_init_task = self.bot.loop.create_task(self.initialize_persistent_views())
        self.web_manager.register_all_routes()
        self.application_ping_logic.register_routes(self.web_app)  # NEW - Register ping routes
        asyncio.create_task(self.initialize_webserver())
        self.role_counting_logic.start_tasks()
        self.calendar_sync_logic.start_tasks()
        self.activity_tracking_logic.start_tasks()
        self.bot.loop.create_task(self._run_migrations())

    async def _run_migrations(self):
        """Run any necessary data migrations"""
        await self.bot.wait_until_ready()
        
        # Run message count migration for all guilds
        for guild in self.bot.guilds:
            try:
                if hasattr(self, 'activity_tracking_logic') and self.activity_tracking_logic:
                    await self.activity_tracking_logic._migrate_existing_users(guild)
            except Exception as e:
                log.error(f"Migration failed for guild {guild.id}: {e}")

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
        
        # Add persistent report moderation view
        log.info("Initializing persistent report moderation views...")
        try:
            # Create a dummy view just for registration - it will be replaced when actual reports are created
            dummy_embed = discord.Embed(title="Dummy", description="Dummy")
            report_view = ReportModerationView(self.report_logic, dummy_embed, 0)
            self.bot.add_view(report_view)
            log.info("Successfully registered persistent report moderation view")
        except Exception as e:
            log.error(f"Failed to register persistent report moderation view: {e}")

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
        self.lfg_logic.stop_tasks() # NEW
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

    # =============================================================================
    # EVENT LISTENERS (XP System)
    # =============================================================================

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Handle voice state updates for activity tracking and XP awards."""
        if member.bot:
            return
        await self.activity_tracking_logic.handle_voice_state_update(member, before, after)

    @commands.Cog.listener()
    async def on_message(self, message):
        """Handle messages for XP awards and LFG forum filtering."""
        if message.author.bot or not message.guild:
            return
        await self.activity_tracking_logic.handle_message(message)
        
        # Handle LFG forum message filtering
        await self.lfg_logic.on_message(message)

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        """Handle reaction additions for XP awards and LFG system."""
        if user.bot or not reaction.message.guild:
            return
        await self.activity_tracking_logic.handle_reaction_add(reaction, user)
        
        # Handle LFG reactions
        await self.lfg_logic.on_reaction_add(reaction, user)

    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        """Auto-sync when a new role is created."""
        webhook_url = await self.config.guild(role.guild).django_webhook_url()
        if webhook_url:
            webhook_secret = await self.config.guild(role.guild).django_webhook_secret()
            try:
                await self.web_manager._sync_all_roles_to_django(role.guild, webhook_url, webhook_secret)
                log.info(f"Auto-synced new role '{role.name}' to Django for guild {role.guild.name}")
            except Exception as e:
                log.error(f"Failed to auto-sync new role '{role.name}' to Django: {e}")

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        """Auto-sync when a role is deleted."""
        webhook_url = await self.config.guild(role.guild).django_webhook_url()
        if webhook_url:
            webhook_secret = await self.config.guild(role.guild).django_webhook_secret()
            try:
                await self.web_manager._sync_all_roles_to_django(role.guild, webhook_url, webhook_secret)
                log.info(f"Auto-synced after role '{role.name}' deletion to Django for guild {role.guild.name}")
            except Exception as e:
                log.error(f"Failed to auto-sync after role '{role.name}' deletion to Django: {e}")

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        """Auto-sync when a role is updated (name change, color change, etc)."""
        # Only sync if the role name changed (to avoid excessive syncing)
        if before.name != after.name:
            webhook_url = await self.config.guild(after.guild).django_webhook_url()
            if webhook_url:
                webhook_secret = await self.config.guild(after.guild).django_webhook_secret()
                try:
                    await self.web_manager._sync_all_roles_to_django(after.guild, webhook_url, webhook_secret)
                    log.info(f"Auto-synced role update '{before.name}' -> '{after.name}' to Django for guild {after.guild.name}")
                except Exception as e:
                    log.error(f"Failed to auto-sync role update '{before.name}' -> '{after.name}' to Django: {e}")

    # =============================================================================
    # MAIN COMMAND GROUPS
    # =============================================================================

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
        await self.application_ping_logic.show_config(ctx)  # NEW
        await self.report_logic.show_config(ctx)  # NEW
        # Acknowledge the gamertag system, which is user-based
        embed = discord.Embed(
            title="🎮 Gamertag System",
            description="The gamertag system is user-data based and has no server-wide settings to display. "
                        "Use `!gtag stats` for usage statistics.",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

    # =============================================================================
    # XP ACTIVITY TRACKING COMMANDS (CLEANED UP)
    # =============================================================================

    @zerolivesleft_group.group(name="xp", aliases=["activityset", "atset"])
    async def xp_group(self, ctx: commands.Context):
        """🎯 XP-based Activity Tracking System"""
        if ctx.invoked_subcommand is None: 
            await ctx.send_help(ctx.command)

    # === SETUP COMMANDS (Start Here!) ===
    @xp_group.command(name="quicksetup")
    async def xp_quick_setup(self, ctx):
        """🚀 Complete setup guide for the dual-track XP system."""
        embed = discord.Embed(
            title="🎯 Dual-Track XP System Setup Guide",
            description="Your server uses a dual progression system:",
            color=discord.Color.green()
        )
        embed.add_field(
            name="🏘️ Community Track",
            value="Recruit → Member (24 hours activity, permanent upgrade)",
            inline=False
        )
        embed.add_field(
            name="🎖️ Military Track", 
            value="Recruit → Private → Higher Ranks (12+ hours to start, XP-based)",
            inline=False
        )
        embed.add_field(
            name="1️⃣ Setup Dual System",
            value="`!zll xp setupdual` - Configure both progression tracks",
            inline=False
        )
        embed.add_field(
            name="2️⃣ Setup Military Ranks",
            value="`!zll xp setupranks` - Configure your 29 existing ranks",
            inline=False
        )
        embed.add_field(
            name="3️⃣ Configure XP Rates",
            value="`!zll xp rates 1 3 1 5` - Voice/Message/Reaction/Join XP",
            inline=False
        )
        embed.add_field(
            name="4️⃣ Enable Prestige",
            value="`!zll prestige enable true 0.5` - Enable prestige system",
            inline=False
        )
        embed.add_field(
            name="5️⃣ Set Channels & API",
            value="`!zll xp channel #promotions`\n`!zll xp api <url> <key>`",
            inline=False
        )
        embed.set_footer(text="Users can be both Recruit + Private until they reach Member status!")
        await ctx.send(embed=embed)

    @xp_group.command(name="setupranks")
    async def xp_setup_ranks(self, ctx):
        """Set up your server's 29 military ranks with XP requirements."""
        await self.activity_tracking_logic.setup_default_ranks(ctx)

    @xp_group.command(name="setupdual")
    async def xp_setup_dual(self, ctx):
        """🎯 Set up the dual progression system (Community + Military tracks)."""
        await self.activity_tracking_logic.setup_dual_system(ctx)

    @xp_group.command(name="baseroles")
    async def xp_manage_base_roles(self, ctx, action: str, role: discord.Role = None):
        """Manage base roles required for XP earning: list/add/remove/clear"""
        await self.activity_tracking_logic.manage_base_roles(ctx, action, role)

    @xp_group.command(name="setuprecruit")  
    async def xp_setup_recruit(self, ctx):
        """⚠️ DEPRECATED: Use setupdual instead for the new dual-track system."""
        await ctx.send(
            "⚠️ **This command is deprecated!**\n"
            f"Use `{ctx.prefix}zll xp setupdual` instead to set up the new dual progression system.\n\n"
            "**Dual System Benefits:**\n"
            "• Community track: Recruit → Member (24 hours)\n" 
            "• Military track: Recruit → Private → Higher ranks (12+ hours, XP-based)\n"
            "• Users can have both Recruit + Military rank simultaneously"
        )

    # === XP CONFIGURATION ===
    @xp_group.command(name="rates")
    async def xp_set_rates(self, ctx, voice: int = 1, message: int = 3, reaction: int = 1, voice_join: int = 5):
        """Set XP rates: !zll xp rates <voice/min> <message> <reaction> <voice_join>"""
        await self.activity_tracking_logic.set_xp_rates(ctx, voice, message, reaction, voice_join)

    @xp_group.command(name="cooldown")
    async def xp_set_cooldown(self, ctx, seconds: int = 60):
        """Set message XP cooldown to prevent spam."""
        await self.activity_tracking_logic.set_message_cooldown(ctx, seconds)

    # === ADMIN TOOLS ===
    @xp_group.command(name="give")
    async def xp_give(self, ctx, member: discord.Member, amount: int, *, reason: str = "Admin award"):
        """Give XP to a user: !zll xp give @user 500 Good job!"""
        await self.activity_tracking_logic.add_xp_command(ctx, member, amount, reason)

    @xp_group.command(name="reset")
    async def xp_reset(self, ctx, member: discord.Member):
        """Reset a user's XP completely."""
        await self.activity_tracking_logic.reset_user_xp(ctx, member)

    @xp_group.command(name="award")
    async def xp_bulk_award(self, ctx, amount: int, role: discord.Role = None):
        """Award XP to all members or specific role: !zll xp award 100 @Role"""
        await self.activity_tracking_logic.bulk_award_xp(ctx, amount, role)

    # === CONFIGURATION ===
    @xp_group.command(name="api")
    async def xp_set_api(self, ctx, url: str, key: str):
        """Set website API: !zll xp api https://site.com/api/ your-key"""
        await self.activity_tracking_logic.set_api(ctx, url, key)

    @xp_group.command(name="channel")
    async def xp_set_channel(self, ctx, channel: discord.TextChannel):
        """Set promotion announcement channel."""
        await self.activity_tracking_logic.set_promotion_channel(ctx, channel)

    # === INFO & DEBUG ===
    @xp_group.command(name="config")
    async def xp_show_config(self, ctx):
        """Show current XP system configuration."""
        await self.activity_tracking_logic.show_config_command(ctx)

    @xp_group.command(name="info")
    async def xp_debug_info(self, ctx):
        """Show debug information and system status."""
        await self.activity_tracking_logic.debug_info(ctx)

    @xp_group.command(name="sync")
    async def xp_force_sync(self, ctx):
        """Force sync all active voice users."""
        await self.activity_tracking_logic.force_sync(ctx)

    # =============================================================================
    # PRESTIGE SYSTEM COMMANDS
    # =============================================================================

    @zerolivesleft_group.group(name="prestige")
    async def prestige_group(self, ctx):
        """🌟 Prestige system commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @prestige_group.command(name="enable")
    async def prestige_enable(self, ctx, enabled: bool = True, multiplier: float = 0.5):
        """Enable prestige: !zll prestige enable true 0.5"""
        await self.activity_tracking_logic.setup_prestige(ctx, enabled, multiplier)

    # =============================================================================
    # MILITARY RANKS COMMANDS
    # =============================================================================

    @zerolivesleft_group.group(name="ranks")
    async def ranks_group(self, ctx):
        """🎖️ Manage military ranks."""
        if ctx.invoked_subcommand is None: 
            await ctx.send_help(ctx.command)

    @ranks_group.command(name="add")
    async def ranks_add(self, ctx, role: discord.Role, required_xp: int):
        """Add military rank: !zll ranks add @Role 1000"""
        await self.activity_tracking_logic.add_rank(ctx, role, required_xp)

    @ranks_group.command(name="remove")
    async def ranks_remove(self, ctx, role_or_name: str):
        """Remove military rank by name or ID."""
        await self.activity_tracking_logic.remove_rank(ctx, role_or_name)

    @ranks_group.command(name="list")
    async def ranks_list(self, ctx):
        """List all configured military ranks."""
        await self.activity_tracking_logic.list_ranks(ctx)

    @ranks_group.command(name="clear")
    async def ranks_clear(self, ctx):
        """Clear all military ranks (dangerous!)"""
        await self.activity_tracking_logic.clear_ranks(ctx)

    # =============================================================================
    # USER COMMANDS (XP & Activity)
    # =============================================================================

    @commands.hybrid_command(name="myxp")
    async def myxp(self, ctx):
        """Check your XP and prestige level."""
        await self.activity_tracking_logic.myxp(ctx)

    @commands.hybrid_command(name="myvoicetime")
    async def myvoicetime(self, ctx): 
        """Check your total voice activity time."""
        await self.activity_tracking_logic.myvoicetime(ctx)

    @commands.hybrid_command(name="status")
    async def status(self, ctx, member: discord.Member = None): 
        """Show detailed progression status for yourself or another user."""
        await self.activity_tracking_logic.status(ctx, member)

    @commands.hybrid_command(name="leaderboard", aliases=["lb", "top"])
    async def leaderboard(self, ctx, page: int = 1):
        """Show XP leaderboard for the server."""
        await self.activity_tracking_logic.leaderboard(ctx, page)

    @commands.hybrid_command(name="prestige")
    async def prestige_command(self, ctx):
        """Prestige if you're eligible (resets XP for higher prestige level)."""
        await self.activity_tracking_logic.prestige_command(ctx)

    # =============================================================================
    # GAMERTAGS COMMANDS (NEW)
    # =============================================================================

    @commands.hybrid_group(name="gamertag", aliases=["gtag"], invoke_without_command=True)
    async def gamertag_group(self, ctx: commands.Context, *, user_input: str = None):
        """🎮 View your own or another user's gamertags. Use `!gtag setup` to begin."""
        if ctx.invoked_subcommand is None:
            await self.gamertags_logic.view_gamertags(ctx, user_input)

    @gamertag_group.command(name="setup")
    async def gtag_setup(self, ctx: commands.Context):
        """Start an interactive DM to set up your gamertags."""
        await self.gamertags_logic.setup_gamertags(ctx)

    @gamertag_group.command(name="me")
    async def gtag_me(self, ctx: commands.Context):
        """View your own saved gamertags."""
        await self.gamertags_logic.list_my_gamertags(ctx)

    @gamertag_group.command(name="clear")
    async def gtag_clear(self, ctx: commands.Context):
        """Clear all of your saved gamertags."""
        await self.gamertags_logic.clear_gamertags(ctx)

    @gamertag_group.command(name="stats")
    @commands.is_owner()
    async def gtag_stats(self, ctx: commands.Context):
        """Show statistics for the gamertag system."""
        await self.gamertags_logic.get_stats(ctx)

    # =============================================================================
    # LFG SYSTEM COMMANDS (NEW)
    # =============================================================================

    @zerolivesleft_group.group(name="lfg")
    async def lfg_group(self, ctx: commands.Context):
        """🎮 Looking for Group system commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @lfg_group.command(name="setup")
    @commands.admin_or_permissions(manage_guild=True)
    async def lfg_setup(self, ctx, forum_channel: discord.ForumChannel):
        """Set up the LFG system with a forum channel"""
        await self.lfg_logic.setup_lfg(ctx, forum_channel)

    @lfg_group.command(name="config")
    @commands.admin_or_permissions(manage_guild=True)
    async def lfg_config(self, ctx, setting: str = None, *, value: str = None):
        """Configure LFG settings: role, cleanup, maxplayers"""
        await self.lfg_logic.config_lfg(ctx, setting, value=value)

    @commands.hybrid_command(name="lfg")
    async def create_lfg_post(self, ctx, game: str, players_needed: int, description: str = None, time: str = None):
        """
        Create a Looking for Group post
        
        Usage: !lfg <game> <players_needed> [description] [time]
        Example: !lfg "Valorant" 3 "Ranked games" "8pm EST"
        """
        await self.lfg_logic.create_lfg(ctx, game, players_needed, description, time)

    # =============================================================================
    # REPORT SYSTEM COMMANDS
    # =============================================================================

    @zerolivesleft_group.group(name="report")
    async def report_group(self, ctx: commands.Context):
        """📋 Configure the server reporting system."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @report_group.command(name="setchannel")
    @commands.admin_or_permissions(manage_guild=True)
    async def report_set_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel where reports will be sent."""
        await self.report_logic.set_report_channel(ctx, channel)

    @report_group.command(name="setcooldown")
    @commands.admin_or_permissions(manage_guild=True)
    async def report_set_cooldown(self, ctx, seconds: int):
        """Set the cooldown between reports (in seconds)."""
        await self.report_logic.set_cooldown(ctx, seconds)

    @report_group.command(name="addrole")
    @commands.admin_or_permissions(manage_guild=True)
    async def report_add_role(self, ctx, role: discord.Role):
        """Add a role that can submit reports."""
        await self.report_logic.add_allowed_role(ctx, role)

    @report_group.command(name="removerole")
    @commands.admin_or_permissions(manage_guild=True)
    async def report_remove_role(self, ctx, role: discord.Role):
        """Remove a role from being able to submit reports."""
        await self.report_logic.remove_allowed_role(ctx, role)

    @report_group.command(name="clearroles")
    @commands.admin_or_permissions(manage_guild=True)
    async def report_clear_roles(self, ctx):
        """Clear all role restrictions (everyone can report)."""
        await self.report_logic.clear_allowed_roles(ctx)

    @report_group.command(name="config")
    @commands.admin_or_permissions(manage_guild=True)
    async def report_show_config(self, ctx):
        """Show current report system configuration."""
        await self.report_logic.show_config(ctx)

    @report_group.command(name="stats")
    @commands.mod_or_permissions(manage_messages=True)
    async def report_show_stats(self, ctx):
        """View report system statistics."""
        await self.report_logic.show_stats(ctx)

    # User command (outside the admin group)
    @commands.hybrid_command(name="report")
    async def report_command(self, ctx):
        """Submit a report using an interactive form."""
        await self.report_logic.submit_report(ctx)

    # =============================================================================
    # WEBSERVER COMMANDS
    # =============================================================================

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

    # =============================================================================
    # APPLICATION PING COMMANDS (NEW)
    # =============================================================================

    @zerolivesleft_group.group(name="appping", aliases=["ap"])
    async def appping_group(self, ctx: commands.Context):
        """📢 Manage moderator notifications for application submissions."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @appping_group.command(name="setchannel")
    async def appping_set_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel where moderator notifications are sent"""
        await self.application_ping_logic.set_moderator_channel(ctx, channel)

    @appping_group.command(name="setrole")
    async def appping_set_role(self, ctx, role: discord.Role):
        """Set the role to ping when applications are submitted"""
        await self.application_ping_logic.set_moderator_role(ctx, role)

    @appping_group.command(name="setadminurl")
    async def appping_set_admin_url(self, ctx, base_url: str):
        """Set the base URL for the admin panel (e.g., https://zerolivesleft.net/admin)"""
        await self.application_ping_logic.set_admin_panel_url(ctx, base_url)

    @appping_group.command(name="onlineonly")
    async def appping_online_only(self, ctx, online_only: bool):
        """Set whether to only ping online moderators (true/false)"""
        await self.application_ping_logic.set_ping_online_only(ctx, online_only)

    @appping_group.command(name="test")
    async def appping_test(self, ctx, test_user: discord.Member = None):
        """Send a test notification to verify the setup"""
        await self.application_ping_logic.test_notification(ctx, test_user)

    @appping_group.command(name="config")
    async def appping_show_config(self, ctx):
        """Show current application ping configuration"""
        await self.application_ping_logic.show_config(ctx)

    @appping_group.command(name="pending")
    async def appping_list_pending(self, ctx):
        """List currently pending applications"""
        await self.application_ping_logic.list_pending_applications(ctx)

    @appping_group.command(name="clear")
    async def appping_clear_processed(self, ctx):
        """Clear processed applications from tracking (admin only)"""
        await self.application_ping_logic.clear_processed_applications(ctx)

    @appping_group.command(name="startup")
    async def appping_force_startup_check(self, ctx):
        """Manually run the startup application check"""
        await self.application_ping_logic.force_startup_check(ctx)

    @appping_group.command(name="stats")
    async def appping_show_stats(self, ctx):
        """Show application tracking statistics"""
        await self.application_ping_logic.get_processed_count(ctx)

    @appping_group.command(name="info")
    async def appping_show_info(self, ctx):
        """Show information about the startup check system"""
        await self.application_ping_logic.show_startup_check_info(ctx)

    # =============================================================================  
    # APPLICATION PING COMMANDS - PERIODIC CHECKING (FIXED - NO NESTING)
    # =============================================================================

    @appping_group.command(name="periodicstatus")
    async def appping_periodic_status(self, ctx):
        """Show current periodic check status and next check time"""
        await self.application_ping_logic.show_periodic_status(ctx)

    @appping_group.command(name="periodiccheck", aliases=["forcecheck"])
    async def appping_periodic_force_check(self, ctx):
        """Force run a periodic application check immediately"""
        await self.application_ping_logic.force_periodic_check(ctx)

    @appping_group.command(name="periodictoggle")
    async def appping_periodic_toggle(self, ctx, enabled: bool = None):
        """Enable or disable periodic application checks
        
        Usage:
        - `periodictoggle` - Toggle current state
        - `periodictoggle true` - Enable periodic checks  
        - `periodictoggle false` - Disable periodic checks
        """
        await self.application_ping_logic.toggle_periodic_checks(ctx, enabled)

    @appping_group.command(name="periodicinterval")
    async def appping_periodic_set_interval(self, ctx, minutes: int):
        """Set the interval for periodic application checks (1-1440 minutes)
        
        Examples:
        - `periodicinterval 5` - Check every 5 minutes (default)
        - `periodicinterval 10` - Check every 10 minutes
        - `periodicinterval 60` - Check every hour
        """
        await self.application_ping_logic.set_check_interval(ctx, minutes)
    
    # =============================================================================
    # DJANGO INTEGRATION COMMANDS
    # =============================================================================

    @zerolivesleft_group.group(name="django")
    async def django_group(self, ctx: commands.Context):
        """Django website integration commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @django_group.command(name="setwebhook")
    async def django_set_webhook(self, ctx, url: str):
        """Set Django webhook URL for role sync."""
        await self.config.guild(ctx.guild).django_webhook_url.set(url)
        await ctx.send(f"Django webhook URL set to: {url}")

    @django_group.command(name="setwebhooksecret")
    async def django_set_webhook_secret(self, ctx, *, secret: str):
        """Set Django webhook secret for secure communication."""
        await self.config.guild(ctx.guild).django_webhook_secret.set(secret)
        await ctx.send("Django webhook secret has been set.")
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @django_group.command(name="syncall")
    async def django_sync_all_roles(self, ctx):
        """Manually sync all Discord roles to Django."""
        webhook_url = await self.config.guild(ctx.guild).django_webhook_url()
        if not webhook_url:
            return await ctx.send("❌ Django webhook URL not configured. Use `!zll django setwebhook <url>` first.")
        
        webhook_secret = await self.config.guild(ctx.guild).django_webhook_secret()
        
        try:
            await self.web_manager._sync_all_roles_to_django(ctx.guild, webhook_url, webhook_secret)
            await ctx.send("✅ Successfully synced all roles to Django!")
        except Exception as e:
            await ctx.send(f"❌ Error syncing roles: {e}")

    @django_group.command(name="debug")
    async def django_debug_config(self, ctx):
        """Debug Django sync configuration and role mappings."""
        embed = discord.Embed(title="Django Sync Debug Information", color=discord.Color.blue())
        
        # Check webhook config
        webhook_url = await self.config.guild(ctx.guild).django_webhook_url()
        webhook_secret = await self.config.guild(ctx.guild).django_webhook_secret()
        
        embed.add_field(
            name="Webhook Configuration",
            value=f"URL: {'✅ Set' if webhook_url else '❌ Not set'}\nSecret: {'✅ Set' if webhook_secret else '❌ Not set'}",
            inline=False
        )
        
        # Check role mappings from the CORRECT location (global config)
        try:
            role_mappings = await self.config.gc_game_role_mappings()
            counting_guild_id = await self.config.gc_counting_guild_id()
            
            embed.add_field(
                name="Role Mappings (gc_game_role_mappings)",
                value=f"Found {len(role_mappings)} mappings" if role_mappings else "❌ No mappings found",
                inline=False
            )
            
            embed.add_field(
                name="Counting Guild",
                value=f"Set to: {counting_guild_id}" if counting_guild_id else "❌ Not set",
                inline=False
            )
            
            if role_mappings:
                mapping_text = ""
                counting_guild = self.bot.get_guild(counting_guild_id) if counting_guild_id else None
                
                for role_id, game_name in list(role_mappings.items())[:5]:  # Show first 5
                    if counting_guild:
                        role = counting_guild.get_role(int(role_id))
                        role_name = role.name if role else "Role Not Found"
                    else:
                        role_name = "Guild Not Found"
                    mapping_text += f"• {role_name} ({role_id}) → {game_name}\n"
                
                if len(role_mappings) > 5:
                    mapping_text += f"... and {len(role_mappings) - 5} more"
                
                embed.add_field(name="Sample Mappings", value=mapping_text, inline=False)
                
        except Exception as e:
            embed.add_field(name="Role Mappings Error", value=f"❌ Error: {e}", inline=False)
        
        await ctx.send(embed=embed)

    @django_group.command(name="testwebhook")
    async def django_test_webhook(self, ctx):
        """Test the Django webhook with a simple payload."""
        webhook_url = await self.config.guild(ctx.guild).django_webhook_url()
        if not webhook_url:
            return await ctx.send("❌ Django webhook URL not configured.")
        
        webhook_secret = await self.config.guild(ctx.guild).django_webhook_secret()
        
        # Create a test payload
        test_payload = {
            'action': 'sync_games',
            'guild_id': str(ctx.guild.id),
            'guild_name': ctx.guild.name,
            'game_roles': [
                {
                    'id': '12345',
                    'name': 'Test Role',
                    'game_name': 'Test Game',
                    'description': 'Test webhook payload',
                    'color': '#ff0000',
                    'member_count': 1
                }
            ]
        }
        
        try:
            await ctx.send("🔄 Testing webhook...")
            await self.web_manager._send_webhook_to_django(test_payload, webhook_url, webhook_secret)
            await ctx.send("✅ Test webhook sent! Check your Django admin for a 'Test Game' entry.")
        except Exception as e:
            await ctx.send(f"❌ Error testing webhook: {e}")

    @django_group.command(name="syncgames")
    async def django_sync_game_roles(self, ctx):
        """Manually sync only game roles to Django based on role mappings."""
        webhook_url = await self.config.guild(ctx.guild).django_webhook_url()
        if not webhook_url:
            return await ctx.send("❌ Django webhook URL not configured. Use `!zll django setwebhook <url>` first.")
        
        # Check if there are any role mappings configured (in GLOBAL config)
        role_mappings = await self.config.gc_game_role_mappings()
        if not role_mappings:
            return await ctx.send(
                "❌ No game role mappings found. Use `!zll rolecounter addmapping @Role GameName` to add game roles first.\n"
                f"Example: `!zll rolecounter addmapping @Minecraft Minecraft`\n"
                f"Then use `!zll rolecounter listmappings` to see all mapped roles.\n"
                f"Use `!zll django debug` to see detailed debug information."
            )
        
        webhook_secret = await self.config.guild(ctx.guild).django_webhook_secret()
        
        try:
            await ctx.send(f"🔄 Syncing {len(role_mappings)} game role mappings to Django...")
            await self.web_manager._sync_game_roles_to_django(ctx.guild, webhook_url, webhook_secret)
            await ctx.send(f"✅ Successfully synced {len(role_mappings)} game role mappings to Django!")
        except Exception as e:
            await ctx.send(f"❌ Error syncing game roles: {e}")

    # =============================================================================
    # ROLE COUNTER COMMANDS
    # =============================================================================

    @zerolivesleft_group.group(name="rolecounter", aliases=["rc"])
    async def rolecounter_group(self, ctx: commands.Context):
        """Manage the RoleCounter settings."""
        if ctx.invoked_subcommand is None: 
            await ctx.send_help(ctx.command)

    @rolecounter_group.command(name="setapiurl")
    async def rolecounter_set_api_url(self, ctx, url: str): 
        await self.role_counting_logic.set_api_url(ctx, url)

    @rolecounter_group.command(name="setapikey")
    async def rolecounter_set_api_key(self, ctx, *, key: str): 
        await self.role_counting_logic.set_api_key(ctx, key=key)

    @rolecounter_group.command(name="setinterval")
    async def rolecounter_set_interval(self, ctx, minutes: int): 
        await self.role_counting_logic.set_interval(ctx, minutes)

    @rolecounter_group.command(name="setguild")
    async def rolecounter_set_guild(self, ctx, guild: discord.Guild): 
        await self.role_counting_logic.set_guild(ctx, guild)

    @rolecounter_group.command(name="addmapping")
    async def rolecounter_add_mapping(self, ctx, role: discord.Role, *, game_name: str): 
        await self.role_counting_logic.add_mapping(ctx, role, game_name=game_name)

    @rolecounter_group.command(name="removemapping")
    async def rolecounter_remove_mapping(self, ctx, role: discord.Role): 
        await self.role_counting_logic.remove_mapping(ctx, role)

    @rolecounter_group.command(name="listmappings")
    async def rolecounter_list_mappings(self, ctx): 
        await self.role_counting_logic.list_mappings(ctx)

    @rolecounter_group.command(name="showconfig")
    async def rolecounter_show_config(self, ctx): 
        await self.role_counting_logic.show_config_command(ctx)

    # =============================================================================
    # OTHER MODULE COMMANDS
    # =============================================================================

    # Application status check command
    @commands.hybrid_command(name="appstatus", aliases=["application", "checkapp"])
    async def check_application_status(self, ctx, member: discord.Member = None):
        """Check your application status or another member's status (if you have permissions)"""
        await self.application_roles_logic.check_application_status(ctx, member)

    @zerolivesleft_group.group(name="calendar", aliases=["cal"])
    async def calendar_group(self, ctx):
        """Calendar management commands."""
        if ctx.invoked_subcommand is None: 
            await ctx.send_help(ctx.command)

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
        if ctx.invoked_subcommand is None: 
            await ctx.send_help(ctx.command)

    @approles_group.command(name="setapiurl")
    async def approles_set_api_url(self, ctx, url: str): 
        await self.application_roles_logic.set_api_url(ctx, url)

    @approles_group.command(name="setapikey")
    async def approles_set_api_key(self, ctx, *, key: str): 
        await self.application_roles_logic.set_api_key(ctx, key=key)

    @approles_group.command(name="setpendingrole")
    async def approles_set_pending_role(self, ctx, role: discord.Role): 
        await self.application_roles_logic.set_pending_role(ctx, role)

    @approles_group.command(name="setmemberrole")
    async def approles_set_member_role(self, ctx, role: discord.Role): 
        await self.application_roles_logic.set_member_role(ctx, role)

    @approles_group.command(name="setunverifiedrole")
    async def approles_set_unverified_role(self, ctx, role: discord.Role): 
        await self.application_roles_logic.set_unverified_role(ctx, role)

    @approles_group.command(name="setwelcomechannel")
    async def approles_set_welcome_channel(self, ctx, channel: discord.TextChannel): 
        await self.application_roles_logic.set_welcome_channel(ctx, channel)

    @approles_group.command(name="setunverifiedchannel")
    async def approles_set_unverified_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the channel where new members with the Unverified role receive their welcome message."""
        await self.application_roles_logic.set_unverified_channel(ctx, channel)

    @approles_group.command(name="setpendingchannel")
    async def approles_set_pending_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the channel where members with a Pending application role receive their updates."""
        await self.application_roles_logic.set_pending_channel(ctx, channel)

    @approles_group.command(name="setnotificationschannel")
    async def approles_set_notifications_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Sets the channel where application status notifications are sent."""
        await self.application_roles_logic.set_notifications_channel(ctx, channel)

    @approles_group.command(name="testflow")
    async def approles_test_flow(self, ctx, member: discord.Member, status: str):
        """Test member flow with different statuses: none, pending, approved, rejected"""
        await self.application_roles_logic.test_member_flow(ctx, member, status)

    @approles_group.command(name="debug")
    async def approles_debug(self, ctx, member: discord.Member = None):
        """Show debug info for a member's current status and configured roles/channels"""
        await self.application_roles_logic.debug_member_status(ctx, member)

    @approles_group.command(name="move")
    async def approles_manual_move(self, ctx, member: discord.Member, from_status: str, to_status: str):
        """Manually move a user between statuses: unverified, pending, approved"""
        await self.application_roles_logic.manual_move_user(ctx, member, from_status, to_status)

    @approles_group.command(name="history")
    async def approles_view_history(self, ctx, member: discord.Member = None):
        """View detailed join/leave history for a member"""
        await self.application_roles_logic.view_member_history(ctx, member)

    @approles_group.command(name="clearhistory")
    async def approles_clear_history(self, ctx, member: discord.Member):
        """Clear a member's join/leave history (admin only, for testing)"""
        await self.application_roles_logic.clear_member_history(ctx, member)

    @approles_group.command(name="addregion")
    async def approles_add_region(self, ctx, region: str, role: discord.Role): 
        await self.application_roles_logic.add_region_role(ctx, region, role)

    @approles_group.command(name="removeregion")
    async def approles_remove_region(self, ctx, region: str): 
        await self.application_roles_logic.remove_region_role(ctx, region)

    @approles_group.command(name="listregions")
    async def approles_list_regions(self, ctx): 
        await self.application_roles_logic.list_region_roles(ctx)

    @approles_group.command(name="showconfig")
    async def approles_show_config(self, ctx): 
        await self.application_roles_logic.show_config(ctx)

    @approles_group.command(name="setdefaultguild")
    async def approles_set_default_guild(self, ctx, guild: discord.Guild): 
        await self.application_roles_logic.set_default_guild(ctx, guild)

    @zerolivesleft_group.group(name="message", aliases=["msg"])
    async def message_group(self, ctx: commands.Context):
        """Manage the automated messages sent to new members."""
        if ctx.invoked_subcommand is None: 
            await ctx.send_help(ctx.command)

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
    async def menu_create(self, ctx, name: str): 
        await self.role_menu_logic.create_menu(ctx, name)

    @rolemenu_group.command(name="delete")
    async def menu_delete(self, ctx, name: str): 
        await self.role_menu_logic.delete_menu(ctx, name)

    @rolemenu_group.command(name="list")
    async def menu_list(self, ctx): 
        await self.role_menu_logic.list_menus(ctx)

    @rolemenu_group.command(name="edit")
    async def menu_edit(self, ctx, name: str, setting: str, *, value: str): 
        await self.role_menu_logic.edit_menu(ctx, name, setting, value)

    @rolemenu_group.command(name="image")
    async def menu_image(self, ctx, name: str, image_url: str): 
        await self.role_menu_logic.edit_menu(ctx, name, "image_url", image_url)

    @rolemenu_group.command(name="addrole")
    async def rolemenu_add_role(self, ctx, menu_name: str, role: discord.Role, *, label: str = None): 
        await self.role_menu_logic.add_role_to_menu(ctx, menu_name, role, label)

    @rolemenu_group.command(name="removerole")
    async def rolemenu_remove_role(self, ctx, menu_name: str, role: discord.Role): 
        await self.role_menu_logic.remove_role_from_menu(ctx, menu_name, role)

    @rolemenu_group.command(name="post")
    async def rolemenu_post(self, ctx, menu_name: str, channel: discord.TextChannel = None): 
        await self.role_menu_logic.post_menu(ctx, menu_name, channel)

    @rolemenu_group.command(name="update")
    async def rolemenu_update(self, ctx, menu_name: str): 
        await self.role_menu_logic.update_menu_message(ctx, menu_name)

    @rolemenu_group.command(name="updateall")
    async def rolemenu_update_all(self, ctx): 
        await self.role_menu_logic.update_all_menus(ctx)

    @rolemenu_group.command(name="debug")
    async def rolemenu_debug(self, ctx, menu_name: str): 
        await self.role_menu_logic.debug_menu(ctx, menu_name)

    @rolemenu_group.command(name="autoroles")
    async def rolemenu_autoroles(self, ctx, channel: discord.TextChannel = None): 
        await self.role_menu_logic.send_autoroles_menu(ctx, channel)

    @approles_group.command(name="setmoderatorschannel")
    async def approles_set_moderators_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.application_roles_logic.set_moderator_channel(ctx, channel)

    @approles_group.command(name="setmoderatorrole")
    async def approles_set_moderator_role(self, ctx: commands.Context, role: discord.Role):
        await self.application_roles_logic.set_moderator_role(ctx, role)

    @approles_group.command(name="setpingonlineonly")
    async def approles_set_ping_online_only(self, ctx: commands.Context, online_only: bool):
        await self.application_roles_logic.set_ping_online_only(ctx, online_only)

    @approles_group.command(name="setadminpanelurl")
    async def approles_set_admin_panel_url(self, ctx: commands.Context, base_url: str):
        await self.application_roles_logic.set_admin_panel_url(ctx, base_url)

async def setup(bot: Red):
    cog = Zerolivesleft(bot)
    await bot.add_cog(cog)