# webserver.py

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
        self.config.register_global(host="0.0.0.0", port=5000, api_key=None)
        
        self.web_app = web.Application()
        self.web_runner = None
        self.web_site = None
        
        self.web_app.router.add_get("/health", self.health_check_handler)
        asyncio.create_task(self.initialize())

    async def initialize(self):
        """Start the web server."""
        await self.bot.wait_until_ready()
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
        self.web_app.router.add_routes(routes)
        log.info(f"Added {len(routes)} routes to the web server.")

    @commands.group(name="webserver")
    @commands.is_owner()
    async def webserver_group(self, ctx):
        """Commands to manage the central web server."""
        pass

    # --- FIXED: Changed .command() to .group() ---
    @webserver_group.group(name="set")
    async def webserver_set(self, ctx):
        """Base command for setting webserver configuration."""
        pass

    @webserver_set.command(name="port")
    async def set_port(self, ctx, port: int):
        """Set the port for the web server."""
        if not (1024 <= port <= 65535):
            return await ctx.send("Port must be between 1024 and 65535.")
        await self.config.port.set(port)
        await ctx.send(f"Web server port set to {port}. Reload the cog for changes to take effect.")

    @webserver_set.command(name="host")
    async def set_host(self, ctx, host: str):
        """Set the host for the web server."""
        await self.config.host.set(host)
        await ctx.send(f"Web server host set to {host}. Reload the cog for changes to take effect.")

    @webserver_set.command(name="apikey")
    async def set_apikey(self, ctx, *, api_key: str):
        """Set the API key for the web server."""
        await self.config.api_key.set(api_key)
        await ctx.send("API key set. This will be used for all cogs that use the web server.")
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass

    @webserver_group.command(name="showconfig")
    async def show_config(self, ctx):
        """Show the current web server configuration."""
        host = await self.config.host()
        port = await self.config.port()
        api_key = await self.config.api_key()
        try:
            await ctx.author.send(f"**Web Server Configuration**\n- Host: `{host}`\n- Port: `{port}`\n- API Key: `{api_key if api_key else 'Not set'}`")
            await ctx.send("Configuration sent to your DMs.")
        except discord.Forbidden:
            await ctx.send(f"**Web Server Configuration**\n- Host: `{host}`\n- Port: `{port}`\n- API Key: `{'Set' if api_key else 'Not set'}`")

    @webserver_group.command(name="restart")
    async def restart_server(self, ctx):
        """Restart the web server."""
        await ctx.send("Restarting web server...")
        await self.shutdown_server()
        await self.initialize()
        await ctx.send("Web server restarted.")

async def setup(bot):
    cog = WebServer(bot)
    await bot.add_cog(cog)
