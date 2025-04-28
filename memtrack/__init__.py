from .memtrack import MemTrack
from .memtrack import setup

async def setup(bot):
    await bot.add_cog(MemTrack(bot))
