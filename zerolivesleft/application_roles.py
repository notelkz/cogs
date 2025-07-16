# zerolivesleft/application_roles.py

import discord
import logging
import asyncio
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

    async def on_member_join(self, member: discord.Member):
        if member.bot: return

        guild = member.guild
        log.info(f"New member joined: {member.name} ({member.id}). Checking application status.")
        
        guild_id = await self.config.ar_default_guild_id()
        if not guild_id or guild.id != int(guild_id):
            return

        api_key = await self.config.ar_api_key()
        api_url = await self.config.ar_api_url()
        
        if not api_url or not api_key:
            log.error("Cannot check application status: Application API URL or Key not set.")
            role_to_assign_id = await self.config.ar_unverified_role_id()
            is_unverified = True
        else:
            role_to_assign_id = None
            is_unverified = False
            try:
                endpoint = f"{api_url.rstrip('/')}/api/applications/check/{member.id}/"
                headers = {"Authorization": f"Token {api_key}"}
                
                async with self.session.get(endpoint, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
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
                    welcome_message = await self.config.ar_welcome_message()
                    if welcome_channel_id and welcome_message and (channel := guild.get_channel(int(welcome_channel_id))):
                        formatted_message = welcome_message.format(mention=member.mention)
                        await channel.send(formatted_message)
                        log.info(f"Sent welcome message to {channel.name} for {member.name}.")
            except Exception as e:
                log.error(f"An error occurred during post-join actions for {member.name}: {e}")

    async def handle_application_update(self, request):
        api_key = request.headers.get('Authorization', '').replace('Token ', '')
        expected_key = await self.config.ar_api_key() # Changed to use approles specific key
        if not api_key or api_key != expected_key:
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            data = await request.json()
            self.cog.bot.loop.create_task(self.process_role_update(data))
            return web.json_response({"success": True, "message": "Request received."})
        except Exception as e:
            log.error(f"Error processing initial request: {e}")
            return web.json_response({"error": "Invalid JSON"}, status=400)

    async def process_role_update(self, data: dict):
        discord_id = data.get("discord_id")
        status = data.get("status")
        app_data = data.get("application_data", {})
        if not all([discord_id, status]): return

        guild_id = await self.config.ar_default_guild_id()
        if not guild_id: return
        
        guild = self.bot.get_guild(int(guild_id))
        if not guild: return
        
        member = guild.get_member(int(discord_id))
        if not member: return

        log.info(f"Background processing for {member.display_name}, status: '{status}'.")
        pending_role = guild.get_role(await self.config.ar_pending_role_id() or 0)
        unverified_role = guild.get_role(await self.config.ar_unverified_role_id() or 0)

        if status == "rejected":
            roles_to_remove = [r for r in [pending_role, unverified_role] if r and r in member.roles]
            if roles_to_remove: await member.remove_roles(*roles_to_remove, reason="Application Rejected.")
            await member.kick(reason="Application Rejected.")
            log.info(f"Kicked {member.name} ({discord_id}) due to rejected application.")
            return

        if status == "approved":
            roles_to_add = []
            roles_to_remove = [r for r in [pending_role, unverified_role] if r and r in member.roles]

            if member_role_id := await self.config.ar_member_role_id():
                if role := guild.get_role(int(member_role_id)): roles_to_add.append(role)
            
            if region_code := app_data.get("region"):
                if region_role_id := (await self.config.ar_region_roles()).get(region_code):
                    if role := guild.get_role(int(region_role_id)): roles_to_add.append(role)
            
            for role_type in ["platform_role_ids", "game_role_ids"]:
                for role_id in app_data.get(role_type, []):
                    if role_id and (role := guild.get_role(int(role_id))): roles_to_add.append(role)
            
            log.info(f"Final roles to add for {member.display_name}: {[r.name for r in roles_to_add]}")
            
            if roles_to_add: await member.add_roles(*roles_to_add, reason="Application Approved")
            if roles_to_remove: await member.remove_roles(*roles_to_remove, reason="Application Approved")

            log.info(f"Background role update complete for {member.name}.")

    async def handle_application_approved(self, request):
        return web.json_response({"error": "This endpoint is deprecated."}, status=410)
    
    async def cache_all_invites(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            try: self.guild_invites[guild.id] = await guild.invites()
            except (discord.Forbidden, discord.HTTPException): pass
    
    async def on_invite_create(self, invite):
        if invite.guild.id not in self.guild_invites: self.guild_invites[invite.guild.id] = []
        self.guild_invites[invite.guild.id].append(invite)

    def stop_tasks(self):
        if hasattr(self, 'cache_invites_task') and self.cache_invites_task and not self.cache_invites_task.done():
            self.cache_invites_task.cancel()

    async def set_api_url(self, ctx, url):
        await self.config.ar_api_url.set(url)
        await ctx.send(f"API URL set to: {url}")
    
    async def set_api_key(self, ctx, key):
        await self.config.ar_api_key.set(key)
        await ctx.send("API key set successfully.")

    async def toggle_enabled(self, ctx, enabled: bool):
        await self.config.ar_enabled.set(enabled)
        await ctx.send(f"Application role assignment is now {'enabled' if enabled else 'disabled'}.")

    async def set_pending_role(self, ctx, role: discord.Role):
        await self.config.ar_pending_role_id.set(role.id)
        await ctx.send(f"Pending role has been set to **{role.name}**.")

    async def set_member_role(self, ctx, role: discord.Role):
        await self.config.ar_member_role_id.set(role.id)
        await ctx.send(f"Main member role has been set to **{role.name}**.")
        
    async def set_unverified_role(self, ctx, role: discord.Role):
        await self.config.ar_unverified_role_id.set(role.id)
        await ctx.send(f"Unverified role has been set to **{role.name}**.")

    async def set_welcome_channel(self, ctx, channel: discord.TextChannel):
        await self.config.ar_welcome_channel_id.set(channel.id)
        await ctx.send(f"Welcome message channel set to {channel.mention}")

    async def set_welcome_message(self, ctx, *, message: str):
        await self.config.ar_welcome_message.set(message)
        await ctx.send(f"Welcome message has been set to:\n\n{message}")
    
    async def add_region_role(self, ctx, region: str, role: discord.Role):
        async with self.config.ar_region_roles() as region_roles:
            region_roles[region.upper()] = str(role.id)
        await ctx.send(f"Added region role mapping: `{region.upper()}` -> {role.name}")
    
    async def remove_region_role(self, ctx, region: str):
        async with self.config.ar_region_roles() as region_roles:
            if region.upper() in region_roles:
                del region_roles[region.upper()]
                await ctx.send(f"Removed region role mapping for `{region.upper()}`")
            else:
                await ctx.send(f"No region role mapping found for `{region.upper()}`.")
    
    async def list_region_roles(self, ctx):
        region_roles = await self.config.ar_region_roles()
        if not region_roles: return await ctx.send("No region role mappings configured.")
        embed = discord.Embed(title="Region Role Mappings", color=discord.Color.blue())
        for region, role_id in region_roles.items():
            role = ctx.guild.get_role(int(role_id))
            embed.add_field(name=region, value=role.mention if role else f"Unknown Role ({role_id})")
        await ctx.send(embed=embed)
    
    async def show_config(self, ctx: commands.Context):
        all_config = await self.config.get_raw("ar", default={})
        embed = discord.Embed(title="Application Roles Configuration", color=await ctx.embed_color())
        for key, value in all_config.items():
            name = key.replace("ar_", "").replace("_", " ").title()
            if value:
                if "role_id" in key: value_str = f"<@&{value}> (`{value}`)"
                elif key == "region_roles":
                    value_str = "\n".join([f"`{k}`: <@&{v}>" for k, v in value.items()]) or "None"
                elif "api_key" in key:
                    value_str = "`Set`" if value else "`Not Set`"
                else:
                    value_str = f"`{value}`" if value is not None else "`Not Set`"
            else:
                value_str = "`Not Set`"
            embed.add_field(name=name, value=value_str, inline=False)
        await ctx.send(embed=embed)

    async def force_cache_invites(self, ctx):
        await ctx.send("Refreshing invite cache...")
        await self.cache_all_invites()
        await ctx.send("Invite cache refreshed.")
    
    async def set_default_guild(self, ctx, guild: discord.Guild):
        await self.config.ar_default_guild_id.set(str(guild.id))
        await ctx.send(f"Default guild set to: {guild.name}")
    
    async def set_invite_channel(self, ctx, channel: discord.TextChannel):
        if not channel.permissions_for(ctx.guild.me).create_instant_invite:
            return await ctx.send(f"I don't have permission to create invites in {channel.mention}")
        await self.config.ar_invite_channel_id.set(str(channel.id))
        await ctx.send(f"Invite channel set to: {channel.mention}")