from redbot.core import commands, Config
from redbot.core.bot import Red
import discord
import aiohttp
import json
import logging
from datetime import datetime

class RoleSync(commands.Cog):
    """Sync roles between website and Discord"""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1274316388424745030)
        self.website_url = "https://notelkz.net/zerolivesleft"
        
        # Setup logging with more detail
        self.logger = logging.getLogger('red.rolesync')
        self.logger.setLevel(logging.DEBUG)
        
        # Add a debug handler
        handler = logging.StreamHandler()
        handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        
        # Define assignable roles
        self.assignable_roles = {
            "1274316388424745030": {
                "name": "Test Role",
                "description": "A test role for role sync functionality"
            }
        }

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
            self.logger.debug(f"Roles to sync: {roles}")
            
            # Bot token (replace with your actual token)
            token = "MTMxNDY4MzUyMjA4MjE0ODQ0Mg.G1omtJ.M_bLDcWaRnJ8oW9hiGk9hBw_xxWPkbq7_b7TSQ"
            auth_header = f"Bot {token}"
            
            self.logger.debug(f"Auth header length: {len(auth_header)}")
            self.logger.debug(f"First 10 chars of auth header: {auth_header[:10]}")

            headers = {
                "Authorization": auth_header,
                "Content-Type": "application/json",
                "Accept": "application/json"
            }

            payload = {
                "user_id": str(member.id),
                "username": member.name,
                "roles": roles
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.website_url}/roles.php",
                    headers=headers,
                    json=payload
                ) as resp:
                    self.logger.debug(f"Website response status: {resp.status}")
                    
                    try:
                        response_text = await resp.text()
                        self.logger.debug(f"Full response: {response_text}")
                        
                        if resp.status == 401:
                            try:
                                response_json = json.loads(response_text)
                                debug_info = response_json.get('debug', {})
                                self.logger.error(f"Auth failed. Debug info: {debug_info}")
                                return {"success": False, "error": "Authorization failed", "debug": debug_info}
                            except:
                                self.logger.error("Auth failed. Could not parse debug info.")
                                return {"success": False, "error": "Authorization failed"}
                            
                    except Exception as e:
                        self.logger.error(f"Failed to read response: {e}")
                        response_text = "Could not read response"

                    if resp.status != 200:
                        self.logger.error(f"Website sync failed: Status {resp.status}, Response: {response_text}")
                        return {"success": False, "error": f"HTTP {resp.status}"}
                    
                    try:
                        return await resp.json()
                    except Exception as e:
                        self.logger.error(f"Failed to parse JSON response: {e}")
                        return {"success": False, "error": "Invalid JSON response"}

        except Exception as e:
            self.logger.error(f"Sync error: {str(e)}")
            return {"success": False, "error": str(e)}

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Monitor role changes and sync them"""
        if before.roles != after.roles:
            guild_config = await self.config.guild(after.guild).all()
            if not guild_config["enabled"]:
                return

            self.logger.debug(f"Role change detected for {after.name}")

            # Get role changes
            added_roles = set(after.roles) - set(before.roles)
            removed_roles = set(before.roles) - set(after.roles)
            
            # Format roles for sync
            current_roles = [
                {
                    "id": str(role.id),
                    "name": role.name,
                    "color": role.color.value
                }
                for role in after.roles
                if role.name != "@everyone"
            ]

            self.logger.debug(f"Current roles: {current_roles}")

            # Sync with website
            sync_result = await self.sync_with_website(after, current_roles)
            
            # Log changes
            if sync_result.get('success'):
                await self.log_role_changes(
                    after.guild,
                    {
                        "user_id": str(after.id),
                        "username": after.name,
                        "added_roles": [{"id": str(r.id), "name": r.name} for r in added_roles],
                        "removed_roles": [{"id": str(r.id), "name": r.name} for r in removed_roles],
                        "timestamp": datetime.utcnow().isoformat()
                    }
                )
            else:
                self.logger.error(f"Failed to sync roles for {after.name}: {sync_result.get('error')}")

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
            
            # Format current roles
            current_roles = [
                {
                    "id": str(role.id),
                    "name": role.name,
                    "color": role.color.value
                }
                for role in target.roles
                if role.name != "@everyone"
            ]

            # Sync with website
            result = await self.sync_with_website(target, current_roles)
            
            if result.get('success'):
                await ctx.send(f"✅ Successfully synced roles for {target.name}")
            else:
                error_msg = result.get('error', 'Unknown error')
                debug_info = result.get('debug', {})
                await ctx.send(f"❌ Failed to sync roles for {target.name}: {error_msg}\nDebug: {debug_info}")

    @rolesync.command(name="testauth")
    @commands.admin()
    async def test_auth(self, ctx):
        """Test the authentication token"""
        async with ctx.typing():
            token = "MTMxNDY4MzUyMjA4MjE0ODQ0Mg.G1omtJ.M_bLDcWaRnJ8oW9hiGk9hBw_xxWPkbq7_b7TSQ"
            auth_header = f"Bot {token}"
            
            self.logger.info("Testing authentication")
            self.logger.debug(f"Auth header length: {len(auth_header)}")
            
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{self.website_url}/roles.php",
                        headers={
                            "Authorization": auth_header,
                            "Content-Type": "application/json"
                        },
                        json={
                            "test": True,
                            "timestamp": datetime.utcnow().isoformat()
                        }
                    ) as resp:
                        status = resp.status
                        try:
                            response_text = await resp.text()
                            await ctx.send(f"Auth test results:\nStatus: {status}\nResponse: {response_text}")
                        except Exception as e:
                            await ctx.send(f"Failed to read response: {e}")
            except Exception as e:
                await ctx.send(f"Test failed: {str(e)}")

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

    async def log_role_changes(self, guild: discord.Guild, changes: dict):
        """Log role changes to the designated channel"""
        log_channel_id = await self.config.guild(guild).log_channel()
        if not log_channel_id:
            return

        channel = guild.get_channel(log_channel_id)
        if not channel:
            return

        embed = discord.Embed(
            title="Role Changes",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        
        embed.set_author(name=changes["username"])
        
        if changes.get("added_roles"):
            added = "\n".join([f"• {role['name']}" for role in changes["added_roles"]])
            embed.add_field(name="Added Roles", value=added or "None", inline=False)
            
        if changes.get("removed_roles"):
            removed = "\n".join([f"• {role['name']}" for role in changes["removed_roles"]])
            embed.add_field(name="Removed Roles", value=removed or "None", inline=False)

        try:
            await channel.send(embed=embed)
        except discord.HTTPException as e:
            self.logger.error(f"Failed to send log message: {e}")

def setup(bot):
    bot.add_cog(RoleSync(bot))