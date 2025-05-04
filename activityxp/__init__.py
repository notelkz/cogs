from .activityxp import ActivityXP

async def setup(bot):
    await bot.add_cog(ActivityXP(bot))
