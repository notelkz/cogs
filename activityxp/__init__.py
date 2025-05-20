from .xpleaderboard import XPLeaderboard

async def setup(bot):  # Make sure this is async
    cog = XPLeaderboard(bot)
    await bot.add_cog(cog)
