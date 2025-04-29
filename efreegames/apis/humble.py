from .base import StoreAPI, GameData
from datetime import datetime
import aiohttp
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)

class HumbleBundleAPI(StoreAPI):
    BASE_URL = "https://www.humblebundle.com/api/v1"
    STORE_URL = "https://www.humblebundle.com"
    
    async def get_free_games(self) -> List[GameData]:
        await self.initialize()
        
        try:
            url = f"{self.BASE_URL}/store/search?sort=newest&filter=all&price=free"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            
            async with self.session.get(url, headers=headers) as response:
                if await self.handle_rate_limit(response):
                    return await self.get_free_games()
                    
                data = await response.json()
                games = []
                
                for result in data.get('results', []):
                    if self._is_free_game(result):
                        game_data = await self._parse_game_data(result)
                        games.append(game_data)
                
                return games
                
        except Exception as e:
            logger.error(f"Error fetching Humble Bundle free games: {str(e)}")
            raise

    def _is_free_game(self, result: Dict) -> bool:
        """Check if game is currently free"""
        return (result.get('current_price', {}).get('amount', 0) == 0 and
                result.get('full_price', {}).get('amount', 0) > 0)

    async def _parse_game_data(self, result: Dict) -> GameData:
        """Parse game data into GameData object"""
        game_data = GameData()
        game_data.id = result['machine_name']
        game_data.title = result['human_name']
        game_data.store_url = f"{self.STORE_URL}/store/games/{result['machine_name']}"
        game_data.image_url = result.get('featured_image_large', '')
        game_data.store = "HumbleBundle"
        game_data.type = "DLC" if result.get('type') == "dlc" else "GAME"
        game_data.description = result.get('description', '')
        game_data.rating = float(result.get('review_score', 0))
        game_data.is_adult = result.get('mature_content', False)
        game_data.regions = result.get('available_regions', [])
        game_data.color = (201, 55, 57)  # Humble Bundle red
        
        # Parse end date if available
        if 'promotion_end' in result:
            game_data.end_date = datetime.fromtimestamp(result['promotion_end'])
        else:
            game_data.end_date = datetime.max
        
        return game_data

    async def test_connection(self) -> bool:
        """Test API connection"""
        try:
            url = f"{self.BASE_URL}/user/info"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            async with self.session.get(url, headers=headers) as response:
                return response.status == 200
        except:
            return False

    async def get_game_details(self, game_id: str) -> GameData:
        """Get detailed game information"""
        url = f"{self.BASE_URL}/store/products/{game_id}"
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
            'scope': 'store'
        }
        
        return f"{self.STORE_URL}/auth?{urllib.parse.urlencode(params)}"
