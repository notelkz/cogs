from .base import StoreAPI, GameData
from datetime import datetime
import aiohttp
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)

class EpicGamesAPI(StoreAPI):
    BASE_URL = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
    AUTH_URL = "https://www.epicgames.com/id/api/redirect"
    
    async def get_free_games(self) -> List[GameData]:
        await self.initialize()
        
        try:
            async with self.session.get(self.BASE_URL) as response:
                if await self.handle_rate_limit(response):
                    return await self.get_free_games()
                    
                data = await response.json()
                games = []
                
                for game in data.get('data', {}).get('Catalog', {}).get('searchStore', {}).get('elements', []):
                    if self._is_free_game(game):
                        game_data = await self._parse_game_data(game)
                        games.append(game_data)
                
                return games
                
        except Exception as e:
            logger.error(f"Error fetching Epic free games: {str(e)}")
            raise

    def _is_free_game(self, game: Dict) -> bool:
        """Check if game is currently free"""
        if not game.get('promotions'):
            return False
            
        for promo in game['promotions'].get('promotionalOffers', []):
            for offer in promo.get('promotionalOffers', []):
                if offer.get('discountSetting', {}).get('discountPercentage') == 100:
                    return True
        return False

    async def _parse_game_data(self, game: Dict) -> GameData:
        """Parse game data into GameData object"""
        game_data = GameData()
        game_data.id = game['id']
        game_data.title = game['title']
        game_data.store_url = f"https://store.epicgames.com/p/{game['urlSlug']}"
        game_data.image_url = self._get_best_image(game['keyImages'])
        game_data.store = "Epic"
        game_data.type = "GAME" if game['offerType'] == "BASE_GAME" else "DLC"
        game_data.end_date = self._parse_end_date(game)
        game_data.color = (0, 55, 133)  # Epic Games blue
        game_data.description = game.get('description', '')
        game_data.rating = float(game.get('rating', {}).get('averageRating', 0))
        game_data.is_adult = game.get('rating', {}).get('ratingSystem') == "ESRB" and \
                            game.get('rating', {}).get('ratingValue') == "M"
        game_data.regions = game.get('regions', [])
        
        return game_data

    def _get_best_image(self, images: List[Dict]) -> str:
        """Get the best quality image URL"""
        priority = ['DieselStoreFrontWide', 'OfferImageWide', 'Thumbnail']
        
        for type_name in priority:
            for image in images:
                if image.get('type') == type_name:
                    return image['url']
        
        return images[0]['url'] if images else ""

    def _parse_end_date(self, game: Dict) -> datetime:
        """Parse end date from game data"""
        try:
            promo = game['promotions']['promotionalOffers'][0]
            end_date = promo['promotionalOffers'][0]['endDate']
            return datetime.fromisoformat(end_date.replace('Z', '+00:00'))
        except (KeyError, IndexError):
            return datetime.max

    async def test_connection(self) -> bool:
        """Test API connection"""
        try:
            async with self.session.get(self.BASE_URL) as response:
                return response.status == 200
        except:
            return False

    async def get_game_details(self, game_id: str) -> GameData:
        """Get detailed game information"""
        url = f"https://store-content.ak.epicgames.com/api/en-US/content/products/{game_id}"
        
        async with self.session.get(url) as response:
            if await self.handle_rate_limit(response):
                return await self.get_game_details(game_id)
                
            data = await response.json()
            return await self._parse_game_data(data)

    async def get_auth_url(self, user_id: int) -> str:
        """Get URL for account linking"""
        params = {
            'client_id': self.api_key,
            'response_type': 'code',
            'redirect_uri': f"{self.AUTH_URL}?user_id={user_id}"
        }
        
        return f"https://www.epicgames.com/id/authorize?{urllib.parse.urlencode(params)}"
