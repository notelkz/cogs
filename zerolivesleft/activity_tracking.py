# zerolivesleft/activity_tracking.py

import discord
import asyncio
import aiohttp
import os
import json
from datetime import datetime, timedelta

from redbot.core import commands, Config
from aiohttp import web # Still needed for web exceptions like HTTPInternalServerError
from redbot.core.utils.chat_formatting import humanize_list
from redbot.core.utils.views import ConfirmView

import logging

log = logging.getLogger("red.Elkz.zerolivesleft.activity_tracking")

class ActivityTrackingLogic:
    """
    Manages user voice activity tracking, Discord role promotions (Recruit/Member, Military Ranks),
    and sends updates to the Django website.
    """

    def __init__(self, cog_instance):
        self.cog = cog_instance # Reference to the main Zerolivesleft cog
        
        # Access central config and session
        self.config = cog_instance.config
        self.session = cog_instance.session

        self.voice_tracking = {}  # guild_id: {user_id: join_time (datetime.utcnow())}

        # Tasks will be started via start_tasks() method
        self.role_check_task = None
        self.activity_update_task = None

    def start_tasks(self):
        """Starts periodic tasks for role checking and activity updates."""
        # This cog's periodic tasks will be scheduled after bot is ready
        self.cog.bot.loop.create_task(self._setup_periodic_tasks())

    def stop_tasks(self):
        """Stops all periodic tasks."""
        if self.role_check_task and self.role_check_task.is_running():
            self.role_check_task.cancel()
        if self.activity_update_task and self.activity_update_task.is_running():
            self.activity_update_task.cancel()
        
        # Save voice time for users still in voice channels before unload
        for guild_id, members_tracking in self.voice_tracking.items():
            guild = self.cog.bot.get_guild(guild_id)
            if guild:
                for member_id, join_time in members_tracking.items():
                    member = guild.get_member(member_id)
                    if member:
                        duration_minutes = (datetime.utcnow() - join_time).total_seconds() / 60
                        if duration_minutes >= 1:
                            log.info(f"Unloading: Logging {duration_minutes:.2f} minutes for {member.name} due to cog unload.")
                            asyncio.create_task(self._update_user_voice_minutes(guild, member, int(duration_minutes)))
        self.voice_tracking.clear()

    async def _update_user_voice_minutes(self, guild, member, minutes_to_add):
        async with self.config.guild(guild).at_user_activity() as user_activity: # Use central config
            uid = str(member.id)
            user_activity[uid] = user_activity.get(uid, 0) + minutes_to_add
            log.info(f"ActivityTracking: Updated voice minutes for {member.name}: added {minutes_to_add}, new total: {user_activity[uid]}")
        
        total_minutes_for_website = await self._get_user_voice_minutes(guild, member.id)
        asyncio.create_task(self._update_website_activity(guild, member, total_minutes_for_website))
        
        total_minutes = await self._get_user_voice_minutes(guild, member.id)
        await self._check_for_promotion(guild, member, total_minutes)

    async def _get_user_voice_minutes(self, guild, user_id):
        user_activity = await self.config.guild(guild).at_user_activity() # Use central config
        total_minutes = user_activity.get(str(user_id), 0)
        guild_id = guild.id
        if guild_id in self.voice_tracking and user_id in self.voice_tracking[guild_id]:
            join_time = self.voice_tracking[guild_id][user_id]
            current_session_minutes = int((datetime.utcnow() - join_time).total_seconds() / 60)
            if current_session_minutes >= 1:
                total_minutes += current_session_minutes
                log.debug(f"ActivityTracking: Added {current_session_minutes} minutes from current session for user {user_id}")
        return total_minutes

    # --- DJANGO SYNC (API Calls OUT to Django) ---

    async def _update_website_activity(self, guild, member, total_minutes_to_send):
        """Sends *total* activity updates to the Django website."""
        guild_settings = await self.config.guild(guild).all()
        api_url = guild_settings.get("at_api_url") # Use central config
        api_key = guild_settings.get("at_api_key") # Use central config
        
        log.info(f"ActivityTracking: Attempting to update website activity for {member.name} ({member.id}): {total_minutes_to_send} total minutes")
        
        if not api_url or not api_key: 
            log.warning(f"ActivityTracking: Django API URL or Key not configured for guild {guild.id}. Skipping activity update for {member.name}.")
            return
        
        endpoint = f"{api_url}update-activity/"
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {"discord_id": str(member.id), "voice_minutes": total_minutes_to_send}
        
        try:
            async with self.session.post(endpoint, headers=headers, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    response_data = await resp.json()
                    log.info(f"ActivityTracking: Successfully synced {total_minutes_to_send} total minutes for user {member.id}.")
                else:
                    error_text = await resp.text()
                    log.error(f"ActivityTracking: Failed to update activity for {member.id}: {resp.status} - {error_text}")
        except Exception as e:
            log.error(f"ActivityTracking: Exception updating website activity for {member.id}: {str(e)}")

    async def _notify_website_of_promotion(self, guild, discord_id, new_role_name):
        """Notify the website of a community role promotion."""
        guild_settings = await self.config.guild(guild).all()
        promotion_update_url_config = guild_settings.get("at_promotion_update_url")
        api_url = guild_settings.get("at_api_url")
        api_key = guild_settings.get("at_api_key")
        
        log.info(f"ActivityTracking: Notifying website of community role promotion for {discord_id} to {new_role_name}")
        
        if not api_key: 
            log.warning(f"ActivityTracking: API Key not configured for guild {guild.id}. Skipping promotion notification for {discord_id}.")
            return
        
        if promotion_update_url_config:
            endpoint = promotion_update_url_config
        elif api_url:
            endpoint = f"{api_url}update-role/"
        else:
            log.warning(f"ActivityTracking: Neither promotion_update_url nor api_url configured for guild {guild.id}. Skipping promotion notification.")
            return

        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {"discord_id": str(discord_id), "new_role": new_role_name}
        
        try:
            async with self.session.post(endpoint, headers=headers, json=payload, timeout=5) as resp:
                if resp.status == 200:
                    log.info(f"ActivityTracking: Successfully notified website of community role promotion for {discord_id} to {new_role_name}.")
                else:
                    error_text = await resp.text()
                    log.error(f"ActivityTracking: Failed to notify website of community role promotion for {discord_id} to {new_role_name}: {resp.status} - {error_text}")
        except Exception as e:
            log.error(f"ActivityTracking: Exception notifying website of community role promotion for {discord_id}: {str(e)}")

    async def _notify_website_of_military_rank(self, guild, discord_id, rank_name):
        """Notify the website of a military rank update."""
        guild_settings = await self.config.guild(guild).all()
        military_rank_update_url_config = guild_settings.get("at_military_rank_update_url")
        api_url = guild_settings.get("at_api_url")
        api_key = guild_settings.get("at_api_key")
        
        log.info(f"ActivityTracking: Notifying website of military rank update for {discord_id} to {rank_name}")
        
        if not api_key: 
            log.warning(f"ActivityTracking: API Key not configured for guild {guild.id}. Skipping military rank update for {discord_id}.")
            return
        
        if military_rank_update_url_config:
            endpoint = military_rank_update_url_config
        elif api_url:
            endpoint = f"{api_url}update-military-rank/"
        else:
            log.warning(f"ActivityTracking: Neither military_rank_update_url nor api_url configured for guild {guild.id}. Skipping military rank update.")
            return
        
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {"discord_id": str(discord_id), "rank_name": rank_name}
        
        try:
            async with self.session.post(endpoint, headers=headers, json=payload, timeout=5) as resp:
                if resp.status == 200:
                    log.info(f"ActivityTracking: Successfully updated military rank for {discord_id} to {rank_name}")
                else:
                    error_text = await resp.text()
                    log.error(f"ActivityTracking: Failed to update military rank: {resp.status} - {error_text}")
        except Exception as e:
            log.error(f"ActivityTracking: Exception updating military rank: {str(e)}")

    # --- PERIODIC TASKS ---

    async def _setup_periodic_tasks(self):
        """Sets up periodic tasks for role checking and activity updates."""
        await self.cog.bot.wait_until_ready() # Wait for bot to be fully ready
        
        guild_id_env_str = os.environ.get("DISCORD_GUILD_ID") # Used for the main guild for ActivityTracker's internal API routes
        if not guild_id_env_str:
            log.error("ActivityTracking: DISCORD_GUILD_ID environment variable not set. Periodic tasks will not be scheduled.")
            return

        guild_id = int(guild_id_env_str)
        guild = self.cog.bot.get_guild(guild_id)
        if not guild:
            log.error(f"ActivityTracking: Guild with ID {guild_id} not found. Periodic tasks will not be scheduled.")
            return

        # Schedule the periodic role check every 24 hours
        if not self.role_check_task or not self.role_check_task.is_running():
            self.role_check_task = self.cog.bot.loop.create_task(self._schedule_periodic_role_check_loop(guild_id))
        
        # Schedule the periodic activity update every 5 minutes
        if not self.activity_update_task or not self.activity_update_task.is_running():
            self.activity_update_task = self.cog.bot.loop.create_task(self._schedule_periodic_activity_updates_loop(guild_id))

    async def _schedule_periodic_role_check_loop(self, guild_id: int):
        """Schedules the periodic role check to run every 24 hours."""
        while True: # Run indefinitely while cog is loaded
            try:
                log.info(f"ActivityTracking: Running scheduled role check for guild ID: {guild_id}")
                await self._periodic_role_check(guild_id)
                log.info(f"ActivityTracking: Completed scheduled role check for guild ID: {guild_id}")
            except asyncio.CancelledError:
                log.info("ActivityTracking: _schedule_periodic_role_check_loop cancelled.")
                break
            except Exception as e:
                log.exception(f"ActivityTracking: An error occurred during the scheduled role check: {e}")
            await asyncio.sleep(86400)  # Sleep for 24 hours

    async def _schedule_periodic_activity_updates_loop(self, guild_id: int):
        """Schedules periodic updates of activity for users currently in voice channels."""
        while True: # Run indefinitely while cog is loaded
            try:
                log.info(f"ActivityTracking: Running periodic activity update for guild ID: {guild_id}")
                await self._update_active_voice_users(guild_id)
            except asyncio.CancelledError:
                log.info("ActivityTracking: _schedule_periodic_activity_updates_loop cancelled.")
                break
            except Exception as e:
                log.exception(f"ActivityTracking: An error occurred during the periodic activity update: {e}")
            await asyncio.sleep(300)  # Sleep for 5 minutes

    async def _update_active_voice_users(self, guild_id: int):
        """Updates activity for users currently in voice channels."""
        guild = self.cog.bot.get_guild(guild_id)
        if not guild:
            log.error(f"ActivityTracking: Guild with ID {guild_id} not found for periodic activity update.")
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
                        log.info(f"ActivityTracking: Periodic update: {member.name} has been in voice for {minutes_since_join} minutes")
                        await self._update_user_voice_minutes(guild, member, minutes_since_join)
                        self.voice_tracking[guild_id][member.id] = current_time
                        updates_sent += 1
        
        log.info(f"ActivityTracking: Periodic activity update complete. Sent {updates_sent} updates.")

    async def _periodic_role_check(self, guild_id: int):
        """Performs a periodic check of all guild members' roles based on their total voice activity."""
        log.info(f"ActivityTracking: Starting periodic role check for guild ID: {guild_id}")
        guild = self.cog.bot.get_guild(guild_id)
        if not guild:
            log.error(f"ActivityTracking: Guild with ID {guild_id} not found for periodic role check.")
            return

        members_checked = 0
        promotions_made = 0

        try:
            async for member in guild.fetch_members(limit=None):
                if member.bot:
                    continue
                
                members_checked += 1
                total_minutes = await self._get_user_voice_minutes(guild, member.id)

                initial_roles = {r.id for r in member.roles}
                await self._check_for_promotion(guild, member, total_minutes)
                final_roles = {r.id for r in member.roles}

                if initial_roles != final_roles:
                    promotions_made += 1
                    log.info(f"ActivityTracking: Role change detected for {member.name} ({member.id}) during periodic check.")
                
                await asyncio.sleep(0.1)

        except discord.Forbidden:
            log.error(f"ActivityTracking: Bot lacks permissions to fetch members in guild {guild.id} for periodic check. Ensure 'Server Members Intent' is enabled in bot settings.")
        except Exception as e:
            log.exception(f"ActivityTracking: An unexpected error occurred during periodic role check for guild {guild.id}: {e}")

        log.info(f"ActivityTracking: Periodic role check complete for guild {guild.id}. Checked {members_checked} members, made {promotions_made} role changes.")

    # --- PROMOTION LOGIC ---

    async def _check_for_promotion(self, guild, member, total_minutes):
        # Recruit -> Member
        recruit_role_id = await self.config.guild(guild).at_recruit_role_id()
        member_role_id = await self.config.guild(guild).at_member_role_id()
        threshold_hours = await self.config.guild(guild).at_promotion_threshold_hours()
        if recruit_role_id and member_role_id and threshold_hours:
            recruit_role = guild.get_role(recruit_role_id)
            member_role = guild.get_role(member_role_id)
            if recruit_role and member_role and recruit_role in member.roles:
                if total_minutes >= threshold_hours * 60:
                    await member.remove_roles(recruit_role, reason="Promotion")
                    await member.add_roles(member_role, reason="Promotion")
                    await self._notify_website_of_promotion(guild, member.id, "member")
                    channel_id = await self.config.guild(guild).at_promotion_channel_id()
                    if channel_id:
                        channel = guild.get_channel(channel_id)
                        if channel:
                            await channel.send(
                                f"🎉 Congratulations {member.mention}! You've been promoted to **{member_role.name}** status!"
                            )
        # Military Ranks
        military_ranks = await self.config.guild(guild).at_military_ranks()
        if not military_ranks:
            return
        
        sorted_ranks_ascending = sorted(
            [r for r in military_ranks if isinstance(r.get('required_hours'), (int, float))],
            key=lambda x: x['required_hours']
        )
        
        user_hours = total_minutes / 60.0
        
        current_highest_eligible_rank = None
        for rank_data in sorted_ranks_ascending:
            if user_hours >= rank_data['required_hours']:
                current_highest_eligible_rank = rank_data
            else:
                break 

        user_current_military_role_ids = {
            r.id for r in member.roles 
            if any(str(r.id) == str(rank_data.get('discord_role_id')) for rank_data in military_ranks)
        }

        target_role_id = None
        target_rank_name = "None"
        if current_highest_eligible_rank:
            target_role_id = int(current_highest_eligible_rank['discord_role_id'])
            target_rank_name = current_highest_eligible_rank['name']
        
        roles_to_remove = []
        roles_to_add = []

        all_configured_military_role_ids = {int(r['discord_role_id']) for r in military_ranks if 'discord_role_id' in r}

        for role_obj in member.roles:
            if role_obj.id in all_configured_military_role_ids and role_obj.id != target_role_id:
                roles_to_remove.append(role_obj)
        
        if target_role_id and target_role_id not in user_current_military_role_ids:
            add_role_obj = guild.get_role(target_role_id)
            if add_role_obj:
                roles_to_add.append(add_role_obj)

        if roles_to_remove or roles_to_add:
            log.info(f"ActivityTracking: Processing rank changes for {member.name} ({member.id}). Removing: {[r.name for r in roles_to_remove]}, Adding: {[r.name for r in roles_to_add]}")
            try:
                if roles_to_remove:
                    await member.remove_roles(*roles_to_remove, reason="Military rank update")
                if roles_to_add:
                    await member.add_roles(*roles_to_add, reason="Military rank update")
                
                await self._notify_website_of_military_rank(guild, member.id, target_rank_name)

                if target_role_id and current_highest_eligible_rank and roles_to_add:
                    channel_id = await self.config.guild(guild).at_promotion_channel_id()
                    if channel_id:
                        channel = guild.get_channel(channel_id)
                        if channel:
                            await channel.send(
                                f"🎖️ Bravo, {member.mention}! You've achieved the rank of **{current_highest_eligible_rank['name']}**!"
                            )
            except discord.Forbidden:
                log.error(f"ActivityTracking: Bot lacks permissions to manage roles for {member.name} in guild {guild.name} during military rank update.")
            except Exception as e:
                log.exception(f"ActivityTracking: Error during military rank update for {member.name}: {e}")
        else:
            log.debug(f"ActivityTracking: {member.name} already has the correct military rank or no changes needed.")

    def _generate_progress_bar(self, percent, length=10):
        filled_length = int(length * percent / 100)
        bar = '█' * filled_length + '░' * (length - filled_length)
        return f"[{bar}]"


    # --- DISCORD LISTENERS ---
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        
        log.debug(f"ActivityTracking: Voice state update for {member.name} ({member.id}) - Before: {before.channel}, After: {after.channel}")
        
        guild = member.guild
        guild_id = guild.id
        user_id = member.id
        
        if guild_id not in self.voice_tracking:
            self.voice_tracking[guild_id] = {}
        
        if before.channel is None and after.channel is not None:
            # User joined a voice channel
            self.voice_tracking[guild_id][user_id] = datetime.utcnow()
            log.debug(f"ActivityTracking: {member.name} joined voice channel {after.channel.name}. Started tracking.")
        
        elif before.channel is not None and after.channel is None:
            # User left a voice channel
            if user_id in self.voice_tracking[guild_id]:
                join_time = self.voice_tracking[guild_id][user_id]
                duration = datetime.utcnow() - join_time
                minutes = duration.total_seconds() / 60
                
                log.debug(f"ActivityTracking: {member.name} left voice channel {before.channel.name}. Duration: {minutes:.2f} minutes.")
                
                if minutes >= 1:
                    await self._update_user_voice_minutes(guild, member, int(minutes))
                else:
                    log.debug(f"ActivityTracking: Duration too short ({minutes:.2f}m). Skipping sync.")
                
                del self.voice_tracking[guild_id][user_id]
            else:
                log.warning(f"ActivityTracking: {member.name} left voice but wasn't being tracked.")


    # --- Commands (these will be added as subcommands to the main cog's group) ---

    @commands.group(name="activityset", aliases=["atset"])
    async def activityset_group(self, ctx):
        """Manage ActivityTracker settings for Zerolivesleft."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @activityset_group.command()
    async def roles(self, ctx, recruit: discord.Role, member: discord.Role):
        """Set the Recruit and Member roles."""
        await self.config.guild(ctx.guild).at_recruit_role_id.set(recruit.id) # Use central config
        await self.config.guild(ctx.guild).at_member_role_id.set(member.id) # Use central config
        await ctx.send("Recruit and Member roles have been set.")

    @activityset_group.command()
    async def threshold(self, ctx, hours: float):
        """Set the voice hours required to be promoted from Recruit to Member."""
        if hours <= 0:
            return await ctx.send("Threshold must be a positive number of hours.")
        await self.config.guild(ctx.guild).at_promotion_threshold_hours.set(hours) # Use central config
        await ctx.send(f"Promotion threshold set to {hours} hours.")

    @activityset_group.command(name="api")
    async def set_api(self, ctx, url: str, key: str):
        """Set the base API URL and the API Key for the website."""
        if not url.startswith("http://") and not url.startswith("https://"):
            return await ctx.send("API URL must start with http:// or https://")
        if not url.endswith("/"):
            url += "/"
        
        await self.config.guild(ctx.guild).at_api_url.set(url) # Use central config
        await self.config.guild(ctx.guild).at_api_key.set(key) # Use central config
        await ctx.send("API URL and Key have been saved.")

    @activityset_group.command(name="promotionurl")
    async def set_promotion_url(self, ctx, url: str):
        """Set the full URL for community role promotions."""
        if not url.startswith("http://") and not url.startswith("https://"):
            return await ctx.send("URL must start with http:// or https://")
        await self.config.guild(ctx.guild).at_promotion_update_url.set(url) # Use central config
        await ctx.send("Community role promotion URL set.")

    @activityset_group.command(name="militaryrankurl")
    async def set_military_rank_url(self, ctx, url: str):
        """Set the full URL for military rank updates."""
        if not url.startswith("http://") and not url.startswith("https://"):
            return await ctx.send("URL must start with http:// or https://")
        await self.config.guild(ctx.guild).at_military_rank_update_url.set(url) # Use central config
        await ctx.send("Military rank update URL set.")

    @activityset_group.command(name="promotionchannel")
    async def set_promotion_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel for promotion notifications."""
        await self.config.guild(ctx.guild).at_promotion_channel_id.set(channel.id) # Use central config
        await ctx.send(f"Promotion notification channel set to {channel.mention}.")

    @activityset_group.group(name="militaryranks")
    async def militaryranks_group(self, ctx):
        """Manage military ranks."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @militaryranks_group.command(name="add")
    async def add_rank(self, ctx, role: discord.Role, required_hours: float):
        """Add a new military rank."""
        if required_hours < 0:
            return await ctx.send("Required hours cannot be negative.")
        async with self.config.guild(ctx.guild).at_military_ranks() as ranks: # Use central config
            for r in ranks:
                if str(r.get("discord_role_id")) == str(role.id):
                    return await ctx.send(f"A rank for role '{role.name}' already exists.")
                if r.get("name").lower() == role.name.lower():
                    return await ctx.send(f"A rank with name '{role.name}' already exists.")
            
            ranks.append({
                "name": role.name,
                "discord_role_id": str(role.id),
                "required_hours": required_hours
            })
            ranks.sort(key=lambda r: r['required_hours'])
        await ctx.send(f"Added military rank: **{role.name}** at **{required_hours}** hours.")

    @militaryranks_group.command(name="remove")
    async def remove_rank(self, ctx, role_or_name: str):
        """Remove a military rank by role ID or name."""
        async with self.config.guild(ctx.guild).at_military_ranks() as ranks: # Use central config
            initial_len = len(ranks)
            ranks[:] = [r for r in ranks if str(r.get('discord_role_id')) != role_or_name]
            if len(ranks) == initial_len:
                ranks[:] = [r for r in ranks if r.get('name').lower() != role_or_name.lower()]
            
            if len(ranks) < initial_len:
                await ctx.send(f"Removed military rank matching '{role_or_name}'.")
            else:
                await ctx.send(f"No military rank found matching '{role_or_name}'.")

    @militaryranks_group.command(name="clear")
    async def clear_ranks(self, ctx):
        """Clear all configured military ranks."""
        view = ConfirmView(ctx.author, disable_buttons=True)
        await ctx.send(
            "Are you sure you want to clear ALL configured military ranks? This cannot be undone.",
            view=view
        )
        await view.wait()
        if view.result:
            await self.config.guild(ctx.guild).at_military_ranks.set([]) # Use central config
            await ctx.send("All military ranks have been cleared.")
        else:
            await ctx.send("Operation cancelled.")

    @militaryranks_group.command(name="list")
    async def list_ranks(self, ctx):
        """List all configured military ranks."""
        ranks = await self.config.guild(ctx.guild).at_military_ranks() # Use central config
        if not ranks:
            await ctx.send("No military ranks have been set.")
            return
        msg = "**Configured Military Ranks:**\n"
        sorted_ranks = sorted(ranks, key=lambda r: r['required_hours']) 
        for r in sorted_ranks:
            role_obj = ctx.guild.get_role(int(r['discord_role_id'])) if r.get('discord_role_id') else None
            role_mention = role_obj.mention if role_obj else f"ID: `{r.get('discord_role_id', 'N/A')}`"
            msg += f"- **{r['name']}** ({role_mention}): `{r['required_hours']}` hours\n"
        await ctx.send(msg)

    async def show_config_command(self, ctx: commands.Context):
        """Shows the current ActivityTracker configuration."""
        guild_settings = await self.config.guild(ctx.guild).all() # Get guild-specific config
        embed = discord.Embed(
            title="ActivityTracker Settings",
            color=discord.Color.blue()
        )
        api_url = guild_settings.get("at_api_url")
        api_key = guild_settings.get("at_api_key")
        promotion_url = guild_settings.get("at_promotion_update_url")
        military_rank_url = guild_settings.get("at_military_rank_update_url")
        embed.add_field(
            name="API Configuration",
            value=(
                f"Base API URL: `{api_url or 'Not set'}`\n"
                f"API Key: `{'✓ Set' if api_key else '✗ Not set'}`\n"
                f"Community Role URL: `{promotion_url or 'Not set'}`\n"
                f"Military Rank URL: `{military_rank_url or 'Not set'}`"
            ),
            inline=False
        )
        recruit_role_id = guild_settings.get("at_recruit_role_id")
        member_role_id = guild_settings.get("at_member_role_id")
        recruit_role = ctx.guild.get_role(recruit_role_id) if recruit_role_id else None
        member_role = ctx.guild.get_role(member_role_id) if member_role_id else None
        embed.add_field(
            name="Role Configuration",
            value=(
                f"Recruit Role: {recruit_role.mention if recruit_role else '`Not set`'}\n"
                f"Member Role: {member_role.mention if member_role else '`Not set`'}\n"
                f"Promotion Threshold: `{guild_settings.get('at_promotion_threshold_hours')} hours`"
            ),
            inline=False
        )
        channel_id = guild_settings.get("at_promotion_channel_id")
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        embed.add_field(
            name="Notification Settings",
            value=f"Promotion Channel: {channel.mention if channel else '`Not set`'}",
            inline=False
        )
        military_ranks = guild_settings.get("at_military_ranks", [])
        valid_ranks = [r for r in military_ranks if 'discord_role_id' in r and ctx.guild.get_role(int(r['discord_role_id']))]
        embed.add_field(
            name="Military Ranks",
            value=(
                f"Total Configured: `{len(military_ranks)}`\n"
                f"Valid Ranks: `{len(valid_ranks)}`\n"
                f"Use `{ctx.prefix}zll activityset militaryranks list` for details" # Updated command help
            ),
            inline=False
        )
        await ctx.send(embed=embed)

    # --- DEBUG/UTILITY ---

    @activityset_group.command(name="debug")
    async def debug_info(self, ctx):
        """Shows debug information about the ActivityTracker cog."""
        embed = discord.Embed(
            title="ActivityTracker Debug Information",
            color=discord.Color.gold()
        )
        # Access guild from cog.bot
        main_guild_id_env = os.environ.get("DISCORD_GUILD_ID", "Not set")
        main_guild = self.cog.bot.get_guild(int(main_guild_id_env)) if main_guild_id_env != "Not set" else None

        web_status = "Unknown"
        if self.cog.web_runner and self.cog.web_site:
            web_status = "Running"
        elif self.cog.web_runner and not self.cog.web_site:
            web_status = "Runner setup, site not started"
        else:
            web_status = "Not running"

        # Host and Port from main cog's config
        host = await self.cog.config.webserver_host()
        port = await self.cog.config.webserver_port()
        
        embed.add_field(
            name="Central Web API Server (from WebServer)",
            value=f"Status: {web_status}\nHost: {host}\nPort: {port}",
            inline=False
        )
        total_tracked = 0
        for guild_id, members in self.voice_tracking.items():
            total_tracked += len(members)
        embed.add_field(
            name="Voice Tracking",
            value=f"Currently tracking: {total_tracked} users",
            inline=False
        )
        embed.add_field(
            name="Environment Variables (DISCORD_GUILD_ID used by ActivityTracker)",
            value=f"DISCORD_GUILD_ID: {main_guild_id_env}\n(Note: ACTIVITY_WEB_HOST/PORT are no longer directly used by this module's web server)",
            inline=False
        )
        embed.set_footer(text=f"ActivityTracker Cog | Discord.py {discord.__version__}")
        await ctx.send(embed=embed)

    @activityset_group.command(name="forcesync")
    async def force_sync(self, ctx):
        """Force a sync of all active voice users."""
        guild = ctx.guild # Sync for the current guild
        guild_id = guild.id
        
        updates_sent = 0
        
        await ctx.send("Starting forced sync of all active voice users...")
        
        for voice_channel in guild.voice_channels:
            for member in voice_channel.members:
                if member.bot:
                    continue
                
                if guild_id in self.voice_tracking and member.id in self.voice_tracking[guild_id]:
                    join_time = self.voice_tracking[guild_id][member.id]
                    current_time = datetime.utcnow()
                    minutes_since_join = int((current_time - join_time).total_seconds() / 60)
                    
                    if minutes_since_join >= 1:
                        await ctx.send(f"Syncing {member.name}: {minutes_since_join} minutes")
                        
                        await self._update_user_voice_minutes(guild, member, minutes_since_join)
                        
                        self.voice_tracking[guild_id][member.id] = current_time
                        updates_sent += 1
        
        await ctx.send(f"Forced sync complete. Sent {updates_sent} updates.")