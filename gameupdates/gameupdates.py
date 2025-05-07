import discord
from redbot.core import commands, Config
import feedparser
import asyncio
import re

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
    
    async def _check_for_updates(self):
        """Check for updates for all games in all guilds."""
        for guild in self.bot.guilds:
            try:
                games = await self.config.guild(guild).games()
                custom_feeds = await self.config.guild(guild).custom_feeds()
                
                for game, data in games.items():
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
                        
                    last_update = data.get("last_update")
                    new_updates = []
                    for update in updates:
                        if update["id"] == last_update:
                            break
                        new_updates.append(update)
                        
                    if new_updates:
                        for update in reversed(new_updates):
                            embed = discord.Embed(
                                title=update["content"].split('\n')[0],
                                description='\n'.join(update["content"].split('\n')[1:]),
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
                                    await forum.create_thread(
                                        name=embed.title[:100] if embed.title else "Patch Notes",
                                        content=embed.description or "Patch notes update",
                                        embed=embed
                                    )
                                elif forum_thread:
                                    await forum_thread.send(embed=embed)
                                elif target:
                                    await target.send(embed=embed)
                            except Exception as e:
                                print(f"Error sending update for {game} in {guild.name}: {e}")
                                continue
                        # Save the latest update id
                        data["last_update"] = updates[0]["id"]
                        games[game] = data
                        await self.config.guild(guild).games.set(games)
            except Exception as e:
                print(f"Error processing guild {guild.name}: {e}")

    async def fetch_patch_notes(self, url, game):
        """Fetch and parse patch notes from an RSS feed."""
        loop = asyncio.get_event_loop()
        try:
            feed = await loop.run_in_executor(None, feedparser.parse, url)
            updates = []
            for entry in feed.entries:
                # Try to filter for patch/update notes
                if any(word in entry.title.lower() for word in ("patch", "update", "notes", "hotfix", "changelog")):
                    updates.append({
                        "id": getattr(entry, "id", getattr(entry, "link", None)),
                        "content": f"**{entry.title}**\n\n{getattr(entry, 'summary', '')}",
                        "date": getattr(entry, "published", getattr(entry, "updated", None)),
                        "url": getattr(entry, "link", None)
                    })
            return updates
        except Exception as e:
            print(f"Error fetching updates for {game}: {e}")
            return []

    @commands.group()
    @commands.guild_only()
    async def gameupdates(self, ctx):
        """Game patch notes setup."""
        pass

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
            "valorant", "fragpunk", "helldivers 2", "gta online"
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
            
            if current_part:
                parts.append(current_part)
                
            for part in parts:
                await ctx.send(part)
        else:
            await ctx.send(msg)

    @gameupdates.command()
    async def setup(self, ctx, game_name: GameConverter, channel_or_thread_or_forum: discord.abc.GuildChannel):
        """
        Set up a game to post updates in a channel, thread, or forum.
        
        Example: [p]gameupdates setup "Squad" #squad-updates
        You can also mention a user to use their name as the game name: [p]gameupdates setup @Squad #squad-updates
        """
        game_name = game_name.lower()
        custom_feeds = await self.config.guild(ctx.guild).custom_feeds()
        
        # Check if game exists (either built-in or custom)
        if game_name not in GAME_FEEDS and game_name not in custom_feeds:
            await ctx.send("Game not supported. Use `[p]gameupdates listgames` to see available games.")
            return
            
        games = await self.config.guild(ctx.guild).games()
        
        # Determine the type of channel provided
        if isinstance(channel_or_thread_or_forum, discord.TextChannel):
            games[game_name] = {"channel": channel_or_thread_or_forum.id, "thread": None, "forum": None, "forum_thread": None, "last_update": None}
            target_type = "channel"
        elif isinstance(channel_or_thread_or_forum, discord.Thread):
            games[game_name] = {"channel": None, "thread": channel_or_thread_or_forum.id, "forum": None, "forum_thread": None, "last_update": None}
            target_type = "thread"
        elif isinstance(channel_or_thread_or_forum, discord.ForumChannel):
            games[game_name] = {"channel": None, "thread": None, "forum": channel_or_thread_or_forum.id, "forum_thread": None, "last_update": None}
            target_type = "forum"
        else:
            await ctx.send("Invalid channel type. Please provide a text channel, thread, or forum channel.")
            return
            
        await self.config.guild(ctx.guild).games.set(games)
        await ctx.send(f"Patch notes for **{game_name.title()}** will be posted in the {target_type} {channel_or_thread_or_forum.mention}.")

    @gameupdates.command()
    async def addchannel(self, ctx, game_name: GameConverter, channel: discord.TextChannel):
        """
        Set a channel for patch notes.
        
        Example: [p]gameupdates addchannel "Squad" #squad-updates
        You can also mention a user to use their name as the game name: [p]gameupdates addchannel @Squad #squad-updates
        """
        game_name = game_name.lower()
        custom_feeds = await self.config.guild(ctx.guild).custom_feeds()
        
        # Check if game exists (either built-in or custom)
        if game_name not in GAME_FEEDS and game_name not in custom_feeds:
            await ctx.send("Game not supported. Use `[p]gameupdates listgames` to see available games.")
            return
            
        games = await self.config.guild(ctx.guild).games()
        games[game_name] = {"channel": channel.id, "thread": None, "forum": None, "forum_thread": None, "last_update": None}
        await self.config.guild(ctx.guild).games.set(games)
        await ctx.send(f"Patch notes for **{game_name.title()}** will be posted in {channel.mention}.")

    @gameupdates.command()
    async def addthread(self, ctx, game_name: GameConverter, thread: discord.Thread):
        """
        Set a thread for patch notes.
        
        Example: [p]gameupdates addthread "Squad" #squad-thread
        You can also mention a user to use their name as the game name: [p]gameupdates addthread @Squad #squad-thread
        """
        game_name = game_name.lower()
        custom_feeds = await self.config.guild(ctx.guild).custom_feeds()
        
        # Check if game exists (either built-in or custom)
        if game_name not in GAME_FEEDS and game_name not in custom_feeds:
            await ctx.send("Game not supported. Use `[p]gameupdates listgames` to see available games.")
            return
            
        games = await self.config.guild(ctx.guild).games()
        games[game_name] = {"channel": None, "thread": thread.id, "forum": None, "forum_thread": None, "last_update": None}
        await self.config.guild(ctx.guild).games.set(games)
        await ctx.send(f"Patch notes for **{game_name.title()}** will be posted in thread {thread.mention}.")

    @gameupdates.command()
    async def addforum(self, ctx, game_name: GameConverter, forum: discord.ForumChannel):
        """
        Set a forum channel for patch notes.
        
        Example: [p]gameupdates addforum "Squad" #squad-forum
        You can also mention a user to use their name as the game name: [p]gameupdates addforum @Squad #squad-forum
        """
        game_name = game_name.lower()
        custom_feeds = await self.config.guild(ctx.guild).custom_feeds()
        
        # Check if game exists (either built-in or custom)
        if game_name not in GAME_FEEDS and game_name not in custom_feeds:
            await ctx.send("Game not supported. Use `[p]gameupdates listgames` to see available games.")
            return
            
        games = await self.config.guild(ctx.guild).games()
        games[game_name] = {"channel": None, "thread": None, "forum": forum.id, "forum_thread": None, "last_update": None}
        await self.config.guild(ctx.guild).games.set(games)
        await ctx.send(f"Patch notes for **{game_name.title()}** will be posted as new posts in forum {forum.mention}.")

    @gameupdates.command()
    @commands.has_guild_permissions(manage_channels=True)
    async def createforumthread(self, ctx, game_name: GameConverter, forum: discord.ForumChannel, thread_name: str = None):
        """
        Create a thread in a forum channel for patch notes and set it up.
        
        Example: [p]gameupdates createforumthread "Squad" #squad-forum "Squad Patch Notes"
        You can also mention a user to use their name as the game name: [p]gameupdates createforumthread @Squad #squad-forum
        """
        game_name = game_name.lower()
        custom_feeds = await self.config.guild(ctx.guild).custom_feeds()
        
        # Check if game exists (either built-in or custom)
        if game_name not in GAME_FEEDS and game_name not in custom_feeds:
            await ctx.send("Game not supported. Use `[p]gameupdates listgames` to see available games.")
            return

        # Default thread name if not provided
        if not thread_name:
            thread_name = f"{game_name.title()} Patch Notes"

        try:
            # Create the thread in the forum
            thread = await forum.create_thread(
                name=thread_name,
                content=f"This thread will be used for **{game_name.title()}** patch notes.",
                auto_archive_duration=10080  # 7 days
            )
            
            # Set up the game to use this forum thread
            games = await self.config.guild(ctx.guild).games()
            games[game_name] = {"channel": None, "thread": None, "forum": None, "forum_thread": thread.thread.id, "last_update": None}
            await self.config.guild(ctx.guild).games.set(games)
            
            await ctx.send(f"Created thread {thread.thread.mention} in forum {forum.mention} and set it up for **{game_name.title()}** patch notes.")
        except discord.Forbidden:
            await ctx.send("I don't have permission to create threads in that forum.")
        except Exception as e:
            await ctx.send(f"Error creating forum thread: {str(e)}")

    @gameupdates.command()
    @commands.has_guild_permissions(manage_channels=True)
    async def createchannel(self, ctx, game_name: GameConverter, channel_name: str = None, category: discord.CategoryChannel = None):
        """
        Create a new text channel for a game's patch notes and set it up.
        Optionally specify a channel name and category.
        
        Example: [p]gameupdates createchannel "Squad" squad-patch-notes
        You can also mention a user to use their name as the game name: [p]gameupdates createchannel @Squad squad-patch-notes
        """
        game_name = game_name.lower()
        custom_feeds = await self.config.guild(ctx.guild).custom_feeds()
        
        # Check if game exists (either built-in or custom)
        if game_name not in GAME_FEEDS and game_name not in custom_feeds:
            await ctx.send("Game not supported. Use `[p]gameupdates listgames` to see available games.")
            return

        # Default channel name if not provided
        if not channel_name:
            channel_name = f"{game_name.replace(' ', '-')}-patch-notes"

        # Create the channel
        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(read_messages=True, send_messages=False)
        }
        try:
            new_channel = await ctx.guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                reason=f"Patch notes channel for {game_name.title()} (requested by {ctx.author})"
            )
        except discord.Forbidden:
            await ctx.send("I do not have permission to create channels.")
            return
        except Exception as e:
            await ctx.send(f"Failed to create channel: {e}")
            return

        # Set up the game to use this channel
        games = await self.config.guild(ctx.guild).games()
        games[game_name] = {"channel": new_channel.id, "thread": None, "forum": None, "forum_thread": None, "last_update": None}
        await self.config.guild(ctx.guild).games.set(games)
        await ctx.send(f"Created {new_channel.mention} and set it up for **{game_name.title()}** patch notes.")

    @gameupdates.command()
    @commands.has_guild_permissions(manage_channels=True)
    async def createforum(self, ctx, game_name: GameConverter, forum_name: str = None, category: discord.CategoryChannel = None):
        """
        Create a new forum channel for a game's patch notes and set it up.
        Optionally specify a forum name and category.
        
        Example: [p]gameupdates createforum "Squad" squad-updates
        You can also mention a user to use their name as the game name: [p]gameupdates createforum @Squad squad-updates
        """
        game_name = game_name.lower()
        custom_feeds = await self.config.guild(ctx.guild).custom_feeds()
        
        # Check if game exists (either built-in or custom)
        if game_name not in GAME_FEEDS and game_name not in custom_feeds:
            await ctx.send("Game not supported. Use `[p]gameupdates listgames` to see available games.")
            return

        # Default forum name if not provided
        if not forum_name:
            forum_name = f"{game_name.replace(' ', '-')}-updates"

        # Create the forum
        try:
            new_forum = await ctx.guild.create_forum(
                name=forum_name,
                category=category,
                reason=f"Patch notes forum for {game_name.title()} (requested by {ctx.author})"
            )
        except discord.Forbidden:
            await ctx.send("I do not have permission to create forum channels.")
            return
        except Exception as e:
            await ctx.send(f"Failed to create forum: {e}")
            return

        # Set up the game to use this forum
        games = await self.config.guild(ctx.guild).games()
        games[game_name] = {"channel": None, "thread": None, "forum": new_forum.id, "forum_thread": None, "last_update": None}
        await self.config.guild(ctx.guild).games.set(games)
        await ctx.send(f"Created forum {new_forum.mention} and set it up for **{game_name.title()}** patch notes.")

    @gameupdates.command()
    async def remove(self, ctx, game_name: GameConverter):
        """
        Remove a game from patch notes posting.
        
        Example: [p]gameupdates remove "Squad"
        You can also mention a user to use their name as the game name: [p]gameupdates remove @Squad
        """
        game_name = game_name.lower()
        games = await self.config.guild(ctx.guild).games()
        if game_name in games:
            del games[game_name]
            await self.config.guild(ctx.guild).games.set(games)
            await ctx.send(f"Removed **{game_name.title()}** from updates.")
        else:
            await ctx.send("Game not found.")

    @gameupdates.command()
    async def list(self, ctx):
        """List all games and their channels/threads/forums."""
        games = await self.config.guild(ctx.guild).games()
        if not games:
            await ctx.send("No games set up.")
            return
        msg = ""
        for g, d in games.items():
            loc = "Not set"
            if d.get("forum"):
                forum = ctx.guild.get_channel(d["forum"])
                loc = forum.mention if forum else f"Forum ID {d['forum']}"
            elif d.get("forum_thread"):
                thread = ctx.guild.get_thread(d["forum_thread"])
                loc = thread.mention if thread else f"Forum Thread ID {d['forum_thread']}"
            elif d.get("thread"):
                thread = ctx.guild.get_thread(d["thread"])
                loc = thread.mention if thread else f"Thread ID {d['thread']}"
            elif d.get("channel"):
                channel = ctx.guild.get_channel(d["channel"])
                loc = channel.mention if channel else f"Channel ID {d['channel']}"
            msg += f"**{g.title()}**: {loc}\n"
        await ctx.send(msg)

    @gameupdates.command()
    @commands.is_owner()
    async def forceupdate(self, ctx):
        """Force check for updates now (bot owner only)."""
        await ctx.send("Checking for game updates...")
        try:
            await self._check_for_updates()
            await ctx.send("Update check completed.")
        except Exception as e:
            await ctx.send(f"Error during update check: {str(e)}")

async def setup(bot):
    """Load the GameUpdates cog."""
    await bot.add_cog(GameUpdates(bot))

