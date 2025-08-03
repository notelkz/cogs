# Complete clean application_roles.py with join history tracking and moderator notifications

import discord
import logging
import asyncio
import json
from typing import Dict, List, Optional, Tuple
import aiohttp
from datetime import datetime, timedelta
from aiohttp import web
from redbot.core import commands, Config

log = logging.getLogger("red.Elkz.zerolivesleft.application_roles")

class ApplicationRolesLogic:
    """
    Handles assigning roles to new members and updating them upon application approval.
    """

    def __init__(self, cog):
        self.cog = cog
        self.bot = cog.bot
        self.config = cog.config
        self.session = cog.session
        
        self.config.register_global(
            ar_api_url=None,
            ar_api_key=None,
            ar_region_roles={},
            ar_enabled=True,
            ar_default_guild_id=None,
            ar_invite_channel_id=None,
            ar_pending_role_id=None,
            ar_member_role_id=None,
            ar_unverified_role_id=None,
            # Channel configurations
            ar_welcome_channel_id=None,  # For approved members (main server)
            ar_unverified_channel_id=None,  # For unverified members (#dmz)
            ar_pending_channel_id=None,     # For pending members (#enlistment)
            ar_notifications_channel_id=None,  # For status update notifications
            ar_welcome_message="Welcome {mention}! To join our community, please submit an application at https://zerolivesleft.net/apply/",
            # Join history tracking
            ar_member_history={},  # {guild_id: {user_id: {"joins": [timestamps], "leaves": [timestamps], "total_joins": int}}}
            # NEW: Moderator notification settings
            ar_moderator_channel_id=None,        # Channel for moderator notifications
            ar_moderator_role_id=None,           # Role to ping for new applications
            ar_ping_online_only=False,           # Whether to ping only online moderators
            ar_admin_panel_base_url="https://zerolivesleft.net/admin/community/enhancedapplication/",
        )
        
        self.guild_invites = {}
        
        self.bot.add_listener(self.on_member_join, "on_member_join")
        self.bot.add_listener(self.on_member_remove, "on_member_remove")
        self.bot.add_listener(self.on_invite_create, "on_invite_create")
        
        self.cache_invites_task = asyncio.create_task(self.cache_all_invites())
        
        log.info("ApplicationRolesLogic initialized")

    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return

        guild = member.guild
        log.info(f"New member joined: {member.name} ({member.id}). Checking application status.")
        
        default_guild_id = await self.config.ar_default_guild_id()
        if not default_guild_id or guild.id != int(default_guild_id):
            return

        # Track this join in our history
        is_returning = await self._track_member_join(guild.id, member.id)
        log.info(f"Member {member.name} join tracked. Returning member: {is_returning}")

        api_key = await self.config.ar_api_key()
        api_url = await self.config.ar_api_url()
        
        # Default to unverified
        status = "none"
        app_data = {}
        
        if api_url and api_key:
            try:
                endpoint = f"{api_url.rstrip('/')}/api/applications/check/{member.id}/"
                headers = {"Authorization": f"Token {api_key}"}
                
                log.info(f"Checking application status at: {endpoint}")
                async with self.session.get(endpoint, headers=headers, timeout=10) as resp:
                    log.info(f"Application check response: {resp.status}")
                    if resp.status == 200:
                        data = await resp.json()
                        log.info(f"Application check data: {data}")
                        status = data.get("status", "none")
                        app_data = data.get("application_data", {})
                    else:
                        log.error(f"Failed to check application status for {member.name}: {resp.status}")

            except Exception as e:
                log.error(f"Exception checking application status for {member.name}: {e}")

        # Handle different statuses with returning member info
        await self._handle_member_by_status(member, status, app_data, is_returning)

    async def on_member_remove(self, member: discord.Member):
        """Track when members leave the server"""
        if member.bot:
            return
            
        guild = member.guild
        default_guild_id = await self.config.ar_default_guild_id()
        if not default_guild_id or guild.id != int(default_guild_id):
            return
            
        # Track this leave in our history
        await self._track_member_leave(guild.id, member.id)
        log.info(f"Member {member.name} ({member.id}) leave tracked for guild {guild.name}")

    async def _track_member_join(self, guild_id: int, user_id: int) -> bool:
        """
        Track a member joining and return True if they've been here before
        """
        async with self.config.ar_member_history() as history:
            guild_str = str(guild_id)
            user_str = str(user_id)
            
            if guild_str not in history:
                history[guild_str] = {}
            
            if user_str not in history[guild_str]:
                history[guild_str][user_str] = {
                    "joins": [],
                    "leaves": [],
                    "total_joins": 0
                }
            
            user_history = history[guild_str][user_str]
            current_time = datetime.now().isoformat()
            
            # Add this join
            user_history["joins"].append(current_time)
            user_history["total_joins"] += 1
            
            # Keep only last 10 joins/leaves to prevent data bloat
            if len(user_history["joins"]) > 10:
                user_history["joins"] = user_history["joins"][-10:]
            
            # Return True if this is NOT their first join
            is_returning = user_history["total_joins"] > 1
            
            log.info(f"Tracked join for user {user_id} in guild {guild_id}. Total joins: {user_history['total_joins']}, Returning: {is_returning}")
            return is_returning

    async def _track_member_leave(self, guild_id: int, user_id: int):
        """
        Track a member leaving
        """
        async with self.config.ar_member_history() as history:
            guild_str = str(guild_id)
            user_str = str(user_id)
            
            if guild_str not in history:
                history[guild_str] = {}
            
            if user_str not in history[guild_str]:
                # They're leaving but we have no join record - initialize
                history[guild_str][user_str] = {
                    "joins": [],
                    "leaves": [],
                    "total_joins": 0
                }
            
            user_history = history[guild_str][user_str]
            current_time = datetime.now().isoformat()
            
            # Add this leave
            user_history["leaves"].append(current_time)
            
            # Keep only last 10 joins/leaves to prevent data bloat
            if len(user_history["leaves"]) > 10:
                user_history["leaves"] = user_history["leaves"][-10:]
            
            log.info(f"Tracked leave for user {user_id} in guild {guild_id}")

    async def _get_member_history(self, guild_id: int, user_id: int) -> dict:
        """
        Get a member's join/leave history
        """
        history = await self.config.ar_member_history()
        guild_str = str(guild_id)
        user_str = str(user_id)
        
        if guild_str in history and user_str in history[guild_str]:
            return history[guild_str][user_str]
        
        return {
            "joins": [],
            "leaves": [],
            "total_joins": 0
        }

    async def _handle_member_by_status(self, member: discord.Member, status: str, app_data: dict, is_returning: bool = False):
        """Handle member join based on their application status and history"""
        guild = member.guild
        
        log.info(f"Handling member {member.name} with status: {status}, returning: {is_returning}")
        
        if status == "approved":
            # Pre-approved user - give them full access immediately (NOT pending role)
            await self._handle_approved_member(member, app_data, is_returning)
        elif status == "pending":
            # User has pending application - goes to #enlistment
            await self._handle_pending_member(member, is_returning)
        elif status == "rejected":
            # Previously rejected - treat as unverified, goes to #dmz
            await self._handle_unverified_member(member, is_returning_rejected=True, is_returning=is_returning)
        else:
            # No application or unknown status - goes to #dmz
            await self._handle_unverified_member(member, is_returning=is_returning)

    async def _handle_approved_member(self, member: discord.Member, app_data: dict, is_returning: bool = False):
        """Handle a member who was APPROVED before joining Discord - they get full access immediately"""
        guild = member.guild
        log.info(f"Pre-approved member joined: {member.name} - assigning full member roles immediately (returning: {is_returning})")
        
        # Assign all appropriate roles immediately (NOT pending role)
        roles_to_add = []
        
        # Add member role
        if member_role_id := await self.config.ar_member_role_id():
            if role := guild.get_role(int(member_role_id)):
                roles_to_add.append(role)
                log.info(f"Adding member role: {role.name}")
        
        # Add region role
        if region_code := app_data.get("region"):
            region_roles = await self.config.ar_region_roles()
            if region_role_id := region_roles.get(region_code.upper()):
                if role := guild.get_role(int(region_role_id)):
                    roles_to_add.append(role)
                    log.info(f"Adding region role: {role.name}")
        
        # Add platform and game roles
        for role_type in ["platform_role_ids", "game_role_ids"]:
            role_ids = app_data.get(role_type, [])
            for role_id in role_ids:
                if role_id and (role := guild.get_role(int(role_id))):
                    roles_to_add.append(role)
                    log.info(f"Adding {role_type.split('_')[0]} role: {role.name}")
        
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason="Pre-approved application")
            log.info(f"Added roles to pre-approved member {member.name}: {[r.name for r in roles_to_add]}")
        
        # Send welcome message to main welcome channel (they skip DMZ and enlistment entirely)
        welcome_channel_id = await self.config.ar_welcome_channel_id()
        if welcome_channel_id and (channel := guild.get_channel(int(welcome_channel_id))):
            if is_returning:
                embed = discord.Embed(
                    title="Welcome Back to Zero Lives Left!",
                    description=(
                        f"Welcome back {member.mention}! Your application was already approved - "
                        f"you now have full access to the server again! 🎉\n\n"
                        f"All your roles have been restored and you can jump right back into the action!"
                    ),
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="You're all set!",
                    value="Great to have you back! Feel free to pick up where you left off.",
                    inline=False
                )
            else:
                embed = discord.Embed(
                    title="Welcome to Zero Lives Left!",
                    description=(
                        f"Welcome {member.mention}! Your application was already approved - "
                        f"you now have full access to the server! 🎉\n\n"
                        f"You've been granted all your roles and can explore the entire server."
                    ),
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="You're all set!",
                    value="Feel free to explore all the channels and get involved in the community.",
                    inline=False
                )
            
            if guild.icon:
                embed.set_thumbnail(url=guild.icon.url)
            embed.set_footer(text="Welcome to the community!" if not is_returning else "Welcome back!")
            await channel.send(content=member.mention, embed=embed)
            log.info(f"Sent pre-approved welcome embed to {channel.name} for {member.name}.")

    async def _handle_pending_member(self, member: discord.Member, is_returning: bool = False):
        """Handle a member who has a PENDING application - goes to #enlistment"""
        guild = member.guild
        log.info(f"Member with pending application joined: {member.name} - sending to #enlistment (returning: {is_returning})")
        
        # Assign pending role
        pending_role_id = await self.config.ar_pending_role_id()
        if pending_role_id and (role := guild.get_role(int(pending_role_id))):
            await member.add_roles(role, reason="Has pending application")
            log.info(f"Assigned pending role to {member.name}")
        
        # Send message to #enlistment (pending channel)
        pending_channel_id = await self.config.ar_pending_channel_id()
        if pending_channel_id and (channel := guild.get_channel(int(pending_channel_id))):
            if is_returning:
                embed = discord.Embed(
                    title="Welcome Back to Enlistment!",
                    description=(
                        f"Welcome back {member.mention}! We see you have a pending application "
                        f"that's still being reviewed.\n\n"
                        f"You've been placed back in the **Enlistment** area while we finish "
                        f"processing your application. Thanks for your continued patience!"
                    ),
                    color=discord.Color.blurple()
                )
            else:
                embed = discord.Embed(
                    title="Welcome to Enlistment!",
                    description=(
                        f"Welcome {member.mention}! Great news - you already have an application "
                        f"submitted and it's currently under review.\n\n"
                        f"You've been placed in the **Enlistment** area while our team processes "
                        f"your application. This means you're one step closer to full access!"
                    ),
                    color=discord.Color.blurple()
                )
            
            if guild.icon:
                embed.set_thumbnail(url=guild.icon.url)
            embed.add_field(
                name="What's Next?",
                value="Our team will review your application and you'll be notified here once it's processed. Thank you for your patience!",
                inline=False
            )
            embed.set_footer(text="You're in the review queue - we'll update you soon!")
            await channel.send(content=member.mention, embed=embed)
            log.info(f"Sent pending welcome embed to #enlistment ({channel.name}) for {member.name}.")

    async def _handle_unverified_member(self, member: discord.Member, is_returning_rejected: bool = False, is_returning: bool = False):
        """Handle an unverified member (new or previously rejected) - goes to #dmz"""
        guild = member.guild
        log.info(f"Unverified member joined: {member.name} - sending to #dmz (returning rejected: {is_returning_rejected}, returning: {is_returning})")
        
        # Assign unverified role
        unverified_role_id = await self.config.ar_unverified_role_id()
        if unverified_role_id and (role := guild.get_role(int(unverified_role_id))):
            await member.add_roles(role, reason="Unverified member")
            log.info(f"Assigned unverified role to {member.name}")
        
        # Send message to #dmz (unverified channel)
        unverified_channel_id = await self.config.ar_unverified_channel_id()
        if unverified_channel_id and (channel := guild.get_channel(int(unverified_channel_id))):
            if is_returning_rejected:
                embed = discord.Embed(
                    title="Back to the DMZ",
                    description=(
                        f"Hey there {member.mention}, looks like you're back in the DMZ. "
                        f"Your previous application didn't quite make the cut, but don't worry - "
                        f"everyone gets another shot! 💪\n\n"
                        f"Take some time to look over the requirements and try again when you're ready."
                    ),
                    color=discord.Color.orange()
                )
                embed.add_field(
                    name="Ready to try again?",
                    value="When you're ready, submit a fresh application and we'll give it another look!",
                    inline=False
                )
            else:
                # For unverified users (new or returning), always treat as needing to apply
                # Don't use "welcome back" - they need to go through the application process!
                embed = discord.Embed(
                    title="Welcome to Zero Lives Left!",
                    description=(
                        f"Hey {member.mention}! Welcome to our Discord server! 👋\n\n"
                        f"You're currently in the **DMZ** area. To get access to the full server "
                        f"and join our community, you'll need to submit an application first. "
                        f"It's quick and helps us keep things organized!"
                    ),
                    color=discord.Color.blue()
                )
                embed.add_field(
                    name="How to get full access:",
                    value="1. **Apply** using the link below\n2. **Wait for review** (you'll be moved to the application area)\n3. **Get approved** and unlock everything!",
                    inline=False
                )
            
            if guild.icon:
                embed.set_thumbnail(url=guild.icon.url)
            embed.add_field(
                name="Application Form",
                value="[**Submit Application**](https://zerolivesleft.net/apply/)",
                inline=False
            )
            
            if is_returning_rejected:
                embed.set_footer(text="Everyone deserves a second chance! 🌟")
            else:
                embed.set_footer(text="Once you apply, you'll be moved to the review area!")
                
            await channel.send(content=member.mention, embed=embed)
            log.info(f"Sent unverified welcome embed to #dmz ({channel.name}) for {member.name}.")

    async def handle_application_submitted(self, request):
        """Handle when someone submits an application on the website"""
        try:
            log.info(f"Application submission endpoint called: {request.path}")
            
            body_text = await request.text()
            log.info(f"Request headers: {dict(request.headers)}")
            log.info(f"Request body: {body_text}")
            
            auth_header = request.headers.get('Authorization', '')
            log.info(f"Auth header received: '{auth_header}'")
            
            if auth_header.startswith('Token '):
                api_key = auth_header.replace('Token ', '')
            elif auth_header.startswith('Bearer '):
                api_key = auth_header.replace('Bearer ', '')
            else:
                api_key = auth_header
                
            log.info(f"Extracted API key: '{api_key[:5]}...' (if available)")
            
            expected_key = await self.config.ar_api_key()
            log.info(f"Expected API key starts with: '{expected_key[:5] if expected_key else 'None'}...' (if available)")
            
            if not api_key or api_key != expected_key:
                log.warning(f"API key validation failed. Received key doesn't match expected key.")
                return web.json_response({"error": "Unauthorized"}, status=401)
            
            try:
                data = json.loads(body_text)
            except json.JSONDecodeError:
                log.error(f"Failed to parse JSON from body text, trying request.json()")
                try:
                    data = await request.json()
                except Exception as e:
                    log.error(f"Failed to parse JSON body: {e}")
                    return web.json_response({"error": f"Invalid JSON: {str(e)}"}, status=400)
            
            log.info(f"Parsed request data: {data}")
            
            discord_id = data.get("discord_id")
            if not discord_id:
                log.error("Missing discord_id in application submission")
                return web.json_response({"error": "Missing discord_id"}, status=400)

            log.info(f"Processing application submission for Discord ID: {discord_id}")
            
            default_guild_id = await self.config.ar_default_guild_id()
            if not default_guild_id:
                log.error("Default guild ID not configured")
                return web.json_response({"error": "Guild not configured"}, status=500)
                
            guild = self.bot.get_guild(int(default_guild_id))
            if not guild:
                log.error(f"Default guild with ID {default_guild_id} not found")
                return web.json_response({"error": "Guild not found"}, status=500)
            
            member = guild.get_member(int(discord_id))
            if not member:
                log.error(f"Member with ID {discord_id} not found in guild {guild.name}")
                return web.json_response({"error": "Member not in server"}, status=404)

            log.info(f"Found member {member.name} in guild {guild.name}")
            
            # Move user from Unverified (DMZ) to Pending (Enlistment)
            await self._move_user_to_pending(member, data)
            
            return web.json_response({"success": True})
        except Exception as e:
            log.error(f"Error in handle_application_submitted: {e}", exc_info=True)
            return web.json_response({"error": f"Internal server error: {str(e)}"}, status=500)

    async def _get_online_moderators(self, guild: discord.Guild, moderator_role: discord.Role) -> List[discord.Member]:
        """Get list of online moderators from the role"""
        online_moderators = []
        for member in moderator_role.members:
            if member.status != discord.Status.offline:
                online_moderators.append(member)
        return online_moderators

    async def _send_moderator_notification(self, guild: discord.Guild, member: discord.Member, application_data: dict = None):
        """Send notification to moderators about a new application"""
        moderator_channel_id = await self.config.ar_moderator_channel_id()
        moderator_role_id = await self.config.ar_moderator_role_id()
        ping_online_only = await self.config.ar_ping_online_only()
        admin_panel_base_url = await self.config.ar_admin_panel_base_url()
        
        if not moderator_channel_id:
            log.warning("Moderator channel not configured - skipping moderator notification")
            return
        
        channel = guild.get_channel(int(moderator_channel_id))
        if not channel:
            log.error(f"Moderator channel with ID {moderator_channel_id} not found")
            return
        
        # Build the mention string
        mention_text = ""
        if moderator_role_id:
            moderator_role = guild.get_role(int(moderator_role_id))
            if moderator_role:
                if ping_online_only:
                    online_mods = await self._get_online_moderators(guild, moderator_role)
                    if online_mods:
                        mention_text = " ".join([mod.mention for mod in online_mods])
                    else:
                        mention_text = f"{moderator_role.mention} (no online moderators)"
                else:
                    mention_text = moderator_role.mention
        
        # Create the embed
        embed = discord.Embed(
            title="🆕 New Application Submitted",
            description=f"A new application has been submitted by {member.mention}",
            color=discord.Color.orange()
        )
        
        # Add basic member info
        embed.add_field(
            name="📊 Member Information",
            value=(
                f"**Username:** {member.name}#{member.discriminator}\n"
                f"**Display Name:** {member.display_name}\n"
                f"**Discord ID:** {member.id}\n"
                f"**Account Created:** <t:{int(member.created_at.timestamp())}:R>\n"
                f"**Joined Server:** <t:{int(member.joined_at.timestamp())}:R>"
            ),
            inline=False
        )
        
        # Add application preview if available
        if application_data:
            preview_text = ""
            if region := application_data.get("region"):
                preview_text += f"**Region:** {region}\n"
            if platforms := application_data.get("platforms"):
                preview_text += f"**Platforms:** {', '.join(platforms)}\n"
            if games := application_data.get("games"):
                preview_text += f"**Games:** {', '.join(games[:3])}{'...' if len(games) > 3 else ''}\n"
            
            if preview_text:
                embed.add_field(
                    name="📋 Application Preview",
                    value=preview_text,
                    inline=False
                )
        
        # Add admin panel link
        if admin_panel_base_url:
            embed.add_field(
                name="🔗 Review Application",
                value=f"[**Click here to review in Admin Panel**]({admin_panel_base_url})",
                inline=False
            )
        
        # Add member avatar
        if member.avatar:
            embed.set_thumbnail(url=member.avatar.url)
        
        embed.set_footer(
            text=f"Application submitted • Review required",
            icon_url=guild.icon.url if guild.icon else None
        )
        
        # Send the notification
        content = mention_text if mention_text else None
        
        try:
            await channel.send(content=content, embed=embed)
            log.info(f"Sent moderator notification for new application from {member.name}")
        except discord.Forbidden:
            log.error(f"Missing permissions to send message in moderator channel {channel.name}")
        except Exception as e:
            log.error(f"Error sending moderator notification: {e}")

    async def _move_user_to_pending(self, member: discord.Member, application_data: dict = None):
        """Move a user from Unverified role to Pending role and update their channel access"""
        guild = member.guild
        log.info(f"Moving {member.name} from Unverified (DMZ) to Pending (Enlistment)")
        
        unverified_role_id = await self.config.ar_unverified_role_id()
        pending_role_id = await self.config.ar_pending_role_id()
        
        if not unverified_role_id or not pending_role_id:
            log.error(f"Missing role configuration: Unverified={unverified_role_id}, Pending={pending_role_id}")
            return
                
        unverified_role = guild.get_role(int(unverified_role_id))
        pending_role = guild.get_role(int(pending_role_id))

        if not unverified_role or not pending_role:
            log.error(f"Could not find roles: Unverified={bool(unverified_role)}, Pending={bool(pending_role)}")
            return

        log.info(f"Current roles for {member.name}: {[role.name for role in member.roles]}")
        
        # Remove unverified role if they have it
        roles_to_remove = []
        if unverified_role in member.roles:
            roles_to_remove.append(unverified_role)
        
        if roles_to_remove:
            log.info(f"Removing roles: {[role.name for role in roles_to_remove]}")
            await member.remove_roles(*roles_to_remove, reason="Application submitted - moving from DMZ to Enlistment")
            log.info(f"Removed {', '.join([r.name for r in roles_to_remove])} from {member.name}")
                
        # Add pending role if they don't have it
        if pending_role not in member.roles:
            log.info(f"Adding role: {pending_role.name}")
            await member.add_roles(pending_role, reason="Application submitted - moving to Enlistment")
            log.info(f"Added {pending_role.name} to {member.name}")
        else:
            log.info(f"{member.name} already has the {pending_role.name} role")
        
        # Send notification to notifications channel
        await self._send_notification(guild, member, "Application Received", 
            f"{member.mention}, your application has been received and is now under review! You've been moved from the DMZ to the enlistment area.", 
            discord.Color.blue())
        
        # NEW: Send notification to moderators
        await self._send_moderator_notification(guild, member, application_data)
        
        # Send embed to #enlistment (pending channel) upon submission
        pending_channel_id = await self.config.ar_pending_channel_id()
        if pending_channel_id and (channel := guild.get_channel(int(pending_channel_id))):
            embed = discord.Embed(
                title="Application received! 📝",
                description=(
                    f"Perfect, {member.mention}! Your application has been submitted successfully! ✅\n\n"
                    f"**What just happened:**\n"
                    f"• You've been moved from the DMZ to the **review area**\n"
                    f"• Our team will look over your application\n"
                    f"• You'll get an update right here when we're done\n"
                    f"• If approved, you'll get full server access!"
                ),
                color=discord.Color.blue()
            )
            if guild.icon:
                embed.set_thumbnail(url=guild.icon.url)
            embed.add_field(
                name="⏱️ How long does this take?",
                value="Applications are usually reviewed within 24-48 hours. We'll ping you here as soon as we have news!",
                inline=False
            )
            embed.set_footer(text="Thanks for applying! 🎉")
            await channel.send(content=member.mention, embed=embed)
            log.info(f"Sent 'Application Received' embed to #enlistment ({channel.name}) for {member.name}.")

        log.info(f"Successfully moved {member.name} from DMZ to Enlistment after application submission")
        log.info(f"New roles: {[role.name for role in member.roles]}")

    async def handle_application_update(self, request):
        """Handle application approval/rejection updates from Django"""
        log.info("Application update endpoint called")
        try:
            body_text = await request.text()
            log.info(f"Request headers: {dict(request.headers)}")
            log.info(f"Request body: {body_text}")
            
            api_key = request.headers.get('Authorization', '').replace('Token ', '')
            expected_key = await self.config.ar_api_key()
            
            if not api_key or api_key != expected_key:
                log.warning(f"API key validation failed for application update")
                return web.json_response({"error": "Unauthorized"}, status=401)
                
            data = await request.json()
            log.info(f"Processing application update: {data}")
            self.cog.bot.loop.create_task(self.process_role_update(data))
            return web.json_response({"success": True, "message": "Request received."})
        except Exception as e:
            log.error(f"Error processing application update request: {e}", exc_info=True)
            return web.json_response({"error": f"Error: {str(e)}"}, status=400)

    async def process_role_update(self, data: dict):
        """Process application approval/rejection"""
        discord_id = data.get("discord_id")
        status = data.get("status")
        app_data = data.get("application_data", {})
        
        if not all([discord_id, status]): 
            log.error(f"Missing required data in process_role_update: discord_id={discord_id}, status={status}")
            return

        default_guild_id = await self.config.ar_default_guild_id()
        if not default_guild_id: 
            log.error("No default guild configured for role updates")
            return
        
        guild = self.bot.get_guild(int(default_guild_id))
        if not guild: 
            log.error(f"Could not find guild with ID {default_guild_id}")
            return
        
        member = guild.get_member(int(discord_id))
        if not member: 
            log.error(f"Could not find member with ID {discord_id} in guild {guild.name}")
            return

        log.info(f"Processing role update for {member.name} with status {status}")

        pending_role = guild.get_role(int(await self.config.ar_pending_role_id() or 0))
        unverified_role = guild.get_role(int(await self.config.ar_unverified_role_id() or 0))

        if status == "rejected":
            log.info(f"Application rejected for {member.name}. Moving back to DMZ and kicking.")
            
            # Send notification before kicking
            await self._send_notification(guild, member, "Application Rejected", 
                f"{member.mention}, unfortunately your application has been rejected. You will be removed from the server.", 
                discord.Color.red())
            
            roles_to_remove = [r for r in [pending_role, unverified_role] if r and r in member.roles]
            if roles_to_remove: 
                await member.remove_roles(*roles_to_remove, reason="Application Rejected.")
                log.info(f"Removed roles: {[r.name for r in roles_to_remove]}")
            
            # Try to DM them before kicking
            try:
                await member.send(
                    f"Your application to {guild.name} has been rejected. You have been removed from the server."
                )
            except (discord.Forbidden, discord.HTTPException):
                log.warning(f"Could not DM {member.name} about application rejection.")

            await member.kick(reason="Application Rejected.")
            log.info(f"Kicked {member.name} due to rejected application")
            return

        if status == "approved":
            log.info(f"Application approved for {member.name}. Moving from Enlistment to full server access.")
            
            # Send notification first
            await self._send_notification(guild, member, "Application Approved", 
                f"🎉 {member.mention}, your application has been **APPROVED**! Welcome to the community!", 
                discord.Color.green())
            
            roles_to_add = []
            roles_to_remove = [r for r in [pending_role, unverified_role] if r and r in member.roles]

            if member_role_id := await self.config.ar_member_role_id():
                if role := guild.get_role(int(member_role_id)): 
                    roles_to_add.append(role)
                    log.info(f"Adding member role: {role.name}")
            
            if region_code := app_data.get("region"):
                region_roles = await self.config.ar_region_roles()
                log.info(f"Checking region role for {region_code.upper()}. Available mappings: {region_roles}")
                if region_role_id := region_roles.get(region_code.upper()):
                    if role := guild.get_role(int(region_role_id)): 
                        roles_to_add.append(role)
                        log.info(f"Adding region role: {role.name}")
            
            for role_type in ["platform_role_ids", "game_role_ids"]:
                role_ids = app_data.get(role_type, [])
                log.info(f"Processing {role_type}: {role_ids}")
                for role_id in role_ids:
                    if role_id and (role := guild.get_role(int(role_id))): 
                        roles_to_add.append(role)
                        log.info(f"Adding {role_type.split('_')[0]} role: {role.name}")
            
            if roles_to_add: 
                await member.add_roles(*roles_to_add, reason="Application Approved - full server access granted")
                log.info(f"Added roles: {[r.name for r in roles_to_add]}")
            if roles_to_remove: 
                await member.remove_roles(*roles_to_remove, reason="Application Approved - removing pending/unverified roles")
                log.info(f"Removed roles: {[r.name for r in roles_to_remove]}")
            
            # Send success message to main welcome channel ONLY if it's configured and different from DMZ
            welcome_channel_id = await self.config.ar_welcome_channel_id()
            unverified_channel_id = await self.config.ar_unverified_channel_id()
            
            # Only send welcome message if:
            # 1. Welcome channel is configured
            # 2. Welcome channel is different from the unverified/DMZ channel
            # 3. Member can actually see the welcome channel
            if (welcome_channel_id and 
                welcome_channel_id != unverified_channel_id and 
                (channel := guild.get_channel(int(welcome_channel_id)))):
                
                # Check if member can see the channel
                if channel.permissions_for(member).read_messages:
                    embed = discord.Embed(
                        title="🎉 Application approved! Welcome to the community!",
                        description=(
                            f"Awesome news, {member.mention}! Your application has been **approved**! 🎊\n\n"
                            f"**You now have access to:**\n"
                            f"🎮 All game channels and voice rooms\n"
                            f"💬 Community discussions and events\n"
                            f"🎯 Everything Zero Lives Left has to offer!\n\n"
                            f"Welcome to the family!"
                        ),
                        color=discord.Color.green()
                    )
                    if guild.icon:
                        embed.set_thumbnail(url=guild.icon.url)
                    embed.add_field(
                        name="Ready to get started?",
                        value="Explore the channels, introduce yourself, and jump into some games with everyone!",
                        inline=False
                    )
                    embed.set_footer(text="Welcome to Zero Lives Left! 🚀")
                    await channel.send(content=member.mention, embed=embed)
                    log.info(f"Sent 'Application Approved' embed to main welcome channel ({channel.name}) for {member.name}.")
                else:
                    log.warning(f"Member {member.name} cannot see welcome channel {channel.name} - skipping welcome message")
            else:
                if welcome_channel_id == unverified_channel_id:
                    log.info(f"Welcome channel is same as DMZ channel - skipping welcome message for {member.name}")
                else:
                    log.info(f"No appropriate welcome channel configured for approved members - skipping welcome message for {member.name}")

            log.info(f"Role update complete for {member.name} - they now have full server access")

    async def _send_notification(self, guild: discord.Guild, member: discord.Member, title: str, message: str, color: discord.Color):
        """Send a notification to the configured notifications channel"""
        notifications_channel_id = await self.config.ar_notifications_channel_id()
        if notifications_channel_id and (channel := guild.get_channel(int(notifications_channel_id))):
            embed = discord.Embed(title=title, description=message, color=color)
            embed.set_footer(text=f"User: {member.name}#{member.discriminator} ({member.id})")
            await channel.send(embed=embed)
            log.info(f"Sent notification to {channel.name}: {title} for {member.name}")

    # Moderator notification configuration methods
    async def set_moderator_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel where moderator notifications are sent for new applications"""
        await self.config.ar_moderator_channel_id.set(channel.id)
        await ctx.send(f"Moderator notifications channel set to {channel.mention}")
        log.info(f"Moderator notifications channel set to {channel.name} ({channel.id})")

    async def set_moderator_role(self, ctx, role: discord.Role):
        """Set the role to ping when new applications are submitted"""
        await self.config.ar_moderator_role_id.set(role.id)
        await ctx.send(f"Moderator role set to {role.mention}")
        log.info(f"Moderator role set to {role.name} ({role.id})")

    async def set_ping_online_only(self, ctx, online_only: bool):
        """Set whether to ping only online moderators or all moderators"""
        await self.config.ar_ping_online_only.set(online_only)
        status = "only online moderators" if online_only else "all moderators"
        await ctx.send(f"Moderator pinging set to: {status}")
        log.info(f"Moderator ping setting: online_only={online_only}")

    async def set_admin_panel_url(self, ctx, base_url: str):
        """Set the base URL for the Django admin panel"""
        # Ensure URL ends with a slash
        if not base_url.endswith('/'):
            base_url += '/'
        
        await self.config.ar_admin_panel_base_url.set(base_url)
        await ctx.send(f"Admin panel base URL set to: {base_url}")
        log.info(f"Admin panel base URL set to: {base_url}")

    # Self-service status check command
    async def check_application_status(self, ctx, member: discord.Member = None):
        """Check application status for yourself or another member"""
        target = member or ctx.author
        
        # Only allow checking others if user has manage roles permission
        if member and not ctx.author.guild_permissions.manage_roles:
            await ctx.send("You can only check your own application status.", ephemeral=True)
            return
        
        api_url = await self.config.ar_api_url()
        api_key = await self.config.ar_api_key()
        
        if not api_url or not api_key:
            await ctx.send("Application system is not configured.", ephemeral=True)
            return
        
        try:
            endpoint = f"{api_url.rstrip('/')}/api/applications/check/{target.id}/"
            headers = {"Authorization": f"Token {api_key}"}
            
            async with self.session.get(endpoint, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    status = data.get("status", "none")
                    submission_date = data.get("submitted_date")
                    
                    embed = discord.Embed(
                        title=f"Application Status for {target.display_name}",
                        color=discord.Color.blue()
                    )
                    
                    status_messages = {
                        "pending": "🟡 **Pending** - Your application is under review (you're in #enlistment)",
                        "approved": "🟢 **Approved** - Your application has been approved! (you have full access)",
                        "rejected": "🔴 **Rejected** - Your application was not approved",
                        "none": "⚪ **No Application** - No application found (you're in #dmz)"
                    }
                    
                    embed.add_field(
                        name="Status",
                        value=status_messages.get(status, f"❓ **Unknown** - {status}"),
                        inline=False
                    )
                    
                    if submission_date:
                        embed.add_field(
                            name="Submitted",
                            value=submission_date,
                            inline=True
                        )
                    
                    if status == "none":
                        embed.add_field(
                            name="Next Steps",
                            value="[Submit an application here](https://zerolivesleft.net/apply/)",
                            inline=False
                        )
                    elif status == "rejected":
                        embed.add_field(
                            name="Next Steps",
                            value="You may submit a new application when ready.",
                            inline=False
                        )
                    
                    await ctx.send(embed=embed, ephemeral=True)
                    
                elif resp.status == 404:
                    embed = discord.Embed(
                        title=f"Application Status for {target.display_name}",
                        description="⚪ **No Application Found** - You're in the DMZ\n\n[Submit an application here](https://zerolivesleft.net/apply/)",
                        color=discord.Color.light_grey()
                    )
                    await ctx.send(embed=embed, ephemeral=True)
                    
                else:
                    await ctx.send(f"Error checking application status: {resp.status}", ephemeral=True)
                    
        except Exception as e:
            log.error(f"Error checking application status for {target.name}: {e}")
            await ctx.send("An error occurred while checking application status.", ephemeral=True)

    # Configuration methods
    async def cache_all_invites(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try: 
                self.guild_invites[guild.id] = await guild.invites()
                log.info(f"Cached {len(self.guild_invites[guild.id])} invites for guild {guild.name}")
            except (discord.Forbidden, discord.HTTPException) as e: 
                log.error(f"Failed to cache invites for guild {guild.name}: {e}")
    
    async def on_invite_create(self, invite):
        if invite.guild.id not in self.guild_invites: 
            self.guild_invites[invite.guild.id] = []
        self.guild_invites[invite.guild.id].append(invite)
        log.info(f"New invite created and cached for {invite.guild.name}: {invite.code}")

    def stop_tasks(self):
        if hasattr(self, 'cache_invites_task') and self.cache_invites_task and not self.cache_invites_task.done():
            self.cache_invites_task.cancel()
            log.info("Invite cache task cancelled")

    async def set_api_url(self, ctx, url):
        await self.config.ar_api_url.set(url)
        await ctx.send(f"API URL set to: {url}")
        log.info(f"API URL set to: {url}")
    
    async def set_api_key(self, ctx, key):
        await self.config.ar_api_key.set(key)
        await ctx.send("API key set successfully.")
        log.info(f"API key updated")

    async def toggle_enabled(self, ctx, enabled: bool):
        await self.config.ar_enabled.set(enabled)
        await ctx.send(f"Application role assignment is now {'enabled' if enabled else 'disabled'}.")
        log.info(f"Application role assignment {'enabled' if enabled else 'disabled'}")

    async def set_pending_role(self, ctx, role: discord.Role):
        await self.config.ar_pending_role_id.set(role.id)
        await ctx.send(f"Pending role has been set to **{role.name}** (for #enlistment).")
        log.info(f"Pending role set to {role.name} ({role.id})")

    async def set_member_role(self, ctx, role: discord.Role):
        await self.config.ar_member_role_id.set(role.id)
        await ctx.send(f"Main member role has been set to **{role.name}**.")
        log.info(f"Member role set to {role.name} ({role.id})")
        
    async def set_unverified_role(self, ctx, role: discord.Role):
        await self.config.ar_unverified_role_id.set(role.id)
        await ctx.send(f"Unverified role has been set to **{role.name}** (for #dmz).")
        log.info(f"Unverified role set to {role.name} ({role.id})")

    async def set_welcome_channel(self, ctx, channel: discord.TextChannel):
        """Sets the channel for approved member welcome messages (main server access)"""
        # Check if this is the same as the DMZ channel
        unverified_channel_id = await self.config.ar_unverified_channel_id()
        if unverified_channel_id and channel.id == int(unverified_channel_id):
            await ctx.send("⚠️ **Warning:** You're setting the welcome channel to the same channel as the DMZ. "
                          "Approved members won't be able to see welcome messages in the DMZ channel. "
                          "Consider using a different channel that approved members can access (like #general or #welcome).")
        
        await self.config.ar_welcome_channel_id.set(channel.id)
        await ctx.send(f"Welcome channel (for approved members with full access) set to {channel.mention}.")
        log.info(f"Welcome channel set to {channel.name} ({channel.id})")

    async def set_unverified_channel(self, ctx, channel: discord.TextChannel):
        """Sets the channel where new members with the Unverified role receive their welcome (#dmz)"""
        # Check if this is the same as the welcome channel
        welcome_channel_id = await self.config.ar_welcome_channel_id()
        if welcome_channel_id and channel.id == int(welcome_channel_id):
            await ctx.send("⚠️ **Warning:** You're setting the DMZ channel to the same channel as the welcome channel. "
                          "This means approved members will receive welcome messages in a channel they can't see. "
                          "Consider using different channels for DMZ and welcome messages.")
        
        await self.config.ar_unverified_channel_id.set(channel.id)
        await ctx.send(f"**Unverified** welcome channel set to {channel.mention} (#dmz). New unverified members will now receive messages here.")
        log.info(f"Unverified channel (#dmz) set to {channel.name} ({channel.id})")

    async def set_pending_channel(self, ctx, channel: discord.TextChannel):
        """Sets the channel where members with a Pending application role receive their updates (#enlistment)"""
        await self.config.ar_pending_channel_id.set(channel.id)
        await ctx.send(f"**Pending** application channel set to {channel.mention} (#enlistment). Members with pending applications will now receive messages here.")
        log.info(f"Pending channel (#enlistment) set to {channel.name} ({channel.id})")

    async def set_notifications_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel for application status notifications"""
        await self.config.ar_notifications_channel_id.set(channel.id)
        await ctx.send(f"Application notifications channel set to {channel.mention}")
        log.info(f"Notifications channel set to {channel.name} ({channel.id})")

    async def set_welcome_message(self, ctx: commands.Context, *, message: str):
        """Sets the generic welcome message (legacy)"""
        await self.config.ar_welcome_message.set(message)
        await ctx.send(f"Generic welcome message has been set to:\n\n{message}")
        log.info(f"Generic welcome message updated")
    
    async def add_region_role(self, ctx, region: str, role: discord.Role):
        async with self.config.ar_region_roles() as region_roles:
            region_roles[region.upper()] = str(role.id)
        await ctx.send(f"Added region role mapping: `{region.upper()}` -> {role.name}")
        log.info(f"Added region role mapping: {region.upper()} -> {role.name} ({role.id})")
    
    async def remove_region_role(self, ctx, region: str):
        async with self.config.ar_region_roles() as region_roles:
            if region.upper() in region_roles:
                del region_roles[region.upper()]
                await ctx.send(f"Removed region role mapping for `{region.upper()}`")
                log.info(f"Removed region role mapping for {region.upper()}")
            else:
                await ctx.send(f"No region role mapping found for `{region.upper()}`.")
                log.info(f"Attempted to remove non-existent region mapping: {region.upper()}")
    
    async def list_region_roles(self, ctx):
        region_roles = await self.config.ar_region_roles()
        if not region_roles: 
            await ctx.send("No region role mappings configured.")
            return
            
        embed = discord.Embed(title="Region Role Mappings", color=discord.Color.blue())
        for region, role_id in region_roles.items():
            role = ctx.guild.get_role(int(role_id))
            embed.add_field(name=region, value=role.mention if role else f"Unknown Role ({role_id})", inline=False)
        await ctx.send(embed=embed)
        log.info(f"Listed {len(region_roles)} region role mappings")
    
    async def show_config(self, ctx: commands.Context):
        all_config = await self.config.all()
        ar_config = {k: v for k, v in all_config.items() if k.startswith("ar_")}
        embed = discord.Embed(title="Application Roles Configuration", color=await ctx.embed_color())
        
        # Add helpful channel descriptions
        channel_descriptions = {
            "ar_unverified_channel_id": "Unverified Channel (#dmz)",
            "ar_pending_channel_id": "Pending Channel (#enlistment)",
            "ar_welcome_channel_id": "Welcome Channel (approved members)",
            "ar_notifications_channel_id": "Notifications Channel",
            "ar_moderator_channel_id": "Moderator Notifications Channel"
        }
        
        for key, value in ar_config.items():
            name = channel_descriptions.get(key, key.replace("ar_", "").replace("_", " ").title())
            value_str = ""
            if value is None:
                value_str = "`Not Set`"
            elif "role_id" in key:
                if ctx.guild:
                    role = ctx.guild.get_role(int(value))
                    value_str = role.mention if role else f"<@&{value}> (Not found: `{value}`)"
                else:
                    value_str = f"`{value}`"
            elif "channel_id" in key:
                if ctx.guild:
                    channel = ctx.guild.get_channel(int(value))
                    value_str = channel.mention if channel else f"<#{value}> (Not found: `{value}`)"
                else:
                    value_str = f"`{value}`"
            elif key == "ar_region_roles":
                if value:
                    mapped_roles = []
                    for k, v in value.items():
                        role = ctx.guild.get_role(int(v)) if ctx.guild else None
                        mapped_roles.append(f"`{k}`: {role.mention if role else f'Unknown ({v})'}")
                    value_str = "\n".join(mapped_roles)
                else:
                    value_str = "None"
            elif "api_key" in key:
                value_str = "`Set`" if value else "`Not Set`"
            elif key == "ar_member_history":
                if value:
                    total_users = sum(len(guild_data) for guild_data in value.values())
                    value_str = f"`{total_users} users tracked`"
                else:
                    value_str = "`No history tracked`"
            elif key == "ar_ping_online_only":
                value_str = f"`{value}`"
            elif key == "ar_admin_panel_base_url":
                value_str = f"`{value}`"
            else:
                value_str = f"`{value}`"
            
            embed.add_field(name=name, value=value_str, inline=False)
        
        # Add moderator notifications summary
        moderator_channel_id = await self.config.ar_moderator_channel_id()
        moderator_role_id = await self.config.ar_moderator_role_id()
        ping_online_only = await self.config.ar_ping_online_only()
        admin_panel_base_url = await self.config.ar_admin_panel_base_url()
        
        embed.add_field(
            name="Moderator Notifications",
            value=(
                f"Channel: {ctx.guild.get_channel(int(moderator_channel_id)).mention if moderator_channel_id and ctx.guild and ctx.guild.get_channel(int(moderator_channel_id)) else '`Not Set`'}\n"
                f"Role: {ctx.guild.get_role(int(moderator_role_id)).mention if moderator_role_id and ctx.guild and ctx.guild.get_role(int(moderator_role_id)) else '`Not Set`'}\n"
                f"Online Only: `{ping_online_only}`\n"
                f"Admin Panel: `{admin_panel_base_url}`"
            ),
            inline=False
        )
        
        await ctx.send(embed=embed)
        log.info("Configuration displayed")

    async def force_cache_invites(self, ctx):
        await ctx.send("Refreshing invite cache...")
        await self.cache_all_invites()
        await ctx.send("Invite cache refreshed.")
        log.info("Invite cache manually refreshed")
    
    async def set_default_guild(self, ctx, guild: discord.Guild):
        await self.config.ar_default_guild_id.set(str(guild.id))
        await ctx.send(f"Default guild set to: {guild.name}")
        log.info(f"Default guild set to {guild.name} ({guild.id})")
    
    async def set_invite_channel(self, ctx, channel: discord.TextChannel):
        if not channel.permissions_for(ctx.guild.me).create_instant_invite:
            await ctx.send(f"I don't have permission to create invites in {channel.mention}")
            log.warning(f"Missing invite creation permission in {channel.name}")
            return
        await self.config.ar_invite_channel_id.set(str(channel.id))
        await ctx.send(f"Invite channel set to: {channel.mention}")
        log.info(f"Invite channel set to {channel.name} ({channel.id})")

    # Testing and debugging methods
    async def test_member_flow(self, ctx, member: discord.Member, test_status: str):
        """Test command to simulate different application statuses for a member"""
        if not ctx.author.guild_permissions.manage_roles:
            await ctx.send("You need manage roles permission to use this command.")
            return
            
        test_app_data = {
            "region": "US",
            "platform_role_ids": [],
            "game_role_ids": []
        }
        
        guild = member.guild
        
        # Update the test flow to also track history
        await ctx.send(f"Testing {member.mention} with status: {test_status}")
        
        # For testing, we need to simulate the join tracking
        is_returning = await self._track_member_join(guild.id, member.id)
        await ctx.send(f"Join tracked. Member is returning: {is_returning}")
        
        # First remove any existing test roles to start clean
        unverified_role_id = await self.config.ar_unverified_role_id()
        pending_role_id = await self.config.ar_pending_role_id()
        member_role_id = await self.config.ar_member_role_id()
        
        unverified_role = guild.get_role(int(unverified_role_id)) if unverified_role_id else None
        pending_role = guild.get_role(int(pending_role_id)) if pending_role_id else None
        member_role = guild.get_role(int(member_role_id)) if member_role_id else None
        
        # Remove all test roles first
        roles_to_remove = []
        for role in [unverified_role, pending_role, member_role]:
            if role and role in member.roles:
                roles_to_remove.append(role)
        
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="Test flow - clearing roles")
            await ctx.send(f"Cleared existing roles: {[r.name for r in roles_to_remove]}")
        
        # Now simulate the correct status by actually assigning the role first
        if test_status == "none" or test_status == "rejected":
            # Assign unverified role for none/rejected status
            if unverified_role:
                await member.add_roles(unverified_role, reason="Test flow - unverified")
                await ctx.send(f"Assigned {unverified_role.name} role for testing")
            await self._handle_