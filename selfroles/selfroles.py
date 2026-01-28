import discord
from redbot.core import commands, Config
from discord.ui import Button, View
from typing import Optional, Dict, Any
import asyncio
import re

class MultiRoleView(View):
    def __init__(self, roles: dict, category: str, custom_emojis: dict = None):
        super().__init__(timeout=None)
        custom_emojis = custom_emojis or {}
        
        # Define default emojis for each category
        default_emojis = {
            "platform": {
                "pc": "💻",
                "nintendo": "🎮", 
                "playstation": "🟦",
                "xbox": "🟩"
            },
            "location": {
                "europe": "🇪🇺",
                "na": "🇺🇸",
                "sa": "🇧🇷",
                "asia": "🏮",
                "oceania": "🌊",
                "africa": "🐘"
            },
            "pronoun": {
                "he": "🔹",
                "she": "🔸",
                "they": "▫️",
                "ask": "💬"
            }
        }

        layouts = {
            "platform": [
                ("pc", "PC", discord.ButtonStyle.secondary, "💻"),
                ("nintendo", "Nintendo", discord.ButtonStyle.danger, "🎮"),
                ("playstation", "PlayStation", discord.ButtonStyle.primary, "🟦"),
                ("xbox", "Xbox", discord.ButtonStyle.success, "🟩")
            ],
            "location": [
                ("europe", "Europe", discord.ButtonStyle.primary, "🇪🇺"),
                ("na", "North America", discord.ButtonStyle.success, "🇺🇸"),
                ("sa", "South America", discord.ButtonStyle.success, "🇧🇷"),
                ("asia", "Asia", discord.ButtonStyle.danger, "🏮"),
                ("oceania", "Oceania", discord.ButtonStyle.primary, "🌊"),
                ("africa", "Africa", discord.ButtonStyle.secondary, "🐘")
            ],
            "pronoun": [
                ("he", "He/Him", discord.ButtonStyle.secondary, "🔹"),
                ("she", "She/Her", discord.ButtonStyle.secondary, "🔸"),
                ("they", "They/Them", discord.ButtonStyle.secondary, "▫️"),
                ("ask", "Other/Ask", discord.ButtonStyle.secondary, "💬")
            ]
        }

        for key, label, style, default_emoji in layouts.get(category, []):
            role_id = roles.get(key)
            if not role_id:
                continue

            # Get custom emoji or fallback to default
            emoji_to_use = None
            
            # Try to get custom emoji from config
            custom_emoji = custom_emojis.get(key)
            if custom_emoji:
                # Validate if it's a valid emoji format
                if isinstance(custom_emoji, str) and custom_emoji.strip():
                    # Check if it's a custom emoji (format: <a?:name:id> or <a?:name:id>)
                    if custom_emoji.startswith('<') and custom_emoji.endswith('>'):
                        # This is a custom emoji, validate it
                        emoji_match = re.match(r'<a?:\w+:(\d+)>', custom_emoji)
                        if emoji_match:
                            emoji_to_use = custom_emoji
                        else:
                            # Invalid format, use default
                            emoji_to_use = default_emoji
                    else:
                        # Regular unicode emoji or simple name
                        emoji_to_use = custom_emoji
                else:
                    # Invalid custom emoji, use default
                    emoji_to_use = default_emoji
            else:
                # No custom emoji configured, use default
                emoji_to_use = default_emoji
            
            # Ensure emoji is valid before creating button
            if emoji_to_use:
                try:
                    button = Button(
                        label=label,
                        style=style,
                        emoji=emoji_to_use if emoji_to_use else default_emoji
                    )
                    button.callback = self.button_callback
                    self.add_item(button)
                except Exception:
                    # Fallback to default emoji if custom emoji fails
                    button = Button(
                        label=label,
                        style=style,
                        emoji=default_emoji
                    )
                    button.callback = self.button_callback
                    self.add_item(button)
            else:
                # Fallback to default emoji
                button = Button(
                    label=label,
                    style=style,
                    emoji=default_emoji
                )
                button.callback = self.button_callback
                self.add_item(button)

    async def button_callback(self, interaction: discord.Interaction):
        # This will be overridden by individual buttons
        pass

class SelfRoleCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.config.register_guild(
            roles={},
            custom_emojis={}
        )

    @commands.group()
    @commands.has_permissions(manage_roles=True)
    async def selfrole(self, ctx):
        """Self-role management commands"""
        pass

    @selfrole.command()
    async def setup(self, ctx):
        """Interactive setup for self-roles"""
        await ctx.send("Starting self-role setup. Please respond with the role name and emoji (e.g. 'member 🎯') or 'done' to finish.")

    @selfrole.command()
    async def add(self, ctx, role: discord.Role, emoji: str = None):
        """Add a self-role"""
        async with self.config.guild(ctx.guild).roles() as roles:
            roles[str(role.id)] = {
                "name": role.name,
                "emoji": emoji or ""
            }
        await ctx.send(f"Added role {role.name} with emoji {emoji or 'default'}")

    @selfrole.command()
    async def remove(self, ctx, role: discord.Role):
        """Remove a self-role"""
        async with self.config.guild(ctx.guild).roles() as roles:
            if str(role.id) in roles:
                del roles[str(role.id)]
                await ctx.send(f"Removed role {role.name}")
            else:
                await ctx.send(f"Role {role.name} not found")

    @selfrole.command()
    async def list(self, ctx):
        """List all self-roles"""
        roles = await self.config.guild(ctx.guild).roles()
        if not roles:
            await ctx.send("No self-roles configured")
            return
            
        embed = discord.Embed(title="Self-Roles", description="Configured self-roles:")
        for role_id, role_info in roles.items():
            role = ctx.guild.get_role(int(role_id))
            if role:
                emoji_str = role_info.get("emoji", "")
                embed.add_field(
                    name=role.name,
                    value=f"Emoji: {emoji_str or 'default'}",
                    inline=False
                )
        await ctx.send(embed=embed)

    @selfrole.command()
    async def post(self, ctx, channel: discord.TextChannel = None):
        """Post the self-role buttons in a channel"""
        roles = await self.config.guild(ctx.guild).roles()
        if not roles:
            await ctx.send("No self-roles configured. Use `selfrole add` to add some.")
            return

        custom_emojis = await self.config.guild(ctx.guild).custom_emojis()
        
        # Create the view with proper emoji handling
        view = MultiRoleView(roles, "platform", custom_emojis)
        
        try:
            await (channel or ctx.channel).send("Select your roles:", view=view)
            await ctx.send("Self-role buttons posted successfully!")
        except discord.HTTPException as e:
            if "Invalid emoji" in str(e):
                await ctx.send("Error: Invalid emoji detected. Please check your emoji configuration.")
            else:
                await ctx.send(f"Error posting buttons: {e}")
        except Exception as e:
            await ctx.send(f"Unexpected error: {e}")

    @selfrole.command()
    async def emoji(self, ctx, role: discord.Role, emoji: str):
        """Set a custom emoji for a role"""
        async with self.config.guild(ctx.guild).custom_emojis() as emojis:
            emojis[str(role.id)] = emoji
        await ctx.send(f"Set emoji {emoji} for role {role.name}")

    @selfrole.command()
    async def clear(self, ctx):
        """Clear all self-roles"""
        async with self.config.guild(ctx.guild).roles() as roles:
            roles.clear()
        async with self.config.guild(ctx.guild).custom_emojis() as emojis:
            emojis.clear()
        await ctx.send("All self-roles cleared")

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Handle button interactions"""
        if not interaction.data or not interaction.data.get("custom_id"):
            return
            
        # This is a simple handler - you would implement proper role assignment logic here
        if interaction.type == discord.InteractionType.component:
            await interaction.response.send_message("Role assigned!", ephemeral=True)

def setup(bot):
    bot.add_cog(SelfRoleCog(bot))
