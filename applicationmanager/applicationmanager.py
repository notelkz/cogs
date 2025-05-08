from redbot.core import commands

class ApplicationManager(commands.Cog):
    """Stub ApplicationManager for testing."""

    def __init__(self, bot):
        self.bot = bot

    async def user_has_application(self, user):
        return False  # Always pretend user has no application

    async def reopen_application(self, user):
        pass  # Do nothing

    async def create_application(self, user):
        pass  # Do nothing

async def setup(bot):
    await bot.add_cog(ApplicationManager(bot))
