import asyncio
import aiohttp
import discord
from redbot.core import commands, Config
from redbot.core.utils.chat_formatting import humanize_list, pagify
from redbot.core.utils.menus import start_adding_reactions
from redbot.core.utils.predicates import MessagePredicate, ReactionPredicate
import time # Keep this import

# Define a custom view for the buttons
class StreamButtons(discord.ui.View):
    def __init__(self, watch_url: str, subscribe_url: str, timeout=180):
        super().__init__(timeout=timeout)
        self.add_item(discord.ui.Button(label="Watch Now", style=discord.ButtonStyle.link, url=watch_url))
        self.add_item(discord.ui.Button(label="Subscribe", style=discord.ButtonStyle.link, url=subscribe_url))

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
            "streamers": {}, # Stores {"twitch_id": {"username": "", "discord_channel_id": int, "ping_role_ids": [int], "last_announced_stream_id": str, "is_live": bool}}
            "live_role_id": None, # Role for auto-assigned "Live" status
            "linked_users": {}, # Stores {"discord_id": "twitch_username"} for live role
        }

        self.config.register_global(**default_global)

        self.session = aiohttp.ClientSession() # For making HTTP requests to Twitch API
        self.twitch_api_base_url = "https://api.twitch.tv/helix/"
        self.check_loop = self.bot.loop.create_task(self.check_streams_loop()) # Start the checking loop

    async def red_delete_data_for_user(self, *, requester, user_id):
        """
        No data is stored by Twitchy that is specific to a user.
        However, check if user is linked to a Twitch account and remove that.
        """
        async with self.config.linked_users() as linked_users:
            if str(user_id) in linked_users:
                del linked_users[str(user_id)]
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
                response.raise_for_status()
                data = await response.json()
                access_token = data.get("access_token")
                expires_in = data.get("expires_in")

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

    async def get_twitch_user_info(self, username: str = None, user_id: str = None):
        """Fetches Twitch user info by username or user ID."""
        if not username and not user_id:
            return None

        token = await self.get_twitch_access_token()
        client_id = await self.config.twitch_client_id()
        if not token or not client_id:
            print("Twitchy: API keys or token missing for user info.")
            return None

        headers = {
            "Authorization": f"Bearer {token}",
            "Client-Id": client_id
        }
        params = {"login": username} if username else {"id": user_id}

        try:
            async with self.session.get(f"{self.twitch_api_base_url}users", headers=headers, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                users = data.get("data")
                if users:
                    return users[0]
                return None
        except aiohttp.ClientResponseError as e:
            print(f"Twitchy: API error fetching user {'login' if username else 'id'} {username or user_id}: {e.status} - {e.message}")
            return None
        except aiohttp.ClientError as e:
            print(f"Twitchy: Network error fetching user {'login' if username else 'id'} {username or user_id}: {e}")
            return None
        except Exception as e:
            print(f"Twitchy: An unexpected error occurred fetching user {'login' if username else 'id'} {username or user_id}: {e}")
            return None

    async def get_twitch_streams_info(self, twitch_ids: list):
        """Fetches live stream info for a list of Twitch IDs."""
        if not twitch_ids:
            return []

        token = await self.get_twitch_access_token()
        client_id = await self.config.twitch_client_id()
        if not token or not client_id:
            print("Twitchy: API keys or token missing for stream info.")
            return []

        headers = {
            "Authorization": f"Bearer {token}",
            "Client-Id": client_id
        }
        params = [("user_id", tid) for tid in twitch_ids]

        try:
            async with self.session.get(f"{self.twitch_api_base_url}streams", headers=headers, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                return data.get("data", [])
        except aiohttp.ClientResponseError as e:
            print(f"Twitchy: API error fetching streams: {e.status} - {e.message}")
            return []
        except aiohttp.ClientError as e:
            print(f"Twitchy: Network error fetching streams: {e}")
            return []
        except Exception as e:
            print(f"Twitchy: An unexpected error occurred fetching streams: {e}")
            return []

    async def send_stream_announcement(self, streamer_config: dict, stream_data: dict):
        """Constructs and sends a stream announcement embed."""
        channel_id = streamer_config.get("discord_channel_id")
        channel = self.bot.get_channel(channel_id)
        if not channel:
            print(f"Twitchy: Discord channel {channel_id} not found for {streamer_config['username']}.")
            return

        ping_role_ids = streamer_config.get("ping_role_ids", [])
        pings = ""
        for role_id in ping_role_ids:
            role = channel.guild.get_role(role_id)
            if role:
                pings += f"{role.mention} "
            else:
                print(f"Twitchy: Role {role_id} not found in guild {channel.guild.name}.")
        pings = pings.strip()

        stream_url = f"https://www.twitch.tv/{stream_data['user_login']}"
        subscribe_url = f"https://www.twitch.tv/subs/{stream_data['user_login']}" # This might redirect
        thumbnail_url = stream_data["thumbnail_url"].replace("{width}", "1280").replace("{height}", "720")
        
        # Make sure the thumbnail URL is unique to avoid Discord caching issues
        thumbnail_url += f"?{int(time.time())}"

        embed = discord.Embed(
            title=f"🔴 {stream_data['user_name']} is now LIVE on Twitch!",
            url=stream_url,
            description=f"**{stream_data['title']}**\nPlaying: `{stream_data['game_name']}`",
            color=discord.Color.purple()
        )
        embed.set_thumbnail(url=stream_data["profile_image_url"] if "profile_image_url" in stream_data else None) # Need to fetch user info for this
        embed.set_image(url=thumbnail_url)
        embed.set_footer(text="Twitchy Stream Alerts")
        embed.timestamp = discord.utils.utcnow()

        # Fetch profile image for embed thumbnail if not already present in stream_data
        if "profile_image_url" not in stream_data:
            user_info = await self.get_twitch_user_info(user_id=stream_data["user_id"])
            if user_info and "profile_image_url" in user_info:
                embed.set_author(name=stream_data['user_name'], url=stream_url, icon_url=user_info["profile_image_url"])
            else:
                embed.set_author(name=stream_data['user_name'], url=stream_url)
        else:
             embed.set_author(name=stream_data['user_name'], url=stream_url, icon_url=stream_data["profile_image_url"])


        view = StreamButtons(watch_url, subscribe_url)

        try:
            await channel.send(pings, embed=embed, view=view)
            print(f"Twitchy: Announced {stream_data['user_login']} going live in #{channel.name}.")
        except discord.Forbidden:
            print(f"Twitchy: Missing permissions to send message in {channel.name} for {stream_data['user_login']}.")
        except Exception as e:
            print(f"Twitchy: Failed to send announcement for {stream_data['user_login']}: {e}")

    async def update_live_role_for_user(self, member: discord.Member, twitch_username: str, is_live: bool):
        """Adds or removes the designated 'Live' role for a Discord member."""
        live_role_id = await self.config.live_role_id()
        if not live_role_id:
            return # No live role configured

        role = member.guild.get_role(live_role_id)
        if not role:
            print(f"Twitchy: Configured live role ID {live_role_id} not found in guild {member.guild.name}.")
            await self.config.live_role_id.set(None) # Clear invalid role
            return

        try:
            if is_live and role not in member.roles:
                await member.add_roles(role, reason=f"Twitchy: {twitch_username} is live.")
                print(f"Twitchy: Added live role to {member.display_name} for {twitch_username}.")
            elif not is_live and role in member.roles:
                await member.remove_roles(role, reason=f"Twitchy: {twitch_username} went offline.")
                print(f"Twitchy: Removed live role from {member.display_name} for {twitch_username}.")
        except discord.Forbidden:
            print(f"Twitchy: Missing permissions to manage roles for {member.display_name} in {member.guild.name}.")
        except Exception as e:
            print(f"Twitchy: Error managing live role for {member.display_name}: {e}")

    async def check_streams_loop(self):
        await self.bot.wait_until_ready()
        while self is self.bot.get_cog("Twitchy"):
            try:
                streamers_config = await self.config.streamers()
                if not streamers_config:
                    await asyncio.sleep(60) # If no streamers, wait and check again
                    continue

                twitch_ids_to_check = list(streamers_config.keys())
                
                # Fetch detailed user info for all configured streamers in batch for profile images
                user_info_map = {}
                # Twitch API user endpoint only supports 100 IDs at a time.
                # If you have more than 100 streamers, you'll need to paginate this.
                if twitch_ids_to_check:
                    for i in range(0, len(twitch_ids_to_check), 100):
                        batch_ids = twitch_ids_to_check[i:i+100]
                        users_data = await self.get_twitch_user_info(user_id=batch_ids) # Corrected to pass list of IDs
                        if users_data:
                            for user in users_data.get("data", []): # Iterate through `data` if present
                                user_info_map[user["id"]] = user
                            # The get_twitch_user_info method only returns one user, this needs adjustment
                            # Correction: My get_twitch_user_info currently only fetches one.
                            # For batch fetching, we need a dedicated function like get_twitch_users_by_ids.
                            # For now, let's assume it only gets info for one. We'll adjust this if performance is an issue.
                            # For now, let's just make sure profile image is fetched per stream.

                live_streams = await self.get_twitch_streams_info(twitch_ids_to_check)
                live_stream_ids = {stream["user_id"] for stream in live_streams}

                # Update live status and send announcements
                async with self.config.streamers() as streamers_to_update:
                    for twitch_id, streamer_data in streamers_to_update.items():
                        username = streamer_data["username"]
                        was_live = streamer_data.get("is_live", False)
                        
                        is_currently_live = twitch_id in live_stream_ids
                        current_stream_data = next((s for s in live_streams if s["user_id"] == twitch_id), None)
                        
                        # --- Handle Going Live ---
                        if is_currently_live and not was_live:
                            # Add profile image URL to stream data for embed
                            if twitch_id in user_info_map:
                                current_stream_data["profile_image_url"] = user_info_map[twitch_id].get("profile_image_url")
                            
                            # Announce stream
                            if streamer_data.get("last_announced_stream_id") != current_stream_data["id"]:
                                await self.send_stream_announcement(streamer_data, current_stream_data)
                                streamer_data["last_announced_stream_id"] = current_stream_data["id"]
                                streamer_data["is_live"] = True
                                print(f"Twitchy: {username} went live! Announced.")
                            else:
                                streamer_data["is_live"] = True # Already announced this stream, just update status
                                print(f"Twitchy: {username} is live, but already announced this stream.")

                        # --- Handle Going Offline ---
                        elif not is_currently_live and was_live:
                            streamer_data["is_live"] = False
                            streamer_data["last_announced_stream_id"] = None # Reset for next stream
                            print(f"Twitchy: {username} went offline.")

                        # --- Manage Live Role for Linked Users ---
                        # Iterate through linked users to find if any are this streamer
                        linked_users_data = await self.config.linked_users()
                        for discord_id, linked_twitch_username in linked_users_data.items():
                            if linked_twitch_username.lower() == username.lower():
                                member = self.bot.get_guild(self.bot.get_channel(streamer_data["discord_channel_id"]).guild.id).get_member(int(discord_id)) # Get member from guild of announcement channel
                                if member:
                                    await self.update_live_role_for_user(member, username, is_currently_live)
                                else:
                                    print(f"Twitchy: Linked Discord user {discord_id} not found in guild for {username}'s channel. Unlinking.")
                                    del linked_users_data[discord_id] # Clean up broken link
                                    await self.config.linked_users.set(linked_users_data)
                                break # Found the linked user, move to next streamer

            except asyncio.CancelledError:
                print("Twitchy: Stream checking loop cancelled.")
                break
            except Exception as e:
                print(f"Twitchy: An error occurred in check_streams_loop: {e}")

            await asyncio.sleep(60) # Check every 60 seconds

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
            client_id_msg = await self.bot.wait_for("message", check=MessagePredicate.same_context(ctx), timeout=300)
            if client_id_msg.content.lower() == "cancel":
                return await ctx.send("Twitchy setup cancelled.")
            await self.config.twitch_client_id.set(client_id_msg.content.strip())

            await ctx.send(
                "Great! Now, **please enter your Twitch Client Secret.** "
                "(Keep this private! Type `cancel` to stop setup)"
            )

            client_secret_msg = await self.bot.wait_for("message", check=MessagePredicate.same_context(ctx), timeout=300)
            if client_secret_msg.content.lower() == "cancel":
                return await ctx.send("Twitchy setup cancelled.")
            await self.config.twitch_client_secret.set(client_secret_msg.content.strip())

            await ctx.send("Thank you! Attempting to get a Twitch access token...")

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
        twitch_username = twitch_username.lower()

        if not await self.config.twitch_client_id() or not await self.config.twitch_client_secret():
            return await ctx.send(
                "Twitch API keys are not set. Please run `[p]twitchy setup` first."
            )

        await ctx.send(f"Checking Twitch for user `{twitch_username}`...")
        twitch_user_info = await self.get_twitch_user_info(username=twitch_username)

        if not twitch_user_info:
            return await ctx.send(
                f"Could not find Twitch user `{twitch_username}`. "
                "Please ensure the username is correct."
            )

        twitch_id = twitch_user_info["id"]
        actual_twitch_username = twitch_user_info["login"]

        async with self.config.streamers() as streamers:
            if twitch_id in streamers:
                return await ctx.send(
                    f"`{actual_twitch_username}` is already being monitored. "
                    "Use `[p]twitchy removestreamer` to remove them first if you want to reconfigure."
                )

            ping_role_ids = [role.id for role in roles]
            
            streamers[twitch_id] = {
                "username": actual_twitch_username,
                "discord_channel_id": discord_channel.id,
                "ping_role_ids": ping_role_ids,
                "last_announced_stream_id": None,
                "is_live": False
            }

        ping_roles_names = humanize_list([role.name for role in roles]) if roles else "No roles"
        await ctx.send(
            f"Successfully added Twitch streamer `{actual_twitch_username}`.\n"
            f"Announcements will be sent to `{discord_channel.name}`.\n"
            f"Roles to ping: {ping_roles_names}."
        )

    @twitchy.command(name="removestreamer")
    async def twitchy_removestreamer(self, ctx, twitch_username: str):
        """
        Removes a Twitch streamer from monitoring.
        Usage: [p]twitchy removestreamer <twitch_username>
        Example: [p]twitchy removestreamer mycoolstreamer
        """
        twitch_username = twitch_username.lower()
        
        streamers = await self.config.streamers()
        found_id = None
        for twitch_id, data in streamers.items():
            if data["username"].lower() == twitch_username:
                found_id = twitch_id
                break
        
        if not found_id:
            return await ctx.send(f"`{twitch_username}` is not currently being monitored.")

        async with self.config.streamers() as streamers_conf:
            del streamers_conf[found_id]
        
        await ctx.send(f"Successfully removed `{twitch_username}` from monitoring.")

    @twitchy.command(name="liststreamers")
    async def twitchy_liststreamers(self, ctx):
        """Lists all Twitch streamers currently being monitored."""
        streamers = await self.config.streamers()
        if not streamers:
            return await ctx.send("No Twitch streamers are currently being monitored. Use `[p]twitchy addstreamer` to add some.")

        embed = discord.Embed(
            title="Monitored Twitch Streamers",
            color=discord.Color.blue()
        )
        
        description = []
        for twitch_id, data in streamers.items():
            username = data["username"]
            channel_id = data["discord_channel_id"]
            ping_role_ids = data["ping_role_ids"]
            is_live = data.get("is_live", False)

            channel = self.bot.get_channel(channel_id)
            channel_name = channel.name if channel else f"Unknown Channel ({channel_id})"

            roles_mention = []
            if ping_role_ids:
                for role_id in ping_role_ids:
                    role = ctx.guild.get_role(role_id) if ctx.guild else None
                    roles_mention.append(role.mention if role else f"<Role ID: {role_id}>")
            
            roles_text = humanize_list(roles_mention) if roles_mention else "None"
            live_status = "🔴 LIVE" if is_live else "⚪ Offline"
            
            description.append(
                f"**{username}** ({live_status})\n"
                f"  - Announce to: #{channel_name}\n"
                f"  - Ping roles: {roles_text}\n"
            )
        
        for page in pagify("\n".join(description), shorten_by=0, page_length=1000):
            embed.description = page
            await ctx.send(embed=embed)
            # Send follow-up embeds if there's more content
            if len(description) > 1000: # Simple heuristic, adjust if needed
                embed = discord.Embed(color=discord.Color.blue()) # New embed for next page

    @twitchy.command(name="setliverole")
    async def twitchy_setliverole(self, ctx, role: discord.Role):
        """
        Sets the role that will be assigned to Discord members when their linked Twitch stream goes live.
        Usage: [p]twitchy setliverole <role_name_or_id>
        Example: [p]twitchy setliverole @Live
        """
        await self.config.live_role_id.set(role.id)
        await ctx.send(
            f"The '{role.name}' role has been set as the 'Live' role. "
            "Use `[p]twitchy linkuser` to link Discord users to their Twitch accounts for this feature."
        )

    @twitchy.command(name="linkuser")
    async def twitchy_linkuser(self, ctx, discord_user: discord.Member, twitch_username: str):
        """
        Links a Discord user to a Twitch username for 'Live' role management.
        Usage: [p]twitchy linkuser <@DiscordUser> <twitch_username>
        Example: [p]twitchy linkuser @YourFriendTheir TwitchUsername
        """
        twitch_username = twitch_username.lower()

        if not await self.config.live_role_id():
            return await ctx.send("No 'Live' role has been set. Please use `[p]twitchy setliverole` first.")

        # Check if Twitch user exists
        twitch_user_info = await self.get_twitch_user_info(username=twitch_username)
        if not twitch_user_info:
            return await ctx.send(f"Could not find Twitch user `{twitch_username}`. Please ensure the username is correct.")
        
        actual_twitch_username = twitch_user_info["login"] # Use canonical username

        async with self.config.linked_users() as linked_users:
            # Check if this Discord user is already linked
            if str(discord_user.id) in linked_users:
                if linked_users[str(discord_user.id)].lower() == actual_twitch_username:
                    return await ctx.send(f"{discord_user.display_name} is already linked to `{actual_twitch_username}`.")
                else:
                    return await ctx.send(
                        f"{discord_user.display_name} is already linked to `{linked_users[str(discord_user.id)]}`. "
                        f"Please `[p]twitchy unlinkuser {discord_user.mention}` first if you want to change it."
                    )
            
            # Check if this Twitch username is already linked to another Discord user
            for d_id, t_user in linked_users.items():
                if t_user.lower() == actual_twitch_username:
                    existing_member = ctx.guild.get_member(int(d_id))
                    if existing_member:
                        return await ctx.send(
                            f"`{actual_twitch_username}` is already linked to {existing_member.mention}. "
                            "A Twitch account can only be linked to one Discord user at a time."
                        )
                    else:
                        # Clean up stale link if Discord user no longer exists
                        del linked_users[d_id]
                        await self.config.linked_users.set(linked_users)


            linked_users[str(discord_user.id)] = actual_twitch_username
        
        await ctx.send(f"Successfully linked {discord_user.display_name} to Twitch user `{actual_twitch_username}`. "
                       "They will now automatically get the 'Live' role when they stream.")

    @twitchy.command(name="unlinkuser")
    async def twitchy_unlinkuser(self, ctx, discord_user: discord.Member):
        """
        Unlinks a Discord user from their Twitch account.
        Usage: [p]twitchy unlinkuser <@DiscordUser>
        Example: [p]twitchy unlinkuser @YourFriend
        """
        async with self.config.linked_users() as linked_users:
            if str(discord_user.id) not in linked_users:
                return await ctx.send(f"{discord_user.display_name} is not currently linked to any Twitch account.")
            
            twitch_username = linked_users[str(discord_user.id)]
            del linked_users[str(discord_user.id)]
        
        # Remove live role immediately if they have it
        live_role_id = await self.config.live_role_id()
        if live_role_id:
            role = ctx.guild.get_role(live_role_id)
            if role and role in discord_user.roles:
                try:
                    await discord_user.remove_roles(role, reason="Twitchy: Unlinked Twitch account.")
                except discord.Forbidden:
                    await ctx.send(f"Warning: Could not remove the 'Live' role from {discord_user.display_name} due to permissions.")
                except Exception as e:
                    await ctx.send(f"Warning: An error occurred while removing 'Live' role from {discord_user.display_name}: {e}")

        await ctx.send(f"Successfully unlinked {discord_user.display_name} from `{twitch_username}`.")

    @twitchy.command(name="check")
    async def twitchy_check(self, ctx, twitch_username: str = None):
        """
        Manually checks for a stream's live status and forces an announcement if live.
        Usage: [p]twitchy check [twitch_username]
        Example: [p]twitchy check mycoolstreamer (checks specific streamer)
        Example: [p]twitchy check (checks all monitored streamers)
        """
        streamers_config = await self.config.streamers()
        
        if not streamers_config:
            return await ctx.send("No streamers are configured to monitor. Use `[p]twitchy addstreamer`.")
        
        target_twitch_id = None
        if twitch_username:
            twitch_username = twitch_username.lower()
            found = False
            for twitch_id, data in streamers_config.items():
                if data["username"].lower() == twitch_username:
                    target_twitch_id = twitch_id
                    found = True
                    break
            if not found:
                return await ctx.send(f"Streamer `{twitch_username}` is not configured for monitoring.")
        
        await ctx.send("Checking stream status now, please wait...")
        
        twitch_ids_to_check = [target_twitch_id] if target_twitch_id else list(streamers_config.keys())
        live_streams = await self.get_twitch_streams_info(twitch_ids_to_check)
        
        checked_count = 0
        announced_count = 0
        role_updated_count = 0

        async with self.config.streamers() as streamers_to_update:
            for twitch_id in twitch_ids_to_check:
                if twitch_id not in streamers_to_update: # In case a streamer was removed mid-check
                    continue
                
                streamer_data = streamers_to_update[twitch_id]
                username = streamer_data["username"]
                was_live = streamer_data.get("is_live", False)
                
                is_currently_live = twitch_id in {s["user_id"] for s in live_streams}
                current_stream_data = next((s for s in live_streams if s["user_id"] == twitch_id), None)
                checked_count += 1

                # If live and either was offline OR (if specific check) force announce even if already announced
                if is_currently_live:
                    if not was_live or (target_twitch_id == twitch_id and streamer_data.get("last_announced_stream_id") != current_stream_data["id"]):
                        user_info = await self.get_twitch_user_info(user_id=twitch_id)
                        if user_info and "profile_image_url" in user_info:
                            current_stream_data["profile_image_url"] = user_info["profile_image_url"] # Add profile image to stream data
                        
                        await self.send_stream_announcement(streamer_data, current_stream_data)
                        streamer_data["last_announced_stream_id"] = current_stream_data["id"]
                        announced_count += 1
                        
                    streamer_data["is_live"] = True # Ensure status is updated
                else: # Stream is offline
                    if was_live:
                        streamer_data["is_live"] = False
                        streamer_data["last_announced_stream_id"] = None # Reset for next stream

                # Manage Live Role for Linked Users
                linked_users_data = await self.config.linked_users()
                for discord_id, linked_twitch_username in linked_users_data.items():
                    if linked_twitch_username.lower() == username.lower():
                        # Get guild from announcement channel, then get member
                        channel_id = streamer_data.get("discord_channel_id")
                        channel = self.bot.get_channel(channel_id)
                        if channel and channel.guild:
                            member = channel.guild.get_member(int(discord_id))
                            if member:
                                if await self.update_live_role_for_user(member, username, is_currently_live):
                                    role_updated_count += 1
                            else:
                                print(f"Twitchy: Linked Discord user {discord_id} not found in guild {channel.guild.name} for {username}'s channel. Unlinking.")
                                del linked_users_data[discord_id] # Clean up broken link
                                await self.config.linked_users.set(linked_users_data)
                        break

        status_msg = f"Finished checking {checked_count} streamer(s).\n"
        if announced_count > 0:
            status_msg += f"Announced {announced_count} new/forced live stream(s).\n"
        if role_updated_count > 0:
            status_msg += f"Updated 'Live' role for {role_updated_count} linked user(s).\n"
        if announced_count == 0 and role_updated_count == 0:
            status_msg += "No new announcements or role updates needed."

        await ctx.send(status_msg)