from redbot.core.bot import Red
from .disapps import DisApps

async def setup(bot: Red) -> None:
    await bot.add_cog(DisApps(bot))
