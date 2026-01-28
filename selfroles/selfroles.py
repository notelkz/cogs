import discord
from redbot.core import commands, Config
from discord.ui import Button, View
from typing import Optional, Dict, Any
import asyncio
import re

class SelfRoleButton(Button):
    def __init__(self, role_id: str, label: str, style: discord.ButtonStyle, emoji: Optional[str] = None):
        super().__init__(label=label, style=style, emoji=emoji)
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        try:
            member = interaction.user
            role = interaction.guild.get_role(int(self.role_id))
            
            if not role:
                await interaction.response.send_message("Role not found!", ephemeral=True)
                return
                
            if role in member.roles:
                await member.remove_roles(role, reason="Self-role removed")
                await interaction.response.send_message(f"Removed role: {role.name}", ephemeral=True)
            else:
                await member.add_roles(role, reason="Self-role added")
                await interaction.response.send_message(f"Added role: {role.name}", ephemeral=True)
                
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)

class SelfRoleView(View):
    def __init__(self, roles_config: dict, custom_emojis: dict = None):
        super().__init__(timeout=None)
        custom_emojis = custom_emojis or {}
        
        # Define default emojis for each category
        default_emojis = {
            "pc": "💻",
            "nintendo": "🎮", 
            "playstation": "🟦",
            "xbox": "🟩",
            "europe": "🇪🇺",
            "na": "🇺🇸",
            "sa": "🇧🇷",
            "asia": "🏮",
            "oceania": "🌊",
            "africa": "🐘",
            "he": "🔹",
            "she": "🔸",
            "they": "▫️",
            "ask": "💬"
        }

        # Layouts for different categories
        layouts = {
            "platform": [
                ("pc", "PC", discord.ButtonStyle.secondary, "💻"),
                ("nintendo", "Nintendo", discord.ButtonStyle.danger, "🎮"),
                ("playstation", "PlayStation", discord.ButtonStyle.primary, "🟦"),
                ("xbox", "Xbox", discord.ButtonStyle.secondary, "🟩")
            ],
            "region": [
                ("europe", "Europe", discord.ButtonStyle.primary, "🇪🇺"),
                ("na", "North America", discord.ButtonStyle.primary, "🇺🇸"),
                ("sa", "South America", discord.ButtonStyle.primary, "🇧🇷"),
                ("asia", "Asia", discord.ButtonStyle.primary, "🏮"),
                ("oceania", "Oceania", discord.ButtonStyle.primary, "🌊")
            ],
            "pronouns": [
                ("he", "He/Him", discord.ButtonStyle.secondary, "🔹"),
                ("she", "She/Her", discord.ButtonStyle.secondary, "🔸"),
                ("they", "They/Them", discord.ButtonStyle.secondary, "▫️"),
                ("ask", "Ask Me", discord.ButtonStyle.secondary, "💬")
            ]
        }

        # Determine which layout to use based on the roles configuration
        layout_type = self._determine_layout_type(roles_config)
        buttons_config = layouts.get(layout_type, layouts["platform"])
        
        for role_key, label, style, default_emoji in buttons_config:
            if role_key in roles_config:
                role_id = roles_config[role_key]
                # Get custom emoji or use default
                custom_emoji = custom_emojis.get(role_id, default_emoji)
                
                # Validate emoji format
                if custom_emoji and not self._is_valid_emoji(custom_emoji):
                    custom_emoji = default_emoji
                    
                button = SelfRoleButton(
                    role_id=role_id,
                    label=label,
                    style=style,
                    emoji=custom_emoji
                )
                self.add_item(button)

    def _determine_layout_type(self, roles_config: dict) -> str:
        """Determine layout type based on role keys"""
        if any(key in roles_config for key in ["pc", "nintendo", "playstation", "xbox"]):
            return "platform"
        elif any(key in roles_config for key in ["europe", "na", "sa", "asia", "oceania"]):
            return "region"
        elif any(key in roles_config for key in ["he", "she", "they", "ask"]):
            return "pronouns"
        return "platform"

    def _is_valid_emoji(self, emoji: str) -> bool:
        """Check if emoji is valid (either unicode or Discord format)"""
        if not emoji:
            return True
            
        # Check if it's a Discord custom emoji format <:name:id> or <a:name:id>
        if re.match(r'<a?:[a-zA-Z0-9_]+:\d+>', emoji):
            return True
            
        # Check if it's a unicode emoji
        try:
            # This will raise an error for invalid emoji strings
            emoji.encode('utf-8')
            return True
        except (UnicodeEncodeError, AttributeError):
            return False

