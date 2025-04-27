from .applications import Applications

async def setup(bot):
    await bot.add_cog(Applications(bot))
