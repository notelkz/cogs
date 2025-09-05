from .twitchschedule import TwitchSchedule

async def setup(bot):
    """Entry point for Red-DiscordBot cog loading"""
    cog = TwitchSchedule(bot)
    await bot.add_cog(cog)