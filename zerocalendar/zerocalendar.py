# zerocalendar/zerocalendar.py

import discord # Essential for Redbot cogs
from discord.ext import commands # CRITICAL: This is where commands.Cog comes from
import logging # For logging

log = logging.getLogger("red.zerocogs.zerocalendar")

class ZeroCalendar(commands.Cog): # This must correctly inherit commands.Cog
    """
    Calendar integration for Zero Lives Left (Absolute minimum version for debugging).
    """
    
    def __init__(self, bot):
        self.bot = bot
        log.info("ZeroCalendar cog is initializing (absolute minimum version).")
    
    def cog_unload(self):
        log.info("ZeroCalendar cog is unloading (absolute minimum version).")
        pass

    @commands.command(name="testcalendar")
    async def test_calendar(self, ctx):
        """Tests if the calendar cog is loaded."""
        await ctx.send("Calendar cog is loaded and ready! This is the absolute minimum version.")

# This setup function is what Redbot calls to load the cog
def setup(bot):
    bot.add_cog(ZeroCalendar(bot))
    log.info("ZeroCalendar setup function called.")