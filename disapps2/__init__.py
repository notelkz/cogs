from .disappstest import DisappsTest

async def setup(bot):
    await bot.add_cog(DisappsTest(bot))
