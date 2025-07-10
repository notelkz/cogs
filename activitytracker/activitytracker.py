# Red-V3/cogs/activitytracker/activitytracker.py

import discord
import asyncio
import aiohttp
import os
import json
from datetime import datetime

from redbot.core import commands, Config
from aiohttp import web # Required for creating web server routes

class ActivityTracker(commands.Cog):
    """Tracks user voice activity and syncs with a Django website API."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        
        default_guild = {
            "api_url": None,
            "api_key": None,
            "recruit_role_id": None,
            "member_role_id": None,
            "promotion_threshold_hours": 24.0,
            "promotion_channel_id": None
        }
        self.config.register_guild(**default_guild)
        
        self.voice_tracking = {}
        self.session = aiohttp.ClientSession()
        
        # --- ADDITION: Start the web server for the API endpoint ---
        self.bot.loop.create_task(self.initialize_webserver())

    # --- NEW: Function to handle the web server and routes ---
    async def initialize_webserver(self):
        await self.bot.wait_until_ready()
        routes = web.RouteTableDef()

        @routes.post("/api/assign_initial_role")
        async def assign_initial_role(request):
            # Security Check
            api_key = await self.config.guild(request.app["guild"]).api_key()
            if not api_key or request.headers.get("X-API-Key") != api_key:
                return web.Response(text="Unauthorized", status=401)
            
            try:
                data = await request.json()
                discord_id = int(data.get("discord_id"))
            except (ValueError, TypeError, json.JSONDecodeError):
                return web.Response(text="Invalid request data", status=400)

            guild = request.app["guild"]
            recruit_role_id = await self.config.guild(guild).recruit_role_id()
            if not recruit_role_id:
                print("BOT API ERROR: Recruit Role ID is not configured.")
                return web.Response(text="Recruit role not configured", status=500)

            member = guild.get_member(discord_id)
            recruit_role = guild.get_role(recruit_role_id)

            if member and recruit_role:
                try:
                    await member.add_roles(recruit_role, reason="Initial role assignment from website.")
                    print(f"BOT API: Successfully assigned Recruit role to {member.name}")
                    return web.Response(text="Role assigned successfully", status=200)
                except discord.Forbidden:
                    print(f"BOT API ERROR: Missing permissions to assign role to {member.name}")
                    return web.Response(text="Missing permissions", status=503)
                except Exception as e:
                    print(f"BOT API ERROR: Failed to assign role: {e}")
                    return web.Response(text="Internal server error", status=500)
            else:
                print(f"BOT API WARN: Could not find member ({discord_id}) or role ({recruit_role_id}) in guild.")
                return web.Response(text="Member or role not found", status=404)

        app = web.Application()
        app.add_routes(routes)
        
        # Get the guild ID from environment variables
        guild_id_str = os.environ.get("DISCORD_GUILD_ID")
        if not guild_id_str:
            print("CRITICAL ERROR: DISCORD_GUILD_ID environment variable not set for the bot.")
            return
            
        app["guild"] = self.bot.get_guild(int(guild_id_str))
        runner = web.AppRunner(app)
        await runner.setup()
        
        site = web.TCPSite(runner, "0.0.0.0", 5002) 
        await site.start()
        print("ActivityTracker API server started on port 5002.")

    def cog_unload(self):
        asyncio.create_task(self.session.close())

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        if before.channel is None and after.channel is not None:
            if member.guild.id not in self.voice_tracking:
                self.voice_tracking[member.guild.id] = {}
            self.voice_tracking[member.guild.id][member.id] = datetime.utcnow()
            print(f"ACTIVITY: User {member.name} joined voice. Starting session.")
        elif before.channel is not None and after.channel is None:
            if member.guild.id in self.voice_tracking and member.id in self.voice_tracking[member.guild.id]:
                join_time = self.voice_tracking[member.guild.id].pop(member.id)
                duration_minutes = (datetime.utcnow() - join_time).total_seconds() / 60
                if duration_minutes < 1:
                    return
                print(f"ACTIVITY: User {member.name} left voice. Duration: {duration_minutes:.2f} minutes.")
                await self._update_website_activity(member.guild, member, int(duration_minutes))

    async def _update_website_activity(self, guild: discord.Guild, member: discord.Member, minutes_to_add: int):
        guild_settings = await self.config.guild(guild).all()
        api_url = guild_settings.get("api_url")
        api_key = guild_settings.get("api_key")
        if not api_url or not api_key: return
        endpoint = f"{api_url}/api/update_activity/"
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {"discord_id": str(member.id), "voice_minutes": minutes_to_add}
        try:
            async with self.session.post(endpoint, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    print(f"API: Successfully synced {minutes_to_add} minutes for user {member.id}.")
                    data = await resp.json()
                    total_minutes = data.get("total_minutes", 0)
                    await self._check_for_promotion(guild, member, total_minutes)
                else:
                    print(f"API ERROR: Failed to update activity for {member.id}: {resp.status} - {await resp.text()}")
        except Exception as e:
            print(f"NETWORK ERROR: Could not reach website for {member.id}: {e}")

    async def _check_for_promotion(self, guild: discord.Guild, member: discord.Member, total_minutes: int):
        """
        Checks for both Member promotion and Time Rank promotion, handling them as separate systems.
        """
        guild_settings = await self.config.guild(guild).all()
        api_url = guild_settings.get("api_url")
        api_key = guild_settings.get("api_key")

        # --- System 1: Recruit -> Member Promotion ---
        recruit_role_id = guild_settings.get("recruit_role_id")
        member_role_id = guild_settings.get("member_role_id")
        promotion_threshold_hours = guild_settings.get("promotion_threshold_hours")
        
        if all([recruit_role_id, member_role_id, promotion_threshold_hours]):
            promotion_threshold_minutes = promotion_threshold_hours * 60
            recruit_role = guild.get_role(recruit_role_id)
            
            # This logic only runs if the user is currently a Recruit
            if recruit_role and recruit_role in member.roles and total_minutes >= promotion_threshold_minutes:
                member_role = guild.get_role(member_role_id)
                if member_role:
                    print(f"MEMBERSHIP: Promoting {member.name} from Recruit to Member...")
                    try:
                        await member.remove_roles(recruit_role, reason="Automatic promotion via voice activity")
                        await member.add_roles(member_role, reason="Automatic promotion via voice activity")
                        await self._notify_website_of_promotion(guild, member.id, "member") # Update website role
                        
                        channel_id = guild_settings.get("promotion_channel_id")
                        if channel_id:
                            channel = guild.get_channel(channel_id)
                            if channel:
                                await channel.send(
                                    f"🎉 Congratulations {member.mention}! You've been promoted to **Member** status!"
                                )
                    except discord.Forbidden:
                        print(f"MEMBERSHIP ERROR: Missing permissions to promote {member.name}.")
                    except Exception as e:
                        print(f"MEMBERSHIP ERROR: An unexpected error occurred: {e}")

        # --- System 2: Military Time Rank Promotion (runs for everyone, every time) ---
        if not api_url or not api_key:
            return

        # Fetch the list of all possible time ranks from the website
        ranks_endpoint = f"{api_url}/api/get_time_ranks/"
        headers = {"X-API-Key": api_key}
        try:
            async with self.session.get(ranks_endpoint, headers=headers) as resp:
                if resp.status != 200:
                    print(f"RANKING ERROR: Could not fetch time ranks from website. Status: {resp.status}")
                    return
                # The API returns ranks ordered from highest to lowest (by rank_order)
                time_ranks = await resp.json()
        except Exception as e:
            print(f"RANKING NETWORK ERROR: {e}")
            return

        if not time_ranks:
            return # No ranks configured on the website

        # Determine the highest rank the user has earned
        user_hours = total_minutes / 60
        earned_rank = None
        for rank in time_ranks:
            if user_hours >= rank['required_hours']:
                earned_rank = rank
                break # Stop at the first (highest) rank they qualify for

        if not earned_rank or int(earned_rank['discord_role_id']) == recruit_role_id:
            return # User doesn't qualify for any rank yet, or their rank is still Recruit

        # Check if the user already has this rank to avoid unnecessary API calls
        earned_role_id = int(earned_rank['discord_role_id'])
        if any(role.id == earned_role_id for role in member.roles):
            return # User already has the correct rank

        # --- The Promotion: Remove old rank roles and add the new one ---
        print(f"RANKING: Updating {member.name}'s rank to {earned_rank['name']}...")
        
        # Get all possible time rank IDs (excluding Recruit) to remove any old ones
        all_time_rank_ids = {int(r['discord_role_id']) for r in time_ranks if int(r['discord_role_id']) != recruit_role_id}
        
        roles_to_keep = [role for role in member.roles if role.id not in all_time_rank_ids]
        
        new_rank_role = guild.get_role(earned_role_id)
        if not new_rank_role:
            print(f"RANKING ERROR: Role ID {earned_role_id} not found in this server.")
            return

        roles_to_keep.append(new_rank_role)

        try:
            await member.edit(roles=roles_to_keep, reason=f"Automatic time rank update to {earned_rank['name']}")
            print(f"RANKING SUCCESS: {member.name} is now {earned_rank['name']}.")
        except discord.Forbidden:
            print(f"RANKING ERROR: Missing permissions to manage roles for {member.name}.")
        except Exception as e:
            print(f"RANKING ERROR: An unexpected error occurred: {e}")

    async def _notify_website_of_promotion(self, guild: discord.Guild, discord_id: int, new_role: str):
        guild_settings = await self.config.guild(guild).all()
        api_url = guild_settings.get("api_url")
        api_key = guild_settings.get("api_key")
        if not api_url or not api_key: return
        endpoint = f"{api_url}/api/update_role/"
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {"discord_id": str(discord_id), "new_role": new_role}
        try:
            async with self.session.post(endpoint, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    print(f"API: Successfully notified website of promotion for {discord_id}.")
                else:
                    print(f"API ERROR: Failed to update role on website for {discord_id}: {resp.status} - {await resp.text()}")
        except Exception as e:
            print(f"NETWORK ERROR: Could not notify website of promotion for {discord_id}: {e}")

    @commands.group(name="activityset")
    @commands.admin_or_permissions(manage_guild=True)
    async def activityset(self, ctx):
        pass
    
    @activityset.command(name="api")
    async def set_api(self, ctx, url: str, key: str):
        if not url.startswith("http"):
            return await ctx.send("The URL must start with `http://` or `https://`.")
        await self.config.guild(ctx.guild).api_url.set(url)
        await self.config.guild(ctx.guild).api_key.set(key)
        await ctx.send("API URL and Key have been set.")

    @activityset.command(name="roles")
    async def set_roles(self, ctx, recruit_role: discord.Role, member_role: discord.Role):
        await self.config.guild(ctx.guild).recruit_role_id.set(recruit_role.id)
        await self.config.guild(ctx.guild).member_role_id.set(member_role.id)
        await ctx.send(f"Roles set: Recruits = `{recruit_role.name}`, Members = `{member_role.name}`")
    
    @activityset.command(name="threshold")
    async def set_threshold(self, ctx, hours: float):
        if hours <= 0: return await ctx.send("Threshold must be a positive number of hours.")
        await self.config.guild(ctx.guild).promotion_threshold_hours.set(hours)
        await ctx.send(f"Promotion threshold set to {hours} hours.")
    
    @activityset.command(name="channel")
    async def set_channel(self, ctx, channel: discord.TextChannel = None):
        if channel:
            await self.config.guild(ctx.guild).promotion_channel_id.set(channel.id)
            await ctx.send(f"Promotion announcements will be sent to {channel.mention}.")
        else:
            await self.config.guild(ctx.guild).promotion_channel_id.set(None)
            await ctx.send("Promotion announcements have been disabled.")