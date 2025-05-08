from .zeroapplications import ZeroApplications

async def setup(bot):
    await bot.add_cog(ZeroApplications(bot))
