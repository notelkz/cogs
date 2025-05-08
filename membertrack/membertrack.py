from redbot.core import commands, Config
from aiohttp import web

class MemberCount(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.webserver = None

    async def cog_load(self):
        # Start webserver on cog load
        self.webserver = web.Application()
        self.webserver.router.add_get('/membercount', self.handle_membercount)
        runner = web.AppRunner(self.webserver)
        await runner.setup()
        # Use a port that's open on your server (e.g., 8080)
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()

    async def handle_membercount(self, request):
        # Replace with your guild ID
        guild = self.bot.get_guild(995753617611042916)
        if guild:
            return web.json_response({"member_count": guild.member_count})
        else:
            return web.json_response({"error": "Guild not found"}, status=404)

async def setup(bot):
    await bot.add_cog(MemberCount(bot))
