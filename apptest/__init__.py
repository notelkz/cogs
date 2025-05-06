from .apptest import AppTest

async def setup(bot):
    await bot.add_cog(AppTest(bot))
