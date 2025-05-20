from .twitchannouncer import TwitchAnnouncer

async def setup(bot):
    await bot.add_cog(TwitchAnnouncer(bot))
