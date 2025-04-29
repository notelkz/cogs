"""Free games tracker for multiple game stores with enhanced features."""
from redbot.core.bot import Red
from .efreegames import EFreeGames

__red_end_user_data_statement__ = """This cog stores:
- User notification preferences for different game types
- User-linked store accounts (Steam ID, Epic Games ID)
- Server configuration for notification roles and channels
"""

async def setup(bot: Red):
    """Load EFreeGames cog."""
    cog = EFreeGames(bot)
    await bot.add_cog(cog)
