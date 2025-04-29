# stores.py
import aiohttp
from datetime import datetime, timezone
from typing import List, Optional
from .utils import GameData, RateLimiter
import logging

logger = logging.getLogger("red.efreegames.stores")

class StoreAPI:
    def __init__(self, session: aiohttp.ClientSession, api_key: str):
        self.session = session
        self.api_key = api_key
        self.rate_limiter = RateLimiter(2.0)  # 2 calls per second default

class EpicGamesAPI(StoreAPI):
    async def get_free_games(self) -> List[GameData]:
        await self.rate_limiter.wait()
        
        try:
            headers = {"Authorization": f"Bearer {self.api_key}"}
            async with self.session.get(
                "https://store-site-backend-static.ak.epicgames.com/freeGames",
                headers=headers
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    games = []
                    
                    for game in data.get("data", {}).get("Catalog", {}).get("searchStore", {}).get("elements", []):
                        if game.get("promotions") and game["promotions"].get("promotionalOffers"):
                            game_data = GameData(
                                title=game["title"],
                                store_url=f"https://store.epicgames.com/p/{game['urlSlug']}",
                                image_url=game["keyImages"][0]["url"],
                                end_date=datetime.fromisoformat(
                                    game["promotions"]["promotionalOffers"][0]["promotionalOffers"][0]["endDate"]
                                ),
                                store="epic",
                                game_type="GAME" if game["offerType"] == "BASE_GAME" else "DLC",
                                rating=game.get("rating", {}).get("averageRating", 0.0),
                                price_original=float(game.get("price", {}).get("totalPrice", {}).get("originalPrice", 0)) / 100,
                                regions=game.get("regions", ["GLOBAL"]),
                                adult_content=game.get("mature", False)
                            )
                            games.append(game_data)
                    
                    return games
        except Exception as e:
            logger.error(f"Error fetching Epic Games: {e}")
            return []

class SteamAPI(StoreAPI):
    async def get_free_games(self) -> List[GameData]:
        await self.rate_limiter.wait()
        
        try:
            async with self.session.get(
                "https://api.steampowered.com/ISteamApps/GetAppList/v2/"
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    games = []
                    
                    for app in data["applist"]["apps"]:
                        # Check if game is free
                        app_details_url = f"https://store.steampowered.com/api/appdetails?appids={app['appid']}"
                        
                        await self.rate_limiter.wait()
                        async with self.session.get(app_details_url) as app_response:
                            if app_response.status == 200:
                                app_data = await app_response.json()
                                
                                if app_data[str(app['appid'])]["success"]:
                                    details = app_data[str(app['appid'])]["data"]
                                    
                                    if details.get("is_free") and not details.get("type") == "demo":
                                        game_data = GameData(
                                            title=details["name"],
                                            store_url=f"https://store.steampowered.com/app/{app['appid']}",
                                            image_url=details["header_image"],
                                            end_date=datetime.now(timezone.utc),  # Steam doesn't provide end dates
                                            store="steam",
                                            game_type="DLC" if details["type"] == "dlc" else "GAME",
                                            rating=details.get("metacritic", {}).get("score", 0.0),
                                            price_original=float(details.get("price_overview", {}).get("initial", 0)) / 100,
                                            regions=details.get("supported_languages", "").split(", "),
                                            adult_content=details.get("required_age", 0) >= 18
                                        )
                                        games.append(game_data)
                    
                    return games
        except Exception as e:
            logger.error(f"Error fetching Steam games: {e}")
            return []

# Add similar implementations for other stores...
