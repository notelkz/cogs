from .zerocalendar import ZeroCalendar

async def setup(bot):
    await bot.add_cog(ZeroCalendar(bot))
