import discord
from redbot.core import commands, Config
from redbot.core.bot import Red

class EShut(commands.Cog):
    """Send shutdown notifications in a specified channel."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=95932766180, force_registration=True
        )
        default_guild = {"channel_id": None}
        self.config.register_guild(**default_guild)
        
        # Register shutdown handler
        self.bot.add_listener(self.shutdown_handler, "on_red_shutdown")
    
    @commands.group(name="eshut")
    @commands.guild_only()
    async def _eshut(self, ctx: commands.Context):
        """Manage shutdown notifications."""
        pass
    
    @_eshut.command(name="setup")
    @commands.admin_or_permissions(manage_guild=True)
    async def _setup(self, ctx: commands.Context):
        """Set the current channel for shutdown notifications."""
        await self.config.guild(ctx.guild).channel_id.set(ctx.channel.id)
        await ctx.send(f"✅ Shutdown notifications will be sent to {ctx.channel.mention}")
    
    @_eshut.command(name="test")
    @commands.admin_or_permissions(manage_guild=True)
    async def _test(self, ctx: commands.Context):
        """Test the shutdown notification."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        if not channel_id:
            await ctx.send("❌ No shutdown channel configured. Use `eshut setup` first.")
            return
            
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("❌ The configured channel no longer exists. Please run `eshut setup` again.")
            return
            
        await channel.send("🔄 **Bot Shutdown Test:** This is what you'll see when the bot shuts down.")
        await ctx.send("✅ Test notification sent!")
    
    @_eshut.command(name="status")
    async def _status(self, ctx: commands.Context):
        """Check the current shutdown notification channel."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        if not channel_id:
            await ctx.send("❌ No shutdown channel configured.")
            return
            
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("❌ The configured channel no longer exists.")
            return
            
        await ctx.send(f"✅ Shutdown notifications will be sent to {channel.mention}")
    
    async def shutdown_handler(self):
        """Handle the bot shutdown event."""
        for guild in self.bot.guilds:
            try:
                channel_id = await self.config.guild(guild).channel_id()
                if channel_id:
                    channel = guild.get_channel(channel_id)
                    if channel and channel.permissions_for(guild.me).send_messages:
                        await channel.send("🛑 **Bot is shutting down now!** I'll be back soon.")
            except Exception:
                # We don't want errors during shutdown to cause issues
                pass

def setup(bot: Red):
    """Add the cog to the bot."""
    bot.add_cog(EShut(bot))
