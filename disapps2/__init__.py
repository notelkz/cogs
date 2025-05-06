from .disappstest import Disappstest

async def setup(bot):
    cog = Disappstest(bot)
    await bot.add_cog(cog)
