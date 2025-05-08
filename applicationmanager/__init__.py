from .applicationmanager import ApplicationManager

async def setup(bot):
    await bot.add_cog(ApplicationManager(bot))
