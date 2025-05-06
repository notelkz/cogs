import discord
from redbot.core import commands, app_commands
import os

ROLE_ID = 1369334433714409576  # The role to give

class ZeroEmbed(commands.Cog):
    """Posts the Zero Lives Left embed with attached images and a role button."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def zeroembed(self, ctx):
        """Posts the Zero Lives Left embed with images as attachments and a button."""

        cog_folder = os.path.dirname(os.path.abspath(__file__))
        autoroles_path = os.path.join(cog_folder, "autoroles.png")
        discordactivity_path = os.path.join(cog_folder, "discordactivity.png")

        embed1 = discord.Embed(
            color=0xFF0000
        )
        embed1.set_author(name="Zero Lives Left")
        embed1.set_image(url="attachment://autoroles.png")

        embed2 = discord.Embed(
            description=(
                "To receive automatic game roles, you need to have your "
                "[Discord User Activity Privacy](http://notelkz.net/images/discordactivity.png) "
                "setup correctly and click the button below."
            ),
            color=0xFF0015
        )
        embed2.set_image(url="attachment://discordactivity.png")

        # Create the button
        view = RoleButtonView(ctx.author, ctx.guild)

        try:
            with open(autoroles_path, "rb") as f1, open(discordactivity_path, "rb") as f2:
                file1 = discord.File(f1, filename="autoroles.png")
                file2 = discord.File(f2, filename="discordactivity.png")
                await ctx.send(
                    embeds=[embed1, embed2],
                    files=[file1, file2],
                    view=view
                )
        except FileNotFoundError:
            await ctx.send("One or both image files are missing from the cog folder.")

class RoleButtonView(discord.ui.View):
    def __init__(self, author, guild):
        super().__init__(timeout=180)  # 3 minutes timeout
        self.guild = guild

    @discord.ui.button(label="Get Game Role", style=discord.ButtonStyle.danger, custom_id="get_game_role")
    async def get_role_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        role = self.guild.get_role(ROLE_ID)
        if not role:
            await interaction.response.send_message("Role not found. Please contact an admin.", ephemeral=True)
            return

        member = self.guild.get_member(interaction.user.id)
        if not member:
            await interaction.response.send_message("Could not find you in this server.", ephemeral=True)
            return

        if role in member.roles:
            await interaction.response.send_message("You already have this role!", ephemeral=True)
            return

        try:
            await member.add_roles(role, reason="Button click in zeroembed")
            await interaction.response.send_message("Role given!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I do not have permission to give you this role.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {e}", ephemeral=True)
