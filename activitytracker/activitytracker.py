# Red-V3/cogs/activitytracker/activitytracker.py

import discord
import asyncio
import aiohttp
from datetime import datetime

from redbot.core import commands, Config
from redbot.core.tasks import loop

class ActivityTracker(commands.Cog):
    """Tracks user voice activity and syncs with a Django website API."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210, force_registration=True)
        
        # Guild-specific settings stored in Red's Config
        default_guild = {
            "api_url": None,
            "api_key": None,
            "recruit_role_id": None,
            "member_role_id": None,
            "promotion_threshold_hours": 24.0,
            "promotion_channel_id": None
        }
        self.config.register_guild(**default_guild)
        
        # In-memory tracking for live voice sessions {guild_id: {user_id: join_timestamp}}
        self.voice_tracking = {}
        self.session = aiohttp.ClientSession() # Create a persistent session for API calls
        
    def cog_unload(self):
        # Cleanly close the session when the cog is unloaded
        asyncio.create_task(self.session.close())

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Listen for users joining/leaving voice channels."""
        if member.bot:
            return

        # User joins a voice channel
        if before.channel is None and after.channel is not None:
            if member.guild.id not in self.voice_tracking:
                self.voice_tracking[member.guild.id] = {}
            self.voice_tracking[member.guild.id][member.id] = datetime.utcnow()
            print(f"User {member.name} joined voice. Starting session.")

        # User leaves a voice channel
        elif before.channel is not None and after.channel is None:
            if member.guild.id in self.voice_tracking and member.id in self.voice_tracking[member.guild.id]:
                join_time = self.voice_tracking[member.guild.id].pop(member.id)
                duration_minutes = (datetime.utcnow() - join_time).total_seconds() / 60
                
                if duration_minutes < 1: # Ignore very short sessions
                    return

                print(f"User {member.name} left voice. Duration: {duration_minutes:.2f} minutes.")
                await self._update_website_activity(member.guild, member.id, int(duration_minutes))

    async def _update_website_activity(self, guild, discord_id, minutes_to_add):
        """Send activity update to the website API."""
        guild_settings = await self.config.guild(guild).all()
        api_url = guild_settings.get("api_url")
        api_key = guild_settings.get("api_key")
        
        if not api_url or not api_key:
            return
            
        endpoint = f"{api_url}/api/update_activity/"
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {
            "discord_id": str(discord_id),
            "voice_minutes": minutes_to_add
        }
        
        try:
            async with self.session.post(endpoint, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    print(f"Successfully synced {minutes_to_add} minutes for user {discord_id}.")
                    # The API response now includes the new total minutes
                    data = await resp.json()
                    total_minutes = data.get("total_minutes", 0)
                    await self._check_for_promotion(guild, discord_id, total_minutes)
                else:
                    print(f"API Error updating activity for {discord_id}: {resp.status} - {await resp.text()}")
        except Exception as e:
            print(f"Network Error updating website for {discord_id}: {e}")

    async def _check_for_promotion(self, guild, discord_id, total_minutes):
        """Checks if a user qualifies for promotion from Recruit to Member."""
        guild_settings = await self.config.guild(guild).all()
        recruit_role_id = guild_settings.get("recruit_role_id")
        member_role_id = guild_settings.get("member_role_id")
        promotion_threshold_hours = guild_settings.get("promotion_threshold_hours")
        
        if not all([recruit_role_id, member_role_id, promotion_threshold_hours]):
            return

        promotion_threshold_minutes = promotion_threshold_hours * 60
        
        if total_minutes >= promotion_threshold_minutes:
            member = guild.get_member(int(discord_id))
            recruit_role = guild.get_role(recruit_role_id)
            member_role = guild.get_role(member_role_id)

            if member and recruit_role and member_role and recruit_role in member.roles:
                print(f"Promoting user {member.name} to Member...")
                try:
                    await member.remove_roles(recruit_role, reason="Automatic promotion via voice activity")
                    await member.add_roles(member_role, reason="Automatic promotion via voice activity")
                    await self._notify_website_of_promotion(guild, discord_id, "member")
                    
                    channel_id = guild_settings.get("promotion_channel_id")
                    if channel_id:
                        channel = guild.get_channel(channel_id)
                        if channel:
                            await channel.send(
                                f"🎉 Congratulations {member.mention}! "
                                f"You've been promoted to **Member** status after accumulating "
                                f"{total_minutes / 60:.1f} hours of voice activity!"
                            )
                except discord.Forbidden:
                    print(f"Error: Missing permissions to promote {member.name}.")
                except Exception as e:
                    print(f"Error during promotion for {member.name}: {e}")

    async def _notify_website_of_promotion(self, guild, discord_id, new_role):
        """Notify the website of a role promotion."""
        guild_settings = await self.config.guild(guild).all()
        api_url = guild_settings.get("api_url")
        api_key = guild_settings.get("api_key")
        
        if not api_url or not api_key:
            return
            
        endpoint = f"{api_url}/api/update_role/"
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {"discord_id": str(discord_id), "new_role": new_role}
        
        try:
            async with self.session.post(endpoint, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    print(f"Successfully notified website of promotion for {discord_id}.")
                else:
                    print(f"API Error updating role on website for {discord_id}: {resp.status} - {await resp.text()}")
        except Exception as e:
            print(f"Network Error notifying website of promotion for {discord_id}: {e}")

    @commands.group(name="activityset")
    @commands.admin_or_permissions(manage_guild=True)
    async def activityset(self, ctx):
        """Configure the Activity Tracker."""
        pass
    
    @activityset.command(name="api")
    async def set_api(self, ctx, url: str, key: str):
        """Set the website API URL and Key."""
        if not url.startswith("http"):
            return await ctx.send("The URL must start with `http://` or `https://`.")
        await self.config.guild(ctx.guild).api_url.set(url)
        await self.config.guild(ctx.guild).api_key.set(key)
        await ctx.send("API URL and Key have been set.")

    @activityset.command(name="roles")
    async def set_roles(self, ctx, recruit_role: discord.Role, member_role: discord.Role):
        """Set the recruit and member roles for promotion."""
        await self.config.guild(ctx.guild).recruit_role_id.set(recruit_role.id)
        await self.config.guild(ctx.guild).member_role_id.set(member_role.id)
        await ctx.send(f"Roles set: Recruits = `{recruit_role.name}`, Members = `{member_role.name}`")
    
    @activityset.command(name="threshold")
    async def set_threshold(self, ctx, hours: float):
        """Set the voice activity threshold for promotion (in hours)."""
        if hours <= 0:
            return await ctx.send("Threshold must be a positive number of hours.")
        await self.config.guild(ctx.guild).promotion_threshold_hours.set(hours)
        await ctx.send(f"Promotion threshold set to {hours} hours.")
    
    @activityset.command(name="channel")
    async def set_channel(self, ctx, channel: discord.TextChannel = None):
        """Set the channel for promotion announcements. (Leave empty to disable)"""
        if channel:
            await self.config.guild(ctx.guild).promotion_channel_id.set(channel.id)
            await ctx.send(f"Promotion announcements will be sent to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).promotion_channel_id.set(None)
            await ctx.send("Promotion announcements have been disabled.")
