import aiohttp
from datetime import datetime
from .base import StoreAPI, GameData

class EpicGamesAPI(StoreAPI):
    BASE_URL = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
    
    async def initialize(self):
        self.session = aiohttp.ClientSession()

    async def get_free_games(self) -> List[GameData]:
        async with self.session.get(self.BASE_URL) as response:
            data = await response.json()
            games = []
            
            for game in data.get('data', {}).get('Catalog', {}).get('searchStore', {}).get('elements', []):
                if game.get('promotions'):
                    game_data = GameData()
                    game_data.title = game['title']
                    game_data.store_url = f"https://store.epicgames.com/p/{game['urlSlug']}"
                    game_data.image_url = game['keyImages'][0]['url']
                    game_data.store = "Epic"
                    game_data.type = "GAME" if game['offerType'] == "BASE_GAME" else "DLC"
                    game_data.end_date = datetime.fromisoformat(game['promotions']['promotionalOffers'][0]['endDate'])
                    game_data.color = (0, 55, 133)  # Epic Games blue
                    games.append(game_data)
            
            return games

    async def test_connection(self) -> bool:
        try:
            async with self.session.get(self.BASE_URL) as response:
                return response.status == 200
        except:
            return False

    async def get_game_details(self, game_id: str) -> GameData:
        # Implementation for getting detailed game information
        pass
