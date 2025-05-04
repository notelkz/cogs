from redbot.core import commands, Config
from redbot.core.bot import Red
from aiohttp import web

class WPDiscordSync(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.guild_id = 995753617611042916  # Replace with your Discord server ID

    async def red_webserver_get_routes(self):
        return [
            web.get("/api/roles", self.get_roles),
            web.get("/api/member_roles/{discord_id}", self.get_member_roles)
        ]

    async def get_roles(self, request):
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return web.json_response({"error": "Guild not found"}, status=404)
        roles = [{"id": str(role.id), "name": role.name} for role in guild.roles if not role.is_default()]
        return web.json_response(roles)

    async def get_member_roles(self, request):
        discord_id = int(request.match_info["discord_id"])
        guild = self.bot.get_guild(self.guild_id)
        if not guild:
            return web.json_response({"error": "Guild not found"}, status=404)
        member = guild.get_member(discord_id)
        if not member:
            return web.json_response({"error": "Member not found"}, status=404)
        roles = [str(role.id) for role in member.roles if not role.is_default()]
        return web.json_response(roles)

def setup(bot):
    bot.add_cog(WPDiscordSync(bot))
