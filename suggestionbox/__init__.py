from .suggestionbox import SuggestionBox

__red_end_user_data_statement__ = (
    "This cog stores suggestion content, timestamps, and status information. "
    "No personal data is stored - suggestions are anonymous. "
    "All data can be removed by deleting the guild's configuration."
)


async def setup(bot):
    """Load the SuggestionBox cog."""
    cog = SuggestionBox(bot)
    await bot.add_cog(cog)