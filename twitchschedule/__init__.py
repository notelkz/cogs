from redbot.core.bot import Red
from .twitchschedule import TwitchSchedule

async def setup(bot: Red):
    cog = TwitchSchedule(bot)
    await bot.add_cog(cog)
