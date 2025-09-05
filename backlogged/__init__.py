from .twitchschedule import TwitchSchedule

async def setup(bot):
    """Entry point for Red-DiscordBot cog loading"""
    await bot.add_cog(TwitchSchedule(bot))