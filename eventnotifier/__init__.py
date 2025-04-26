from .eventnotifier import EventNotifier

async def setup(bot):
    cog = EventNotifier(bot)
    await cog.initialize()
    await bot.add_cog(cog)
