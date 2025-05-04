from .wpdiscordsync import WPDiscordSync

def setup(bot):
    bot.add_cog(WPDiscordSync(bot))
