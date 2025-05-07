from .gameupdates import GameUpdates

async def setup(bot):
    await bot.add_cog(GameUpdates(bot))
