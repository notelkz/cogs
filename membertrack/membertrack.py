from redbot.core import commands, Config
import discord
from datetime import datetime

class MemberTracker(commands.Cog):
    """Basic member tracking cog"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "notification_channel": None,
            "test_value": False
        }
        self.config.register_guild(**default_guild)

    @commands.group()
    @commands.admin_or_permissions(administrator=True)
    async def membertrack(self, ctx):
        """Member tracking commands"""
        pass

    @membertrack.command()
    async def test(self, ctx):
        """Test if the cog is working"""
        await ctx.send("MemberTracker cog is working!")

    @membertrack.command()
    async def setchannel(self, ctx, channel: discord.TextChannel):
        """Set the notification channel"""
        await self.config.guild(ctx.guild).notification_channel.set(channel.id)
        await ctx.send(f"Channel set to {channel.mention}")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Test member join event"""
        if member.bot:
            return
            
        channel_id = await self.config.guild(member.guild).notification_channel()
        if channel_id:
            channel = member.guild.get_channel(channel_id)
            if channel:
                await channel.send(f"New member joined: {member.mention}")

async def setup(bot):
    await bot.add_cog(MemberTracker(bot))
