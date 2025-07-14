import asyncio
import logging
from aiohttp import web
from redbot.core import commands, Config

log = logging.getLogger("red.zerocogs.webserver")

class WebServer(commands.Cog):
    """A central cog to manage a single aiohttp web server for other cogs."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9876543210)
        self.config.register_global(host="0.0.0.0", port=5000)
        
        self.web_app = web.Application()
        self.web_runner = None
        self.web_site = None
        
        # Add a default health check route
        self.web_app.router.add_get("/health", self.health_check_handler)

    async def initialize(self):
        """Start the web server."""
        if not self.web_runner:
            host = await self.config.host()
            port = await self.config.port()
            try:
                self.web_runner = web.AppRunner(self.web_app)
                await self.web_runner.setup()
                self.web_site = web.TCPSite(self.web_runner, host, port)
                await self.web_site.start()
                log.info(f"Central web server started on http://{host}:{port}")
            except Exception as e:
                log.error(f"Failed to start central web server: {e}")

    def cog_unload(self):
        """Gracefully shut down the web server."""
        if self.web_runner:
            asyncio.create_task(self.shutdown_server())

    async def shutdown_server(self):
        log.info("Shutting down central web server...")
        await self.web_app.shutdown()
        await self.web_runner.cleanup()
        log.info("Central web server shut down successfully.")

    async def health_check_handler(self, request):
        return web.Response(text="OK", status=200)

    def add_routes(self, routes):
        """Allow other cogs to add their routes."""
        self.web_app.router.add_routes(routes)
        log.info(f"Added {len(routes)} routes to the web server.")

async def setup(bot):
    cog = WebServer(bot)
    await bot.add_cog(cog)
    await cog.initialize()
