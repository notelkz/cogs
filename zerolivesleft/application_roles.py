# zerolivesleft/application_roles.py
# Complete, corrected file with all message logic

import discord
import logging
import asyncio
from aiohttp import web
from redbot.core import commands

log = logging.getLogger("red.Elkz.zerolivesleft.application_roles")

class ApplicationRolesLogic:
    """
    Handles assigning roles and sending welcome messages for the application process.
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
            ar_pending_role_id=None,
            ar_member_role_id=None,
            ar_unverified_role_id=None,
            ar_welcome_channel_id=None,
            ar_send_private_welcome=True,
            ar_send_public_welcome=True,
            ar_unverified_message="Welcome to Zero Lives Left! To gain full access to the server, please submit an application at: https://zerolivesleft.net/apply/",
            ar_pending_message="Thank you for submitting your application! We have received it and will review it shortly. You now have access to our public channels.",
        )
        
        self.bot.add_listener(self.on_member_join, "on_member_join")
        log.info("ApplicationRolesLogic initialized")

    async def on_member_join(self, member: discord.Member):
        if member.bot: return
        guild = member.guild
        if not await self.config.ar_enabled() or guild.id != await self.config.ar_default_guild_id():
            return

        log.info(f"New member '{member.name}' joined. Checking application status via API.")
        
        role_to_assign = None
        should_send_unverified_message = False
        should_send_pending_message = False # <-- New flag

        api_url = await self.config.ar_api_url()
        api_key = await self.config.ar_api_key()

        if api_url and api_key:
            try:
                endpoint = f"{api_url.rstrip('/')}/check_application_status/{member.id}"
                headers = {"Authorization": f"Token {api_key}"}
                async with self.session.get(endpoint, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        status = data.get("status")
                        if status == "pending":
                            log.info(f"User {member.name} has a PENDING application. Assigning 'Pending' role.")
                            role_to_assign = guild.get_role(await self.config.ar_pending_role_id() or 0)
                            should_send_pending_message = True # ✅ SET FLAG TO SEND PENDING MESSAGE
                        else:
                            log.info(f"User {member.name} does not have a pending application. Assigning 'Unverified' role.")
                            role_to_assign = guild.get_role(await self.config.ar_unverified_role_id() or 0)
                            should_send_unverified_message = True
                    else:
                        log.error(f"API check for {member.name} failed with status {resp.status}. Defaulting to Unverified.")
                        role_to_assign = guild.get_role(await self.config.ar_unverified_role_id() or 0)
                        should_send_unverified_message = True
            except Exception as e:
                log.error(f"API check for {member.name} raised an exception: {e}. Defaulting to Unverified.")
                role_to_assign = guild.get_role(await self.config.ar_unverified_role_id() or 0)
                should_send_unverified_message = True
        else:
            log.warning("API URL or Key not set. Defaulting all new members to Unverified.")
            role_to_assign = guild.get_role(await self.config.ar_unverified_role_id() or 0)
            should_send_unverified_message = True

        if role_to_assign:
            try:
                await member.add_roles(role_to_assign, reason="New member join processing")
                log.info(f"Assigned '{role_to_assign.name}' to {member.name}.")
            except discord.Forbidden:
                log.error(f"Failed to assign role to {member.name}. Missing 'Manage Roles' permission.")
                return

        if should_send_unverified_message:
            await self._send_unverified_welcome_messages(member)
        
        # ✅ --- ADDED THIS BLOCK TO SEND THE PENDING MESSAGE ---
        if should_send_pending_message:
            if await self.config.ar_send_private_welcome():
                if pending_message := await self.config.ar_pending_message():
                    try:
                        await member.send(pending_message)
                        log.info(f"Sent 'Pending' welcome back DM to {member.name}.")
                    except discord.Forbidden:
                        log.warning(f"Could not send 'Pending' welcome back DM to {member.name}. DMs are closed.")
    
    async def _send_unverified_welcome_messages(self, member: discord.Member):
        guild = member.guild
        send_dm, send_public = await self.config.ar_send_private_welcome(), await self.config.ar_send_public_welcome()
        welcome_message = await self.config.ar_unverified_message()
        dm_sent = False

        if send_dm and welcome_message:
            try:
                await member.send(welcome_message)
                log.info(f"Sent 'Unverified' welcome DM to {member.name}.")
                dm_sent = True
            except discord.Forbidden:
                log.warning(f"Could not send 'Unverified' welcome DM to {member.name}. Their DMs are closed.")
        
        if not dm_sent and send_public:
            welcome_channel_id = await self.config.ar_welcome_channel_id()
            if not welcome_channel_id or not (channel := guild.get_channel(welcome_channel_id)):
                log.warning("Could not send public welcome message. Welcome channel is not set or not found.")
                return
            try:
                embed = discord.Embed(title="Welcome to Zero Lives Left!", description="To gain access to the rest of the server, you must submit an application on our website. You have been given the **Unverified** role for now.", color=discord.Color.blurple())
                if guild.icon: embed.set_thumbnail(url=guild.icon.url)
                embed.add_field(name="Application Link", value="[Click here to apply](https://zerolivesleft.net/apply/)", inline=False)
                embed.set_footer(text="Once your application is approved, your roles will be updated automatically.")
                await channel.send(content=member.mention, embed=embed)
                log.info(f"Sent public welcome embed to #{channel.name} for {member.name}.")
            except discord.Forbidden:
                log.error(f"Could not send public welcome embed to #{channel.name}. Missing 'Send Messages' or 'Embed Links' permissions.")

    async def handle_application_submitted(self, request):
        api_key = request.headers.get('Authorization', '').replace('Token ', '')
        if not api_key or api_key != await self.config.ar_api_key():
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            data = await request.json()
            discord_id = data.get("discord_id")
            guild = self.bot.get_guild(await self.config.ar_default_guild_id())
            if not discord_id or not guild or not (member := guild.get_member(int(discord_id))):
                return web.json_response({"error": "Member not found"}, status=404)
            unverified_role = guild.get_role(await self.config.ar_unverified_role_id() or 0)
            pending_role = guild.get_role(await self.config.ar_pending_role_id() or 0)
            if unverified_role and pending_role and unverified_role in member.roles:
                await member.remove_roles(unverified_role, reason="Application submitted.")
                await member.add_roles(pending_role, reason="Application submitted.")
                log.info(f"Upgraded {member.name} from Unverified to Pending.")
                if await self.config.ar_send_private_welcome():
                    if pending_message := await self.config.ar_pending_message():
                        try:
                            await member.send(pending_message)
                            log.info(f"Sent 'Pending' confirmation DM to {member.name}.")
                        except discord.Forbidden:
                            log.warning(f"Could not send 'Pending' DM to {member.name}.")
            return web.json_response({"success": True})
        except Exception as e:
            log.error(f"Error handling submitted app: {e}", exc_info=True)
            return web.json_response({"error": "Internal server error"}, status=500)

    async def handle_application_update(self, request):
        api_key = request.headers.get('Authorization', '').replace('Token ', '')
        if not api_key or api_key != await self.config.ar_api_key():
            return web.json_response({"error": "Unauthorized"}, status=401)
        try:
            data = await request.json()
            self.bot.loop.create_task(self.process_role_update(data))
            return web.json_response({"success": True, "message": "Request received."})
        except Exception as e:
            log.error(f"Error processing app update: {e}", exc_info=True)
            return web.json_response({"error": f"Error: {str(e)}"}, status=400)

    async def process_role_update(self, data: dict):
        discord_id, status = data.get("discord_id"), data.get("status")
        app_data = data.get("application_data", {})
        guild = self.bot.get_guild(await self.config.ar_default_guild_id() or 0)
        if not all([discord_id, status, guild]) or not (member := guild.get_member(int(discord_id))):
            return log.error(f"Missing data or member/guild not found for role update.")
        log.info(f"Processing role update for {member.name} with status {status}")
        pending_role = guild.get_role(await self.config.ar_pending_role_id() or 0)
        unverified_role = guild.get_role(await self.config.ar_unverified_role_id() or 0)
        roles_to_remove = [r for r in [pending_role, unverified_role] if r and r in member.roles]
        if status == "rejected":
            if roles_to_remove: await member.remove_roles(*roles_to_remove, reason="Application Rejected.")
            await member.kick(reason="Application Rejected.")
            log.info(f"Kicked {member.name}")
        elif status == "approved":
            roles_to_add = []
            if role_id := await self.config.ar_member_role_id():
                if role := guild.get_role(role_id): roles_to_add.append(role)
            if region_code := app_data.get("region"):
                region_roles = await self.config.ar_region_roles()
                if role_id := region_roles.get(region_code.upper()):
                    if role := guild.get_role(int(role_id)): roles_to_add.append(role)
            for r_type in ["platform_role_ids", "game_role_ids"]:
                for r_id in app_data.get(r_type, []):
                    if r_id and (role := guild.get_role(int(r_id))): roles_to_add.append(role)
            if roles_to_add: await member.add_roles(*roles_to_add, reason="Application Approved")
            if roles_to_remove: await member.remove_roles(*roles_to_remove, reason="Application Approved")
            log.info(f"Role update complete for {member.name}")

    def stop_tasks(self):
        log.info("Application Roles tasks stopped.")

    async def set_api_url(self, ctx, url):
        await self.config.ar_api_url.set(url)
        await ctx.send(f"API URL set to: `{url}`")

    async def set_api_key(self, ctx, key):
        await self.config.ar_api_key.set(key)
        await ctx.send("API key set successfully.")

    async def set_pending_role(self, ctx, role: discord.Role):
        await self.config.ar_pending_role_id.set(role.id)
        await ctx.send(f"Pending role set to: {role.mention}")

    async def set_member_role(self, ctx, role: discord.Role):
        await self.config.ar_member_role_id.set(role.id)
        await ctx.send(f"Member role set to: {role.mention}")
        
    async def set_unverified_role(self, ctx, role: discord.Role):
        await self.config.ar_unverified_role_id.set(role.id)
        await ctx.send(f"Unverified role set to: {role.mention}")

    async def set_welcome_channel(self, ctx, channel: discord.TextChannel):
        await self.config.ar_welcome_channel_id.set(channel.id)
        await ctx.send(f"Welcome channel set to: {channel.mention}")

    async def add_region_role(self, ctx, region: str, role: discord.Role):
        async with self.config.ar_region_roles() as region_roles:
            region_roles[region.upper()] = role.id
        await ctx.send(f"Region role `{region.upper()}` mapped to {role.mention}")
    
    async def remove_region_role(self, ctx, region: str):
        async with self.config.ar_region_roles() as region_roles:
            if region.upper() in region_roles:
                del region_roles[region.upper()]
                await ctx.send(f"Removed region mapping for `{region.upper()}`")
            else:
                await ctx.send(f"No mapping found for `{region.upper()}`.")
    
    async def list_region_roles(self, ctx):
        region_roles = await self.config.ar_region_roles()
        if not region_roles: return await ctx.send("No region roles configured.")
        embed = discord.Embed(title="Region Role Mappings", color=await ctx.embed_color())
        msg = ""
        for region, role_id in region_roles.items():
            role = ctx.guild.get_role(role_id)
            line = f"**{region}**: {role.mention if role else f'Unknown Role (`{role_id}`)'}"
            msg += line + "\n"
        embed.description = msg
        await ctx.send(embed=embed)
    
    async def show_config(self, ctx: commands.Context):
        ar_config = {k: v for k, v in (await self.config.all()).items() if k.startswith("ar_")}
        embed = discord.Embed(title="Application Roles Configuration", color=await ctx.embed_color())
        for key, value in sorted(ar_config.items()):
            name = key.replace("ar_", "").replace("_", " ").title()
            value_str = f"`{value}`" if value else "`Not Set`"
            if value:
                if "role_id" in key: value_str = f"<@&{value}> (`{value}`)"
                elif "channel_id" in key: value_str = f"<#{value}> (`{value}`)"
                elif key == "ar_region_roles": value_str = "\n".join([f"`{k}`: <@&{v}>" for k, v in value.items()]) or "None"
                elif "api_key" in key: value_str = "`Set`"
                elif "_message" in key: value_str = f"```{discord.utils.escape_markdown(str(value))}```"
            embed.add_field(name=name, value=value_str, inline=False)
        await ctx.send(embed=embed)

    async def set_default_guild(self, ctx, guild: discord.Guild):
        await self.config.ar_default_guild_id.set(guild.id)
        await ctx.send(f"Default guild set to: **{guild.name}**")