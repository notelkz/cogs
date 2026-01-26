from .selfroles import SelfRoles

async def setup(bot):
    cog = SelfRoles(bot)
    await bot.add_cog(cog)