import discord
from redbot.core import commands
from redbot.core.bot import Red

# === CONFIGURATION ===
EMBED_CONFIG = {
    "url": "attachment://zeroroles.png",
    "color": 0x210E3E,  # A deep blue, fits Battlefield theme
    "footer": "Choose your roles using the buttons below.",
}

ROLES = [
    {
        "label": "Battlefield 6",
        "role_id": 1369393506635747338,
        "emoji": "6️⃣",
    },
    {
        "label": "Battlefield 2042",
        "role_id": 1274316388424745030,
        "emoji": "🌐",
    },
    {
        "label": "Battlefield V",
        "role_id": 1049456888087068692,
        "emoji": "🇻",
    },
    {
        "label": "Battlefield 1",
        "role_id": 1274316717442728058,
        "emoji": "1️⃣",
    },
    {
        "label": "Battlefield 4/3/2",
        "role_id": 1357985105901256816,
        "emoji": "🎮",
    },
]

IMAGE_FILENAME = "zeroroles.png"  # Now expects /zeroroles.png in the bot's root folder

# === END CONFIGURATION ===

class ZeroRolesView(discord.ui.View):
    def __init__(self, roles):
        super().__init__(timeout=None)
        for role in roles:
            self.add_item(ZeroRoleButton(role))

class ZeroRoleButton(discord.ui.Button):
    def __init__(self, role_info):
        super().__init__(
            label=role_info["label"],
            style=discord.ButtonStyle.primary,
            custom_id=f"zeroroles_role_{role_info['role_id']}",
            emoji=role_info.get("emoji"),
        )
        self.role_id = role_info["role_id"]

    async def callback(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user
        role = guild.get_role(self.role_id)
        if not role:
            await interaction.response.send_message("Role not found. Please contact an admin.", ephemeral=True)
            return

        if role in member.roles:
            try:
                await member.remove_roles(role, reason="ZeroRoles role button")
                await interaction.response.send_message(f"Removed {role.name} role.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("I don't have permission to remove that role.", ephemeral=True)
        else:
            try:
                await member.add_roles(role, reason="ZeroRoles role button")
                await interaction.response.send_message(f"Added {role.name} role.", ephemeral=True)
            except discord.Forbidden:
                await interaction.response.send_message("I don't have permission to add that role.", ephemeral=True)

class ZeroRoles(commands.Cog):
    """Battlefield Role Selector (ZeroRoles)"""

    def __init__(self, bot: Red):
        self.bot = bot

    @commands.admin_or_permissions(manage_guild=True)
    @commands.command()
    async def zeroroles(self, ctx: commands.Context):
        """
        Send the Battlefield role selector embed (ZeroRoles).
        """
        embed = discord.Embed(
            description="",
            color=EMBED_CONFIG["color"],
            url=EMBED_CONFIG["url"]
        )
        embed.set_footer(text=EMBED_CONFIG["footer"])

        # Load image from bot's root folder
        image_path = f"./{IMAGE_FILENAME}"
        try:
            file = discord.File(image_path, filename=IMAGE_FILENAME)
        except Exception:
            await ctx.send("Image file not found. Please add 'zeroroles.png' to the bot's root folder.")
            return

        await ctx.send(
            embed=embed,
            file=file,
            view=ZeroRolesView(ROLES)
        )

def setup(bot: Red):
    bot.add_cog(ZeroRoles(bot))
