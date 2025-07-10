from .activitytracker import ActivityTracker

async def setup(bot):
    """
    Loads the ActivityTracker cog.
    """
    await bot.add_cog(ActivityTracker(bot))