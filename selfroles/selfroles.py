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
        self.config = Config.get_conf(self, identifier=9988776655, force_registration=True)
        self.config.register_guild(
            platforms={},
            platform_emojis={},
            locations={},
            pronouns={}
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
            await interaction.response.send_message(f"Removed the **{role.name}** role.", ephemeral=True)
        else:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"Added the **{role.name}** role!", ephemeral=True)

    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def selfroles(self, ctx):
        """Self-assignable roles management"""
        pass

    @selfroles.command()
    async def setup(self, ctx):
        """Setup using Reactions for custom emojis."""
        setup_structure = {
            "platforms": ["PC", "Nintendo", "PlayStation", "Xbox"],
            "locations": ["Europe", "North America", "South America", "Asia", "Oceania", "Africa"],
            "pronouns": ["He/Him", "She/Her", "They/Them", "Other/Ask"]
        }

        await ctx.send("Starting setup. Type **quit** to exit.")

        for cat_key, labels in setup_structure.items():
            await ctx.send(f"--- **{cat_key.upper()}** ---")
            roles_to_save, emojis_to_save = {}, {}
            
            for label in labels:
                await ctx.send(f"Mention the role for **{label}**:")
                try:
                    r_msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=60.0)
                    if r_msg.content.lower() == "quit": return
                    
                    role_id = r_msg.role_mentions[0].id if r_msg.role_mentions else None
                    if not role_id:
                        match = re.search(r'\d{17,20}', r_msg.content)
                        role_id = int(match.group()) if match else None

                    if not role_id:
                        await ctx.send("❌ Role not found. Skipping.")
                        continue

                    key = label.lower().split("/")[0].split(" ")[0]
                    if "north" in label.lower(): key = "na"
                    if "south" in label.lower(): key = "sa"
                    if "other" in label.lower(): key = "ask"
                    roles_to_save[key] = role_id

                    if cat_key == "platforms":
                        p_msg = await ctx.send(f"**React to THIS message** with the custom emoji for **{label}** (or type 'skip'):")
                        try:
                            # Wait for reaction OR a skip message
                            reaction_task = self.bot.wait_for("reaction_add", check=lambda r, u: u == ctx.author and r.message.id == p_msg.id, timeout=30.0)
                            message_task = self.bot.wait_for("message", check=lambda m: m.author == ctx.author and m.content.lower() == "skip", timeout=30.0)
                            
                            done, pending = await asyncio.wait([reaction_task, message_task], return_when=asyncio.FIRST_COMPLETED)
                            for task in pending: task.cancel()

                            result = done.pop().result()
                            if isinstance(result, tuple):
                                emojis_to_save[key] = str(result[0].emoji)
                                await ctx.send(f"✅ Saved with {result[0].emoji}")
                        except asyncio.TimeoutError:
                            await ctx.send("Timed out. Using default.")
                except Exception as e:
                    await ctx.send(f"Error: {e}")

            await self.config.guild(ctx.guild).get_attr(cat_key).set(roles_to_save)
            if cat_key == "platforms":
                await self.config.guild(ctx.guild).platform_emojis.set(emojis_to_save)

        await ctx.send("Setup complete. Run `!selfroles post`.")

    @selfroles.command()
    async def post(self, ctx, channel: Optional[discord.TextChannel] = None):
        """Posts the formatted embeds with descriptions."""
        channel = channel or ctx.channel
        data = await self.config.guild(ctx.guild).all()
        
        # Define the embeds and text
        categories = [
            {
                "title": "🎮 Gaming Platforms",
                "desc": "Select the platforms you play on to find others to game with!",
                "type": "platform",
                "data_key": "platforms",
                "emoji_key": "platform_emojis",
                "color": 0x5865F2
            },
            {
                "title": "🌍 Regional Roles",
                "desc": "Choose your region to see local channels and get better ping in matches.",
                "type": "location",
                "data_key": "locations",
                "emoji_key": None,
                "color": 0x3BA55D
            },
            {
                "title": "✨ Pronouns",
                "desc": "Select your preferred pronouns to let the community know how to address you.",
                "type": "pronoun",
                "data_key": "pronouns",
                "emoji_key": None,
                "color": 0x1ABC9C
            }
        ]

        for cat in categories:
            roles = data.get(cat["data_key"], {})
            if not any(roles.values()): continue

            emojis = data.get(cat["emoji_key"], {}) if cat["emoji_key"] else {}
            view = MultiRoleView(roles, cat["type"], emojis)
            embed = discord.Embed(title=cat["title"], description=cat["desc"], color=cat["color"])
            await channel.send(embed=embed, view=view)