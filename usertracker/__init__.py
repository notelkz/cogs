from .usertracker import UserTracker

__red_end_user_data_statement__ = "This cog stores user voice time and message counts for tracking purposes."

async def setup(bot):
    try:
        cog = UserTracker(bot)
        await bot.add_cog(cog)  # Await the add_cog method
        await cog.initialize()  # Await the initialize method
        print("UserTracker cog loaded successfully.")
    except Exception as e:
        print(f"Failed to load UserTracker cog: {str(e)}")
