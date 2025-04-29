from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from datetime import datetime

class GameData:
    def __init__(self):
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

class StoreAPI(ABC):
    def __init__(self, api_key: str, api_secret: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = None

    @abstractmethod
    async def initialize(self):
        """Initialize API connection"""
        pass

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
