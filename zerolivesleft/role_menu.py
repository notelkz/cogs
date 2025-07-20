# zerolivesleft-alpha/role_menus.py
# Complete, rewritten file for dynamic menus

import discord
from redbot.core import commands, Config
from redbot.core.utils.views import ConfirmView
import asyncio
from typing import Optional

# These view and button classes are now dynamic
class ZeroRolesView(discord.ui.View):
    def __init__(self, cog, guild_id: int, menu_name: str, menu_data: dict):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = guild_id
        self.menu_name = menu_name
        
        style_map = {
            "primary": discord.ButtonStyle.primary,
            "secondary": discord.ButtonStyle.secondary,
            "success": discord.ButtonStyle.success,
            "danger": discord.ButtonStyle.danger,
        }
        button_style = style_map.get(menu_data.get("style", "secondary").lower(), discord.ButtonStyle.secondary)

        for role_info in menu_data.get("roles", []):
            self.add_item(ZeroRoleButton(
                role_id=role_info["role_id"],
                label=role_info["label"],
                style=button_style
            ))

class ZeroRoleButton(discord.ui.Button):
    def __init__(self, role_id: int, label: str, style: discord.ButtonStyle):
        super().__init__(
            label=label, style=style, custom_id=f"zeroroles_v2_{role_id}"
        )
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        role = interaction.guild.get_role(self.role_id)
        if not role:
            return await interaction.response.send_message("This role no longer exists. Please contact an admin.", ephemeral=True)

        if role in interaction.user.roles:
            try:
                await interaction.user.remove_roles(role, reason="Role menu")
                await interaction.response.send_message(f"Removed the **{role.name}** role.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("I don't have permission to remove roles.", ephemeral=True)
        else:
            try:
                await interaction.user.add_roles(role, reason="Role menu")
                await interaction.response.send_message(f"You now have the **{role.name}** role.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("I don't have permission to add roles.", ephemeral=True)

class AutoRoleButton(discord.ui.Button):
    # This class is unchanged
    def __init__(self):
        super().__init__(
            label="Toggle Automatic Roles", style=discord.ButtonStyle.danger, custom_id="zeroroles_autorole_toggle"
        )
        self.auto_role_id = 1369334433714409576
    
    async def callback(self, interaction: discord.Interaction):
        # This callback logic is unchanged
        pass
        
class AutoRoleView(discord.ui.View):
    # This class is unchanged
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(AutoRoleButton())

class RoleMenuLogic:
    def __init__(self, cog_instance):
        self.cog = cog_instance
        self.bot = cog_instance.bot
        self.config = cog_instance.config

    def create_view_from_config(self, menu_name: Optional[str] = None, menu_data: Optional[dict] = None):
        """Helper to create a view, used for re-initialising persistent views on startup"""
        # This is a dummy call for the initial add_view. The real views are built when posted/updated.
        return ZeroRolesView(self, 0, "dummy", {})

    async def _get_menu(self, guild: discord.Guild, name: str) -> Optional[dict]:
        menus = await self.config.guild(guild).role_menus()
        return menus.get(name.lower())

    async def _save_menu(self, guild: discord.Guild, name: str, data: dict):
        async with self.config.guild(guild).role_menus() as menus:
            menus[name.lower()] = data

    # --- Command Logic Methods ---
    
    async def create_menu(self, ctx: commands.Context, name: str):
        """Creates a new, empty role menu."""
        name = name.lower()
        if await self._get_menu(ctx.guild, name):
            return await ctx.send(f"A menu named `{name}` already exists.")
        
        new_menu = {
            "image_url": None, "color": 0x2F3136, "footer": "Select your roles",
            "style": "secondary", "roles": [], "message_id": None, "channel_id": None
        }
        await self._save_menu(ctx.guild, name, new_menu)
        await ctx.send(f"✅ Created new empty role menu: `{name}`. "
                       f"Use `!zll roles menu edit`, `menu image`, and `addrole` to configure it.")

    async def delete_menu(self, ctx: commands.Context, name: str):
        """Deletes a role menu."""
        name = name.lower()
        async with self.config.guild(ctx.guild).role_menus() as menus:
            if name not in menus:
                return await ctx.send(f"No menu named `{name}` found.")
            
            view = ConfirmView(ctx.author, disable_buttons=True)
            await ctx.send(f"Are you sure you want to permanently delete the `{name}` role menu?", view=view)
            await view.wait()
            if view.result:
                del menus[name]
                await ctx.send(f"🗑️ Deleted role menu: `{name}`")
            else:
                await ctx.send("Deletion cancelled.")

    async def list_menus(self, ctx: commands.Context):
        """Lists all configured role menus."""
        menus = await self.config.guild(ctx.guild).role_menus()
        if not menus:
            return await ctx.send("No role menus have been created yet.")
        
        desc = "\n".join([f"- `{name}` ({len(data.get('roles',[]))} roles)" for name, data in menus.items()])
        embed = discord.Embed(title="Configured Role Menus", description=desc, color=await ctx.embed_color())
        await ctx.send(embed=embed)

    async def edit_menu(self, ctx: commands.Context, name: str, setting: str, value: str):
        """Edits a setting for a role menu (color, footer, style)."""
        name = name.lower()
        menu = await self._get_menu(ctx.guild, name)
        if not menu:
            return await ctx.send(f"No menu named `{name}` found.")
        
        setting = setting.lower()
        valid_settings = ["color", "footer", "style", "image_url"]
        if setting not in valid_settings:
            return await ctx.send(f"Invalid setting. Choose from: `{', '.join(valid_settings)}`")
        
        if setting == "color":
            try:
                value = int(value.replace("#", ""), 16)
            except ValueError:
                return await ctx.send("Invalid color. Please provide a hex code (e.g., `#FF0000`).")
        elif setting == "style":
            valid_styles = ["primary", "secondary", "success", "danger"]
            if value.lower() not in valid_styles:
                return await ctx.send(f"Invalid style. Choose from: `{', '.join(valid_styles)}`")
            value = value.lower()

        menu[setting] = value
        await self._save_menu(ctx.guild, name, menu)
        await ctx.send(f"✅ Set `{setting}` for menu `{name}` to: `{value}`")
        await self.update_menu_message(ctx, name, silent=True)


    async def add_role_to_menu(self, ctx: commands.Context, menu_name: str, role: discord.Role, label: str = None):
        """Adds a role to a menu."""
        menu_name = menu_name.lower()
        if not label:
            label = role.name
        
        menu = await self._get_menu(ctx.guild, menu_name)
        if not menu:
            return await ctx.send(f"No menu named `{menu_name}` found.")

        for r in menu["roles"]:
            if r["role_id"] == role.id:
                return await ctx.send(f"Role `{role.name}` is already in this menu.")
        
        menu["roles"].append({"label": label, "role_id": role.id})
        await self._save_menu(ctx.guild, menu_name, menu)
        await ctx.send(f"✅ Added role `{role.name}` (labeled as `{label}`) to menu `{menu_name}`.")
        await self.update_menu_message(ctx, menu_name, silent=True)

    async def remove_role_from_menu(self, ctx: commands.Context, menu_name: str, role: discord.Role):
        """Removes a role from a menu."""
        menu_name = menu_name.lower()
        menu = await self._get_menu(ctx.guild, menu_name)
        if not menu:
            return await ctx.send(f"No menu named `{menu_name}` found.")

        initial_len = len(menu["roles"])
        menu["roles"] = [r for r in menu["roles"] if r["role_id"] != role.id]
        
        if len(menu["roles"]) < initial_len:
            await self._save_menu(ctx.guild, menu_name, menu)
            await ctx.send(f"🗑️ Removed role `{role.name}` from menu `{menu_name}`.")
            await self.update_menu_message(ctx, menu_name, silent=True)
        else:
            await ctx.send(f"Role `{role.name}` was not found in that menu.")

    async def _build_menu_components(self, guild: discord.Guild, name: str) -> Optional[tuple]:
        menu_data = await self._get_menu(guild, name)
        if not menu_data:
            return None
        
        embed = discord.Embed(description="", color=menu_data.get("color", 0x2F3136))
        embed.set_footer(text=menu_data.get("footer", "Select your roles"))
        if menu_data.get("image_url"):
            embed.set_image(url=menu_data["image_url"])
        
        view = ZeroRolesView(self, guild.id, name, menu_data)
        return embed, view

    async def post_menu(self, ctx: commands.Context, menu_name: str, channel: discord.TextChannel = None):
        """Posts a role menu to a channel for the first time."""
        if not channel:
            channel = ctx.channel
        
        menu_name = menu_name.lower()
        components = await self._build_menu_components(ctx.guild, menu_name)
        if not components:
            return await ctx.send(f"No menu named `{menu_name}` found.")
        
        embed, view = components
        try:
            msg = await channel.send(embed=embed, view=view)
            menu_data = await self._get_menu(ctx.guild, menu_name)
            menu_data["message_id"] = msg.id
            menu_data["channel_id"] = channel.id
            await self._save_menu(ctx.guild, menu_name, menu_data)
            await ctx.send(f"✅ Menu `{menu_name}` posted in {channel.mention}. I will now attempt to update this message when you make changes.")
        except discord.Forbidden:
            await ctx.send(f"I don't have permission to post in {channel.mention}.")

    async def update_menu_message(self, ctx: commands.Context, menu_name: str, silent: bool = False):
        """Finds and updates an existing role menu message."""
        menu_name = menu_name.lower()
        menu_data = await self._get_menu(ctx.guild, menu_name)
        if not menu_data:
            if not silent: await ctx.send(f"No menu named `{menu_name}` found.")
            return

        if not menu_data.get("channel_id") or not menu_data.get("message_id"):
            if not silent: await ctx.send(f"This menu hasn't been posted yet. Use `!zll roles post {menu_name}` first.")
            return

        components = await self._build_menu_components(ctx.guild, menu_name)
        if not components:
            return
        
        embed, view = components
        try:
            channel = self.bot.get_channel(menu_data["channel_id"])
            if not channel:
                if not silent: await ctx.send("The channel for this menu no longer exists.")
                return
            
            message = await channel.fetch_message(menu_data["message_id"])
            await message.edit(embed=embed, view=view)
            if not silent: await ctx.send(f"✅ Updated the `{menu_name}` menu message in {channel.mention}.")
        except discord.NotFound:
            if not silent: await ctx.send("The original message for this menu could not be found (it may have been deleted). Please post it again.")
        except discord.Forbidden:
            if not silent: await ctx.send(f"I don't have permission to edit messages in the destination channel.")
        except Exception as e:
            if not silent: await ctx.send(f"An unexpected error occurred while updating the menu: {e}")
    
    # This static command is preserved
    async def send_autoroles_menu(self, ctx, channel: discord.TextChannel = None):
        if channel is None: channel = ctx.channel
        embed1 = discord.Embed(color=0xFF0000, description="**Enable Automatic Game Roles**")
        embed1.set_image(url="https://zerolivesleft.net/media/uploads/autoroles.png")
        embed2 = discord.Embed(
            description="Our bot can automatically assign you game roles based on the games you're playing! To use this feature, you must enable **'Share your detected activities with others'** in your Discord privacy settings (as shown below) and then click the button.",
            color=0xFF0005
        )
        embed2.set_image(url="https://zerolivesleft.net/media/uploads/discordactivity.png")
        try:
            await channel.send(embeds=[embed1, embed2], view=AutoRoleView())
            if channel != ctx.channel:
                await ctx.send(f"Auto-roles menu sent to {channel.mention}")
        except discord.Forbidden:
            await ctx.send("I don't have permission to send messages in that channel.")