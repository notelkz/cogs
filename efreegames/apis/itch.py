from .base import StoreAPI, GameData
from datetime import datetime
import aiohttp
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)

class ItchioAPI(StoreAPI):
    BASE_URL = "https://itch.io/api/1"
    STORE_URL = "https://itch.io"
    
    async def get_free_games(self) -> List[GameData]:
        await self.initialize()
        
        try:
            url = f"{self.BASE_URL}/games/search?price=free&sort=newest"
            headers = {"Authorization": f"Bearer {self.api_key}"}
            
            async with self.session.get(url, headers=headers) as response:
                if await self.handle_rate_limit(response):
                    return await self.get_free_games()
                    
                data = await response.json()
                games = []
                
                for game in data.get('games', []):
                    if self._is_free_game(game):
                        game_data = await self._parse_game_data(game)
                        games.append(game_data)
                
                return games
                
        except Exception as e:
            logger.error(f"Error fetching itch.io free games: {str(e)}")
            raise

    def _is_free_game(self, game: Dict) -> bool:
        """Check if game is currently free"""
        return (game.get('price', 0) == 0 and
                not game.get('min_price', 0) > 0)

    async def _parse_game_data(self, game: Dict) -> GameData:
        """Parse game data into GameData object"""
        game_data = GameData()
        game_data.id = str(game['id'])
        game_data.title = game['title']
        game_data.store_url = game['url']
        game_data.image_url = game.get('cover_url', '')
        game_data.store = "Itch.io"
        game_data.type = "GAME"  # Itch.io doesn't distinguish DLCs
        game_data.description = game.get('short_description', '')
        game_data.rating = float(game.get('rating', 0))
        game_data.is_adult = game.get('classification', '') == "mature"
        game_data.regions = []  # Itch.io is generally worldwide
        game_data.color = (250, 92, 92)  # Itch.io red
        
        # Itch.io doesn't typically have end dates for free games
        game_data.end_date = datetime.max
        
        return game_data

    async def test_connection(self) -> bool:
        """Test API connection"""
        try:
            url = f"{self.BASE_URL}/me"
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
            return await self._parse_game_data(data['game'])

    async def get_auth_url(self, user_id: int) -> str:
        """Get URL for account linking"""
        params = {
            'client_id': self.api_key,
            'scope': 'profile:games',
            'response_type': 'code',
            'redirect_uri': f"{self.BASE_URL}/oauth/callback?user_id={user_id}"
        }
        
        return f"{self.STORE_URL}/user/oauth?{urllib.parse.urlencode(params)}"
