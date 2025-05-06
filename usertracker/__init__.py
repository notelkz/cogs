from .usertracker import UserTracker

__red_end_user_data_statement__ = "This cog stores user voice time and message counts for tracking purposes."

async def setup(bot):
    await bot.add_cog(UserTracker(bot))
