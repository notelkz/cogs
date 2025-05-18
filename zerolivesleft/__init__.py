from .main import ZeroLivesLeft 
from .wp_reports import WPReports

async def setup(bot):
    cog = ZeroLivesLeft(bot)
    await bot.add_cog(cog)
    cog = WPReports(bot)
    await bot.add_cog(cog)