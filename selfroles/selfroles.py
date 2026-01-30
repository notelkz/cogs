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

            # Uses the custom emoji string (ID) or the default unicode
            emoji_to_use = custom_emojis.get(key) or default_emoji

            self.add_item(Button(
                style=style,
                label=label,
                emoji=emoji_to_use,
                custom_id=f"selfrole_{category}_{key}"
            ))

class SelfRoles(commands.Cog):
    """Self-assignable roles with proper Red Config handling."""

    def __init__(self, bot):
        self.bot = bot
        # Using Red's Config system avoids Permission Errors on the filesystem
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
        """Setup using Reactions for 100% accurate custom emojis."""
        setup_structure = {
            "platforms": ["PC", "Nintendo", "PlayStation", "Xbox"],
            "locations": ["Europe", "North America", "South America", "Asia", "Oceania", "Africa"],
            "pronouns": ["He/Him", "She/Her", "They/Them", "Other/Ask"]
        }

        await ctx.send("Starting setup. Use **skip** or **quit**.")

        for cat_key, labels in setup_structure.items():
            await ctx.send(f"--- **{cat_key.upper()}** ---")
            roles_to_save = {}
            emojis_to_save = {}
            
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

                    # CUSTOM EMOJI SECTION (Platforms only)
                    if cat_key == "platforms":
                        prompt = await ctx.send(f"**React to THIS message** with the custom emoji for **{label}**:")
                        try:
                            reaction, user = await self.bot.wait_for(
                                "reaction_add", 
                                check=lambda r, u: u == ctx.author and r.message.id == prompt.id,
                                timeout=30.0
                            )
                            # Convert the emoji object to a string Discord understands (ID or Unicode)
                            emojis_to_save[key] = str(reaction.emoji)
                            await ctx.send(f"✅ Emoji {reaction.emoji} captured.")
                        except asyncio.TimeoutError:
                            await ctx.send("Timed out. Using default emoji.")

                except Exception:
                    await ctx.send("Error processing. Skipping.")

            await self.config.guild(ctx.guild).get_attr(cat_key).set(roles_to_save)
            if cat_key == "platforms":
                await self.config.guild(ctx.guild).platform_emojis.set(emojis_to_save)

        await ctx.send("Setup finished! Run `!selfroles post`.")

    @selfroles.command()
    async def post(self, ctx, channel: Optional[discord.TextChannel] = None):
        channel = channel or ctx.channel
        data = await self.config.guild(ctx.guild).all()
        
        # We send them one by one to ensure no single bad emoji crashes the whole command
        categories = [
            ("🎮 Gaming Platforms", "platform", "platforms", "platform_emojis", 0x5865F2),
            ("🌍 Regional Roles", "location", "locations", None, 0x3BA55D),
            ("✨ Pronouns", "pronoun", "pronouns", None, 0x1ABC9C)
        ]

        for title, cat_type, data_key, emoji_key, color in categories:
            try:
                roles = data.get(data_key, {})
                emojis = data.get(emoji_key, {}) if emoji_key else {}
                
                if not any(roles.values()): continue

                view = MultiRoleView(roles, cat_type, emojis)
                embed = discord.Embed(title=title, color=color)
                await channel.send(embed=embed, view=view)
            except Exception as e:
                await ctx.send(f"❌ Failed to post {title}: {e}")