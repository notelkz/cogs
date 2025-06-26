from .twitchy import Twitchy

async def setup(bot):
    await bot.add_cog(Twitchy(bot))