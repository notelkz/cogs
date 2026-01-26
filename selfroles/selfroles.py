import discord
from redbot.core import commands, Config
from discord.ui import Button, View
from typing import Optional
import asyncio
import re

# --- VIEWS ---

class MultiRoleView(View):
    def __init__(self, roles: dict, category: str, custom_emojis: dict = None):
        super().__init__(timeout=None)
        custom_emojis = custom_emojis or {}
        
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
            if roles.get(key):
                emoji = custom_emojis.get(key) or default_emoji
                self.add_item(Button(
                    style=style,
                    label=label,
                    emoji=emoji,
                    custom_id=f"selfrole_{category}_{key}"
                ))

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
        
        data = await self.config.guild(interaction.guild).get_attr(target_config)()
        role_id = data.get(parts[2])
        
        role = interaction.guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message("Role missing.", ephemeral=True)
            
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
        """Unified Setup: Type 'Role Emoji' for Platforms, or just 'Role' for others."""
        setup_structure = {
            "platforms": ["PC", "Nintendo", "PlayStation", "Xbox"],
            "locations": ["Europe", "North America", "South America", "Asia", "Oceania", "Africa"],
            "pronouns": ["He/Him", "She/Her", "They/Them", "Other/Ask"]
        }

        await ctx.send("Starting setup. Type **skip** to skip, or **quit** to exit.")

        for cat_key, labels in setup_structure.items():
            await ctx.send(f"--- **{cat_key.upper()}** ---")
            roles_to_save = {}
            emojis_to_save = {}
            
            for label in labels:
                prompt = f"Send Role for **{label}**"
                if cat_key == "platforms":
                    prompt += " (Optional: Add emoji after it, e.g. `@Role 🎮`)"
                
                await ctx.send(f"{prompt}:")
                try:
                    msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=60.0)
                    content = msg.content.strip()
                    
                    if content.lower() == "quit": return await ctx.send("Quit.")
                    
                    key = label.lower().split("/")[0].split(" ")[0]
                    if "north" in label.lower(): key = "na"
                    if "south" in label.lower(): key = "sa"
                    if "other" in label.lower(): key = "ask"

                    if content.lower() == "skip":
                        roles_to_save[key] = None
                        continue

                    # Extract Role ID and Emoji
                    role_id = None
                    if msg.role_mentions:
                        role_id = msg.role_mentions[0].id
                    else:
                        # Try to find a number in the string for ID
                        match = re.search(r'\d{17,20}', content)
                        if match: role_id = int(match.group())

                    if not role_id:
                        await ctx.send("Couldn't find a role. Skipping.")
                        continue

                    roles_to_save[key] = role_id

                    # Extract Emoji (anything that isn't the role mention/ID)
                    if cat_key == "platforms":
                        clean_content = re.sub(r'<@&\d+>|\d{17,20}', '', content).strip()
                        if clean_content:
                            emojis_to_save[key] = clean_content
                        else:
                            emojis_to_save[key] = None

                except asyncio.TimeoutError:
                    return await ctx.send("Timed out.")

            await self.config.guild(ctx.guild).get_attr(cat_key).set(roles_to_save)
            if cat_key == "platforms":
                await self.config.guild(ctx.guild).platform_emojis.set(emojis_to_save)

        await ctx.send("Setup complete. Use `!selfroles post`.")

    @selfroles.command()
    async def post(self, ctx, channel: Optional[discord.TextChannel] = None):
        channel = channel or ctx.channel
        guild_data = await self.config.guild(ctx.guild).all()

        p_emb = discord.Embed(title="🎮 Gaming Platforms", color=discord.Color.blurple())
        await channel.send(embed=p_emb, view=MultiRoleView(guild_data["platforms"], "platform", guild_data.get("platform_emojis")))

        l_emb = discord.Embed(title="🌍 Regional Roles", color=discord.Color.green())
        await channel.send(embed=l_emb, view=MultiRoleView(guild_data["locations"], "location"))

        pr_emb = discord.Embed(title="✨ Pronouns", color=discord.Color.teal())
        await channel.send(embed=pr_emb, view=MultiRoleView(guild_data["pronouns"], "pronoun"))