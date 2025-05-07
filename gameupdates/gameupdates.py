import discord
from redbot.core import commands, Config, tasks
import feedparser
import asyncio

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

class GameUpdates(commands.Cog):
    """Fetch and post patch notes for many games to channels or threads."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567894)
        default_guild = {
            "games": {}  # {game_name: {"channel": channel_id, "thread": thread_id, "last_update": update_id}}
        }
        self.config.register_guild(**default_guild)
        self.update_loop.start()

    def cog_unload(self):
        self.update_loop.cancel()

    @commands.group()
    @commands.guild_only()
    async def gameupdates(self, ctx):
        """Game patch notes setup."""
        pass

    @gameupdates.command()
    async def addchannel(self, ctx, game: str, channel: discord.TextChannel):
        """Set a channel for patch notes (game name required)."""
        game = game.lower()
        if game not in GAME_FEEDS:
            await ctx.send("Game not supported. Supported games: " + ", ".join(GAME_FEEDS.keys()))
            return
        games = await self.config.guild(ctx.guild).games()
        games[game] = {"channel": channel.id, "thread": None, "last_update": None}
        await self.config.guild(ctx.guild).games.set(games)
        await ctx.send(f"Patch notes for **{game.title()}** will be posted in {channel.mention}.")

    @gameupdates.command()
    async def addthread(self, ctx, game: str, thread: discord.Thread):
        """Set a thread for patch notes (game name required)."""
        game = game.lower()
        if game not in GAME_FEEDS:
            await ctx.send("Game not supported. Supported games: " + ", ".join(GAME_FEEDS.keys()))
            return
        games = await self.config.guild(ctx.guild).games()
        games[game] = {"channel": None, "thread": thread.id, "last_update": None}
        await self.config.guild(ctx.guild).games.set(games)
        await ctx.send(f"Patch notes for **{game.title()}** will be posted in thread {thread.mention}.")

    @gameupdates.command()
    async def remove(self, ctx, game: str):
        """Remove a game from patch notes posting."""
        game = game.lower()
        games = await self.config.guild(ctx.guild).games()
        if game in games:
            del games[game]
            await self.config.guild(ctx.guild).games.set(games)
            await ctx.send(f"Removed **{game.title()}** from updates.")
        else:
            await ctx.send("Game not found.")

    @gameupdates.command()
    async def list(self, ctx):
        """List all games and their channels/threads."""
        games = await self.config.guild(ctx.guild).games()
        if not games:
            await ctx.send("No games set up.")
            return
        msg = ""
        for g, d in games.items():
            if d["thread"]:
                thread = ctx.guild.get_thread(d["thread"])
                loc = thread.mention if thread else f"Thread ID {d['thread']}"
            elif d["channel"]:
                channel = ctx.guild.get_channel(d["channel"])
                loc = channel.mention if channel else f"Channel ID {d['channel']}"
            else:
                loc = "Not set"
            msg += f"**{g.title()}**: {loc}\n"
        await ctx.send(msg)

    @tasks.loop(minutes=10)
    async def update_loop(self):
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            games = await self.config.guild(guild).games()
            for game, data in games.items():
                feed_url = GAME_FEEDS.get(game)
                if not feed_url:
                    continue
                target = None
                if data.get("thread"):
                    target = guild.get_thread(data["thread"])
                elif data.get("channel"):
                    target = guild.get_channel(data["channel"])
                if not target:
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
                            await target.send(embed=embed)
                        except Exception:
                            pass
                    # Save the latest update id
                    data["last_update"] = updates[0]["id"]
                    games[game] = data
                    await self.config.guild(guild).games.set(games)

    @update_loop.before_loop
    async def before_update_loop(self):
        await self.bot.wait_until_ready()

    async def fetch_patch_notes(self, url, game):
        loop = asyncio.get_event_loop()
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

def setup(bot):
    bot.add_cog(GameUpdates(bot))
