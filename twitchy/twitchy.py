import asyncio
import aiohttp
import discord
from redbot.core import commands, Config, data_manager
from redbot.core.utils.chat_formatting import humanize_list, pagify
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import MessagePredicate, ReactionPredicate
import time
import datetime
from datetime import timedelta
import io
import os
import pytz
import re
import dateutil.parser
import traceback
from PIL import Image, ImageDraw, ImageFont

# Define a custom view for the buttons
class StreamButtons(discord.ui.View):
    def __init__(self, watch_url: str, subscribe_url: str, timeout=180):
        super().__init__(timeout=timeout)
        self.add_item(discord.ui.Button(label="Watch Now", style=discord.ButtonStyle.link, url=watch_url))
        self.add_item(discord.ui.Button(label="Subscribe", style=discord.ButtonStyle.link, url=subscribe_url))

class Twitchy(commands.Cog):
    """
    Automatically announces when Twitch streams go live and manages 'Live' roles
    based on Discord activity and provides Twitch schedule functionality.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)

        # Default global settings
        self.config.register_global(
            twitch_client_id=None,
            twitch_client_secret=None,
            twitch_redirect_uri="http://localhost:8000/callback",
            twitch_access_token_info=None,
            custom_template_url=None,
            custom_font_url=None
        )

        # Default guild settings (for each Discord server)
        self.config.register_guild(
            live_role_id=None,
            streamers=[], # Stores list of streamer usernames
            streamer_status_data={}, # NEW: Stores dictionary of {"username": {"live_status": True, ...}}
            announcement_channel_id=None,
            schedule_channel_id=None,
            schedule_ping_role_id=None
        )

        self.session = aiohttp.ClientSession()
        self.loop = bot.loop
        self.live_check_task = None
        self.refresh_token_task = None
        self.schedule_update_task = None

        # Paths for schedule resources
        self.template_path = data_manager.cog_data_path(self) / "schedule_template.png"
        self.font_path = data_manager.cog_data_path(self) / "schedule_font.ttf"

        # **IMPORTANT: UPDATED DEFAULT FONT URL**
        self.default_template_url = "https://raw.githubusercontent.com/Twitchy-Cog/Twitchy/main/default_schedule_template.png"
        self.default_font_url = "https://raw.githubusercontent.com/googlefonts/robotoslab/main/RobotoSlab-Regular.ttf" # Updated URL

        self.template_image = None
        self.font = None

        self.init_tasks()
        self.ensure_schedule_resources_task = self.loop.create_task(self.ensure_schedule_resources())

    def cog_unload(self):
        if self.live_check_task:
            self.live_check_task.cancel()
        if self.refresh_token_task:
            self.refresh_token_task.cancel()
        if self.schedule_update_task:
            self.schedule_update_task.cancel()
        self.loop.create_task(self.session.close())

    def init_tasks(self):
        self.live_check_task = self.loop.create_task(self.check_live_status_loop())
        self.refresh_token_task = self.loop.create_task(self.refresh_twitch_token_loop())
        self.schedule_update_task = self.loop.create_task(self.update_all_guild_schedules_loop())

    async def _download_file(self, url, path):
        try:
            async with self.session.get(url) as response:
                response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
                with open(path, 'wb') as f:
                    f.write(await response.read())
                return True
        except aiohttp.ClientError as e:
            print(f"Twitchy Cog: Failed to download file from {url}: {e}")
            return False
        except Exception as e:
            print(f"Twitchy Cog: An unexpected error occurred downloading {url}: {e}")
            traceback.print_exc()
            return False

    async def ensure_schedule_resources(self):
        custom_template_url = await self.config.custom_template_url()
        custom_font_url = await self.config.custom_font_url()

        template_downloaded = False
        font_downloaded = False

        # Try downloading custom template first
        if custom_template_url and not self.template_path.exists():
            print(f"Twitchy Cog: Attempting to download custom template from {custom_template_url}")
            template_downloaded = await self._download_file(custom_template_url, self.template_path)
            if template_downloaded:
                print("Twitchy Cog: Custom schedule template downloaded.")
            else:
                print("Twitchy Cog: Failed to download custom template.")

        # If custom template failed or not set, download default
        if not template_downloaded and not self.template_path.exists():
            print(f"Twitchy Cog: Downloading default schedule template from {self.default_template_url}")
            template_downloaded = await self._download_file(self.default_template_url, self.template_path)
            if template_downloaded:
                print("Twitchy Cog: Default schedule template downloaded.")
            else:
                print("Twitchy Cog: Failed to download default schedule template. Creating a placeholder.")
                # Create a simple placeholder image if download fails
                try:
                    img = Image.new('RGB', (1000, 500), color = (73, 109, 137))
                    d = ImageDraw.Draw(img)
                    d.text((10,10), "Schedule template missing! Check bot console.", fill=(255,255,255))
                    img.save(self.template_path)
                    print("Twitchy Cog: Created a placeholder schedule template image.")
                    template_downloaded = True
                except Exception as e:
                    print(f"Twitchy Cog: Could not create placeholder image: {e}")

        # Try downloading custom font first
        if custom_font_url and not self.font_path.exists():
            print(f"Twitchy Cog: Attempting to download custom font from {custom_font_url}")
            font_downloaded = await self._download_file(custom_font_url, self.font_path)
            if font_downloaded:
                print("Twitchy Cog: Custom font downloaded.")
            else:
                print("Twitchy Cog: Failed to download custom font.")

        # If custom font failed or not set, download default
        if not font_downloaded and not self.font_path.exists():
            print(f"Twitchy Cog: Downloading default Roboto-Regular.ttf font from {self.default_font_url}")
            font_downloaded = await self._download_file(self.default_font_url, self.font_path)
            if font_downloaded:
                print("Twitchy Cog: Default font downloaded.")
            else:
                print("Twitchy Cog: Failed to download default font.")

        # Load resources into PIL
        try:
            if self.template_path.exists():
                self.template_image = Image.open(self.template_path).convert("RGBA")
            if self.font_path.exists():
                self.font = ImageFont.truetype(str(self.font_path), 24) # Adjust size as needed
        except Exception as e:
            print(f"Twitchy Cog: Error loading schedule resources: {e}")
            self.template_image = None
            self.font = None
            return False

        return self.template_image is not None and self.font is not None

    @commands.group()
    async def twitchy(self, ctx):
        """Manage Twitchy Cog settings."""
        pass

    @twitchy.command(name="setcreds")
    @commands.is_owner()
    async def twitchy_set_credentials(self, ctx, client_id: str, client_secret: str, redirect_uri: str = "http://localhost:8000/callback"):
        """
        Set your Twitch API Client ID, Client Secret, and Redirect URI.
        The Redirect URI defaults to http://localhost:8000/callback if not provided.
        """
        await self.config.client_id.set(client_id)
        await self.config.client_secret.set(client_secret)
        await self.config.twitch_redirect_uri.set(redirect_uri)
        await self.config.twitch_access_token_info.set(None) # Clear existing token info
        await ctx.send("Twitch API credentials set. Please authorize the bot using `[p]twitchy authorize`.")
        self.refresh_token_task.cancel() # Cancel existing task
        self.refresh_token_task = self.loop.create_task(self.refresh_twitch_token_loop()) # Start new task

    @twitchy.command(name="authorize")
    @commands.is_owner()
    async def twitchy_authorize(self, ctx):
        """
        Generates the authorization URL for Twitch.
        After authorization, you will receive a code to complete the process.
        """
        client_id = await self.config.client_id()
        redirect_uri = await self.config.twitch_redirect_uri()

        if not client_id or not redirect_uri:
            await ctx.send("Please set your Twitch API credentials first using `[p]twitchy setcreds <client_id> <client_secret> [redirect_uri]`.")
            return

        scope = "user:read:follows user:read:subscriptions channel:read:subscriptions" # Minimal scope for now
        auth_url = f"https://id.twitch.tv/oauth2/authorize?client_id={client_id}&redirect_uri={redirect_uri}&response_type=code&scope={scope}"
        await ctx.send(f"Please visit this URL to authorize the bot: {auth_url}\n"
                       f"After authorization, you will be redirected to your specified Redirect URI with a `code` in the URL. "
                       f"Use that code with `[p]twitchy setcode <your_code>`.")

    @twitchy.command(name="setcode")
    @commands.is_owner()
    async def twitchy_set_code(self, ctx, code: str):
        """
        Completes the Twitch authorization process with the received code.
        """
        client_id = await self.config.client_id()
        client_secret = await self.config.twitch_client_secret()
        redirect_uri = await self.config.twitch_redirect_uri()

        if not client_id or not client_secret or not redirect_uri:
            await ctx.send("Please set your Twitch API credentials first using `[p]twitchy setcreds`.")
            return

        token_url = "https://id.twitch.tv/oauth2/token"
        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri
        }

        try:
            async with self.session.post(token_url, data=payload) as response:
                response.raise_for_status()
                token_info = await response.json()
                await self.config.twitch_access_token_info.set(token_info)
                await ctx.send("✅ Twitch authorization successful!")
                self.refresh_token_task.cancel() # Cancel old task
                self.refresh_token_task = self.loop.create_task(self.refresh_twitch_token_loop()) # Start new task
        except aiohttp.ClientError as e:
            await ctx.send(f"❌ Failed to get Twitch access token: {e}")
        except Exception as e:
            await ctx.send(f"❌ An unexpected error occurred: {e}")
            traceback.print_exc()

    async def get_twitch_headers(self):
        token_info = await self.config.twitch_access_token_info()
        client_id = await self.config.client_id()

        if not token_info or "access_token" not in token_info:
            print("Twitchy Cog: No access token available. Attempting to refresh.")
            await self.refresh_twitch_token() # Attempt to refresh immediately
            token_info = await self.config.twitch_access_token_info() # Get updated info

        if not token_info or "access_token" not in token_info:
            print("Twitchy Cog: Failed to obtain valid access token for Twitch API calls.")
            return None

        return {
            "Client-ID": client_id,
            "Authorization": f"Bearer {token_info['access_token']}"
        }

    async def refresh_twitch_token(self):
        token_info = await self.config.twitch_access_token_info()
        client_id = await self.config.client_id()
        client_secret = await self.config.twitch_client_secret()

        if not token_info or "refresh_token" not in token_info:
            print("Twitchy Cog: No refresh token available to refresh Twitch access token.")
            return False

        refresh_url = "https://id.twitch.tv/oauth2/token"
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": token_info["refresh_token"],
            "client_id": client_id,
            "client_secret": client_secret
        }

        try:
            async with self.session.post(refresh_url, data=payload) as response:
                response.raise_for_status()
                new_token_info = await response.json()
                new_token_info["retrieved_at"] = time.time() # Store retrieval time for expiration
                await self.config.twitch_access_token_info.set(new_token_info)
                print("Twitchy Cog: Twitch access token refreshed successfully.")
                return True
        except aiohttp.ClientError as e:
            print(f"Twitchy Cog: Failed to refresh Twitch access token: {e}")
            # Optionally clear the token info if refresh fails consistently
            await self.config.twitch_access_token_info.set(None)
            return False
        except Exception as e:
            print(f"Twitchy Cog: An unexpected error occurred during token refresh: {e}")
            traceback.print_exc()
            return False

    async def refresh_twitch_token_loop(self):
        await self.bot.wait_until_ready()
        while True:
            token_info = await self.config.twitch_access_token_info()
            if token_info and "expires_in" in token_info and "retrieved_at" in token_info:
                # Refresh 10 minutes before expiration
                expires_at = token_info["retrieved_at"] + token_info["expires_in"]
                time_to_sleep = max(0, expires_at - time.time() - 600) # 600 seconds = 10 minutes
                print(f"Twitchy Cog: Next token refresh in {time_to_sleep / 60:.2f} minutes.")
                await asyncio.sleep(time_to_sleep)
            else:
                # If no token info or incomplete, try refreshing every hour
                print("Twitchy Cog: No full token info for scheduled refresh. Retrying in 1 hour.")
                await asyncio.sleep(3600) # 1 hour

            await self.refresh_twitch_token()

    @twitchy.command(name="addstreamer")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def twitchy_add_streamer(self, ctx, twitch_username: str):
        """Add a Twitch streamer to monitor."""
        async with self.config.guild(ctx.guild).streamers() as streamers:
            if twitch_username.lower() in [s.lower() for s in streamers]:
                await ctx.send(f"{twitch_username} is already being monitored.")
                return

            # Optional: Verify streamer exists on Twitch
            headers = await self.get_twitch_headers()
            if not headers:
                await ctx.send("Cannot verify streamer without Twitch API authorization. Please authorize the bot first.")
                streamers.append(twitch_username) # Add anyway, but warn
                await ctx.send(f"Added {twitch_username}, but could not verify. Authorization needed for live checks.")
                return

            try:
                # Use the new Twitch API endpoint for users
                users_url = f"https://api.twitch.tv/helix/users?login={twitch_username}"
                async with self.session.get(users_url, headers=headers) as response:
                    response.raise_for_status()
                    data = await response.json()
                    if not data["data"]:
                        await ctx.send(f"Could not find Twitch user `{twitch_username}`. Please check the spelling.")
                        return
                    else:
                        streamers.append(twitch_username)
                        await ctx.send(f"✅ Now monitoring {twitch_username}'s Twitch stream.")
            except aiohttp.ClientResponseError as e:
                await ctx.send(f"❌ Twitch API error when verifying streamer: {e.status} - {e.message}. Check your credentials or try again later.")
            except Exception as e:
                await ctx.send(f"❌ An unexpected error occurred: {e}")
                traceback.print_exc()

    @twitchy.command(name="removestreamer")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def twitchy_remove_streamer(self, ctx, twitch_username: str):
        """Remove a Twitch streamer from monitoring."""
        async with self.config.guild(ctx.guild).streamers() as streamers:
            if twitch_username.lower() not in [s.lower() for s in streamers]:
                await ctx.send(f"{twitch_username} is not currently being monitored.")
                return
            
            # Case-insensitive removal
            streamers[:] = [s for s in streamers if s.lower() != twitch_username.lower()]
            await ctx.send(f"✅ Stopped monitoring {twitch_username}'s Twitch stream.")
        
        # Also remove their status data
        async with self.config.guild(ctx.guild).streamer_status_data() as streamer_status_data:
            if twitch_username.lower() in streamer_status_data:
                del streamer_status_data[twitch_username.lower()]

    @twitchy.command(name="liststreamers")
    @commands.guild_only()
    async def twitchy_list_streamers(self, ctx):
        """List all Twitch streamers being monitored."""
        streamers = await self.config.guild(ctx.guild).streamers()
        if not streamers:
            await ctx.send("No Twitch streamers are currently being monitored in this server.")
            return

        streamer_list = "\n".join(f"- {s}" for s in streamers)
        for page in pagify(streamer_list, page_length=1000):
            await ctx.send(f"**Monitored Twitch Streamers:**\n{page}")

    async def get_stream_info(self, streamer_logins: list):
        if not streamer_logins:
            return {}

        headers = await self.get_twitch_headers()
        if not headers:
            return {}

        base_url = "https://api.twitch.tv/helix/streams"
        stream_info = {}
        # Twitch API allows up to 100 logins per request
        for i in range(0, len(streamer_logins), 100):
            batch = streamer_logins[i:i+100]
            params = "&".join([f"user_login={login}" for login in batch])
            url = f"{base_url}?{params}"

            try:
                async with self.session.get(url, headers=headers) as response:
                    response.raise_for_status()
                    data = await response.json()
                    for stream in data.get("data", []):
                        stream_info[stream["user_login"].lower()] = stream
            except aiohttp.ClientResponseError as e:
                print(f"Twitchy Cog: API error during stream info fetch: {e.status} - {e.message}")
            except Exception as e:
                print(f"Twitchy Cog: An unexpected error occurred fetching stream info: {e}")
                traceback.print_exc()
        return stream_info

    async def get_user_info(self, user_logins: list):
        if not user_logins:
            return {}

        headers = await self.get_twitch_headers()
        if not headers:
            return {}

        base_url = "https://api.twitch.tv/helix/users"
        user_info = {}
        for i in range(0, len(user_logins), 100):
            batch = user_logins[i:i+100]
            params = "&".join([f"login={login}" for login in batch])
            url = f"{base_url}?{params}"

            try:
                async with self.session.get(url, headers=headers) as response:
                    response.raise_for_status()
                    data = await response.json()
                    for user in data.get("data", []):
                        user_info[user["login"].lower()] = user
            except aiohttp.ClientResponseError as e:
                print(f"Twitchy Cog: API error during user info fetch: {e.status} - {e.message}")
            except Exception as e:
                print(f"Twitchy Cog: An unexpected error occurred fetching user info: {e}")
                traceback.print_exc()
        return user_info

    async def check_live_status_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                all_streamers = set()
                for guild_id in await self.config.all_guilds():
                    guild_data = await self.config.guild_from_id(guild_id).all()
                    if guild_data.get("streamers"):
                        all_streamers.update(guild_data["streamers"])
                
                if all_streamers:
                    stream_info = await self.get_stream_info(list(all_streamers))
                    user_info = await self.get_user_info(list(all_streamers)) # Fetch user info for profile pictures, etc.

                    for guild_id in await self.config.all_guilds():
                        guild_config = await self.config.guild_from_id(guild_id).all()
                        guild = self.bot.get_guild(guild_id)
                        
                        if not guild:
                            continue # Guild no longer exists

                        streamers_to_monitor = guild_config.get("streamers", [])
                        announcement_channel_id = guild_config.get("announcement_channel_id")
                        live_role_id = guild_config.get("live_role_id")

                        channel = guild.get_channel(announcement_channel_id) if announcement_channel_id else None
                        live_role = guild.get_role(live_role_id) if live_role_id else None

                        for streamer_username in streamers_to_monitor:
                            streamer_username_lower = streamer_username.lower()
                            is_live = streamer_username_lower in stream_info
                            
                            # Get the actual Twitch user ID from user_info
                            twitch_user_id = user_info.get(streamer_username_lower, {}).get("id")

                            # NEW: Get current live status from config using streamer_status_data
                            streamer_status = await self.config.guild(guild).streamer_status_data.get_raw(streamer_username_lower, default={})
                            last_live_status = streamer_status.get("live_status", False)

                            if twitch_user_id: # Only proceed if we have a valid Twitch user ID
                                if is_live and not last_live_status:
                                    # Stream just went live
                                    stream_data = stream_info[streamer_username_lower]
                                    stream_title = stream_data.get("title", "No Title")
                                    game_name = stream_data.get("game_name", "N/A")
                                    thumbnail_url = stream_data.get("thumbnail_url", "").replace("{width}", "1280").replace("{height}", "720")
                                    viewer_count = stream_data.get("viewer_count", 0)
                                    profile_image_url = user_info.get(streamer_username_lower, {}).get("profile_image_url")
                                    
                                    embed = discord.Embed(
                                        title=f"🔴 {streamer_username} is now LIVE on Twitch!",
                                        url=f"https://www.twitch.tv/{streamer_username}",
                                        description=f"**{stream_title}**\nPlaying: {game_name}\nViewers: {viewer_count}",
                                        color=0x9146FF # Twitch purple
                                    )
                                    embed.set_thumbnail(url=profile_image_url)
                                    embed.set_image(url=thumbnail_url)
                                    embed.set_footer(text="Twitch Stream Announcement")
                                    embed.timestamp = datetime.datetime.now(datetime.timezone.utc)

                                    if channel:
                                        try:
                                            # Create buttons for Watch Now and Subscribe
                                            watch_url = f"https://www.twitch.tv/{streamer_username}"
                                            subscribe_url = f"https://www.twitch.tv/subs/{streamer_username}" # General subscribe link
                                            view = StreamButtons(watch_url, subscribe_url)
                                            await channel.send(content=f"{live_role.mention}" if live_role else None, embed=embed, view=view)
                                        except discord.Forbidden:
                                            print(f"Twitchy Cog: Missing permissions to send messages in {channel.name} ({guild.name}).")
                                        except Exception as e:
                                            print(f"Twitchy Cog: Error sending announcement: {e}")
                                            traceback.print_exc()
                                    
                                    if live_role:
                                        for member in guild.members:
                                            # Only add role if member is actually a follower/subscriber or if you want to give to all
                                            # For simplicity, we'll iterate through all members for now.
                                            # A more advanced check would involve Twitch API for followers/subs.
                                            # For now, this just adds the role to any member.
                                            # If this is for specific members, logic needs to be added to find them.
                                            if streamer_username.lower() in [a.name.lower() for a in member.activities if isinstance(a, discord.Activity) and a.type == discord.ActivityType.streaming and a.platform == "Twitch"]:
                                                try:
                                                    await member.add_roles(live_role)
                                                except discord.Forbidden:
                                                    print(f"Twitchy Cog: Missing permissions to add role in {guild.name}.")
                                                    break
                                                except Exception as e:
                                                    print(f"Twitchy Cog: Error adding role to {member.name}: {e}")
                                                    traceback.print_exc()
                                            
                                    streamer_status["live_status"] = True
                                    await self.config.guild(guild).streamer_status_data.set_raw(streamer_username_lower, value=streamer_status)

                                elif not is_live and last_live_status:
                                    # Stream just went offline
                                    print(f"Twitchy Cog: {streamer_username} just went offline.")
                                    # Optionally send an offline announcement
                                    # if channel:
                                    #     await channel.send(f"🔴 {streamer_username} is now offline.")
                                    
                                    if live_role:
                                        for member in guild.members:
                                            if live_role in member.roles:
                                                # Check if the member is still streaming this specific streamer, or any other Twitch stream
                                                # If they are, don't remove the role.
                                                is_still_streaming_twitch = False
                                                for activity in member.activities:
                                                    if isinstance(activity, discord.Activity) and activity.type == discord.ActivityType.streaming and activity.platform == "Twitch":
                                                        # This logic assumes the role is for *any* live Twitch streamer.
                                                        # If it's specific to the streamer who just went offline, then you can remove the role.
                                                        # For now, we are generous and only remove if *no* Twitch stream is active for the member.
                                                        is_still_streaming_twitch = True
                                                        break
                                                
                                                if not is_still_streaming_twitch:
                                                    try:
                                                        await member.remove_roles(live_role)
                                                    except discord.Forbidden:
                                                        print(f"Twitchy Cog: Missing permissions to remove role in {guild.name}.")
                                                        break
                                                    except Exception as e:
                                                        print(f"Twitchy Cog: Error removing role from {member.name}: {e}")
                                                        traceback.print_exc()

                                    streamer_status["live_status"] = False
                                    await self.config.guild(guild).streamer_status_data.set_raw(streamer_username_lower, value=streamer_status)

            except Exception as e:
                print(f"Twitchy Cog: An error occurred in live check loop: {e}")
                traceback.print_exc()
            
            await asyncio.sleep(60) # Check every 60 seconds

    @commands.group()
    async def twitchy_schedule(self, ctx):
        """Manage Twitch schedule settings and generation."""
        pass

    @twitchy_schedule.command(name="setchannel")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def schedule_set_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel for posting the weekly Twitch schedule."""
        await self.config.guild(ctx.guild).schedule_channel_id.set(channel.id)
        await ctx.send(f"✅ Twitch schedule will now be posted in {channel.mention}.")

    @twitchy_schedule.command(name="setpingrole")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def schedule_set_ping_role(self, ctx, role: discord.Role = None):
        """
        Set a role to ping when the schedule is posted.
        Provide no role to remove the ping role.
        """
        if role:
            await self.config.guild(ctx.guild).schedule_ping_role_id.set(role.id)
            await ctx.send(f"✅ '{role.name}' will be pinged when the schedule is posted.")
        else:
            await ctx.send("✅ Schedule ping role removed.")

    @twitchy_schedule.command(name="settemplate")
    @commands.is_owner()
    async def schedule_set_template(self, ctx, url: str = None):
        """
        Set a custom image URL to use as the schedule template.
        Provide no URL to revert to the default template.
        """
        await self.config.custom_template_url.set(url)
        if url:
            await ctx.send(f"✅ Custom schedule template URL set to: {url}\nAttempting to download...")
        else:
            await ctx.send("✅ Reverted to default schedule template.\nAttempting to download default...")
        
        # Force re-download of resources
        await self.ensure_schedule_resources_task
        self.ensure_schedule_resources_task = self.loop.create_task(self.ensure_schedule_resources())
        await ctx.send("🔄 Schedule resources refresh initiated. Check console for status.")

    @twitchy_schedule.command(name="setfont")
    @commands.is_owner()
    async def schedule_set_font(self, ctx, url: str = None):
        """
        Set a custom font URL (TTF file) for the schedule image.
        Provide no URL to revert to the default font.
        """
        await self.config.custom_font_url.set(url)
        if url:
            await ctx.send(f"✅ Custom schedule font URL set to: {url}\nAttempting to download...")
        else:
            await ctx.send("✅ Reverted to default schedule font.\nAttempting to download default...")
        
        # Force re-download of resources
        await self.ensure_schedule_resources_task
        self.ensure_schedule_resources_task = self.loop.create_task(self.ensure_schedule_resources())
        await ctx.send("🔄 Schedule resources refresh initiated. Check console for status.")

    async def fetch_twitch_schedule(self, broadcaster_id: str):
        headers = await self.get_twitch_headers()
        if not headers:
            print("Twitchy Cog: Could not fetch schedule, no valid Twitch API headers.")
            return None

        schedule_url = f"https://api.twitch.tv/helix/schedule?broadcaster_id={broadcaster_id}"
        
        try:
            async with self.session.get(schedule_url, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
                return data.get("data", {}).get("segments")
        except aiohttp.ClientResponseError as e:
            print(f"Twitchy Cog: Failed to fetch Twitch schedule (HTTP {e.status}): {e.message}")
            return None
        except Exception as e:
            print(f"Twitchy Cog: An unexpected error occurred fetching schedule: {e}")
            traceback.print_exc()
            return None

    async def generate_schedule_image(self, schedule_data: list, start_date: datetime.datetime, guild_name: str, guild_icon_url: str = None):
        if not self.template_image or not self.font:
            print("Twitchy Cog: Cannot generate schedule image: template or font not loaded.")
            return None

        # Create a blank image with the same dimensions as the template
        img = self.template_image.copy()
        draw = ImageDraw.Draw(img)

        # Basic text drawing parameters
        text_color = (255, 255, 255) # White
        title_font = ImageFont.truetype(str(self.font_path), 48) if self.font_path.exists() else self.font
        segment_font = ImageFont.truetype(str(self.font_path), 20) if self.font_path.exists() else self.font
        
        # Add guild name/title
        title_text = f"{guild_name}'s Weekly Schedule"
        title_bbox = draw.textbbox((0, 0), title_text, font=title_font)
        title_width = title_bbox[2] - title_bbox[0]
        title_x = (img.width - title_width) / 2
        draw.text((title_x, 50), title_text, fill=text_color, font=title_font)

        # Add guild icon (if available)
        if guild_icon_url:
            try:
                async with self.session.get(guild_icon_url) as response:
                    response.raise_for_status()
                    icon_data = await response.read()
                    icon_img = Image.open(io.BytesIO(icon_data)).convert("RGBA")
                    icon_img = icon_img.resize((100, 100)) # Resize icon
                    img.paste(icon_img, (int(img.width - 120), 20), icon_img) # Top right corner
            except Exception as e:
                print(f"Twitchy Cog: Could not load guild icon: {e}")

        # Organize schedule by day of the week
        daily_schedule = {i: [] for i in range(7)} # 0=Monday, 6=Sunday
        
        for segment in schedule_data:
            # Parse start time and convert to local timezone
            start_time_utc = dateutil.parser.isoparse(segment["start_time"]).replace(tzinfo=datetime.timezone.utc)
            # Assuming bot is running in a consistent timezone for image generation,
            # or pass guild's timezone here if available. For now, use local.
            start_time_local = start_time_utc.astimezone(datetime.datetime.now(datetime.timezone.utc).astimezone().tzinfo)
            
            day_of_week = start_time_local.weekday() # Monday is 0, Sunday is 6
            daily_schedule[day_of_week].append(segment)
        
        # Sort segments within each day by time
        for day in daily_schedule:
            daily_schedule[day].sort(key=lambda x: dateutil.parser.isoparse(x["start_time"]))

        # Define drawing area for schedule content (adjust these coordinates based on your template)
        content_start_x = 50
        content_start_y = 150
        line_height = 25
        day_spacing = 150 # Horizontal space between days

        days_of_week = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        
        # Draw schedule for each day
        for i, day_name in enumerate(days_of_week):
            current_x = content_start_x + (i * day_spacing)
            current_y = content_start_y

            # Draw day name
            draw.text((current_x, current_y), day_name, fill=text_color, font=segment_font)
            current_y += line_height # Move down for segments

            if not daily_schedule[i]:
                draw.text((current_x, current_y), "No streams", fill=text_color, font=segment_font)
                continue

            for segment in daily_schedule[i]:
                start_time_utc = dateutil.parser.isoparse(segment["start_time"]).replace(tzinfo=datetime.timezone.utc)
                start_time_local = start_time_utc.astimezone(datetime.datetime.now(datetime.timezone.utc).astimezone().tzinfo)
                
                stream_title = segment.get("title", "No Title")
                category_name = segment.get("category", {}).get("name", "N/A")
                
                time_str = start_time_local.strftime("%I:%M %p")
                
                segment_text = f"{time_str} - {stream_title} ({category_name})"
                draw.text((current_x, current_y), segment_text, fill=text_color, font=segment_font)
                current_y += line_height

        # Save the image to a bytes buffer
        byte_arr = io.BytesIO()
        img.save(byte_arr, format='PNG')
        byte_arr.seek(0)
        return byte_arr

    async def update_all_guild_schedules_loop(self):
        await self.bot.wait_until_ready()
        # Run once on startup after resources are ensured
        await self.ensure_schedule_resources_task
        await self.update_all_guild_schedules()

        while True:
            # Calculate sleep time until next Monday 00:00 local time
            now = datetime.datetime.now(datetime.timezone.utc).astimezone() # Local timezone
            
            # Find next Monday
            days_until_monday = (0 - now.weekday() + 7) % 7 # 0 is Monday
            if days_until_monday == 0 and now.time() > datetime.time(0, 0, 0): # If it's Monday but after midnight
                days_until_monday = 7 # Go to next Monday

            next_monday = now + timedelta(days=days_until_monday)
            next_monday = next_monday.replace(hour=0, minute=0, second=0, microsecond=0)
            
            time_to_sleep = (next_monday - now).total_seconds()
            
            if time_to_sleep < 0: # Should not happen if logic is correct, but as a safeguard
                time_to_sleep = 0 # Run immediately

            print(f"Twitchy Cog: Next full schedule update in {time_to_sleep / 3600:.2f} hours (on {next_monday.strftime('%Y-%m-%d %H:%M %Z')}).")
            await asyncio.sleep(time_to_sleep)
            
            await self.update_all_guild_schedules()

    async def update_all_guild_schedules(self):
        print("Twitchy Cog: Initiating full schedule update for all guilds.")
        for guild_id in await self.config.all_guilds():
            guild_config = await self.config.guild_from_id(guild_id).all()
            guild = self.bot.get_guild(guild_id)
            
            if not guild:
                continue # Guild no longer exists

            broadcaster_id = None
            # Find a broadcaster ID from the monitored streamers to fetch schedule
            # For simplicity, just taking the first one if available.
            # A more robust solution might allow setting a specific broadcaster for schedule.
            streamers = guild_config.get("streamers", [])
            if streamers:
                # Get user info for the first streamer to get broadcaster_id
                user_info = await self.get_user_info([streamers[0]])
                broadcaster_id = user_info.get(streamers[0].lower(), {}).get("id")

            schedule_channel_id = guild_config.get("schedule_channel_id")
            ping_role_id = guild_config.get("schedule_ping_role_id")

            schedule_channel = guild.get_channel(schedule_channel_id) if schedule_channel_id else None
            ping_role = guild.get_role(ping_role_id) if ping_role_id else None

            if not schedule_channel or not broadcaster_id:
                if not schedule_channel:
                    print(f"Twitchy Cog: Skipping schedule update for guild {guild.name}: No schedule channel set.")
                if not broadcaster_id:
                    print(f"Twitchy Cog: Skipping schedule update for guild {guild.name}: No broadcaster ID found from monitored streamers.")
                continue

            await self.post_schedule(schedule_channel, broadcaster_id, ping_role)
        print("Twitchy Cog: Full schedule update completed.")

    async def post_schedule(self, channel: discord.TextChannel, broadcaster_id: str, ping_role: discord.Role = None, start_date: datetime.datetime = None):
        """Fetches, generates, and posts the Twitch schedule."""
        if not self.template_image or not self.font:
            await channel.send("❌ Schedule resources (template/font) are not loaded. Please ensure they are available and try again.")
            return

        schedule = await self.fetch_twitch_schedule(broadcaster_id)
        
        if schedule is None:
            await channel.send("❌ Failed to fetch schedule from Twitch! Check the bot's console for errors.")
            return

        # Filter schedule for the current week (Monday to Sunday)
        now = datetime.datetime.now(datetime.timezone.utc).astimezone()
        if start_date: # For testing, use provided start_date
            start_of_period = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_period = start_of_period + timedelta(days=6, hours=23, minutes=59, seconds=59)
        else: # For regular updates, use current week
            start_of_week = now - timedelta(days=now.weekday()) # Go back to Monday
            start_of_period = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_period = start_of_period + timedelta(days=6, hours=23, minutes=59, seconds=59)

        filtered_schedule = []
        for seg in schedule:
            seg_start_time_utc = dateutil.parser.isoparse(seg["start_time"]).replace(tzinfo=datetime.timezone.utc)
            seg_start_time_local = seg_start_time_utc.astimezone(now.tzinfo) # Use local timezone of bot
            
            if start_of_period <= seg_start_time_local <= end_of_period:
                filtered_schedule.append(seg)
        filtered_schedule.sort(key=lambda x: dateutil.parser.isoparse(x["start_time"]))

        if not filtered_schedule:
            await channel.send("ℹ️ No Twitch schedule segments found for the current week.")
            return

        image_buffer = await self.generate_schedule_image(filtered_schedule, start_of_period, channel.guild.name, channel.guild.icon.url if channel.guild.icon else None)

        if image_buffer:
            message_content = f"Here's the Twitch schedule for the week of {start_of_period.strftime('%Y-%m-%d')}!"
            if ping_role:
                message_content = f"{ping_role.mention} {message_content}"
            
            try:
                discord_file = discord.File(image_buffer, filename="twitch_schedule.png")
                await channel.send(content=message_content, file=discord_file)
            except discord.Forbidden:
                print(f"Twitchy Cog: Missing permissions to send files in {channel.name} ({channel.guild.name}).")
            except Exception as e:
                print(f"Twitchy Cog: Error sending schedule image: {e}")
                traceback.print_exc()
        else:
            await channel.send("❌ Failed to generate schedule image.")
    
    @twitchy_schedule.command(name="test")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def schedule_test(self, ctx):
        """Test generating and posting a schedule for the current week."""
        # Find a broadcaster ID from the monitored streamers
        broadcaster_id = None
        streamers = await self.config.guild(ctx.guild).streamers()
        if streamers:
            user_info = await self.get_user_info([streamers[0]])
            broadcaster_id = user_info.get(streamers[0].lower(), {}).get("id")

        if not broadcaster_id:
            await ctx.send("❌ No streamers configured or could not get broadcaster ID. Please add a streamer first using `[p]twitchy addstreamer`.")
            return

        guild_tz = pytz.timezone("Europe/London") # Example: use a default timezone for testing if not configured per guild

        await ctx.send("🔄 Generating test schedule. This may take a moment...")

        if not self.template_image or not self.font:
            await ctx.send("❌ Schedule resources (template/font) are not loaded. Please ensure they are available.")
            await self.ensure_schedule_resources() # Attempt to load them
            if not self.template_image or not self.font:
                await ctx.send("❌ Failed to load resources even after attempting refresh. Check console for errors.")
                return

        schedule = await self.fetch_twitch_schedule(broadcaster_id)
        
        if schedule is not None:
            # Filter schedule for the current week (Monday to Sunday)
            now = datetime.datetime.now(datetime.timezone.utc).astimezone(guild_tz)
            start_of_period = now - timedelta(days=now.weekday()) # Go back to Monday
            start_of_period = start_of_period.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_period = start_of_period + timedelta(days=6, hours=23, minutes=59, seconds=59)

            filtered_schedule = []
            for seg in schedule:
                seg_start_time_utc = dateutil.parser.isoparse(seg["start_time"]).replace(tzinfo=datetime.timezone.utc)
                seg_start_time_local = seg_start_time_utc.astimezone(guild_tz)
                if start_of_period <= seg_start_time_local <= end_of_period:
                    filtered_schedule.append(seg)
            filtered_schedule.sort(key=lambda x: dateutil.parser.isoparse(x["start_time"]))

            # Send to current context channel for testing
            await self.post_schedule(ctx.channel, broadcaster_id, start_date=start_of_period)
            await ctx.send("✅ Test complete!")
        else:
            await ctx.send("❌ Failed to fetch schedule from Twitch! Check the bot's console for errors.")

    @twitchy_schedule.command(name="reload")
    async def schedule_reload_resources(self, ctx):
        """Force redownload of the schedule template image and font files."""
        await ctx.send("🔄 Redownloading schedule resources...")
        
        # Delete existing files to force re-download
        if self.font_path.exists():
            os.remove(self.font_path)
        if self.template_path.exists():
            os.remove(self.template_path)
        
        success = await self.ensure_schedule_resources()
        
        if success:
            await ctx.send("✅ Successfully redownloaded schedule resources.")
        else:
            await ctx.send("❌ Failed to redownload all schedule resources. Check console for errors.")