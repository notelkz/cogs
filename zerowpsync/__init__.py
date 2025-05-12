from .zerowpsync import ZeroWPSync

async def setup(bot):
    bot.add_cog(ZeroWPSync(bot))
