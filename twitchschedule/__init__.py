from redbot.core import commands
from .twitchschedule import TwitchSchedule

async def setup(bot):
    await bot.add_cog(TwitchSchedule(bot))
