"""
Free Games cog for Red-DiscordBot
Tracks and announces free games from various digital storefronts
"""
import asyncio
from redbot.core.bot import Red
from .efreegames import EFreeGames

__red_end_user_data_statement__ = (
    "This cog stores configuration for game announcements and role pings per server. "
    "It also maintains a cache of previously announced games to prevent duplicates. "
    "No personal user data is stored."
)

async def setup(bot: Red):
    """Load EFreeGames cog."""
    cog = EFreeGames(bot)
    await cog.initialize_cache()
    await bot.add_cog(cog)
