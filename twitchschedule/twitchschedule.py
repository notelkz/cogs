import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import Optional
import aiohttp
import datetime
import asyncio

class TwitchSchedule(commands.Cog):
    """Sync Twitch streaming schedule to Discord"""
    
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
            print("[TwitchSchedule] No credentials found")
            return None
            
        client_id, client_secret = credentials
        async with aiohttp.ClientSession() as session:
            url = "https://id.twitch.tv/oauth2/token"
            params = {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "client_credentials"
            }
            try:
                async with session.post(url, params=params) as resp:
                    print(f"[TwitchSchedule] Token request status: {resp.status}")
                    if resp.status != 200:
                        error_text = await resp.text()
                        print(f"[TwitchSchedule] Token error response: {error_text}")
                        return None
                    data = await resp.json()
                    if "access_token" not in data:
                        print(f"[TwitchSchedule] No access token in response: {data}")
                        return None
                    print("[TwitchSchedule] Successfully obtained access token")
                    return data.get("access_token")
            except Exception as e:
                print(f"[TwitchSchedule] Error getting token: {str(e)}")
                return None

    async def get_schedule(self, username: str):
        """Fetch schedule from Twitch API"""
        print(f"[TwitchSchedule] Attempting to fetch schedule for {username}")
        
        credentials = await self.get_credentials()
        if not credentials:
            print("[TwitchSchedule] No credentials found")
            return None

        if not self.access_token:
            print("[TwitchSchedule] No access token, attempting to get one")
            self.access_token = await self.get_twitch_token()
            if not self.access_token:
                print("[TwitchSchedule] Failed to get access token")
                return None

        client_id, _ = credentials
        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {self.access_token}"
        }

        print(f"[TwitchSchedule] Headers being used: Client-ID: {client_id[:6]}... Bearer: {self.access_token[:6]}...")

        async with aiohttp.ClientSession() as session:
            url = f"https://api.twitch.tv/helix/schedule?broadcaster_login={username}"
            print(f"[TwitchSchedule] Requesting URL: {url}")
            try:
                async with session.get(url, headers=headers) as resp:
                    print(f"[TwitchSchedule] Response Status: {resp.status}")
                    response_text = await resp.text()
                    print(f"[TwitchSchedule] Response Text: {response_text}")
                    
                    if resp.status == 401:
                        print("[TwitchSchedule] Token expired, refreshing...")
                        self.access_token = await self.get_twitch_token()
                        return await self.get_schedule(username)
                    elif resp.status == 404:
                        print(f"[TwitchSchedule] Schedule not found for user {username}")
                        return None
                    elif resp.status != 200:
                        print(f"[TwitchSchedule] Error response: {response_text}")
                        return None
                    
                    try:
                        data = await resp.json()
                        print(f"[TwitchSchedule] Parsed JSON response: {data}")
                        
                        if "data" not in data:
                            print("[TwitchSchedule] No data field in response")
                            return None
                            
                        segments = data.get("data", {}).get("segments", [])
                        print(f"[TwitchSchedule] Found {len(segments)} schedule segments")
                        return segments
                    except Exception as e:
                        print(f"[TwitchSchedule] Error parsing JSON: {str(e)}")
                        return None
                        
            except Exception as e:
                print(f"[TwitchSchedule] Network error: {str(e)}")
                return None

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
                                print(f"[TwitchSchedule] Could not fetch schedule for {twitch_username}")

                await asyncio.sleep(update_interval)
            except Exception as e:
                print(f"[TwitchSchedule] Error in schedule update loop: {e}")
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

        future_streams = False
        for segment in schedule:
            start_time = datetime.datetime.fromisoformat(segment["start_time"].replace("Z", "+00:00"))
            title = segment["title"]
            category = segment.get("category", {}).get("name", "No Category")

            # Only show future streams
            if start_time > datetime.datetime.utcnow():
                future_streams = True
                embed.add_field(
                    name=f"{start_time.strftime('%Y-%m-%d %H:%M UTC')}",
                    value=f"**{title}**\nCategory: {category}",
                    inline=False
                )

        if not future_streams:
            embed.add_field(
                name="No Upcoming Streams",
                value="Check back later for new streams!",
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
        await ctx.message.delete(delay=5)
        
        await self.bot.set_shared_api_tokens("twitch", 
            client_id=client_id,
            client_secret=client_secret
        )
        await ctx.send("Twitch API credentials have been set!", delete_after=5)

    @commands.command()
    @commands.is_owner()
    async def checktwitchcreds(self, ctx):
        """Check if Twitch credentials are properly set and working"""
        await ctx.message.delete(delay=5)
        
        credentials = await self.get_credentials()
        if not credentials:
            await ctx.send("No Twitch credentials found! Use `[p]settwitchcreds` to set them.", delete_after=10)
            return
            
        client_id, client_secret = credentials
        masked_id = client_id[:6] + "*" * (len(client_id) - 6)
        masked_secret = client_secret[:6] + "*" * (len(client_secret) - 6)
        
        # Test token generation
        token = await self.get_twitch_token()
        
        embed = discord.Embed(
            title="Twitch Credentials Status",
            color=discord.Color.blue() if token else discord.Color.red()
        )
        embed.add_field(name="Client ID", value=f"Set: {masked_id}", inline=False)
        embed.add_field(name="Client Secret", value=f"Set: {masked_secret}", inline=False)
        embed.add_field(name="Token Generation", value="✅ Success" if token else "❌ Failed", inline=False)
        
        if token:
            # Test API access
            test_url = "https://api.twitch.tv/helix/users"
            headers = {
                "Client-ID": client_id,
                "Authorization": f"Bearer {token}"
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(test_url, headers=headers) as resp:
                    embed.add_field(
                        name="API Test",
                        value=f"✅ Working (Status: {resp.status})" if resp.status == 200 else f"❌ Failed (Status: {resp.status})",
                        inline=False
                    )
        
        await ctx.send(embed=embed, delete_after=15)

    @commands.group(aliases=["tsched"])
    @commands.admin_or_permissions(manage_guild=True)
    async def twitchschedule(self, ctx):
        """Manage Twitch schedule settings"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @twitchschedule.command(name="setchannel")
    async def setchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel for schedule updates"""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"Schedule updates will be posted in {channel.mention}")

    @twitchschedule.command(name="setuser")
    async def setuser(self, ctx, username: str):
        """Set the Twitch username to track"""
        await self.config.guild(ctx.guild).twitch_username.set(username.lower())
        await ctx.send(f"Now tracking schedule for {username}")

    @twitchschedule.command(name="setinterval")
    async def setinterval(self, ctx, hours: int):
        """Set how often to check for schedule updates (in hours)"""
        if hours < 1:
            await ctx.send("Interval must be at least 1 hour")
            return
        await self.config.guild(ctx.guild).update_interval.set(hours * 3600)
        await ctx.send(f"Schedule will update every {hours} hours")

    @twitchschedule.command(name="settings")
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

    @twitchschedule.command(name="forceupdate")
    async def forceupdate(self, ctx):
        """Force an immediate schedule update"""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        twitch_username = await self.config.guild(ctx.guild).twitch_username()

        print(f"[TwitchSchedule] Force update requested for {twitch_username}")

        if not channel_id or not twitch_username:
            await ctx.send("Please set both channel and username first!")
            return

        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("Cannot find the configured channel!")
            return

        credentials = await self.get_credentials()
        if not credentials:
            await ctx.send("Twitch API credentials are not set! Please use `[p]settwitchcreds` to set them.")
            return

        status_message = await ctx.send(f"Attempting to fetch schedule for {twitch_username}...")
        
        schedule = await self.get_schedule(twitch_username)
        if schedule:
            await self.post_schedule(channel, schedule)
            await status_message.edit(content="Schedule has been updated!")
        else:
            error_msg = (
                "Could not fetch schedule. Common issues:\n"
                "1. Incorrect Twitch username\n"
                "2. The streamer doesn't have any scheduled streams\n"
                "3. API credentials might be invalid\n\n"
                f"Current settings:\n"
                f"Username: {twitch_username}\n"
                f"Channel: {channel.mention}\n\n"
                "Use `[p]checktwitchcreds` to verify API credentials."
            )
            await status_message.edit(content=error_msg)

def setup(bot: Red):
    bot.add_cog(TwitchSchedule(bot))
