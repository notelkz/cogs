from .disapps import DisApps

async def setup(bot):
    await bot.add_cog(DisApps(bot))
