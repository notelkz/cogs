from redbot.core import commands, Config
from redbot.core.bot import Red
import discord
import aiohttp
import logging
from datetime import datetime

class RoleSync(commands.Cog):
    """Sync roles between website and Discord"""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1274316388424745030)
        self.website_url = "https://notelkz.net/zerolivesleft"
        
        # Setup logging
        self.logger = logging.getLogger('red.rolesync')
        self.logger.setLevel(logging.DEBUG)
        
        # Default config
        default_guild = {
            "log_channel": None,
            "enabled": True
        }
        self.config.register_guild(**default_guild)

    @commands.group(name="rolesync")
    @commands.admin()
    async def rolesync(self, ctx):
        """Role sync management commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @rolesync.command(name="test")
    @commands.admin()
    async def test_command(self, ctx):
        """Test if the cog is responding"""
        await ctx.send("RoleSync cog is responding!")

def setup(bot):
    bot.add_cog(RoleSync(bot))
