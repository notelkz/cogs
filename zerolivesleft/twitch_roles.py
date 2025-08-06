import logging
import discord
import aiohttp
from aiohttp import web
from redbot.core import commands
from typing import Optional, Dict, Any
import asyncio

log = logging.getLogger("red.Elkz.zerolivesleft.twitch_roles")

class TwitchRolesLogic:
    """
    Handles Twitch integration for automatic role assignment to verified streamers.
    """
    
    def __init__(self, cog):
        self.cog = cog
        self.bot = cog.bot
        self.config = cog.config
        
    def register_routes(self, app: web.Application):
        """Register web routes for Twitch integration"""
        app.router.add_post('/twitch/verify', self.handle_twitch_verification)
        app.router.add_get('/twitch/status', self.handle_twitch_status)
        log.info("Twitch roles web routes registered")
    
    async def handle_twitch_verification(self, request):
        """Handle incoming Twitch verification webhooks"""
        try:
            data = await request.json()
            guild_id = data.get('guild_id')
            user_id = data.get('user_id')
            twitch_username = data.get('twitch_username')
            verified = data.get('verified', False)
            
            if not all([guild_id, user_id, twitch_username]):
                return web.json_response({'error': 'Missing required fields'}, status=400)
            
            guild = self.bot.get_guild(int(guild_id))
            if not guild:
                return web.json_response({'error': 'Guild not found'}, status=404)
            
            member = guild.get_member(int(user_id))
            if not member:
                return web.json_response({'error': 'Member not found'}, status=404)
            
            # Process the verification
            await self._process_twitch_verification(guild, member, twitch_username, verified)
            
            return web.json_response({'success': True})
            
        except Exception as e:
            log.error(f"Error handling Twitch verification: {e}")
            return web.json_response({'error': 'Internal server error'}, status=500)
    
    async def handle_twitch_status(self, request):
        """Handle status requests for Twitch integration"""
        return web.json_response({'status': 'Twitch integration active'})
    
    async def _process_twitch_verification(self, guild: discord.Guild, member: discord.Member, 
                                         twitch_username: str, verified: bool):
        """Process a Twitch verification result"""
        try:
            twitch_role_id = await self.config.guild(guild).twitch_role_id()
            auto_assign = await self.config.guild(guild).twitch_auto_assign()
            log_channel_id = await self.config.guild(guild).twitch_log_channel_id()
            
            if not twitch_role_id:
                log.warning(f"No Twitch role configured for guild {guild.id}")
                return
            
            twitch_role = guild.get_role(twitch_role_id)
            if not twitch_role:
                log.error(f"Twitch role {twitch_role_id} not found in guild {guild.id}")
                return
            
            # Assign or remove role based on verification status
            if verified and auto_assign:
                if twitch_role not in member.roles:
                    await member.add_roles(twitch_role, reason=f"Verified Twitch streamer: {twitch_username}")
                    log.info(f"Added Twitch role to {member} ({twitch_username})")
                    
                    # Log to channel if configured
                    if log_channel_id:
                        log_channel = guild.get_channel(log_channel_id)
                        if log_channel:
                            embed = discord.Embed(
                                title="🎮 Twitch Verification",
                                description=f"{member.mention} has been verified as Twitch streamer **{twitch_username}** and given the {twitch_role.mention} role.",
                                color=discord.Color.purple()
                            )
                            embed.set_thumbnail(url=member.display_avatar.url)
                            await log_channel.send(embed=embed)
            
            elif not verified and twitch_role in member.roles:
                await member.remove_roles(twitch_role, reason=f"Twitch verification failed for: {twitch_username}")
                log.info(f"Removed Twitch role from {member} ({twitch_username})")
                
                # Log to channel if configured
                if log_channel_id:
                    log_channel = guild.get_channel(log_channel_id)
                    if log_channel:
                        embed = discord.Embed(
                            title="❌ Twitch Verification Failed",
                            description=f"{member.mention}'s Twitch verification for **{twitch_username}** failed. {twitch_role.mention} role removed.",
                            color=discord.Color.red()
                        )
                        embed.set_thumbnail(url=member.display_avatar.url)
                        await log_channel.send(embed=embed)
                        
        except Exception as e:
            log.error(f"Error processing Twitch verification for {member}: {e}")
    
    async def set_twitch_role(self, ctx, role: discord.Role):
        """Set the role to assign to verified Twitch streamers."""
        await self.config.guild(ctx.guild).twitch_role_id.set(role.id)
        embed = discord.Embed(
            title="🎮 Twitch Role Set",
            description=f"Verified Twitch streamers will now receive the {role.mention} role.",
            color=discord.Color.purple()
        )
        await ctx.send(embed=embed)
    
    async def set_log_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel for Twitch verification logs."""
        await self.config.guild(ctx.guild).twitch_log_channel_id.set(channel.id)
        embed = discord.Embed(
            title="📝 Twitch Log Channel Set",
            description=f"Twitch verification logs will now be sent to {channel.mention}.",
            color=discord.Color.purple()
        )
        await ctx.send(embed=embed)
    
    async def set_auto_assign(self, ctx, enabled: bool):
        """Enable or disable automatic role assignment for verified streamers."""
        await self.config.guild(ctx.guild).twitch_auto_assign.set(enabled)
        status = "enabled" if enabled else "disabled"
        embed = discord.Embed(
            title="⚙️ Auto-Assignment Updated",
            description=f"Automatic role assignment for verified Twitch streamers is now **{status}**.",
            color=discord.Color.green() if enabled else discord.Color.red()
        )
        await ctx.send(embed=embed)
    
    async def set_require_verification(self, ctx, enabled: bool):
        """Enable or disable requirement for Twitch verification."""
        await self.config.guild(ctx.guild).twitch_require_verification.set(enabled)
        status = "required" if enabled else "not required"
        embed = discord.Embed(
            title="🔒 Verification Requirement Updated",
            description=f"Twitch verification is now **{status}** for role assignment.",
            color=discord.Color.green() if enabled else discord.Color.orange()
        )
        await ctx.send(embed=embed)
    
    async def show_config(self, ctx):
        """Show current Twitch integration configuration."""
        guild_config = self.config.guild(ctx.guild)
        
        twitch_role_id = await guild_config.twitch_role_id()
        auto_assign = await guild_config.twitch_auto_assign()
        log_channel_id = await guild_config.twitch_log_channel_id()
        require_verification = await guild_config.twitch_require_verification()
        
        embed = discord.Embed(
            title="🎮 Twitch Integration Configuration",
            color=discord.Color.purple()
        )
        
        # Twitch role
        if twitch_role_id:
            twitch_role = ctx.guild.get_role(twitch_role_id)
            role_info = twitch_role.mention if twitch_role else f"❌ Role not found (ID: {twitch_role_id})"
        else:
            role_info = "❌ Not set"
        embed.add_field(name="Twitch Role", value=role_info, inline=True)
        
        # Auto-assignment
        embed.add_field(
            name="Auto-Assignment", 
            value="✅ Enabled" if auto_assign else "❌ Disabled", 
            inline=True
        )
        
        # Verification requirement
        embed.add_field(
            name="Require Verification", 
            value="✅ Required" if require_verification else "❌ Not Required", 
            inline=True
        )
        
        # Log channel
        if log_channel_id:
            log_channel = ctx.guild.get_channel(log_channel_id)
            channel_info = log_channel.mention if log_channel else f"❌ Channel not found (ID: {log_channel_id})"
        else:
            channel_info = "❌ Not set"
        embed.add_field(name="Log Channel", value=channel_info, inline=True)
        
        # Webhook endpoint
        host = await self.cog.config.webserver_host()
        port = await self.cog.config.webserver_port()
        embed.add_field(
            name="Webhook Endpoint", 
            value=f"`http://{host}:{port}/twitch/verify`", 
            inline=False
        )
        
        embed.set_footer(text="Use the commands under 'zll twitch' to configure these settings.")
        await ctx.send(embed=embed)
    
    async def verify_user(self, ctx, member: discord.Member = None):
        """Manually verify a user's Twitch status and assign role if applicable."""
        if not member:
            member = ctx.author
        
        # This is a placeholder - in a real implementation, you'd integrate with Twitch API
        # For now, we'll just show what the process would look like
        embed = discord.Embed(
            title="🔍 Manual Twitch Verification",
            description=f"Manual verification for {member.mention} would be processed here.\n\n"
                       "**Note:** This requires integration with your website's Twitch verification system.",
            color=discord.Color.orange()
        )
        embed.add_field(
            name="Next Steps",
            value="• Connect this to your website's Twitch API integration\n"
                  "• Verify the user's Twitch account status\n"
                  "• Assign role based on verification result",
            inline=False
        )
        await ctx.send(embed=embed)
    
    async def refresh_all_users(self, ctx):
        """Refresh Twitch verification status for all members with the Twitch role."""
        twitch_role_id = await self.config.guild(ctx.guild).twitch_role_id()
        
        if not twitch_role_id:
            embed = discord.Embed(
                title="❌ Configuration Error",
                description="No Twitch role has been configured. Use `!zll twitch setrole` first.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return
        
        twitch_role = ctx.guild.get_role(twitch_role_id)
        if not twitch_role:
            embed = discord.Embed(
                title="❌ Role Not Found",
                description=f"The configured Twitch role (ID: {twitch_role_id}) was not found.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            return
        
        members_with_role = [member for member in ctx.guild.members if twitch_role in member.roles]
        
        embed = discord.Embed(
            title="🔄 Twitch Verification Refresh",
            description=f"Found {len(members_with_role)} members with the {twitch_role.mention} role.\n\n"
                       "**Note:** This would refresh verification status for all these members through your website's API.",
            color=discord.Color.blue()
        )
        
        if members_with_role:
            member_list = ", ".join([member.display_name for member in members_with_role[:10]])
            if len(members_with_role) > 10:
                member_list += f" and {len(members_with_role) - 10} more..."
            embed.add_field(name="Members to Refresh", value=member_list, inline=False)
        
        embed.add_field(
            name="Implementation Required",
            value="Connect this to your website's API to actually refresh verification statuses.",
            inline=False
        )
        
        await ctx.send(embed=embed)