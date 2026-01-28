import discord
from redbot.core import commands, Config
from discord.ui import Button, View
from typing import Optional
import asyncio
import re

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
            role_id = roles.get(key)
            if not role_id:
                continue

            # This part now handles custom server emojis correctly
            emoji_to_use = custom_emojis.get(key) or default_emoji

            self.add_item(Button(
                style=style,
                label=label,
                emoji=emoji_to_use,
                custom_id=f"selfrole_{category}_{key}"
            ))

class SelfRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=5566778899)
        self.config.register_guild(platforms={}, platform_emojis={}, locations={}, pronouns={})
        bot.add_listener(self.button_listener, "on_interaction")

    async def button_listener(self, interaction: discord.Interaction):
        if not interaction.data or "custom_id" not in interaction.data:
            return
        cid = interaction.data["custom_id"]
        if not cid.startswith("selfrole_"):
            return
            
        parts = cid.split("_")
        cat_map = {"platform": "platforms", "location": "locations", "pronoun": "pronouns"}
        data = await self.config.guild(interaction.guild).get_attr(cat_map[parts[1]])()
        
        role = interaction.guild.get_role(data.get(parts[2]))
        if not role: return
            
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
        """Setup: Type '@Role :custom_emoji:'"""
        setup_structure = {
            "platforms": ["PC", "Nintendo", "PlayStation", "Xbox"],
            "locations": ["Europe", "North America", "South America", "Asia", "Oceania", "Africa"],
            "pronouns": ["He/Him", "She/Her", "They/Them", "Other/Ask"]
        }

        await ctx.send("Starting setup. Use **skip** or **quit** as needed.")

        for cat_key, labels in setup_structure.items():
            await ctx.send(f"--- **{cat_key.upper()}** ---")
            roles_to_save = {}
            emojis_to_save = {}
            
            for label in labels:
                await ctx.send(f"Role & Emoji for **{label}**:")
                try:
                    msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=60.0)
                    if msg.content.lower() == "quit": return
                    if msg.content.lower() == "skip": continue

                    # 1. Get Role ID
                    role_id = None
                    if msg.role_mentions:
                        role_id = msg.role_mentions[0].id
                    else:
                        match = re.search(r'\d{17,20}', msg.content)
                        if match: role_id = int(match.group())
                    
                    if not role_id:
                        await ctx.send("Role not found. skipping.")
                        continue

                    key = label.lower().split("/")[0].split(" ")[0]
                    if "north" in label.lower(): key = "na"
                    if "south" in label.lower(): key = "sa"
                    if "other" in label.lower(): key = "ask"
                    roles_to_save[key] = role_id

                    # 2. Get Custom Emoji correctly
                    if cat_key == "platforms":
                        # Check if message contains a custom emoji
                        custom_emoji_match = re.search(r'<(a?):(\w+):(\d+)>', msg.content)
                        if custom_emoji_match:
                            emojis_to_save[key] = custom_emoji_match.group(0)
                        else:
                            # Fallback: check for standard unicode emoji in the text
                            cleaned = re.sub(r'<@&\d+>|\d{17,20}', '', msg.content).strip()
                            if cleaned:
                                emojis_to_save[key] = cleaned.split()[0]

                except asyncio.TimeoutError:
                    return await ctx.send("Timed out.")

            await self.config.guild(ctx.guild).get_attr(cat_key).set(roles_to_save)
            if cat_key == "platforms":
                await self.config.guild(ctx.guild).platform_emojis.set(emojis_to_save)

        await ctx.send("Setup complete. Run `!selfroles post`.")

    @selfroles.command()
    async def post(self, ctx, channel: Optional[discord.TextChannel] = None):
        channel = channel or ctx.channel
        data = await self.config.guild(ctx.guild).all()
        
        # We wrap the whole thing in one try-except for the 400 error
        try:
            p_view = MultiRoleView(data["platforms"], "platform", data.get("platform_emojis"))
            await channel.send(embed=discord.Embed(title="🎮 Gaming Platforms", color=0x5865F2), view=p_view)
            
            l_view = MultiRoleView(data["locations"], "location")
            await channel.send(embed=discord.Embed(title="🌍 Regional Roles", color=0x3BA55D), view=l_view)
            
            pr_view = MultiRoleView(data["pronouns"], "pronoun")
            await channel.send(embed=discord.Embed(title="✨ Pronouns", color=0x1ABC9C), view=pr_view)
        except discord.HTTPException as e:
            await ctx.send(f"❌ Discord rejected the emojis. Error: {e}")