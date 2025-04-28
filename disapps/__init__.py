from redbot.core.bot import Red
from redbot.core import Config
import discord
from typing import Dict, Any

from .disapps import DisApps

__red_end_user_data_statement__ = "This cog stores user IDs and application data including declined applications."

async def setup(bot: Red) -> None:
    cog = DisApps(bot)
    await bot.add_cog(cog)

    # Initialize default guild settings
    defaults = {
        "mod_role": None,
        "accepted_role": None,
        "assignable_roles": [],
        "applications_category": None,
        "archive_category": None,
        "setup_complete": False,
        "applications": {}
    }
    
    # Initialize Config
    config = Config.get_conf(
        cog,
        identifier=1234567890,
        force_registration=True
    )
    
    # Register default guild settings
    config.register_guild(**defaults)
    
    # Example of application data structure in applications dict:
    # {
    #     "user_id": {
    #         "channel_id": 123456789,
    #         "status": "pending/accepted/declined",
    #         "declines": 0,
    #         "timestamp": 1234567890,
    #         "previously_accepted": False
    #     }
    # }
    
    await bot.add_cog(cog)
