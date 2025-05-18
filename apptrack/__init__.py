from .apptrack import AppTrack

async def setup(bot):
    await bot.add_cog(AppTrack(bot))
