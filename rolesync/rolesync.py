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
                error_msg = result.get('error', 'Unknown error')
                await ctx.send(f"❌ Failed to sync roles for {target.name}: {error_msg}")

    @rolesync.command(name="test")
    @commands.admin()
    async def test_command(self, ctx):
        """Test the connection to the website"""
        async with ctx.typing():
            try:
                headers = {
                    "Authorization": f"Bot {self.bot.http.token}",
                    "Content-Type": "application/json"
                }
                
                self.logger.debug("Testing connection with headers:", headers)
                
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self.website_url}/roles.php",
                        headers=headers,
                        json={"test": True}
                    ) as resp:
                        text = await resp.text()
                        self.logger.debug(f"Test response status: {resp.status}")
                        self.logger.debug(f"Test response text: {text}")
                        
                        if resp.status == 200:
                            await ctx.send("✅ Connection test successful!")
                        else:
                            await ctx.send(f"❌ Connection test failed: HTTP {resp.status}\nResponse: {text}")
            
            except Exception as e:
                self.logger.error(f"Test error: {str(e)}")
                await ctx.send(f"❌ Connection test failed: {str(e)}")

    @rolesync.command(name="status")
    @commands.admin()
    async def sync_status(self, ctx):
        """Check the sync status"""
        embed = discord.Embed(
            title="Role Sync Status",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        
        guild_config = await self.config.guild(ctx.guild).all()
        log_channel = ctx.guild.get_channel(guild_config["log_channel"]) if guild_config["log_channel"] else None
        
        embed.add_field(
            name="Status",
            value="✅ Enabled" if guild_config["enabled"] else "❌ Disabled",
            inline=False
        )
        embed.add_field(
            name="Log Channel",
            value=log_channel.mention if log_channel else "Not set",
            inline=False
        )
        
        await ctx.send(embed=embed)

    @rolesync.command(name="toggle")
    @commands.admin()
    async def toggle_sync(self, ctx):
        """Toggle role sync on/off"""
        current = await self.config.guild(ctx.guild).enabled()
        await self.config.guild(ctx.guild).enabled.set(not current)
        state = "enabled" if not current else "disabled"
        await ctx.send(f"Role sync has been {state}")

    @rolesync.command(name="setlog")
    @commands.admin()
    async def set_log_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel for role sync logs"""
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"Log channel set to {channel.mention}")

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
            
            # Log to discord channel if set
            if guild_config["log_channel"]:
                channel = after.guild.get_channel(guild_config["log_channel"])
                if channel:
                    added_roles = set(after.roles) - set(before.roles)
                    removed_roles = set(before.roles) - set(after.roles)
                    
                    embed = discord.Embed(
                        title="Role Update",
                        color=discord.Color.blue(),
                        timestamp=datetime.utcnow()
                    )
                    embed.set_author(name=after.name, icon_url=after.display_avatar.url)
                    
                    if added_roles:
                        embed.add_field(
                            name="Added Roles",
                            value="\n".join([role.name for role in added_roles]),
                            inline=False
                        )
                    
                    if removed_roles:
                        embed.add_field(
                            name="Removed Roles",
                            value="\n".join([role.name for role in removed_roles]),
                            inline=False
                        )
                    
                    if result.get('success'):
                        embed.set_footer(text="✅ Synced successfully")
                    else:
                        embed.set_footer(text=f"❌ Sync failed: {result.get('error')}")
                    
                    await channel.send(embed=embed)

def setup(bot):
    bot.add_cog(RoleSync(bot))
