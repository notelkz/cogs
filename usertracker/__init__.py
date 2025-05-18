from .usertracker import UserTracker

async def setup(bot):
    await bot.add_cog(UserTracker(bot))
