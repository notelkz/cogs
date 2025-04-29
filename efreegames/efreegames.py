from redbot.core import commands, Config
from redbot.core.utils.chat_formatting import box, pagify
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS
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
    """Game types for filtering"""
    FULL_GAME = "full_game"
    DLC = "dlc"
    EXPANSION = "expansion"
    BUNDLE = "bundle"
    IN_GAME_CONTENT = "in_game_content"
    OTHER = "other"

    @classmethod
    def list(cls):
        """Get list of all game types"""
        return [t.value for t in cls]

class StoreStatus(Enum):
    """Store API status"""
    OPERATIONAL = "operational"
    DEGRADED = "degraded"
    DOWN = "down"
    RATE_LIMITED = "rate_limited"

    def to_emoji(self) -> str:
        """Convert status to emoji"""
        return {
            self.OPERATIONAL: "🟢",
            self.DEGRADED: "🟡",
            self.DOWN: "🔴",
            self.RATE_LIMITED: "⏳"
        }[self]

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
    """Game type filter configuration"""
    def __init__(self, **kwargs):
        self.full_game = kwargs.get('full_game', True)
        self.dlc = kwargs.get('dlc', False)
        self.expansion = kwargs.get('expansion', False)
        self.bundle = kwargs.get('bundle', True)
        self.in_game_content = kwargs.get('in_game_content', False)
        self.other = kwargs.get('other', False)

    def to_dict(self) -> dict:
        return {
            'full_game': self.full_game,
            'dlc': self.dlc,
            'expansion': self.expansion,
            'bundle': self.bundle,
            'in_game_content': self.in_game_content,
            'other': self.other
        }

    @classmethod
    def from_dict(cls, data: dict):
        return cls(**data)

    def get_enabled(self) -> List[str]:
        """Get list of enabled game types"""
        return [k for k, v in self.to_dict().items() if v]

class GameCache:
    """Cache system for tracked games"""
    def __init__(self, max_age_days: int = 30):
        self.max_age = timedelta(days=max_age_days)
        self.games = {}

    def add_game(self, game: dict) -> str:
        """Add a game to the cache"""
        game_hash = self.generate_game_hash(game)
        self.games[game_hash] = {
            'timestamp': datetime.datetime.utcnow(),
            'data': game
        }
        return game_hash

    def is_duplicate(self, game: dict) -> bool:
        """Check if a game is already in the cache"""
        game_hash = self.generate_game_hash(game)
        return game_hash in self.games

    def clean_old_entries(self) -> int:
        """Remove old entries from cache and return number removed"""
        current_time = datetime.datetime.utcnow()
        to_remove = [
            game_hash for game_hash, data in self.games.items()
            if current_time - data['timestamp'] > self.max_age
        ]
        for game_hash in to_remove:
            del self.games[game_hash]
        return len(to_remove)

    @staticmethod
    def generate_game_hash(game: dict) -> str:
        """Generate unique hash for a game"""
        game_string = f"{game.get('name', '')}-{game.get('store', '')}-{game.get('url', '')}"
        return hashlib.md5(game_string.encode()).hexdigest()

    def to_dict(self) -> dict:
        """Convert cache to dictionary for storage"""
        return {
            game_hash: {
                'timestamp': data['timestamp'].isoformat(),
                'data': data['data']
            }
            for game_hash, data in self.games.items()
        }

    @classmethod
    def from_dict(cls, data: dict, max_age_days: int = 30) -> 'GameCache':
        """Create cache from dictionary"""
        cache = cls(max_age_days=max_age_days)
        cache.games = {
            game_hash: {
                'timestamp': datetime.datetime.fromisoformat(data['timestamp']),
                'data': data['data']
            }
            for game_hash, data in data.items()
        }
        return cache

    def get_stats(self) -> dict:
        """Get cache statistics"""
        current_time = datetime.datetime.utcnow()
        store_counts = {}
        oldest = None
        newest = None

        for data in self.games.values():
            store = data['data'].get('store', 'Unknown')
            store_counts[store] = store_counts.get(store, 0) + 1
            
            if oldest is None or data['timestamp'] < oldest:
                oldest = data['timestamp']
            if newest is None or data['timestamp'] > newest:
                newest = data['timestamp']

        return {
            'total_games': len(self.games),
            'store_counts': store_counts,
            'oldest_entry': oldest,
            'newest_entry': newest
        }
class GameClaimButton(Button):
    """Button for claiming free games"""
    def __init__(self, url: str):
        super().__init__(
            style=discord.ButtonStyle.success,
            label="Claim Now",
            url=url,
            emoji="🎮"
        )

class GameClaimView(View):
    """View containing the claim button"""
    def __init__(self, url: str):
        super().__init__(timeout=None)
        self.add_item(GameClaimButton(url=url))

