from .base import StoreAPI, GameData
from datetime import datetime
import aiohttp
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)

class UbisoftAPI(StoreAPI):
    BASE_URL = "https://public-ubiservices.ubi.com/v1"
    STORE_URL = "https://store.ubisoft.com"
    
    async def get_free_games(self) -> List[GameData]:
        await self.initialize()
        
        try:
            url = f"{self.BASE_URL}/spaces/store/products"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Ubi-AppId": self.api_secret,
                "Ubi-LocaleCode": "en-US"
            }
            params = {
                "free": True,
                "sortBy": "releaseDate",
                "sortOrder": "desc"
            }
            
            async with self.session.get(url, headers=headers, params=params) as response:
                if await self.handle_rate_limit(response):
                    return await self.get_free_games()
                    
                data = await response.json()
                games = []
                
                for product in data.get('products', []):
                    if self._is_free_game(product):
                        game_data = await self._parse_game_data(product)
                        games.append(game_data)
                
                return games
                
        except Exception as e:
            logger.error(f"Error fetching Ubisoft free games: {str(e)}")
            raise

    def _is_free_game(self, product: Dict) -> bool:
        """Check if game is currently free"""
        price_info = product.get('price', {})
        return (price_info.get('current', 0) == 0 and
                price_info.get('base', 0) > 0)

    async def _parse_game_data(self, product: Dict) -> GameData:
        """Parse game data into GameData object"""
        game_data = GameData()
        game_data.id = product['id']
        game_data.title = product['name']
        game_data.store_url = f"{self.STORE_URL}/game/{product['slug']}"
        game_data.image_url = product.get('thumbnail', {}).get('url', '')
        game_data.store = "Ubisoft"
        game_data.type = "DLC" if product.get('type') == "DLC" else "GAME"
        game_data.description = product.get('description', '')
        game_data.rating = float(product.get('rating', {}).get('average', 0))
        game_data.is_adult = product.get('ageRating', {}).get('rating', '') in ['MATURE', 'ADULTS_ONLY']
        game_data.regions = product.get('availableCountries', [])
        game_data.color = (0, 85, 204)  # Ubisoft blue
        
        # Parse promotion end date
        if 'promotion' in product:
            game_data.end_date = datetime.fromisoformat(product['promotion']['endDate'])
        else:
            game_data.end_date = datetime.max
        
        return game_data

    async def test_connection(self) -> bool:
        """Test API connection"""
        try:
            url = f"{self.BASE_URL}/profiles/me"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Ubi-AppId": self.api_secret
            }
            async with self.session.get(url, headers=headers) as response:
                return response.status == 200
        except:
            return False

    async def get_game_details(self, game_id: str) -> GameData:
        """Get detailed game information"""
        url = f"{self.BASE_URL}/spaces/store/products/{game_id}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Ubi-AppId": self.api_secret
        }
        
        async with self.session.get(url, headers=headers) as response:
            if await self.handle_rate_limit(response):
                return await self.get_game_details(game_id)
                
            data = await response.json()
            return await self._parse_game_data(data)

    async def get_auth_url(self, user_id: int) -> str:
        """Get URL for account linking"""
        params = {
            'client_id': self.api_key,
            'response_type': 'code',
            'redirect_uri': f"{self.BASE_URL}/auth/callback?user_id={user_id}",
            'scope': 'store.access'
        }
        
        return f"{self.STORE_URL}/connect/oauth2/auth?{urllib.parse.urlencode(params)}"
