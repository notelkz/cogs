from .selfroles import SelfRoles

async def setup(bot):
    # This is the standard entry point for Redbot cogs
    cog = SelfRoles(bot)
    await bot.add_cog(cog)