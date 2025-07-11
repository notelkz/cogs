import discord
import asyncio
import aiohttp
import os
import json
from datetime import datetime

from redbot.core import commands, Config
from aiohttp import web
from redbot.core.utils.chat_formatting import humanize_list
from redbot.core.utils.views import ConfirmView

import logging

log = logging.getLogger("red.activitytracker")

class ActivityTracker(commands.Cog):
    """
    Tracks user voice activity, handles Discord role promotions (Recruit/Member, Military Ranks),
    and exposes an API for a Django website to query member initial role assignment and military rank definitions.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        default_guild = {
            "user_activity": {},  # user_id: total_minutes
            "recruit_role_id": None,
            "member_role_id": None,
            "promotion_threshold_hours": 24.0,
            "military_ranks": [],  # list of dicts: {name, discord_role_id, required_hours}
            "api_url": None,
            "api_key": None,
            "promotion_update_url": None,
            "promotion_channel_id": None,
        }
        self.config.register_guild(**default_guild)
        self.voice_tracking = {}  # guild_id: {user_id: join_time}
        self.session = aiohttp.ClientSession()

        # Web API
        self.web_app = web.Application()
        self.web_runner = None
        self.web_site = None
        self.web_app.router.add_post("/api/assign_initial_role", self.assign_initial_role_handler)
        self.web_app.router.add_get("/api/get_military_ranks", self.get_military_ranks_handler)
        self.web_app.router.add_get("/health", self.health_check_handler)
        self.bot.loop.create_task(self.initialize_webserver())

    async def initialize_webserver(self):
        await self.bot.wait_until_ready()
        guild_id_str = os.environ.get("DISCORD_GUILD_ID")
        if not guild_id_str:
            log.critical("DISCORD_GUILD_ID environment variable not set. Web API will not function.")
            return
        guild = self.bot.get_guild(int(guild_id_str))
        if not guild:
            log.critical(f"Guild with ID {guild_id_str} not found. Web API will not function.")
            return
        self.web_app["guild"] = guild
        try:
            self.web_runner = web.AppRunner(self.web_app)
            await self.web_runner.setup()
            host = os.environ.get("ACTIVITY_WEB_HOST", "0.0.0.0")
            port = int(os.environ.get("ACTIVITY_WEB_PORT", 5002))
            self.web_site = web.TCPSite(self.web_runner, host, port)
            await self.web_site.start()
            log.info(f"ActivityTracker API server started on http://{host}:{port}/")
        except Exception as e:
            log.critical(f"Failed to start ActivityTracker web API server: {e}")
            self.web_runner = None
            self.web_site = None

    def cog_unload(self):
        if self.web_runner:
            asyncio.create_task(self._shutdown_web_server())
        asyncio.create_task(self.session.close())

    async def _shutdown_web_server(self):
        if self.web_runner:
            log.info("Shutting down ActivityTracker web API server...")
            try:
                await self.web_app.shutdown()
                await self.web_runner.cleanup()
                log.info("ActivityTracker web API server shut down successfully.")
            except Exception as e:
                log.error(f"Error during web API server shutdown: {e}")
        self.web_runner = None
        self.web_site = None

    async def _authenticate_web_request(self, request: web.Request):
        guild = request.app["guild"]
        expected_key = await self.config.guild(guild).api_key()
        if not expected_key:
            raise web.HTTPUnauthorized(reason="Web API Key not configured on RedBot for this guild.")
        provided_key = request.headers.get("X-API-Key")
        if not provided_key:
            raise web.HTTPUnauthorized(reason="X-API-Key header missing.")
        if provided_key != expected_key:
            raise web.HTTPForbidden(reason="Invalid API Key.")
        return True

    async def health_check_handler(self, request: web.Request):
        return web.Response(text="OK", status=200)

    async def assign_initial_role_handler(self, request):
        try:
            await self._authenticate_web_request(request)
        except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
            return e
        try:
            data = await request.json()
            discord_id = int(data.get("discord_id"))
        except (ValueError, TypeError, json.JSONDecodeError):
            return web.Response(text="Invalid request data", status=400)
        guild = request.app["guild"]
        recruit_role_id = await self.config.guild(guild).recruit_role_id()
        if not recruit_role_id:
            return web.Response(text="Recruit role not configured", status=500)
        member = guild.get_member(discord_id)
        recruit_role = guild.get_role(recruit_role_id)
        if member and recruit_role:
            try:
                if recruit_role not in member.roles:
                    await member.add_roles(recruit_role, reason="Initial role assignment from website.")
                return web.Response(text="Role assigned/already present successfully", status=200)
            except discord.Forbidden:
                return web.Response(text="Missing permissions", status=503)
            except Exception:
                return web.Response(text="Internal server error", status=500)
        else:
            return web.Response(text="Member or role not found", status=404)

    async def get_military_ranks_handler(self, request):
        try:
            await self._authenticate_web_request(request)
        except (web.HTTPUnauthorized, web.HTTPForbidden) as e:
            return e
        guild = request.app["guild"]
        military_ranks = await self.config.guild(guild).military_ranks()
        if not military_ranks:
            return web.json_response([], status=200)
        try:
            sorted_ranks = sorted(
                [r for r in military_ranks if 'required_hours' in r and isinstance(r['required_hours'], (int, float))],
                key=lambda x: x['required_hours']
            )
        except Exception:
            return web.Response(text="Internal Server Error: Malformed rank data", status=500)
        return web.json_response(sorted_ranks)

    # --- VOICE TRACKING ---

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        guild = member.guild
        guild_id = guild.id
        user_id = member.id
        if guild_id not in self.voice_tracking:
            self.voice_tracking[guild_id] = {}
        # Joined voice
        if before.channel is None and after.channel is not None:
            self.voice_tracking[guild_id][user_id] = datetime.utcnow()
        # Left voice
        elif before.channel is not None and after.channel is None:
            if user_id in self.voice_tracking[guild_id]:
                join_time = self.voice_tracking[guild_id][user_id]
                minutes = int((datetime.utcnow() - join_time).total_seconds() / 60)
                if minutes >= 1:
                    await self._update_user_voice_minutes(guild, member, minutes)
                del self.voice_tracking[guild_id][user_id]

    async def _update_user_voice_minutes(self, guild, member, minutes_to_add):
        async with self.config.guild(guild).user_activity() as user_activity:
            uid = str(member.id)
            user_activity[uid] = user_activity.get(uid, 0) + minutes_to_add
        asyncio.create_task(self._update_website_activity(guild, member, minutes_to_add))
        total_minutes = await self._get_user_voice_minutes(guild, member.id)
        await self._check_for_promotion(guild, member, total_minutes)

    async def _get_user_voice_minutes(self, guild, user_id):
        user_activity = await self.config.guild(guild).user_activity()
        total_minutes = user_activity.get(str(user_id), 0)
        guild_id = guild.id
        if guild_id in self.voice_tracking and user_id in self.voice_tracking[guild_id]:
            join_time = self.voice_tracking[guild_id][user_id]
            current_session_minutes = int((datetime.utcnow() - join_time).total_seconds() / 60)
            if current_session_minutes >= 1:
                total_minutes += current_session_minutes
        return total_minutes

    # --- DJANGO SYNC ---

    async def _update_website_activity(self, guild, member, minutes_to_add):
        api_url = await self.config.guild(guild).api_url()
        api_key = await self.config.guild(guild).api_key()
        if not api_url or not api_key:
            return
        endpoint = f"{api_url}/api/update_activity/"
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {"discord_id": str(member.id), "voice_minutes": minutes_to_add}
        try:
            async with self.session.post(endpoint, headers=headers, json=payload, timeout=10) as resp:
                if resp.status != 200:
                    log.warning(f"Failed to update Django: {resp.status} {await resp.text()}")
        except Exception as e:
            log.warning(f"Failed to update Django: {e}")

    async def _notify_website_of_promotion(self, guild, discord_id, new_role_name):
        promotion_update_url = await self.config.guild(guild).promotion_update_url()
        api_key = await self.config.guild(guild).api_key()
        if not promotion_update_url or not api_key:
            return
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {"discord_id": str(discord_id), "new_role_name": new_role_name}
        try:
            async with self.session.post(promotion_update_url, headers=headers, json=payload, timeout=5) as resp:
                if resp.status != 200:
                    log.warning(f"Failed to notify Django of promotion: {resp.status} {await resp.text()}")
        except Exception as e:
            log.warning(f"Failed to notify Django of promotion: {e}")

    # --- PROMOTION LOGIC ---

    async def _check_for_promotion(self, guild, member, total_minutes):
        # Recruit -> Member
        recruit_role_id = await self.config.guild(guild).recruit_role_id()
        member_role_id = await self.config.guild(guild).member_role_id()
        threshold_hours = await self.config.guild(guild).promotion_threshold_hours()
        if recruit_role_id and member_role_id and threshold_hours:
            recruit_role = guild.get_role(recruit_role_id)
            member_role = guild.get_role(member_role_id)
            if recruit_role and member_role and recruit_role in member.roles:
                if total_minutes >= threshold_hours * 60:
                    await member.remove_roles(recruit_role, reason="Promotion")
                    await member.add_roles(member_role, reason="Promotion")
                    await self._notify_website_of_promotion(guild, member.id, member_role.name)
                    channel_id = await self.config.guild(guild).promotion_channel_id()
                    if channel_id:
                        channel = guild.get_channel(channel_id)
                        if channel:
                            await channel.send(
                                f"🎉 Congratulations {member.mention}! You've been promoted to **{member_role.name}** status!"
                            )
        # Military Ranks
        military_ranks = await self.config.guild(guild).military_ranks()
        if not military_ranks:
            return
        sorted_ranks = sorted(
            [r for r in military_ranks if isinstance(r.get('required_hours'), (int, float))],
            key=lambda x: x['required_hours'],
            reverse=True
        )
        user_hours = total_minutes / 60
        for rank in sorted_ranks:
            if user_hours >= rank['required_hours']:
                role = guild.get_role(int(rank['discord_role_id']))
                if role and role not in member.roles:
                    all_rank_ids = [int(r['discord_role_id']) for r in military_ranks if 'discord_role_id' in r]
                    remove_roles = [r for r in member.roles if r.id in all_rank_ids]
                    await member.remove_roles(*remove_roles, reason="Rank promotion")
                    await member.add_roles(role, reason="Rank promotion")
                    await self._notify_website_of_promotion(guild, member.id, rank['name'])
                    channel_id = await self.config.guild(guild).promotion_channel_id()
                    if channel_id:
                        channel = guild.get_channel(channel_id)
                        if channel:
                            await channel.send(
                                f"🎖️ Bravo, {member.mention}! You've achieved the rank of **{rank['name']}**!"
                            )
                break

    # --- COMMANDS ---

    @commands.command()
    async def myvoicetime(self, ctx):
        """Shows your total accumulated voice time."""
        total_minutes = await self._get_user_voice_minutes(ctx.guild, ctx.author.id)
        hours = total_minutes // 60
        minutes = total_minutes % 60
        await ctx.send(f"Your total voice time is {hours} hours and {minutes} minutes.")

    @commands.command(name="status")
    async def status(self, ctx, member: discord.Member = None):
        """Show your (or another's) voice time and promotion progress."""
        target = member or ctx.author
        total_minutes = await self._get_user_voice_minutes(ctx.guild, target.id)
        hours = total_minutes // 60
        minutes = total_minutes % 60
        embed = discord.Embed(
            title=f"Activity Status for {target.display_name}",
            color=target.color
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(
            name="Voice Activity",
            value=f"**{hours}** hours and **{minutes}** minutes",
            inline=False
        )
        # Membership progress
                recruit_role_id = await self.config.guild(ctx.guild).recruit_role_id()
        member_role_id = await self.config.guild(ctx.guild).member_role_id()
        threshold_hours = await self.config.guild(ctx.guild).promotion_threshold_hours()
        if recruit_role_id and member_role_id and threshold_hours:
            recruit_role = ctx.guild.get_role(recruit_role_id)
            member_role = ctx.guild.get_role(member_role_id)
            if recruit_role and member_role:
                if member_role in target.roles:
                    embed.add_field(
                        name="Membership Status",
                        value=f"✅ Full Member ({member_role.mention})",
                        inline=False
                    )
                elif recruit_role in target.roles:
                    threshold_minutes = threshold_hours * 60
                    progress = min(100, (total_minutes / threshold_minutes) * 100)
                    remaining_minutes = max(0, threshold_minutes - total_minutes)
                    remaining_hours = remaining_minutes / 60
                    progress_bar = self._generate_progress_bar(progress)
                    embed.add_field(
                        name="Membership Progress",
                        value=(
                            f"{recruit_role.mention} → {member_role.mention}\n"
                            f"{progress_bar} **{progress:.1f}%**\n"
                            f"Remaining: **{remaining_hours:.1f}** hours"
                        ),
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="Membership Status",
                        value="Not in membership track (missing Recruit role)",
                        inline=False
                    )
        # Military Rank progress
        military_ranks = await self.config.guild(ctx.guild).military_ranks()
        if military_ranks:
            try:
                sorted_ranks = sorted(
                    [r for r in military_ranks if isinstance(r.get('required_hours'), (int, float))],
                    key=lambda x: x['required_hours']
                )
                current_rank = None
                next_rank = None
                user_rank_ids = {
                    role.id for role in target.roles
                    if any(str(role.id) == str(r.get('discord_role_id')) for r in military_ranks)
                }
                if user_rank_ids:
                    user_ranks = [r for r in sorted_ranks if str(r.get('discord_role_id')) in map(str, user_rank_ids)]
                    if user_ranks:
                        current_rank = max(user_ranks, key=lambda x: x['required_hours'])
                if current_rank:
                    higher_ranks = [r for r in sorted_ranks if r['required_hours'] > current_rank['required_hours']]
                    if higher_ranks:
                        next_rank = min(higher_ranks, key=lambda x: x['required_hours'])
                else:
                    if sorted_ranks:
                        next_rank = sorted_ranks[0]
                if current_rank:
                    current_role_id = current_rank.get('discord_role_id')
                    current_role = ctx.guild.get_role(int(current_role_id)) if current_role_id else None
                    embed.add_field(
                        name="Current Military Rank",
                        value=(
                            f"**{current_rank.get('name')}**\n"
                            f"{current_role.mention if current_role else 'Role not found'}\n"
                            f"Required: {current_rank.get('required_hours')} hours"
                        ),
                        inline=False
                    )
                if next_rank:
                    next_role_id = next_rank.get('discord_role_id')
                    next_role = ctx.guild.get_role(int(next_role_id)) if next_role_id else None
                    current_hours = current_rank.get('required_hours', 0) if current_rank else 0
                    next_hours = next_rank.get('required_hours', 0)
                    if next_hours > current_hours:
                        progress = min(100, ((hours - current_hours) / (next_hours - current_hours)) * 100)
                        remaining_hours = max(0, next_hours - hours)
                        progress_bar = self._generate_progress_bar(progress)
                        embed.add_field(
                            name="Next Military Rank",
                            value=(
                                f"**{next_rank.get('name')}**\n"
                                f"{next_role.mention if next_role else 'Role not found'}\n"
                                f"{progress_bar} **{progress:.1f}%**\n"
                                f"Remaining: **{remaining_hours:.1f}** hours"
                            ),
                            inline=False
                        )
                    else:
                        embed.add_field(
                            name="Next Military Rank",
                            value="You have reached the highest rank! 🎖️",
                            inline=False
                        )
                elif current_rank:
                    embed.add_field(
                        name="Next Military Rank",
                        value="You have reached the highest rank! 🎖️",
                        inline=False
                    )
                else:
                    embed.add_field(
                        name="Military Rank",
                        value="No military ranks configured or eligible",
                        inline=False
                    )
            except Exception as e:
                embed.add_field(
                    name="Military Rank Error",
                    value=f"An error occurred processing military ranks: {str(e)}",
                    inline=False
                )
        await ctx.send(embed=embed)

    def _generate_progress_bar(self, percent, length=10):
        filled_length = int(length * percent / 100)
        bar = '█' * filled_length + '░' * (length - filled_length)
        return f"[{bar}]"

    # --- ADMIN/CONFIG COMMANDS ---

    @commands.group(name="activityset")
    @commands.admin_or_permissions(manage_guild=True)
    async def activityset(self, ctx):
        """Manage ActivityTracker settings."""
        pass

    @activityset.command()
    async def roles(self, ctx, recruit: discord.Role, member: discord.Role):
        await self.config.guild(ctx.guild).recruit_role_id.set(recruit.id)
        await self.config.guild(ctx.guild).member_role_id.set(member.id)
        await ctx.send("Roles set.")

    @activityset.command()
    async def threshold(self, ctx, hours: float):
        await self.config.guild(ctx.guild).promotion_threshold_hours.set(hours)
        await ctx.send("Threshold set.")

    @activityset.command()
    async def api(self, ctx, url: str, key: str):
        await self.config.guild(ctx.guild).api_url.set(url)
        await self.config.guild(ctx.guild).api_key.set(key)
        await ctx.send("API settings saved.")

    @activityset.command()
    async def promotionurl(self, ctx, url: str):
        await self.config.guild(ctx.guild).promotion_update_url.set(url)
        await ctx.send("Promotion update URL set.")

    @activityset.command()
    async def promotionchannel(self, ctx, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).promotion_channel_id.set(channel.id)
        await ctx.send(f"Promotion notification channel set to {channel.mention}.")

    @activityset.group()
    async def militaryranks(self, ctx):
        """Manage military ranks."""
        pass

    @militaryranks.command(name="add")
    async def add_rank(self, ctx, role: discord.Role, required_hours: float):
        async with self.config.guild(ctx.guild).military_ranks() as ranks:
            ranks.append({
                "name": role.name,
                "discord_role_id": str(role.id),
                "required_hours": required_hours
            })
        await ctx.send(f"Added {role.name} at {required_hours} hours.")

    @militaryranks.command(name="clear")
    async def clear_ranks(self, ctx):
        await self.config.guild(ctx.guild).military_ranks.set([])
        await ctx.send("Ranks cleared.")

    @militaryranks.command(name="list")
    async def list_ranks(self, ctx):
        ranks = await self.config.guild(ctx.guild).military_ranks()
        if not ranks:
            await ctx.send("No ranks set.")
            return
        msg = "\n".join(f"{r['name']}: {r['required_hours']}h (role {r['discord_role_id']})" for r in ranks)
        await ctx.send(msg)

    @activityset.command(name="settings")
    async def show_settings(self, ctx):
        """Shows the current ActivityTracker settings."""
        settings = await self.config.guild(ctx.guild).all()
        embed = discord.Embed(
            title="ActivityTracker Settings",
            color=discord.Color.blue()
        )
        api_url = settings.get("api_url")
        api_key = settings.get("api_key")
        promotion_url = settings.get("promotion_update_url")
        embed.add_field(
            name="API Configuration",
            value=(
                f"API URL: `{api_url or 'Not set'}`\n"
                f"API Key: `{'✓ Set' if api_key else '✗ Not set'}`\n"
                f"Promotion URL: `{promotion_url or 'Not set'}`"
            ),
            inline=False
        )
        recruit_role_id = settings.get("recruit_role_id")
        member_role_id = settings.get("member_role_id")
        recruit_role = ctx.guild.get_role(recruit_role_id) if recruit_role_id else None
        member_role = ctx.guild.get_role(member_role_id) if member_role_id else None
        embed.add_field(
            name="Role Configuration",
            value=(
                f"Recruit Role: {recruit_role.mention if recruit_role else '`Not set`'}\n"
                f"Member Role: {member_role.mention if member_role else '`Not set`'}\n"
                f"Promotion Threshold: `{settings.get('promotion_threshold_hours')} hours`"
            ),
            inline=False
        )
        channel_id = settings.get("promotion_channel_id")
        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        embed.add_field(
            name="Notification Settings",
            value=f"Promotion Channel: {channel.mention if channel else '`Not set`'}",
            inline=False
        )
        military_ranks = settings.get("military_ranks", [])
        valid_ranks = [r for r in military_ranks if 'discord_role_id' in r and ctx.guild.get_role(int(r['discord_role_id']))]
        embed.add_field(
            name="Military Ranks",
            value=(
                f"Total Configured: `{len(military_ranks)}`\n"
                f"Valid Ranks: `{len(valid_ranks)}`\n"
                f"Use `{ctx.prefix}activityset militaryranks list` for details"
            ),
            inline=False
        )
        await ctx.send(embed=embed)

    # --- DEBUG/UTILITY ---

    @activityset.command(name="debug")
    @commands.is_owner()
    async def debug_info(self, ctx):
        """Shows debug information about the ActivityTracker cog."""
        embed = discord.Embed(
            title="ActivityTracker Debug Information",
            color=discord.Color.gold()
        )
        web_status = "Running" if self.web_runner and self.web_site else "Not running"
        host = os.environ.get("ACTIVITY_WEB_HOST", "0.0.0.0")
        port = os.environ.get("ACTIVITY_WEB_PORT", "5002")
        embed.add_field(
            name="Web API Server",
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
        guild_id_env = os.environ.get("DISCORD_GUILD_ID", "Not set")
        embed.add_field(
            name="Environment Variables",
            value=f"DISCORD_GUILD_ID: {guild_id_env}\nACTIVITY_WEB_HOST: {host}\nACTIVITY_WEB_PORT: {port}",
            inline=False
        )
        embed.set_footer(text=f"ActivityTracker Cog | Discord.py {discord.__version__}")
        await ctx.send(embed=embed)

def setup(bot):
    bot.add_cog(ActivityTracker(bot))
