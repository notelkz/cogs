import discord
from redbot.core import commands
from redbot.core.bot import Red
import os

EMBED_CONFIG = {
    "color": 0x210E3E,  # A deep blue, fits Battlefield theme
    "footer": "Choose your roles using the buttons below.",
}

ROLES = [
    {
        "label": "Battlefield 6",
        "role_id": 1369393506635747338,
        "emoji": "",
    },
    {
        "label": "Battlefield 2042",
        "role_id": 1274316388424745030,
        "emoji": "",
    },
    {
        "label": "Battlefield V",
        "role_id": 1049456888087068692,
        "emoji": "",
    },
    {
        "label": "Battlefield 1",
        "role_id": 1274316717442728058,
        "emoji": "",
    },
    {
        "label": "Battlefield 4/3/2",
        "role_id": 1357985105901256816,
        "emoji": "",
    },
]

IMAGE_FILENAME = "zeroroles.png"  # Expects zeroroles.png in the same folder as this file

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
            color=EMBED_CONFIG["color"]
        )
        embed.set_footer(text=EMBED_CONFIG["footer"])
        embed.set_image(url="attachment://zeroroles.png")

        # Get the directory where zeroroles.py is located
        cog_folder = os.path.dirname(os.path.abspath(__file__))
        image_path = os.path.join(cog_folder, IMAGE_FILENAME)
        try:
            file = discord.File(image_path, filename=IMAGE_FILENAME)
        except Exception:
            await ctx.send("Image file not found. Please add 'zeroroles.png' to the same folder as zeroroles.py.")
            return

        await ctx.send(
            embed=embed,
            file=file,
            view=ZeroRolesView(ROLES)
        )

async def setup(bot: Red):
    await bot.add_cog(ZeroRoles(bot))
