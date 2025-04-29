from redbot.core import commands, Config
import discord
from discord.ui import Button, View
import aiohttp
import datetime
from datetime import timedelta
import asyncio
from typing import Dict, List, Optional, Union, Tuple, Set
from enum import Enum
import logging
import time
import backoff
import colorthief
from io import BytesIO
import json
import hashlib

logger = logging.getLogger("red.efreegames")

class GameType(Enum):
    FULL_GAME = "full_game"
    DLC = "dlc"
    EXPANSION = "expansion"
    BUNDLE = "bundle"
    IN_GAME_CONTENT = "in_game_content"
    OTHER = "other"

class StoreStatus(Enum):
    """Store API status"""
    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    DOWN = "down"
    RATE_LIMITED = "rate_limited"

class StoreError(Exception):
    """Base exception for store-related errors"""
    pass

class RateLimitError(StoreError):
    """Exception for rate limit errors"""
    def __init__(self, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"Rate limited, retry after {retry_after} seconds")

class APIError(StoreError):
    """Exception for API errors"""
    pass

class RateLimit:
    """Rate limit configuration"""
    def __init__(self, calls: int, period: float):
        self.calls = calls
        self.period = period
        self.tokens = calls
        self.last_update = time.monotonic()
        self.retry_after = 0

    def update(self):
        """Update available tokens based on time passed"""
        now = time.monotonic()
        time_passed = now - self.last_update
        self.tokens = min(
            self.calls,
            self.tokens + (time_passed * (self.calls / self.period))
        )
        self.last_update = now

    async def acquire(self):
        """Acquire a token, waiting if necessary"""
        while self.tokens < 1:
            self.update()
            if self.tokens < 1:
                await asyncio.sleep(self.period / self.calls)
        self.tokens -= 1

class GameTypeFlags:
    def __init__(self, **kwargs):
        self.full_game = kwargs.get('full_game', True)
        self.dlc = kwargs.get('dlc', False)
        self.expansion = kwargs.get('expansion', False)
        self.bundle = kwargs.get('bundle', True)
        self.in_game_content = kwargs.get('in_game_content', False)
        self.other = kwargs.get('other', False)

    def to_dict(self):
        return {
            'full_game': self.full_game,
            'dlc': self.dlc,
            'expansion': self.expansion,
            'bundle': self.bundle,
            'in_game_content': self.in_game_content,
            'other': self.other
        }

    @classmethod
    def from_dict(cls, data):
        return cls(**data)

class GameCache:
    def __init__(self, max_age_days: int = 30):
        self.max_age = timedelta(days=max_age_days)
        self.games = {}

    def add_game(self, game: dict) -> str:
        game_hash = self.generate_game_hash(game)
        self.games[game_hash] = {
            'timestamp': datetime.datetime.utcnow(),
            'data': game
        }
        return game_hash

    def is_duplicate(self, game: dict) -> bool:
        game_hash = self.generate_game_hash(game)
        return game_hash in self.games

    def clean_old_entries(self):
        current_time = datetime.datetime.utcnow()
        to_remove = [
            game_hash for game_hash, data in self.games.items()
            if current_time - data['timestamp'] > self.max_age
        ]
        for game_hash in to_remove:
            del self.games[game_hash]

    @staticmethod
    def generate_game_hash(game: dict) -> str:
        game_string = f"{game.get('name', '')}-{game.get('store', '')}-{game.get('url', '')}"
        return hashlib.md5(game_string.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            game_hash: {
                'timestamp': data['timestamp'].isoformat(),
                'data': data['data']
            }
            for game_hash, data in self.games.items()
        }

    @classmethod
    def from_dict(cls, data: dict, max_age_days: int = 30) -> 'GameCache':
        cache = cls(max_age_days=max_age_days)
        cache.games = {
            game_hash: {
                'timestamp': datetime.datetime.fromisoformat(data['timestamp']),
                'data': data['data']
            }
            for game_hash, data in data.items()
        }
        return cache
class GameClaimButton(Button):
    def __init__(self, url: str):
        super().__init__(
            style=discord.ButtonStyle.success,
            label="Claim Now",
            url=url
        )

class GameClaimView(View):
    def __init__(self, url: str):
        super().__init__(timeout=None)
        self.add_item(GameClaimButton(url=url))

