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

    async def sync_with_website(self, member: discord.Member, roles: list):
        """Sync roles with website"""
        try:
            self.logger.debug(f"Attempting to sync roles for {member.name}")
        
        # Format roles for the website
        role_data = [
            {
                "id": str(role.id),
                "name": role.name,
                "color": role.color.value
            }
            for role in roles
            if not role.is_default()
        ]

        headers = {
            "Authorization": f"Bot {self.bot.http.token}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        self.logger.debug(f"Sending request with headers: {headers}")
        self.logger.debug(f"Sending roles data: {role_data}")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self.website_url}/roles.php",
                headers=headers,
                json={
                    "user_id": str(member.id),
                    "username": member.name,
                    "roles": role_data
                }
            ) as resp:
                text = await resp.text()
                self.logger.debug(f"Response status: {resp.status}")
                self.logger.debug(f"Response text: {text}")

                if resp.status == 200:
                    return await resp.json()
                else:
                    return {"success": False, "error": f"HTTP {resp.status}: {text}"}

    except Exception as e:
        self.logger.error(f"Sync error: {str(e)}")
        return {"success": False, "error": str(e)}

    @commands.group(name="rolesync")
    @commands.admin()
    async def rolesync(self, ctx):
        """Role sync management commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @rolesync.command(name="sync")
    async def sync_roles(self, ctx, member: discord.Member = None):
        """Sync roles for a member or yourself"""
        target = member or ctx.author
        
        async with ctx.typing():
            self.logger.info(f"Manual sync requested for {target.name}")
            result = await self.sync_with_website(target, target.roles)
            
            if result.get('success'):
                await ctx.send(f"✅ Successfully synced roles for {target.name}")
            else:
                await ctx.send(f"❌ Failed to sync roles for {target.name}: {result.get('error', 'Unknown error')}")

    @rolesync.command(name="test")
    @commands.admin()
    async def test_command(self, ctx):
        """Test if the cog is responding"""
        await ctx.send("RoleSync cog is responding!")

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Monitor role changes and sync them"""
        if before.roles != after.roles:
            guild_config = await self.config.guild(after.guild).all()
            if not guild_config["enabled"]:
                return

            self.logger.debug(f"Role change detected for {after.name}")
            result = await self.sync_with_website(after, after.roles)
            
            if not result.get('success'):
                self.logger.error(f"Failed to sync roles for {after.name}: {result.get('error')}")

def setup(bot):
    bot.add_cog(RoleSync(bot))
