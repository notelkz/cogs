from redbot.core import commands, Config
import discord

class EShut(commands.Cog):
    """Shutdown notification cog."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.config.register_guild(channel=None)

    @commands.group()
    @commands.guild_only()
    async def eshut(self, ctx):
        """Shutdown notification commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @eshut.command()
    @commands.has_permissions(manage_guild=True)
    async def setup(self, ctx):
        """Set this channel as the shutdown notification channel."""
        await self.config.guild(ctx.guild).channel.set(ctx.channel.id)
        await ctx.send(f"Shutdown notification channel set to {ctx.channel.mention}.")

    @eshut.command()
    async def send(self, ctx):
        """Send a shutdown message now (for testing)."""
        channel_id = await self.config.guild(ctx.guild).channel()
        if not channel_id:
            await ctx.send("No shutdown channel set. Use `!eshut setup` in the desired channel.")
            return
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("Configured channel not found. Please re-run `!eshut setup`.")
            return
        await channel.send("Bot is shutting down! :wave:")

    async def on_shutdown(self):
        # This is called when the bot is shutting down
        for guild in self.bot.guilds:
            channel_id = await self.config.guild(guild).channel()
            if channel_id:
                channel = guild.get_channel(channel_id)
                if channel:
                    try:
                        await channel.send("Bot is shutting down! :wave:")
                    except Exception:
                        pass  # Ignore errors (e.g., missing permissions)

def setup(bot):
    bot.add_cog(EShut(bot))
