import discord
from redbot.core import commands, Config
from discord.ui import Button, View
from typing import Optional, Union
import asyncio
import re

# --- VIEWS ---

class MultiRoleView(View):
    def __init__(self, roles: dict, category: str, custom_emojis: dict = None):
        super().__init__(timeout=None)
        custom_emojis = custom_emojis or {}
        
        layouts = {
            "platform": [
                ("pc", "PC", discord.ButtonStyle.primary, "<:steam:1325786409084260434>"),
                ("nintendo", "Nintendo", discord.ButtonStyle.primary, "<:switch:1325786378130296923>"),
                ("xbox", "Xbox", discord.ButtonStyle.primary, "<:xbox:1325786407889014795>"),
                ("playstation", "PlayStation", discord.ButtonStyle.primary, "<:ps:1325786400309641236>")
            ],
            "location": [
                ("europe", "Europe", discord.ButtonStyle.secondary, "🇪🇺"),
                ("north_america", "North America", discord.ButtonStyle.secondary, "🇺🇸"),
                ("south_america", "South America", discord.ButtonStyle.secondary, "🇧🇷"),
                ("asia", "Asia", discord.ButtonStyle.secondary, "🌏"),
                ("africa", "Africa", discord.ButtonStyle.secondary, "🌍"),
                ("oceania", "Oceania", discord.ButtonStyle.secondary, "🇦🇺")
            ],
            "pronoun": [
                ("he", "He/Him", discord.ButtonStyle.success, "🔹"),
                ("she", "She/Her", discord.ButtonStyle.success, "🔸"),
                ("they", "They/Them", discord.ButtonStyle.success, "▫️"),
                ("ask", "Other/Ask", discord.ButtonStyle.success, "💬")
            ]
        }

        for key, label, style, default_emoji in layouts.get(category, []):
            if roles.get(key):
                # Priority: Saved custom emoji -> Default emoji
                raw_emoji = custom_emojis.get(key) or default_emoji
                
                # Create button
                btn = Button(
                    style=style,
                    label=label,
                    custom_id=f"selfrole_{category}_{key}"
                )
                
                # Attempt to apply emoji safely
                if raw_emoji:
                    try:
                        # Handle both custom emoji formats: <:name:id> and <a:name:id>
                        if isinstance(raw_emoji, str) and raw_emoji.startswith('<'):
                            # This is already a custom emoji string, we can use it directly
                            btn.emoji = raw_emoji
                        else:
                            # This is a unicode emoji or other format
                            btn.emoji = raw_emoji
                    except Exception as e:
                        print(f"Failed to set emoji for {key}: {e}")
                        btn.emoji = default_emoji # Fallback to safety
                
                self.add_item(btn)

# --- COG ---

class SelfRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=5566778899)
        self.config.register_guild(
            platforms={}, platform_emojis={}, locations={}, pronouns={}
        )
        bot.add_listener(self.button_listener, "on_interaction")

    async def button_listener(self, interaction: discord.Interaction):
        if not interaction.data or "custom_id" not in interaction.data:
            return
        custom_id = interaction.data["custom_id"]
        if not custom_id.startswith("selfrole_"):
            return
            
        parts = custom_id.split("_")
        category_map = {"platform": "platforms", "location": "locations", "pronoun": "pronouns"}
        target_config = category_map.get(parts[1])
        
        if not target_config:
            return
            
        data = await self.config.guild(interaction.guild).get_attr(target_config)()
        role_id = data.get(parts[2])
        
        if not role_id:
            return
            
        role = interaction.guild.get_role(role_id)
        if not role:
            return
            
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role)
            await interaction.response.send_message(f"Removed **{role.name}**.", ephemeral=True)
        else:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"Added **{role.name}**.", ephemeral=True)

    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def selfroles(self, ctx):
        """Self-assignable roles management"""
        pass

    @selfroles.command()
    async def setup(self, ctx):
        """Setup: Type '@Role Emoji'. Example: @PC <:steam:1325786409084260434>"""
        setup_structure = {
            "platforms": ["PC", "Nintendo", "Xbox", "PlayStation"],
            "locations": ["Europe", "North America", "South America", "Asia", "Africa", "Oceania"],
            "pronouns": ["He/Him", "She/Her", "They/Them", "Other/Ask"]
        }

        await ctx.send("Starting setup. Type **skip** to skip, or **quit** to exit.")

        for cat_key, labels in setup_structure.items():
            await ctx.send(f"--- **{cat_key.upper()}** ---")
            roles_to_save = {}
            emojis_to_save = {}
            
            for label in labels:
                await ctx.send(f"Role/Emoji for **{label}**:")
                try:
                    msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=60.0)
                    content = msg.content.strip()
                    
                    if content.lower() == "quit":
                        return await ctx.send("Setup cancelled.")
                    if content.lower() == "skip":
                        continue

                    # Find Role ID
                    role_id = None
                    if msg.role_mentions:
                        role_id = msg.role_mentions[0].id
                    else:
                        id_match = re.search(r'\d{17,20}', content)
                        if id_match:
                            role_id = int(id_match.group())

                    if not role_id:
                        await ctx.send("No role found. Skipping.")
                        continue

                    # Key mapping
                    key = label.lower().split("/")[0].split(" ")[0]
                    if "north" in label.lower(): key = "north_america"
                    if "south" in label.lower(): key = "south_america"
                    if "other" in label.lower(): key = "ask"

                    roles_to_save[key] = role_id

                    # Emoji extraction
                    if cat_key == "platforms":
                        # Look for custom emoji format <:name:id> or <a:name:id>
                        custom_match = re.search(r'<a?:\w+:\d+>', content)
                        if custom_match:
                            emojis_to_save[key] = custom_match.group(0)
                        else:
                            # Try to extract unicode emoji or fallback to default
                            emoji_match = re.search(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF]', content)
                            if emoji_match:
                                emojis_to_save[key] = emoji_match.group()
                            else:
                                emojis_to_save[key] = None  # Will use default in view

                except asyncio.TimeoutError:
                    return await ctx.send("Setup timed out.")

            await self.config.guild(ctx.guild).get_attr(cat_key).set(roles_to_save)
            if cat_key == "platforms":
                await self.config.guild(ctx.guild).platform_emojis.set(emojis_to_save)

        await ctx.send("Setup complete. Run `!selfroles post`.")

    @selfroles.command()
    async def post(self, ctx, channel: Optional[discord.TextChannel] = None):
        channel = channel or ctx.channel
        guild_data = await self.config.guild(ctx.guild).all()
        
        try:
            # Create platform buttons with custom emojis
            platform_view = MultiRoleView(guild_data["platforms"], "platform", guild_data.get("platform_emojis"))
            await channel.send(embed=discord.Embed(title="🎮 Gaming Platforms", color=discord.Color.blurple()), view=platform_view)
            
            # Create location buttons
            location_view = MultiRoleView(guild_data["locations"], "location")
            await channel.send(embed=discord.Embed(title="🌍 Regional Roles", color=discord.Color.green()), view=location_view)
            
            # Create pronoun buttons
            pronoun_view = MultiRoleView(guild_data["pronouns"], "pronoun")
            await channel.send(embed=discord.Embed(title="✨ Pronouns", color=discord.Color.teal()), view=pronoun_view)
            
        except Exception as e:
            await ctx.send(f"Error posting: {e}. Check logs for details.")

    @selfroles.command()
    async def reset(self, ctx):
        """Reset all self-roles configuration"""
        await self.config.guild(ctx.guild).clear()
        await ctx.send("Configuration reset.")

    @selfroles.command()
    async def list(self, ctx):
        """List current configuration"""
        data = await self.config.guild(ctx.guild).all()
        if not any(data.values()):
            return await ctx.send("No configuration found.")
        
        embed = discord.Embed(title="Current Self-Roles Configuration", color=discord.Color.blue())
        
        if data["platforms"]:
            platforms = "\n".join([f"{key}: {role}" for key, role in data["platforms"].items()])
            embed.add_field(name="Platforms", value=platforms, inline=False)
            
        if data["platform_emojis"]:
            emojis = "\n".join([f"{key}: {emoji}" for key, emoji in data["platform_emojis"].items()])
            embed.add_field(name="Platform Emojis", value=emojis, inline=False)
            
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(SelfRoles(bot))
