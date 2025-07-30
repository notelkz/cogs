async def setup_dual_system(self, ctx):
        """Set up the dual progression system with your server's roles."""
        recruit_role_id = 1358140362367041827  # Your Recruit role
        private_role_id = 1274274605435060224  # Your Private role
        
        recruit_role = ctx.guild.get_role(recruit_role_id)
        private_role = ctx.guild.get_role(private_role_id)
        
        if not recruit_role:
            return await ctx.send(f"❌ Recruit role not found (ID: {recruit_role_id})")
        if not private_role:
            return await ctx.send(f"❌ Private role not found (ID: {private_role_id})")
        
        # Ask user what the Member role ID is
        await ctx.send(
            "🤔 **What is your Member role?**\n"
            "Please mention the role that users get when they complete their 24-hour community membership requirement.\n"
            "Example: `@Member` or provide the role ID"
        )
        
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel
        
        try:
            response = await ctx.bot.wait_for('message', check=check, timeout=60.0)
            
            # Try to extract role from mention or ID
            member_role = None
            if response.role_mentions:
                member_role = response.role_mentions[0]
            else:
                # Try to parse as role ID
                try:
                    role_id = int(response.content.strip())
                    member_role = ctx.guild.get_role(role_id)
                except ValueError:
                    return await ctx.send("❌ Invalid role. Please mention a role or provide a valid role ID.")
            
            if not member_role:
                return await ctx.send("❌ Could not find that role. Please try again.")
            
        except asyncio.TimeoutError:
            return await ctx.send("⏰ Timed out waiting for Member role. Please try the command again.")
        
        # Ask for additional base roles (Veteran, The Old Guard)
        await ctx.send(
            "🎖️ **Additional Base Roles (Optional)**\n"
            "Do you have Veteran and/or The Old Guard roles? Please mention them or type 'none' to skip.\n"
            "These roles will also allow XP earning. Example: `@Veteran @The Old Guard` or `none`"
        )
        
        additional_base_roles = []
        try:
            response2 = await ctx.bot.wait_for('message', check=check, timeout=60.0)
            
            if response2.content.lower().strip() != 'none':
                if response2.role_mentions:
                    additional_base_roles = response2.role_mentions
                else:
                    # Try to parse role IDs separated by spaces
                    role_ids = response2.content.strip().split()
                    for role_id_str in role_ids:
                        try:
                            role_id = int(role_id_str)
                            role = ctx.guild.get_role(role_id)
                            if role:
                                additional_base_roles.append(role)
                        except ValueError:
                            continue
        
        except asyncio.TimeoutError:
            await ctx.send("⏰ Timed out, proceeding without additional base roles.")
        
        # Set up both tracks and base roles
        await self.config.guild(ctx.guild).at_recruit_role_id.set(recruit_role_id)
        await self.config.guild(ctx.guild).at_member_role_id.set(member_role.id)
        await self.config.guild(ctx.guild).at_member_threshold_hours.set(24)
        await self.config.guild(ctx.guild).at_military_start_hours.set(12)
        
        # Configure base roles for XP earning
        base_role_ids = [recruit_role_id, member_role.id]
        for role in additional_base_roles:
            base_role_ids.append(role.id)
        
        await self.config.guild(ctx.guild).at_base_role_ids.set(base_role_ids)
        
        embed = discord.Embed(
            title="⚖️ Dual Progression System Setup Complete",
            color=discord.Color.green()
        )
        embed.add_field(
            name="🏘️ Community Track",
            value=(
                f"{recruit_role.mention} → {member_role.mention}\n"
                f"• **24 hours** total activity\n"
                f"• Removes {recruit_role.mention}\n"
                f"• Adds {member_role.mention}\n"
                f"• **Permanent membership upgrade**"
            ),
            inline=False
        )
        embed.add_field(
            name="🎖️ Military Track", 
            value=(
                f"{recruit_role.mention} → {private_role.mention} → Higher Ranks\n"
                f"• **12 hours** to start military progression\n"
                f"• XP-based rank progression\n"
                f"• Keeps {recruit_role.mention} until 24 hours\n"
                f"• Can have both community + military roles"
            ),
            inline=False
        )
        
        # Build base roles display
        base_roles_display = [recruit_role.mention, member_role.mention]
        for role in additional_base_roles:
            base_roles_display.append(role.mention)
        
        embed.add_field(
            name="🎯 XP Earning Requirements",
            value=(
                f"**Users must have one of these roles to earn XP:**\n"
                f"{', '.join(base_roles_display)}\n\n"
                f"*Users without these roles cannot earn XP from any activity.*"
            ),
            inline=False
        )
        
        embed.add_field(
            name="📅 Example Timeline",
            value=(
                f"• **0-12 hrs**: Just {recruit_role.mention} (earning XP)\n"
                f"• **12-24 hrs**: {recruit_role.mention} + {private_role.mention} (dual roles!)\n"
                f"• **24+ hrs**: {member_role.mention} + {private_role.mention}+ (community upgrade)"
            ),
            inline=False
        )
        embed.set_footer(text="XP earning is now restricted to members with base community roles!")
        
        await ctx.send(embed=embed)# zerolivesleft/activity_tracking.py
# XP-Based Activity Tracking System with Prestige

import discord
import asyncio
import aiohttp
import os
import json
import time
from datetime import datetime, timedelta

from redbot.core import commands, Config
from aiohttp import web
from redbot.core.utils.chat_formatting import humanize_list
from redbot.core.utils.views import ConfirmView

import logging

log = logging.getLogger("red.Elkz.zerolivesleft.activity_tracking")