class APIManager:
    """Manages API connections and rate limits"""
    def __init__(self):
        self.rate_limits = {
            "Epic": RateLimit(30, 60),    # 30 calls per minute
            "Steam": RateLimit(100, 300),  # 100 calls per 5 minutes
            "GOG": RateLimit(60, 60),     # 60 calls per minute
            "Humble": RateLimit(30, 60),   # 30 calls per minute
            "Itch": RateLimit(20, 60),     # 20 calls per minute
            "EA": RateLimit(30, 60),       # 30 calls per minute
            "Ubisoft": RateLimit(30, 60)   # 30 calls per minute
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
        """Make an API request with rate limiting and error handling"""
        rate_limit = self.rate_limits[store]
        await rate_limit.acquire()

        try:
            async with session.request(method, url, timeout=30, **kwargs) as response:
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

    def get_store_status(self, store: str) -> dict:
        """Get detailed status for a store"""
        return {
            'status': self.store_status[store],
            'error_count': self.error_counts[store],
            'last_success': self.last_success[store],
            'rate_limit': {
                'calls': self.rate_limits[store].calls,
                'period': self.rate_limits[store].period,
                'tokens': self.rate_limits[store].tokens
            }
        }
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

    async def generate_test_embed(self, store: str, result: Tuple[bool, str, float]) -> discord.Embed:
        """Generate embed for test results"""
        success, status, elapsed = result
        store_status = self.api_manager.get_store_status(store)
        
        embed = discord.Embed(
            title=f"API Test Results - {store}",
            color=discord.Color.green() if success else discord.Color.red(),
            timestamp=datetime.datetime.utcnow()
        )
        
        embed.add_field(
            name="Status",
            value=f"{store_status['status'].to_emoji()} {status}",
            inline=True
        )
        
        embed.add_field(
            name="Response Time",
            value=f"{elapsed:.2f}s",
            inline=True
        )
        
        embed.add_field(
            name="Error Count",
            value=str(store_status['error_count']),
            inline=True
        )
        
        if store_status['last_success']:
            embed.add_field(
                name="Last Success",
                value=store_status['last_success'].strftime("%Y-%m-%d %H:%M:%S UTC"),
                inline=True
            )
        
        rate_limit = store_status['rate_limit']
        embed.add_field(
            name="Rate Limit",
            value=f"{rate_limit['calls']} calls / {rate_limit['period']}s\n"
                  f"Available: {int(rate_limit['tokens'])}",
            inline=True
        )
        
        return embed
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

    async def cog_load(self):
        """Tasks to run when cog loads"""
        await self.initialize_cache()
        self.start_tasks()

    def cog_unload(self):
        """Cleanup when cog unloads"""
        if self.task:
            self.task.cancel()
        if self.session:
            self.bot.loop.create_task(self.session.close())
        if self.game_cache:
            self.bot.loop.create_task(self.save_cache())

    def start_tasks(self):
        """Start the automatic update task"""
        if self.task:
            self.task.cancel()
        self.task = self.bot.loop.create_task(self.automatic_check())

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

    # Store icons for embed author
    store_icons = {
        "Epic": "https://cdn.discordapp.com/attachments/1094721906376433766/1094722556168843374/epic.png",
        "Steam": "https://cdn.discordapp.com/attachments/1094721906376433766/1094722557045137428/steam.png",
        "GOG": "https://cdn.discordapp.com/attachments/1094721906376433766/1094722556487610428/gog.png",
        "Humble": "https://cdn.discordapp.com/attachments/1094721906376433766/1094722556768825425/humble.png",
        "Itch": "https://cdn.discordapp.com/attachments/1094721906376433766/1094722556999454791/itch.png",
        "EA": "https://cdn.discordapp.com/attachments/1094721906376433766/1094722555850088488/ea.png",
        "Ubisoft": "https://cdn.discordapp.com/attachments/1094721906376433766/1094722557359697940/ubisoft.png"
    }

    @commands.group(name="efreegames", aliases=["fg"])
    async def efreegames(self, ctx):
        """Free games management commands"""
        if ctx.invoked_subcommand is None:
            try:
                async with ctx.typing():
                    # Add a timeout for the store checks
                    games = await asyncio.wait_for(
                        self.check_all_stores(),
                        timeout=30.0  # 30 second timeout
                    )
                    
                    if not games:
                        await ctx.send("No free games found at the moment.")
                        return
                    
                    await self.send_games_embed(ctx.channel, games)
            except asyncio.TimeoutError:
                await ctx.send("❌ The store check timed out. Please try again later or check individual stores with `!forcecheckstore`")
            except Exception as e:
                logger.error(f"Error in efreegames command: {e}")
                await ctx.send("❌ An error occurred while checking for free games. Please try again later.")
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

    async def filter_games_by_type(self, games: Dict[str, List[dict]], guild_id: int) -> Dict[str, List[dict]]:
        """Filter games based on guild's game type preferences"""
        guild_filters = GameTypeFlags.from_dict(
            await self.config.guild_from_id(guild_id).game_type_filters()
        )
        
        filtered_games = {}
        for store, store_games in games.items():
            filtered_store_games = []
            for game in store_games:
                game_type = game.get('type', GameType.OTHER.value)
                filter_attr = game_type.lower()
                if hasattr(guild_filters, filter_attr) and getattr(guild_filters, filter_attr, False):
                    filtered_store_games.append(game)
            if filtered_store_games:
                filtered_games[store] = filtered_store_games
        
        return filtered_games

    async def get_ping_roles(self, guild_id: int, store: str, game_type: str) -> List[int]:
        """Get roles to ping based on store and game type"""
        guild_settings = await self.config.guild_from_id(guild_id).ping_roles()
        
        if not guild_settings["enabled"]:
            return []
        
        roles = set()
        
        if guild_settings["default"]:
            roles.add(guild_settings["default"])
        
        if store in guild_settings["stores"]:
            roles.add(guild_settings["stores"][store])
        
        if game_type in guild_settings["game_types"]:
            roles.add(guild_settings["game_types"][game_type])
            
        return list(roles)

    async def format_role_pings(self, guild: discord.Guild, role_ids: List[int]) -> str:
        """Format role pings from role IDs"""
        role_mentions = []
        for role_id in role_ids:
            role = guild.get_role(role_id)
            if role:
                role_mentions.append(role.mention)
        return " ".join(role_mentions)

    async def create_or_get_thread(self, 
                                 channel: discord.TextChannel,
                                 name: str,
                                 stored_thread_id: Optional[int] = None) -> discord.Thread:
        """Create a new thread or get existing one"""
        if stored_thread_id:
            thread = channel.get_thread(stored_thread_id)
            if thread:
                try:
                    await thread.edit(archived=False)
                    return thread
                except discord.NotFound:
                    pass

        thread = await channel.create_thread(
            name=name,
            type=discord.ChannelType.public_thread,
            auto_archive_duration=10080  # 7 days
        )
        return thread
    async def send_games_embed(self, 
                             destination: Union[discord.TextChannel, discord.Thread],
                             games: Dict[str, List[dict]],
                             store: Optional[str] = None):
        """Creates and sends embeds with claim buttons for free games"""
        try:
            if isinstance(destination.guild, discord.Guild):
                games = await self.filter_games_by_type(games, destination.guild.id)
                if not games:
                    await destination.send("No free games found matching your filters.")
                    return

            if store:
                # Single store games
                store_games = games.get(store, [])
                if not store_games:
                    await destination.send(f"No free games found for {store}.")
                    return

                role_pings = set()
                for game in store_games:
                    roles = await self.get_ping_roles(
                        destination.guild.id,
                        store,
                        game.get('type', GameType.OTHER.value)
                    )
                    role_pings.update(roles)

                ping_text = await self.format_role_pings(destination.guild, list(role_pings))
                
                if ping_text:
                    await destination.send(ping_text)
                
                for game in store_games:
                    try:
                        embed, view = await self.create_game_embed(game, store)
                        await destination.send(embed=embed, view=view)
                        await asyncio.sleep(0.5)  # Slight delay between messages
                    except Exception as e:
                        logger.error(f"Error sending game embed: {e}")
                        continue

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

                ping_text = await self.format_role_pings(destination.guild, list(role_pings))
                
                if ping_text:
                    await destination.send(ping_text)
                
                for store_name, store_games in games.items():
                    for game in store_games:
                        try:
                            embed, view = await self.create_game_embed(game, store_name)
                            await destination.send(embed=embed, view=view)
                            await asyncio.sleep(0.5)
                        except Exception as e:
                            logger.error(f"Error sending game embed: {e}")
                            continue

        except Exception as e:
            logger.error(f"Error in send_games_embed: {e}")
            await destination.send("❌ An error occurred while sending game information.")

    @commands.group(name="gamestore")
    @commands.admin_or_permissions(administrator=True)
    async def gamestore(self, ctx):
        """Manage store settings"""
        if ctx.invoked_subcommand is None:
            await self.show_store_status(ctx)

    @gamestore.command(name="list")
    async def store_list(self, ctx):
        """List all supported stores and their status"""
        guild_config = await self.config.guild(ctx.guild).all()
        
        embed = discord.Embed(
            title="Store Status",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.utcnow()
        )
        
        for store in self.SUPPORTED_STORES:
            status = self.api_manager.get_store_status(store)
            enabled = guild_config["stores_enabled"].get(store, False)
            
            value = (
                f"Enabled: {'✅' if enabled else '❌'}\n"
                f"Status: {status['status'].to_emoji()} {status['status'].value}\n"
                f"Error Count: {status['error_count']}"
            )
            
            if status['last_success']:
                value += f"\nLast Success: {status['last_success'].strftime('%Y-%m-%d %H:%M UTC')}"
            
            embed.add_field(
                name=store,
                value=value,
                inline=True
            )
        
        await ctx.send(embed=embed)

    @gamestore.command(name="enable")
    async def store_enable(self, ctx, store: str):
        """Enable a store"""
        store = store.capitalize()
        if store not in self.SUPPORTED_STORES:
            await ctx.send(f"❌ Invalid store. Available stores: {', '.join(self.SUPPORTED_STORES)}")
            return
        
        async with self.config.guild(ctx.guild).stores_enabled() as stores:
            stores[store] = True
        
        await ctx.send(f"✅ Enabled {store} store")

    @gamestore.command(name="disable")
    async def store_disable(self, ctx, store: str):
        """Disable a store"""
        store = store.capitalize()
        if store not in self.SUPPORTED_STORES:
            await ctx.send(f"❌ Invalid store. Available stores: {', '.join(self.SUPPORTED_STORES)}")
            return
        
        async with self.config.guild(ctx.guild).stores_enabled() as stores:
            stores[store] = False
        
        await ctx.send(f"✅ Disabled {store} store")
    async def show_store_status(self, ctx):
        """Show status of all stores"""
        try:
            guild_config = await self.config.guild(ctx.guild).all()
            
            embed = discord.Embed(
                title="Store Status",
                color=discord.Color.blue(),
                timestamp=datetime.datetime.utcnow()
            )
            
            for store in self.SUPPORTED_STORES:
                status = self.api_manager.get_store_status(store)
                enabled = guild_config["stores_enabled"].get(store, False)
                rate_limit = status['rate_limit']
                
                value = (
                    f"**Status:** {status['status'].to_emoji()} {status['status'].value}\n"
                    f"**Enabled:** {'✅' if enabled else '❌'}\n"
                    f"**Rate Limit:** {rate_limit['calls']}/{rate_limit['period']}s\n"
                    f"**Available Calls:** {int(rate_limit['tokens'])}\n"
                    f"**Error Count:** {status['error_count']}"
                )
                
                if status['last_success']:
                    value += f"\n**Last Success:** {status['last_success'].strftime('%Y-%m-%d %H:%M UTC')}"
                
                embed.add_field(
                    name=f"{store}",
                    value=value,
                    inline=False
                )
            
            # Add overall statistics
            total_enabled = sum(1 for store in self.SUPPORTED_STORES if guild_config["stores_enabled"].get(store, False))
            operational_count = sum(1 for store in self.SUPPORTED_STORES if self.api_manager.store_status[store] == StoreStatus.OPERATIONAL)
            
            embed.description = (
                f"**Total Stores:** {len(self.SUPPORTED_STORES)}\n"
                f"**Enabled Stores:** {total_enabled}\n"
                f"**Operational Stores:** {operational_count}"
            )
            
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Error showing store status: {e}")
            await ctx.send("❌ An error occurred while fetching store status.")

    @commands.group(name="settings")
    @commands.admin_or_permissions(administrator=True)
    async def settings(self, ctx):
        """Manage free games announcements settings"""
        if ctx.invoked_subcommand is None:
            await self.show_settings(ctx)

    async def show_settings(self, ctx):
        """Show current settings"""
        try:
            guild_config = await self.config.guild(ctx.guild).all()
            
            embed = discord.Embed(
                title="Free Games Settings",
                color=discord.Color.blue(),
                timestamp=datetime.datetime.utcnow()
            )
            
            # Channel settings
            channel = ctx.guild.get_channel(guild_config["announcement_channel"])
            embed.add_field(
                name="Announcement Channel",
                value=channel.mention if channel else "Not set",
                inline=False
            )
            
            # Thread settings
            thread_status = "Enabled" if guild_config["use_threads"] else "Disabled"
            if guild_config["use_threads"]:
                thread_status += f"\nFormat: {guild_config['thread_name_format']}"
            embed.add_field(
                name="Thread Mode",
                value=thread_status,
                inline=False
            )
            
            # Store status
            stores_status = ""
            for store, enabled in guild_config["stores_enabled"].items():
                stores_status += f"{store}: {'✅' if enabled else '❌'}\n"
            embed.add_field(
                name="Enabled Stores",
                value=stores_status or "No stores configured",
                inline=False
            )
            
            # Game type filters
            filters = GameTypeFlags.from_dict(guild_config["game_type_filters"])
            filter_status = ""
            for game_type, enabled in filters.to_dict().items():
                filter_status += f"{game_type.replace('_', ' ').title()}: {'✅' if enabled else '❌'}\n"
            embed.add_field(
                name="Game Type Filters",
                value=filter_status or "No filters configured",
                inline=False
            )
            
            # Update interval
            interval = await self.config.update_interval()
            embed.add_field(
                name="Update Interval",
                value=f"Every {interval} hours",
                inline=False
            )
            
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Error showing settings: {e}")
            await ctx.send("❌ An error occurred while fetching settings.")

    @settings.command(name="channel")
    async def set_channel(self, ctx, channel: discord.TextChannel = None):
        """Set the channel for free game announcements"""
        if channel is None:
            channel = ctx.channel

        await self.config.guild(ctx.guild).announcement_channel.set(channel.id)
        await ctx.send(f"✅ Free game announcements will be posted in {channel.mention}")

    @settings.command(name="interval")
    async def set_interval(self, ctx, hours: int):
        """Set how often to check for free games (minimum 24 hours)"""
        if hours < 24:
            await ctx.send("❌ The minimum interval is 24 hours.")
            return
        
        await self.config.update_interval.set(hours)
        self.start_tasks()
        
        await ctx.send(f"✅ Update interval set to {hours} hours. The next check will be in {hours} hours.")
    @settings.command(name="threads")
    async def toggle_threads(self, ctx, enabled: bool = True, *, format: str = None):
        """Toggle thread mode and set thread name format"""
        try:
            await self.config.guild(ctx.guild).use_threads.set(enabled)
            
            if enabled:
                if format:
                    await self.config.guild(ctx.guild).thread_name_format.set(format)
                    await ctx.send(f"✅ Thread mode enabled with format: {format}")
                else:
                    await ctx.send("✅ Thread mode enabled with default format")
            else:
                await ctx.send("✅ Thread mode disabled")
        except Exception as e:
            logger.error(f"Error toggling threads: {e}")
            await ctx.send("❌ An error occurred while updating thread settings.")

    @commands.group(name="filter")
    @commands.admin_or_permissions(administrator=True)
    async def filter(self, ctx):
        """Manage game type filters"""
        if ctx.invoked_subcommand is None:
            await self.show_filters(ctx)

    async def show_filters(self, ctx):
        """Show current filter settings"""
        try:
            guild_filters = GameTypeFlags.from_dict(
                await self.config.guild(ctx.guild).game_type_filters()
            )
            
            embed = discord.Embed(
                title="Game Type Filters",
                color=discord.Color.blue(),
                description="Current filter settings for free game announcements:",
                timestamp=datetime.datetime.utcnow()
            )
            
            for game_type, enabled in guild_filters.to_dict().items():
                embed.add_field(
                    name=game_type.replace('_', ' ').title(),
                    value="✅ Enabled" if enabled else "❌ Disabled",
                    inline=True
                )
            
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Error showing filters: {e}")
            await ctx.send("❌ An error occurred while fetching filter settings.")

    @filter.command(name="type")
    async def filter_type(self, ctx, game_type: str, enabled: bool):
        """Toggle a game type filter"""
        try:
            game_type = GameType(game_type.lower())
        except ValueError:
            await ctx.send(f"❌ Invalid game type. Available types: {', '.join(GameType.list())}")
            return

        try:
            async with self.config.guild(ctx.guild).game_type_filters() as filters:
                filters[game_type.value] = enabled

            status = "enabled" if enabled else "disabled"
            await ctx.send(f"✅ {game_type.value.replace('_', ' ').title()} filter {status}")
        except Exception as e:
            logger.error(f"Error setting filter: {e}")
            await ctx.send("❌ An error occurred while updating filter settings.")

    @filter.command(name="reset")
    async def filter_reset(self, ctx):
        """Reset filters to default settings"""
        try:
            await self.config.guild(ctx.guild).game_type_filters.set(
                GameTypeFlags().to_dict()
            )
            await ctx.send("✅ Game type filters have been reset to default settings")
        except Exception as e:
            logger.error(f"Error resetting filters: {e}")
            await ctx.send("❌ An error occurred while resetting filters.")

    @commands.group(name="roles")
    @commands.admin_or_permissions(administrator=True)
    async def roles(self, ctx):
        """Manage role ping settings"""
        if ctx.invoked_subcommand is None:
            await self.show_roles(ctx)

    async def show_roles(self, ctx):
        """Show current role ping settings"""
        try:
            guild_settings = await self.config.guild(ctx.guild).ping_roles()
            
            embed = discord.Embed(
                title="Role Ping Settings",
                color=discord.Color.blue(),
                description=f"Role pings are currently {'enabled' if guild_settings['enabled'] else 'disabled'}",
                timestamp=datetime.datetime.utcnow()
            )
            
            # Default role
            default_role = ctx.guild.get_role(guild_settings["default"]) if guild_settings["default"] else None
            embed.add_field(
                name="Default Role",
                value=default_role.mention if default_role else "None",
                inline=False
            )
            
            # Store roles
            store_roles = ""
            for store, role_id in guild_settings["stores"].items():
                role = ctx.guild.get_role(role_id)
                if role:
                    store_roles += f"{store}: {role.mention}\n"
            embed.add_field(
                name="Store-Specific Roles",
                value=store_roles or "None",
                inline=False
            )
            
            # Game type roles
            type_roles = ""
            for game_type, role_id in guild_settings["game_types"].items():
                role = ctx.guild.get_role(role_id)
                if role:
                    type_roles += f"{game_type.replace('_', ' ').title()}: {role.mention}\n"
            embed.add_field(
                name="Game Type Roles",
                value=type_roles or "None",
                inline=False
            )
            
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Error showing roles: {e}")
            await ctx.send("❌ An error occurred while fetching role settings.")
    @roles.command(name="toggle")
    async def roles_toggle(self, ctx, enabled: bool):
        """Enable or disable role pings"""
        try:
            async with self.config.guild(ctx.guild).ping_roles() as settings:
                settings["enabled"] = enabled
            
            status = "enabled" if enabled else "disabled"
            await ctx.send(f"✅ Role pings have been {status}")
        except Exception as e:
            logger.error(f"Error toggling roles: {e}")
            await ctx.send("❌ An error occurred while updating role settings.")

    @roles.command(name="default")
    async def roles_default(self, ctx, role: discord.Role = None):
        """Set the default role to ping for all free games"""
        try:
            async with self.config.guild(ctx.guild).ping_roles() as settings:
                settings["default"] = role.id if role else None
            
            if role:
                await ctx.send(f"✅ Default ping role set to {role.mention}")
            else:
                await ctx.send("✅ Default ping role has been cleared")
        except Exception as e:
            logger.error(f"Error setting default role: {e}")
            await ctx.send("❌ An error occurred while updating role settings.")

    @roles.command(name="store")
    async def roles_store(self, ctx, store: str, role: discord.Role = None):
        """Set a role to ping for a specific store"""
        store = store.capitalize()
        if store not in self.SUPPORTED_STORES:
            await ctx.send(f"❌ Invalid store. Available stores: {', '.join(self.SUPPORTED_STORES)}")
            return

        try:
            async with self.config.guild(ctx.guild).ping_roles() as settings:
                if role:
                    settings["stores"][store] = role.id
                    await ctx.send(f"✅ Role for {store} set to {role.mention}")
                else:
                    if store in settings["stores"]:
                        del settings["stores"][store]
                    await ctx.send(f"✅ Role for {store} has been cleared")
        except Exception as e:
            logger.error(f"Error setting store role: {e}")
            await ctx.send("❌ An error occurred while updating role settings.")

    @roles.command(name="type")
    async def roles_type(self, ctx, game_type: str, role: discord.Role = None):
        """Set a role to ping for a specific game type"""
        try:
            game_type = GameType(game_type.lower())
        except ValueError:
            await ctx.send(f"❌ Invalid game type. Available types: {', '.join(GameType.list())}")
            return

        try:
            async with self.config.guild(ctx.guild).ping_roles() as settings:
                if role:
                    settings["game_types"][game_type.value] = role.id
                    await ctx.send(f"✅ Role for {game_type.value} set to {role.mention}")
                else:
                    if game_type.value in settings["game_types"]:
                        del settings["game_types"][game_type.value]
                    await ctx.send(f"✅ Role for {game_type.value} has been cleared")
        except Exception as e:
            logger.error(f"Error setting game type role: {e}")
            await ctx.send("❌ An error occurred while updating role settings.")

    @roles.command(name="clear")
    async def roles_clear(self, ctx):
        """Clear all role ping settings"""
        try:
            await self.config.guild(ctx.guild).ping_roles.set({
                "default": None,
                "stores": {},
                "game_types": {},
                "enabled": True
            })
            await ctx.send("✅ All role ping settings have been cleared")
        except Exception as e:
            logger.error(f"Error clearing roles: {e}")
            await ctx.send("❌ An error occurred while clearing role settings.")

    @commands.group(name="cache")
    @commands.admin_or_permissions(administrator=True)
    async def cache(self, ctx):
        """Manage game announcement cache"""
        if ctx.invoked_subcommand is None:
            await self.show_cache_status(ctx)

    async def show_cache_status(self, ctx):
        """Show current cache status"""
        try:
            if not self.game_cache:
                await self.initialize_cache()
            
            stats = self.game_cache.get_stats()
            
            embed = discord.Embed(
                title="Game Cache Status",
                color=discord.Color.blue(),
                timestamp=datetime.datetime.utcnow()
            )
            
            embed.add_field(
                name="Total Games",
                value=str(stats['total_games']),
                inline=True
            )
            
            embed.add_field(
                name="Cache Age Limit",
                value=f"{await self.config.cache_max_age()} days",
                inline=True
            )
            
            # Store statistics
            store_stats = ""
            for store, count in stats['store_counts'].items():
                store_stats += f"{store}: {count}\n"
            embed.add_field(
                name="Games per Store",
                value=store_stats or "No cached games",
                inline=False
            )
            
            if stats['oldest_entry']:
                embed.add_field(
                    name="Oldest Entry",
                    value=stats['oldest_entry'].strftime("%Y-%m-%d %H:%M UTC"),
                    inline=True
                )
            
            if stats['newest_entry']:
                embed.add_field(
                    name="Newest Entry",
                    value=stats['newest_entry'].strftime("%Y-%m-%d %H:%M UTC"),
                    inline=True
                )
            
            await ctx.send(embed=embed)
        except Exception as e:
            logger.error(f"Error showing cache status: {e}")
            await ctx.send("❌ An error occurred while fetching cache status.")
    @cache.command(name="clear")
    async def cache_clear(self, ctx):
        """Clear the game announcement cache"""
        try:
            self.game_cache = GameCache(await self.config.cache_max_age())
            await self.save_cache()
            await ctx.send("✅ Game announcement cache has been cleared")
        except Exception as e:
            logger.error(f"Error clearing cache: {e}")
            await ctx.send("❌ An error occurred while clearing the cache.")

    @cache.command(name="clean")
    async def cache_clean(self, ctx):
        """Remove old entries from the cache"""
        try:
            if not self.game_cache:
                await self.initialize_cache()
            
            old_count = len(self.game_cache.games)
            removed = self.game_cache.clean_old_entries()
            new_count = len(self.game_cache.games)
            
            await self.save_cache()
            await ctx.send(f"✅ Removed {removed} old entries from the cache. {new_count} entries remaining.")
        except Exception as e:
            logger.error(f"Error cleaning cache: {e}")
            await ctx.send("❌ An error occurred while cleaning the cache.")

    async def check_all_stores(self, force_refresh: bool = False) -> Dict[str, List[dict]]:
        """Check all stores for free games"""
        try:
            last_check = await self.config.last_check()
            current_time = datetime.datetime.utcnow().timestamp()
            
            # If we have cached results and it's been less than an hour
            if not force_refresh and last_check and (current_time - last_check < 3600):
                cached_games = await self.config.cached_games()
                if cached_games:
                    return cached_games

            # Initialize results dictionary
            results = {}
            
            # Check each store with a timeout
            for store in self.SUPPORTED_STORES:
                try:
                    check_method = getattr(self, f"check_{store.lower()}")
                    store_games = await asyncio.wait_for(check_method(), timeout=10.0)
                    if store_games:
                        results[store] = store_games
                except asyncio.TimeoutError:
                    logger.error(f"Timeout checking {store}")
                    continue
                except Exception as e:
                    logger.error(f"Error checking {store}: {e}")
                    continue

            # Only update cache if we got any results
            if results:
                await self.config.last_check.set(current_time)
                await self.config.cached_games.set(results)
            
            return results

        except Exception as e:
            logger.error(f"Error in check_all_stores: {e}")
            return {}

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

    @backoff.on_exception(
        backoff.expo,
        (RateLimitError, APIError),
        max_tries=3,
        max_time=300
    )
    async def check_steam(self) -> List[dict]:
        """Check Steam for free games"""
        try:
            url = "https://api.steampowered.com/ISteamApps/GetAppList/v2/"
            data = await self.api_manager.make_request(
                self.session,
                "Steam",
                url
            )
            
            games = []
            for app in data.get("applist", {}).get("apps", [])[:100]:  # Limit to first 100 for performance
                try:
                    details_url = f"https://store.steampowered.com/api/appdetails?appids={app['appid']}"
                    details = await self.api_manager.make_request(
                        self.session,
                        "Steam",
                        details_url
                    )
                    
                    app_data = details.get(str(app['appid']), {}).get("data", {})
                    if app_data.get("is_free") and not app_data.get("type") == "demo":
                        games.append({
                            "id": str(app['appid']),
                            "name": app_data["name"],
                            "url": f"https://store.steampowered.com/app/{app['appid']}",
                            "image_url": app_data.get("header_image"),
                            "type": GameType.DLC.value if app_data.get("type") == "dlc" else GameType.FULL_GAME.value,
                            "original_price": app_data.get("price_overview", {}).get("final_formatted", "N/A"),
                            "store": "Steam"
                        })
                except Exception as e:
                    logger.debug(f"Error checking Steam app {app['appid']}: {e}")
                    continue
            
            return games
        except Exception as e:
            logger.error(f"Error checking Steam: {e}")
            return []
    @backoff.on_exception(
        backoff.expo,
        (RateLimitError, APIError),
        max_tries=3,
        max_time=300
    )
    async def check_gog(self) -> List[dict]:
        """Check GOG for free games"""
        try:
            url = "https://www.gog.com/games/ajax/filtered"
            params = {
                "price": "free",
                "sort": "popularity"
            }
            data = await self.api_manager.make_request(
                self.session,
                "GOG",
                url,
                params=params
            )
            
            games = []
            for product in data.get("products", []):
                if product.get("price", {}).get("isFree"):
                    games.append({
                        "id": str(product["id"]),
                        "name": product["title"],
                        "url": f"https://www.gog.com{product['url']}",
                        "image_url": product.get("image", ""),
                        "type": GameType.FULL_GAME.value,
                        "original_price": product.get("price", {}).get("baseAmount", "N/A"),
                        "store": "GOG"
                    })
            
            return games
        except Exception as e:
            logger.error(f"Error checking GOG: {e}")
            return []

    @backoff.on_exception(
        backoff.expo,
        (RateLimitError, APIError),
        max_tries=3,
        max_time=300
    )
    async def check_humble(self) -> List[dict]:
        """Check Humble Bundle for free games"""
        try:
            url = "https://www.humblebundle.com/store/api/search"
            params = {
                "sort": "discount",
                "filter": "all",
                "request": 1,
                "page_size": 20
            }
            data = await self.api_manager.make_request(
                self.session,
                "Humble",
                url,
                params=params
            )
            
            games = []
            for result in data.get("results", []):
                if result.get("current_price", {}).get("amount") == 0:
                    games.append({
                        "id": result["machine_name"],
                        "name": result["human_name"],
                        "url": f"https://www.humblebundle.com/store/{result['human_url']}",
                        "image_url": result.get("featured_image", ""),
                        "end_date": result.get("sale_end"),
                        "type": GameType.FULL_GAME.value,
                        "original_price": result.get("full_price", {}).get("amount", "N/A"),
                        "store": "Humble"
                    })
            
            return games
        except Exception as e:
            logger.error(f"Error checking Humble Bundle: {e}")
            return []

    @commands.command(name="forcecheckstore")
    @commands.admin_or_permissions(administrator=True)
    async def force_check_store(self, ctx, store: str):
        """Force check a specific store"""
        store = store.capitalize()
        if store not in self.SUPPORTED_STORES:
            await ctx.send(f"❌ Invalid store. Available stores: {', '.join(self.SUPPORTED_STORES)}")
            return

        async with ctx.typing():
            try:
                check_method = getattr(self, f"check_{store.lower()}")
                games = await asyncio.wait_for(check_method(), timeout=30.0)
                if games:
                    await self.send_games_embed(ctx.channel, {store: games}, store)
                else:
                    await ctx.send(f"No free games found on {store}")
            except asyncio.TimeoutError:
                await ctx.send(f"❌ Timeout while checking {store}")
            except Exception as e:
                logger.error(f"Error checking {store}: {e}")
                await ctx.send(f"❌ Error checking {store}: {str(e)}")

    @commands.command(name="fgdebug")
    @commands.is_owner()
    async def debug_stores(self, ctx):
        """Debug store checks"""
        message = await ctx.send("Debugging store checks...")
        results = {}
        
        for store in self.SUPPORTED_STORES:
            try:
                start_time = time.time()
                check_method = getattr(self, f"check_{store.lower()}")
                games = await asyncio.wait_for(check_method(), timeout=10.0)
                elapsed = time.time() - start_time
                
                results[store] = {
                    "success": True,
                    "games_found": len(games) if games else 0,
                    "time": elapsed,
                    "error": None
                }
            except Exception as e:
                results[store] = {
                    "success": False,
                    "games_found": 0,
                    "time": 0,
                    "error": str(e)
                }
        
        # Create debug embed
        embed = discord.Embed(
            title="Store Check Debug Results",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.utcnow()
        )
        
        for store, data in results.items():
            value = (
                f"Success: {'✅' if data['success'] else '❌'}\n"
                f"Games Found: {data['games_found']}\n"
                f"Time: {data['time']:.2f}s\n"
            )
            if data['error']:
                value += f"Error: {data['error']}\n"
            
            embed.add_field(
                name=store,
                value=value,
                inline=False
            )
        
        await message.edit(content=None, embed=embed)
    async def automatic_check(self):
        """Automatically check for free games and post updates"""
        await self.bot.wait_until_ready()
        while True:
            try:
                interval = await self.config.update_interval()
                games = await asyncio.wait_for(
                    self.check_all_stores(force_refresh=True),
                    timeout=60.0  # 1 minute timeout for all checks
                )
                
                # Post updates to all configured channels
                for guild in self.bot.guilds:
                    try:
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
                    except Exception as e:
                        logger.error(f"Error posting updates to guild {guild.id}: {e}")
                        continue
                
                await asyncio.sleep(interval * 3600)  # Convert hours to seconds
            except asyncio.CancelledError:
                break
            except asyncio.TimeoutError:
                logger.error("Timeout during automatic check")
                await asyncio.sleep(300)  # Wait 5 minutes before retrying
            except Exception as e:
                logger.error(f"Error in automatic check: {e}")
                await asyncio.sleep(300)  # Wait 5 minutes before retrying

    @commands.command(name="checkstores")
    @commands.admin_or_permissions(administrator=True)
    async def check_stores(self, ctx):
        """Force check all stores"""
        async with ctx.typing():
            message = await ctx.send("Checking all stores...")
            try:
                results = await asyncio.wait_for(
                    self.check_all_stores(force_refresh=True),
                    timeout=60.0
                )
                
                embed = discord.Embed(
                    title="Store Check Results",
                    color=discord.Color.blue(),
                    timestamp=datetime.datetime.utcnow()
                )
                
                total_games = 0
                for store, games in results.items():
                    status = self.api_manager.get_store_status(store)
                    games_count = len(games)
                    total_games += games_count
                    
                    embed.add_field(
                        name=store,
                        value=f"{status['status'].to_emoji()} {games_count} free games found\n"
                              f"Status: {status['status'].value}",
                        inline=True
                    )
                
                embed.description = f"Found {total_games} free games across all stores"
                await message.edit(content=None, embed=embed)
                
                # Send game announcements
                if total_games > 0:
                    await self.send_games_embed(ctx.channel, results)
                
            except asyncio.TimeoutError:
                await message.edit(content="❌ Store check timed out. Please try again later.")
            except Exception as e:
                logger.error(f"Error in check_stores: {e}")
                await message.edit(content="❌ An error occurred while checking stores.")

def setup(bot):
    """Add the cog to the bot."""
    cog = EFreeGames(bot)
    bot.add_cog(cog)
