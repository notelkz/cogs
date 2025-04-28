from .membertracker import MemberTracker

async def setup(bot):
    await bot.add_cog(MemberTracker(bot))
