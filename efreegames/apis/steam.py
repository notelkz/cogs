from .base import StoreAPI, GameData
from datetime import datetime
import aiohttp
from typing import List, Dict
import logging

logger = logging.getLogger(__name__)

class SteamAPI(StoreAPI):
    BASE_URL = "https://api.steampowered.com"
    STORE_URL = "https://store.steampowered.com"
    
    async def get_free_games(self) -> List[GameData]:
        await self.initialize()
        
        try:
            url = f"{self.BASE_URL}/ISteamApps/GetAppList/v2/"
            async with self.session.get(url) as response:
                if await self.handle_rate_limit(response):
                    return await self.get_free_games()
                    
                data = await response.json()
                games = []
                
                for app in data['applist']['apps']:
                    if await self._is_free_game(app['appid']):
                        game_data = await self.get_game_details(str(app['appid']))
                        if game_data:
                            games.append(game_data)
                
                return games
                
        except Exception as e:
            logger.error(f"Error fetching Steam free games: {str(e)}")
            raise

    async def _is_free_game(self, app_id: int) -> bool:
        """Check if game is currently free"""
        url = f"{self.STORE_URL}/api/appdetails?appids={app_id}"
        
        async with self.session.get(url) as response:
            if await self.handle_rate_limit(response):
                return await self._is_free_game(app_id)
                
            data = await response.json()
            
            if str(app_id) not in data:
                return False
                
            app_data = data[str(app_id)]
            if not app_data['success']:
                return False
                
            return (app_data['data'].get('is_free', False) or
                    (app_data['data'].get('price_overview', {}).get('initial', 0) > 0 and
                     app_data['data'].get('price_overview', {}).get('final', 0) == 0))

    async def get_game_details(self, game_id: str) -> GameData:
        """Get detailed game information"""
        url = f"{self.STORE_URL}/api/appdetails?appids={game_id}"
        
        async with self.session.get(url) as response:
            if await self.handle_rate_limit(response):
                return await self.get_game_details(game_id)
                
            data = await response.json()
            
            if game_id not in data or not data[game_id]['success']:
                return None
                
            app_data = data[game_id]['data']
            
            game_data = GameData()
            game_data.id = game_id
            game_data.title = app_data['name']
            game_data.store_url = f"{self.STORE_URL}/app/{game_id}"
            game_data.image_url = app_data['header_image']
            game_data.store = "Steam"
            game_data.type = "DLC" if app_data['type'] == "dlc" else "GAME"
            game_data.description = app_data.get('short_description', '')
            game_data.rating = float(app_data.get('metacritic', {}).get('score', 0))
            game_data.is_adult = app_data.get('required_age', 0) >= 18
            game_data.regions = self._parse_regions(app_data)
            game_data.color = (0, 174, 239)  # Steam blue
            
            # Parse free weekend or temporary free period
            if 'price_overview' in app_data:
                game_data.end_date = await self._get_free_period_end(game_id)
            
            return game_data

    def _parse_regions(self, app_data: Dict) -> List[str]:
        """Parse available regions from app data"""
        regions = []
        if 'countries' in app_data:
            countries = app_data['countries'].split(',')
            regions.extend(countries)
        return regions

    async def _get_free_period_end(self, game_id: str) -> datetime:
        """Get end date of free period"""
        url = f"{self.STORE_URL}/api/appdetails?appids={game_id}&filters=price_overview"
        
        async with self.session.get(url) as response:
            if await self.handle_rate_limit(response):
                return await self._get_free_period_end(game_id)
                
            data = await response.json()
            
            try:
                end_date = data[game_id]['data']['price_overview']['free_end_date']
                return datetime.fromtimestamp(end_date)
            except (KeyError, TypeError):
                return datetime.max

    async def test_connection(self) -> bool:
        """Test API connection"""
        try:
            url = f"{self.BASE_URL}/ISteamApps/GetAppList/v2/"
            async with self.session.get(url) as response:
                return response.status == 200
        except:
            return False

    async def get_auth_url(self, user_id: int) -> str:
        """Get URL for account linking"""
        params = {
            'openid.ns': 'http://specs.openid.net/auth/2.0',
            'openid.mode': 'checkid_setup',
            'openid.return_to': f"{self.BASE_URL}/auth?user_id={user_id}",
            'openid.realm': self.BASE_URL,
            'openid.identity': 'http://specs.openid.net/auth/2.0/identifier_select',
            'openid.claimed_id': 'http://specs.openid.net/auth/2.0/identifier_select'
        }
        
        return f"https://steamcommunity.com/openid/login?{urllib.parse.urlencode(params)}"
