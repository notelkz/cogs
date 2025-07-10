# Red-V3/cogs/activitytracker/activitytracker.py

import discord
import asyncio
import aiohttp
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
            if request.headers.get("X-API-Key") != await self.config.guild(request.app["guild"]).api_key():
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
        # Pass the primary guild object to the web app for context
        app["guild"] = self.bot.get_guild(int(os.environ.get("DISCORD_GUILD_ID"))) # Assumes GUILD_ID is an env var
        runner = web.AppRunner(app)
        await runner.setup()
        # Listen on a different port than your gamecounter API to avoid conflicts
        site = web.TCPSite(runner, "0.0.0.0", 5002) 
        await site.start()
        print("ActivityTracker API server started on port 5002.")

    def cog_unload(self):
        asyncio.create_task(self.session.close())

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # ... (This function remains exactly the same as before) ...
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

    # ... (The rest of your cog's functions remain the same) ...
    # _update_website_activity, _check_for_promotion, _notify_website_of_promotion,
    # and all the activityset commands are unchanged.
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
        guild_settings = await self.config.guild(guild).all()
        recruit_role_id = guild_settings.get("recruit_role_id")
        member_role_id = guild_settings.get("member_role_id")
        promotion_threshold_hours = guild_settings.get("promotion_threshold_hours")
        if not all([recruit_role_id, member_role_id, promotion_threshold_hours]): return
        promotion_threshold_minutes = promotion_threshold_hours * 60
        recruit_role = guild.get_role(recruit_role_id)
        if recruit_role and recruit_role in member.roles and total_minutes >= promotion_threshold_minutes:
            member_role = guild.get_role(member_role_id)
            if not member_role:
                print(f"PROMOTION ERROR: Member role with ID {member_role_id} not found.")
                return
            print(f"PROMOTION: Promoting user {member.name} to Member...")
            try:
                await member.remove_roles(recruit_role, reason="Automatic promotion via voice activity")
                await member.add_roles(member_role, reason="Automatic promotion via voice activity")
                await self._notify_website_of_promotion(guild, member.id, "member")
                channel_id = guild_settings.get("promotion_channel_id")
                if channel_id:
                    channel = guild.get_channel(channel_id)
                    if channel:
                        await channel.send(f"🎉 Congratulations {member.mention}! You've been promoted to **Member** status after accumulating {total_minutes / 60:.1f} hours of voice activity!")
            except discord.Forbidden:
                print(f"PROMOTION ERROR: Missing permissions to promote {member.name}.")
            except Exception as e:
                print(f"PROMOTION ERROR: An unexpected error occurred for {member.name}: {e}")

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

