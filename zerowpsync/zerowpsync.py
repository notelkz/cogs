import aiohttp
from redbot.core import commands, Config
from discord.ext import tasks

class ZeroWPSync(commands.Cog):
    """Sync Discord roles to WordPress"""

    def __init__(self, bot):
        self.bot = bot
        self.wp_url = "http://zerolivesleft.net/z/discord-webhook"
        self.secret = "FqCE9Y6tOQ6TBciw01M5GyRj8vp4Crbb"  # Match .env

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.roles == after.roles:
            return
        roles = [r.id for r in after.roles if r.id != after.guild.id]
        payload = {
            "discord_id": str(after.id),
            "roles": roles
        }
        headers = {"X-ZeroDiscord-Secret": self.secret}
        async with aiohttp.ClientSession() as session:
            async with session.post(self.wp_url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    print(f"Failed to sync roles for {after.id}: {resp.status}")

async def setup(bot):
    bot.add_cog(ZeroWPSync(bot))
