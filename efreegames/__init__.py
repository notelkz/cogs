from .efreegames import EFreeGames

async def setup(bot):
    await bot.add_cog(EFreeGames(bot))
