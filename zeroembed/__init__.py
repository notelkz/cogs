from .zeroembed import ZeroEmbed

async def setup(bot):
    await bot.add_cog(ZeroEmbed(bot))
