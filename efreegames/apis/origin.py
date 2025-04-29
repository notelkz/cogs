from .base import StoreAPI, GameData
from datetime import datetime
import aiohttp
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)

class OriginAPI(StoreAPI):
    BASE_URL = "https://api.origin.com/ecommerce2/public/v1"
    STORE_URL = "https://www.origin.com"
    
    async def get_free_games(self) -> List[GameData]:
        await self.initialize()
        
        try:
            url = f"{self.BASE_URL}/offers/search"
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "X-Origin-Platform": "PCWIN"
            }
            params = {
                "free": "true",
                "sort": "newest",
                "limit": 100
            }
            
            async with self.session.get(url, headers=headers, params=params) as response:
                if await self.handle_rate_limit(response):
                    return await self.get_free_games()
                    
                data = await response.json()
                games = []
                
                for offer in data.get('offers', []):
                    if self._is_free_game(offer):
                        game_data = await self._parse_game_data(offer)
                        games.append(game_data)
                
                return games
                
        except Exception as e:
            logger.error(f"Error fetching Origin free games: {str(e)}")
            raise

    def _is_free_game(self, offer: Dict) -> bool:
        """Check if game is currently free"""
        pricing = offer.get('pricing', {})
        return (pricing.get('currentPrice', 0) == 0 and
                pricing.get('originalPrice', 0) > 0)

    async def _parse_game_data(self, offer: Dict) -> GameData:
        """Parse game data into GameData object"""
        game_data = GameData()
        game_data.id = offer['offerId']
        game_data.title = offer['displayName']
        game_data.store_url = f"{self.STORE_URL}/store/games/{offer['slug']}"
        game_data.image_url = offer.get('imageServer', '') + offer.get('keyArt', '')
        game_data.store = "Origin"
        game_data.type = "DLC" if offer.get('itemType') == "DLC" else "GAME"
        game_data.description = offer.get('description', '')
        game_data.rating = float(offer.get('rating', {}).get('average', 0))
        game_data.is_adult = offer.get('esrbRating', '') in ['M', 'AO']
        game_data.regions = offer.get('availableRegions', [])
        game_data.color = (242, 102, 22)  # Origin orange
        
        # Parse promotion end date if available
        if 'promotionEndDate' in offer:
            game_data.end_date = datetime.fromisoformat(offer['promotionEndDate'])
        else:
            game_data.end_date = datetime.max
        
        return game_data

    async def test_connection(self) -> bool:
        """Test API connection"""
        try:
            url = f"{self.BASE_URL}/authentication/status"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            async with self.session.get(url, headers=headers) as response:
                return response.status == 200
        except:
            return False

    async def get_game_details(self, game_id: str) -> GameData:
        """Get detailed game information"""
        url = f"{self.BASE_URL}/offers/{game_id}"
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
            'response_type': 'code',
            'redirect_uri': f"{self.BASE_URL}/auth/callback?user_id={user_id}",
            'scope': 'basic.identity offline_access'
        }
        
        return f"{self.STORE_URL}/connect/auth?{urllib.parse.urlencode(params)}"
