import asyncio
import aiohttp
import discord
import logging
from datetime import datetime
from typing import Optional, Dict, Any
from aiohttp import web
from redbot.core import commands, Config, tasks
from redbot.core.bot import Red

log = logging.getLogger("red.gamecounter")

class ConfirmView(discord.ui.View):
    def __init__(self, author: discord.Member):
        super().__init__(timeout=60)
        self.author = author
        self.result = None
        self.message = None

    @discord.ui.button(label="Yes", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.author:
            await interaction.response.send_message("You cannot use this button.", ephemeral=True)
            return
        self.result = True
        self.stop()
        await interaction.response.edit_message(content="Confirmed!", view=None)

    @discord.ui.button(label="No", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.author:
            await interaction.response.send_message("You cannot use this button.", ephemeral=True)
            return
        self.result = False
        self.stop()
        await interaction.response.edit_message(content="Cancelled!", view=None)

class GameCounter(commands.Cog):
    """A cog for counting Discord role members and sending data to Django API."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        
        # Default configuration
        default_global = {
            "django_api_url": "",
            "django_api_token": "",
            "guild_id": None,
            "role_mappings": {},
            "web_api_enabled": False,
            "web_api_host": "0.0.0.0",
            "web_api_port": 8080,
            "loop_interval": 5,
            "activity_data": {}
        }
        
        self.config.register_global(**default_global)
        
        # Web server components
        self.web_app = web.Application()
        self.web_runner = None
        self.web_site = None
        
        # Setup web routes
        self.web_app.router.add_get('/api/roles', self.web_api_roles)
        self.web_app.router.add_get('/api/status', self.web_api_status)
        
        # HTTP session for API calls
        self.session = None

    async def cog_load(self):
        """Called when the cog is loaded."""
        self.session = aiohttp.ClientSession()
        
        # Start the counter loop
        interval = await self.config.loop_interval()
        self.count_and_update.change_interval(minutes=interval)
        self.count_and_update.start()
        
        # Start web API if enabled
        if await self.config.web_api_enabled():
            try:
                await self._start_web_server()
            except Exception as e:
                log.error(f"Failed to start web API server on cog load: {e}")

    async def cog_unload(self):
        """Called when the cog is unloaded."""
        # Stop the counter loop
        if self.count_and_update.is_running():
            self.count_and_update.cancel()
        
        # Close HTTP session
        if self.session and not self.session.closed:
            await self.session.close()
        
        # Stop web server
        await self._shutdown_web_server()

    @tasks.loop(minutes=5)
    async def count_and_update(self):
        """Main loop that counts role members and sends data to Django API."""
        try:
            guild_id = await self.config.guild_id()
            if not guild_id:
                log.warning("No guild ID configured for GameCounter")
                return

            guild = self.bot.get_guild(guild_id)
            if not guild:
                log.error(f"Could not find guild with ID {guild_id}")
                return

            role_mappings = await self.config.role_mappings()
            if not role_mappings:
                log.warning("No role mappings configured")
                return

            django_api_url = await self.config.django_api_url()
            django_api_token = await self.config.django_api_token()
            
            if not django_api_url or not django_api_token:
                log.warning("Django API URL or token not configured")
                return

            # Count members for each mapped role
            role_counts = {}
            for role_name, role_id in role_mappings.items():
                role = guild.get_role(int(role_id))
                if role:
                    role_counts[role_name] = len(role.members)
                    log.debug(f"Role {role_name} ({role_id}): {len(role.members)} members")
                else:
                    log.warning(f"Could not find role with ID {role_id}")
                    role_counts[role_name] = 0

            # Send data to Django API
            await self._send_to_django_api(role_counts, django_api_url, django_api_token)
            
        except Exception as e:
            log.error(f"Error in count_and_update: {e}")

    async def _send_to_django_api(self, role_counts: Dict[str, int], api_url: str, api_token: str):
        """Send role count data to Django API."""
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession()

        headers = {
            'Authorization': f'Token {api_token}',
            'Content-Type': 'application/json'
        }
        
        data = {
            'timestamp': datetime.utcnow().isoformat(),
            'role_counts': role_counts
        }

        try:
            async with self.session.post(api_url, json=data, headers=headers) as response:
                if response.status == 200:
                    log.info(f"Successfully sent role counts to Django API: {role_counts}")
                else:
                    log.error(f"Django API returned status {response.status}: {await response.text()}")
        except Exception as e:
            log.error(f"Failed to send data to Django API: {e}")

    async def _start_web_server(self):
        """Start the web API server."""
        if self.web_runner:
            return
            
        host = await self.config.web_api_host()
        port = await self.config.web_api_port()
        
        self.web_runner = web.AppRunner(self.web_app)
        await self.web_runner.setup()
        self.web_site = web.TCPSite(self.web_runner, host, port)
        await self.web_site.start()
        log.info(f"GameCounter web API server started on {host}:{port}")

    async def _shutdown_web_server(self):
        """Shutdown the web API server."""
        if self.web_site:
            await self.web_site.stop()
            self.web_site = None
        
        if self.web_runner:
            await self.web_runner.cleanup()
            self.web_runner = None

    async def web_api_roles(self, request):
        """Web API endpoint for role counts."""
        try:
            guild_id = await self.config.guild_id()
            if not guild_id:
                return web.json_response({"error": "No guild configured"}, status=500)

            guild = self.bot.get_guild(guild_id)
            if not guild:
                return web.json_response({"error": "Guild not found"}, status=500)

            role_mappings = await self.config.role_mappings()
            role_counts = {}
            
            for role_name, role_id in role_mappings.items():
                role = guild.get_role(int(role_id))
                if role:
                    role_counts[role_name] = len(role.members)
                else:
                    role_counts[role_name] = 0

            return web.json_response({
                "timestamp": datetime.utcnow().isoformat(),
                "guild_name": guild.name,
                "role_counts": role_counts
            })
        except Exception as e:
            log.error(f"Web API error: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def web_api_status(self, request):
        """Web API endpoint for status."""
        return web.json_response({
            "status": "running",
            "timestamp": datetime.utcnow().isoformat(),
            "loop_running": self.count_and_update.is_running()
        })

    @commands.hybrid_group(name="gamecounter", aliases=["gc"])
    @commands.is_owner()
    async def gamecounter_settings(self, ctx: commands.Context):
        """GameCounter configuration commands."""
        if ctx.invoked_subcommand is None:
            embed = discord.Embed(title="GameCounter Settings", color=discord.Color.blue())
            
            guild_id = await self.config.guild_id()
            django_url = await self.config.django_api_url()
            web_enabled = await self.config.web_api_enabled()
            web_host = await self.config.web_api_host()
            web_port = await self.config.web_api_port()
            interval = await self.config.loop_interval()
            
            embed.add_field(name="Guild ID", value=guild_id or "Not set", inline=True)
            embed.add_field(name="Django API URL", value=django_url or "Not set", inline=True)
            embed.add_field(name="Web API", value="Enabled" if web_enabled else "Disabled", inline=True)
            embed.add_field(name="Web API Address", value=f"{web_host}:{web_port}", inline=True)
            embed.add_field(name="Loop Interval", value=f"{interval} minutes", inline=True)
            embed.add_field(name="Loop Status", value="Running" if self.count_and_update.is_running() else "Stopped", inline=True)
            
            await ctx.send(embed=embed)

    @gamecounter_settings.command(name="setguild")
    @commands.is_owner()
    async def set_guild(self, ctx: commands.Context, guild_id: int):
        """Set the guild ID to monitor."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return await ctx.send(f"Could not find guild with ID {guild_id}")
        
        await self.config.guild_id.set(guild_id)
        await ctx.send(f"Guild set to: {guild.name} ({guild_id})")

    @gamecounter_settings.command(name="setdjangoapi")
    @commands.is_owner()
    async def set_django_api(self, ctx: commands.Context, url: str, token: str):
        """Set Django API URL and token."""
        await self.config.django_api_url.set(url)
        await self.config.django_api_token.set(token)
        await ctx.send("Django API settings updated successfully.")

    @gamecounter_settings.command(name="addrole")
    @commands.is_owner()
    async def add_role_mapping(self, ctx: commands.Context, role_name: str, role_id: int):
        """Add a role mapping for counting."""
        role_mappings = await self.config.role_mappings()
        role_mappings[role_name] = role_id
        await self.config.role_mappings.set(role_mappings)
        await ctx.send(f"Added role mapping: {role_name} -> {role_id}")

    @gamecounter_settings.command(name="removerole")
    @commands.is_owner()
    async def remove_role_mapping(self, ctx: commands.Context, role_name: str):
        """Remove a role mapping."""
        role_mappings = await self.config.role_mappings()
        if role_name in role_mappings:
            del role_mappings[role_name]
            await self.config.role_mappings.set(role_mappings)
            await ctx.send(f"Removed role mapping: {role_name}")
        else:
            await ctx.send(f"Role mapping '{role_name}' not found.")

    @gamecounter_settings.command(name="listroles")
    @commands.is_owner()
    async def list_role_mappings(self, ctx: commands.Context):
        """List all role mappings."""
        role_mappings = await self.config.role_mappings()
        if not role_mappings:
            return await ctx.send("No role mappings configured.")
        
        embed = discord.Embed(title="Role Mappings", color=discord.Color.green())
        for role_name, role_id in role_mappings.items():
            embed.add_field(name=role_name, value=f"ID: {role_id}", inline=True)
        
        await ctx.send(embed=embed)

    @gamecounter_settings.command(name="setinterval")
    @commands.is_owner()
    async def set_interval(self, ctx: commands.Context, minutes: int):
        """Set the loop interval in minutes."""
        if minutes < 1:
            return await ctx.send("Interval must be at least 1 minute.")
        
        await self.config.loop_interval.set(minutes)
        
        # Restart the loop with new interval
        if self.count_and_update.is_running():
            self.count_and_update.cancel()
        
        self.count_and_update.change_interval(minutes=minutes)
        self.count_and_update.start()
        
        await ctx.send(f"Loop interval set to {minutes} minutes and restarted.")

    @gamecounter_settings.command(name="enablewebapi")
    @commands.is_owner()
    async def enable_web_api(self, ctx: commands.Context, host: str = "0.0.0.0", port: int = 8080):
        """Enable and configure the web API."""
        await self.config.web_api_enabled.set(True)
        await self.config.web_api_host.set(host)
        await self.config.web_api_port.set(port)
        
        try:
            await self._start_web_server()
            await ctx.send(f"Web API enabled and started on `{host}:{port}`.")
        except Exception as e:
            await ctx.send(f"Web API enabled but failed to start: {e}")

    @gamecounter_settings.command(name="disablewebapi")
    @commands.is_owner()
    async def disable_web_api(self, ctx: commands.Context):
        """Disable the web API."""
        await self.config.web_api_enabled.set(False)
        await self._shutdown_web_server()
        await ctx.send("Web API disabled and stopped.")

    @gamecounter_settings.command(name="startloop")
    @commands.is_owner()
    async def start_counter_loop(self, ctx: commands.Context):
        """Start the counter loop."""
        if self.count_and_update.is_running():
            return await ctx.send("The counter loop is already running.")
        
        try:
            self.count_and_update.start()
            await ctx.send("Game counter loop started successfully.")
        except Exception as e:
            await ctx.send(f"Error starting counter loop: {e}")

    @gamecounter_settings.command(name="stoploop")
    @commands.is_owner()
    async def stop_counter_loop(self, ctx: commands.Context):
        """Stop the counter loop."""
        if not self.count_and_update.is_running():
            return await ctx.send("The counter loop is not running.")
        
        try:
            self.count_and_update.cancel()
            await ctx.send("Game counter loop stopped successfully.")
        except Exception as e:
            await ctx.send(f"Error stopping counter loop: {e}")

    @gamecounter_settings.command(name="runnow")
    @commands.is_owner()
    async def run_now(self, ctx: commands.Context):
        """Manually trigger the count and update process."""
        await ctx.send("Running count and update process...")
        try:
            await self.count_and_update()
            await ctx.send("Count and update process completed successfully.")
        except Exception as e:
            await ctx.send(f"Error during count and update process: {e}")
            log.error(f"Manual count and update failed: {e}")

async def setup(bot: Red):
    await bot.add_cog(GameCounter(bot))
