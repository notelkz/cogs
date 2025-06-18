import asyncio
import aiohttp
import discord
from redbot.core import commands, Config
from redbot.core.utils.chat_formatting import humanize_list
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import MessagePredicate, ReactionPredicate
import time # <--- Keep this import

class Twitchy(commands.Cog):
    """
    Automatically announces when Twitch streams go live and manages 'Live' roles.
    """

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True) # Unique ID

        # Default configuration for the cog
        default_global = {
            "twitch_client_id": None,
            "twitch_client_secret": None,
            "twitch_access_token": None,
            "twitch_token_expires_at": 0,
            "streamers": {}, # Stores streamer configurations: {"twitch_id": {"username": "", "discord_channels": [], "ping_roles": []}}
            "live_role_id": None, # Role for auto-assigned "Live" status
            "linked_users": {} # Stores {"discord_id": "twitch_username"} for live role
        }

        self.config.register_global(**default_global)

        self.session = aiohttp.ClientSession() # For making HTTP requests to Twitch API
        self.check_loop = self.bot.loop.create_task(self.check_streams_loop()) # Start the checking loop
        self.twitch_api_base_url = "https://api.twitch.tv/helix/"


    async def red_delete_data_for_user(self, *, requester, user_id):
        """
        No data is stored by Twitchy that is specific to a user.
        """
        return


    def cog_unload(self):
        """Clean up when the cog is unloaded."""
        if self.check_loop:
            self.check_loop.cancel()
        if self.session:
            asyncio.create_task(self.session.close())


    async def get_twitch_access_token(self):
        """Fetches and stores a new Twitch API access token."""
        client_id = await self.config.twitch_client_id()
        client_secret = await self.config.twitch_client_secret()

        if not client_id or not client_secret:
            return None

        # Check if current token is valid and not expired
        expires_at = await self.config.twitch_token_expires_at()
        if expires_at > time.time() + 60: # Token valid for at least 60 more seconds
            return await self.config.twitch_access_token()

        token_url = "https://id.twitch.tv/oauth2/token"
        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials"
        }

        try:
            async with self.session.post(token_url, data=payload) as response:
                response.raise_for_status() # Raise an exception for HTTP errors
                data = await response.json()
                access_token = data.get("access_token")
                expires_in = data.get("expires_in") # Seconds until expiry

                if access_token:
                    await self.config.twitch_access_token.set(access_token)
                    await self.config.twitch_token_expires_at.set(time.time() + expires_in)
                    print("Twitchy: Successfully obtained new Twitch access token.")
                    return access_token
                else:
                    print("Twitchy: Failed to get access token from Twitch response.")
                    return None
        except aiohttp.ClientError as e:
            print(f"Twitchy: Failed to connect to Twitch for token: {e}")
            return None
        except Exception as e:
            print(f"Twitchy: An unexpected error occurred while getting token: {e}")
            return None


    async def get_twitch_user_info(self, username: str):
        """Fetches Twitch user info by username."""
        token = await self.get_twitch_access_token()
        client_id = await self.config.twitch_client_id()

        if not token or not client_id:
            return None

        headers = {
            "Authorization": f"Bearer {token}",
            "Client-Id": client_id
        }
        params = {"login": username}

        try:
            async with self.session.get(f"{self.twitch_api_base_url}users", headers=headers, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                users = data.get("data")
                if users:
                    return users[0] # Return the first user found
                return None
        except aiohttp.ClientResponseError as e:
            print(f"Twitchy: API error fetching user '{username}': {e.status} - {e.message}")
            return None
        except aiohttp.ClientError as e:
            print(f"Twitchy: Network error fetching user '{username}': {e}")
            return None
        except Exception as e:
            print(f"Twitchy: An unexpected error occurred fetching user '{username}': {e}")
            return None


    # This loop will periodically check Twitch streams
    async def check_streams_loop(self):
        await self.bot.wait_until_ready()
        while self is self.bot.get_cog("Twitchy"): # Ensure the cog is still loaded
            try:
                # We'll implement the actual checking logic here later
                # For now, just a placeholder and a sleep
                # print("Twitchy: Checking streams...") # Uncomment for debugging
                pass
            except asyncio.CancelledError:
                break # Exit gracefully if the loop is cancelled
            except Exception as e:
                print(f"Twitchy: An error occurred in check_streams_loop: {e}")
            await asyncio.sleep(60) # Check every 60 seconds (can be made configurable)


    @commands.group(name="twitchy")
    @commands.is_owner()
    async def twitchy(self, ctx):
        """Manages Twitch stream announcements and 'Live' roles."""
        pass

    @twitchy.command(name="setup")
    async def twitchy_setup(self, ctx):
        """
        Interactive setup for Twitch API keys.
        """
        if await self.config.twitch_client_id() and await self.config.twitch_client_secret():
            msg = await ctx.send(
                "Twitch API keys are already configured. Would you like to reset them? "
                "React with ✅ to reset, or ❌ to cancel."
            )
            start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(msg, ctx.author)
            try:
                await self.bot.wait_for("reaction_add", check=pred, timeout=60)
                if pred.result is False:
                    return await ctx.send("Twitchy setup cancelled. Existing keys remain.")
                else:
                    await self.config.twitch_client_id.set(None)
                    await self.config.twitch_client_secret.set(None)
                    await self.config.twitch_access_token.set(None)
                    await ctx.send("Twitch API keys have been reset. Proceeding with new setup.")
            except asyncio.TimeoutError:
                return await ctx.send("No response. Twitchy setup cancelled.")

        await ctx.send(
            "Welcome to Twitchy Setup! To get started, I need your Twitch Developer "
            "**Client ID** and **Client Secret**. "
            "You can get these by creating an application at:\n"
            "<https://dev.twitch.tv/console/apps>\n\n"
            "**Please enter your Twitch Client ID now.** (Type `cancel` to stop setup)"
        )

        try:
            # Get Client ID
            client_id_msg = await self.bot.wait_for("message", check=MessagePredicate.same_context(ctx), timeout=300)
            if client_id_msg.content.lower() == "cancel":
                return await ctx.send("Twitchy setup cancelled.")
            await self.config.twitch_client_id.set(client_id_msg.content.strip())

            await ctx.send(
                "Great! Now, **please enter your Twitch Client Secret.** "
                "(Keep this private! Type `cancel` to stop setup)"
            )

            # Get Client Secret
            client_secret_msg = await self.bot.wait_for("message", check=MessagePredicate.same_context(ctx), timeout=300)
            if client_secret_msg.content.lower() == "cancel":
                return await ctx.send("Twitchy setup cancelled.")
            await self.config.twitch_client_secret.set(client_secret_msg.content.strip())

            await ctx.send("Thank you! Attempting to get a Twitch access token...")

            # Try to get a token immediately
            token = await self.get_twitch_access_token()
            if token:
                await ctx.send(
                    "**Twitch API keys successfully saved and token obtained!** "
                    "You can now use `[p]twitchy addstreamer` to configure streamers."
                )
            else:
                await ctx.send(
                    "**Failed to obtain Twitch access token.** Please double-check your Client ID and Client Secret "
                    "and try `[p]twitchy setup` again. Errors might be logged in the console."
                )

        except asyncio.TimeoutError:
            await ctx.send("You took too long to respond. Twitchy setup cancelled.")
        except Exception as e:
            await ctx.send(f"An unexpected error occurred during setup: {e}")

    @twitchy.command(name="addstreamer")
    async def twitchy_addstreamer(self, ctx, twitch_username: str, discord_channel: discord.TextChannel, *roles: discord.Role):
        """
        Adds a Twitch streamer to monitor.
        Usage: [p]twitchy addstreamer <twitch_username> <#discord_channel> [role1] [role2]...
        Example: [p]twitchy addstreamer mycoolstreamer #stream-alerts @LiveRole @Everyone
        """
        twitch_username = twitch_username.lower() # Twitch usernames are case-insensitive

        # 1. Check if API keys are set
        if not await self.config.twitch_client_id() or not await self.config.twitch_client_secret():
            return await ctx.send(
                "Twitch API keys are not set. Please run `[p]twitchy setup` first."
            )

        # 2. Get Twitch user info (to get the Twitch ID)
        await ctx.send(f"Checking Twitch for user `{twitch_username}`...")
        twitch_user_info = await self.get_twitch_user_info(twitch_username)

        if not twitch_user_info:
            return await ctx.send(
                f"Could not find Twitch user `{twitch_username}`. "
                "Please ensure the username is correct."
            )

        twitch_id = twitch_user_info["id"]
        actual_twitch_username = twitch_user_info["login"] # Use the canonical login name

        # 3. Get current streamers config
        async with self.config.streamers() as streamers:
            if twitch_id in streamers:
                return await ctx.send(
                    f"`{actual_twitch_username}` is already being monitored. "
                    "Use `[p]twitchy removestreamer` to remove them first if you want to reconfigure."
                )

            # 4. Store configuration
            ping_role_ids = [role.id for role in roles]
            
            streamers[twitch_id] = {
                "username": actual_twitch_username,
                "discord_channel_id": discord_channel.id, # Store ID, not object
                "ping_role_ids": ping_role_ids, # Store IDs, not objects
                "last_announced_stream_id": None, # To prevent duplicate announcements
                "is_live": False # Current live status
            }

        ping_roles_names = humanize_list([role.name for role in roles]) if roles else "No roles"
        await ctx.send(
            f"Successfully added Twitch streamer `{actual_twitch_username}`.\n"
            f"Announcements will be sent to `{discord_channel.name}`.\n"
            f"Roles to ping: {ping_roles_names}."
        )


    # We'll add removestreamer, liststreamers, and other commands here later
    # We'll also implement the live stream checking logic in check_streams_loop