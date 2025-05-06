import discord
from redbot.core import commands
import os

class ZeroEmbed(commands.Cog):
    """Posts the Zero Lives Left embed with attached images."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def zeroembed(self, ctx):
        """Posts the Zero Lives Left embed with images as attachments."""

        # Get the path to the images (assuming they are in the same folder as this file)
        cog_folder = os.path.dirname(os.path.abspath(__file__))
        autoroles_path = os.path.join(cog_folder, "autoroles.png")
        discordactivity_path = os.path.join(cog_folder, "discordactivity.png")

        # Create embeds
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

        # Open files and send
        try:
            with open(autoroles_path, "rb") as f1, open(discordactivity_path, "rb") as f2:
                file1 = discord.File(f1, filename="autoroles.png")
                file2 = discord.File(f2, filename="discordactivity.png")
                await ctx.send(
                    embeds=[embed1, embed2],
                    files=[file1, file2]
                )
        except FileNotFoundError:
            await ctx.send("One or both image files are missing from the cog folder.")

