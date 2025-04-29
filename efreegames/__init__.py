"""Free games tracker for Epic Games Store and Steam."""
from redbot.core.bot import Red
from .efreegames import EFreeGames

__red_end_user_data_statement__ = "This cog does not store end user data."

async def setup(bot: Red):
    """Load EFreeGames cog."""
    cog = EFreeGames(bot)
    await bot.add_cog(cog)
