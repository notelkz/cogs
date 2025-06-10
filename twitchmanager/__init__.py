from .twitchmanager import TwitchManager

async def setup(bot):
    await bot.add_cog(TwitchManager(bot))
