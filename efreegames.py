from redbot.core import commands, Config
import discord
from discord.ui import Button, View
import aiohttp
import datetime
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

class EFreeGames(commands.Cog):
    """Track free games across different gaming storefronts"""

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

    def cog_unload(self):
        if self.task:
            self.task.cancel()
        if self.session:
            self.bot.loop.create_task(self.session.close())
        if self.game_cache:
            self.bot.loop.create_task(self.save_cache())

    async def initialize(self):
        await self.initialize_cache()

    # ... (rest of the implementation from previous messages)
    # Include all the methods we've discussed: store checks, commands,
    # embed creation, cache management, error handling, etc.

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

# Continuing EFreeGames class...

    @commands.group(name="efreegames", aliases=["fg"])
    async def efreegames(self, ctx):
        """Shows currently available free games across different storefronts"""
        if ctx.invoked_subcommand is None:
            async with ctx.typing():
                games = await self.check_all_stores()
                await self.send_games_embed(ctx.channel, games)

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

    # Similar implementations for other stores...
    # check_steam(), check_gog(), check_humble(), check_itch(), check_ea(), check_ubisoft()

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

