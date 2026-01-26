import discord
from redbot.core import commands, Config
from discord.ui import Button, View
from typing import Optional
import asyncio

# --- VIEWS ---

class MultiRoleView(View):
    """Generic View for all three categories with Emojis"""
    def __init__(self, roles: dict, category: str):
        super().__init__(timeout=None)
        
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

        for key, label, style, emoji in layouts.get(category, []):
            if roles.get(key):
                self.add_item(Button(
                    style=style,
                    label=label,
                    emoji=emoji,
                    custom_id=f"selfrole_{category}_{key}"
                ))

# --- COG ---

class SelfRoles(commands.Cog):
    """Combined Role Selection with Quit option and fixed Other/Ask label"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=5566778899)
        
        default_guild = {
            "platforms": {"pc": None, "nintendo": None, "playstation": None, "xbox": None},
            "locations": {"europe": None, "na": None, "sa": None, "asia": None, "oceania": None, "africa": None},
            "pronouns": {"he": None, "she": None, "they": None, "ask": None}
        }
        self.config.register_guild(**default_guild)
        bot.add_listener(self.button_listener, "on_interaction")

    async def button_listener(self, interaction: discord.Interaction):
        if not interaction.data or "custom_id" not in interaction.data:
            return
            
        custom_id = interaction.data["custom_id"]
        if not custom_id.startswith("selfrole_"):
            return
            
        parts = custom_id.split("_")
        category_map = {"platform": "platforms", "location": "locations", "pronoun": "pronouns"}
        category = parts[1]
        role_key = parts[2]
        
        target_config = category_map.get(category)
        if not target_config:
            return

        data = await self.config.guild(interaction.guild).get_attr(target_config)()

        role_id = data.get(role_key)
        if not role_id:
            return await interaction.response.send_message("Role not configured.", ephemeral=True)
            
        role = interaction.guild.get_role(role_id)
        if not role:
            return await interaction.response.send_message("Role no longer exists.", ephemeral=True)
            
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role)
            await interaction.response.send_message(f"Removed **{role.name}**.", ephemeral=True)
        else:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"Added **{role.name}**.", ephemeral=True)

    @commands.group(name="selfroles")
    @commands.admin_or_permissions(manage_guild=True)
    async def selfroles(self, ctx):
        """Manage all self-assignable roles"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @selfroles.command()
    async def setup(self, ctx):
        """Run the full setup with 'quit' and 'skip' options"""
        setup_structure = {
            "platforms": ["PC", "Nintendo", "PlayStation", "Xbox"],
            "locations": ["Europe", "North America", "South America", "Asia", "Oceania", "Africa"],
            "pronouns": ["He/Him", "She/Her", "They/Them", "Other/Ask"]
        }

        await ctx.send("Starting setup. Type **skip** to skip a role, or **quit** to cancel setup entirely.")

        for cat_key, labels in setup_structure.items():
            await ctx.send(f"--- Setting up **{cat_key.upper()}** ---")
            roles_to_save = {}
            for label in labels:
                await ctx.send(f"Mention the role or ID for **{label}**:")
                try:
                    msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=45.0)
                    content = msg.content.lower()
                    
                    if content == "quit":
                        return await ctx.send("❌ Setup cancelled.")
                    
                    # Normalizing keys for storage
                    key = label.lower().split("/")[0].split(" ")[0]
                    if "north" in label.lower(): key = "na"
                    if "south" in label.lower(): key = "sa"
                    if "other" in label.lower(): key = "ask"

                    if content == "skip":
                        roles_to_save[key] = None
                        continue
                    
                    rid = msg.role_mentions[0].id if msg.role_mentions else int(msg.content)
                    roles_to_save[key] = rid
                except ValueError:
                    await ctx.send("That didn't look like a valid ID. Skipping this role.")
                except asyncio.TimeoutError:
                    return await ctx.send("⌛ Setup timed out. Please restart.")

            await self.config.guild(ctx.guild).get_attr(cat_key).set(roles_to_save)

        await ctx.send("✅ Setup finished! Use `!selfroles post` to display.")

    @selfroles.command()
    async def post(self, ctx, channel: Optional[discord.TextChannel] = None):
        """Post all role selection embeds"""
        channel = channel or ctx.channel
        guild_data = await self.config.guild(ctx.guild).all()

        p_embed = discord.Embed(title="🎮 Gaming Platforms", description="Select your platforms to find other players!", color=discord.Color.blurple())
        await channel.send(embed=p_embed, view=MultiRoleView(guild_data["platforms"], "platform"))

        l_embed = discord.Embed(title="🌍 Regional Roles", description="Select your region for local pings and news.", color=discord.Color.green())
        await channel.send(embed=l_embed, view=MultiRoleView(guild_data["locations"], "location"))

        pr_embed = discord.Embed(title="✨ Pronouns", description="Select your preferred pronouns.", color=discord.Color.teal())
        await channel.send(embed=pr_embed, view=MultiRoleView(guild_data["pronouns"], "pronoun"))

        await ctx.send(f"All roles posted to {channel.mention}!")