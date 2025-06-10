import discord
from redbot.core import commands, Config, checks
import aiohttp
import asyncio
from datetime import datetime
from typing import Optional

class TwitchAnnouncer(commands.Cog):
    """Announce when specific users go live on Twitch."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "announcement_channel": None,
            "ping_roles": [],
            "streamers": {},  # {twitch_name: {"discord_id": id, "last_announced": timestamp}}
            "client_id": None,
            "client_secret": None,
            "access_token": None,
            "token_expires": None,
            "check_frequency": 300  # Default 5 minutes
        }
        self.config.register_guild(**default_guild)
        self.check_streams_task = self.bot.loop.create_task(self.check_streams_loop())
        self.headers = None
        self.rate_limiter = commands.CooldownMapping.from_cooldown(
            1, 1, commands.BucketType.guild
        )  # 1 request per second per guild

    def cog_unload(self):
        if self.check_streams_task:
            self.check_streams_task.cancel()

    async def get_twitch_headers(self, guild):
        """Get valid Twitch API headers."""
        try:
            now = datetime.utcnow().timestamp()
            token_expires = await self.config.guild(guild).token_expires()
            access_token = await self.config.guild(guild).access_token()
            client_id = await self.config.guild(guild).client_id()
            client_secret = await self.config.guild(guild).client_secret()

            if not client_id or not client_secret:
                print(f"[DEBUG] Missing client ID or secret for guild {guild.id}")
                return None

            # Always get a new token if we don't have one or if it's expired
            if not token_expires or not access_token or now >= token_expires:
                print(f"[DEBUG] Getting new token for guild {guild.id}")
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        "https://id.twitch.tv/oauth2/token",
                        params={
                            "client_id": client_id,
                            "client_secret": client_secret,
                            "grant_type": "client_credentials"
                        }
                    ) as resp:
                        if resp.status != 200:
                            error_text = await resp.text()
                            print(f"[DEBUG] Token request failed: Status {resp.status}, Response: {error_text}")
                            return None
                        
                        data = await resp.json()
                        access_token = data["access_token"]
                        expires_in = data["expires_in"]
                        
                        # Store the new token
                        await self.config.guild(guild).access_token.set(access_token)
                        await self.config.guild(guild).token_expires.set(now + expires_in)
                        print(f"[DEBUG] New token obtained for guild {guild.id}")

            headers = {
                "Client-ID": client_id,
                "Authorization": f"Bearer {access_token}"
            }
            
            print(f"[DEBUG] Returning headers for guild {guild.id}: {headers}")
            return headers

        except Exception as e:
            print(f"[DEBUG] Error in get_twitch_headers: {str(e)}")
            return None

    async def check_streams_loop(self):
        """Loop to check if streamers are live."""
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild in self.bot.guilds:
                    await self.check_guild_streams(guild)
                
                # Sleep after checking all guilds
                check_frequency = await self.config.guild(guild).check_frequency()
                await asyncio.sleep(check_frequency)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                print(f"Network error in stream check loop: {e}")
                await asyncio.sleep(60)
            except Exception as e:
                print(f"Unexpected error in stream check loop: {e}")
                await asyncio.sleep(60)

    async def check_guild_streams(self, guild):
        """Check streams for a specific guild."""
        try:
            streamers = await self.config.guild(guild).streamers()
            if not streamers:
                return

            headers = await self.get_twitch_headers(guild)
            if not headers:
                print(f"[DEBUG] No valid headers for guild {guild.id}")
                return

            # Apply rate limiting
            bucket = self.rate_limiter.get_bucket(discord.Object(id=guild.id))
            retry_after = bucket.update_rate_limit()
            if retry_after:
                await asyncio.sleep(retry_after)

            async with aiohttp.ClientSession() as session:
                for twitch_name in streamers:
                    try:
                        url = f"https://api.twitch.tv/helix/streams?user_login={twitch_name}"
                        print(f"[DEBUG] Requesting: {url}")
                        print(f"[DEBUG] Headers: {headers}")
                        
                        async with session.get(url, headers=headers) as resp:
                            text = await resp.text()
                            print(f"[DEBUG] Response status: {resp.status}")
                            print(f"[DEBUG] Response text: {text}")
                            
                            if resp.status == 401:
                                print("[DEBUG] Got 401 - Clearing token and retrying")
                                # Clear the token so it will be refreshed next time
                                await self.config.guild(guild).access_token.set(None)
                                await self.config.guild(guild).token_expires.set(None)
                                return
                                
                            if resp.status != 200:
                                print(f"[DEBUG] Twitch API error for {twitch_name}: {resp.status}")
                                continue
                                
                            data = await resp.json()
                            
                            is_live = bool(data["data"])
                            last_announced = streamers[twitch_name].get("last_announced", 0)
                            
                            if is_live and data["data"][0]["started_at"] != last_announced:
                                await self.announce_stream(guild, twitch_name, data["data"][0])
                                streamers[twitch_name]["last_announced"] = data["data"][0]["started_at"]
                                await self.config.guild(guild).streamers.set(streamers)
                                
                    except Exception as e:
                        print(f"[DEBUG] Error checking stream {twitch_name}: {str(e)}")
                        continue
        except Exception as e:
            print(f"[DEBUG] Error in check_guild_streams: {str(e)}")
    
    async def announce_stream(self, guild, twitch_name, stream_data):
        """Announce a live stream."""
        channel_id = await self.config.guild(guild).announcement_channel()
        if not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            return

        # Get roles to ping
        ping_roles = await self.config.guild(guild).ping_roles()
        role_mentions = " ".join(f"<@&{role_id}>" for role_id in ping_roles)
        
        # Debug logging for role mentions
        print(f"[DEBUG] Announcing stream for {twitch_name}")
        print(f"[DEBUG] Ping roles: {ping_roles}")
        print(f"[DEBUG] Role mentions string: '{role_mentions}'")

        embed = discord.Embed(
            title=stream_data["title"],
            url=f"https://twitch.tv/{twitch_name}",
            color=discord.Color.purple(),
            timestamp=datetime.now()
        )
        
        embed.set_author(
            name=f"{twitch_name} is now live on Twitch!",
            icon_url="https://static.twitchcdn.net/assets/favicon-32-d6025c14e900565d6177.png"
        )
        
        embed.add_field(
            name="Playing",
            value=stream_data.get("game_name", "Unknown"),
            inline=True
        )
        
        embed.add_field(
            name="Viewers",
            value=str(stream_data.get("viewer_count", 0)),
            inline=True
        )

        if stream_data.get("thumbnail_url"):
            try:
                thumbnail = stream_data["thumbnail_url"]
                timestamp = int(datetime.now().timestamp())
                
                resolutions = [
                    ("1280", "720"),
                    ("640", "360"),
                    ("480", "270")
                ]
                
                thumbnail_set = False
                for width, height in resolutions:
                    try:
                        current_thumbnail = thumbnail.replace("{width}", width).replace("{height}", height)
                        current_thumbnail = f"{current_thumbnail}?t={timestamp}"
                        
                        async with aiohttp.ClientSession() as session:
                            async with session.head(current_thumbnail) as resp:
                                if resp.status == 200:
                                    embed.set_image(url=current_thumbnail)
                                    thumbnail_set = True
                                    break
                    except:
                        continue
                
                if not thumbnail_set:
                    preview_url = f"https://static-cdn.jtvnw.net/previews-ttv/live_user_{twitch_name}-1280x720.jpg"
                    embed.set_image(url=f"{preview_url}?t={timestamp}")
                    
            except Exception as e:
                print(f"Error setting stream thumbnail: {e}")
                preview_url = f"https://static-cdn.jtvnw.net/previews-ttv/live_user_{twitch_name}-1280x720.jpg"
                embed.set_image(url=f"{preview_url}?t={timestamp}")

        view = StreamView(twitch_name)
        
        try:
            if role_mentions:
                await channel.send(role_mentions, embed=embed, view=view)
            else:
                await channel.send(embed=embed, view=view)
        except discord.HTTPException as e:
            print(f"Error sending announcement: {e}")

    @commands.group(aliases=["tann"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def twitchannouncer(self, ctx):
        """Twitch announcer settings."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @twitchannouncer.command(name="setup")
    async def setup_announcer(self, ctx, channel: discord.TextChannel):
        """Set up the Twitch announcer."""
        await self.config.guild(ctx.guild).announcement_channel.set(channel.id)
        await ctx.send(f"Announcement channel set to {channel.mention}")

    @twitchannouncer.command(name="addstreamer")
    async def add_streamer(self, ctx, twitch_name: str, discord_member: Optional[discord.Member] = None):
        """Add a Twitch streamer to announce."""
        async with self.config.guild(ctx.guild).streamers() as streamers:
            streamers[twitch_name.lower()] = {
                "discord_id": discord_member.id if discord_member else None,
                "last_announced": None
            }
        await ctx.send(f"Added {twitch_name} to announcement list.")

    @twitchannouncer.command(name="removestreamer")
    async def remove_streamer(self, ctx, twitch_name: str):
        """Remove a Twitch streamer from announcements."""
        async with self.config.guild(ctx.guild).streamers() as streamers:
            if twitch_name.lower() in streamers:
                del streamers[twitch_name.lower()]
                await ctx.send(f"Removed {twitch_name} from announcement list.")
            else:
                await ctx.send("Streamer not found in list.")

    @twitchannouncer.command(name="liststreamers")
    async def list_streamers(self, ctx):
        """List all tracked streamers."""
        streamers = await self.config.guild(ctx.guild).streamers()
        if not streamers:
            await ctx.send("No streamers in list.")
            return

        msg = "**Tracked Streamers:**\n"
        for twitch_name, data in streamers.items():
            discord_id = data.get("discord_id")
            if discord_id:
                member = ctx.guild.get_member(discord_id)
                msg += f"- {twitch_name} ({member.mention if member else 'Unknown member'})\n"
            else:
                msg += f"- {twitch_name}\n"
        
        await ctx.send(msg)

    @twitchannouncer.command(name="listroles")
    async def list_ping_roles(self, ctx):
        """List all roles that will be pinged for stream announcements."""
        ping_roles = await self.config.guild(ctx.guild).ping_roles()
        if not ping_roles:
            await ctx.send("No roles configured for pinging.")
            return
            
        msg = "**Roles that will be pinged:**\n"
        for role_id in ping_roles:
            role = ctx.guild.get_role(role_id)
            if role:
                msg += f"- {role.name} (ID: {role_id})\n"
            else:
                msg += f"- Unknown role (ID: {role_id})\n"
        
        await ctx.send(msg)

    @twitchannouncer.command(name="setfrequency")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def set_check_frequency(self, ctx, seconds: int):
        """Set how frequently to check for live streams (in seconds)."""
        if seconds < 30:
            await ctx.send("❌ Check frequency cannot be less than 30 seconds to avoid API rate limits.")
            return
            
        streamer_count = len(await self.config.guild(ctx.guild).streamers())
        requests_per_minute = (60 / seconds) * streamer_count
        
        if requests_per_minute > 50:
            await ctx.send(f"⚠️ Warning: With {streamer_count} streamers, checking every {seconds} seconds "
                          f"will make approximately {requests_per_minute:.1f} requests per minute to the Twitch API. "
                          "This might cause rate limit issues.")
        
        await self.config.guild(ctx.guild).check_frequency.set(seconds)
        await ctx.send(f"✅ Stream check frequency set to {seconds} seconds.")

    @twitchannouncer.command(name="showfrequency")
    @commands.guild_only()
    async def show_check_frequency(self, ctx):
        """Show the current check frequency for live streams."""
        frequency = await self.config.guild(ctx.guild).check_frequency()
        streamer_count = len(await self.config.guild(ctx.guild).streamers())
        requests_per_minute = (60 / frequency) * streamer_count
        
        embed = discord.Embed(
            title="Twitch Announcer Settings",
            color=discord.Color.purple()
        )
        embed.add_field(name="Check Frequency", value=f"{frequency} seconds", inline=True)
        embed.add_field(name="Tracked Streamers", value=str(streamer_count), inline=True)
        embed.add_field(name="Requests per Minute", value=f"{requests_per_minute:.1f}", inline=True)
        
        await ctx.send(embed=embed)

    @twitchannouncer.command(name="addrole")
    async def add_ping_role(self, ctx, role: discord.Role):
        """Add a role to ping for stream announcements."""
        async with self.config.guild(ctx.guild).ping_roles() as roles:
            if role.id not in roles:
                roles.append(role.id)
        await ctx.send(f"Added {role.name} to announcement pings.")

    @twitchannouncer.command(name="removerole")
    async def remove_ping_role(self, ctx, role: discord.Role):
        """Remove a role from stream announcements."""
        async with self.config.guild(ctx.guild).ping_roles() as roles:
            if role.id in roles:
                roles.remove(role.id)
        await ctx.send(f"Removed {role.name} from announcement pings.")

    @twitchannouncer.command(name="setauth")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def set_twitch_auth(self, ctx):
        """Set Twitch API authentication."""
        await ctx.send("Please check your DMs for the setup process.")
        
        def check(m):
            return m.author == ctx.author and m.channel.type == discord.ChannelType.private

        try:
            await ctx.author.send("Please enter your Twitch Client ID:")
            client_id_msg = await self.bot.wait_for('message', check=check, timeout=60)
            
            await ctx.author.send("Please enter your Twitch Client Secret:")
            client_secret_msg = await self.bot.wait_for('message', check=check, timeout=60)

            client_id = client_id_msg.content
            client_secret = client_secret_msg.content

            # Test the credentials
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://id.twitch.tv/oauth2/token",
                    params={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "grant_type": "client_credentials"
                    }
                ) as resp:
                    if resp.status != 200:
                        await ctx.author.send("❌ Invalid credentials! Please check your Client ID and Secret.")
                        return
                    
                    data = await resp.json()
                    access_token = data["access_token"]
                    expires_in = data["expires_in"]
                    now = datetime.utcnow().timestamp()
                    
                    await self.config.guild(ctx.guild).client_id.set(client_id)
                    await self.config.guild(ctx.guild).client_secret.set(client_secret)
                    await self.config.guild(ctx.guild).access_token.set(access_token)
                    await self.config.guild(ctx.guild).token_expires.set(now + expires_in)
                    
                    await ctx.author.send("✅ Twitch API authentication successfully set and verified!")
                    if ctx.channel.type != discord.ChannelType.private:
                        await ctx.send("✅ Twitch API authentication has been set up via DM.")

        except asyncio.TimeoutError:
            await ctx.author.send("Setup timed out. Please try again.")
        except discord.Forbidden:
            await ctx.send("I couldn't send you a DM. Please enable DMs and try again.")
        except Exception as e:
            await ctx.author.send(f"An error occurred: {str(e)}")

    @twitchannouncer.command(name="checkauth")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def check_auth(self, ctx):
        """Check the status of Twitch API authentication."""
        client_id = await self.config.guild(ctx.guild).client_id()
        client_secret = await self.config.guild(ctx.guild).client_secret()
        access_token = await self.config.guild(ctx.guild).access_token()
        token_expires = await self.config.guild(ctx.guild).token_expires()
        
        if not client_id or not client_secret:
            await ctx.send("❌ Client ID or Client Secret not set. Please use `setauth` to configure them.")
            return

        # If we don't have a token, try to get one
        if not access_token:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        "https://id.twitch.tv/oauth2/token",
                        params={
                            "client_id": client_id,
                            "client_secret": client_secret,
                            "grant_type": "client_credentials"
                        }
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            access_token = data["access_token"]
                            expires_in = data["expires_in"]
                            now = datetime.utcnow().timestamp()
                            
                            await self.config.guild(ctx.guild).access_token.set(access_token)
                            await self.config.guild(ctx.guild).token_expires.set(now + expires_in)
                            
                            token_expires = now + expires_in
                            await ctx.send("✅ Successfully generated new access token!")
                        else:
                            error_text = await resp.text()
                            await ctx.send(f"❌ Failed to generate token. Status: {resp.status}, Error: {error_text}")
            except Exception as e:
                await ctx.send(f"❌ Error generating token: {str(e)}")
            
        now = datetime.utcnow().timestamp()
        
        embed = discord.Embed(
            title="Twitch API Authentication Status",
            color=discord.Color.purple()
        )
        
        embed.add_field(
            name="Client ID",
            value="✅ Set" if client_id else "❌ Not Set",
            inline=True
        )
        
        embed.add_field(
            name="Client Secret",
            value="✅ Set" if client_secret else "❌ Not Set",
            inline=True
        )
        
        embed.add_field(
            name="Access Token",
            value="✅ Set" if access_token else "❌ Not Set",
            inline=True
        )
        
        if token_expires:
            if now >= token_expires:
                status = "❌ Expired"
            else:
                remaining = int(token_expires - now)
                status = f"✅ Valid for {remaining} seconds"
        else:
            status = "❌ Not Set"
            
        embed.add_field(
            name="Token Status",
            value=status,
            inline=False
        )

        # Test the current token if we have one
        if access_token:
            try:
                headers = {
                    "Client-ID": client_id,
                    "Authorization": f"Bearer {access_token}"
                }
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        "https://api.twitch.tv/helix/users",
                        headers=headers
                    ) as resp:
                        if resp.status == 200:
                            embed.add_field(
                                name="Token Test",
                                value="✅ Token working correctly",
                                inline=False
                            )
                        else:
                            error_text = await resp.text()
                            embed.add_field(
                                name="Token Test",
                                value=f"❌ Token not working (Status {resp.status}): {error_text}",
                                inline=False
                            )
            except Exception as e:
                embed.add_field(
                    name="Token Test",
                    value=f"❌ Error testing token: {str(e)}",
                    inline=False
                )
        
        await ctx.send(embed=embed)

    @twitchannouncer.command(name="test")
    async def test_announcement(self, ctx, twitch_name: str):
        """Test stream announcement for a specific streamer."""
        headers = await self.get_twitch_headers(ctx.guild)
        if not headers:
            await ctx.send("Twitch API authentication not set up!")
            return

        async with aiohttp.ClientSession() as session:
            url = f"https://api.twitch.tv/helix/streams?user_login={twitch_name}"
            async with session.get(url, headers=headers) as resp:
                text = await resp.text()
                print(f"[DEBUG] Twitch API status: {resp.status}")
                print(f"[DEBUG] Twitch API response: {text}")
                if resp.status != 200:
                    await ctx.send(f"Failed to fetch stream data. Twitch API returned {resp.status}: {text}")
                    return
                    
                data = await resp.json()
                if not data["data"]:
                    await ctx.send(f"{twitch_name} is not live. Creating test announcement anyway...")
                    test_data = {
                        "title": "Test Stream",
                        "game_name": "Just Chatting",
                        "viewer_count": 0,
                        "started_at": datetime.utcnow().isoformat(),
                        "thumbnail_url": None
                    }
                    await self.announce_stream(ctx.guild, twitch_name, test_data)
                else:
                    await self.announce_stream(ctx.guild, twitch_name, data["data"][0])

class StreamView(discord.ui.View):
    def __init__(self, twitch_name):
        super().__init__(timeout=None)
        self.twitch_name = twitch_name
        
        self.watch_button = discord.ui.Button(
            label="Watch Stream",
            url=f"https://twitch.tv/{twitch_name}",
            style=discord.ButtonStyle.url
        )
        self.subscribe_button = discord.ui.Button(
            label="Subscribe",
            url=f"https://twitch.tv/{twitch_name}/subscribe",
            style=discord.ButtonStyle.url
        )
        self.add_item(self.watch_button)
        self.add_item(self.subscribe_button)


async def setup(bot):
    await bot.add_cog(TwitchAnnouncer(bot))
