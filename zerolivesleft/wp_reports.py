import discord
from discord.ext import commands, tasks
from redbot.core import commands as redcommands, Config
from redbot.core.bot import Red
from aiohttp import web
import aiohttp
import asyncio
from redbot.core.utils import log  # Import for logging

class WPReports(commands.Cog):
    """Handle WordPress user reports in Discord."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)  # Unique identifier for your cog
        default_guild = {
            "report_channel": None,
            "mod_role": None,
            "wp_api_url": None,
            "wp_api_key": None,
        }
        self.config.register_guild(**default_guild)
        self.webserver = None

    async def cog_load(self):
        self.webserver = await self.bot.add_cog_endpoint("/report", self.report_endpoint)

    async def cog_unload(self):
        if self.webserver:
            await self.bot.remove_cog_endpoint("/report")

    @redcommands.guild_only()
    @redcommands.admin_or_permissions(manage_guild=True)
    @redcommands.group()
    async def wpreportset(self, ctx):
        """Settings for WP Reports."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @wpreportset.command()
    async def channel(self, ctx, channel: discord.TextChannel):
        """Set the channel for reports."""
        await self.config.guild(ctx.guild).report_channel.set(channel.id)
        await ctx.send(f"Report channel set to {channel.mention}")

    @wpreportset.command()
    async def modrole(self, ctx, role: discord.Role):
        """Set the moderator role allowed to act on reports."""
        await self.config.guild(ctx.guild).mod_role.set(role.id)
        await ctx.send(f"Moderator role set to {role.name}")

    @wpreportset.command()
    async def wpapi(self, ctx, url: str, key: str):
        """Set the WordPress REST API endpoint and API key."""
        await self.config.guild(ctx.guild).wp_api_url.set(url)
        await self.config.guild(ctx.guild).wp_api_key.set(key)
        await ctx.send("WordPress API endpoint and key set.")

    async def report_endpoint(self, request):
        """Endpoint for WordPress to send reports."""
        try:
            data = await request.json()
            guild_id = int(data["guild_id"])
            reported_user = data["reported_user"]
            reporter = data["reporter"]
            reason = data["reason"]
            wp_report_id = data["wp_report_id"]
        except (KeyError, ValueError, TypeError) as e:  # More specific error handling
            log.exception("Invalid report data received:", exc_info=e)
            return web.json_response({"error": str(e)}, status=400)

        guild = self.bot.get_guild(guild_id)
        if not guild:
            log.error("Guild not found for ID: %s", guild_id)
            return web.json_response({"error": "Guild not found"}, status=404)

        channel_id = await self.config.guild(guild).report_channel()
        if not channel_id:
            log.error("Report channel not set for guild: %s", guild.name)
            return web.json_response({"error": "Report channel not set"}, status=400)
        
        channel = guild.get_channel(channel_id)
        if not channel:
            log.error("Channel not found for ID: %s in guild: %s", channel_id, guild.name)
            return web.json_response({"error": "Channel not found"}, status=404)

        embed = discord.Embed(
            title="🚩 New User Report",
            color=discord.Color.red(),
            description=f"**Reported User:** {reported_user}\n"
                        f"**Reporter:** {reporter}\n"
                        f"**Reason:** {reason}\n"
                        f"**WP Report ID:** {wp_report_id}"
        )
        view = ReportActionView(self, wp_report_id, guild)
        try:
            await channel.send(embed=embed, view=view)
        except discord.HTTPException as e:
            log.exception("Failed to send report to Discord:", exc_info=e)
            return web.json_response({"error": "Failed to send report to Discord"}, status=500)

        return web.json_response({"status": "ok"})

    async def update_wp_status(self, guild, wp_report_id, status):
        """Update report status on WordPress via REST API."""
        url = await self.config.guild(guild).wp_api_url()
        key = await self.config.guild(guild).wp_api_key()
        if not url or not key:
            log.error("WordPress API URL or key not set.")
            return

        payload = {
            "report_id": wp_report_id,
            "status": status,
            "api_key": key
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=5) as resp:
                    if resp.status != 200:
                        log.error("Failed to update WordPress report status.  Status code: %s, Response: %s", resp.status, await resp.text())
                    else:
                        log.info("Successfully updated WordPress report status for report ID %s to %s", wp_report_id, status)
        except aiohttp.ClientError as e:
            log.exception("Error updating WP report status:", exc_info=e)
        except Exception as e:
            log.exception("Unexpected error updating WP report status:", exc_info=e)


class ReportActionView(discord.ui.View):
    def __init__(self, cog, wp_report_id, guild):
        super().__init__(timeout=None)
        self.cog = cog
        self.wp_report_id = wp_report_id
        self.guild = guild

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        mod_role_id = await self.cog.config.guild(self.guild).mod_role()
        if mod_role_id is None:
            await interaction.response.send_message("Moderator role not set.", ephemeral=True)
            return False
        mod_role = self.guild.get_role(mod_role_id)
        if mod_role is None or mod_role not in interaction.user.roles:
            await interaction.response.send_message("You do not have permission to act on reports.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Resolve", style=discord.ButtonStyle.green, custom_id="resolve_report")
    async def resolve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Report marked as resolved.", ephemeral=True)
        await self.cog.update_wp_status(self.guild, self.wp_report_id, "resolved")
        self.disable_all_items()
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Ban User", style=discord.ButtonStyle.red, custom_id="ban_report")
    async def ban(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Report marked as banned.", ephemeral=True)
        await self.cog.update_wp_status(self.guild, self.wp_report_id, "banned")
        self.disable_all_items()
        await interaction.message.edit(view=self)
