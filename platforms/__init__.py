from .platforms import Platforms

async def setup(bot):
    await bot.add_cog(Platforms(bot))
