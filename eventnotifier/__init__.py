from .eventnotifier import EventNotifier

async def setup(bot):
    await bot.add_cog(EventNotifier(bot))
