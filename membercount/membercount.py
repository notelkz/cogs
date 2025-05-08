from redbot.core import commands, data_manager
from aiohttp import web
import aiohttp_cors
from datetime import datetime
import json
import os

GUILD_ID = 995753617611042916  # Your Guild ID
ROLE_ID = 1018116224158273567  # The role you want to track

DATA_PATH = data_manager.cog_data_path(__file__)
os.makedirs(DATA_PATH, exist_ok=True)
VOICE_DATA_FILE = os.path.join(DATA_PATH, "voice_data.json")
MESSAGE_DATA_FILE = os.path.join(DATA_PATH, "message_data.json")

class MemberCount(commands.Cog):
    """Expose member count, role count, voice minutes, message count, and application stats via HTTP endpoints."""

    def __init__(self, bot):
        self.bot = bot
        self.webserver = None
        self.runner = None
        self.site = None

        # Voice tracking
        self.voice_sessions = {}  # user_id: join_time
        self.voice_minutes = []   # list of [user_id, join_time, leave_time]
        self._load_voice_data()

        # Message tracking
        self.message_log = []  # list of [timestamp, user_id]
        self._load_message_data()

    # --- Persistence ---
    def _load_voice_data(self):
        if os.path.exists(VOICE_DATA_FILE):
            with open(VOICE_DATA_FILE, "r") as f:
                self.voice_minutes = json.load(f)
        else:
            self.voice_minutes = []

    def _save_voice_data(self):
        with open(VOICE_DATA_FILE, "w") as f:
            json.dump(self.voice_minutes, f)

    def _load_message_data(self):
        if os.path.exists(MESSAGE_DATA_FILE):
            with open(MESSAGE_DATA_FILE, "r") as f:
                self.message_log = json.load(f)
        else:
            self.message_log = []

    def _save_message_data(self):
        with open(MESSAGE_DATA_FILE, "w") as f:
            json.dump(self.message_log, f)

    # --- Red events ---
    async def cog_load(self):
        self.webserver = web.Application()
        # Register all your routes
        self.webserver.router.add_get('/membercount', self.handle_membercount)
        self.webserver.router.add_get('/messagecount', self.handle_messagecount)
        self.webserver.router.add_get('/voiceminutes', self.handle_voiceminutes)
        self.webserver.router.add_get('/rolecount', self.handle_rolecount)
        self.webserver.router.add_get('/rolevoiceminutes', self.handle_rolevoiceminutes)
        self.webserver.router.add_get('/appstats', self.handle_appstats)

        # --- CORS setup ---
        cors = aiohttp_cors.setup(
            self.webserver,
            defaults={
                "https://notelkz.net": aiohttp_cors.ResourceOptions(
                allow_credentials=True,
                expose_headers="*",
                allow_headers="*",
        )
    },
)
                # If you want to restrict to your domain only, use:
                # "https://notelkz.net": aiohttp_cors.ResourceOptions(
                #     allow_credentials=True,
                #     expose_headers="*",
                #     allow_headers="*",
                # )
            },
        )

        # Apply CORS to all routes
        for route in list(self.webserver.router.routes()):
            cors.add(route)

        self.runner = web.AppRunner(self.webserver)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '0.0.0.0', 8081)  # Use your chosen port
        await self.site.start()

    async def cog_unload(self):
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()

    # --- Endpoints ---
    async def handle_membercount(self, request):
        guild = self.bot.get_guild(GUILD_ID)
        if guild:
            return web.json_response({"member_count": guild.member_count})
        else:
            return web.json_response({"error": "Guild not found"}, status=404)

    async def handle_messagecount(self, request):
        now = datetime.utcnow().timestamp()
        week_ago = now - 7 * 24 * 60 * 60
        count = sum(1 for ts, _ in self.message_log if ts >= week_ago)
        return web.json_response({"messages_7d": count})

    async def handle_voiceminutes(self, request):
        now = datetime.utcnow().timestamp()
        week_ago = now - 7 * 24 * 60 * 60
        total_minutes = 0
        for entry in self.voice_minutes:
            user_id, join_time, leave_time = entry
            if leave_time >= week_ago:
                start = max(join_time, week_ago)
                end = leave_time
                total_minutes += (end - start) / 60
        return web.json_response({"voice_minutes_7d": int(total_minutes)})

    async def handle_rolecount(self, request):
        guild = self.bot.get_guild(GUILD_ID)
        params = request.rel_url.query
        role_id = params.get("role_id")
        if not role_id:
            return web.json_response({"error": "Missing role_id"}, status=400)
        try:
            role_id = int(role_id)
        except ValueError:
            return web.json_response({"error": "Invalid role_id"}, status=400)
        role = guild.get_role(role_id)
        if not role:
            return web.json_response({"error": "Role not found"}, status=404)
        count = len(role.members)
        return web.json_response({"role_member_count": count})

    async def handle_rolevoiceminutes(self, request):
        guild = self.bot.get_guild(GUILD_ID)
        if not guild:
            return web.json_response({"error": "Guild not found"}, status=404)
        role = guild.get_role(ROLE_ID)
        if not role:
            return web.json_response({"error": "Role not found"}, status=404)
        role_member_ids = {m.id for m in role.members}
        member_count = len(role_member_ids)

        now = datetime.utcnow().timestamp()
        week_ago = now - 7 * 24 * 60 * 60
        total_minutes = 0
        for user_id, join_time, leave_time in self.voice_minutes:
            if user_id in role_member_ids and leave_time >= week_ago:
                start = max(join_time, week_ago)
                end = leave_time
                total_minutes += (end - start) / 60

        return web.json_response({
            "role_member_count": member_count,
            "role_voice_minutes_7d": int(total_minutes)
        })

    async def handle_appstats(self, request):
        zeroapps = self.bot.get_cog("ZeroApplications")
        if not zeroapps:
            return web.json_response({"error": "ZeroApplications cog not loaded"}, status=500)
        try:
            all_apps = zeroapps.applications  # {guild_id: {member_id: application_dict}}
            guild_apps = all_apps.get(GUILD_ID) or all_apps.get(str(GUILD_ID), {})
            app_list = list(guild_apps.values())
        except Exception as e:
            return web.json_response({"error": f"Failed to fetch applications: {e}"}, status=500)
        total = len(app_list)
        accepted = sum(1 for app in app_list if app.get("status") == "accepted")
        rejected = sum(1 for app in app_list if app.get("status") == "rejected")
        return web.json_response({
            "total": total,
            "accepted": accepted,
            "rejected": rejected
        })

    # --- Listeners ---
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.guild.id != GUILD_ID:
            return
        now = datetime.utcnow().timestamp()
        if before.channel is None and after.channel is not None:
            # Joined voice
            self.voice_sessions[member.id] = now
        elif before.channel is not None and after.channel is None:
            # Left voice
            join_time = self.voice_sessions.pop(member.id, None)
            if join_time:
                self.voice_minutes.append([member.id, join_time, now])
                self._save_voice_data()

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild and message.guild.id == GUILD_ID and not message.author.bot:
            now = datetime.utcnow().timestamp()
            self.message_log.append([now, message.author.id])
            self._save_message_data()

async def setup(bot):
    await bot.add_cog(MemberCount(bot))
