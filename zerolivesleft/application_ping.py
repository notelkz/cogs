# application_ping.py
# Handles moderator notifications when applications are submitted

import discord
import logging
import asyncio
import json
from typing import Dict, List, Optional
import aiohttp
from datetime import datetime
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
        )
        
        log.info("ApplicationPingLogic initialized")

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
            review_url = f"{admin_panel_url.rstrip('/')}/applications/application/{application_id}/change/"
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
            count = len(pending)
            pending.clear()
            
        await ctx.send(f"✅ Cleared {count} tracked applications from the system.")
        log.info(f"Cleared {count} pending applications from tracking")

    def register_routes(self, web_app):
        """Register web routes for this logic"""
        web_app.router.add_post("/api/applications/submitted/ping", self.handle_application_submitted_ping)
        log.info("Application ping routes registered")