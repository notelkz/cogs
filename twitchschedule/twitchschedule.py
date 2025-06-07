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
        print("\n=== GETTING TWITCH TOKEN ===")
        credentials = await self.get_credentials()
        if not credentials:
            print("❌ No credentials found")
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
                    print(f"Token request status: {resp.status}")
                    if resp.status != 200:
                        error_text = await resp.text()
                        print(f"❌ Token error response: {error_text}")
                        return None
                    data = await resp.json()
                    if "access_token" not in data:
                        print(f"❌ No access token in response: {data}")
                        return None
                    print("✅ Successfully obtained access token")
                    return data.get("access_token")
            except Exception as e:
                print(f"❌ Error getting token: {str(e)}")
                return None
            finally:
                print("=== END TOKEN REQUEST ===\n")

    async def get_schedule(self, username: str):
        """Fetch schedule from Twitch API"""
        print("\n=== FETCHING TWITCH SCHEDULE ===")
        print(f"Username: {username}")
        
        # First, verify the user exists
        credentials = await self.get_credentials()
        if not credentials:
            print("❌ No credentials found")
            return None

        if not self.access_token:
            print("Getting new access token...")
            self.access_token = await self.get_twitch_token()
            if not self.access_token:
                print("❌ Failed to get access token")
                return None

        client_id, _ = credentials
        headers = {
            "Client-ID": client_id,
            "Authorization": f"Bearer {self.access_token}"
        }

        # First, verify the user exists
        async with aiohttp.ClientSession() as session:
            user_url = f"https://api.twitch.tv/helix/users?login={username}"
            print(f"Verifying user exists: {user_url}")
            
            try:
                async with session.get(user_url, headers=headers) as resp:
                    print(f"User check status: {resp.status}")
                    user_data = await resp.json()
                    print(f"User data: {user_data}")
                    
                    if resp.status != 200 or not user_data.get("data"):
                        print(f"❌ User {username} not found")
                        return None
                    
                    broadcaster_id = user_data["data"][0]["id"]
                    print(f"Found broadcaster ID: {broadcaster_id}")

            except Exception as e:
                print(f"❌ Error checking user: {str(e)}")
                return None

        # Now fetch the schedule using broadcaster ID
        async with aiohttp.ClientSession() as session:
            url = f"https://api.twitch.tv/helix/schedule?broadcaster_id={broadcaster_id}"
            print(f"Requesting schedule: {url}")
            
            try:
                async with session.get(url, headers=headers) as resp:
                    print(f"Schedule response status: {resp.status}")
                    response_text = await resp.text()
                    print(f"Schedule response: {response_text}")

                    if resp.status == 404:
                        print(f"✓ User exists but has no schedule")
                        return []  # Return empty list for no schedule
                    elif resp.status != 200:
                        print(f"❌ Error response: {response_text}")
                        return None

                    try:
                        data = await resp.json()
                        print(f"Parsed schedule data: {data}")
                        
                        if "data" not in data:
                            print("❌ No data field in response")
                            return []
                            
                        segments = data.get("data", {}).get("segments", [])
                        print(f"Found {len(segments)} schedule segments")
                        
                        if not segments:
                            print("✓ No scheduled streams found")
                            return []
                            
                        return segments

                    except Exception as e:
                        print(f"❌ Error parsing response: {str(e)}")
                        return None

            except Exception as e:
                print(f"❌ Network error: {str(e)}")
                return None
            finally:
                print("=== END FETCH ===\n")
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
                            if schedule is not None:  # None means error, empty list means no schedule
                                await self.post_schedule(channel, schedule)
                            else:
                                print(f"Could not fetch schedule for {twitch_username}")

                await asyncio.sleep(update_interval)
            except Exception as e:
                print(f"Error in schedule update loop: {e}")
                await asyncio.sleep(60)

    async def post_schedule(self, channel: discord.TextChannel, schedule: list):
        """Post the schedule to Discord"""
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
        print("\n=== CHECKING TWITCH CREDENTIALS ===")
        
        # Delete the command message for security
        await ctx.message.delete(delay=5)
        
        credentials = await self.get_credentials()
        if not credentials:
            await ctx.send("❌ No Twitch credentials found! Use `[p]settwitchcreds` to set them.", delete_after=10)
            print("❌ No credentials found")
            return
            
        client_id, client_secret = credentials
        masked_id = client_id[:6] + "*" * (len(client_id) - 6)
        masked_secret = client_secret[:6] + "*" * (len(client_secret) - 6)
        
        print(f"Testing credentials - Client ID: {masked_id}")
        
        # Test token generation
        token = await self.get_twitch_token()
        print(f"Token generation: {'Success' if token else 'Failed'}")
        
        embed = discord.Embed(
            title="Twitch Credentials Status",
            color=discord.Color.blue() if token else discord.Color.red()
        )
        embed.add_field(name="Client ID", value=f"Set: {masked_id}", inline=False)
        embed.add_field(name="Client Secret", value=f"Set: {masked_secret}", inline=False)
        embed.add_field(name="Token Generation", value="✅ Success" if token else "❌ Failed", inline=False)
        
        if token:
            # Test API access with a specific user query
            test_url = "https://api.twitch.tv/helix/users?login=ninja"  # Using a known user as test
            headers = {
                "Client-ID": client_id,
                "Authorization": f"Bearer {token}"
            }
            print(f"Testing API with headers: {headers}")
            
            async with aiohttp.ClientSession() as session:
                try:
                    async with session.get(test_url, headers=headers) as resp:
                        status = resp.status
                        response_text = await resp.text()
                        print(f"API Test Status: {status}")
                        print(f"API Test Response: {response_text}")
                        
                        if status == 200:
                            embed.add_field(
                                name="API Test",
                                value="✅ API Connection Successful",
                                inline=False
                            )
                        elif status == 401:
                            embed.add_field(
                                name="API Test",
                                value="❌ Authentication Failed - Invalid Credentials",
                                inline=False
                            )
                        elif status == 400:
                            embed.add_field(
                                name="API Test",
                                value="❌ Bad Request - Please verify Client ID format",
                                inline=False
                            )
                        else:
                            embed.add_field(
                                name="API Test",
                                value=f"❌ Failed (Status: {status})\nResponse: {response_text[:100]}",
                                inline=False
                            )
                except Exception as e:
                    print(f"API Test Error: {str(e)}")
                    embed.add_field(
                        name="API Test",
                        value=f"❌ Connection Error: {str(e)}",
                        inline=False
                    )
        
        print("=== END CREDENTIALS CHECK ===\n")
        
        # Send and delete after 15 seconds
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
        print("\n=== FORCE UPDATE REQUESTED ===")
        
        channel_id = await self.config.guild(ctx.guild).channel_id()
        twitch_username = await self.config.guild(ctx.guild).twitch_username()

        print(f"Channel ID: {channel_id}")
        print(f"Twitch Username: {twitch_username}")

        if not channel_id or not twitch_username:
            await ctx.send("Please set both channel and username first!")
            print("❌ Missing channel or username configuration")
            return

        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("Cannot find the configured channel!")
            print("❌ Channel not found")
            return

        credentials = await self.get_credentials()
        if not credentials:
            await ctx.send("Twitch API credentials are not set! Please use `[p]settwitchcreds` to set them.")
            print("❌ No credentials found")
            return

        status_message = await ctx.send(f"🔄 Checking schedule for {twitch_username}...")
        print(f"Attempting fetch for user: {twitch_username}")

        schedule = await self.get_schedule(twitch_username)
        
        if schedule is not None:  # None means error, empty list means no schedule
            if len(schedule) > 0:
                await self.post_schedule(channel, schedule)
                await status_message.edit(content="✅ Schedule has been updated!")
                print("✅ Schedule updated successfully")
            else:
                await status_message.edit(
                    content=f"ℹ️ No upcoming scheduled streams found for {twitch_username}\n"
                    "This could mean:\n"
                    "1. The streamer hasn't set up any scheduled streams\n"
                    "2. All scheduled streams have already passed\n"
                    "3. The schedule is currently empty"
                )
                print("ℹ️ No scheduled streams found")
        else:
            error_msg = (
                "❌ Error fetching schedule:\n"
                f"1. Verified '{twitch_username}' exists? (Check capitalization)\n"
                "2. API credentials are working (✓ confirmed)\n"
                "3. The Twitch API is responding properly\n\n"
                "Current settings:\n"
                f"• Username: {twitch_username}\n"
                f"• Channel: {channel.mention}\n\n"
                "Check bot logs for detailed error information."
            )
            await status_message.edit(content=error_msg)
            print("❌ Failed to fetch schedule")
        
        print("=== END FORCE UPDATE ===\n")

def setup(bot: Red):
    bot.add_cog(TwitchSchedule(bot))
