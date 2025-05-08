from redbot.core import commands
from aiohttp import web

GUILD_ID = 995753617611042916  # Your Guild ID

class MemberCount(commands.Cog):
    """Expose member count via HTTP endpoint."""

    def __init__(self, bot):
        self.bot = bot
        self.webserver = None
        self.runner = None
        self.site = None

    async def cog_load(self):
        # Start webserver on cog load
        self.webserver = web.Application()
        self.webserver.router.add_get('/membercount', self.handle_membercount)
        self.runner = web.AppRunner(self.webserver)
        await self.runner.setup()
        # Use a port that's open on your server (e.g., 8080)
        self.site = web.TCPSite(self.runner, '0.0.0.0', 8081)
        await self.site.start()

    async def cog_unload(self):
        # Properly close the webserver
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()

    async def handle_membercount(self, request):
        guild = self.bot.get_guild(GUILD_ID)
        headers = {
            "Access-Control-Allow-Origin": "*",  # Or set to your domain for more security
            "Access-Control-Allow-Methods": "GET",
            "Access-Control-Allow-Headers": "Content-Type",
        }
        if guild:
            return web.json_response({"member_count": guild.member_count}, headers=headers)
        else:
            return web.json_response({"error": "Guild not found"}, status=404, headers=headers)
