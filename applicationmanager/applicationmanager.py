from redbot.core import commands
import discord

class ApplicationManager(commands.Cog):
    """Bridge for ZeroWelcome and DisApps."""

    def __init__(self, bot):
        self.bot = bot
        self.disapps = None

    async def cog_load(self):
        self.disapps = self.bot.get_cog("DisApps")
        if not self.disapps:
            print("[ApplicationManager] DisApps cog not loaded! ApplicationManager will not work properly.")

    def _get_disapps(self):
        # Always get the latest reference in case it's reloaded
        return self.bot.get_cog("DisApps")

    async def user_has_application(self, user: discord.Member):
        disapps = self._get_disapps()
        if not disapps:
            return False
        guild = user.guild
        applications = await disapps.config.guild(guild).applications()
        return str(user.id) in applications and applications[str(user.id)].get("channel_id") is not None

    async def reopen_application(self, user: discord.Member):
        disapps = self._get_disapps()
        if not disapps:
            return
        guild = user.guild
        applications = await disapps.config.guild(guild).applications()
        app_data = applications.get(str(user.id))
        if not app_data or not app_data.get("channel_id"):
            return  # No application to reopen

        channel = guild.get_channel(app_data["channel_id"])
        if not channel:
            return  # Channel doesn't exist

        # Restore channel permissions and move to applications category
        await disapps.restore_channel(channel, guild, user)
        # Set status to pending
        applications[str(user.id)]["status"] = "pending"
        await disapps.config.guild(guild).applications.set(applications)
        # Notify user
        try:
            await channel.send(f"{user.mention} Your application channel has been reopened. Please continue your application.")
        except Exception:
            pass

    async def create_application(self, user: discord.Member):
        disapps = self._get_disapps()
        if not disapps:
            return
        # This will call the same logic as if the user joined the server
        await disapps.on_member_join(user)

async def setup(bot):
    await bot.add_cog(ApplicationManager(bot))