class SelfRoles(commands.Cog):
    """Self-role management system"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        
        default_guild = {
            "roles": {},
            "custom_emojis": {},
            "category": "platform"  # platform, region, pronouns
        }
        
        self.config.register_guild(**default_guild)

    @commands.group(name="selfrole", aliases=["srole", "roles"])
    @commands.guild_only()
    async def selfrole(self, ctx):
        """Self-role management commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @selfrole.command(name="add")
    @commands.has_permissions(manage_roles=True)
    async def selfrole_add(self, ctx, role: discord.Role, emoji: str = None):
        """Add a role to the self-role system"""
        async with self.config.guild(ctx.guild).roles() as roles:
            roles[role.name.lower()] = str(role.id)
            
        if emoji:
            async with self.config.guild(ctx.guild).custom_emojis() as emojis:
                emojis[str(role.id)] = emoji
                
        await ctx.send(f"Added role **{role.name}** to self-role system")

    @selfrole.command(name="remove")
    @commands.has_permissions(manage_roles=True)
    async def selfrole_remove(self, ctx, role: discord.Role):
        """Remove a role from the self-role system"""
        async with self.config.guild(ctx.guild).roles() as roles:
            if role.name.lower() in roles:
                del roles[role.name.lower()]
                
        async with self.config.guild(ctx.guild).custom_emojis() as emojis:
            if str(role.id) in emojis:
                del emojis[str(role.id)]
                
        await ctx.send(f"Removed role **{role.name}** from self-role system")

    @selfrole.command(name="post")
    @commands.has_permissions(manage_roles=True)
    async def selfrole_post(self, ctx, channel: discord.TextChannel = None):
        """Post the self-role buttons in a channel"""
        roles = await self.config.guild(ctx.guild).roles()
        custom_emojis = await self.config.guild(ctx.guild).custom_emojis()
        
        if not roles:
            await ctx.send("No self-roles configured. Use `selfrole add` to add some.")
            return

        try:
            view = SelfRoleView(roles, custom_emojis)
            message = await (channel or ctx.channel).send("Select your roles:", view=view)
            await ctx.send("Self-role buttons posted successfully!")
        except Exception as e:
            await ctx.send(f"Error posting buttons: {e}")

    @selfrole.command(name="emoji")
    @commands.has_permissions(manage_roles=True)
    async def selfrole_emoji(self, ctx, role: discord.Role, emoji: str):
        """Set a custom emoji for a role"""
        async with self.config.guild(ctx.guild).custom_emojis() as emojis:
            emojis[str(role.id)] = emoji
        await ctx.send(f"Set emoji {emoji} for role {role.name}")

    @selfrole.command(name="list")
    @commands.has_permissions(manage_roles=True)
    async def selfrole_list(self, ctx):
        """List all configured self-roles"""
        roles = await self.config.guild(ctx.guild).roles()
        custom_emojis = await self.config.guild(ctx.guild).custom_emojis()
        
        if not roles:
            await ctx.send("No self-roles configured.")
            return
            
        embed = discord.Embed(title="Self-Roles", description="Configured self-roles:")
        for role_name, role_id in roles.items():
            role = ctx.guild.get_role(int(role_id))
            if role:
                emoji_str = custom_emojis.get(role_id, "default")
                embed.add_field(
                    name=role.name,
                    value=f"Emoji: {emoji_str}",
                    inline=False
                )
        await ctx.send(embed=embed)

    @selfrole.command(name="clear")
    @commands.has_permissions(manage_roles=True)
    async def selfrole_clear(self, ctx):
        """Clear all self-roles"""
        async with self.config.guild(ctx.guild).roles() as roles:
            roles.clear()
        async with self.config.guild(ctx.guild).custom_emojis() as emojis:
            emojis.clear()
        await ctx.send("All self-roles cleared")

    @selfrole.command(name="category")
    @commands.has_permissions(manage_roles=True)
    async def selfrole_category(self, ctx, category: str):
        """Set the category for self-roles (platform, region, pronouns)"""
        if category.lower() not in ["platform", "region", "pronouns"]:
            await ctx.send("Category must be one of: platform, region, pronouns")
            return
            
        await self.config.guild(ctx.guild).category.set(category.lower())
        await ctx.send(f"Set category to: {category.lower()}")

def setup(bot):
    bot.add_cog(SelfRoles(bot))
