from .zeroroles import ZeroRoles

async def setup(bot):
    await bot.add_cog(ZeroRoles(bot))
