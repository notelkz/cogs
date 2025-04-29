# utils.py
import aiohttp
from PIL import Image
import io
import colorsys
from typing import Tuple, Dict, Any
import json
import asyncio
from datetime import datetime, timezone
import logging

logger = logging.getLogger("red.efreegames.utils")

async def extract_dominant_color(image_url: str, session: aiohttp.ClientSession) -> Tuple[int, int, int]:
    """Extract the dominant color from an image URL"""
    try:
        async with session.get(image_url) as response:
            if response.status == 200:
                image_data = await response.read()
                image = Image.open(io.BytesIO(image_data))
                image = image.convert('RGBA')
                
                # Resize for faster processing
                image.thumbnail((100, 100))
                
                pixels = list(image.getdata())
                non_transparent_pixels = [p for p in pixels if p[3] > 128]
                
                if not non_transparent_pixels:
                    return (47, 49, 54)  # Discord's dark theme color
                
                r_total = sum(p[0] for p in non_transparent_pixels)
                g_total = sum(p[1] for p in non_transparent_pixels)
                b_total = sum(p[2] for p in non_transparent_pixels)
                
                count = len(non_transparent_pixels)
                return (
                    r_total // count,
                    g_total // count,
                    b_total // count
                )
    except Exception as e:
        logger.error(f"Error extracting color: {e}")
        return (47, 49, 54)

class RateLimiter:
    def __init__(self, calls_per_second: float):
        self.calls_per_second = calls_per_second
        self.minimum_interval = 1.0 / calls_per_second
        self.last_call_time = 0.0

    async def wait(self):
        """Wait if necessary to respect rate limits"""
        current_time = datetime.now().timestamp()
        time_since_last_call = current_time - self.last_call_time
        
        if time_since_last_call < self.minimum_interval:
            await asyncio.sleep(self.minimum_interval - time_since_last_call)
        
        self.last_call_time = datetime.now().timestamp()

class GameData:
    def __init__(self, 
                 title: str,
                 store_url: str,
                 image_url: str,
                 end_date: datetime,
                 store: str,
                 game_type: str = "GAME",
                 rating: float = 0.0,
                 price_original: float = 0.0,
                 regions: list = None,
                 adult_content: bool = False):
        self.title = title
        self.store_url = store_url
        self.image_url = image_url
        self.end_date = end_date
        self.store = store
        self.game_type = game_type
        self.rating = rating
        self.price_original = price_original
        self.regions = regions or ["GLOBAL"]
        self.adult_content = adult_content

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "store_url": self.store_url,
            "image_url": self.image_url,
            "end_date": self.end_date.isoformat(),
            "store": self.store,
            "game_type": self.game_type,
            "rating": self.rating,
            "price_original": self.price_original,
            "regions": self.regions,
            "adult_content": self.adult_content
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'GameData':
        data['end_date'] = datetime.fromisoformat(data['end_date'])
        return cls(**data)

async def validate_api_key(session: aiohttp.ClientSession, store: str, api_key: str) -> bool:
    """Validate API key for different stores"""
    validators = {
        "epic": validate_epic_key,
        "steam": validate_steam_key,
        "gog": validate_gog_key,
        "humble": validate_humble_key,
        "itch": validate_itch_key,
        "origin": validate_origin_key,
        "ubisoft": validate_ubisoft_key
    }
    
    validator = validators.get(store.lower())
    if validator:
        return await validator(session, api_key)
    return False

# Store-specific API key validators
async def validate_epic_key(session: aiohttp.ClientSession, api_key: str) -> bool:
    try:
        headers = {"Authorization": f"Bearer {api_key}"}
        async with session.get("https://api.epicgames.dev/epic/oauth/verify", headers=headers) as response:
            return response.status == 200
    except:
        return False

# Add similar validators for other stores...
