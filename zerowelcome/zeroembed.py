import discord
from redbot.core import commands, checks
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import warning
from datetime import datetime, timezone

class ZeroWelcome(commands.Cog):
    """Send a welcome embed with application buttons."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.app_cog = None

    async def cog_load(self):
        self.app_cog = self.bot.get_cog("ApplicationManager")
        if not self.app_cog:
            print("[ZeroWelcome] ApplicationManager cog not loaded! ZeroWelcome will not work properly.")

    @commands.command()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def zerowelcome(self, ctx, channel: discord.TextChannel):
        """Send the welcome embed to a specified channel."""
        self.app_cog = self.bot.get_cog("ApplicationManager")
        if not self.app_cog:
            await ctx.send(warning("ApplicationManager cog is not loaded. Please load it first."))
            return

        embed = discord.Embed(
            title="Welcome",
            description="Please use the button below to create a new application if one wasn't created for you automatically upon you joining the Discord.\n\nThis can also be used if <@&1274512593842606080> wish to join and become a full <@&1018116224158273567>.",
            color=0x6A9A44,
            timestamp=datetime(2025, 5, 8, 12, 7, 0, tzinfo=timezone.utc)
        )
        embed.set_footer(text="If you have any issues, please use the Contact Moderator button.")
        embed.set_author(name="Zero Lives Left", url="http://zerolivesleft.net")
        embed.set_thumbnail(url="attachment://zerosmall.png")

        # Buttons
        view = ZeroWelcomeView(self.app_cog)

        await channel.send(embed=embed, view=view)
        await ctx.send(f"Welcome embed sent to {channel.mention}.")

class ZeroWelcomeView(discord.ui.View):
    def __init__(self, app_cog):
        super().__init__(timeout=None)
        self.app_cog = app_cog

    @discord.ui.button(label="Create New Application", style=discord.ButtonStyle.success, custom_id="zerowelcome_create")
    async def create_application(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        # Check if user already has an application
        if await self.app_cog.user_has_application(user):
            await self.app_cog.reopen_application(user)
            await interaction.response.send_message("Your application has been reopened. Please check your application channel.", ephemeral=True)
        else:
            await self.app_cog.create_application(user)
            await interaction.response.send_message("A new application channel has been created for you. Please check your channels.", ephemeral=True)

    @discord.ui.button(label="Contact Moderator", style=discord.ButtonStyle.primary, custom_id="zerowelcome_contact")
    async def contact_moderator(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        mod_role = guild.get_role(1274512593842606080)  # Replace with your Moderator role ID
        online_mods = [m for m in mod_role.members if m.status in (discord.Status.online, discord.Status.idle, discord.Status.dnd) and not m.bot]
        if online_mods:
            mod_mentions = " ".join(m.mention for m in online_mods)
            await interaction.response.send_message(f"{mod_mentions} - A user needs assistance!", ephemeral=False)
        else:
            await interaction.response.send_message(f"{mod_role.mention} - No moderators are currently online, but all will be notified.", ephemeral=False)

def setup(bot: Red):
    bot.add_cog(ZeroWelcome(bot))
