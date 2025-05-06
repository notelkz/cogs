import discord
from redbot.core import commands

class ZeroEmbed(commands.Cog):
    """Posts the Zero Lives Left embed."""

    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def zeroembed(self, ctx):
        """Posts the Zero Lives Left embed with images."""
        embed1 = discord.Embed(
            color=0xFF0000
        )
        embed1.set_author(name="Zero Lives Left")
        embed1.set_image(url="https://notelkz.net/images/autoroles.png")

        embed2 = discord.Embed(
            description=(
                "To receive automatic game roles, you need to have your "
                "[Discord User Activity Privacy](http://notelkz.net/images/discordactivity.png) "
                "setup correctly and click the button below."
            ),
            color=0xFF0015
        )
        embed2.set_image(url="https://notelkz.net/images/discordactivity.png")

        await ctx.send(embeds=[embed1, embed2])
