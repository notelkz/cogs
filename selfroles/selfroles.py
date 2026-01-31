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
        
        # Internal layouts for the buttons
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

            # Uses the captured custom emoji string or the default unicode emoji
            emoji_to_use = custom_emojis.get(key) or default_emoji

            self.add_item(Button(
                style=style,
                label=label,
                emoji=emoji_to_use,
                custom_id=f"selfrole_{category}_{key}"
            ))

class SelfRoles(commands.Cog):
    """Self-assignable roles with custom emoji support and clean spacing."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=9988776655, force_registration=True)
        default_guild = {
            "platforms": {},
            "platform_emojis": {},
            "locations": {},
            "pronouns": {}
        }
        self.config.register_guild(**default_guild)
        bot.add_listener(self.button_listener, "on_interaction")

    async def button_listener(self, interaction: discord.Interaction):
        if not interaction.data or "custom_id" not in interaction.data:
            return
        cid = interaction.data["custom_id"]
        if not cid.startswith("selfrole_"):
            return
            
        parts = cid.split("_")
        cat_map = {"platform": "platforms", "location": "locations", "pronoun": "pronouns"}
        conf_category = cat_map.get(parts[1])
        if not conf_category: return

        data = await self.config.guild(interaction.guild).get_attr(conf_category)()
        role_id = data.get(parts[2])
        
        role = interaction.guild.get_role(role_id)
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
    async def clear_data(self, ctx):
        """Wipes the cog data for the server."""
        await self.config.guild(ctx.guild).clear()
        await ctx.send("✅ All role data cleared.")

    @selfroles.command()
    async def setup(self, ctx):
        """Setup using reactions to capture custom emojis accurately."""
        setup_structure = {
            "platforms": ["PC", "Nintendo", "PlayStation", "Xbox"],
            "locations": ["Europe", "North America", "South America", "Asia", "Oceania", "Africa"],
            "pronouns": ["He/Him", "She/Her", "They/Them", "Other/Ask"]
        }

        await ctx.send("Starting setup. Type **quit** to stop or **skip** to move past a role.")

        for cat_key, labels in setup_structure.items():
            await ctx.send(f"--- **{cat_key.upper()}** ---")
            roles_to_save, emojis_to_save = {}, {}
            
            for label in labels:
                await ctx.send(f"Mention the role for **{label}**:")
                try:
                    r_msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=60.0)
                    if r_msg.content.lower() == "quit": return
                    if r_msg.content.lower() == "skip": continue

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

                    # CUSTOM EMOJI CAPTURE VIA REACTION
                    prompt = await ctx.send(f"**React to THIS message** with the emoji for **{label}** (or type 'skip'):")
                    try:
                        reaction_task = self.bot.wait_for("reaction_add", check=lambda r, u: u == ctx.author and r.message.id == prompt.id, timeout=30.0)
                        message_task = self.bot.wait_for("message", check=lambda m: m.author == ctx.author and m.content.lower() == "skip", timeout=30.0)
                        
                        done, pending = await asyncio.wait([reaction_task, message_task], return_when=asyncio.FIRST_COMPLETED)
                        for t in pending: t.cancel()

                        result = done.pop().result()
                        if isinstance(result, tuple):
                            emojis_to_save[key] = str(result[0].emoji)
                            await ctx.send(f"✅ Saved with {result[0].emoji}")
                        else:
                            await ctx.send("✅ Using default emoji.")
                    except asyncio.TimeoutError:
                        await ctx.send("Timed out. Using default.")

                except Exception as e:
                    await ctx.send(f"Error: {e}")

            await self.config.guild(ctx.guild).get_attr(cat_key).set(roles_to_save)
            if cat_key == "platforms":
                await self.config.guild(ctx.guild).platform_emojis.set(emojis_to_save)

        await ctx.send("Setup finished! Use `!selfroles post` to see the result.")

    @selfroles.command()
    async def post(self, ctx, channel: Optional[discord.TextChannel] = None):
        """Posts the embeds with proper spacing."""
        channel = channel or ctx.channel
        data = await self.config.guild(ctx.guild).all()
        
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
                "emoji_key": "platform_emojis",
                "color": 0x3BA55D
            },
            {
                "title": "✨ Pronouns",
                "desc": "Select your preferred pronouns to let the community know how to address you.",
                "type": "pronoun",
                "data_key": "pronouns",
                "emoji_key": "platform_emojis",
                "color": 0x1ABC9C
            }
        ]

        for i, cat in enumerate(categories):
            roles = data.get(cat["data_key"], {})
            if not any(roles.values()): continue

            emojis = data.get("platform_emojis", {})
            view = MultiRoleView(roles, cat["type"], emojis)
            embed = discord.Embed(title=cat["title"], description=cat["desc"], color=cat["color"])
            
            await channel.send(embed=embed, view=view)

            # Use Discord's empty line markdown for spacing
            if i < len(categories) - 1:
                await channel.send("_ _")