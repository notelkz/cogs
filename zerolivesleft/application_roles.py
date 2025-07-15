# zerolivesleft/application_roles.py

import discord
import logging
import asyncio
from typing import Dict, List, Optional, Tuple
import aiohttp
from datetime import datetime, timedelta
from aiohttp import web

log = logging.getLogger("red.Elkz.zerolivesleft.application_roles")

class ApplicationRolesLogic:
    """
    Handles assigning roles to new members based on their application choices.
    Tracks Discord invites to match joining members with their applications.
    """

    def __init__(self, cog):
        self.cog = cog
        self.bot = cog.bot
        self.config = cog.config
        self.session = cog.session
        
        # Register global settings
        self.config.register_global(
            ar_api_url=None,  # URL to fetch application data
            ar_api_key=None,  # API key for authentication
            ar_region_roles={},  # Mapping of region names to role IDs
            ar_enabled=True,  # Whether role assignment is enabled
            ar_default_guild_id=None,  # Default guild to create invites for
            ar_invite_channel_id=None,  # Channel to create invites in
        )
        
        # Cache of guild invites for tracking which invite was used
        self.guild_invites = {}
        
        # Register the event listener
        self.bot.add_listener(self.on_member_join, "on_member_join")
        self.bot.add_listener(self.on_invite_create, "on_invite_create")
        
        # Start the task to cache all invites when the bot starts
        self.cache_invites_task = asyncio.create_task(self.cache_all_invites())
    
    async def handle_application_approved(self, request):
        """Handle notification that an application has been approved."""
        # Verify API key
        api_key = request.headers.get('Authorization', '').replace('Token ', '')
        expected_key = await self.config.ar_api_key()
        
        if not api_key or api_key != expected_key:
            return web.json_response({"error": "Unauthorized"}, status=401)
        
        try:
            data = await request.json()
            application_id = data.get('application_id')
            
            if not application_id:
                return web.json_response({"error": "Missing application_id"}, status=400)
            
            # Get the guild ID from the request or config
            guild_id = data.get('guild_id') or await self.config.ar_default_guild_id()
            if not guild_id:
                return web.json_response({"error": "No guild specified"}, status=400)
            
            guild = self.bot.get_guild(int(guild_id))
            if not guild:
                return web.json_response({"error": "Guild not found"}, status=404)
            
            # Generate an invite
            invite_channel_id = await self.config.ar_invite_channel_id()
            if not invite_channel_id:
                # Use the first text channel we have permission for
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).create_instant_invite:
                        invite_channel_id = channel.id
                        break
            
            if not invite_channel_id:
                return web.json_response({"error": "No suitable channel found for invite"}, status=500)
            
            channel = guild.get_channel(int(invite_channel_id))
            if not channel:
                return web.json_response({"error": "Invite channel not found"}, status=404)
            
            # Create the invite with a 24-hour expiration and single use
            invite = await channel.create_invite(
                max_age=86400,  # 24 hours
                max_uses=1,     # Single use
                unique=True,
                reason=f"Application approved: {application_id}"
            )
            
            # Return the invite URL
            return web.json_response({
                "success": True,
                "invite_url": invite.url,
                "invite_code": invite.code,
                "expires_at": (datetime.utcnow() + timedelta(seconds=invite.max_age)).isoformat() if invite.max_age else None
            })
            
        except Exception as e:
            log.error(f"Error handling application approval: {e}")
            return web.json_response({"error": str(e)}, status=500)
    
    async def cache_all_invites(self):
        """Cache all invites for all guilds the bot is in."""
        await self.bot.wait_until_ready()
        log.info("Caching all guild invites...")
        
        for guild in self.bot.guilds:
            try:
                invites = await guild.invites()
                self.guild_invites[guild.id] = invites
                log.info(f"Cached {len(invites)} invites for guild {guild.name} ({guild.id})")
            except (discord.Forbidden, discord.HTTPException) as e:
                log.error(f"Error caching invites for guild {guild.name} ({guild.id}): {e}")
        
        log.info("Finished caching all guild invites")
    
    async def on_invite_create(self, invite):
        """Event handler for when a new invite is created."""
        try:
            if invite.guild.id not in self.guild_invites:
                self.guild_invites[invite.guild.id] = []
            
            self.guild_invites[invite.guild.id].append(invite)
            log.info(f"New invite created and cached: {invite.code} for guild {invite.guild.name}")
        except Exception as e:
            log.error(f"Error handling new invite: {e}")
    
    async def on_member_join(self, member):
        """Event handler for when a member joins a guild."""
        try:
            guild = member.guild
            log.info(f"Member joined: {member.name} ({member.id}) to guild {guild.name}")
            
            # Check if role assignment is enabled
            enabled = await self.config.ar_enabled()
            if not enabled:
                log.info("Application role assignment is disabled. Skipping.")
                return
            
            # Get the invite used
            invite_used = None
            invite_code = None
            
            # Get fresh invites
            try:
                new_invites = await guild.invites()
            except (discord.Forbidden, discord.HTTPException) as e:
                log.error(f"Error fetching invites for guild {guild.name}: {e}")
                return
            
            # Find the invite that was used by comparing with our cached invites
            if guild.id in self.guild_invites:
                for invite in new_invites:
                    for cached_invite in self.guild_invites[guild.id]:
                        if invite.code == cached_invite.code and invite.uses > cached_invite.uses:
                            invite_used = invite
                            invite_code = invite.code
                            break
                    if invite_used:
                        break
            
            # Update our cache
            self.guild_invites[guild.id] = new_invites
            
            if invite_code:
                log.info(f"Invite used: {invite_code}")
                
                # Fetch application data from the API
                application = await self.fetch_application_by_invite_code(invite_code)
                
                if not application:
                    log.warning(f"No application found for invite code {invite_code}")
                    
                    # Try to find by Discord ID as fallback
                    application = await self.fetch_application_by_discord_id(str(member.id))
                    
                    if not application:
                        log.warning(f"No application found for Discord ID {member.id} either")
                        return
            else:
                # If we couldn't determine the invite, try to find by Discord ID
                log.warning("Could not determine which invite was used")
                application = await self.fetch_application_by_discord_id(str(member.id))
                
                if not application:
                    log.warning(f"No application found for Discord ID {member.id}")
                    return
            
            log.info(f"Found application for {application.get('display_name', 'Unknown')}")
            
            # Update the application to mark that the user has joined
            await self.update_application_joined_status(application.get('id'))
            
            # Get the games they selected
            selected_games = application.get('games', [])
            
            # Assign roles based on selected games
            roles_assigned = 0
            
            for game in selected_games:
                discord_role_id = game.get('discord_role_id')
                if not discord_role_id:
                    log.warning(f"Game {game.get('name')} has no Discord role ID")
                    continue
                    
                try:
                    role = guild.get_role(int(discord_role_id))
                    if role:
                        await member.add_roles(role, reason=f"Auto-assigned from application")
                        roles_assigned += 1
                        log.info(f"Assigned role {role.name} to {member.name}")
                    else:
                        log.warning(f"Role {discord_role_id} not found in guild")
                except Exception as e:
                    log.error(f"Error assigning role for game {game.get('name')}: {str(e)}")
            
            # Assign region role if applicable
            region = application.get('region')
            if region:
                region_roles = await self.config.ar_region_roles()
                if region in region_roles:
                    try:
                        role_id = int(region_roles[region])
                        role = guild.get_role(role_id)
                        if role:
                            await member.add_roles(role, reason=f"Auto-assigned from application (region)")
                            roles_assigned += 1
                            log.info(f"Assigned region role {role.name} to {member.name}")
                    except Exception as e:
                        log.error(f"Error assigning region role: {str(e)}")
            
            log.info(f"Assigned {roles_assigned} roles to {member.name}")
            
        except Exception as e:
            log.error(f"Error processing member join for {member.name}: {str(e)}")
    
    async def fetch_application_by_invite_code(self, invite_code):
        """Fetch application data from the API by invite code."""
        api_url = await self.config.ar_api_url()
        api_key = await self.config.ar_api_key()
        
        if not api_url or not api_key:
            log.error("API URL or API key not configured")
            return None
        
        url = f"{api_url.rstrip('/')}/applications/by-invite/{invite_code}/"
        headers = {"Authorization": f"Token {api_key}", "Content-Type": "application/json"}
        
        try:
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    log.error(f"API error: {response.status} - {await response.text()}")
                    return None
        except Exception as e:
            log.error(f"Error fetching application by invite code: {e}")
            return None
    
    async def fetch_application_by_discord_id(self, discord_id):
        """Fetch application data from the API by Discord ID."""
        api_url = await self.config.ar_api_url()
        api_key = await self.config.ar_api_key()
        
        if not api_url or not api_key:
            log.error("API URL or API key not configured")
            return None
        
        url = f"{api_url.rstrip('/')}/applications/by-discord/{discord_id}/"
        headers = {"Authorization": f"Token {api_key}", "Content-Type": "application/json"}
        
        try:
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    log.error(f"API error: {response.status} - {await response.text()}")
                    return None
        except Exception as e:
            log.error(f"Error fetching application by Discord ID: {e}")
            return None
    
    async def update_application_joined_status(self, application_id):
        """Update the application to mark that the user has joined."""
        api_url = await self.config.ar_api_url()
        api_key = await self.config.ar_api_key()
        
        if not api_url or not api_key:
            log.error("API URL or API key not configured")
            return False
        
        url = f"{api_url.rstrip('/')}/applications/{application_id}/joined/"
        headers = {"Authorization": f"Token {api_key}", "Content-Type": "application/json"}
        
        try:
            async with self.session.post(url, headers=headers, json={"joined": True}) as response:
                if response.status == 200:
                    log.info(f"Updated application {application_id} joined status")
                    return True
                else:
                    log.error(f"API error: {response.status} - {await response.text()}")
                    return False
        except Exception as e:
            log.error(f"Error updating application joined status: {e}")
            return False
    
    # --- Commands ---
    
    async def set_api_url(self, ctx, url):
        """Set the API URL for fetching application data."""
        await self.config.ar_api_url.set(url)
        await ctx.send(f"API URL set to: {url}")
    
    async def set_api_key(self, ctx, key):
        """Set the API key for authentication."""
        await self.config.ar_api_key.set(key)
        await ctx.send("API key set successfully.")
    
    async def toggle_enabled(self, ctx, enabled):
        """Toggle whether role assignment is enabled."""
        await self.config.ar_enabled.set(enabled)
        status = "enabled" if enabled else "disabled"
        await ctx.send(f"Application role assignment is now {status}.")
    
    async def add_region_role(self, ctx, region, role):
        """Add a mapping from region to role ID."""
        region_roles = await self.config.ar_region_roles()
        region_roles[region] = str(role.id)
        await self.config.ar_region_roles.set(region_roles)
        await ctx.send(f"Added region role mapping: {region} -> {role.name}")
    
    async def remove_region_role(self, ctx, region):
        """Remove a region role mapping."""
        region_roles = await self.config.ar_region_roles()
        if region in region_roles:
            del region_roles[region]
            await self.config.ar_region_roles.set(region_roles)
            await ctx.send(f"Removed region role mapping for {region}")
        else:
            await ctx.send(f"No region role mapping found for {region}")
    
    async def list_region_roles(self, ctx):
        """List all region role mappings."""
        region_roles = await self.config.ar_region_roles()
        if not region_roles:
            await ctx.send("No region role mappings configured.")
            return
        
        embed = discord.Embed(
            title="Region Role Mappings",
            color=discord.Color.blue(),
            description="The following region roles will be assigned based on application choices:"
        )
        
        for region, role_id in region_roles.items():
            role = ctx.guild.get_role(int(role_id))
            role_name = role.name if role else f"Unknown Role ({role_id})"
            embed.add_field(name=region, value=role_name, inline=True)
        
        await ctx.send(embed=embed)
    
    async def show_config(self, ctx):
        """Show the current configuration."""
        api_url = await self.config.ar_api_url()
        api_key = await self.config.ar_api_key()
        enabled = await self.config.ar_enabled()
        region_roles = await self.config.ar_region_roles()
        default_guild_id = await self.config.ar_default_guild_id()
        invite_channel_id = await self.config.ar_invite_channel_id()
        
        embed = discord.Embed(
            title="Application Roles Configuration",
            color=discord.Color.blue()
        )
        
        embed.add_field(name="Enabled", value=str(enabled), inline=False)
        embed.add_field(name="API URL", value=api_url or "Not set", inline=False)
        embed.add_field(name="API Key", value="Set" if api_key else "Not set", inline=False)
        
        # Add default guild info
        if default_guild_id:
            guild = self.bot.get_guild(int(default_guild_id))
            guild_name = guild.name if guild else f"Unknown Guild ({default_guild_id})"
            embed.add_field(name="Default Guild", value=guild_name, inline=False)
        else:
            embed.add_field(name="Default Guild", value="Not set", inline=False)
        
        # Add invite channel info
        if invite_channel_id:
            guild = ctx.guild
            channel = guild.get_channel(int(invite_channel_id))
            channel_name = channel.mention if channel else f"Unknown Channel ({invite_channel_id})"
            embed.add_field(name="Invite Channel", value=channel_name, inline=False)
        else:
            embed.add_field(name="Invite Channel", value="Not set", inline=False)
        
        region_roles_text = ""
        for region, role_id in region_roles.items():
            role = ctx.guild.get_role(int(role_id))
            role_name = role.name if role else f"Unknown Role ({role_id})"
            region_roles_text += f"{region}: {role_name}\n"
        
        if not region_roles_text:
            region_roles_text = "No region roles configured"
        
        embed.add_field(name="Region Roles", value=region_roles_text, inline=False)
        
        await ctx.send(embed=embed)
    
    async def force_cache_invites(self, ctx):
        """Force a refresh of the invite cache."""
        await ctx.send("Refreshing invite cache...")
        await self.cache_all_invites()
        await ctx.send("Invite cache refreshed.")
    
    async def set_default_guild(self, ctx, guild):
        """Set the default guild for creating invites."""
        await self.config.ar_default_guild_id.set(str(guild.id))
        await ctx.send(f"Default guild set to: {guild.name}")
    
    async def set_invite_channel(self, ctx, channel):
        """Set the channel for creating invites."""
        # Check if the bot has permission to create invites in this channel
        if not channel.permissions_for(ctx.guild.me).create_instant_invite:
            await ctx.send(f"I don't have permission to create invites in {channel.mention}")
            return
        
        await self.config.ar_invite_channel_id.set(str(channel.id))
        await ctx.send(f"Invite channel set to: {channel.mention}")
    
    def stop_tasks(self):
        """Cancel any running tasks."""
        if hasattr(self, 'cache_invites_task') and self.cache_invites_task:
            self.cache_invites_task.cancel()