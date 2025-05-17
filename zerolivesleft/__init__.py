from .wp_reports import WPReports

async def setup(bot):
    bot.add_cog(WPReports(bot))
