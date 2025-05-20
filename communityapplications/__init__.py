from .communityapplications import CommunityApplications

async def setup(bot):
    cog = CommunityApplications(bot)
    await bot.add_cog(cog)
