# application_ping.py
# Handles moderator notifications when applications are submitted

import discord
import logging
import asyncio
import json
from typing import Dict, List, Optional
import aiohttp
from datetime import datetime, timedelta
from aiohttp import web
from redbot.core import commands, Config

log = logging.getLogger("red.Elkz.zerolivesleft.application_ping")

class ApplicationPingLogic:
    """
    Handles notifying moderators when applications are submitted and provides review links.
    """

    def __init__(self, cog):
        self.cog = cog
        self.bot = cog.bot
        self.config = cog.config
        self.session = cog.session
        
        # Register configuration for this logic
        self.config.register_global(
            # Moderator notification settings
            ap_moderator_channel_id=None,      # Channel to send notifications
            ap_moderator_role_id=None,         # Role to ping for notifications
            ap_admin_panel_base_url=None,      # Base URL for admin panel (e.g., https://zerolivesleft.net/admin)
            ap_ping_online_only=True,          # Only ping online moderators
            ap_notification_embed_color=0x3498db,  # Blue color for embeds
            ap_include_user_info=True,         # Include user avatar/info in embed
            
            # Application tracking
            ap_pending_applications={},        # Track pending applications {app_id: {user_id, submitted_at, etc}}
            ap_processed_applications=[],      # Track processed applications to avoid duplicates (LIST not set)
            ap_last_startup_check=None,        # Last time we did a startup check
            
            # Periodic check configuration
            ap_periodic_check_enabled=True,      # Enable/disable periodic checks
            ap_check_interval_minutes=5,         # Check every 5 minutes
            ap_last_periodic_check=None,         # Last time we did a periodic check
            ap_periodic_check_running=False,     # Flag to prevent overlapping checks
        )
        
        # Start both startup check and periodic checking
        self.bot.loop.create_task(self._startup_application_check())
        self.bot.loop.create_task(self._start_periodic_checking())
        
        log.info("ApplicationPingLogic initialized with periodic checking")

    async def handle_application_submitted_ping(self, request):
        """
        Handle webhook when application is submitted - send moderator notification
        This is called AFTER the main application_roles logic processes the user
        """
        try:
            log.info("Application ping webhook endpoint called")
            
            # Authenticate the request
            auth_header = request.headers.get('Authorization', '')
            if auth_header.startswith('Token '):
                api_key = auth_header.replace('Token ', '')
            elif auth_header.startswith('Bearer '):
                api_key = auth_header.replace('Bearer ', '')
            else:
                api_key = auth_header
                
            expected_key = await self.cog.config.ar_api_key()  # Reuse the same API key
            
            if not api_key or api_key != expected_key:
                log.warning("API key validation failed for application ping")
                return web.json_response({"error": "Unauthorized"}, status=401)
            
            # Parse the request data
            data = await request.json()
            log.info(f"Application ping data received: {data}")
            
            # Extract application data
            discord_id = data.get("discord_id")
            application_id = data.get("application_id")
            application_data = data.get("application_data", {})
            submitted_at = data.get("submitted_at")
            
            if not all([discord_id, application_id]):
                log.error("Missing required data in application ping webhook")
                return web.json_response({"error": "Missing discord_id or application_id"}, status=400)

            # Process the notification
            await self._send_moderator_notification(
                discord_id=int(discord_id),
                application_id=application_id,
                application_data=application_data,
                submitted_at=submitted_at
            )
            
            return web.json_response({"success": True, "message": "Moderator notification sent"})
            
        except Exception as e:
            log.error(f"Error in handle_application_submitted_ping: {e}", exc_info=True)
            return web.json_response({"error": f"Internal server error: {str(e)}"}, status=500)

    async def _send_moderator_notification(self, discord_id: int, application_id: str, application_data: dict, submitted_at: str = None):
        """Send notification to moderators about new application"""
        
        # Get configuration
        moderator_channel_id = await self.config.ap_moderator_channel_id()
        moderator_role_id = await self.config.ap_moderator_role_id()
        admin_panel_url = await self.config.ap_admin_panel_base_url()
        ping_online_only = await self.config.ap_ping_online_only()
        embed_color = await self.config.ap_notification_embed_color()
        include_user_info = await self.config.ap_include_user_info()
        
        if not moderator_channel_id:
            log.warning("Moderator channel not configured - cannot send notification")
            return
            
        # Get the default guild
        default_guild_id = await self.cog.config.ar_default_guild_id()
        if not default_guild_id:
            log.error("Default guild not configured for application ping")
            return
            
        guild = self.bot.get_guild(int(default_guild_id))
        if not guild:
            log.error(f"Guild {default_guild_id} not found for application ping")
            return
            
        channel = guild.get_channel(int(moderator_channel_id))
        if not channel:
            log.error(f"Moderator channel {moderator_channel_id} not found in guild {guild.name}")
            return
            
        # Get the member who submitted the application
        member = guild.get_member(discord_id)
        if not member:
            log.warning(f"Member {discord_id} not found in guild - they may have left")
            member_name = f"Unknown User ({discord_id})"
            member_avatar = None
            member_mention = f"<@{discord_id}>"
        else:
            member_name = f"{member.display_name} ({member.name}#{member.discriminator})"
            member_avatar = member.display_avatar.url if member.display_avatar else None
            member_mention = member.mention

        # Create the notification embed
        embed = discord.Embed(
            title="🔔 New Application Submitted",
            description=f"A new application has been submitted and needs review!",
            color=embed_color,
            timestamp=datetime.now()
        )
        
        # Add user information
        if include_user_info and member_avatar:
            embed.set_thumbnail(url=member_avatar)
            
        embed.add_field(
            name="👤 Applicant", 
            value=f"{member_mention}\n`{member_name}`\n`ID: {discord_id}`",
            inline=True
        )
        
        embed.add_field(
            name="📝 Application ID",
            value=f"`{application_id}`",
            inline=True
        )
        
        if submitted_at:
            embed.add_field(
                name="⏰ Submitted At",
                value=f"`{submitted_at}`",
                inline=True
            )
        
        # Add application details if available
        if application_data:
            details = []
            
            # Add age if available
            if application_data.get("age"):
                details.append(f"**Age:** {application_data['age']}")
            
            # Add display name and ingame name if available
            if application_data.get("display_name"):
                details.append(f"**Display Name:** {application_data['display_name']}")
            
            if application_data.get("ingame_name"):
                details.append(f"**In-Game Name:** {application_data['ingame_name']}")
            
            if application_data.get("region"):
                details.append(f"**Region:** {application_data['region']}")
                
            if application_data.get("games"):
                games = application_data["games"][:3]  # Show first 3 games
                details.append(f"**Games:** {', '.join(games)}")
                if len(application_data["games"]) > 3:
                    details.append(f"... and {len(application_data['games']) - 3} more")
                    
            if application_data.get("platforms"):
                details.append(f"**Platforms:** {', '.join(application_data['platforms'])}")
                
            if details:
                embed.add_field(
                    name="📋 Application Details",
                    value="\n".join(details),
                    inline=False
                )
        
        # Add review link if admin panel URL is configured
        if admin_panel_url:
            review_url = f"{admin_panel_url.rstrip('/')}/community/enhancedapplication/{application_id}/change/"
            embed.add_field(
                name="🔗 Review Application",
                value=f"[**Click here to review and approve/reject**]({review_url})",
                inline=False
            )
            
        # Add quick action buttons (if we want to implement those later)
        embed.add_field(
            name="ℹ️ Next Steps",
            value="Review the application using the admin panel link above, then approve or reject it.",
            inline=False
        )
        
        embed.set_footer(text=f"Application #{application_id} • Zero Lives Left")
        
        # Prepare the mention
        mention_text = ""
        if moderator_role_id:
            role = guild.get_role(int(moderator_role_id))
            if role:
                if ping_online_only:
                    # Get online members with the moderator role
                    online_mods = [
                        member for member in role.members 
                        if member.status != discord.Status.offline and not member.bot
                    ]
                    if online_mods:
                        mention_text = " ".join([mod.mention for mod in online_mods[:5]])  # Limit to 5 mentions
                        if len(online_mods) > 5:
                            mention_text += f" (and {len(online_mods) - 5} other online moderators)"
                    else:
                        mention_text = role.mention  # Fallback to role mention if no one is online
                else:
                    mention_text = role.mention
            else:
                log.warning(f"Moderator role {moderator_role_id} not found in guild")
        
        # Store the pending application for tracking
        async with self.config.ap_pending_applications() as pending:
            pending[application_id] = {
                "user_id": discord_id,
                "submitted_at": submitted_at or datetime.now().isoformat(),
                "notification_sent_at": datetime.now().isoformat(),
                "guild_id": guild.id
            }
        
        # Mark as processed to avoid duplicates
        async with self.config.ap_processed_applications() as processed:
            if application_id not in processed:
                processed.append(application_id)
        
        # Send the notification
        try:
            message = await channel.send(content=mention_text, embed=embed)
            log.info(f"Sent moderator notification for application {application_id} by user {discord_id}")
            
            # Add reaction for quick acknowledgment
            try:
                await message.add_reaction("👀")  # Eyes reaction for "reviewing"
                await message.add_reaction("✅")  # Check for "processed"
            except discord.HTTPException:
                pass  # Ignore if we can't add reactions
                
        except discord.HTTPException as e:
            log.error(f"Failed to send moderator notification: {e}")

    async def _startup_application_check(self):
        """Check for pending applications that may have been missed during bot downtime"""
        await self.bot.wait_until_ready()
        
        # Wait a bit for the bot to fully initialize
        await asyncio.sleep(10)
        
        log.info("🔍 Starting startup application check...")
        
        try:
            # Get configuration
            moderator_channel_id = await self.config.ap_moderator_channel_id()
            admin_panel_url = await self.config.ap_admin_panel_base_url()
            processed_applications = await self.config.ap_processed_applications()
            
            if not moderator_channel_id:
                log.info("📭 Startup check skipped: No moderator channel configured")
                return
                
            # Get the guild and channel
            default_guild_id = await self.cog.config.ar_default_guild_id()
            if not default_guild_id:
                log.warning("⚠️ Startup check failed: Default guild not configured")
                return
                
            guild = self.bot.get_guild(int(default_guild_id))
            if not guild:
                log.warning(f"⚠️ Startup check failed: Guild {default_guild_id} not found")
                return
                
            channel = guild.get_channel(int(moderator_channel_id))
            if not channel:
                log.warning(f"⚠️ Startup check failed: Channel {moderator_channel_id} not found")
                return
            
            # Make API call to Django to get pending applications
            api_url = await self.cog.config.ar_api_url()
            api_key = await self.cog.config.ar_api_key()
            
            if not api_url or not api_key:
                log.info("📭 Startup check skipped: Django API not configured")
                return
            
            # Call Django API to get pending applications
            try:
                endpoint = f"{api_url.rstrip('/')}/api/applications/pending/"
                headers = {"Authorization": f"Token {api_key}"}
                
                async with self.session.get(endpoint, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        pending_apps = await resp.json()
                        log.info(f"📋 Found {len(pending_apps)} pending applications from Django")
                        
                        notifications_sent = 0
                        for app in pending_apps:
                            app_id = str(app.get('id'))
                            
                            # Skip if we've already processed this application
                            if app_id in processed_applications:
                                log.debug(f"⏭️ Skipping application {app_id} - already processed")
                                continue
                            
                            # Send notification for this missed application
                            try:
                                await self._send_moderator_notification(
                                    discord_id=int(app.get('discord_id')),
                                    application_id=app_id,
                                    application_data=app.get('application_data', {}),
                                    submitted_at=app.get('submitted_at')
                                )
                                notifications_sent += 1
                                log.info(f"📨 Sent startup notification for missed application {app_id}")
                                
                                # Small delay to avoid rate limits
                                await asyncio.sleep(1)
                                
                            except Exception as e:
                                log.error(f"❌ Failed to send startup notification for application {app_id}: {e}")
                        
                        if notifications_sent > 0:
                            # Send a summary message to moderators
                            summary_embed = discord.Embed(
                                title="🔄 Startup Application Check Complete",
                                description=f"Found and notified about **{notifications_sent}** pending applications that were submitted while the bot was offline.",
                                color=discord.Color.orange(),
                                timestamp=datetime.now()
                            )
                            summary_embed.set_footer(text="All pending applications have been processed")
                            
                            try:
                                await channel.send(embed=summary_embed)
                                log.info(f"✅ Startup check complete: {notifications_sent} notifications sent")
                            except discord.HTTPException:
                                pass
                        else:
                            log.info("✅ Startup check complete: No missed applications found")
                    
                    elif resp.status == 404:
                        log.info("📭 Startup check: No pending applications endpoint available")
                    else:
                        log.warning(f"⚠️ Startup check API returned {resp.status}")
                        
            except asyncio.TimeoutError:
                log.warning("⏰ Startup check timed out - Django API may be slow")
            except Exception as e:
                log.error(f"❌ Error during startup application check: {e}")
            
            # Update last check time
            await self.config.ap_last_startup_check.set(datetime.now().isoformat())
            
        except Exception as e:
            log.error(f"❌ Fatal error in startup application check: {e}")

    async def _start_periodic_checking(self):
        """Start the periodic application checking loop"""
        await self.bot.wait_until_ready()
        
        # Wait for startup check to complete first
        await asyncio.sleep(15)
        
        log.info("🔄 Starting periodic application checking...")
        
        while True:
            try:
                # Check if periodic checking is enabled
                if not await self.config.ap_periodic_check_enabled():
                    log.debug("Periodic checking is disabled, skipping...")
                    await asyncio.sleep(60)  # Check again in 1 minute
                    continue
                
                # Check if we're already running a check
                if await self.config.ap_periodic_check_running():
                    log.debug("Periodic check already running, skipping...")
                    await asyncio.sleep(30)  # Wait 30 seconds and try again
                    continue
                
                # Get the check interval
                interval_minutes = await self.config.ap_check_interval_minutes()
                
                # Check if it's time for the next check
                last_check_str = await self.config.ap_last_periodic_check()
                if last_check_str:
                    last_check = datetime.fromisoformat(last_check_str)
                    next_check = last_check + timedelta(minutes=interval_minutes)
                    
                    if datetime.now() < next_check:
                        # Not time yet, wait until it is
                        wait_seconds = (next_check - datetime.now()).total_seconds()
                        log.debug(f"Next periodic check in {wait_seconds:.0f} seconds")
                        await asyncio.sleep(min(wait_seconds, 60))  # Max 60 second sleep
                        continue
                
                # Time to run the check
                await self._run_periodic_check()
                
            except Exception as e:
                log.error(f"❌ Error in periodic checking loop: {e}")
                await asyncio.sleep(60)  # Wait 1 minute before retrying

    async def _run_periodic_check(self):
        """Run a periodic check for new applications"""
        try:
            # Set flag that we're running
            await self.config.ap_periodic_check_running.set(True)
            
            log.info("🔍 Running periodic application check...")
            
            # Get configuration
            moderator_channel_id = await self.config.ap_moderator_channel_id()
            processed_applications = await self.config.ap_processed_applications()
            
            if not moderator_channel_id:
                log.debug("📭 Periodic check skipped: No moderator channel configured")
                return
            
            # Get API configuration
            api_url = await self.cog.config.ar_api_url()
            api_key = await self.cog.config.ar_api_key()
            
            if not api_url or not api_key:
                log.debug("📭 Periodic check skipped: Django API not configured")
                return
            
            # Call Django API to get pending applications
            try:
                endpoint = f"{api_url.rstrip('/')}/api/applications/pending/"
                headers = {"Authorization": f"Token {api_key}"}
                
                async with self.session.get(endpoint, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        pending_apps = await resp.json()
                        log.debug(f"📋 Periodic check found {len(pending_apps)} pending applications")
                        
                        notifications_sent = 0
                        for app in pending_apps:
                            app_id = str(app.get('id'))
                            
                            # Skip if we've already processed this application
                            if app_id in processed_applications:
                                continue
                            
                            # Send notification for this new application
                            try:
                                await self._send_moderator_notification(
                                    discord_id=int(app.get('discord_id')),
                                    application_id=app_id,
                                    application_data=app.get('application_data', {}),
                                    submitted_at=app.get('submitted_at')
                                )
                                notifications_sent += 1
                                log.info(f"📨 Sent periodic notification for new application {app_id}")
                                
                                # Small delay to avoid rate limits
                                await asyncio.sleep(1)
                                
                            except Exception as e:
                                log.error(f"❌ Failed to send periodic notification for application {app_id}: {e}")
                        
                        if notifications_sent > 0:
                            log.info(f"✅ Periodic check complete: {notifications_sent} new applications found and notified")
                        else:
                            log.debug("✅ Periodic check complete: No new applications found")
                    
                    elif resp.status == 404:
                        log.debug("📭 Periodic check: No pending applications endpoint available")
                    else:
                        log.warning(f"⚠️ Periodic check API returned {resp.status}")
                        
            except asyncio.TimeoutError:
                log.warning("⏰ Periodic check timed out - Django API may be slow")
            except Exception as e:
                log.error(f"❌ Error during periodic application check: {e}")
            
            # Update last check time
            await self.config.ap_last_periodic_check.set(datetime.now().isoformat())
            
        except Exception as e:
            log.error(f"❌ Fatal error in periodic application check: {e}")
        finally:
            # Always clear the running flag
            await self.config.ap_periodic_check_running.set(False)

    async def force_startup_check(self, ctx: commands.Context):
        """Manually trigger startup application check"""
        await ctx.send("🔍 Running startup application check...")
        await self._startup_application_check()
        await ctx.send("✅ Startup application check completed!")

    async def force_periodic_check(self, ctx: commands.Context):
        """Manually trigger a periodic application check"""
        if await self.config.ap_periodic_check_running():
            await ctx.send("⏳ A periodic check is already running. Please wait for it to complete.")
            return
        
        await ctx.send("🔍 Running periodic application check...")
        await self._run_periodic_check()
        await ctx.send("✅ Periodic application check completed!")

    async def set_check_interval(self, ctx: commands.Context, minutes: int):
        """Set the interval for periodic application checks (in minutes)"""
        if minutes < 1:
            await ctx.send("❌ Check interval must be at least 1 minute.")
            return
        
        if minutes > 1440:  # 24 hours
            await ctx.send("❌ Check interval cannot be more than 1440 minutes (24 hours).")
            return
        
        await self.config.ap_check_interval_minutes.set(minutes)
        await ctx.send(f"✅ Periodic check interval set to **{minutes} minutes**")
        log.info(f"Periodic check interval set to {minutes} minutes")

    async def toggle_periodic_checks(self, ctx: commands.Context, enabled: bool = None):
        """Enable or disable periodic application checks"""
        if enabled is None:
            # Toggle current state
            current_state = await self.config.ap_periodic_check_enabled()
            enabled = not current_state
        
        await self.config.ap_periodic_check_enabled.set(enabled)
        status = "enabled" if enabled else "disabled"
        await ctx.send(f"✅ Periodic application checks have been **{status}**")
        log.info(f"Periodic checks {status}")

    async def show_periodic_status(self, ctx: commands.Context):
        """Show the status of periodic application checking"""
        embed = discord.Embed(
            title="🔄 Periodic Application Check Status",
            color=discord.Color.blue()
        )
        
        # Get configuration
        enabled = await self.config.ap_periodic_check_enabled()
        interval_minutes = await self.config.ap_check_interval_minutes()
        last_check = await self.config.ap_last_periodic_check()
        running = await self.config.ap_periodic_check_running()
        
        # Status
        status_emoji = "🟢" if enabled else "🔴"
        embed.add_field(
            name="Status",
            value=f"{status_emoji} {'Enabled' if enabled else 'Disabled'}",
            inline=True
        )
        
        # Current state
        if running:
            embed.add_field(
                name="Current State",
                value="🔄 Check Running",
                inline=True
            )
        else:
            embed.add_field(
                name="Current State",
                value="⏸️ Idle",
                inline=True
            )
        
        # Interval
        embed.add_field(
            name="Check Interval",
            value=f"`{interval_minutes} minutes`",
            inline=True
        )
        
        # Last check
        if last_check:
            last_check_time = datetime.fromisoformat(last_check)
            time_ago = datetime.now() - last_check_time
            
            if time_ago.total_seconds() < 60:
                time_str = f"{int(time_ago.total_seconds())} seconds ago"
            elif time_ago.total_seconds() < 3600:
                time_str = f"{int(time_ago.total_seconds() / 60)} minutes ago"
            else:
                time_str = f"{int(time_ago.total_seconds() / 3600)} hours ago"
                
            embed.add_field(
                name="Last Check",
                value=f"`{last_check[:19]}`\n({time_str})",
                inline=False
            )
        else:
            embed.add_field(
                name="Last Check",
                value="`Never`",
                inline=False
            )
        
        # Next check (if enabled)
        if enabled and last_check and not running:
            last_check_time = datetime.fromisoformat(last_check)
            next_check = last_check_time + timedelta(minutes=interval_minutes)
            time_until = next_check - datetime.now()
            
            if time_until.total_seconds() > 0:
                if time_until.total_seconds() < 60:
                    next_str = f"{int(time_until.total_seconds())} seconds"
                else:
                    next_str = f"{int(time_until.total_seconds() / 60)} minutes"
                    
                embed.add_field(
                    name="Next Check",
                    value=f"In `{next_str}`",
                    inline=True
                )
            else:
                embed.add_field(
                    name="Next Check",
                    value="⏰ Due now",
                    inline=True
                )
        
        # Commands
        embed.add_field(
            name="Commands",
            value=(
                "`periodic status` - Show this status\n"
                "`periodic toggle` - Enable/disable checks\n"
                "`periodic interval <minutes>` - Set check interval\n"
                "`periodic check` - Force check now"
            ),
            inline=False
        )
        
        await ctx.send(embed=embed)

    async def get_processed_count(self, ctx: commands.Context):
        """Show how many applications have been processed"""
        processed_applications = await self.config.ap_processed_applications()
        pending_applications = await self.config.ap_pending_applications()
        last_startup_check = await self.config.ap_last_startup_check()
        last_periodic_check = await self.config.ap_last_periodic_check()
        
        embed = discord.Embed(
            title="📊 Application Tracking Statistics",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="Processed Applications",
            value=f"`{len(processed_applications)}` applications",
            inline=True
        )
        
        embed.add_field(
            name="Currently Pending",
            value=f"`{len(pending_applications)}` applications",
            inline=True
        )
        
        embed.add_field(
            name="Last Startup Check",
            value=f"`{last_startup_check[:19] if last_startup_check else 'Never'}`",
            inline=False
        )
        
        embed.add_field(
            name="Last Periodic Check",
            value=f"`{last_periodic_check[:19] if last_periodic_check else 'Never'}`",
            inline=False
        )
        
        await ctx.send(embed=embed)

    # Configuration commands
    async def set_moderator_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Set the channel where moderator notifications are sent"""
        await self.config.ap_moderator_channel_id.set(channel.id)
        await ctx.send(f"✅ Moderator notification channel set to {channel.mention}")
        log.info(f"Moderator notification channel set to {channel.name} ({channel.id})")

    async def set_moderator_role(self, ctx: commands.Context, role: discord.Role):
        """Set the role to ping when applications are submitted"""
        await self.config.ap_moderator_role_id.set(role.id)
        await ctx.send(f"✅ Moderator role set to {role.mention}")
        log.info(f"Moderator role set to {role.name} ({role.id})")

    async def set_admin_panel_url(self, ctx: commands.Context, base_url: str):
        """Set the base URL for the admin panel (e.g., https://zerolivesleft.net/admin)"""
        # Clean up the URL
        if not base_url.startswith(('http://', 'https://')):
            base_url = 'https://' + base_url
        base_url = base_url.rstrip('/')
        
        await self.config.ap_admin_panel_base_url.set(base_url)
        await ctx.send(f"✅ Admin panel base URL set to: `{base_url}`")
        log.info(f"Admin panel base URL set to {base_url}")

    async def set_ping_online_only(self, ctx: commands.Context, online_only: bool):
        """Set whether to only ping online moderators"""
        await self.config.ap_ping_online_only.set(online_only)
        status = "only ping online moderators" if online_only else "ping all moderators"
        await ctx.send(f"✅ Moderator pinging set to: {status}")
        log.info(f"Ping online only set to {online_only}")

    async def test_notification(self, ctx: commands.Context, test_user: discord.Member = None):
        """Send a test notification to verify the setup"""
        target_user = test_user or ctx.author
        
        # Create test data
        test_data = {
            "discord_id": target_user.id,
            "application_id": "TEST-" + str(datetime.now().strftime("%Y%m%d-%H%M%S")),
            "application_data": {
                "display_name": target_user.display_name,
                "ingame_name": f"{target_user.name}_gaming",
                "age": 25,
                "region": "US",
                "games": ["Minecraft", "Call of Duty", "Valorant"],
                "platforms": ["PC", "Console"]
            },
            "submitted_at": datetime.now().isoformat()
        }
        
        await ctx.send("🧪 Sending test notification...")
        
        try:
            await self._send_moderator_notification(
                discord_id=test_data["discord_id"],
                application_id=test_data["application_id"],
                application_data=test_data["application_data"],
                submitted_at=test_data["submitted_at"]
            )
            await ctx.send("✅ Test notification sent successfully!")
        except Exception as e:
            await ctx.send(f"❌ Error sending test notification: {e}")
            log.error(f"Error in test notification: {e}")

    async def show_config(self, ctx: commands.Context):
        """Show current application ping configuration"""
        embed = discord.Embed(
            title="Application Ping Configuration",
            color=discord.Color.blue()
        )
        
        # Get all config values
        moderator_channel_id = await self.config.ap_moderator_channel_id()
        moderator_role_id = await self.config.ap_moderator_role_id()
        admin_panel_url = await self.config.ap_admin_panel_base_url()
        ping_online_only = await self.config.ap_ping_online_only()
        embed_color = await self.config.ap_notification_embed_color()
        pending_apps = await self.config.ap_pending_applications()
        
        # Periodic check info
        periodic_enabled = await self.config.ap_periodic_check_enabled()
        check_interval = await self.config.ap_check_interval_minutes()
        
        # Channel configuration
        if moderator_channel_id:
            channel = ctx.guild.get_channel(int(moderator_channel_id))
            channel_value = channel.mention if channel else f"<#{moderator_channel_id}> (Not found)"
        else:
            channel_value = "`Not set`"
        embed.add_field(name="📢 Notification Channel", value=channel_value, inline=False)
        
        # Role configuration
        if moderator_role_id:
            role = ctx.guild.get_role(int(moderator_role_id))
            role_value = role.mention if role else f"<@&{moderator_role_id}> (Not found)"
        else:
            role_value = "`Not set`"
        embed.add_field(name="👥 Moderator Role", value=role_value, inline=False)
        
        # Admin panel URL
        embed.add_field(
            name="🔗 Admin Panel URL",
            value=f"`{admin_panel_url}`" if admin_panel_url else "`Not set`",
            inline=False
        )
        
        # Ping settings
        embed.add_field(
            name="📳 Ping Settings",
            value=f"Online only: `{'Yes' if ping_online_only else 'No'}`\nEmbed color: `#{embed_color:06x}`",
            inline=False
        )
        
        # Periodic check settings
        periodic_status = "🟢 Enabled" if periodic_enabled else "🔴 Disabled"
        embed.add_field(
            name="🔄 Periodic Checks",
            value=f"Status: {periodic_status}\nInterval: `{check_interval} minutes`",
            inline=False
        )
        
        # Pending applications
        embed.add_field(
            name="📋 Pending Applications",
            value=f"`{len(pending_apps)} tracked`",
            inline=False
        )
        
        await ctx.send(embed=embed)

    async def list_pending_applications(self, ctx: commands.Context):
        """List currently pending applications"""
        pending_apps = await self.config.ap_pending_applications()
        
        if not pending_apps:
            await ctx.send("📭 No pending applications currently tracked.")
            return
            
        embed = discord.Embed(
            title="📋 Pending Applications",
            description=f"Currently tracking {len(pending_apps)} pending applications:",
            color=discord.Color.orange()
        )
        
        for app_id, data in list(pending_apps.items())[:10]:  # Show max 10
            user_id = data.get("user_id")
            submitted_at = data.get("submitted_at", "Unknown")
            
            # Try to get the user
            user = self.bot.get_user(user_id) if user_id else None
            user_info = f"<@{user_id}>" if user else f"Unknown ({user_id})"
            
            embed.add_field(
                name=f"Application {app_id}",
                value=f"User: {user_info}\nSubmitted: {submitted_at[:10] if submitted_at != 'Unknown' else submitted_at}",
                inline=True
            )
        
        if len(pending_apps) > 10:
            embed.set_footer(text=f"... and {len(pending_apps) - 10} more pending applications")
            
        await ctx.send(embed=embed)

    async def clear_processed_applications(self, ctx: commands.Context):
        """Clear processed applications from tracking (admin only)"""
        if not ctx.author.guild_permissions.administrator:
            await ctx.send("❌ You need administrator permissions to use this command.")
            return
            
        async with self.config.ap_pending_applications() as pending:
            pending_count = len(pending)
            pending.clear()
            
        async with self.config.ap_processed_applications() as processed:
            processed_count = len(processed)
            processed.clear()
            
        await ctx.send(f"✅ Cleared {pending_count} pending and {processed_count} processed applications from tracking.")
        log.info(f"Cleared {pending_count} pending and {processed_count} processed applications from tracking")

    async def show_startup_check_info(self, ctx: commands.Context):
        """Show information about the startup check system"""
        embed = discord.Embed(
            title="🔍 Startup Application Check System",
            description="Automatically checks for missed applications when the bot starts up.",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="How it works",
            value=(
                "• Bot checks Django API for pending applications on startup\n"
                "• Compares with internal tracking to avoid duplicates\n"
                "• Sends notifications for any missed applications\n"
                "• Runs automatically 10 seconds after bot startup"
            ),
            inline=False
        )
        
        last_check = await self.config.ap_last_startup_check()
        embed.add_field(
            name="Last Check",
            value=f"`{last_check[:19] if last_check else 'Never'}`",
            inline=True
        )
        
        processed_apps = await self.config.ap_processed_applications()
        embed.add_field(
            name="Applications Tracked",
            value=f"`{len(processed_apps)}` processed",
            inline=True
        )
        
        embed.add_field(
            name="Manual Commands",
            value="`!zll appping startup` - Force run startup check\n`!zll appping stats` - Show tracking statistics",
            inline=False
        )
        
        await ctx.send(embed=embed)

    def register_routes(self, web_app):
        """Register web routes for this logic"""
        web_app.router.add_post("/api/applications/submitted/ping", self.handle_application_submitted_ping)
        log.info("Application ping routes registered")