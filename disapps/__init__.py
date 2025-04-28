from redbot.core.bot import Red
from .disapps import DisApps

async def setup(bot: Red) -> None:
    await bot.add_cog(DisApps(bot))

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

