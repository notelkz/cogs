from redbot.core import commands, Config
import discord
from datetime import datetime, timedelta
import asyncio

class MemberTracker(commands.Cog):
    """Tracks member joins and manages roles after a specified time period"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "notification_channel": None,
            "wait_period": 14,
            "member_joins": {},
            "roles_to_add": [],
            "roles_to_remove": [],
            "notify_role_changes": True
        }
        self.config.register_guild(**default_guild)
        self.bg_task = self.bot.loop.create_task(self.check_member_duration())

    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.bg_task.cancel()

    @commands.group()
    @commands.admin_or_permissions(administrator=True)
    async def membertrack(self, ctx):
        """Member tracking commands"""
        pass

    @membertrack.command(name="test")
    async def test_command(self, ctx):
        """Test if the cog is working"""
        await ctx.send("MemberTracker cog is working!")

    @membertrack.command()
    async def setchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel for notifications"""
        await self.config.guild(ctx.guild).notification_channel.set(channel.id)
        await ctx.send(f"Notification channel set to {channel.mention}")

async def setup(bot):
    await bot.add_cog(MemberTracker(bot))
