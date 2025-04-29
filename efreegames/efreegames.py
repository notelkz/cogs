import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from datetime import datetime, timezone, timedelta
import aiohttp
import asyncio
import base64
import pytz
from typing import Dict, Optional, List
import json

class EFreeGames(commands.Cog):
    """Track and post free games from various storefronts."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.session = aiohttp.ClientSession()
        
        # API endpoints
        self.epic_api_url = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
        self.steam_api_url = "https://store.steampowered.com/api/featuredcategories"
        
        # Store colors for different storefronts
        self.store_colors = {
            "epic": 0x2F2F2F,  # Epic Games Store gray
            "steam": 0x1b2838,  # Steam navy blue
        }
        
        default_guild = {
            "channel_id": None,
            "store_threads": {},
            "last_posted_games": {}
        }
        
        default_global = {
            "epic": {
                "client_id": None,
                "client_secret": None,
                "access_token": None,
                "token_expires": None
            },
            "steam": {
                "api_key": None
            }
        }
        
        self.config.register_guild(**default_guild)
        self.config.register_global(**default_global)
        
        self.bg_task = self.bot.loop.create_task(self.check_free_games_schedule())

    def cog_unload(self):
        """Cleanup when cog is unloaded."""
        if self.bg_task:
            self.bg_task.cancel()
        asyncio.create_task(self.session.close())

    @commands.group(name="efreegames")
    async def efreegames(self, ctx):
        """Configure free games notifications."""
        pass

    @efreegames.command(name="setchannel")
    @commands.admin_or_permissions(manage_channels=True)
    async def set_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel for free games notifications."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"Free games will be posted in {channel.mention}")

    @efreegames.command(name="setthread")
    @commands.admin_or_permissions(manage_channels=True)
    async def set_thread(self, ctx, store: str, thread: discord.Thread):
        """Set a thread for a specific storefront."""
        store = store.lower()
        if store not in ["epic", "steam"]:
            return await ctx.send("Invalid store. Supported stores: epic, steam")
        
        async with self.config.guild(ctx.guild).store_threads() as threads:
            threads[store] = thread.id
        await ctx.send(f"Games from {store.title()} will be posted in {thread.mention}")

    @efreegames.command(name="epiccreds")
    @checks.is_owner()
    async def set_epic_credentials(self, ctx, client_id: str, client_secret: str):
        """
        Set Epic Games Store API credentials.
        
        Get these from https://dev.epicgames.com/portal/
        """
        try:
            await ctx.message.delete()
        except:
            pass
            
        async with self.config.epic() as epic_config:
            epic_config["client_id"] = client_id
            epic_config["client_secret"] = client_secret
            epic_config["access_token"] = None
            epic_config["token_expires"] = None
            
        await ctx.send("Epic Games Store credentials have been set.", delete_after=10)
    @efreegames.command(name="steamkey")
    @checks.is_owner()
    async def set_steam_key(self, ctx, api_key: str):
        """
        Set Steam Web API key.
        
        Get this from https://steamcommunity.com/dev/apikey
        """
        try:
            await ctx.message.delete()
        except:
            pass
            
        async with self.config.steam() as steam_config:
            steam_config["api_key"] = api_key
            
        await ctx.send("Steam API key has been set.", delete_after=10)

    @efreegames.command(name="showconfig")
    @checks.is_owner()
    async def show_config(self, ctx):
        """Show the current API configuration status (without showing the actual credentials)."""
        epic_config = await self.config.epic()
        steam_config = await self.config.steam()
        
        embed = discord.Embed(title="Free Games API Configuration", color=discord.Color.blue())
        
        epic_status = "✅ Configured" if epic_config["client_id"] else "❌ Not configured"
        epic_token = "✅ Valid" if await self.is_epic_token_valid() else "❌ Invalid/Missing"
        embed.add_field(
            name="Epic Games Store",
            value=f"Status: {epic_status}\nToken: {epic_token}",
            inline=False
        )
        
        steam_status = "✅ Configured" if steam_config["api_key"] else "❌ Not configured"
        embed.add_field(
            name="Steam",
            value=f"Status: {steam_status}",
            inline=False
        )
        
        await ctx.send(embed=embed)

    async def is_epic_token_valid(self) -> bool:
        """Check if the current Epic Games Store token is valid."""
        epic_config = await self.config.epic()
        if not epic_config["access_token"] or not epic_config["token_expires"]:
            return False
            
        expires = datetime.fromisoformat(epic_config["token_expires"])
        return datetime.now(timezone.utc) < expires

    async def get_epic_token(self) -> Optional[str]:
        """Get a valid Epic Games Store access token."""
        epic_config = await self.config.epic()
        
        # Check if we have valid credentials
        if not epic_config["client_id"] or not epic_config["client_secret"]:
            return None
            
        # Check if current token is still valid
        if await self.is_epic_token_valid():
            return epic_config["access_token"]
            
        # Get new token
        token_url = "https://account-public-service-prod.ol.epicgames.com/account/api/oauth/token"
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": "Basic " + base64.b64encode(
                f"{epic_config['client_id']}:{epic_config['client_secret']}".encode()
            ).decode()
        }
        data = {
            "grant_type": "client_credentials"
        }
        
        try:
            async with self.session.post(token_url, headers=headers, data=data) as response:
                if response.status == 200:
                    token_data = await response.json()
                    
                    # Save new token
                    async with self.config.epic() as epic_config:
                        epic_config["access_token"] = token_data["access_token"]
                        epic_config["token_expires"] = (
                            datetime.now(timezone.utc) + 
                            timedelta(seconds=token_data["expires_in"])
                        ).isoformat()
                    
                    return token_data["access_token"]
        except Exception as e:
            print(f"Error getting Epic token: {e}")
            return None
    async def fetch_epic_games(self) -> List[Dict]:
        """Fetch free games from Epic Games Store using authenticated API."""
        token = await self.get_epic_token()
        if not token:
            print("No valid Epic Games Store token available")
            return []
            
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json"
        }
        
        try:
            params = {
                "locale": "en-US",
                "country": "US",
                "allowCountries": "US"
            }
            
            async with self.session.get(
                self.epic_api_url,
                params=params,
                headers=headers
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    free_games = []
                    
                    for game in data.get("data", {}).get("Catalog", {}).get("searchStore", {}).get("elements", []):
                        promotions = game.get("promotions")
                        if not promotions:
                            continue
                            
                        promotional_offers = promotions.get("promotionalOffers", [])
                        upcoming_offers = promotions.get("upcomingPromotionalOffers", [])
                        
                        for offer in promotional_offers:
                            for promo in offer.get("promotionalOffers", []):
                                if promo.get("discountSetting", {}).get("discountPercentage") == 0:
                                    start_date = datetime.fromisoformat(promo.get("startDate", "").replace("Z", "+00:00"))
                                    end_date = datetime.fromisoformat(promo.get("endDate", "").replace("Z", "+00:00"))
                                    
                                    if start_date <= datetime.now(timezone.utc) <= end_date:
                                        free_games.append({
                                            "title": game.get("title", "Unknown"),
                                            "store_url": f"https://store.epicgames.com/en-US/p/{game.get('urlSlug')}",
                                            "image_url": game.get("keyImages", [{}])[0].get("url"),
                                            "end_time": end_date,
                                            "description": game.get("description", ""),
                                            "publisher": game.get("seller", {}).get("name", "Unknown Publisher")
                                        })
                    
                    return free_games
                    
        except Exception as e:
            print(f"Error fetching Epic Games: {e}")
            return []

    async def fetch_steam_games(self) -> List[Dict]:
        """Fetch free games from Steam using API key."""
        steam_config = await self.config.steam()
        api_key = steam_config["api_key"]
        
        if not api_key:
            print("No Steam API key configured")
            return []
            
        try:
            params = {
                "key": api_key,
                "format": "json"
            }
            
            async with self.session.get(self.steam_api_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    free_games = []
                    
                    specials = data.get("specials", {}).get("items", [])
                    
                    for game in specials:
                        if game.get("discount_percent") == 100:
                            app_id = game.get("id")
                            game_details_url = f"https://store.steampowered.com/api/appdetails?appids={app_id}"
                            
                            async with self.session.get(game_details_url) as detail_response:
                                if detail_response.status == 200:
                                    detail_data = await detail_response.json()
                                    game_data = detail_data.get(str(app_id), {}).get("data", {})
                                    
                                    if game_data:
                                        end_time = datetime.now(timezone.utc)
                                        end_time = end_time.replace(hour=23, minute=59, second=59)
                                        
                                        free_games.append({
                                            "title": game_data.get("name", "Unknown"),
                                            "store_url": f"https://store.steampowered.com/app/{app_id}",
                                            "image_url": game_data.get("header_image"),
                                            "end_time": end_time,
                                            "description": game_data.get("short_description", ""),
                                            "publisher": game_data.get("publishers", ["Unknown Publisher"])[0]
                                        })
                    
                    return free_games
                    
        except Exception as e:
            print(f"Error fetching Steam games: {e}")
            return []
    async def create_game_embed(self, game: Dict, store: str) -> discord.Embed:
        """Create an enhanced embed for a free game."""
        embed = discord.Embed(
            title=game["title"],
            url=game["store_url"],
            description=f"{game.get('description', '')}\n\nFree to claim until: <t:{int(game['end_time'].timestamp())}:R>",
            color=self.store_colors.get(store, 0x000000)
        )
        
        if game.get("image_url"):
            embed.set_image(url=game["image_url"])
            
        if game.get("publisher"):
            embed.add_field(name="Publisher", value=game["publisher"], inline=True)
            
        embed.set_footer(text=f"Via {store.title()}")
        
        return embed

    async def check_free_games_schedule(self):
        """Background task to check for free games at scheduled time."""
        await self.bot.wait_until_ready()
        
        while True:
            try:
                # Get current time in BST
                now = datetime.now(pytz.timezone('Europe/London'))
                
                # Check if it's 12 PM BST
                if now.hour == 12 and now.minute == 0:
                    # Fetch games from all stores
                    epic_games = await self.fetch_epic_games()
                    steam_games = await self.fetch_steam_games()
                    
                    # Post to all configured guilds
                    all_guilds = await self.config.all_guilds()
                    for guild_id in all_guilds:
                        guild = self.bot.get_guild(guild_id)
                        if not guild:
                            continue
                            
                        channel_id = all_guilds[guild_id]["channel_id"]
                        if not channel_id:
                            continue
                            
                        channel = guild.get_channel(channel_id)
                        if not channel:
                            continue
                            
                        store_threads = all_guilds[guild_id].get("store_threads", {})
                        
                        # Post Epic Games
                        for game in epic_games:
                            embed = await self.create_game_embed(game, "epic")
                            if "epic" in store_threads:
                                thread = guild.get_thread(store_threads["epic"])
                                if thread:
                                    await thread.send(embed=embed)
                                    continue
                            await channel.send(embed=embed)
                        
                        # Post Steam Games
                        for game in steam_games:
                            embed = await self.create_game_embed(game, "steam")
                            if "steam" in store_threads:
                                thread = guild.get_thread(store_threads["steam"])
                                if thread:
                                    await thread.send(embed=embed)
                                    continue
                            await channel.send(embed=embed)
                
            except Exception as e:
                print(f"Error in free games check schedule: {e}")
                
            # Wait for 60 seconds before checking again
            await asyncio.sleep(60)

    @efreegames.command(name="test")
    @checks.admin_or_permissions(manage_channels=True)
    async def test_creds(self, ctx):
        """Test the configured API credentials."""
        async with ctx.typing():
            epic_token = await self.get_epic_token()
            steam_config = await self.config.steam()
            
            embed = discord.Embed(
                title="API Credentials Test",
                color=discord.Color.blue()
            )
            
            # Test Epic Games Store
            if epic_token:
                embed.add_field(
                    name="Epic Games Store",
                    value="✅ Successfully authenticated",
                    inline=False
                )
            else:
                embed.add_field(
                    name="Epic Games Store",
                    value="❌ Authentication failed",
                    inline=False
                )
            
            # Test Steam
            if steam_config["api_key"]:
                test_url = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"
                params = {
                    "key": steam_config["api_key"],
                    "steamids": "76561197960435530"
                }
                
                try:
                    async with self.session.get(test_url, params=params) as response:
                        if response.status == 200:
                            embed.add_field(
                                name="Steam",
                                value="✅ API key valid",
                                inline=False
                            )
                        else:
                            embed.add_field(
                                name="Steam",
                                value="❌ API key invalid",
                                inline=False
                            )
                except:
                    embed.add_field(
                        name="Steam",
                        value="❌ Connection error",
                        inline=False
                    )
            else:
                embed.add_field(
                    name="Steam",
                    value="❌ API key not configured",
                    inline=False
                )
            
            await ctx.send(embed=embed)

    @efreegames.command(name="check")
    async def check_free(self, ctx):
        """Manually check for free games."""
        async with ctx.typing():
            epic_games = await self.fetch_epic_games()
            steam_games = await self.fetch_steam_games()
            
            if not epic_games and not steam_games:
                return await ctx.send("No free games found at the moment.")
            
            for game in epic_games:
                embed = await self.create_game_embed(game, "epic")
                await ctx.send(embed=embed)
                
            for game in steam_games:
                embed = await self.create_game_embed(game, "steam")
                await ctx.send(embed=embed)
