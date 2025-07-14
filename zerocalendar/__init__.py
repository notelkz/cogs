async def setup(bot):
    from .zerocalendar import ZeroCalendar
    await bot.add_cog(ZeroCalendar(bot))