class ActivityTrackingLogic:
    """
    XP-based activity tracking system with prestige support.
    Tracks voice minutes for website display and awards XP for various activities.
    """

    def __init__(self, cog_instance):
        self.cog = cog_instance
        self.config = cog_instance.config
        self.session = cog_instance.session
        self.voice_tracking = {}
        self.message_cooldowns = {}  # user_id: last_message_time
        self.role_check_task = None
        self.activity_update_task = None
        
        # Register XP system config
        default_guild = {
            # Legacy voice tracking (for website compatibility)
            "at_user_activity": {},
            "at_recruit_role_id": None,
            "at_member_role_id": None,  # This may be handled by another cog
            "at_promotion_threshold_hours": 10,  # Legacy, not used in dual system
            "at_promotion_channel_id": None,
            "at_military_ranks": [],
            "at_user_message_count": {},
            "at_api_url": None,
            "at_api_key": None,
            "at_promotion_update_url": None,
            "at_military_rank_update_url": None,
            
            # NEW: XP System configs
            "at_user_xp": {},
            "at_user_prestige": {},
            "at_promotion_threshold_xp": 1000,  # Legacy, not used in dual system
            "at_voice_xp_rate": 1,
            "at_message_xp": 3,
            "at_reaction_xp": 1,
            "at_voice_join_xp": 5,
            "at_message_cooldown": 60,
            "at_prestige_enabled": False,
            "at_prestige_multiplier": 0.5,
            
            # NEW: Dual progression system
            "at_member_threshold_hours": 24,  # Hours needed for Recruit → Member
            "at_military_start_hours": 12,   # Hours needed to start military progression
            
            # NEW: Base role requirements for XP earning
            "at_base_role_ids": [],  # List of role IDs that allow XP earning
        }
        
        self.config.register_guild(**default_guild)

    def start_tasks(self):
        """Starts periodic tasks for role checking and activity updates."""
        self.cog.bot.loop.create_task(self._setup_periodic_tasks())

    def stop_tasks(self):
        """Stops all periodic tasks."""
        if self.role_check_task and not self.role_check_task.done():
            self.role_check_task.cancel()
        if self.activity_update_task and not self.activity_update_task.done():
            self.activity_update_task.cancel()
    
        # Save voice activity for users currently being tracked
        for guild_id, members_tracking in self.voice_tracking.items():
            guild = self.cog.bot.get_guild(guild_id)
            if guild:
                for member_id, join_time in list(members_tracking.items()):
                    member = guild.get_member(member_id)
                    if member:
                        duration_minutes = (datetime.utcnow() - join_time).total_seconds() / 60
                        if duration_minutes >= 1:
                            log.info(f"ActivityTracking: Unloading: Logging {duration_minutes:.2f} minutes for {member.name} due to cog unload.")
                            asyncio.create_task(self._update_user_voice_minutes(guild, member, int(duration_minutes)))
        self.voice_tracking.clear()

    async def _migrate_existing_users(self, guild):
        """One-time migration to estimate message counts for existing users"""
        migration_key = f"at_message_count_migrated_{guild.id}"
        
        if await self.config.custom("migrations", migration_key)():
            return  # Already migrated
        
        # Get all users with XP
        user_data = await self.config.guild(guild).at_user_data()
        message_xp = await self.config.guild(guild).at_message_xp()
        
        if message_xp > 0:
            async with self.config.guild(guild).at_user_message_count() as user_message_count:
                for user_id, data in user_data.items():
                    if user_id not in user_message_count:
                        # Rough estimate: total_xp / message_xp
                        estimated_messages = data.get("xp", 0) // message_xp
                        user_message_count[user_id] = estimated_messages
                        log.info(f"ActivityTracking: Migrated {estimated_messages} estimated messages for user {user_id}")
        
        await self.config.custom("migrations", migration_key).set(True)
        log.info(f"ActivityTracking: Message count migration completed for guild {guild.id}")

    # --- XP SYSTEM CORE ---
    
    async def _check_base_role_eligibility(self, guild, member):
        """Check if user has a required base role for XP earning."""
        base_role_ids = await self.config.guild(guild).at_base_role_ids()
        
        if not base_role_ids:
            # If no base roles configured, everyone can earn XP
            return True
        
        # Check if user has any of the required base roles
        user_role_ids = {role.id for role in member.roles}
        return any(role_id in user_role_ids for role_id in base_role_ids)
    
    async def _add_xp(self, guild, member, xp_amount, source="unknown"):
        """Add XP to a user and check for promotions."""
        # Check if user is eligible for XP earning
        if not await self._check_base_role_eligibility(guild, member):
            log.debug(f"ActivityTracking: {member.name} not eligible for XP (missing base role)")
            return
        
        async with self.config.guild(guild).at_user_xp() as user_xp:
            uid = str(member.id)
            old_xp = user_xp.get(uid, 0)
            user_xp[uid] = old_xp + xp_amount
            new_xp = user_xp[uid]
            log.info(f"ActivityTracking: Added {xp_amount} XP to {member.name} from {source}. Total: {new_xp}")
        
        # Sync XP to website
        asyncio.create_task(self._update_website_xp(guild, member, new_xp))
        
        # Check for promotions
        await self._check_for_promotion(guild, member, new_xp)

    async def _get_user_xp(self, guild, user_id):
        """Get total XP for a user."""
        user_xp = await self.config.guild(guild).at_user_xp()
        return user_xp.get(str(user_id), 0)

    async def _get_user_prestige(self, guild, user_id):
        """Get prestige level for a user."""
        user_prestige = await self.config.guild(guild).at_user_prestige()
        return user_prestige.get(str(user_id), 0)

    async def _prestige_user(self, guild, member):
        """Prestige a user (reset XP, increase prestige level, keep voice minutes)."""
        async with self.config.guild(guild).at_user_prestige() as user_prestige:
            uid = str(member.id)
            old_prestige = user_prestige.get(uid, 0)
            user_prestige[uid] = old_prestige + 1
            new_prestige = user_prestige[uid]
        
        # Reset XP but keep voice minutes for website
        async with self.config.guild(guild).at_user_xp() as user_xp:
            user_xp[str(member.id)] = 0
        
        # Remove all military rank roles
        military_ranks = await self.config.guild(guild).at_military_ranks()
        roles_to_remove = []
        for role_obj in member.roles:
            if any(str(role_obj.id) == str(rank.get('discord_role_id')) for rank in military_ranks):
                roles_to_remove.append(role_obj)
        
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="Prestige reset")
        
        # Notify website
        await self._update_website_prestige(guild, member, new_prestige)
        
        # Send prestige notification
        channel_id = await self.config.guild(guild).at_promotion_channel_id()
        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel:
                await channel.send(
                    f"🌟 **PRESTIGE!** {member.mention} has achieved **Prestige {new_prestige}** and started their journey anew! 🌟"
                )
        
        log.info(f"ActivityTracking: {member.name} prestiged to level {new_prestige}")

    # --- VOICE TRACKING (for website data) ---

    async def _update_user_voice_minutes(self, guild, member, minutes_to_add):
        """Update voice minutes (for website) and award XP."""
        # Update voice minutes for website
        async with self.config.guild(guild).at_user_activity() as user_activity:
            uid = str(member.id)
            user_activity[uid] = user_activity.get(uid, 0) + minutes_to_add
            log.info(f"ActivityTracking: Updated voice minutes for {member.name}: added {minutes_to_add}, new total: {user_activity[uid]}")
        
        # Award XP for voice activity
        voice_xp_rate = await self.config.guild(guild).at_voice_xp_rate()
        if voice_xp_rate > 0:
            xp_to_award = minutes_to_add * voice_xp_rate
            await self._add_xp(guild, member, xp_to_award, "voice_activity")
        
        # Sync voice minutes to website
        total_minutes_for_website = await self._get_user_voice_minutes(guild, member.id)
        asyncio.create_task(self._update_website_activity(guild, member, total_minutes_for_website))

    async def _get_user_voice_minutes(self, guild, user_id):
        """Get total voice minutes for a user."""
        user_activity = await self.config.guild(guild).at_user_activity()
        total_minutes = user_activity.get(str(user_id), 0)
        guild_id = guild.id
        if guild_id in self.voice_tracking and user_id in self.voice_tracking[guild_id]:
            join_time = self.voice_tracking[guild_id][user_id]
            current_session_minutes = int((datetime.utcnow() - join_time).total_seconds() / 60)
            if current_session_minutes >= 1:
                total_minutes += current_session_minutes
                log.debug(f"ActivityTracking: Added {current_session_minutes} minutes from current session for user {user_id}")
        return total_minutes

    # --- MESSAGE XP TRACKING ---

    async def handle_message(self, message):
    if message.author.bot or not message.guild:
        return
    
    guild = message.guild
    member = message.author
    user_id = member.id
    current_time = time.time()
    
    # NEW: Always count the message (no cooldown for counting)
    async with self.config.guild(guild).at_user_message_count() as user_message_count:
        uid = str(user_id)
        user_message_count[uid] = user_message_count.get(uid, 0) + 1
        new_count = user_message_count[uid]
        log.debug(f"ActivityTracking: Message count for {member.name}: {new_count}")
    
    # XP award (with cooldown)
    message_cooldown = await self.config.guild(guild).at_message_cooldown()
    if user_id in self.message_cooldowns:
        if current_time - self.message_cooldowns[user_id] < message_cooldown:
            return  # Still on cooldown for XP, but message was still counted
    
    self.message_cooldowns[user_id] = current_time
    
    # Award XP for message
    message_xp = await self.config.guild(guild).at_message_xp()
    if message_xp > 0:
        await self._add_xp(guild, member, message_xp, "message")

    async def handle_reaction_add(self, reaction, user):
        """Handle reaction add for XP awards."""
        if user.bot or not reaction.message.guild:
            return
        
        guild = reaction.message.guild
        member = guild.get_member(user.id)
        if not member:
            return
        
        # Award XP for giving reaction
        reaction_xp = await self.config.guild(guild).at_reaction_xp()
        if reaction_xp > 0:
            await self._add_xp(guild, member, reaction_xp, "reaction_given")

    # --- WEBSITE SYNC (API Calls) ---

    async def _update_website_xp(self, guild, member, total_xp):
        """Send XP updates to the Django website."""
        guild_settings = await self.config.guild(guild).all()
        api_url = guild_settings.get("at_api_url")
        api_key = guild_settings.get("at_api_key")
        
        if not api_url or not api_key:
            return
        
        endpoint = f"{api_url}update-xp/"
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        
        prestige_level = await self._get_user_prestige(guild, member.id)
        payload = {
            "discord_id": str(member.id),
            "xp": total_xp,
            "prestige_level": prestige_level
        }
        
        try:
            async with self.session.post(endpoint, headers=headers, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    log.info(f"ActivityTracking: Successfully synced XP for user {member.id}: {total_xp} XP, Prestige {prestige_level}")
                else:
                    error_text = await resp.text()
                    log.error(f"ActivityTracking: Failed to update XP: {resp.status} - {error_text}")
        except Exception as e:
            log.error(f"ActivityTracking: Exception updating website XP: {str(e)}")

    async def _update_website_prestige(self, guild, member, prestige_level):
        """Send prestige updates to the Django website."""
        guild_settings = await self.config.guild(guild).all()
        api_url = guild_settings.get("at_api_url")
        api_key = guild_settings.get("at_api_key")
        
        if not api_url or not api_key:
            return
        
        endpoint = f"{api_url}update-prestige/"
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {
            "discord_id": str(member.id),
            "prestige_level": prestige_level
        }
        
        try:
            async with self.session.post(endpoint, headers=headers, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    log.info(f"ActivityTracking: Successfully updated prestige for user {member.id}: {prestige_level}")
                else:
                    error_text = await resp.text()
                    log.error(f"ActivityTracking: Failed to update prestige: {resp.status} - {error_text}")
        except Exception as e:
            log.error(f"ActivityTracking: Exception updating prestige: {str(e)}")

    # Find this in your activity_tracking.py around line 445 and replace the entire function:

    async def _update_website_activity(self, guild, member, total_minutes_to_send):
        """Send voice activity and message count updates to the Django website."""
        guild_settings = await self.config.guild(guild).all()
        api_url = guild_settings.get("at_api_url")
        api_key = guild_settings.get("at_api_key")
        
        if not api_url or not api_key: 
            return
        
        # Get message count for this user from XP system
        message_count = await self._get_user_message_count(guild, member.id)
        
        endpoint = f"{api_url}update-activity/"
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {
            "discord_id": str(member.id), 
            "voice_minutes": total_minutes_to_send,
            "message_count": message_count
        }
        
        try:
            async with self.session.post(endpoint, headers=headers, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    log.info(f"ActivityTracking: Successfully synced activity for user {member.id}: {total_minutes_to_send} voice minutes, {message_count} messages")
                else:
                    error_text = await resp.text()
                    log.error(f"ActivityTracking: Failed to update activity: {resp.status} - {error_text}")
        except Exception as e:
            log.error(f"ActivityTracking: Exception updating website activity: {str(e)}")

    async def _get_user_message_count(self, guild, user_id):
        """Get total message count for a user from XP system data."""
        try:
            # Get message count from your XP tracking - you'll need to track this
            # For now, we can estimate based on XP if you don't have direct message tracking
            user_xp = await self.config.guild(guild).at_user_xp()
            total_xp = user_xp.get(str(user_id), 0)
            
            # Get XP rates
            message_xp = await self.config.guild(guild).at_message_xp()
            voice_xp_rate = await self.config.guild(guild).at_voice_xp_rate()
            reaction_xp = await self.config.guild(guild).at_reaction_xp()
            voice_join_xp = await self.config.guild(guild).at_voice_join_xp()
            
            # Get voice minutes for this user
            voice_minutes = await self._get_user_voice_minutes(guild, user_id)
            voice_xp_earned = voice_minutes * voice_xp_rate
            
            # Estimate message count (this is rough - ideally you'd track messages directly)
            if message_xp > 0:
                # Assume most remaining XP comes from messages (rough estimate)
                remaining_xp = max(0, total_xp - voice_xp_earned)
                estimated_message_count = remaining_xp // message_xp
                return max(0, estimated_message_count)
            
            return 0
        except Exception as e:
            log.error(f"ActivityTracking: Error getting message count for user {user_id}: {e}")
            return 0

    async def _notify_website_of_promotion(self, guild, discord_id, new_role_name):
        """Notify the website of a community role promotion."""
        guild_settings = await self.config.guild(guild).all()
        promotion_update_url_config = guild_settings.get("at_promotion_update_url")
        api_url = guild_settings.get("at_api_url")
        api_key = guild_settings.get("at_api_key")
        
        if not api_key: 
            return
        
        if promotion_update_url_config:
            endpoint = promotion_update_url_config
        elif api_url:
            endpoint = f"{api_url}update-role/"
        else:
            return

        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {"discord_id": str(discord_id), "new_role": new_role_name}
        
        try:
            async with self.session.post(endpoint, headers=headers, json=payload, timeout=5) as resp:
                if resp.status == 200:
                    log.info(f"ActivityTracking: Successfully notified website of promotion for {discord_id} to {new_role_name}")
        except Exception as e:
            log.error(f"ActivityTracking: Exception notifying website of promotion: {str(e)}")

    async def _notify_website_of_military_rank(self, guild, discord_id, rank_name):
        """Notify the website of a military rank update."""
        guild_settings = await self.config.guild(guild).all()
        military_rank_update_url_config = guild_settings.get("at_military_rank_update_url")
        api_url = guild_settings.get("at_api_url")
        api_key = guild_settings.get("at_api_key")
        
        if not api_key: 
            return
        
        if military_rank_update_url_config:
            endpoint = military_rank_update_url_config
        elif api_url:
            endpoint = f"{api_url}update-military-rank/"
        else:
            return
        
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {"discord_id": str(discord_id), "rank_name": rank_name}
        
        try:
            async with self.session.post(endpoint, headers=headers, json=payload, timeout=5) as resp:
                if resp.status == 200:
                    log.info(f"ActivityTracking: Successfully updated military rank for {discord_id} to {rank_name}")
        except Exception as e:
            log.error(f"ActivityTracking: Exception updating military rank: {str(e)}")

    # --- PERIODIC TASKS ---

    async def _setup_periodic_tasks(self):
        """Sets up periodic tasks for role checking and activity updates."""
        await self.cog.bot.wait_until_ready()
        
        guild_id_from_config = await self.cog.config.gc_counting_guild_id()
        guild_id_env_str = os.environ.get("DISCORD_GUILD_ID")

        guild_id = None
        if guild_id_from_config:
            guild_id = guild_id_from_config
        elif guild_id_env_str:
            try:
                guild_id = int(guild_id_env_str)
            except ValueError:
                log.error("ActivityTracking: DISCORD_GUILD_ID environment variable is not a valid integer.")
                return
        else:
            log.error("ActivityTracking: Main guild ID not set in config or environment.")
            return

        guild = self.cog.bot.get_guild(guild_id)
        if not guild:
            log.error(f"ActivityTracking: Guild with ID {guild_id} not found.")
            return

        if self.role_check_task is None or self.role_check_task.done():
            self.role_check_task = self.cog.bot.loop.create_task(self._schedule_periodic_role_check_loop(guild_id))
        
        if self.activity_update_task is None or self.activity_update_task.done():
            self.activity_update_task = self.cog.bot.loop.create_task(self._schedule_periodic_activity_updates_loop(guild_id))

    async def _schedule_periodic_role_check_loop(self, guild_id: int):
        """Schedules the periodic role check to run every 24 hours."""
        while True:
            try:
                log.info(f"ActivityTracking: Running scheduled role check for guild ID: {guild_id}")
                await self._periodic_role_check(guild_id)
                log.info(f"ActivityTracking: Completed scheduled role check for guild ID: {guild_id}")
            except asyncio.CancelledError:
                log.info("ActivityTracking: _schedule_periodic_role_check_loop cancelled.")
                break
            except Exception as e:
                log.exception(f"ActivityTracking: An error occurred during the scheduled role check: {e}")
            await asyncio.sleep(86400)

    async def _schedule_periodic_activity_updates_loop(self, guild_id: int):
        """Schedules periodic updates of activity for users currently in voice channels."""
        while True:
            try:
                await self._update_active_voice_users(guild_id)
            except asyncio.CancelledError:
                log.info("ActivityTracking: _schedule_periodic_activity_updates_loop cancelled.")
                break
            except Exception as e:
                log.exception(f"ActivityTracking: An error occurred during the periodic activity update: {e}")
            await asyncio.sleep(300)

    async def _update_active_voice_users(self, guild_id: int):
        """Updates activity for users currently in voice channels."""
        guild = self.cog.bot.get_guild(guild_id)
        if not guild:
            return
        
        updates_sent = 0
        
        for voice_channel in guild.voice_channels:
            for member in voice_channel.members:
                if member.bot:
                    continue
                
                if guild_id in self.voice_tracking and member.id in self.voice_tracking[guild_id]:
                    join_time = self.voice_tracking[guild_id][member.id]
                    current_time = datetime.utcnow()
                    
                    minutes_since_join = int((current_time - join_time).total_seconds() / 60)
                    
                    if minutes_since_join >= 5:
                        await self._update_user_voice_minutes(guild, member, minutes_since_join)
                        self.voice_tracking[guild_id][member.id] = current_time
                        updates_sent += 1
        
        log.info(f"ActivityTracking: Periodic activity update complete. Sent {updates_sent} updates.")

    async def _periodic_role_check(self, guild_id: int):
        """Performs a periodic check of all guild members' roles based on their XP."""
        guild = self.cog.bot.get_guild(guild_id)
        if not guild:
            return

        members_checked = 0
        promotions_made = 0

        try:
            async for member in guild.fetch_members(limit=None):
                if member.bot:
                    continue
                
                members_checked += 1
                total_xp = await self._get_user_xp(guild, member.id)

                initial_roles = {r.id for r in member.roles}
                await self._check_for_promotion(guild, member, total_xp)
                final_roles = {r.id for r in member.roles}

                if initial_roles != final_roles:
                    promotions_made += 1
                
                await asyncio.sleep(0.1)

        except Exception as e:
            log.exception(f"ActivityTracking: Error during periodic role check: {e}")

        log.info(f"ActivityTracking: Periodic role check complete. Checked {members_checked} members, made {promotions_made} role changes.")

    # --- PROMOTION LOGIC (XP-based) ---

    async def _check_for_promotion(self, guild, member, total_xp):
        """Check for promotions based on XP."""
        # Recruit -> Member (XP-based)
        recruit_role_id = await self.config.guild(guild).at_recruit_role_id()
        member_role_id = await self.config.guild(guild).at_member_role_id()
        threshold_xp = await self.config.guild(guild).at_promotion_threshold_xp()
        
        if recruit_role_id and member_role_id and threshold_xp:
            recruit_role = guild.get_role(recruit_role_id)
            member_role = guild.get_role(member_role_id)
            if recruit_role and member_role and recruit_role in member.roles:
                if total_xp >= threshold_xp:
                    await member.remove_roles(recruit_role, reason="XP Promotion")
                    await member.add_roles(member_role, reason="XP Promotion")
                    await self._notify_website_of_promotion(guild, member.id, "member")
                    channel_id = await self.config.guild(guild).at_promotion_channel_id()
                    if channel_id:
                        channel = guild.get_channel(channel_id)
                        if channel:
                            await channel.send(
                                f"🎉 Congratulations {member.mention}! You've been promoted to **{member_role.name}** status!"
                            )

        # Military Ranks (XP-based with prestige support)
        await self._check_military_rank_promotion(guild, member, total_xp)

    async def _check_military_rank_promotion(self, guild, member, total_xp):
        """Check for military rank promotions with prestige support."""
        military_ranks = await self.config.guild(guild).at_military_ranks()
        if not military_ranks:
            return
        
        prestige_level = await self._get_user_prestige(guild, member.id)
        prestige_enabled = await self.config.guild(guild).at_prestige_enabled()
        
        # Calculate XP requirements with prestige multiplier
        prestige_multiplier = await self.config.guild(guild).at_prestige_multiplier()
        xp_multiplier = 1 + (prestige_level * prestige_multiplier) if prestige_enabled else 1
        
        # Find eligible ranks
        eligible_ranks = []
        for rank_data in military_ranks:
            required_xp = rank_data.get('required_xp', 0)
            adjusted_required_xp = int(required_xp * xp_multiplier)
            if total_xp >= adjusted_required_xp:
                eligible_ranks.append((rank_data, adjusted_required_xp))
        
        if not eligible_ranks:
            return
        
        # Get highest eligible rank
        current_highest_eligible_rank = max(eligible_ranks, key=lambda x: x[1])[0]
        
        # Check for prestige eligibility
        max_rank = max(military_ranks, key=lambda x: x.get('required_xp', 0))
        max_required_xp = int(max_rank.get('required_xp', 0) * xp_multiplier)
        
        if prestige_enabled and total_xp >= max_required_xp * 1.5:  # 150% of max rank for prestige
            # Offer prestige
            await self._offer_prestige(guild, member)
            return
        
        # Normal rank promotion logic
        user_current_military_role_ids = {
            r.id for r in member.roles 
            if any(str(r.id) == str(rank_data.get('discord_role_id')) for rank_data in military_ranks)
        }

        target_role_id = int(current_highest_eligible_rank['discord_role_id'])
        target_rank_name = current_highest_eligible_rank['name']
        
        if target_role_id not in user_current_military_role_ids:
            # Remove old military ranks
            roles_to_remove = []
            all_military_role_ids = {int(r['discord_role_id']) for r in military_ranks if 'discord_role_id' in r}
            
            for role_obj in member.roles:
                if role_obj.id in all_military_role_ids:
                    roles_to_remove.append(role_obj)
            
            # Add new rank
            add_role_obj = guild.get_role(target_role_id)
            
            try:
                if roles_to_remove:
                    await member.remove_roles(*roles_to_remove, reason="Military rank promotion")
                if add_role_obj:
                    await member.add_roles(add_role_obj, reason="Military rank promotion")
                
                await self._notify_website_of_military_rank(guild, member.id, target_rank_name)

                channel_id = await self.config.guild(guild).at_promotion_channel_id()
                if channel_id:
                    channel = guild.get_channel(channel_id)
                    if channel:
                        prestige_text = f" (Prestige {prestige_level})" if prestige_level > 0 else ""
                        await channel.send(
                            f"🎖️ Bravo, {member.mention}! You've achieved the rank of **{target_rank_name}**{prestige_text}!"
                        )
            except Exception as e:
                log.exception(f"ActivityTracking: Error during military rank promotion: {e}")

    async def _offer_prestige(self, guild, member):
        """Offer prestige to a user who has reached maximum rank."""
        channel_id = await self.config.guild(guild).at_promotion_channel_id()
        if not channel_id:
            return
        
        channel = guild.get_channel(channel_id)
        if not channel:
            return
        
        current_prestige = await self._get_user_prestige(guild, member.id)
        
        embed = discord.Embed(
            title="🌟 PRESTIGE AVAILABLE! 🌟",
            description=f"{member.mention}, you've reached the pinnacle of military ranks!",
            color=discord.Color.gold()
        )
        embed.add_field(
            name="Prestige Benefits",
            value=(
                f"• Become **Prestige {current_prestige + 1}**\n"
                f"• Reset XP and start the journey again\n"
                f"• Keep all voice activity records\n"
                f"• Higher XP requirements but more prestigious ranks\n"
                f"• Special prestige recognition"
            ),
            inline=False
        )
        embed.add_field(
            name="How to Prestige",
            value=f"Use `!prestige` to prestige (this will reset your XP!)",
            inline=False
        )
        
        await channel.send(embed=embed)

    # --- VOICE STATE HANDLER ---

    async def handle_voice_state_update(self, member, before, after):
        """Handle voice state updates for tracking."""
        if member.bot:
            return
        
        guild = member.guild
        guild_id = guild.id
        user_id = member.id
        
        if guild_id not in self.voice_tracking:
            self.voice_tracking[guild_id] = {}
        
        # User JOINS a voice channel
        if before.channel is None and after.channel is not None:
            self.voice_tracking[guild_id][user_id] = datetime.utcnow()
            log.info(f"ActivityTracking: {member.name} joined voice channel {after.channel.name}")
            
            # Award bonus XP for joining voice
            voice_join_xp = await self.config.guild(guild).at_voice_join_xp()
            if voice_join_xp > 0:
                await self._add_xp(guild, member, voice_join_xp, "voice_join")
        
        # User LEAVES a voice channel
        elif before.channel is not None and after.channel is None:
            if user_id in self.voice_tracking[guild_id]:
                join_time = self.voice_tracking[guild_id].pop(user_id)
                duration = datetime.utcnow() - join_time
                minutes = duration.total_seconds() / 60
                
                log.info(f"ActivityTracking: {member.name} left voice channel {before.channel.name}. Duration: {minutes:.2f} minutes.")
                
                if minutes >= 1:
                    await self._update_user_voice_minutes(guild, member, int(minutes))

    def _generate_progress_bar(self, percent, length=10):
        """Generate a progress bar."""
        filled_length = int(length * percent / 100)
        bar = '█' * filled_length + '░' * (length - filled_length)
        return f"[{bar}]"

    # --- COMMANDS ---

    async def setup_default_ranks(self, ctx):
        """Set up your server's military ranks with XP scaling."""
        # Your actual server ranks with role IDs and suggested XP requirements
        server_ranks = [
            ("Private", 1274274605435060224, 100),
            ("Private First Class", 1274274696048934965, 200),
            ("Corporal", 1274771534119964813, 350),
            ("Specialist", 1274771654907658402, 550),
            ("Sergeant", 1274771991748022276, 800),
            ("Staff Sergeant", 1274772130424164384, 1100),
            ("Sergeant First Class", 1274772191107485706, 1450),
            ("Master Sergeant", 1274772252545519708, 1850),
            ("First Sergeant", 1274772335689465978, 2300),
            ("Sergeant Major", 1274772419927605299, 2800),
            ("Command Sergeant Major", 1274772500164640830, 3350),
            ("Sergeant Major of the Army", 1274772595031539787, 3950),
            ("Warrant Officer 1", 1358212838631407797, 4600),
            ("Chief Warrant Officer 2", 1358213159583875172, 5300),
            ("Chief Warrant Officer 3", 1358213229112852721, 6050),
            ("Chief Warrant Officer 4", 1358213408704430150, 6850),
            ("Chief Warrant Officer 5", 1358213451289460847, 7700),
            ("Second Lieutenant", 1358213662216814784, 8600),
            ("First Lieutenant", 1358213759805554979, 9550),
            ("Captain", 1358213809466118276, 10550),
            ("Major", 1358213810598449163, 11600),
            ("Lieutenant Colonel", 1358213812175503430, 12700),
            ("Colonel", 1358213813140459520, 13850),
            ("Brigadier General", 1358213814234906786, 15050),
            ("Major General", 1358213815203795004, 16300),
            ("Lieutenant General", 1358213817229770783, 17600),
            ("General", 1358213815983935608, 18950),
            ("General of the Army", 1358213816617275483, 20350)
        ]
        
        # Check which roles exist and add them to config
        existing_ranks = []
        missing_roles = []
        
        for rank_name, role_id, xp_req in server_ranks:
            role = ctx.guild.get_role(role_id)
            if role:
                existing_ranks.append((rank_name, role_id, xp_req))
            else:
                missing_roles.append(f"{rank_name} (ID: {role_id})")
        
        if missing_roles:
            missing_list = "\n".join(missing_roles[:10])
            if len(missing_roles) > 10:
                missing_list += f"\n... and {len(missing_roles) - 10} more"
            
            await ctx.send(
                f"⚠️ **Missing Roles Found**\n"
                f"The following roles don't exist in your server:\n```\n{missing_list}\n```\n"
                f"**Found {len(existing_ranks)} valid roles out of {len(server_ranks)} total.**\n\n"
                f"Continue with existing roles only?"
            )
        
        # Add existing ranks to config
        if existing_ranks:
            async with self.config.guild(ctx.guild).at_military_ranks() as ranks:
                # Clear existing ranks first
                ranks.clear()
                
                for rank_name, role_id, xp_req in existing_ranks:
                    ranks.append({
                        "name": rank_name,
                        "discord_role_id": str(role_id),
                        "required_xp": xp_req
                    })
                
                # Already sorted by XP in the list above
            
            embed = discord.Embed(
                title="🎖️ Military Ranks Setup Complete",
                color=discord.Color.green()
            )
            embed.add_field(
                name="Results",
                value=f"✅ Configured **{len(existing_ranks)}** ranks\n❌ Missing **{len(missing_roles)}** roles",
                inline=False
            )
            embed.add_field(
                name="XP Range",
                value=f"**{existing_ranks[0][2]:,} XP** (Private) → **{existing_ranks[-1][2]:,} XP** (General of the Army)",
                inline=False
            )
            embed.add_field(
                name="Next Steps",
                value=(
                    f"• Set XP rates: `{ctx.prefix}zll setxprates 1 3 1 5`\n"
                    f"• Set recruit promotion: `{ctx.prefix}zll setrecruitxp <@recruit_role> <@private_role> 100`\n"
                    f"• Enable prestige: `{ctx.prefix}zll prestige enable true 0.5`"
                ),
                inline=False
            )
            
            await ctx.send(embed=embed)
        
        if not existing_ranks:
            await ctx.send("❌ No valid roles found. Please check that the roles exist in your server.")

    async def create_default_rank_roles(self, ctx):
        """This command is not needed since you already have your military ranks created."""
        await ctx.send(
            "ℹ️ **Role Creation Not Needed**\n"
            "Your server already has a complete military rank structure!\n\n"
            f"Use `{ctx.prefix}zll xp setupranks` to configure your existing 29 military ranks with XP requirements.\n\n"
            "**Your Current Rank Structure:**\n"
            "• **Enlisted Ranks**: Private → Sergeant Major of the Army (12 ranks)\n"
            "• **Warrant Officers**: WO1 → CW5 (5 ranks)\n"
            "• **Officers**: 2nd Lieutenant → General of the Army (12 ranks)\n\n"
            "Total: **29 ranks** with realistic military progression!"
        )

    async def setup_dual_system(self, ctx):
        """Set up the dual progression system with your server's roles."""
        recruit_role_id = 1358140362367041827  # Your Recruit role
        private_role_id = 1274274605435060224  # Your Private role
        
        recruit_role = ctx.guild.get_role(recruit_role_id)
        private_role = ctx.guild.get_role(private_role_id)
        
        if not recruit_role:
            return await ctx.send(f"❌ Recruit role not found (ID: {recruit_role_id})")
        if not private_role:
            return await ctx.send(f"❌ Private role not found (ID: {private_role_id})")
        
        # Ask user what the Member role ID is
        await ctx.send(
            "🤔 **What is your Member role?**\n"
            "Please mention the role that users get when they complete their 24-hour community membership requirement.\n"
            "Example: `@Member` or provide the role ID"
        )
        
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel
        
        try:
            response = await ctx.bot.wait_for('message', check=check, timeout=60.0)
            
            # Try to extract role from mention or ID
            member_role = None
            if response.role_mentions:
                member_role = response.role_mentions[0]
            else:
                # Try to parse as role ID
                try:
                    role_id = int(response.content.strip())
                    member_role = ctx.guild.get_role(role_id)
                except ValueError:
                    return await ctx.send("❌ Invalid role. Please mention a role or provide a valid role ID.")
            
            if not member_role:
                return await ctx.send("❌ Could not find that role. Please try again.")
            
        except asyncio.TimeoutError:
            return await ctx.send("⏰ Timed out waiting for Member role. Please try the command again.")
        
        # Set up both tracks
        await self.config.guild(ctx.guild).at_recruit_role_id.set(recruit_role_id)
        await self.config.guild(ctx.guild).at_member_role_id.set(member_role.id)
        await self.config.guild(ctx.guild).at_member_threshold_hours.set(24)
        await self.config.guild(ctx.guild).at_military_start_hours.set(12)
        
        embed = discord.Embed(
            title="⚖️ Dual Progression System Setup Complete",
            color=discord.Color.green()
        )
        embed.add_field(
            name="🏘️ Community Track",
            value=(
                f"{recruit_role.mention} → {member_role.mention}\n"
                f"• **24 hours** total activity\n"
                f"• Removes {recruit_role.mention}\n"
                f"• Adds {member_role.mention}\n"
                f"• **Permanent membership upgrade**"
            ),
            inline=False
        )
        embed.add_field(
            name="🎖️ Military Track", 
            value=(
                f"{recruit_role.mention} → {private_role.mention} → Higher Ranks\n"
                f"• **12 hours** to start military progression\n"
                f"• XP-based rank progression\n"
                f"• Keeps {recruit_role.mention} until 24 hours\n"
                f"• Can have both community + military roles"
            ),
            inline=False
        )
        embed.add_field(
            name="📅 Example Timeline",
            value=(
                f"• **0-12 hrs**: Just {recruit_role.mention}\n"
                f"• **12-24 hrs**: {recruit_role.mention} + {private_role.mention} (dual roles!)\n"
                f"• **24+ hrs**: {member_role.mention} + {private_role.mention}+ (community upgrade)"
            ),
            inline=False
        )
        embed.set_footer(text="Both promotion tracks are now managed by this cog!")
        
        await ctx.send(embed=embed)

    async def setup_recruit_system(self, ctx):
        """Set up the recruit to private promotion system."""
        recruit_role_id = 1358140362367041827  # Your Recruit role
        private_role_id = 1274274605435060224  # Your Private role
        
        recruit_role = ctx.guild.get_role(recruit_role_id)
        private_role = ctx.guild.get_role(private_role_id)
        
        if not recruit_role:
            return await ctx.send(f"❌ Recruit role not found (ID: {recruit_role_id})")
        if not private_role:
            return await ctx.send(f"❌ Private role not found (ID: {private_role_id})")
        
        # Set default recruit to private promotion at 100 XP
        await self.config.guild(ctx.guild).at_recruit_role_id.set(recruit_role_id)
        await self.config.guild(ctx.guild).at_member_role_id.set(private_role_id)
        await self.config.guild(ctx.guild).at_promotion_threshold_xp.set(100)
        
        embed = discord.Embed(
            title="👥 Recruit System Setup Complete",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="Configuration",
            value=(
                f"Recruit Role: {recruit_role.mention}\n"
                f"Private Role: {private_role.mention}\n"
                f"Required XP: **100 XP**"
            ),
            inline=False
        )
        embed.add_field(
            name="How it works",
            value=(
                "• New members get the Recruit role\n"
                "• After earning 100 XP, they auto-promote to Private\n"
                "• Private is the starting military rank for the progression system"
            ),
            inline=False
        )
        
        await ctx.send(embed=embed)

    async def set_xp_rates(self, ctx, voice_per_minute: int = 1, message_xp: int = 3, reaction_xp: int = 1, voice_join_bonus: int = 5):
        """Set XP rates for different activities."""
        await self.config.guild(ctx.guild).at_voice_xp_rate.set(voice_per_minute)
        await self.config.guild(ctx.guild).at_message_xp.set(message_xp)
        await self.config.guild(ctx.guild).at_reaction_xp.set(reaction_xp)
        await self.config.guild(ctx.guild).at_voice_join_xp.set(voice_join_bonus)
        
        embed = discord.Embed(title="🎯 XP Rates Updated", color=discord.Color.blue())
        embed.add_field(
            name="New Rates",
            value=(
                f"Voice Activity: **{voice_per_minute} XP** per minute\n"
                f"Messages: **{message_xp} XP** per message\n"
                f"Reactions: **{reaction_xp} XP** per reaction\n"
                f"Voice Join Bonus: **{voice_join_bonus} XP**"
            ),
            inline=False
        )
        await ctx.send(embed=embed)

    async def set_message_cooldown(self, ctx, seconds: int):
        """Set cooldown between message XP awards."""
        if seconds < 0:
            return await ctx.send("Cooldown cannot be negative.")
        
        await self.config.guild(ctx.guild).at_message_cooldown.set(seconds)
        await ctx.send(f"Message XP cooldown set to {seconds} seconds.")

    async def setup_prestige(self, ctx, enabled: bool = True, multiplier: float = 0.5):
        """Enable/disable prestige system and set XP multiplier."""
        await self.config.guild(ctx.guild).at_prestige_enabled.set(enabled)
        await self.config.guild(ctx.guild).at_prestige_multiplier.set(multiplier)
        
        if enabled:
            await ctx.send(
                f"✅ **Prestige System Enabled**\n"
                f"XP Multiplier: **{multiplier}** per prestige level\n"
                f"Example: Prestige 1 = {1 + multiplier}x XP requirements"
            )
        else:
            await ctx.send("❌ **Prestige System Disabled**")

    async def prestige_command(self, ctx):
        """Allow a user to prestige if eligible."""
        member = ctx.author
        guild = ctx.guild
        
        prestige_enabled = await self.config.guild(guild).at_prestige_enabled()
        if not prestige_enabled:
            return await ctx.send("❌ Prestige system is not enabled on this server.")
        
        military_ranks = await self.config.guild(guild).at_military_ranks()
        if not military_ranks:
            return await ctx.send("❌ No military ranks configured.")
        
        total_xp = await self._get_user_xp(guild, member.id)
        prestige_level = await self._get_user_prestige(guild, member.id)
        
        # Calculate if eligible for prestige
        prestige_multiplier = await self.config.guild(guild).at_prestige_multiplier()
        xp_multiplier = 1 + (prestige_level * prestige_multiplier)
        
        max_rank = max(military_ranks, key=lambda x: x.get('required_xp', 0))
        max_required_xp = int(max_rank.get('required_xp', 0) * xp_multiplier)
        prestige_threshold = max_required_xp * 1.5
        
        if total_xp < prestige_threshold:
            return await ctx.send(
                f"❌ You need **{prestige_threshold:,} XP** to prestige (you have {total_xp:,} XP).\n"
                f"Reach 150% of the highest rank requirement to unlock prestige."
            )
        
        # Confirmation
        embed = discord.Embed(
            title="🌟 Prestige Confirmation",
            description="Are you sure you want to prestige?",
            color=discord.Color.gold()
        )
        embed.add_field(
            name="What happens:",
            value=(
                f"• Your XP will reset to 0\n"
                f"• You'll become **Prestige {prestige_level + 1}**\n"
                f"• All military ranks will be removed\n"
                f"• Future XP requirements will be higher\n"
                f"• Voice activity records are kept"
            ),
            inline=False
        )
        embed.add_field(
            name="Current Stats:",
            value=f"XP: {total_xp:,}\nPrestige: {prestige_level}",
            inline=False
        )
        
        view = ConfirmView(ctx.author, disable_buttons=True)
        await ctx.send(embed=embed, view=view)
        await view.wait()
        
        if view.result:
            await self._prestige_user(guild, member)
            await ctx.send(f"🌟 **Congratulations!** You are now **Prestige {prestige_level + 1}**!")
        else:
            await ctx.send("Prestige cancelled.")

    async def add_xp_command(self, ctx, member: discord.Member, amount: int, reason: str = "Admin award"):
        """Manually add XP to a user."""
        if amount <= 0:
            return await ctx.send("XP amount must be positive.")
        
        await self._add_xp(ctx.guild, member, amount, f"admin_award:{reason}")
        
        total_xp = await self._get_user_xp(ctx.guild, member.id)
        await ctx.send(f"✅ Added **{amount:,} XP** to {member.mention}. Total: **{total_xp:,} XP**")

    async def set_recruit_member_xp(self, ctx, recruit_role: discord.Role, member_role: discord.Role, required_xp: int):
        """Set recruit/member roles and XP threshold."""
        await self.config.guild(ctx.guild).at_recruit_role_id.set(recruit_role.id)
        await self.config.guild(ctx.guild).at_member_role_id.set(member_role.id)
        await self.config.guild(ctx.guild).at_promotion_threshold_xp.set(required_xp)
        
        await ctx.send(
            f"✅ **Recruit/Member System Updated**\n"
            f"Recruit Role: {recruit_role.mention}\n"
            f"Member Role: {member_role.mention}\n"
            f"Required XP: **{required_xp:,}**"
        )

    async def roles(self, ctx, recruit: discord.Role, member: discord.Role):
        """Set the Recruit and Member roles (legacy command - redirects to XP version)."""
        await ctx.send(
            f"ℹ️ **Note:** This system now uses XP instead of hours!\n"
            f"Use `{ctx.prefix}zll setrecruitxp {recruit.mention} {member.mention} <xp_amount>` instead.\n"
            f"Setting roles with default 1000 XP requirement..."
        )
        await self.set_recruit_member_xp(ctx, recruit, member, 1000)

    async def threshold(self, ctx, xp_amount: int):
        """Set XP threshold for recruit to member promotion."""
        if xp_amount <= 0:
            return await ctx.send("XP threshold must be positive.")
        
        await self.config.guild(ctx.guild).at_promotion_threshold_xp.set(xp_amount)
        await ctx.send(f"✅ Promotion threshold set to **{xp_amount:,} XP**.")

    async def set_api(self, ctx, url: str, key: str):
        """Set the base API URL and the API Key for the website."""
        if not url.startswith("http://") and not url.startswith("https://"):
            return await ctx.send("API URL must start with http:// or https://")
        if not url.endswith("/"):
            url += "/"
        
        await self.config.guild(ctx.guild).at_api_url.set(url)
        await self.config.guild(ctx.guild).at_api_key.set(key)
        await ctx.send("✅ API URL and Key have been saved.")

    async def set_promotion_url(self, ctx, url: str):
        """Set the full URL for community role promotions."""
        if not url.startswith("http://") and not url.startswith("https://"):
            return await ctx.send("URL must start with http:// or https://")
        await self.config.guild(ctx.guild).at_promotion_update_url.set(url)
        await ctx.send("✅ Community role promotion URL set.")

    async def set_military_rank_url(self, ctx, url: str):
        """Set the full URL for military rank updates."""
        if not url.startswith("http://") and not url.startswith("https://"):
            return await ctx.send("URL must start with http:// or https://")
        await self.config.guild(ctx.guild).at_military_rank_update_url.set(url)
        await ctx.send("✅ Military rank update URL set.")

    async def set_promotion_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel for promotion notifications."""
        await self.config.guild(ctx.guild).at_promotion_channel_id.set(channel.id)
        await ctx.send(f"✅ Promotion notification channel set to {channel.mention}.")

    async def add_rank(self, ctx, role: discord.Role, required_xp: int):
        """Add a new military rank with XP requirement."""
        if required_xp < 0:
            return await ctx.send("Required XP cannot be negative.")
        
        async with self.config.guild(ctx.guild).at_military_ranks() as ranks:
            for r in ranks:
                if str(r.get("discord_role_id")) == str(role.id):
                    return await ctx.send(f"A rank for role '{role.name}' already exists.")
                if r.get("name").lower() == role.name.lower():
                    return await ctx.send(f"A rank with name '{role.name}' already exists.")
            
            ranks.append({
                "name": role.name,
                "discord_role_id": str(role.id),
                "required_xp": required_xp
            })
            ranks.sort(key=lambda r: r['required_xp'])
        
        await ctx.send(f"✅ Added military rank: **{role.name}** at **{required_xp:,} XP**.")

    async def remove_rank(self, ctx, role_or_name: str):
        """Remove a military rank by role ID or name."""
        async with self.config.guild(ctx.guild).at_military_ranks() as ranks:
            initial_len = len(ranks)
            ranks[:] = [r for r in ranks if str(r.get('discord_role_id')) != role_or_name and r.get('name').lower() != role_or_name.lower()]
            
            if len(ranks) < initial_len:
                await ctx.send(f"✅ Removed military rank matching '{role_or_name}'.")
            else:
                await ctx.send(f"❌ No military rank found matching '{role_or_name}'.")

    async def clear_ranks(self, ctx):
        """Clear all configured military ranks."""
        view = ConfirmView(ctx.author, disable_buttons=True)
        await ctx.send(
            "⚠️ Are you sure you want to clear ALL configured military ranks? This cannot be undone.",
            view=view
        )
        await view.wait()
        if view.result:
            await self.config.guild(ctx.guild).at_military_ranks.set([])
            await ctx.send("✅ All military ranks have been cleared.")
        else:
            await ctx.send("Operation cancelled.")

    async def list_ranks(self, ctx):
        """List all configured military ranks."""
        ranks = await self.config.guild(ctx.guild).at_military_ranks()
        if not ranks:
            await ctx.send("❌ No military ranks have been set.")
            return
        
        embed = discord.Embed(title="🎖️ Military Ranks", color=discord.Color.blue())
        
        sorted_ranks = sorted(ranks, key=lambda r: r['required_xp'])
        rank_text = ""
        
        for i, r in enumerate(sorted_ranks):
            role_obj = ctx.guild.get_role(int(r['discord_role_id'])) if r.get('discord_role_id') else None
            role_mention = role_obj.mention if role_obj else f"❌ Missing Role"
            rank_text += f"{i+1}. **{r['name']}** - {r['required_xp']:,} XP - {role_mention}\n"
        
        embed.description = rank_text
        await ctx.send(embed=embed)

    async def show_config_command(self, ctx):
        """Shows the current XP ActivityTracker configuration."""
        settings = await self.config.guild(ctx.guild).all()
        
        embed = discord.Embed(
            title="🎯 XP ActivityTracker Settings",
            color=discord.Color.blue()
        )
        
        # API Configuration
        api_url = settings.get("at_api_url")
        api_key = settings.get("at_api_key")
        promotion_url = settings.get("at_promotion_update_url")
        military_rank_url = settings.get("at_military_rank_update_url")
        
        embed.add_field(
            name="🌐 API Configuration",
            value=(
                f"Base URL: `{api_url or 'Not set'}`\n"
                f"API Key: `{'✅ Set' if api_key else '❌ Not set'}`\n"
                f"Promotion URL: `{promotion_url or 'Uses base URL'}`\n"
                f"Military Rank URL: `{military_rank_url or 'Uses base URL'}`"
            ),
            inline=False
        )
        
        # XP Rates
        voice_xp = settings.get("at_voice_xp_rate", 1)
        message_xp = settings.get("at_message_xp", 3)
        reaction_xp = settings.get("at_reaction_xp", 1)
        voice_join_xp = settings.get("at_voice_join_xp", 5)
        message_cooldown = settings.get("at_message_cooldown", 60)
        
        embed.add_field(
            name="🎯 XP Rates",
            value=(
                f"Voice: **{voice_xp}** XP/minute\n"
                f"Messages: **{message_xp}** XP (cooldown: {message_cooldown}s)\n"
                f"Reactions: **{reaction_xp}** XP\n"
                f"Voice Join: **{voice_join_xp}** XP bonus"
            ),
            inline=True
        )
        
        # Role Configuration
        recruit_role_id = settings.get("at_recruit_role_id")
        member_role_id = settings.get("at_member_role_id")
        threshold_xp = settings.get("at_promotion_threshold_xp", 0)
        
        recruit_role = ctx.guild.get_role(recruit_role_id) if recruit_role_id else None
        member_role = ctx.guild.get_role(member_role_id) if member_role_id else None
        
        embed.add_field(
            name="👥 Role Configuration",
            value=(
                f"Recruit: {recruit_role.mention if recruit_role else '`Not set`'}\n"
                f"Member: {member_role.mention if member_role else '`Not set`'}\n"
                f"Required XP: **{threshold_xp:,}**"
            ),
            inline=True
        )
        
        # Prestige System
        prestige_enabled = settings.get("at_prestige_enabled", False)
        prestige_multiplier = settings.get("at_prestige_multiplier", 0.5)
        
        embed.add_field(
            name="🌟 Prestige System",
            value=(
                f"Status: {'✅ Enabled' if prestige_enabled else '❌ Disabled'}\n"
                f"XP Multiplier: **{prestige_multiplier}** per prestige\n"
                f"Example P1: **{1 + prestige_multiplier}x** XP requirements"
            ),
            inline=False
        )
        
        # Notification Settings
        channel_id = settings.get("at_promotion_channel_id")
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        
        embed.add_field(
            name="📢 Notifications",
            value=f"Channel: {channel.mention if channel else '`Not set`'}",
            inline=True
        )
        
        # Military Ranks Summary
        military_ranks = settings.get("at_military_ranks", [])
        valid_ranks = [r for r in military_ranks if 'discord_role_id' in r and ctx.guild.get_role(int(r['discord_role_id']))]
        
        embed.add_field(
            name="🎖️ Military Ranks",
            value=(
                f"Configured: **{len(military_ranks)}**\n"
                f"Valid: **{len(valid_ranks)}**\n"
                f"Use `{ctx.prefix}zll listranks` for details"
            ),
            inline=True
        )
        
        await ctx.send(embed=embed)

    async def debug_info(self, ctx):
        """Shows debug information about the XP ActivityTracker cog."""
        embed = discord.Embed(
            title="🔧 XP ActivityTracker Debug Info",
            color=discord.Color.gold()
        )
        
        # Voice Tracking
        total_tracked = sum(len(members) for members in self.voice_tracking.values())
        embed.add_field(
            name="📊 Voice Tracking",
            value=f"Currently tracking: **{total_tracked}** users",
            inline=False
        )
        
        # Message Cooldowns
        active_cooldowns = len([cd for cd in self.message_cooldowns.values() if time.time() - cd < 300])
        embed.add_field(
            name="💬 Message System",
            value=f"Active cooldowns: **{active_cooldowns}** users",
            inline=True
        )
        
        # Task Status
        role_task_status = "Running" if self.role_check_task and not self.role_check_task.done() else "Stopped"
        activity_task_status = "Running" if self.activity_update_task and not self.activity_update_task.done() else "Stopped"
        
        embed.add_field(
            name="⚙️ Background Tasks",
            value=f"Role Check: **{role_task_status}**\nActivity Update: **{activity_task_status}**",
            inline=True
        )
        
        # Environment
        main_guild_id = os.environ.get("DISCORD_GUILD_ID", "Not set")
        embed.add_field(
            name="🌍 Environment",
            value=f"Main Guild ID: `{main_guild_id}`",
            inline=False
        )
        
        embed.set_footer(text=f"XP ActivityTracker | Discord.py {discord.__version__}")
        await ctx.send(embed=embed)

    async def force_sync(self, ctx):
        """Force a sync of all active voice users."""
        guild = ctx.guild
        guild_id = guild.id
        
        updates_sent = 0
        
        status_msg = await ctx.send("🔄 Starting forced sync of all active voice users...")
        
        for voice_channel in guild.voice_channels:
            for member in voice_channel.members:
                if member.bot:
                    continue
                
                if guild_id in self.voice_tracking and member.id in self.voice_tracking[guild_id]:
                    join_time = self.voice_tracking[guild_id][member.id]
                    current_time = datetime.utcnow()
                    
                    minutes_since_join = int((current_time - join_time).total_seconds() / 60)
                    
                    if minutes_since_join >= 1:
                        await self._update_user_voice_minutes(guild, member, minutes_since_join)
                        self.voice_tracking[guild_id][member.id] = current_time
                        updates_sent += 1
        
        await status_msg.edit(content=f"✅ Forced sync complete. Sent **{updates_sent}** updates.")

    async def myvoicetime(self, ctx):
        """Shows your total accumulated voice time."""
        total_minutes = await self._get_user_voice_minutes(ctx.guild, ctx.author.id)
        hours = total_minutes // 60
        minutes = total_minutes % 60
        await ctx.send(f"🎤 Your total voice time: **{hours}** hours and **{minutes}** minutes.")

    async def myxp(self, ctx):
        """Shows your total XP and prestige level."""
        total_xp = await self._get_user_xp(ctx.guild, ctx.author.id)
        prestige_level = await self._get_user_prestige(ctx.guild, ctx.author.id)
        
        embed = discord.Embed(
            title=f"🎯 {ctx.author.display_name}'s XP",
            color=ctx.author.color
        )
        embed.add_field(
            name="Experience Points",
            value=f"**{total_xp:,} XP**",
            inline=True
        )
        
        if prestige_level > 0:
            embed.add_field(
                name="Prestige Level",
                value=f"🌟 **Prestige {prestige_level}**",
                inline=True
            )
        
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    async def status(self, ctx, member: discord.Member = None):
        """Show detailed XP status and progression for a user."""
        target = member or ctx.author
        total_xp = await self._get_user_xp(ctx.guild, target.id)
        total_minutes = await self._get_user_voice_minutes(ctx.guild, target.id)
        prestige_level = await self._get_user_prestige(ctx.guild, target.id)
        
        hours = total_minutes // 60
        minutes = total_minutes % 60
        
        embed = discord.Embed(
            title=f"📊 Activity Status - {target.display_name}",
            color=target.color
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        
        # XP and Activity
        xp_text = f"**{total_xp:,} XP**"
        if prestige_level > 0:
            xp_text += f"\n🌟 **Prestige {prestige_level}**"
        
        embed.add_field(
            name="💎 Experience Points",
            value=xp_text,
            inline=True
        )
        embed.add_field(
            name="🎤 Voice Activity",
            value=f"**{hours}h {minutes}m**",
            inline=True
        )
        
        # Recruit/Member Progress
        recruit_role_id = await self.config.guild(ctx.guild).at_recruit_role_id()
        member_role_id = await self.config.guild(ctx.guild).at_member_role_id()
        threshold_xp = await self.config.guild(ctx.guild).at_promotion_threshold_xp()

        if recruit_role_id and member_role_id and threshold_xp:
            recruit_role = ctx.guild.get_role(recruit_role_id)
            member_role = ctx.guild.get_role(member_role_id)
            if recruit_role and member_role:
                if member_role in target.roles:
                    embed.add_field(
                        name="👥 Membership Status",
                        value=f"✅ Full Member ({member_role.mention})",
                        inline=False
                    )
                elif recruit_role in target.roles:
                    progress = min(100, (total_xp / threshold_xp) * 100)
                    remaining_xp = max(0, threshold_xp - total_xp)
                    progress_bar = self._generate_progress_bar(progress)
                    embed.add_field(
                        name="👥 Membership Progress",
                        value=(
                            f"{recruit_role.mention} → {member_role.mention}\n"
                            f"{progress_bar} **{progress:.1f}%**\n"
                            f"Remaining: **{remaining_xp:,} XP**"
                        ),
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="👥 Membership Status",
                        value="Not in membership track (missing Recruit role)",
                        inline=False
                    )

        # Military Rank Progress
        military_ranks = await self.config.guild(ctx.guild).at_military_ranks()
        if military_ranks:
            try:
                prestige_multiplier = await self.config.guild(ctx.guild).at_prestige_multiplier()
                xp_multiplier = 1 + (prestige_level * prestige_multiplier) if prestige_level > 0 else 1
                
                sorted_ranks = sorted(
                    [r for r in military_ranks if isinstance(r.get('required_xp'), (int, float))],
                    key=lambda x: x['required_xp']
                )

                current_rank = None
                next_rank = None

                # Find current rank
                user_current_military_role_ids = {
                    role.id for role in target.roles
                    if any(str(role.id) == str(r.get('discord_role_id')) for r in military_ranks)
                }
                
                user_eligible_ranks_from_roles = [
                    r for r in military_ranks 
                    if int(r.get('discord_role_id', 0)) in user_current_military_role_ids
                ]

                if user_eligible_ranks_from_roles:
                    current_rank = max(user_eligible_ranks_from_roles, key=lambda x: x['required_xp'])
                
                # Find next rank
                if current_rank:
                    higher_ranks = [r for r in sorted_ranks if r['required_xp'] > current_rank['required_xp']]
                    if higher_ranks:
                        next_rank = min(higher_ranks, key=lambda x: x['required_xp'])
                else:
                    if sorted_ranks:
                        next_rank = sorted_ranks[0]

                # Current Rank Display
                if current_rank:
                    current_role_id = current_rank.get('discord_role_id')
                    current_role = ctx.guild.get_role(int(current_role_id)) if current_role_id else None
                    adjusted_req = int(current_rank.get('required_xp', 0) * xp_multiplier)
                    
                    rank_text = f"**{current_rank.get('name')}**\n"
                    if current_role:
                        rank_text += f"{current_role.mention}\n"
                    rank_text += f"Required: {adjusted_req:,} XP"
                    
                    if prestige_level > 0:
                        original_req = current_rank.get('required_xp', 0)
                        rank_text += f"\n(Base: {original_req:,} XP)"
                    
                    embed.add_field(
                        name="🎖️ Current Military Rank",
                        value=rank_text,
                        inline=True
                    )

                # Next Rank Progress
                if next_rank:
                    next_role_id = next_rank.get('discord_role_id')
                    next_role = ctx.guild.get_role(int(next_role_id)) if next_role_id else None
                    
                    progress_base_xp = int(current_rank.get('required_xp', 0) * xp_multiplier) if current_rank else 0
                    next_required_xp = int(next_rank['required_xp'] * xp_multiplier)
                    
                    if next_required_xp > progress_base_xp:
                        progress = min(100, ((total_xp - progress_base_xp) / (next_required_xp - progress_base_xp)) * 100)
                        remaining_xp = max(0, next_required_xp - total_xp)
                        progress_bar = self._generate_progress_bar(progress)
                        
                        next_text = f"**{next_rank.get('name')}**\n"
                        if next_role:
                            next_text += f"{next_role.mention}\n"
                        next_text += f"{progress_bar} **{progress:.1f}%**\n"
                        next_text += f"Remaining: **{remaining_xp:,} XP**"
                        
                        if prestige_level > 0:
                            original_req = next_rank['required_xp']
                            next_text += f"\n(Base: {original_req:,} XP)"
                        
                        embed.add_field(
                            name="⬆️ Next Military Rank",
                            value=next_text,
                            inline=True
                        )
                    else:
                        embed.add_field(
                            name="👑 Military Status",
                            value="You have reached the highest rank! 🎖️",
                            inline=True
                        )
                elif current_rank:
                    # Check for prestige eligibility
                    prestige_enabled = await self.config.guild(ctx.guild).at_prestige_enabled()
                    if prestige_enabled:
                        max_rank = max(military_ranks, key=lambda x: x.get('required_xp', 0))
                        max_required_xp = int(max_rank.get('required_xp', 0) * xp_multiplier)
                        prestige_threshold = max_required_xp * 1.5
                        
                        if total_xp >= prestige_threshold:
                            embed.add_field(
                                name="🌟 PRESTIGE AVAILABLE!",
                                value=f"You can prestige! Use `{ctx.prefix}prestige`",
                                inline=False
                            )
                        else:
                            remaining_for_prestige = prestige_threshold - total_xp
                            embed.add_field(
                                name="🌟 Prestige Progress",
                                value=f"**{remaining_for_prestige:,} XP** until prestige available",
                                inline=True
                            )
                    else:
                        embed.add_field(
                            name="👑 Military Status",
                            value="You have reached the highest rank! 🎖️",
                            inline=True
                        )
                else:
                    embed.add_field(
                        name="🎖️ Military Rank",
                        value="No military ranks earned yet",
                        inline=True
                    )
                    
            except Exception as e:
                log.error(f"Error processing military ranks for {target.display_name}: {e}", exc_info=True)
                embed.add_field(
                    name="❌ Military Rank Error",
                    value="An error occurred processing military ranks.",
                    inline=False
                )
        else:
            embed.add_field(
                name="🎖️ Military Rank",
                value="No military ranks configured for this server.",
                inline=False
            )
        
        await ctx.send(embed=embed)

    async def leaderboard(self, ctx, page: int = 1):
        """Show XP leaderboard for the server."""
        user_xp = await self.config.guild(ctx.guild).at_user_xp()
        user_prestige = await self.config.guild(ctx.guild).at_user_prestige()
        
        if not user_xp:
            return await ctx.send("❌ No XP data found for this server.")
        
        # Create leaderboard entries
        leaderboard_data = []
        for user_id, xp in user_xp.items():
            member = ctx.guild.get_member(int(user_id))
            if member and not member.bot:
                prestige = user_prestige.get(user_id, 0)
                # Calculate total "score" for sorting (prestige is worth a lot)
                total_score = xp + (prestige * 100000)  # Prestige is worth 100k XP for sorting
                leaderboard_data.append((member, xp, prestige, total_score))
        
        # Sort by total score (XP + prestige bonus)
        leaderboard_data.sort(key=lambda x: x[3], reverse=True)
        
        if not leaderboard_data:
            return await ctx.send("❌ No valid users found for leaderboard.")
        
        # Pagination
        per_page = 10
        total_pages = (len(leaderboard_data) + per_page - 1) // per_page
        page = max(1, min(page, total_pages))
        
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        page_data = leaderboard_data[start_idx:end_idx]
        
        embed = discord.Embed(
            title="🏆 XP Leaderboard",
            color=discord.Color.gold()
        )
        
        leaderboard_text = ""
        for i, (member, xp, prestige, _) in enumerate(page_data, start_idx + 1):
            # Medal emojis for top 3
            if i == 1:
                medal = "🥇"
            elif i == 2:
                medal = "🥈"
            elif i == 3:
                medal = "🥉"
            else:
                medal = f"**{i}.**"
            
            prestige_text = f" ⭐P{prestige}" if prestige > 0 else ""
            leaderboard_text += f"{medal} {member.display_name}: **{xp:,} XP**{prestige_text}\n"
        
        embed.description = leaderboard_text
        embed.set_footer(text=f"Page {page}/{total_pages} • {len(leaderboard_data)} total users")
        
        # Add user's position if not on current page
        user_position = next((i+1 for i, (member, _, _, _) in enumerate(leaderboard_data) if member.id == ctx.author.id), None)
        if user_position and not (start_idx < user_position <= end_idx):
            user_xp_val = user_xp.get(str(ctx.author.id), 0)
            user_prestige_val = user_prestige.get(str(ctx.author.id), 0)
            prestige_text = f" ⭐P{user_prestige_val}" if user_prestige_val > 0 else ""
            embed.add_field(
                name="Your Position",
                value=f"**#{user_position}** - {user_xp_val:,} XP{prestige_text}",
                inline=False
            )
        
        await ctx.send(embed=embed)

    async def reset_user_xp(self, ctx, member: discord.Member):
        """Reset a user's XP (admin only)."""
        view = ConfirmView(ctx.author, disable_buttons=True)
        await ctx.send(
            f"⚠️ Are you sure you want to reset **{member.display_name}'s** XP? This cannot be undone.",
            view=view
        )
        await view.wait()
        
        if not view.result:
            return await ctx.send("Operation cancelled.")
        
        # Reset XP but keep voice minutes and prestige
        async with self.config.guild(ctx.guild).at_user_xp() as user_xp:
            old_xp = user_xp.get(str(member.id), 0)
            user_xp[str(member.id)] = 0
        
        # Remove military ranks
        military_ranks = await self.config.guild(ctx.guild).at_military_ranks()
        roles_to_remove = []
        for role_obj in member.roles:
            if any(str(role_obj.id) == str(rank.get('discord_role_id')) for rank in military_ranks):
                roles_to_remove.append(role_obj)
        
        if roles_to_remove:
            try:
                await member.remove_roles(*roles_to_remove, reason=f"XP reset by {ctx.author}")
            except discord.Forbidden:
                await ctx.send("⚠️ XP reset but couldn't remove roles (missing permissions).")
        
        # Sync to website
        await self._update_website_xp(ctx.guild, member, 0)
        
        await ctx.send(f"✅ Reset **{member.display_name}'s** XP from {old_xp:,} to 0.")

    async def bulk_award_xp(self, ctx, xp_amount: int, role: discord.Role = None):
        """Award XP to all members or members with a specific role."""
        if xp_amount <= 0:
            return await ctx.send("XP amount must be positive.")
        
        target_members = []
        if role:
            target_members = [m for m in role.members if not m.bot]
            target_desc = f"members with role {role.mention}"
        else:
            target_members = [m for m in ctx.guild.members if not m.bot]
            target_desc = "all server members"
        
        if not target_members:
            return await ctx.send("❌ No valid target members found.")
        
        view = ConfirmView(ctx.author, disable_buttons=True)
        await ctx.send(
            f"⚠️ Award **{xp_amount:,} XP** to **{len(target_members)}** {target_desc}?",
            view=view
        )
        await view.wait()
        
        if not view.result:
            return await ctx.send("Operation cancelled.")
        
        status_msg = await ctx.send(f"🎯 Awarding XP to {len(target_members)} members...")
        
        awarded_count = 0
        for i, member in enumerate(target_members):
            try:
                await self._add_xp(ctx.guild, member, xp_amount, f"bulk_award_by_{ctx.author.id}")
                awarded_count += 1
                
                # Update status every 25 members
                if (i + 1) % 25 == 0:
                    await status_msg.edit(content=f"🎯 Awarding XP... {i + 1}/{len(target_members)}")
                
                # Small delay to prevent rate limits
                await asyncio.sleep(0.1)
                
            except Exception as e:
                log.error(f"Error awarding XP to {member.id}: {e}")
        
        await status_msg.edit(
            content=f"✅ **Bulk XP Award Complete**\n"
                   f"Awarded **{xp_amount:,} XP** to **{awarded_count}/{len(target_members)}** members."
        )