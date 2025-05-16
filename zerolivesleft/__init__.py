from .wp_reports import WPReports

def setup(bot):
    bot.add_cog(WPReports(bot))