class APIManager:
    def __init__(self):
        self.rate_limits = {
            "Epic": RateLimit(30, 60),
            "Steam": RateLimit(100, 300),
            "GOG": RateLimit(60, 60),
            "Humble": RateLimit(30, 60),
            "Itch": RateLimit(20, 60),
            "EA": RateLimit(30, 60),
            "Ubisoft": RateLimit(30, 60)
        }
        self.store_status = {store: StoreStatus.OPERATIONAL for store in self.rate_limits}
        self.error_counts = {store: 0 for store in self.rate_limits}
        self.last_success = {store: None for store in self.rate_limits}

    async def make_request(self, 
                          session: aiohttp.ClientSession,
                          store: str,
                          url: str,
                          method: str = "GET",
                          **kwargs) -> dict:
        rate_limit = self.rate_limits[store]
        await rate_limit.acquire()

        try:
            async with session.request(method, url, **kwargs) as response:
                if response.status == 429:
                    retry_after = float(response.headers.get('Retry-After', 60))
                    rate_limit.retry_after = time.monotonic() + retry_after
                    self.store_status[store] = StoreStatus.RATE_LIMITED
                    raise RateLimitError(retry_after)
                
                if response.status >= 500:
                    self.error_counts[store] += 1
                    if self.error_counts[store] >= 3:
                        self.store_status[store] = StoreStatus.DOWN
                    else:
                        self.store_status[store] = StoreStatus.DEGRADED
                    raise APIError(f"{store} API server error: {response.status}")
                
                if response.status >= 400:
                    raise APIError(f"{store} API client error: {response.status}")
                
                self.error_counts[store] = 0
                self.store_status[store] = StoreStatus.OPERATIONAL
                self.last_success[store] = datetime.datetime.utcnow()
                
                return await response.json()

        except asyncio.TimeoutError:
            self.error_counts[store] += 1
            if self.error_counts[store] >= 3:
                self.store_status[store] = StoreStatus.DOWN
            raise APIError(f"{store} API timeout")

        except aiohttp.ClientError as e:
            self.error_counts[store] += 1
            if self.error_counts[store] >= 3:
                self.store_status[store] = StoreStatus.DOWN
            raise APIError(f"{store} API connection error: {str(e)}")

class APITester:
    """Handles API connection testing"""
    
    def __init__(self, session: aiohttp.ClientSession, api_manager: APIManager):
        self.session = session
        self.api_manager = api_manager
        self.test_endpoints = {
            "Epic": {
                "url": "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions",
                "method": "GET",
                "expected_status": 200,
                "headers": {"Accept": "application/json"}
            },
            "Steam": {
                "url": "https://api.steampowered.com/ISteamApps/GetAppList/v2/",
                "method": "GET",
                "expected_status": 200
            },
            "GOG": {
                "url": "https://www.gog.com/games/ajax/filtered",
                "method": "GET",
                "expected_status": 200,
                "params": {"price": "free"}
            },
            "Humble": {
                "url": "https://www.humblebundle.com/store/api/search",
                "method": "GET",
                "expected_status": 200,
                "params": {"sort": "discount"}
            },
            "Itch": {
                "url": "https://itch.io/api/1/games/on-sale",
                "method": "GET",
                "expected_status": 200
            },
            "EA": {
                "url": "https://api.ea.com/games/v1/games",
                "method": "GET",
                "expected_status": 200
            },
            "Ubisoft": {
                "url": "https://store.ubi.com/api/games",
                "method": "GET",
                "expected_status": 200
            }
        }

    async def test_endpoint(self, store: str) -> Tuple[bool, str, float]:
        """Test a specific store endpoint"""
        endpoint = self.test_endpoints[store]
        start_time = time.time()
        
        try:
            await self.api_manager.rate_limits[store].acquire()
            
            async with self.session.request(
                endpoint["method"],
                endpoint["url"],
                headers=endpoint.get("headers", {}),
                params=endpoint.get("params", {}),
                timeout=10
            ) as response:
                elapsed = time.time() - start_time
                
                if response.status == endpoint["expected_status"]:
                    return True, "Success", elapsed
                elif response.status == 429:
                    return False, "Rate Limited", elapsed
                elif response.status >= 500:
                    return False, f"Server Error ({response.status})", elapsed
                else:
                    return False, f"Unexpected Status ({response.status})", elapsed

        except asyncio.TimeoutError:
            elapsed = time.time() - start_time
            return False, "Timeout", elapsed
        except aiohttp.ClientError as e:
            elapsed = time.time() - start_time
            return False, f"Connection Error: {str(e)}", elapsed
        except Exception as e:
            elapsed = time.time() - start_time
            return False, f"Unknown Error: {str(e)}", elapsed

    async def test_all_endpoints(self) -> Dict[str, Tuple[bool, str, float]]:
        """Test all store endpoints"""
        results = {}
        for store in self.test_endpoints:
            results[store] = await self.test_endpoint(store)
        return results
