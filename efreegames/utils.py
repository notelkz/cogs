import aiohttp
import asyncio
from typing import Dict, Optional
import json
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

async def fetch_url(url: str, headers: Optional[Dict] = None) -> Dict:
    """Fetch JSON data from URL with error handling"""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers=headers) as response:
                if response.status == 429:  # Rate limit
                    retry_after = int(response.headers.get('Retry-After', 60))
                    await asyncio.sleep(retry_after)
                    return await fetch_url(url, headers)
                response.raise_for_status()
                return await response.json()
        except aiohttp.ClientError as e:
            logger.error(f"Error fetching {url}: {str(e)}")
            raise

def load_json_file(filepath: str) -> Dict:
    """Load JSON file with error handling"""
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from {filepath}: {str(e)}")
        return {}

def save_json_file(filepath: str, data: Dict):
    """Save data to JSON file"""
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=4)

def parse_currency(price_str: str) -> float:
    """Parse currency string to float"""
    try:
        return float(price_str.replace(',', '').replace('$', '').strip())
    except ValueError:
        return 0.0

def get_store_color(store_name: str) -> tuple:
    """Get RGB color tuple for store"""
    colors = {
        'Epic': (0, 55, 133),
        'Steam': (0, 174, 239),
        'GOG': (132, 46, 176),
        'HumbleBundle': (201, 55, 57),
        'Itch.io': (250, 92, 92),
        'Origin': (242, 102, 22),
        'Ubisoft': (0, 85, 204)
    }
    return colors.get(store_name, (128, 128, 128))
