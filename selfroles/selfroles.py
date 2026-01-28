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
            if role_id:
                # Get emoji and strip any potential hidden whitespace
                raw_emoji = custom_emojis.get(key)
                if raw_emoji:
                    raw_emoji = raw_emoji.strip()
                
                emoji_to_use = raw_emoji if raw_emoji else default_emoji
                
                btn = Button(
                    style=style,
                    label=label,
                    emoji=emoji_to_use,
                    custom_id=f"selfrole_{category}_{key}"
                )
                self.add_item(btn)

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
    async def clear_data(self, ctx):
        """Wipes the saved emojis and roles to fix 'Invalid Emoji' errors."""
        await self.config.guild(ctx.guild).clear()
        await ctx.send("✅ Data cleared. You can now start a fresh `!selfroles setup`.")

    @selfroles.command()
    async def setup(self, ctx):
        setup_structure = {
            "platforms": ["PC", "Nintendo", "PlayStation", "Xbox"],
            "locations": ["Europe", "North America", "South America", "Asia", "Oceania", "Africa"],
            "pronouns": ["He/Him", "She/Her", "They/Them", "Other/Ask"]
        }

        await ctx.send("Type **skip** to skip, or **quit** to exit.")

        for cat_key, labels in setup_structure.items():
            await ctx.send(f"--- **{cat_key.upper()}** ---")
            roles_to_save = {}
            emojis_to_save = {}
            
            for label in labels:
                await ctx.send(f"Send Role & Emoji for **{label}**:")
                try:
                    msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=60.0)
                    content = msg.content.strip()
                    if content.lower() == "quit": return
                    if content.lower() == "skip": continue

                    # 1. Get Role ID
                    role_id = None
                    if msg.role_mentions:
                        role_id = msg.role_mentions[0].id
                    else:
                        match = re.search(r'\d{17,20}', content)
                        if match: role_id = int(match.group())
                    
                    if not role_id:
                        await ctx.send("No role found. Skipping.")
                        continue

                    # 2. Get Key
                    key = label.lower().split("/")[0].split(" ")[0]
                    if "north" in label.lower(): key = "na"
                    if "south" in label.lower(): key = "sa"
                    if "other" in label.lower(): key = "ask"

                    roles_to_save[key] = role_id

                    # 3. Get Emoji (Platforms Only)
                    if cat_key == "platforms":
                        # Strip out the role mention/ID to find the emoji
                        emoji_part = re.sub(r'<@&\d+>|\d{17,20}', '', content).strip()
                        if emoji_part:
                            # Discord emoji format: <:name:id> or raw unicode
                            emojis_to_save[key] = emoji_part

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
        
        try:
            # Platform Embed
            p_view = MultiRoleView(data["platforms"], "platform", data.get("platform_emojis"))
            await channel.send(embed=discord.Embed(title="🎮 Gaming Platforms", color=0x5865F2), view=p_view)
            
            # Location Embed
            l_view = MultiRoleView(data["locations"], "location")
            await channel.send(embed=discord.Embed(title="🌍 Regional Roles", color=0x3BA55D), view=l_view)
            
            # Pronoun Embed
            pr_view = MultiRoleView(data["pronouns"], "pronoun")
            await channel.send(embed=discord.Embed(title="✨ Pronouns", color=0x1ABC9C), view=pr_view)
        except Exception as e:
            await ctx.send(f"⚠️ Error: {e}\nTry running `!selfroles clear_data` and setting up again.")