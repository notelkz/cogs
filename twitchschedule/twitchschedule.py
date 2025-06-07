import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import Optional
import aiohttp
import datetime
import asyncio

class TwitchSchedule(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "channel_id": None,
            "twitch_username": None,
            "update_interval": 3600  # Update every hour by default
        }
        self.config.register_guild(**default_guild)
        self.task = self.bot.loop.create_task(self.schedule_update_loop())
        self.access_token = None

    def cog_unload(self):
        self.task.cancel()

    async def get_credentials(self) -> Optional[tuple[str, str]]:
        """Get stored Twitch credentials"""
        client_id = await self.bot.get_shared_api_tokens("twitch")
        if client_id.get("client_id") and client_id.get("client_secret"):
            return client_id["client_id"], client_id["client_secret"]
        return None

    async def get_twitch_token(self):
        """Get OAuth token from Twitch"""
        credentials = await self.get_credentials()
        if not credentials:
            return None
            
        client_id, client_secret = credentials
        async with aiohttp.ClientSession() as session:
            url = "https://id.twitch.tv/oauth2/token"
            params = {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials"
            }
            async with session.post(url, params=params) as resp:
                data = await resp.json()
                return data.get("access_token")

    async def get_schedule(self, username: str):
        """Fetch schedule from Twitch API"""
        credentials = await self.get_credentials()
        if not credentials:
            return None

        if not self.access_token:
            self.access_token = await self.get_twitch_token()

        client_id, _ = credentials
        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {self.access_token}"
        }

        async with aiohttp.ClientSession() as session:
            url = f"https://api.twitch.tv/helix/schedule?broadcaster_login={username}"
            async with session.get(url, headers=headers) as resp:
                if resp.status == 401:  # Token expired
                    self.access_token = await self.get_twitch_token()
                    return await self.get_schedule(username)
                data = await resp.json()
                return data.get("data", {}).get("segments", [])

    async def schedule_update_loop(self):
        """Loop to periodically update the schedule"""
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild in self.bot.guilds:
                    channel_id = await self.config.guild(guild).channel_id()
                    twitch_username = await self.config.guild(guild).twitch_username()
                    update_interval = await self.config.guild(guild).update_interval()

                    if channel_id and twitch_username:
                        channel = guild.get_channel(channel_id)
                        if channel:
                            schedule = await self.get_schedule(twitch_username)
                            if schedule:
                                await self.post_schedule(channel, schedule)
                            else:
                                print(f"Could not fetch schedule for {twitch_username}")

                await asyncio.sleep(update_interval)
            except Exception as e:
                print(f"Error in schedule update loop: {e}")
                await asyncio.sleep(60)

    async def post_schedule(self, channel: discord.TextChannel, schedule: list):
        """Post the schedule to Discord"""
        if not schedule:
            return

        embed = discord.Embed(
            title="📺 Upcoming Streams",
            color=discord.Color.purple(),
            timestamp=datetime.datetime.utcnow()
        )

        for segment in schedule:
            start_time = datetime.datetime.fromisoformat(segment["start_time"].replace("Z", "+00:00"))
            title = segment["title"]
            category = segment.get("category", {}).get("name", "No Category")

            embed.add_field(
                name=f"{start_time.strftime('%Y-%m-%d %H:%M UTC')}",
                value=f"**{title}**\nCategory: {category}",
                inline=False
            )

        embed.set_footer(text="Last Updated")

        # Delete previous schedule messages
        async for message in channel.history(limit=10):
            if message.author == self.bot.user and message.embeds:
                await message.delete()

        await channel.send(embed=embed)

    @commands.command()
    @commands.is_owner()
    async def settwitchcreds(self, ctx, client_id: str, client_secret: str):
        """Set Twitch API credentials. Only bot owner can use this."""
        # This message will self-destruct after 5 seconds
        await ctx.message.delete(delay=5)
        
        await self.bot.set_shared_api_tokens("twitch", 
            client_id=client_id,
            client_secret=client_secret
        )
        # This response will self-destruct after 5 seconds
        await ctx.send("Twitch API credentials have been set!", delete_after=5)

    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def twitchschedule(self, ctx):
        """Manage Twitch schedule settings"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @twitchschedule.command()
    async def setchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel for schedule updates"""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"Schedule updates will be posted in {channel.mention}")

    @twitchschedule.command()
    async def setuser(self, ctx, username: str):
        """Set the Twitch username to track"""
        await self.config.guild(ctx.guild).twitch_username.set(username.lower())
        await ctx.send(f"Now tracking schedule for {username}")

    @twitchschedule.command()
    async def setinterval(self, ctx, hours: int):
        """Set how often to check for schedule updates (in hours)"""
        if hours < 1:
            await ctx.send("Interval must be at least 1 hour")
            return
        await self.config.guild(ctx.guild).update_interval.set(hours * 3600)
        await ctx.send(f"Schedule will update every {hours} hours")

    @twitchschedule.command()
    async def settings(self, ctx):
        """Show current settings"""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        update_interval = await self.config.guild(ctx.guild).update_interval()

        channel = ctx.guild.get_channel(channel_id) if channel_id else None
        
        embed = discord.Embed(
            title="Twitch Schedule Settings",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="Channel",
            value=channel.mention if channel else "Not set",
            inline=False
        )
        embed.add_field(
            name="Twitch Username",
            value=twitch_username or "Not set",
            inline=False
        )
        embed.add_field(
            name="Update Interval",
            value=f"{update_interval // 3600} hours" if update_interval else "Not set",
            inline=False
        )

        await ctx.send(embed=embed)

    @twitchschedule.command()
    async def forceupdate(self, ctx):
        """Force an immediate schedule update"""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        twitch_username = await self.config.guild(ctx.guild).twitch_username()

        if not channel_id or not twitch_username:
            await ctx.send("Please set both channel and username first!")
            return

        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("Cannot find the configured channel!")
            return

        schedule = await self.get_schedule(twitch_username)
        if schedule:
            await self.post_schedule(channel, schedule)
            await ctx.send("Schedule has been updated!")
        else:
            await ctx.send("Could not fetch schedule. Please check the username and credentials.")

def setup(bot: Red):
    bot.add_cog(TwitchSchedule(bot))
