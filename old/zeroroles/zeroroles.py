import discord
from redbot.core import commands
from redbot.core.bot import Red
import os

ROLE_MENUS = {
    "battlefield": {
        "image": "battlefield.png",
        "color": 0x210E3E,
        "footer": "Choose your Battlefield roles below.",
        "style": discord.ButtonStyle.primary,  # blue
        "roles": [
            {"label": "Battlefield 6", "role_id": 1369393506635747338},
            {"label": "Battlefield 2042", "role_id": 1274316388424745030},
            {"label": "Battlefield V", "role_id": 1049456888087068692},
            {"label": "Battlefield 1", "role_id": 1274316717442728058},
            {"label": "Battlefield 3/4", "role_id": 1357985105901256816},
            {"label": "Battlefield Legacy", "role_id": 1369404415882166303},
        ],
    },
    "fps": {
        "image": "fps.png",
        "color": 0xB22222,
        "footer": "Choose your FPS roles below.",
        "style": discord.ButtonStyle.danger,  # red
        "roles": [
            {"label": "Squad", "role_id": 1357987796731822232},
            {"label": "Hell Let Loose", "role_id": 1357988329567817788},
            {"label": "Delta Force", "role_id": 1357988267554902138},
            {"label": "Arma", "role_id": 1357988461793116250},
        ],
    },
    "hero": {
        "image": "hero.png",
        "color": 0x228B22,
        "footer": "Choose your Hero Shooter roles below.",
        "style": discord.ButtonStyle.success,  # green
        "roles": [
            {"label": "Overwatch", "role_id": 1357988893256843314},
            {"label": "Marvel Rivals", "role_id": 1357988962098221066},
            {"label": "Valorant", "role_id": 1357989018230456332},
            {"label": "FragPunk", "role_id": 1357989070357401700},
        ],
    },
    "extraction": {
        "image": "extraction.png",
        "color": 0x808080,
        "footer": "Choose your Extraction Shooter roles below.",
        "style": discord.ButtonStyle.secondary,  # gray
        "roles": [
            {"label": "ARC Raiders", "role_id": 1369965947045150791},
            {"label": "Escape from Tarkov", "role_id": 1357988778693886083},
        ],
    },
    "br": {
        "image": "br.png",
        "color": 0x5865F2,
        "footer": "Choose your Battle Royale roles below.",
        "style": discord.ButtonStyle.primary,  # blurple
        "roles": [
            {"label": "Warzone", "role_id": 1357988210361372713},
            {"label": "Apex Legends", "role_id": 1357988166870630520},
            {"label": "Fortnite", "role_id": 1184916363702173829},
        ],
    },
    "others": {
        "image": "others.png",
        "color": 0xA9A9A9,
        "footer": "Choose your Other Game roles below.",
        "style": discord.ButtonStyle.secondary,  # gray
        "roles": [
            {"label": "GTA Online", "role_id": 1366446584900096122},
            {"label": "HELLDIVERS 2", "role_id": 1358213819062554826},
        ],
    },
}

class ZeroRolesView(discord.ui.View):
    def __init__(self, roles, style):
        super().__init__(timeout=None)
        for role in roles:
            self.add_item(ZeroRoleButton(role, style))

class ZeroRoleButton(discord.ui.Button):
    def __init__(self, role_info, style):
        super().__init__(
            label=role_info["label"],
            style=style,
            custom_id=f"zerorolesel_{role_info['role_id']}",
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
    """Multi-section Role Selector (ZeroRoles)"""

    def __init__(self, bot: Red):
        self.bot = bot

    @commands.admin_or_permissions(manage_guild=True)
    @commands.group()
    async def zeroroles(self, ctx: commands.Context):
        """Send a role selector menu. Usage: [p]zeroroles <section>"""
        if ctx.invoked_subcommand is None:
            await ctx.send("Please specify a section: battlefield, fps, hero, extraction, br, others.")

    @zeroroles.command()
    async def battlefield(self, ctx: commands.Context):
        await self._send_menu(ctx, "battlefield")

    @zeroroles.command()
    async def fps(self, ctx: commands.Context):
        await self._send_menu(ctx, "fps")

    @zeroroles.command()
    async def hero(self, ctx: commands.Context):
        await self._send_menu(ctx, "hero")

    @zeroroles.command()
    async def extraction(self, ctx: commands.Context):
        await self._send_menu(ctx, "extraction")

    @zeroroles.command()
    async def br(self, ctx: commands.Context):
        await self._send_menu(ctx, "br")

    @zeroroles.command()
    async def others(self, ctx: commands.Context):
        await self._send_menu(ctx, "others")

    async def _send_menu(self, ctx, section):
        config = ROLE_MENUS[section]
        embed = discord.Embed(
            description="",
            color=config["color"]
        )
        embed.set_footer(text=config["footer"])
        embed.set_image(url=f"attachment://{config['image']}")

        # Get the directory where this .py file is located
        cog_folder = os.path.dirname(os.path.abspath(__file__))
        image_path = os.path.join(cog_folder, config["image"])
        try:
            file = discord.File(image_path, filename=config["image"])
        except Exception:
            await ctx.send(f"Image file not found. Please add '{config['image']}' to the same folder as this cog.")
            return

        await ctx.send(
            embed=embed,
            file=file,
            view=ZeroRolesView(config["roles"], config["style"])
        )

async def setup(bot: Red):
    await bot.add_cog(ZeroRoles(bot))
