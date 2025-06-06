import discord
from redbot.core import commands, Config
import feedparser
import asyncio
import re
import html
from bs4 import BeautifulSoup

# Initial game feeds dictionary
GAME_FEEDS = {
    "squad": "https://store.steampowered.com/feeds/news/app/393380/",
    "hell let loose": "https://store.steampowered.com/feeds/news/app/686810/",
    "delta force": "https://store.steampowered.com/feeds/news/app/2511540/",
    "arma 3": "https://store.steampowered.com/feeds/news/app/107410/",
    "arma reforger": "https://store.steampowered.com/feeds/news/app/1874880/",
    "escape from tarkov": "https://www.escapefromtarkov.com/news/rss",
    "overwatch": "https://store.steampowered.com/feeds/news/app/2357570/",
    "overwatch 2": "https://store.steampowered.com/feeds/news/app/2357570/",
    "marvel rivals": "https://store.steampowered.com/feeds/news/app/2286610/",
    "valorant": "https://playvalorant.com/en-us/news/tags/patch-notes/rss/",
    "fragpunk": "https://store.steampowered.com/feeds/news/app/2440510/",
    "helldivers 2": "https://store.steampowered.com/feeds/news/app/553850/",
    "gta online": "https://store.steampowered.com/feeds/news/app/271590/",
    # Adding Battlefield games
    "battlefield 2042": "https://www.ea.com/games/battlefield/battlefield-2042/news.rss",
    "battlefield v": "https://www.ea.com/games/battlefield/battlefield-5/news.rss",
    "battlefield 1": "https://www.ea.com/games/battlefield/battlefield-1/news.rss",
    "battlefield 6": "https://www.ea.com/games/battlefield/news.rss",  # Generic feed until specific one is available
}

class GameConverter(commands.Converter):
    """Converter that allows mentioning a game or typing its name."""
    async def convert(self, ctx, argument):
        # Check if it's a mention format like @GameName
        mention_match = re.match(r'<@!?(\d+)>', argument)
        if mention_match:
            # Try to get the member and use their display name as the game name
            member_id = int(mention_match.group(1))
            member = ctx.guild.get_member(member_id)
            if member:
                return member.display_name.lower()
        
        # If not a mention or member not found, just return the argument as is
        return argument.lower()

