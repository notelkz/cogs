from .webserver import WebServer

async def setup(bot):
    """Load the WebServer cog."""
    cog = WebServer(bot)
    await bot.add_cog(cog)
    # The initialize method in the cog will start the web server
