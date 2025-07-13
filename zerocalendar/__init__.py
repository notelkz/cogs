# zerocalendar/__init__.py

from .zerocalendar import ZeroCalendar

def setup(bot):
    bot.add_cog(ZeroCalendar(bot))
