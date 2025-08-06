# RedBot Cog: twitchroles.py
import logging
import discord
from redbot.core import commands, Config
from redbot.core.utils.chat_formatting import box
from aiohttp import web
import aiohttp
import json

log = logging.getLogger("red.Elkz.twitchroles")

class TwitchRoles(commands.Cog):
    """Manage Twitch streamer roles when users connect their accounts"""
    
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        
        # Default settings
        default_guild = {
            "twitch_role_id": None,
            "auto_assign": True,
            "log_channel_id": None,
            "require_verification": False
        }
        
        self.config.register_guild(**default_guild)
        
        # Web server for webhooks
        self.app = web.Application()
        self.app.router.add_post('/twitch-role-update', self.handle_twitch_role_webhook)
        
    async def cog_load(self):
        """Start the webhook server when cog loads"""
        try:
            # This should integrate with your existing API server
            log.info("TwitchRoles cog loaded - webhook endpoint ready at /twitch-role-update")
        except Exception as e:
            log.error(f"Failed to start TwitchRoles webhook server: {e}")
    
    @commands.group(name="twitchroles")
    @commands.admin_or_permissions(manage_roles=True)
    async def twitch_roles(self, ctx):
        """Configure Twitch role assignments"""
        if ctx.invoked_subcommand is None:
            guild_config = await self.config.guild(ctx.guild).all()
            role_id = guild_config["twitch_role_id"]
            role = ctx.guild.get_role(role_id) if role_id else None
            
            embed = discord.Embed(title="Twitch Roles Configuration", color=0x9146FF)
            embed.add_field(
                name="Twitch Role", 
                value=role.mention if role else "Not set", 
                inline=False
            )
            embed.add_field(
                name="Auto Assign", 
                value="✅ Enabled" if guild_config["auto_assign"] else "❌ Disabled", 
                inline=True
            )
            embed.add_field(
                name="Log Channel", 
                value=f"<#{guild_config['log_channel_id']}>" if guild_config["log_channel_id"] else "Not set", 
                inline=True
            )
            
            await ctx.send(embed=embed)
    
    @twitch_roles.command(name="setrole")
    async def set_twitch_role(self, ctx, role: discord.Role):
        """Set the role to assign to users who connect their Twitch accounts"""
        await self.config.guild(ctx.guild).twitch_role_id.set(role.id)
        
        embed = discord.Embed(
            title="✅ Twitch Role Set", 
            description=f"Users who connect their Twitch accounts will now receive the {role.mention} role.",
            color=0x9146FF
        )
        await ctx.send(embed=embed)
    
    @twitch_roles.command(name="toggle")
    async def toggle_auto_assign(self, ctx):
        """Toggle automatic role assignment on/off"""
        current = await self.config.guild(ctx.guild).auto_assign()
        new_value = not current
        await self.config.guild(ctx.guild).auto_assign.set(new_value)
        
        status = "✅ Enabled" if new_value else "❌ Disabled"
        embed = discord.Embed(
            title="Auto Assignment Updated", 
            description=f"Automatic Twitch role assignment is now {status}",
            color=0x9146FF
        )
        await ctx.send(embed=embed)
    
    @twitch_roles.command(name="logchannel")
    async def set_log_channel(self, ctx, channel: discord.TextChannel = None):
        """Set the channel for Twitch role assignment logs"""
        if channel is None:
            channel = ctx.channel
        
        await self.config.guild(ctx.guild).log_channel_id.set(channel.id)
        
        embed = discord.Embed(
            title="✅ Log Channel Set", 
            description=f"Twitch role assignment logs will be sent to {channel.mention}",
            color=0x9146FF
        )
        await ctx.send(embed=embed)
    
    @twitch_roles.command(name="list")
    async def list_twitch_users(self, ctx):
        """List all users with the Twitch role"""
        guild_config = await self.config.guild(ctx.guild).all()
        role_id = guild_config["twitch_role_id"]
        
        if not role_id:
            await ctx.send("❌ No Twitch role has been set. Use `[p]twitchroles setrole` first.")
            return
        
        role = ctx.guild.get_role(role_id)
        if not role:
            await ctx.send("❌ The configured Twitch role no longer exists.")
            return
        
        if not role.members:
            await ctx.send("📭 No users currently have the Twitch role.")
            return
        
        member_list = []
        for member in role.members:
            member_list.append(f"• {member.display_name} ({member.name})")
        
        embed = discord.Embed(
            title=f"🎮 Users with {role.name} Role",
            description="\n".join(member_list[:20]),  # Limit to 20 to avoid embed limits
            color=0x9146FF
        )
        
        if len(role.members) > 20:
            embed.set_footer(text=f"Showing 20 of {len(role.members)} users")
        
        await ctx.send(embed=embed)
    
    async def handle_twitch_role_webhook(self, request):
        """Handle webhook from Django website for Twitch role updates"""
        try:
            # Verify API key
            auth_header = request.headers.get('Authorization', '')
            expected_token = self.bot.get_cog("APIHandler").api_key  # Assumes you have an API handler
            
            if not auth_header.startswith('Token ') or auth_header[6:] != expected_token:
                log.warning("Unauthorized Twitch role webhook attempt")
                return web.json_response({"error": "Unauthorized"}, status=401)
            
            data = await request.json()
            discord_id = data.get('discord_id')
            action = data.get('action')
            twitch_data = data.get('twitch_data', {})
            
            if not discord_id or not action:
                return web.json_response({"error": "Missing required fields"}, status=400)
            
            # Process the role update
            result = await self.process_role_update(discord_id, action, twitch_data)
            
            return web.json_response({"status": "success", "result": result})
            
        except Exception as e:
            log.error(f"Error in Twitch role webhook: {e}")
            return web.json_response({"error": "Internal server error"}, status=500)
    
    async def process_role_update(self, discord_id: str, action: str, twitch_data: dict):
        """Process the role update for a user"""
        try:
            user_id = int(discord_id)
            results = []
            
            for guild in self.bot.guilds:
                guild_config = await self.config.guild(guild).all()
                
                if not guild_config["auto_assign"] or not guild_config["twitch_role_id"]:
                    continue
                
                role = guild.get_role(guild_config["twitch_role_id"])
                if not role:
                    continue
                
                member = guild.get_member(user_id)
                if not member:
                    continue
                
                try:
                    if action == "connected":
                        if role not in member.roles:
                            await member.add_roles(role, reason="Connected Twitch account")
                            results.append(f"Added {role.name} role in {guild.name}")
                            await self.log_role_change(guild, member, role, "added", twitch_data)
                    
                    elif action == "disconnected":
                        if role in member.roles:
                            await member.remove_roles(role, reason="Disconnected Twitch account")
                            results.append(f"Removed {role.name} role in {guild.name}")
                            await self.log_role_change(guild, member, role, "removed", twitch_data)
                    
                    # For visibility changes, we might want to keep the role but track it
                    elif action in ["visibility_enabled", "visibility_disabled"]:
                        # Could implement special handling here if needed
                        results.append(f"Updated visibility status in {guild.name}")
                
                except discord.HTTPException as e:
                    log.error(f"Failed to update role for {member} in {guild}: {e}")
                    results.append(f"Failed to update role in {guild.name}: {e}")
            
            return results
            
        except Exception as e:
            log.error(f"Error processing role update: {e}")
            return [f"Error: {e}"]
    
    async def log_role_change(self, guild, member, role, action, twitch_data):
        """Log role changes to the configured log channel"""
        guild_config = await self.config.guild(guild).all()
        log_channel_id = guild_config["log_channel_id"]
        
        if not log_channel_id:
            return
        
        log_channel = guild.get_channel(log_channel_id)
        if not log_channel:
            return
        
        try:
            twitch_username = twitch_data.get('twitch_username', 'Unknown')
            
            color = 0x00FF00 if action == "added" else 0xFF0000
            embed = discord.Embed(
                title=f"🎮 Twitch Role {action.title()}",
                color=color,
                timestamp=discord.utils.utcnow()
            )
            
            embed.add_field(name="User", value=member.mention, inline=True)
            embed.add_field(name="Role", value=role.mention, inline=True)
            embed.add_field(name="Twitch", value=f"@{twitch_username}", inline=True)
            embed.add_field(name="Action", value=action.title(), inline=False)
            
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=f"User ID: {member.id}")
            
            await log_channel.send(embed=embed)
            
        except Exception as e:
            log.error(f"Failed to log role change: {e}")
    
    @twitch_roles.command(name="sync")
    @commands.is_owner()
    async def sync_existing_users(self, ctx):
        """Sync roles for users who already have Twitch accounts connected"""
        # This would require an API call to your Django website to get current Twitch users
        embed = discord.Embed(
            title="🔄 Sync Started",
            description="This feature requires integration with the website API to fetch current Twitch users.",
            color=0x9146FF
        )
        await ctx.send(embed=embed)

def setup(bot):
    bot.add_cog(TwitchRoles(bot))