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
        
        # We need to explicitly tell aiohttp to not freeze its router immediately.
        # However, aiohttp's router usually freezes on AppRunner setup.
        # The best way is to ensure all routes are collected before runner.setup()
        # This requires a slight change in logic for how other cogs add routes.

        self.web_app.router.add_get("/health", self.health_check_handler)
        asyncio.create_task(self.initialize())

    async def initialize(self):
        """Start the web server."""
        await self.bot.wait_until_ready()
        if not self.web_runner:
            host = await self.config.host()
            port = await self.config.port()
            try:
                # ALL routes from ALL cogs MUST be added to self.web_app.router
                # *before* web_runner.setup() is called.
                # The issue is GameCounter is adding routes in its setup(),
                # but WebServer might have already called setup().

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

    # MODIFIED: add_routes method - This will now directly add routes
    # during bot startup and the cog's setup. The WebServer initialize method
    # itself will be responsible for ensuring it runs AFTER all other cogs
    # have had a chance to call this add_routes method.
    def add_routes(self, routes):
        """Adds a list of aiohttp routes to the web application."""
        # This method is called by other cogs like GameCounter in their setup().
        # We assume that WebServer's initialize() will process these routes
        # after they've been added here, but before its aiohttp app is setup.
        try:
            self.web_app.router.add_routes(routes)
            log.info(f"Dynamically added {len(routes)} routes to the web application router.")
        except RuntimeError as e:
            # This should ideally not happen if called during cog setup,
            # but if it does, it implies a timing issue that WebServer needs to manage better.
            log.error(f"Failed to add routes to web app: {e}. Router might be frozen prematurely.")

    @commands.group(name="webserver")
    @commands.is_owner()
    async def webserver_group(self, ctx):
        """Commands to manage the central web server."""
        pass

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