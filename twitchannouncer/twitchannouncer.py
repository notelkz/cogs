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
            "token_expires": None
        }
        self.config.register_guild(**default_guild)
        self.check_streams_task = self.bot.loop.create_task(self.check_streams_loop())
        self.headers = None

    def cog_unload(self):
        if self.check_streams_task:
            self.check_streams_task.cancel()

    async def get_twitch_headers(self, guild):
        """Get valid Twitch API headers."""
        now = datetime.utcnow().timestamp()
        token_expires = await self.config.guild(guild).token_expires()
        access_token = await self.config.guild(guild).access_token()

        if not token_expires or not access_token or now >= token_expires:
            # Need new token
            client_id = await self.config.guild(guild).client_id()
            client_secret = await self.config.guild(guild).client_secret()
            
            if not client_id or not client_secret:
                return None

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
                        return None
                    data = await resp.json()
                    access_token = data["access_token"]
                    expires_in = data["expires_in"]
                    
                    await self.config.guild(guild).access_token.set(access_token)
                    await self.config.guild(guild).token_expires.set(now + expires_in)

        return {
            "Client-ID": await self.config.guild(guild).client_id(),
            "Authorization": f"Bearer {access_token}"
        }

    async def check_streams_loop(self):
        """Loop to check if streamers are live."""
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild in self.bot.guilds:
                    await self.check_guild_streams(guild)
                await asyncio.sleep(300)  # Check every 5 minutes
            except Exception as e:
                print(f"Error in stream check loop: {e}")
                await asyncio.sleep(300)

    async def check_guild_streams(self, guild):
        """Check streams for a specific guild."""
        streamers = await self.config.guild(guild).streamers()
        if not streamers:
            return

        headers = await self.get_twitch_headers(guild)
        if not headers:
            return

        async with aiohttp.ClientSession() as session:
            for twitch_name in streamers:
                async with session.get(
                    f"https://api.twitch.tv/helix/streams?user_login={twitch_name}",
                    headers=headers
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    
                    is_live = bool(data["data"])
                    last_announced = streamers[twitch_name].get("last_announced", 0)
                    
                    if is_live and data["data"][0]["started_at"] != last_announced:
                        await self.announce_stream(guild, twitch_name, data["data"][0])
                        streamers[twitch_name]["last_announced"] = data["data"][0]["started_at"]
                        await self.config.guild(guild).streamers.set(streamers)

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
            value=stream_data["game_name"],
            inline=True
        )
        
        embed.add_field(
            name="Viewers",
            value=str(stream_data["viewer_count"]),
            inline=True
        )

        if stream_data.get("thumbnail_url"):
            thumbnail = stream_data["thumbnail_url"]
            thumbnail = thumbnail.replace("{width}", "1280").replace("{height}", "720")
            embed.set_image(url=thumbnail)

        view = StreamView(twitch_name)
        
        if role_mentions:
            await channel.send(role_mentions, embed=embed, view=view)
        else:
            await channel.send(embed=embed, view=view)

    @commands.group(aliases=["tann"])
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def twitchannouncer(self, ctx):
        """Twitch announcer settings."""
        pass

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
    async def set_twitch_auth(self, ctx, client_id: str, client_secret: str):
        """Set Twitch API authentication (Only use in DMs!)."""
        if ctx.guild:
            await ctx.send("Please use this command in DMs for security!")
            return
            
        await self.config.guild(ctx.guild).client_id.set(client_id)
        await self.config.guild(ctx.guild).client_secret.set(client_secret)
        await ctx.send("Twitch API authentication set!")

    @twitchannouncer.command(name="test")
    async def test_announcement(self, ctx, twitch_name: str):
        """Test stream announcement for a specific streamer."""
        headers = await self.get_twitch_headers(ctx.guild)
        if not headers:
            await ctx.send("Twitch API authentication not set up!")
            return

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://api.twitch.tv/helix/streams?user_login={twitch_name}",
                headers=headers
            ) as resp:
                if resp.status != 200:
                    await ctx.send("Failed to fetch stream data.")
                    return
                    
                data = await resp.json()
                if not data["data"]:
                    await ctx.send(f"{twitch_name} is not live. Creating test announcement anyway...")
                    # Create fake stream data for testing
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
        
        # Add buttons
        self.add_item(discord.ui.Button(
            label="Watch Stream",
            url=f"https://twitch.tv/{twitch_name}",
            style=discord.ButtonStyle.url
        ))
        self.add_item(discord.ui.Button(
            label="Subscribe",
            url=f"https://twitch.tv/{twitch_name}/subscribe",
            style=discord.ButtonStyle.url
        ))

async def setup(bot):
    await bot.add_cog(TwitchAnnouncer(bot))