class GameUpdates(commands.Cog):
    """Fetch and post patch notes for many games to channels, threads, or forums."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567894)
        default_guild = {
            "games": {},  # {game_name: {"channel": channel_id, "thread": thread_id, "forum": forum_id, "forum_thread": thread_id, "last_update": update_id}}
            "custom_feeds": {}  # {game_name: feed_url}
        }
        self.config.register_global(
            permanent_games={}  # Games added to the built-in list
        )
        self.config.register_guild(**default_guild)
        
        # Task management
        self.bg_task = None
        self.is_running = True

    async def cog_load(self):
        """Called when the cog is loaded."""
        await self._load_permanent_games()
        self.bg_task = asyncio.create_task(self._update_loop())
        
    async def cog_unload(self):
        """Called when the cog is unloaded."""
        self.is_running = False
        if self.bg_task:
            self.bg_task.cancel()

    async def _load_permanent_games(self):
        """Load permanent games from config and add them to GAME_FEEDS."""
        global GAME_FEEDS  # Declare global before using it
        await self.bot.wait_until_ready()
        permanent_games = await self.config.permanent_games()
        # Add permanent games to GAME_FEEDS
        GAME_FEEDS.update(permanent_games)

    async def _update_loop(self):
        """Main loop that checks for updates periodically."""
        await self.bot.wait_until_ready()
        while self.is_running:
            try:
                await self._check_for_updates()
            except asyncio.CancelledError:
                # Handle cancellation gracefully
                break
            except Exception as e:
                # Log the error but don't crash the loop
                print(f"Error in update loop: {e}")
            
            # Wait 10 minutes before checking again
            await asyncio.sleep(600)
    
    def clean_html(self, html_content):
        """Clean HTML content to make it readable in Discord."""
        if not html_content:
            return ""
            
        try:
            # Parse HTML with BeautifulSoup
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Replace <br> and <p> tags with newlines
            for br in soup.find_all(['br', 'p']):
                br.replace_with('\n' + br.text)
                
            # Replace <li> tags with bullet points
            for li in soup.find_all('li'):
                li.replace_with('\n• ' + li.text)
                
            # Replace <h1>, <h2>, etc. with bold text
            for h in soup.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6']):
                h.replace_with('\n\n**' + h.text + '**\n')
                
            # Get the text and decode HTML entities
            text = html.unescape(soup.get_text())
            
            # Clean up excessive newlines
            text = re.sub(r'\n{3,}', '\n\n', text)
            
            return text.strip()
        except Exception as e:
            print(f"Error cleaning HTML: {e}")
            # Fallback: remove HTML tags with a simple regex
            text = re.sub(r'<[^>]+>', '', html_content)
            return html.unescape(text).strip()

    async def fetch_patch_notes(self, url, game):
        """Fetch and parse patch notes from an RSS feed."""
        loop = asyncio.get_event_loop()
        try:
            feed = await loop.run_in_executor(None, feedparser.parse, url)
            updates = []
            for entry in feed.entries:
                # Try to filter for patch/update notes
                if any(word in entry.title.lower() for word in ("patch", "update", "notes", "hotfix", "changelog")):
                    # Get the content from the entry
                    content = getattr(entry, 'content', [{}])[0].get('value', '') if hasattr(entry, 'content') else ''
                    if not content:
                        content = getattr(entry, 'summary', '')
                    
                    # Clean the HTML content
                    cleaned_content = self.clean_html(content)
                    
                    # Clean the title
                    clean_title = self.clean_html(entry.title)
                    
                    updates.append({
                        "id": getattr(entry, "id", getattr(entry, "link", None)),
                        "title": clean_title,
                        "content": cleaned_content,
                        "date": getattr(entry, "published", getattr(entry, "updated", None)),
                        "url": getattr(entry, "link", None)
                    })
            return updates
        except Exception as e:
            print(f"Error fetching updates for {game}: {e}")
            return []

    def _extract_version_from_title(self, title):
        """Extract version number from update title."""
        # Try to find version patterns like v1.2.3, 1.2.3, or "version 1.2"
        version_info = re.search(r'(v\d+\.\d+|\d+\.\d+|version\s+\d+\.\d+)', title, re.IGNORECASE)
        if version_info:
            return version_info.group(0)
        
        # If no version pattern found, try to find any number that might be a version
        version_info = re.search(r'(\d+\.\d+|\d+)', title)
        if version_info:
            return version_info.group(0)
        
        # Fallback if no version number found
        return "Patch_Notes"

    async def _is_duplicate_thread(self, forum, thread_name):
        """Check if a thread with the same name already exists in the forum."""
        # Check active threads
        for thread in forum.threads:
            if thread.name.lower() == thread_name.lower():
                return True
        
        # Also check recently archived threads
        try:
            async for thread in forum.archived_threads(limit=10):
                if thread.name.lower() == thread_name.lower():
                    return True
        except:
            # If we can't check archived threads, just continue
            pass
            
        return False

    async def _check_for_updates(self, specific_game=None, specific_guild=None, force_post=False):
        """
        Check for updates for games in guilds.
        
        Parameters:
        - specific_game: If provided, only check this game
        - specific_guild: If provided, only check in this guild
        - force_post: If True, post the latest update regardless of last_update
        """
        guilds = [specific_guild] if specific_guild else self.bot.guilds
        
        for guild in guilds:
            try:
                games = await self.config.guild(guild).games()
                custom_feeds = await self.config.guild(guild).custom_feeds()
                
                # Filter games if specific_game is provided
                game_items = [(g, d) for g, d in games.items() 
                             if specific_game is None or g.lower() == specific_game.lower()]
                
                for game, data in game_items:
                    # Get feed URL (check custom feeds first, then built-in)
                    feed_url = custom_feeds.get(game) or GAME_FEEDS.get(game)
                    if not feed_url:
                        continue
                        
                    target = None
                    forum = None
                    forum_thread = None
                    
                    if data.get("forum"):
                        forum = guild.get_channel(data["forum"])
                    elif data.get("forum_thread"):
                        # Get the thread in a forum channel
                        forum_thread = guild.get_thread(data["forum_thread"])
                    elif data.get("thread"):
                        target = guild.get_thread(data["thread"])
                    elif data.get("channel"):
                        target = guild.get_channel(data["channel"])
                    
                    if not target and not forum and not forum_thread:
                        continue
                        
                    updates = await self.fetch_patch_notes(feed_url, game)
                    if not updates:
                        continue
                    
                    if force_post:
                        # When forcing, just post the latest update
                        update = updates[0]
                        embed = discord.Embed(
                            title=update["title"][:256],  # Discord embed title limit
                            description=update["content"][:4000] if len(update["content"]) <= 4000 else update["content"][:3997] + "...",
                            url=update["url"],
                            color=discord.Color.blue()
                        )
                        # Try to parse date, fallback if not possible
                        try:
                            embed.timestamp = discord.utils.parse_time(update["date"])
                        except Exception:
                            pass
                        try:
                            if forum:
                                # For forums, create a new thread with the game name + patch version
                                version_str = self._extract_version_from_title(update["title"])
                                thread_name = f"[{game.upper()}] {version_str}"
                                
                                # Check for duplicate threads before creating a new one
                                if await self._is_duplicate_thread(forum, thread_name):
                                    print(f"Duplicate thread found: {thread_name}. Skipping creation.")
                                    continue  # Skip to the next update
                                
                                await forum.create_thread(
                                    name=thread_name[:100],  # Discord has a 100 character limit for thread names
                                    content=update["content"][:2000] if len(update["content"]) <= 2000 else update["content"][:1997] + "...",
                                    embed=embed
                                )
                            elif forum_thread or target:
                                # For existing threads or channels, just send the message without changing the name
                                target_to_send = forum_thread if forum_thread else target
                                await target_to_send.send(embed=embed)
                            
                            # Update the last_update field
                            data["last_update"] = update["id"]
                            games[game] = data
                            await self.config.guild(guild).games.set(games)
                        except Exception as e:
                            print(f"Error sending update for {game} in {guild.name}: {e}")
                    else:
                        # Normal update checking
                        last_update = data.get("last_update")
                        new_updates = []
                        for update in updates:
                            if update["id"] == last_update:
                                break
                            new_updates.append(update)
                            
                        if new_updates:
                            for update in reversed(new_updates):
                                embed = discord.Embed(
                                    title=update["title"][:256],  # Discord embed title limit
                                    description=update["content"][:4000] if len(update["content"]) <= 4000 else update["content"][:3997] + "...",
                                    url=update["url"],
                                    color=discord.Color.blue()
                                )
                                # Try to parse date, fallback if not possible
                                try:
                                    embed.timestamp = discord.utils.parse_time(update["date"])
                                except Exception:
                                    pass
                                try:
                                    if forum:
                                        # For forums, create a new thread with the game name + patch version
                                        version_str = self._extract_version_from_title(update["title"])
                                        thread_name = f"[{game.upper()}] {version_str}"
                                        
                                        # Check for duplicate threads before creating a new one
                                        if await self._is_duplicate_thread(forum, thread_name):
                                            print(f"Duplicate thread found: {thread_name}. Skipping creation.")
                                            continue  # Skip to the next update
                                        
                                        await forum.create_thread(
                                            name=thread_name[:100],  # Discord has a 100 character limit for thread names
                                            content=update["content"][:2000] if len(update["content"]) <= 2000 else update["content"][:1997] + "...",
                                            embed=embed
                                        )
                                    elif forum_thread or target:
                                        # For existing threads or channels, just send the message without changing the name
                                        target_to_send = forum_thread if forum_thread else target
                                        await target_to_send.send(embed=embed)
                                except Exception as e:
                                    print(f"Error sending update for {game} in {guild.name}: {e}")
                                    continue
                            # Save the latest update id
                            data["last_update"] = updates[0]["id"]
                            games[game] = data
                            await self.config.guild(guild).games.set(games)
            except Exception as e:
                print(f"Error processing guild {guild.name}: {e}")

    @commands.group(name="gameupdates", aliases=["gu"])
    @commands.guild_only()
    async def gameupdates(self, ctx):
        """Game patch notes setup."""
        pass

    @gameupdates.command(name="gusetupall")
    @commands.admin_or_permissions(manage_guild=True)
    async def setupall(self, ctx, channel_or_thread_or_forum: discord.abc.GuildChannel):
        """
        Set up ALL available games to post updates in a channel, thread, or forum.
        
        This will configure every built-in and custom game to post in the specified destination.
        Use with caution as this could result in many updates being posted.
        
        Example: [p]gameupdates gusetupall #game-updates
        """
        custom_feeds = await self.config.guild(ctx.guild).custom_feeds()
        games = await self.config.guild(ctx.guild).games()
        
        # Get all available games (built-in + custom)
        all_games = list(GAME_FEEDS.keys()) + list(custom_feeds.keys())
        all_games = sorted(set(all_games))  # Remove duplicates and sort
        
        if not all_games:
            await ctx.send("No games available to set up.")
            return
        
        # Determine the type of channel provided
        if isinstance(channel_or_thread_or_forum, discord.TextChannel):
            target_type = "channel"
            target_id = channel_or_thread_or_forum.id
            target_dict = {"channel": target_id, "thread": None, "forum": None, "forum_thread": None, "last_update": None}
        elif isinstance(channel_or_thread_or_forum, discord.Thread):
            target_type = "thread"
            target_id = channel_or_thread_or_forum.id
            target_dict = {"channel": None, "thread": target_id, "forum": None, "forum_thread": None, "last_update": None}
        elif isinstance(channel_or_thread_or_forum, discord.ForumChannel):
            target_type = "forum"
            target_id = channel_or_thread_or_forum.id
            target_dict = {"channel": None, "thread": None, "forum": target_id, "forum_thread": None, "last_update": None}
        else:
            await ctx.send("Invalid channel type. Please provide a text channel, thread, or forum channel.")
            return
        
        # Confirm with the user before proceeding
        game_count = len(all_games)
        confirm_msg = await ctx.send(
            f"⚠️ **Warning**: You are about to set up **{game_count} games** to post updates in {channel_or_thread_or_forum.mention}.\n"
            f"This could result in many notifications. Are you sure you want to continue?\n\n"
            f"React with ✅ to confirm or ❌ to cancel."
        )
        
        # Add reactions
        await confirm_msg.add_reaction("✅")
        await confirm_msg.add_reaction("❌")
        
        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["✅", "❌"] and reaction.message.id == confirm_msg.id
        
        try:
            reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)
            
            if str(reaction.emoji) == "❌":
                await ctx.send("Setup cancelled.")
                return
                
        except asyncio.TimeoutError:
            await ctx.send("Setup timed out. No changes were made.")
            return
        
        # Process setup for all games
        setup_count = 0
        
        # Create a progress message
        progress_msg = await ctx.send(f"Setting up games... (0/{game_count})")
        
        for i, game_name in enumerate(all_games):
            # Update every 5 games to avoid rate limits
            if i % 5 == 0 and i > 0:
                await progress_msg.edit(content=f"Setting up games... ({i}/{game_count})")
            
            games[game_name] = target_dict.copy()  # Use a copy to avoid reference issues
            setup_count += 1
        
        # Save the updated games configuration
        await self.config.guild(ctx.guild).games.set(games)
        
        # Final update
        await progress_msg.edit(content=f"Setting up games... ({game_count}/{game_count}) ✅")
        
        # Send completion message
        await ctx.send(
            f"✅ Successfully set up **{setup_count} games** to post updates in the {target_type} {channel_or_thread_or_forum.mention}.\n"
            f"You can use `{ctx.prefix}gameupdates list` to see all configured games."
        )

    @gameupdates.command()
    @commands.is_owner()
    async def forceupdate(self, ctx, game_name: GameConverter = None):
        """
        Force check for updates now (bot owner only).
        
        If a game name is provided, only that game's updates will be posted.
        The latest patch notes will always be posted, even if they've been posted before.
        
        Example: [p]gameupdates forceupdate "Squad"
        You can also mention a user to use their name as the game name: [p]gameupdates forceupdate @Squad
        """
        if game_name:
            # Check if the game exists in any feed
            custom_feeds = await self.config.guild(ctx.guild).custom_feeds()
            if game_name.lower() not in GAME_FEEDS and game_name.lower() not in custom_feeds:
                await ctx.send(f"Game **{game_name}** not found. Use `[p]gameupdates listgames` to see available games.")
                return
                
            # Check if the game is set up in this guild
            games = await self.config.guild(ctx.guild).games()
            if game_name.lower() not in games:
                await ctx.send(f"Game **{game_name}** is not set up in this server. Use `[p]gameupdates setup` to set it up first.")
                return
                
            await ctx.send(f"Checking for **{game_name}** updates...")
            try:
                await self._check_for_updates(specific_game=game_name.lower(), specific_guild=ctx.guild, force_post=True)
                await ctx.send(f"Posted the latest update for **{game_name}**.")
            except Exception as e:
                await ctx.send(f"Error during update check: {str(e)}")
        else:
            await ctx.send("Checking for all game updates...")
            try:
                await self._check_for_updates(specific_guild=ctx.guild, force_post=True)
                await ctx.send("Update check completed and latest updates posted.")
            except Exception as e:
                await ctx.send(f"Error during update check: {str(e)}")

    @gameupdates.command()
    @commands.is_owner()
    async def addgame(self, ctx, game_name: GameConverter, feed_url: str):
        """
        Add a game to the global games list.
        
        Example: [p]gameupdates addgame "Minecraft" https://feedback.minecraft.net/rss
        You can also mention a user to use their name as the game name: [p]gameupdates addgame @Minecraft https://...
        """
        global GAME_FEEDS  # Declare global before using it
        game_name = game_name.lower()
        
        # Check if game already exists
        if game_name in GAME_FEEDS:
            await ctx.send(f"Game '{game_name}' already exists in the list.")
            return
        
        # Validate URL format
        if not feed_url.startswith(("http://", "https://")):
            await ctx.send("Invalid URL. Please provide a valid RSS feed URL starting with http:// or https://")
            return
        
        # Validate RSS feed
        try:
            loop = asyncio.get_event_loop()
            feed = await loop.run_in_executor(None, feedparser.parse, feed_url)
            if not hasattr(feed, 'entries') or len(feed.entries) == 0:
                await ctx.send("This URL doesn't appear to be a valid RSS feed.")
                return
        except Exception as e:
            await ctx.send(f"Error validating feed: {str(e)}")
            return
        
        # Add to GAME_FEEDS
        GAME_FEEDS[game_name] = feed_url
        
        # Save to permanent_games for persistence
        permanent_games = await self.config.permanent_games()
        permanent_games[game_name] = feed_url
        await self.config.permanent_games.set(permanent_games)
        
        await ctx.send(f"Added '{game_name}' to the games list.")

    @gameupdates.command()
    @commands.is_owner()
    async def removepermanent(self, ctx, game_name: str):
        """
        Remove a game from the permanent built-in games list.
        Only bot owner can use this command.
        
        Example: [p]gameupdates removepermanent "Minecraft"
        """
        global GAME_FEEDS  # Declare global before using it
        game_name = game_name.lower()
        
        # Check if it's in the original hardcoded list
        original_games = {
            "squad", "hell let loose", "delta force", "arma 3", "arma reforger",
            "escape from tarkov", "overwatch", "overwatch 2", "marvel rivals",
            "valorant", "fragpunk", "helldivers 2", "gta online", "battlefield 2042",
            "battlefield v", "battlefield 1", "battlefield 6"
        }
        
        if game_name in original_games:
            await ctx.send(f"**{game_name.title()}** is part of the original hardcoded games list and cannot be removed.")
            return
        
        # Check if it's in permanent games
        permanent_games = await self.config.permanent_games()
        if game_name not in permanent_games:
            await ctx.send(f"**{game_name.title()}** is not in the permanent games list.")
            return
            
        # Remove from permanent games
        del permanent_games[game_name]
        await self.config.permanent_games.set(permanent_games)
        
        # Also remove from current GAME_FEEDS
        if game_name in GAME_FEEDS:
            del GAME_FEEDS[game_name]
        
        await ctx.send(f"Removed **{game_name.title()}** from the permanent games list.")

    @gameupdates.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def addcustomgame(self, ctx, game_name: GameConverter, feed_url: str):
        """
        Add a custom game and its RSS feed URL for this server only.
        
        Example: [p]gameupdates addcustomgame "My Game" https://example.com/feed.rss
        You can also mention a user to use their name as the game name: [p]gameupdates addcustomgame @GameName https://...
        """
        game_name = game_name.lower()
        
        # Validate the URL format (basic check)
        if not feed_url.startswith(("http://", "https://")):
            await ctx.send("Invalid URL. Please provide a valid RSS feed URL starting with http:// or https://")
            return
            
        # Check if it's a valid RSS feed
        try:
            loop = asyncio.get_event_loop()
            feed = await loop.run_in_executor(None, feedparser.parse, feed_url)
            if not hasattr(feed, 'entries') or len(feed.entries) == 0:
                await ctx.send("This URL doesn't appear to be a valid RSS feed. Please check the URL and try again.")
                return
        except Exception as e:
            await ctx.send(f"Error validating feed: {str(e)}")
            return
            
        # Add to custom feeds
        custom_feeds = await self.config.guild(ctx.guild).custom_feeds()
        custom_feeds[game_name] = feed_url
        await self.config.guild(ctx.guild).custom_feeds.set(custom_feeds)
        
        await ctx.send(f"Added **{game_name}** to custom games. You can now set up channels for it using the other commands.")

    @gameupdates.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def removegame(self, ctx, game_name: GameConverter):
        """
        Remove a custom game from your list.
        
        Example: [p]gameupdates removegame "My Game"
        You can also mention a user to use their name as the game name: [p]gameupdates removegame @GameName
        """
        game_name = game_name.lower()
        
        # Check if it's a built-in game
        if game_name in GAME_FEEDS:
            await ctx.send(f"**{game_name.title()}** is a built-in game and cannot be removed with this command. Use `removepermanent` if you're the bot owner.")
            return
            
        # Check if it's in custom games
        custom_feeds = await self.config.guild(ctx.guild).custom_feeds()
        if game_name not in custom_feeds:
            await ctx.send(f"**{game_name.title()}** is not in your custom games list.")
            return
            
        # Remove from custom feeds
        del custom_feeds[game_name]
        await self.config.guild(ctx.guild).custom_feeds.set(custom_feeds)
        
        # Also remove any channel settings for this game
        games = await self.config.guild(ctx.guild).games()
        if game_name in games:
            del games[game_name]
            await self.config.guild(ctx.guild).games.set(games)
        
        await ctx.send(f"Removed **{game_name.title()}** from your custom games list.")

    @gameupdates.command()
    async def listgames(self, ctx):
        """List all available games (built-in and custom)."""
        custom_feeds = await self.config.guild(ctx.guild).custom_feeds()
        permanent_games = await self.config.permanent_games()
        
        # Combine built-in and custom games
        all_games = list(GAME_FEEDS.keys()) + list(custom_feeds.keys())
        all_games = sorted(set(all_games))  # Remove duplicates and sort
        
        if not all_games:
            await ctx.send("No games available.")
            return
            
        # Format the list with custom games marked
        msg = "**Available Games:**\n"
        for game in all_games:
            if game in custom_feeds:
                msg += f"• {game.title()} (custom)\n"
            elif game in permanent_games:
                msg += f"• {game.title()} (added by owner)\n"
            else:
                msg += f"• {game.title()}\n"
                
        # Split into multiple messages if too long
        if len(msg) > 1900:
            parts = []
            current_part = "**Available Games:**\n"
            for game in all_games:
                line = f"• {game.title()}"
                if game in custom_feeds:
                    line += " (custom)"
                elif game in permanent_games:
                    line += " (added by owner)"
                line += "\n"
                
                if len(current_part) + len(line) > 1900:
                    parts.append(current_part)
                    current_part = line
                else:
                    current_part += line
            
            # Add the last part if it has content
            if current_part:
                parts.append(current_part)
                
            # Send each part as a separate message
            for i, part in enumerate(parts):
                await ctx.send(f"{part}\n*Page {i+1}/{len(parts)}*")
        else:
            await ctx.send(msg)

    @gameupdates.command()
    async def list(self, ctx):
        """List all games that are set up to post updates in this server."""
        games = await self.config.guild(ctx.guild).games()
        
        if not games:
            await ctx.send("No games are set up to post updates in this server.")
            return
            
        # Format the list with channel/thread info
        msg = "**Games Set Up in This Server:**\n"
        for game, data in sorted(games.items()):
            target_info = []
            
            if data.get("channel"):
                channel = ctx.guild.get_channel(data["channel"])
                if channel:
                    target_info.append(f"Channel: {channel.mention}")
                    
            if data.get("thread"):
                thread = ctx.guild.get_thread(data["thread"])
                if thread:
                    target_info.append(f"Thread: {thread.mention}")
                    
            if data.get("forum"):
                forum = ctx.guild.get_channel(data["forum"])
                if forum:
                    target_info.append(f"Forum: {forum.mention}")
                    
            if data.get("forum_thread"):
                forum_thread = ctx.guild.get_thread(data["forum_thread"])
                if forum_thread:
                    target_info.append(f"Forum Thread: {forum_thread.mention}")
                    
            target_str = " | ".join(target_info) if target_info else "No valid target"
            msg += f"• **{game.title()}** → {target_str}\n"
                
        # Split into multiple messages if too long
        if len(msg) > 1900:
            parts = []
            current_part = "**Games Set Up in This Server:**\n"
            for game, data in sorted(games.items()):
                target_info = []
                
                if data.get("channel"):
                    channel = ctx.guild.get_channel(data["channel"])
                    if channel:
                        target_info.append(f"Channel: {channel.mention}")
                        
                if data.get("thread"):
                    thread = ctx.guild.get_thread(data["thread"])
                    if thread:
                        target_info.append(f"Thread: {thread.mention}")
                        
                if data.get("forum"):
                    forum = ctx.guild.get_channel(data["forum"])
                    if forum:
                        target_info.append(f"Forum: {forum.mention}")
                        
                if data.get("forum_thread"):
                    forum_thread = ctx.guild.get_thread(data["forum_thread"])
                    if forum_thread:
                        target_info.append(f"Forum Thread: {forum_thread.mention}")
                        
                target_str = " | ".join(target_info) if target_info else "No valid target"
                line = f"• **{game.title()}** → {target_str}\n"
                
                if len(current_part) + len(line) > 1900:
                    parts.append(current_part)
                    current_part = line
                else:
                    current_part += line
            
            if current_part:
                parts.append(current_part)
                
            for i, part in enumerate(parts):
                await ctx.send(f"{part}\n*Page {i+1}/{len(parts)}*")
        else:
            await ctx.send(msg)

    @gameupdates.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def setup(self, ctx, game_name: GameConverter, channel_or_thread_or_forum: discord.abc.GuildChannel):
        """
        Set up a game to post updates in a channel, thread, or forum.
        
        Example: [p]gameupdates setup "Squad" #game-updates
        You can also mention a user to use their name as the game name: [p]gameupdates setup @Squad #game-updates
        """
        game_name = game_name.lower()
        
        # Check if the game exists in any feed
        custom_feeds = await self.config.guild(ctx.guild).custom_feeds()
        if game_name not in GAME_FEEDS and game_name not in custom_feeds:
            await ctx.send(f"Game **{game_name}** not found. Use `{ctx.prefix}gameupdates listgames` to see available games or add a custom game.")
            return
            
        # Determine the type of channel provided
        if isinstance(channel_or_thread_or_forum, discord.TextChannel):
            target_type = "channel"
            target_id = channel_or_thread_or_forum.id
            target_dict = {"channel": target_id, "thread": None, "forum": None, "forum_thread": None, "last_update": None}
        elif isinstance(channel_or_thread_or_forum, discord.Thread):
            target_type = "thread"
            target_id = channel_or_thread_or_forum.id
            target_dict = {"channel": None, "thread": target_id, "forum": None, "forum_thread": None, "last_update": None}
        elif isinstance(channel_or_thread_or_forum, discord.ForumChannel):
            target_type = "forum"
            target_id = channel_or_thread_or_forum.id
            target_dict = {"channel": None, "thread": None, "forum": target_id, "forum_thread": None, "last_update": None}
        else:
            await ctx.send("Invalid channel type. Please provide a text channel, thread, or forum channel.")
            return
            
        # Save the configuration
        games = await self.config.guild(ctx.guild).games()
        games[game_name] = target_dict
        await self.config.guild(ctx.guild).games.set(games)
        
        await ctx.send(f"✅ **{game_name.title()}** will now post updates in the {target_type} {channel_or_thread_or_forum.mention}.")

    @gameupdates.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def remove(self, ctx, game_name: GameConverter):
        """
        Remove a game from posting updates in this server.
        
        Example: [p]gameupdates remove "Squad"
        You can also mention a user to use their name as the game name: [p]gameupdates remove @Squad
        """
        game_name = game_name.lower()
        
        # Check if the game is set up
        games = await self.config.guild(ctx.guild).games()
        if game_name not in games:
            await ctx.send(f"Game **{game_name}** is not set up in this server.")
            return
            
        # Remove the game
        del games[game_name]
        await self.config.guild(ctx.guild).games.set(games)
        
        await ctx.send(f"✅ **{game_name.title()}** will no longer post updates in this server.")

    @gameupdates.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def check(self, ctx, game_name: GameConverter = None):
        """
        Check for updates now.
        
        If a game name is provided, only that game's updates will be checked.
        
        Example: [p]gameupdates check "Squad"
        You can also mention a user to use their name as the game name: [p]gameupdates check @Squad
        """
        if game_name:
            # Check if the game exists in any feed
            custom_feeds = await self.config.guild(ctx.guild).custom_feeds()
            if game_name.lower() not in GAME_FEEDS and game_name.lower() not in custom_feeds:
                await ctx.send(f"Game **{game_name}** not found. Use `{ctx.prefix}gameupdates listgames` to see available games.")
                return
                
            # Check if the game is set up in this guild
            games = await self.config.guild(ctx.guild).games()
            if game_name.lower() not in games:
                await ctx.send(f"Game **{game_name}** is not set up in this server. Use `{ctx.prefix}gameupdates setup` to set it up first.")
                return
                
            await ctx.send(f"Checking for **{game_name}** updates...")
            try:
                await self._check_for_updates(specific_game=game_name.lower(), specific_guild=ctx.guild)
                await ctx.send(f"Update check for **{game_name}** completed.")
            except Exception as e:
                await ctx.send(f"Error during update check: {str(e)}")
        else:
            await ctx.send("Checking for all game updates...")
            try:
                await self._check_for_updates(specific_guild=ctx.guild)
                await ctx.send("Update check completed.")
            except Exception as e:
                await ctx.send(f"Error during update check: {str(e)}")

    @gameupdates.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def setforumthread(self, ctx, game_name: GameConverter, forum_thread: discord.Thread):
        """
        Set a specific forum thread for a game's updates.
        
        This is useful if you want all updates for a game to go into a single thread within a forum.
        
        Example: [p]gameupdates setforumthread "Squad" #squad-discussion
        You can also mention a user to use their name as the game name: [p]gameupdates setforumthread @Squad #squad-discussion
        """
        game_name = game_name.lower()
        custom_feeds = await self.config.guild(ctx.guild).custom_feeds()
        
        # Check if the game exists in any feed
        if game_name not in GAME_FEEDS and game_name not in custom_feeds:
            await ctx.send(f"Game **{game_name}** not found. Use `{ctx.prefix}gameupdates listgames` to see available games.")
            return
        
        # Check if the provided thread is actually a forum thread
        if not isinstance(forum_thread.parent, discord.ForumChannel):
            await ctx.send("The provided thread is not a forum thread. Please provide a thread that belongs to a forum channel.")
            return
        
        # Save the forum thread information to the config
        games = await self.config.guild(ctx.guild).games()
        
        # Update the game's settings to only use the forum_thread
        games[game_name] = {"channel": None, "thread": None, "forum": None, "forum_thread": forum_thread.id, "last_update": None}
        
        await self.config.guild(ctx.guild).games.set(games)
        
        await ctx.send(f"Set **{game_name}** to post updates in the forum thread {forum_thread.mention}.")

    @gameupdates.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def clearforumthread(self, ctx, game_name: GameConverter):
        """
        Clear the forum thread setting for a game, reverting to creating new threads for each update.
        
        Example: [p]gameupdates clearforumthread "Squad"
        You can also mention a user to use their name as the game name: [p]gameupdates clearforumthread @Squad
        """
        game_name = game_name.lower()
        
        # Check if the game is being tracked
        games = await self.config.guild(ctx.guild).games()
        if game_name not in games:
            await ctx.send(f"**{game_name}** is not currently set up to post updates in this server.")
            return
        
        # Check if the game has a forum_thread set
        if games[game_name].get("forum_thread") is None:
            await ctx.send(f"**{game_name}** is not currently set to post updates in a specific forum thread.")
            return
        
        # Clear the forum_thread setting, reverting to creating new threads
        games[game_name]["forum_thread"] = None
        await self.config.guild(ctx.guild).games.set(games)
        
        await ctx.send(f"Cleared the forum thread setting for **{game_name}**. New updates will now be posted in new threads.")

def setup(bot):
    bot.add_cog(GameUpdates(bot))
