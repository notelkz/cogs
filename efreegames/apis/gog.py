from .base import StoreAPI, GameData
from datetime import datetime
import aiohttp
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)

class GOGApi(StoreAPI):
    BASE_URL = "https://api.gog.com/v2"
    STORE_URL = "https://www.gog.com"
    
    async def get_free_games(self) -> List[GameData]:
        await self.initialize()
        
        try:
            url = f"{self.BASE_URL}/games/filtered?price=free&order=desc:releaseDate"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            
            async with self.session.get(url, headers=headers) as response:
                if await self.handle_rate_limit(response):
                    return await self.get_free_games()
                    
                data = await response.json()
                games = []
                
                for product in data.get('products', []):
                    if self._is_currently_free(product):
                        game_data = await self._parse_game_data(product)
                        games.append(game_data)
                
                return games
                
        except Exception as e:
            logger.error(f"Error fetching GOG free games: {str(e)}")
            raise

    def _is_currently_free(self, product: Dict) -> bool:
        """Check if game is currently free"""
        price_data = product.get('price', {})
        return (price_data.get('isFree', False) or
                (price_data.get('baseAmount', 0) > 0 and
                 price_data.get('finalAmount', 0) == 0))

    async def _parse_game_data(self, product: Dict) -> GameData:
        """Parse game data into GameData object"""
        game_data = GameData()
        game_data.id = str(product['id'])
        game_data.title = product['title']
        game_data.store_url = f"{self.STORE_URL}/game/{product['slug']}"
        game_data.image_url = product.get('coverHorizontal', '')
        game_data.store = "GOG"
        game_data.type = "DLC" if product.get('type') == "dlc" else "GAME"
        game_data.description = product.get('description', {}).get('lead', '')
        game_data.rating = float(product.get('rating', {}).get('average', 0))
        game_data.is_adult = product.get('age', {}).get('age', 0) >= 18
        game_data.regions = product.get('availability', {}).get('countries', [])
        game_data.color = (132, 46, 176)  # GOG purple
        
        # Parse end date if it's a temporary free promotion
        if 'promotions' in product:
            game_data.end_date = self._parse_promotion_end_date(product['promotions'])
        
        return game_data

    def _parse_promotion_end_date(self, promotions: Dict) -> datetime:
        """Parse end date from promotions data"""
        try:
            for promo in promotions:
                if promo.get('type') == 'free':
                    return datetime.fromisoformat(promo['endDate'])
            return datetime.max
        except (KeyError, IndexError):
            return datetime.max

    async def test_connection(self) -> bool:
        """Test API connection"""
        try:
            url = f"{self.BASE_URL}/account"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            async with self.session.get(url, headers=headers) as response:
                return response.status == 200
        except:
            return False

    async def get_game_details(self, game_id: str) -> GameData:
        """Get detailed game information"""
        url = f"{self.BASE_URL}/games/{game_id}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        
        async with self.session.get(url, headers=headers) as response:
            if await self.handle_rate_limit(response):
                return await self.get_game_details(game_id)
                
            data = await response.json()
            return await self._parse_game_data(data)

    async def get_auth_url(self, user_id: int) -> str:
        """Get URL for account linking"""
        params = {
            'client_id': self.api_key,
            'redirect_uri': f"{self.BASE_URL}/auth/callback?user_id={user_id}",
            'response_type': 'code',
            'scope': 'read'
        }
        
        return f"{self.STORE_URL}/auth/authorize?{urllib.parse.urlencode(params)}"
