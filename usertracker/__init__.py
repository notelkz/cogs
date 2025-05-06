from .usertracker import UserTracker

__red_end_user_data_statement__ = "This cog stores user voice time and message counts for tracking purposes."

def setup(bot):
    cog = UserTracker(bot)
    bot.add_cog(cog)
    bot.loop.create_task(cog.initialize())
