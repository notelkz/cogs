from .usertracker import UserTracker

def setup(bot):
    bot.add_cog(UserTracker(bot))
