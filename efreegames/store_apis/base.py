from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from datetime import datetime
import aiohttp
import logging

logger = logging.getLogger(__name__)

class GameData:
    def __init__(self):
        self.id: str = ""
        self.title: str = ""
        self.store_url: str = ""
        self.image_url: str = ""
        self.end_date: datetime = None
        self.store: str = ""
        self.type: str = ""
        self.price: Dict[str, float] = {}
        self.rating: float = 0.0
        self.is_adult: bool = False
        self.regions: List[str] = []
        self.color: tuple = (0, 0, 0)
        self.description: str = ""

class StoreAPI(ABC):
    def __init__(self, api_key: str, api_secret: str = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session: Optional[aiohttp.ClientSession] = None
        self.rate_limit_remaining = float('inf')
        self.rate_limit_reset = 0

    async def initialize(self):
        """Initialize API connection"""
        if self.session is None:
            self.session = aiohttp.ClientSession()

    async def close(self):
        """Close API connection"""
        if self.session:
            await self.session.close()
            self.session = None

    @abstractmethod
    async def get_free_games(self) -> List[GameData]:
        """Get list of free games"""
        pass

    @abstractmethod
    async def test_connection(self) -> bool:
        """Test API connection"""
        pass

    @abstractmethod
    async def get_game_details(self, game_id: str) -> GameData:
        """Get detailed game information"""
        pass

    @abstractmethod
    async def get_auth_url(self, user_id: int) -> str:
        """Get URL for account linking"""
        pass

    async def handle_rate_limit(self, response: aiohttp.ClientResponse):
        """Handle rate limiting"""
        self.rate_limit_remaining = float(
            response.headers.get('X-RateLimit-Remaining', float('inf'))
        )
        self.rate_limit_reset = int(
            response.headers.get('X-RateLimit-Reset', 0)
        )
        
        if response.status == 429:
            retry_after = int(response.headers.get('Retry-After', 60))
            logger.warning(f"Rate limited. Waiting {retry_after} seconds")
            await asyncio.sleep(retry_after)
            return True
        return False

    def is_rate_limited(self) -> bool:
        """Check if we're currently rate limited"""
        return self.rate_limit_remaining <= 0
