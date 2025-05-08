from .zerowelcome import ZeroWelcome

async def setup(bot):
    await bot.add_cog(ZeroWelcome(bot))
