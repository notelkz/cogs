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
    """

    def __init__(self, cog):
        self.cog = cog
        self.bot = cog.bot
        self.config = cog.config
        self.session = cog.session
        
        # Register global settings
        self.config.register_global(
            ar_api_url=None,
            ar_api_key=None,
            ar_region_roles={},
            ar_enabled=True,
            ar_default_guild_id=None,
            ar_invite_channel_id=None,
            ar_pending_role_id=None,  # Role for new members awaiting approval
            ar_member_role_id=None,   # Main role for approved members
        )
        
        self.guild_invites = {}
        
        self.bot.add_listener(self.on_member_join, "on_member_join")
        self.bot.add_listener(self.on_invite_create, "on_invite_create")
        
        self.cache_invites_task = asyncio.create_task(self.cache_all_invites())
    
    async def on_member_join(self, member: discord.Member):
        """Event handler to assign a 'Pending' role to new members."""
        guild = member.guild
        log.info(f"New member joined: {member.name} ({member.id}) in guild {guild.name}.")

        pending_role_id = await self.config.ar_pending_role_id()
        if not pending_role_id:
            log.warning("Pending role ID is not configured. Cannot assign role.")
            return

        try:
            pending_role = guild.get_role(int(pending_role_id))
            if pending_role:
                await member.add_roles(pending_role, reason="New member, awaiting application approval.")
                log.info(f"Assigned 'Pending' role to {member.name}.")
            else:
                log.error(f"Could not find the 'Pending' role with ID {pending_role_id} in guild {guild.name}.")
        except Exception as e:
            log.error(f"Failed to assign 'Pending' role to {member.name}: {e}")
            
    async def handle_application_update(self, request):
        """Handle application status updates (approved/rejected) from the website."""
        api_key = request.headers.get('Authorization', '').replace('Token ', '')
        expected_key = await self.config.ar_api_key()
        if not api_key or api_key != expected_key:
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
            discord_id = data.get("discord_id")
            status = data.get("status")
            app_data = data.get("application_data", {})
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        if not discord_id or not status:
            return web.json_response({"error": "Missing discord_id or status"}, status=400)

        guild_id = await self.config.ar_default_guild_id()
        if not guild_id:
            return web.json_response({"error": "Default guild not configured in bot"}, status=500)
        
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            return web.json_response({"error": "Guild not found by bot"}, status=500)

        member = guild.get_member(int(discord_id))
        if not member:
            return web.json_response({"error": f"User with ID {discord_id} not found in the Discord server"}, status=404)

        pending_role_id = await self.config.ar_pending_role_id()
        pending_role = guild.get_role(int(pending_role_id)) if pending_role_id else None

        if status == "rejected":
            if pending_role and pending_role in member.roles:
                await member.remove_roles(pending_role, reason="Application Rejected.")
            await member.kick(reason="Application Rejected.")
            log.info(f"Kicked {member.name} ({discord_id}) due to rejected application.")
            return web.json_response({"success": True, "action": "kicked"})

        if status == "approved":
            roles_to_add = []
            roles_to_remove = [pending_role] if pending_role and pending_role in member.roles else []

            member_role_id = await self.config.ar_member_role_id()
            if member_role_id:
                member_role = guild.get_role(int(member_role_id))
                if member_role: roles_to_add.append(member_role)
                else: log.warning(f"Configured Member role ({member_role_id}) not found.")
            
            region_code = app_data.get("region")
            if region_code:
                region_roles_map = await self.config.ar_region_roles()
                region_role_id = region_roles_map.get(region_code)
                if region_role_id:
                    region_role = guild.get_role(int(region_role_id))
                    if region_role: roles_to_add.append(region_role)
                    else: log.warning(f"Configured Region role ({region_role_id}) not found.")

            game_role_ids = app_data.get("game_role_ids", [])
            for role_id in game_role_ids:
                game_role = guild.get_role(int(role_id))
                if game_role: roles_to_add.append(game_role)
                else: log.warning(f"Game role with ID {role_id} not found.")

            if roles_to_add: await member.add_roles(*roles_to_add, reason="Application Approved")
            if roles_to_remove: await member.remove_roles(*[r for r in roles_to_remove if r is not None], reason="Application Approved")

            log.info(f"Updated roles for approved applicant {member.name} ({discord_id}).")
            return web.json_response({"success": True, "action": "roles_updated"})

        return web.json_response({"error": "Invalid status provided"}, status=400)

    async def handle_application_approved(self, request):
        """DEPRECATED: This endpoint is part of the old invite-based workflow."""
        log.warning("Deprecated endpoint /api/applications/approved was called.")
        return web.json_response({"error": "This endpoint is deprecated. Use /api/applications/update-status instead."}, status=410)
    
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
    
    # --- Commands ---
    async def set_pending_role(self, ctx, role: discord.Role):
        """Sets the role for pending applicants."""
        await self.config.ar_pending_role_id.set(str(role.id))
        await ctx.send(f"Pending role has been set to **{role.name}**.")

    async def set_member_role(self, ctx, role: discord.Role):
        """Sets the main role for approved members."""
        await self.config.ar_member_role_id.set(str(role.id))
        await ctx.send(f"Main member role has been set to **{role.name}**.")
    
    async def set_api_url(self, ctx, url):
        await self.config.ar_api_url.set(url)
        await ctx.send(f"API URL set to: {url}")
    
    async def set_api_key(self, ctx, key):
        await self.config.ar_api_key.set(key)
        await ctx.send("API key set successfully.")
    
    async def toggle_enabled(self, ctx, enabled):
        await self.config.ar_enabled.set(enabled)
        status = "enabled" if enabled else "disabled"
        await ctx.send(f"Application role assignment is now {status}.")
    
    async def add_region_role(self, ctx, region, role):
        region_roles = await self.config.ar_region_roles()
        region_roles[region] = str(role.id)
        await self.config.ar_region_roles.set(region_roles)
        await ctx.send(f"Added region role mapping: {region} -> {role.name}")
    
    async def remove_region_role(self, ctx, region):
        region_roles = await self.config.ar_region_roles()
        if region in region_roles:
            del region_roles[region]
            await self.config.ar_region_roles.set(region_roles)
            await ctx.send(f"Removed region role mapping for {region}")
        else:
            await ctx.send(f"No region role mapping found for {region}")
    
    async def list_region_roles(self, ctx):
        region_roles = await self.config.ar_region_roles()
        if not region_roles:
            return await ctx.send("No region role mappings configured.")
        embed = discord.Embed(title="Region Role Mappings", color=discord.Color.blue())
        for region, role_id in region_roles.items():
            role = ctx.guild.get_role(int(role_id))
            embed.add_field(name=region, value=role.name if role else f"Unknown Role ({role_id})", inline=True)
        await ctx.send(embed=embed)
    
    async def show_config(self, ctx):
        all_config = await self.config.all()
        embed = discord.Embed(title="Application Roles Configuration", color=discord.Color.blue())
        for key, value in all_config.items():
            key_name = key.replace('ar_', '').replace('_', ' ').title()
            if key == 'ar_api_key': value_str = 'Set' if value else 'Not set'
            elif key == 'ar_region_roles': value_str = '\n'.join([f"{k}: {v}" for k,v in value.items()]) if value else "None"
            else: value_str = str(value) if value is not None else 'Not set'
            embed.add_field(name=key_name, value=value_str, inline=False)
        await ctx.send(embed=embed)
    
    async def force_cache_invites(self, ctx):
        await ctx.send("Refreshing invite cache...")
        await self.cache_all_invites()
        await ctx.send("Invite cache refreshed.")
    
    async def set_default_guild(self, ctx, guild):
        await self.config.ar_default_guild_id.set(str(guild.id))
        await ctx.send(f"Default guild set to: {guild.name}")
    
    async def set_invite_channel(self, ctx, channel):
        if not channel.permissions_for(ctx.guild.me).create_instant_invite:
            return await ctx.send(f"I don't have permission to create invites in {channel.mention}")
        await self.config.ar_invite_channel_id.set(str(channel.id))
        await ctx.send(f"Invite channel set to: {channel.mention}")
    
    def stop_tasks(self):
        if hasattr(self, 'cache_invites_task') and self.cache_invites_task:
            self.cache_invites_task.cancel()