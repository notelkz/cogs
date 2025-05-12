from .zerowpsync import ZeroWPSync

def setup(bot):
    bot.add_cog(ZeroWPSync(bot))
