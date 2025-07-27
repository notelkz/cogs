# zerolivesleft/application_roles.py
# Complete, updated file

import discord
import logging
import asyncio
import json
from typing import Dict, List, Optional, Tuple
import aiohttp
from datetime import datetime, timedelta
from aiohttp import web
from redbot.core import commands

log = logging.getLogger("red.Elkz.zerolivesleft.application_roles")

class ApplicationRolesLogic:
    """
    Handles assigning roles to new members and updating them upon application approval.
    """

    def __init__(self, cog):
        self.cog = cog
        self.bot = cog.bot
        self.config = cog.config
        self.session = cog.session
        
        self.config.register_global(
            ar_api_url=None,
            ar_api_key=None,
            ar_region_roles={},
            ar_enabled=True,
            ar_default_guild_id=None,
            ar_invite_channel_id=None,
            ar_pending_role_id=None,
            ar_member_role_id=None,
            ar_unverified_role_id=None,
            ar_welcome_channel_id=None,
            ar_welcome_message="Welcome {mention}! To join our community, please submit an application at https://zerolivesleft.net/apply/",
        )
        
        self.guild_invites = {}
        
        self.bot.add_listener(self.on_member_join, "on_member_join")
        self.bot.add_listener(self.on_invite_create, "on_invite_create")
        
        self.cache_invites_task = asyncio.create_task(self.cache_all_invites())
        
        log.info("ApplicationRolesLogic initialized")

    async def on_member_join(self, member: discord.Member):
        if member.bot: return

        guild = member.guild
        log.info(f"New member joined: {member.name} ({member.id}). Checking application status.")
        
        guild_id = await self.config.ar_default_guild_id()
        if not guild_id or guild.id != int(guild_id): return

        api_key = await self.config.ar_api_key()
        api_url = await self.config.ar_api_url()
        
        role_to_assign_id = None
        is_unverified = False
        
        if not api_url or not api_key:
            log.error("Cannot check application status: Application API URL or Key not set. Assigning Unverified.")
            role_to_assign_id = await self.config.ar_unverified_role_id()
            is_unverified = True
        else:
            try:
                endpoint = f"{api_url.rstrip('/')}/api/applications/check/{member.id}/"
                headers = {"Authorization": f"Token {api_key}"}
                
                log.info(f"Checking application status at: {endpoint}")
                async with self.session.get(endpoint, headers=headers, timeout=10) as resp:
                    log.info(f"Application check response: {resp.status}")
                    if resp.status == 200:
                        data = await resp.json()
                        log.info(f"Application check data: {data}")
                        status = data.get("status")
                        
                        if status == "pending":
                            role_to_assign_id = await self.config.ar_pending_role_id()
                            log.info(f"User {member.name} has a pending application. Assigning 'Pending' role.")
                        else:
                            role_to_assign_id = await self.config.ar_unverified_role_id()
                            is_unverified = True
                            log.info(f"User {member.name} does not have a pending application. Assigning 'Unverified' role.")
                    else:
                        log.error(f"Failed to check application status for {member.name}: {resp.status} - {await resp.text()}")
                        role_to_assign_id = await self.config.ar_unverified_role_id()
                        is_unverified = True

            except Exception as e:
                log.error(f"Exception checking application status for {member.name}: {e}")
                role_to_assign_id = await self.config.ar_unverified_role_id()
                is_unverified = True

        if role_to_assign_id and (role := guild.get_role(int(role_to_assign_id))):
            try:
                await member.add_roles(role, reason="New member verification.")
                log.info(f"Successfully assigned '{role.name}' to {member.name}.")
                
                if is_unverified:
                    welcome_channel_id = await self.config.ar_welcome_channel_id()
                    if welcome_channel_id and (channel := guild.get_channel(int(welcome_channel_id))):
                        embed = discord.Embed(
                            title="Welcome to Zero Lives Left!",
                            description=f"To gain access to the rest of the server, you must submit an application on our website. You have been given the **Unverified** role for now.",
                            color=discord.Color.blurple()
                        )
                        embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
                        embed.add_field(
                            name="Application Link",
                            value="[Click here to apply](https://zerolivesleft.net/apply/)",
                            inline=False
                        )
                        embed.set_footer(text="Once your application is approved, your roles will be updated automatically.")
                        await channel.send(content=member.mention, embed=embed)
                        log.info(f"Sent welcome embed to {channel.name} for {member.name}.")
            except Exception as e:
                log.error(f"An error occurred during post-join actions for {member.name}: {e}")
        else:
            log.warning(f"No appropriate role ('Pending' or 'Unverified') could be found or assigned to {member.name}.")

    async def handle_application_update(self, request):
        log.info("Application update endpoint called")
        try:
            body_text = await request.text()
            log.info(f"Request headers: {dict(request.headers)}")
            log.info(f"Request body: {body_text}")
            
            api_key = request.headers.get('Authorization', '').replace('Token ', '')
            expected_key = await self.config.ar_api_key()
            
            if not api_key or api_key != expected_key:
                log.warning(f"API key validation failed for application update")
                return web.json_response({"error": "Unauthorized"}, status=401)
                
            data = await request.json()
            log.info(f"Processing application update: {data}")
            self.cog.bot.loop.create_task(self.process_role_update(data))
            return web.json_response({"success": True, "message": "Request received."})
        except Exception as e:
            log.error(f"Error processing application update request: {e}", exc_info=True)
            return web.json_response({"error": f"Error: {str(e)}"}, status=400)

    async def handle_application_submitted(self, request):
        try:
            log.info(f"Application submission endpoint called: {request.path}")
            
            body_text = await request.text()
            log.info(f"Request headers: {dict(request.headers)}")
            log.info(f"Request body: {body_text}")
            
            auth_header = request.headers.get('Authorization', '')
            log.info(f"Auth header received: '{auth_header}'")
            
            if auth_header.startswith('Token '):
                api_key = auth_header.replace('Token ', '')
            elif auth_header.startswith('Bearer '):
                api_key = auth_header.replace('Bearer ', '')
            else:
                api_key = auth_header
                
            log.info(f"Extracted API key: '{api_key[:5]}...' (if available)")
            
            expected_key = await self.config.ar_api_key()
            log.info(f"Expected API key starts with: '{expected_key[:5] if expected_key else 'None'}...' (if available)")
            
            if not api_key or api_key != expected_key:
                log.warning(f"API key validation failed. Received key doesn't match expected key.")
                return web.json_response({"error": "Unauthorized"}, status=401)
            
            try:
                data = json.loads(body_text)
            except json.JSONDecodeError:
                log.error(f"Failed to parse JSON from body text, trying request.json()")
                try:
                    data = await request.json()
                except Exception as e:
                    log.error(f"Failed to parse JSON body: {e}")
                    return web.json_response({"error": f"Invalid JSON: {str(e)}"}, status=400)
            
            log.info(f"Parsed request data: {data}")
            
            discord_id = data.get("discord_id")
            if not discord_id:
                log.error("Missing discord_id in application submission")
                return web.json_response({"error": "Missing discord_id"}, status=400)

            log.info(f"Processing application submission for Discord ID: {discord_id}")
            
            guild_id = await self.config.ar_default_guild_id()
            if not guild_id:
                log.error("Default guild ID not configured")
                return web.json_response({"error": "Guild not configured"}, status=500)
                
            guild = self.bot.get_guild(int(guild_id))
            if not guild:
                log.error(f"Default guild with ID {guild_id} not found")
                return web.json_response({"error": "Guild not found"}, status=500)
            
            member = guild.get_member(int(discord_id))
            if not member:
                log.error(f"Member with ID {discord_id} not found in guild {guild.name}")
                return web.json_response({"error": "Member not in server"}, status=404)

            log.info(f"Found member {member.name} in guild {guild.name}")
            
            unverified_role_id = await self.config.ar_unverified_role_id()
            pending_role_id = await self.config.ar_pending_role_id()
            
            if not unverified_role_id or not pending_role_id:
                log.error(f"Missing role configuration: Unverified={unverified_role_id}, Pending={pending_role_id}")
                return web.json_response({"error": "Role configuration incomplete"}, status=500)
                
            unverified_role = guild.get_role(int(unverified_role_id))
            pending_role = guild.get_role(int(pending_role_id))

            if not unverified_role or not pending_role:
                log.error(f"Could not find roles: Unverified={bool(unverified_role)}, Pending={bool(pending_role)}")
                return web.json_response({"error": "Roles not found"}, status=500)

            log.info(f"Current roles for {member.name}: {[role.name for role in member.roles]}")
            
            roles_to_remove = []
            if unverified_role in member.roles:
                roles_to_remove.append(unverified_role)
            
            if roles_to_remove:
                log.info(f"Removing roles: {[role.name for role in roles_to_remove]}")
                await member.remove_roles(*roles_to_remove, reason="Application submitted")
                log.info(f"Removed {', '.join([r.name for r in roles_to_remove])} from {member.name}")
                
            if pending_role not in member.roles:
                log.info(f"Adding role: {pending_role.name}")
                await member.add_roles(pending_role, reason="Application submitted")
                log.info(f"Added {pending_role.name} to {member.name}")
            else:
                log.info(f"{member.name} already has the {pending_role.name} role")
            
            log.info(f"Successfully updated roles for {member.name} ({member.id}) after application submission")
            log.info(f"New roles: {[role.name for role in member.roles]}")
            
            return web.json_response({"success": True})
        except Exception as e:
            log.error(f"Error in handle_application_submitted: {e}", exc_info=True)
            return web.json_response({"error": f"Internal server error: {str(e)}"}, status=500)

    async def process_role_update(self, data: dict):
        discord_id = data.get("discord_id")
        status = data.get("status")
        app_data = data.get("application_data", {})
        if not all([discord_id, status]): 
            log.error(f"Missing required data in process_role_update: discord_id={discord_id}, status={status}")
            return

        guild_id = await self.config.ar_default_guild_id()
        if not guild_id: 
            log.error("No default guild configured for role updates")
            return
        
        guild = self.bot.get_guild(int(guild_id))
        if not guild: 
            log.error(f"Could not find guild with ID {guild_id}")
            return
        
        member = guild.get_member(int(discord_id))
        if not member: 
            log.error(f"Could not find member with ID {discord_id} in guild {guild.name}")
            return

        log.info(f"Processing role update for {member.name} with status {status}")

        pending_role = guild.get_role(int(await self.config.ar_pending_role_id() or 0))
        unverified_role = guild.get_role(int(await self.config.ar_unverified_role_id() or 0))

        if status == "rejected":
            log.info(f"Application rejected for {member.name}. Removing roles and kicking.")
            roles_to_remove = [r for r in [pending_role, unverified_role] if r and r in member.roles]
            if roles_to_remove: 
                await member.remove_roles(*roles_to_remove, reason="Application Rejected.")
                log.info(f"Removed roles: {[r.name for r in roles_to_remove]}")
            await member.kick(reason="Application Rejected.")
            log.info(f"Kicked {member.name} due to rejected application")
            return

        if status == "approved":
            log.info(f"Application approved for {member.name}. Updating roles.")
            roles_to_add = []
            roles_to_remove = [r for r in [pending_role, unverified_role] if r and r in member.roles]

            if member_role_id := await self.config.ar_member_role_id():
                if role := guild.get_role(int(member_role_id)): 
                    roles_to_add.append(role)
                    log.info(f"Adding member role: {role.name}")
            
            if region_code := app_data.get("region"):
                region_roles = await self.config.ar_region_roles()
                log.info(f"Checking region role for {region_code.upper()}. Available mappings: {region_roles}")
                if region_role_id := region_roles.get(region_code.upper()):
                    if role := guild.get_role(int(region_role_id)): 
                        roles_to_add.append(role)
                        log.info(f"Adding region role: {role.name}")
            
            for role_type in ["platform_role_ids", "game_role_ids"]:
                role_ids = app_data.get(role_type, [])
                log.info(f"Processing {role_type}: {role_ids}")
                for role_id in role_ids:
                    if role_id and (role := guild.get_role(int(role_id))): 
                        roles_to_add.append(role)
                        log.info(f"Adding {role_type.split('_')[0]} role: {role.name}")
            
            if roles_to_add: 
                await member.add_roles(*roles_to_add, reason="Application Approved")
                log.info(f"Added roles: {[r.name for r in roles_to_add]}")
            if roles_to_remove: 
                await member.remove_roles(*roles_to_remove, reason="Application Approved")
                log.info(f"Removed roles: {[r.name for r in roles_to_remove]}")
            
            log.info(f"Role update complete for {member.name}")
    
    async def cache_all_invites(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try: 
                self.guild_invites[guild.id] = await guild.invites()
                log.info(f"Cached {len(self.guild_invites[guild.id])} invites for guild {guild.name}")
            except (discord.Forbidden, discord.HTTPException) as e: 
                log.error(f"Failed to cache invites for guild {guild.name}: {e}")
    
    async def on_invite_create(self, invite):
        if invite.guild.id not in self.guild_invites: self.guild_invites[invite.guild.id] = []
        self.guild_invites[invite.guild.id].append(invite)
        log.info(f"New invite created and cached for {invite.guild.name}: {invite.code}")

    def stop_tasks(self):
        if hasattr(self, 'cache_invites_task') and self.cache_invites_task and not self.cache_invites_task.done():
            self.cache_invites_task.cancel()
            log.info("Invite cache task cancelled")

    async def set_api_url(self, ctx, url):
        await self.config.ar_api_url.set(url)
        await ctx.send(f"API URL set to: {url}")
        log.info(f"API URL set to: {url}")
    
    async def set_api_key(self, ctx, key):
        await self.config.ar_api_key.set(key)
        await ctx.send("API key set successfully.")
        log.info(f"API key updated")

    async def toggle_enabled(self, ctx, enabled: bool):
        await self.config.ar_enabled.set(enabled)
        await ctx.send(f"Application role assignment is now {'enabled' if enabled else 'disabled'}.")
        log.info(f"Application role assignment {'enabled' if enabled else 'disabled'}")

    async def set_pending_role(self, ctx, role: discord.Role):
        await self.config.ar_pending_role_id.set(role.id)
        await ctx.send(f"Pending role has been set to **{role.name}**.")
        log.info(f"Pending role set to {role.name} ({role.id})")

    async def set_member_role(self, ctx, role: discord.Role):
        await self.config.ar_member_role_id.set(role.id)
        await ctx.send(f"Main member role has been set to **{role.name}**.")
        log.info(f"Member role set to {role.name} ({role.id})")
        
    async def set_unverified_role(self, ctx, role: discord.Role):
        await self.config.ar_unverified_role_id.set(role.id)
        await ctx.send(f"Unverified role has been set to **{role.name}**.")
        log.info(f"Unverified role set to {role.name} ({role.id})")

    async def set_welcome_channel(self, ctx, channel: discord.TextChannel):
        await self.config.ar_welcome_channel_id.set(channel.id)
        await ctx.send(f"Welcome message channel set to {channel.mention}")
        log.info(f"Welcome channel set to {channel.name} ({channel.id})")

    async def set_welcome_message(self, ctx: commands.Context, *, message: str):
        await self.config.ar_welcome_message.set(message)
        await ctx.send(f"Welcome message has been set to:\n\n{message}")
        log.info(f"Welcome message updated")
    
    async def add_region_role(self, ctx, region: str, role: discord.Role):
        async with self.config.ar_region_roles() as region_roles:
            region_roles[region.upper()] = str(role.id)
        await ctx.send(f"Added region role mapping: `{region.upper()}` -> {role.name}")
        log.info(f"Added region role mapping: {region.upper()} -> {role.name} ({role.id})")
    
    async def remove_region_role(self, ctx, region: str):
        async with self.config.ar_region_roles() as region_roles:
            if region.upper() in region_roles:
                del region_roles[region.upper()]
                await ctx.send(f"Removed region role mapping for `{region.upper()}`")
                log.info(f"Removed region role mapping for {region.upper()}")
            else:
                await ctx.send(f"No region role mapping found for `{region.upper()}`.")
                log.info(f"Attempted to remove non-existent region mapping: {region.upper()}")
    
    async def list_region_roles(self, ctx):
        region_roles = await self.config.ar_region_roles()
        if not region_roles: 
            await ctx.send("No region role mappings configured.")
            return
            
        embed = discord.Embed(title="Region Role Mappings", color=discord.Color.blue())
        for region, role_id in region_roles.items():
            role = ctx.guild.get_role(int(role_id))
            embed.add_field(name=region, value=role.mention if role else f"Unknown Role ({role_id})")
        await ctx.send(embed=embed)
        log.info(f"Listed {len(region_roles)} region role mappings")
    
    async def show_config(self, ctx: commands.Context):
        all_config = await self.config.all() # This will get all global settings for the cog
        ar_config = {k: v for k, v in all_config.items() if k.startswith("ar_")}
        embed = discord.Embed(title="Application Roles Configuration", color=await ctx.embed_color())
        for key, value in ar_config.items():
            name = key.replace("ar_", "").replace("_", " ").title()
            if value:
                if "role_id" in key: value_str = f"<@&{value}> (`{value}`)"
                elif key == "ar_region_roles": value_str = "\n".join([f"`{k}`: <@&{v}>" for k, v in value.items()]) or "None"
                elif "api_key" in key: value_str = "`Set`"
                else: value_str = f"`{value}`"
            else:
                value_str = "`Not Set`"
            embed.add_field(name=name, value=value_str, inline=False)
        await ctx.send(embed=embed)
        log.info("Configuration displayed")

    async def force_cache_invites(self, ctx):
        await ctx.send("Refreshing invite cache...")
        await self.cache_all_invites()
        await ctx.send("Invite cache refreshed.")
        log.info("Invite cache manually refreshed")
    
    async def set_default_guild(self, ctx, guild: discord.Guild):
        await self.config.ar_default_guild_id.set(str(guild.id))
        await ctx.send(f"Default guild set to: {guild.name}")
        log.info(f"Default guild set to {guild.name} ({guild.id})")
    
    async def set_invite_channel(self, ctx, channel: discord.TextChannel):
        if not channel.permissions_for(ctx.guild.me).create_instant_invite:
            await ctx.send(f"I don't have permission to create invites in {channel.mention}")
            log.warning(f"Missing invite creation permission in {channel.name}")
            return
        await self.config.ar_invite_channel_id.set(str(channel.id))
        await ctx.send(f"Invite channel set to: {channel.mention}")
        log.info(f"Invite channel set to {channel.name} ({channel.id})")