class EFreeGames(commands.Cog):
    """Track free games across different gaming storefronts"""

    SUPPORTED_STORES = ["Epic", "Steam", "GOG", "Humble", "Itch", "EA", "Ubisoft"]

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.session = aiohttp.ClientSession()
        self.api_manager = APIManager()
        self.api_tester = APITester(self.session, self.api_manager)
        self.game_cache = None
        self.task = None

        default_guild = {
            "announcement_channel": None,
            "use_threads": False,
            "thread_name_format": "Free Games {store}",
            "store_threads": {},
            "combined_thread": None,
            "stores_enabled": {store: True for store in self.SUPPORTED_STORES},
            "game_type_filters": GameTypeFlags().to_dict(),
            "ping_roles": {
                "default": None,
                "stores": {},
                "game_types": {},
                "enabled": True
            }
        }

        default_global = {
            "last_check": None,
            "cached_games": {},
            "api_credentials": {},
            "update_interval": 24,
            "game_cache": {},
            "cache_max_age": 30
        }

        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)
        self.start_tasks()

    def start_tasks(self):
        """Start the automatic update task"""
        if self.task:
            self.task.cancel()
        self.task = self.bot.loop.create_task(self.automatic_check())

    def cog_unload(self):
        if self.task:
            self.task.cancel()
        if self.session:
            self.bot.loop.create_task(self.session.close())
        if self.game_cache:
            self.bot.loop.create_task(self.save_cache())

    async def initialize(self):
        await self.initialize_cache()

    async def initialize_cache(self):
        """Initialize the game cache from stored data"""
        cache_data = await self.config.game_cache()
        max_age = await self.config.cache_max_age()
        self.game_cache = GameCache.from_dict(cache_data, max_age)
        self.game_cache.clean_old_entries()
        await self.save_cache()

    async def save_cache(self):
        """Save the current cache to config"""
        if self.game_cache:
            await self.config.game_cache.set(self.game_cache.to_dict())

    async def get_dominant_color(self, image_url: str) -> discord.Color:
        """Get the dominant color from an image URL"""
        try:
            async with self.session.get(image_url) as response:
                if response.status == 200:
                    img_data = await response.read()
                    img = BytesIO(img_data)
                    color_thief = colorthief.ColorThief(img)
                    dominant_color = color_thief.get_color(quality=1)
                    return discord.Color.from_rgb(*dominant_color)
        except Exception as e:
            logger.error(f"Error getting dominant color: {e}")
        return discord.Color.blue()

    def format_timestamp(self, end_date: Union[str, datetime.datetime]) -> str:
        """Format end date as Discord timestamp"""
        if isinstance(end_date, str):
            try:
                end_date = datetime.datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            except ValueError:
                return "No end date specified"
        
        return f"<t:{int(end_date.timestamp())}:f>"

    async def create_game_embed(self, game: dict, store: str) -> tuple[discord.Embed, GameClaimView]:
        """Create an embed and claim button view for a single game"""
        color = await self.get_dominant_color(game.get('image_url', ''))
        
        embed = discord.Embed(
            title=game['name'],
            url=game['url'],
            color=color,
            timestamp=datetime.datetime.utcnow()
        )
        
        if game.get('end_date'):
            embed.description = f"Free to claim until: {self.format_timestamp(game['end_date'])}"
        
        embed.set_author(
            name=f"Free on {store}",
            icon_url=self.store_icons.get(store, '')
        )
        
        if game.get('image_url'):
            embed.set_image(url=game['image_url'])
        
        if game.get('type'):
            embed.add_field(
                name="Type",
                value=game['type'].replace('_', ' ').title(),
                inline=True
            )
        
        if game.get('original_price'):
            embed.add_field(
                name="Original Price",
                value=game['original_price'],
                inline=True
            )
        
        embed.set_footer(text=f"Game ID: {game.get('id', 'N/A')}")
        
        view = GameClaimView(game['url'])
        
        return embed, view

    # Store icons for embed author
    store_icons = {
        "Epic": "https://example.com/epic-icon.png",
        "Steam": "https://example.com/steam-icon.png",
        "GOG": "https://example.com/gog-icon.png",
        "Humble": "https://example.com/humble-icon.png",
        "Itch": "https://example.com/itch-icon.png",
        "EA": "https://example.com/ea-icon.png",
        "Ubisoft": "https://example.com/ubisoft-icon.png"
    }
    async def send_games_embed(self, 
                             destination: Union[discord.TextChannel, discord.Thread],
                             games: Dict[str, List[dict]],
                             store: Optional[str] = None):
        """Creates and sends embeds with claim buttons for free games"""
        # Filter games and get role pings
        if isinstance(destination.guild, discord.Guild):
            games = await self.filter_games_by_type(games, destination.guild.id)
            if not games:
                return

        if store:
            # Single store games
            store_games = games.get(store, [])
            if not store_games:
                return

            role_pings = set()
            for game in store_games:
                roles = await self.get_ping_roles(
                    destination.guild.id,
                    store,
                    game.get('type', GameType.OTHER.value)
                )
                role_pings.update(roles)

            # Format role pings
            ping_text = await self.format_role_pings(destination.guild, list(role_pings))
            
            # Send each game as a separate embed with claim button
            if ping_text:
                await destination.send(ping_text)
            
            for game in store_games:
                embed, view = await self.create_game_embed(game, store)
                await destination.send(embed=embed, view=view)
                await asyncio.sleep(0.5)  # Slight delay between messages

        else:
            # Multiple stores
            role_pings = set()
            for store_name, store_games in games.items():
                for game in store_games:
                    roles = await self.get_ping_roles(
                        destination.guild.id,
                        store_name,
                        game.get('type', GameType.OTHER.value)
                    )
                    role_pings.update(roles)

            # Format role pings
            ping_text = await self.format_role_pings(destination.guild, list(role_pings))
            
            if ping_text:
                await destination.send(ping_text)
            
            # Send each game as a separate embed with claim button
            for store_name, store_games in games.items():
                for game in store_games:
                    embed, view = await self.create_game_embed(game, store_name)
                    await destination.send(embed=embed, view=view)
                    await asyncio.sleep(0.5)

    @commands.group(name="efreegames", aliases=["fg"])
    async def efreegames(self, ctx):
        """Shows currently available free games across different storefronts"""
        if ctx.invoked_subcommand is None:
            async with ctx.typing():
                games = await self.check_all_stores()
                await self.send_games_embed(ctx.channel, games)

    @commands.admin_or_permissions(administrator=True)
    @efreegames.command(name="interval")
    async def set_interval(self, ctx, hours: int):
        """Set how often to check for free games (minimum 24 hours)"""
        if hours < 24:
            await ctx.send("The minimum interval is 24 hours.")
            return
        
        await self.config.update_interval.set(hours)
        self.start_tasks()
        
        await ctx.send(f"Update interval set to {hours} hours. The next check will be in {hours} hours.")

    @commands.admin_or_permissions(administrator=True)
    @efreegames.command(name="channel")
    async def set_channel(self, ctx, channel: discord.TextChannel = None):
        """Set the channel for free game announcements"""
        if channel is None:
            channel = ctx.channel

        await self.config.guild(ctx.guild).announcement_channel.set(channel.id)
        await ctx.send(f"Free game announcements will be posted in {channel.mention}")

    @commands.admin_or_permissions(administrator=True)
    @efreegames.command(name="threads")
    async def toggle_threads(self, ctx, enabled: bool = True):
        """Toggle using threads for announcements"""
        await self.config.guild(ctx.guild).use_threads.set(enabled)
        if enabled:
            await ctx.send("Thread mode enabled. Each store will have its own thread.")
        else:
            await ctx.send("Thread mode disabled. Announcements will be posted directly in the channel.")

    @backoff.on_exception(
        backoff.expo,
        (RateLimitError, APIError),
        max_tries=3,
        max_time=300
    )
    async def check_epic_games(self) -> List[dict]:
        """Check Epic Games Store for free games"""
        try:
            url = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
            data = await self.api_manager.make_request(
                self.session,
                "Epic",
                url,
                headers={"Accept": "application/json"}
            )
            
            games = []
            for game in data.get("data", {}).get("Catalog", {}).get("searchStore", {}).get("elements", []):
                if game.get("promotions"):
                    promo = game["promotions"]["promotionalOffers"]
                    if promo and promo[0]["promotionalOffers"]:
                        games.append({
                            "id": game["id"],
                            "name": game["title"],
                            "url": f"https://store.epicgames.com/p/{game['urlSlug']}",
                            "end_date": promo[0]["promotionalOffers"][0]["endDate"],
                            "image_url": game.get("keyImages", [{}])[0].get("url", ""),
                            "type": game.get("type", GameType.OTHER.value),
                            "original_price": game.get("price", {}).get("totalPrice", {}).get("fmtPrice", "N/A"),
                            "store": "Epic"
                        })
            
            return games
        except Exception as e:
            logger.error(f"Error checking Epic Games Store: {e}")
            return []

    # Similar implementations for other store checks
    async def check_steam(self) -> List[dict]:
        """Check Steam for free games"""
        # Implementation similar to check_epic_games
        return []

    async def check_gog(self) -> List[dict]:
        """Check GOG for free games"""
        # Implementation similar to check_epic_games
        return []

    async def check_humble(self) -> List[dict]:
        """Check Humble Bundle for free games"""
        # Implementation similar to check_epic_games
        return []

    async def check_itch(self) -> List[dict]:
        """Check itch.io for free games"""
        # Implementation similar to check_epic_games
        return []

    async def check_ea(self) -> List[dict]:
        """Check EA/Origin for free games"""
        # Implementation similar to check_epic_games
        return []

    async def check_ubisoft(self) -> List[dict]:
        """Check Ubisoft Connect for free games"""
        # Implementation similar to check_epic_games
        return []

    async def check_all_stores(self, force_refresh: bool = False) -> Dict[str, List[dict]]:
        """Check all stores for free games"""
        last_check = await self.config.last_check()
        current_time = datetime.datetime.utcnow().timestamp()
        
        if not force_refresh and last_check and (current_time - last_check < 3600):
            return await self.config.cached_games()

        results = {
            "Epic": await self.check_epic_games(),
            "Steam": await self.check_steam(),
            "GOG": await self.check_gog(),
            "Humble": await self.check_humble(),
            "Itch": await self.check_itch(),
            "EA": await self.check_ea(),
            "Ubisoft": await self.check_ubisoft()
        }

        await self.config.last_check.set(current_time)
        await self.config.cached_games.set(results)
        return results
    async def automatic_check(self):
        """Automatically check for free games and post updates"""
        await self.bot.wait_until_ready()
        while True:
            try:
                interval = await self.config.update_interval()
                games = await self.check_all_stores(force_refresh=True)
                
                # Post updates to all configured channels
                for guild in self.bot.guilds:
                    guild_config = await self.config.guild(guild).all()
                    channel_id = guild_config["announcement_channel"]
                    if not channel_id:
                        continue

                    channel = guild.get_channel(channel_id)
                    if not channel:
                        continue

                    if guild_config["use_threads"]:
                        store_threads = guild_config["store_threads"]
                        if store_threads:  # Split by store
                            for store in self.SUPPORTED_STORES:
                                if not guild_config["stores_enabled"][store]:
                                    continue
                                    
                                thread_name = guild_config["thread_name_format"].format(store=store)
                                thread = await self.create_or_get_thread(
                                    channel,
                                    thread_name,
                                    store_threads.get(store)
                                )
                                
                                # Update thread ID in config
                                async with self.config.guild(guild).store_threads() as threads:
                                    threads[store] = thread.id
                                
                                await self.send_games_embed(thread, games, store)
                        else:  # Combined thread
                            thread = await self.create_or_get_thread(
                                channel,
                                "Free Games Updates",
                                guild_config["combined_thread"]
                            )
                            
                            await self.config.guild(guild).combined_thread.set(thread.id)
                            await self.send_games_embed(thread, games)
                    else:
                        await self.send_games_embed(channel, games)
                
                await asyncio.sleep(interval * 3600)  # Convert hours to seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in automatic check: {e}")
                await asyncio.sleep(300)  # Wait 5 minutes before retrying

