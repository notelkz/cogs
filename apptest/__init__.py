from .apptest import AppTest

def setup(bot):
    bot.add_cog(AppTest(bot))
