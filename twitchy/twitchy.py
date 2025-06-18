import asyncio
import aiohttp
import discord
from redbot.core import commands, Config
from redbot.core.utils.chat_formatting import humanize_list
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import MessagePredicate, ReactionPredicate
import time # <--- ADD THIS LINE

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
            "twitch_access_token": None, # Will be generated from client ID/secret
            "twitch_token_expires_at": 0, # <--- THIS WAS THE FIRST SPOT AFFECTED
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
        if expires_at > time.time() + 60: # <--- THIS WAS THE SECOND SPOT AFFECTED (use time.time() here)
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
                    await self.config.twitch_token_expires_at.set(time.time() + expires_in) # <--- ALSO HERE
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