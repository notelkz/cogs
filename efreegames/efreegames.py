import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from datetime import datetime, timezone, timedelta
import aiohttp
import asyncio
import base64
import pytz
from typing import Dict, Optional, List, Set
import json
import re

class EFreeGames(commands.Cog):
    """Track and post free games from various storefronts with enhanced features."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.session = aiohttp.ClientSession()
        
        # API endpoints
        self.epic_api_url = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
        self.steam_api_url = "https://store.steampowered.com/api/featuredcategories"
        self.gog_api_url = "https://www.gog.com/games/ajax/filtered"
        self.humble_api_url = "https://www.humblebundle.com/store/api"
        self.origin_api_url = "https://api.origin.com/ecommerce2/public/v1/products"
        self.ubisoft_api_url = "https://store.ubi.com/api/products"
        self.itchio_api_url = "https://itch.io/api/1"
        
        # Store colors
        self.store_colors = {
            "epic": 0x2F2F2F,
            "steam": 0x1b2838,
            "gog": 0x8C2387,
            "humble": 0xCB272C,
            "origin": 0xF56C2D,
            "ubisoft": 0x00C8FF,
            "itchio": 0xFA5C5C
        }
        
        # Valid game types
        self.valid_game_types = ["dlc", "full_game", "demo", "bundle"]
        
        default_guild = {
            "channel_id": None,
            "store_threads": {},
            "last_posted_games": {},
            "notification_roles": {},
            "filters": {
                "dlc": True,
                "full_game": True,
                "demo": False,
                "bundle": True
            },
            "timezone": "UTC",
            "stores_enabled": {
                "epic": True,
                "steam": True,
                "gog": True,
                "humble": True,
                "origin": True,
                "ubisoft": True,
                "itchio": True
            }
        }
        
        default_global = {
            "epic": {
                "client_id": None,
                "client_secret": None,
                "access_token": None,
                "token_expires": None
            },
            "steam": {"api_key": None},
            "gog": {"api_key": None},
            "humble": {"api_key": None},
            "origin": {"api_key": None},
            "ubisoft": {"api_key": None},
            "itchio": {"api_key": None},
            "check_interval": 3600,
            "last_check": None
        }
        
        default_user = {
            "linked_accounts": {
                "steam": None,
                "epic": None,
                "gog": None,
                "humble": None,
                "origin": None,
                "ubisoft": None,
                "itchio": None
            },
            "notifications": {
                "dlc": False,
                "full_game": True,
                "demo": False,
                "bundle": True
            },
            "stores": {
                "epic": True,
                "steam": True,
                "gog": True,
                "humble": True,
                "origin": True,
                "ubisoft": True,
                "itchio": True
            },
            "dm_notifications": False
        }
        
        self.config.register_guild(**default_guild)
        self.config.register_global(**default_global)
        self.config.register_user(**default_user)
        
        # Start background tasks
        self.bg_task = self.bot.loop.create_task(self.check_free_games_schedule())
        self.token_refresh_task = self.bot.loop.create_task(self.refresh_tokens_schedule())

    def cog_unload(self):
        """Cleanup when cog is unloaded."""
        if self.bg_task:
            self.bg_task.cancel()
        if self.token_refresh_task:
            self.token_refresh_task.cancel()
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
        if store not in self.store_colors.keys():
            return await ctx.send(f"Invalid store. Choose from: {', '.join(self.store_colors.keys())}")
        
        async with self.config.guild(ctx.guild).store_threads() as threads:
            threads[store] = thread.id
        await ctx.send(f"Games from {store.title()} will be posted in {thread.mention}")

    @efreegames.command(name="forcecheck")
    @commands.admin_or_permissions(manage_channels=True)
    async def force_check(self, ctx):
        """Force check and post free games immediately."""
        async with ctx.typing():
            try:
                await ctx.send("Checking for free games...")
                
                all_games = []
                stores = {
                    "epic": self.fetch_epic_games,
                    "steam": self.fetch_steam_games,
                    "gog": self.fetch_gog_games,
                    "humble": self.fetch_humble_games,
                    "origin": self.fetch_origin_games,
                    "ubisoft": self.fetch_ubisoft_games,
                    "itchio": self.fetch_itchio_games
                }
                
                for store, fetch_func in stores.items():
                    games = await fetch_func()
                    for game in games:
                        all_games.append((store, game))
                
                if not all_games:
                    return await ctx.send("No free games found at the moment.")
                
                # Get guild data
                guild_data = await self.config.guild(ctx.guild).all()
                channel_id = guild_data["channel_id"]
                if not channel_id:
                    return await ctx.send("No channel configured for free games. Use `[p]efreegames setchannel` first.")
                
                channel = ctx.guild.get_channel(channel_id)
                if not channel:
                    return await ctx.send("Configured channel not found.")
                
                store_threads = guild_data.get("store_threads", {})
                notification_roles = guild_data.get("notification_roles", {})
                
                games_posted = 0
                for store, game in all_games:
                    # Check if game was already posted
                    if await self.is_game_already_posted(ctx.guild.id, store, game["title"]):
                        continue
                    
                    embed = await self.create_game_embed(game, store)
                    
                    # Get role mention if configured for this game type
                    role_mention = ""
                    if game["type"] in notification_roles:
                        role_id = notification_roles[game["type"]]
                        role = ctx.guild.get_role(role_id)
                        if role:
                            role_mention = role.mention
                    
                    # Post to appropriate thread or channel
                    if store in store_threads:
                        thread = ctx.guild.get_thread(store_threads[store])
                        if thread:
                            await thread.send(content=role_mention, embed=embed)
                            games_posted += 1
                            continue
                    
                    await channel.send(content=role_mention, embed=embed)
                    games_posted += 1
                    
                    # Mark game as posted
                    await self.mark_game_as_posted(ctx.guild.id, store, game["title"])
                    
                    # Send DM notifications
                    await self.notify_users(ctx.guild, game, store)
                
                if games_posted > 0:
                    await ctx.send(f"Successfully posted {games_posted} new free game(s).")
                else:
                    await ctx.send("No new free games found that haven't been posted recently.")
                
            except Exception as e:
                await ctx.send(f"An error occurred while checking for free games: {str(e)}")

    @efreegames.group(name="notify")
    async def notify_settings(self, ctx):
        """Configure your notification preferences."""
        pass

    @notify_settings.command(name="toggle")
    async def toggle_notifications(self, ctx, game_type: str):
        """Toggle notifications for a specific game type."""
        game_type = game_type.lower()
        if game_type not in self.valid_game_types:
            return await ctx.send(f"Invalid game type. Choose from: {', '.join(self.valid_game_types)}")
        
        async with self.config.user(ctx.author).notifications() as notifications:
            notifications[game_type] = not notifications[game_type]
            status = "enabled" if notifications[game_type] else "disabled"
            
        await ctx.send(f"Notifications for {game_type} have been {status}.")

    @notify_settings.command(name="dm")
    async def toggle_dm_notifications(self, ctx):
        """Toggle DM notifications for free games."""
        async with self.config.user(ctx.author).dm_notifications() as dm_enabled:
            dm_enabled = not dm_enabled
            status = "enabled" if dm_enabled else "disabled"
            
        await ctx.send(f"DM notifications have been {status}.")

    @notify_settings.command(name="store")
    async def toggle_store_notifications(self, ctx, store: str):
        """Toggle notifications for a specific store."""
        store = store.lower()
        if store not in self.store_colors.keys():
            return await ctx.send(f"Invalid store. Choose from: {', '.join(self.store_colors.keys())}")
        
        async with self.config.user(ctx.author).stores() as stores:
            stores[store] = not stores[store]
            status = "enabled" if stores[store] else "disabled"
            
        await ctx.send(f"Notifications for {store.title()} have been {status}.")

    @efreegames.command(name="setrole")
    @commands.admin_or_permissions(manage_roles=True)
    async def set_notification_role(self, ctx, game_type: str, role: discord.Role):
        """Set a role to ping for specific game types."""
        game_type = game_type.lower()
        if game_type not in self.valid_game_types:
            return await ctx.send(f"Invalid game type. Choose from: {', '.join(self.valid_game_types)}")
        
        async with self.config.guild(ctx.guild).notification_roles() as roles:
            roles[game_type] = role.id
            
        await ctx.send(f"{role.mention} will be pinged for {game_type} notifications.")

    @efreegames.group(name="account")
    async def account_settings(self, ctx):
        """Manage your linked store accounts."""
        pass

    @account_settings.command(name="link")
    async def link_account(self, ctx, store: str, account_id: str):
        """Link your store account."""
        store = store.lower()
        if store not in self.store_colors.keys():
            return await ctx.send(f"Invalid store. Choose from: {', '.join(self.store_colors.keys())}")
        
        # Validate account ID format based on store
        if not self.validate_account_id(store, account_id):
            return await ctx.send(f"Invalid {store.title()} account ID format.")
        
        async with self.config.user(ctx.author).linked_accounts() as accounts:
            accounts[store] = account_id
            
        await ctx.send(f"{store.title()} account linked successfully!")

    @account_settings.command(name="unlink")
    async def unlink_account(self, ctx, store: str):
        """Unlink a store account."""
        store = store.lower()
        if store not in self.store_colors.keys():
            return await ctx.send(f"Invalid store. Choose from: {', '.join(self.store_colors.keys())}")
        
        async with self.config.user(ctx.author).linked_accounts() as accounts:
            if store in accounts and accounts[store]:
                accounts[store] = None
                await ctx.send(f"{store.title()} account unlinked successfully!")
            else:
                await ctx.send(f"No {store.title()} account was linked.")

    @account_settings.command(name="list")
    async def list_accounts(self, ctx):
        """List your linked store accounts."""
        accounts = await self.config.user(ctx.author).linked_accounts()
        
        embed = discord.Embed(
            title="Linked Store Accounts",
            color=discord.Color.blue()
        )
        
        for store, account_id in accounts.items():
            status = f"Linked: {account_id}" if account_id else "Not linked"
            embed.add_field(
                name=store.title(),
                value=status,
                inline=False
            )
            
        await ctx.send(embed=embed)
    async def validate_account_id(self, store: str, account_id: str) -> bool:
        """Validate store account ID format."""
        patterns = {
            "steam": r"^\d{17}$",  # Steam ID format
            "epic": r"^[a-zA-Z0-9_-]{2,32}$",  # Epic username format
            "gog": r"^[a-zA-Z0-9_-]{3,30}$",
            "humble": r"^[a-zA-Z0-9_-]{4,30}$",
            "origin": r"^[a-zA-Z0-9_-]{3,30}$",
            "ubisoft": r"^[a-zA-Z0-9_-]{3,30}$",
            "itchio": r"^[a-zA-Z0-9_-]{2,30}$"
        }
        
        return bool(re.match(patterns.get(store, r".*"), account_id))

    async def fetch_epic_games(self) -> List[Dict]:
        """Fetch free games from Epic Games Store."""
        token = await self.get_epic_token()
        if not token:
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
            
            async with self.session.get(self.epic_api_url, params=params, headers=headers) as response:
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
                                        game_type = "dlc" if game.get("categories", [{"path": ""}])[0].get("path") == "addons" else "full_game"
                                        
                                        free_games.append({
                                            "title": game.get("title", "Unknown"),
                                            "store_url": f"https://store.epicgames.com/en-US/p/{game.get('urlSlug')}",
                                            "image_url": game.get("keyImages", [{}])[0].get("url"),
                                            "end_time": end_date,
                                            "description": game.get("description", ""),
                                            "publisher": game.get("seller", {}).get("name", "Unknown Publisher"),
                                            "type": game_type
                                        })
                    
                    return free_games
                    
        except Exception as e:
            print(f"Error fetching Epic Games: {e}")
            return []

    async def fetch_steam_games(self) -> List[Dict]:
        """Fetch free games from Steam."""
        steam_config = await self.config.steam()
        api_key = steam_config["api_key"]
        
        if not api_key:
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
                                        game_type = "dlc" if game_data.get("type") == "dlc" else "full_game"
                                        if game_data.get("type") == "demo":
                                            game_type = "demo"
                                        
                                        free_games.append({
                                            "title": game_data.get("name", "Unknown"),
                                            "store_url": f"https://store.steampowered.com/app/{app_id}",
                                            "image_url": game_data.get("header_image"),
                                            "end_time": datetime.now(timezone.utc) + timedelta(days=1),
                                            "description": game_data.get("short_description", ""),
                                            "publisher": game_data.get("publishers", ["Unknown Publisher"])[0],
                                            "type": game_type
                                        })
        async def fetch_gog_games(self) -> List[Dict]:
        """Fetch free games from GOG."""
        try:
            params = {
                "price": "free",
                "sort": "newest"
            }
            
            async with self.session.get(self.gog_api_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    free_games = []
                    
                    for game in data.get("products", []):
                        game_type = "dlc" if game.get("dlc", False) else "full_game"
                        
                        free_games.append({
                            "title": game.get("title", "Unknown"),
                            "store_url": f"https://www.gog.com{game.get('url')}",
                            "image_url": game.get("image", ""),
                            "end_time": datetime.now(timezone.utc) + timedelta(days=1),
                            "description": game.get("description", ""),
                            "publisher": game.get("publisher", "Unknown Publisher"),
                            "type": game_type
                        })
                    
                    return free_games
                    
        except Exception as e:
            print(f"Error fetching GOG games: {e}")
            return []

    async def fetch_humble_games(self) -> List[Dict]:
        """Fetch free games from Humble Bundle."""
        try:
            params = {
                "filter": "price:free",
                "sort": "newest"
            }
            
            async with self.session.get(self.humble_api_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    free_games = []
                    
                    for game in data.get("results", []):
                        game_type = "bundle" if game.get("bundle", False) else "full_game"
                        if game.get("is_dlc"):
                            game_type = "dlc"
                        
                        free_games.append({
                            "title": game.get("human_name", "Unknown"),
                            "store_url": f"https://www.humblebundle.com/store/{game.get('machine_name')}",
                            "image_url": game.get("featured_image", ""),
                            "end_time": datetime.fromtimestamp(game.get("sale_end", 0), timezone.utc),
                            "description": game.get("description", ""),
                            "publisher": game.get("publisher", {}).get("name", "Unknown Publisher"),
                            "type": game_type
                        })
                    
                    return free_games
                    
        except Exception as e:
            print(f"Error fetching Humble Bundle games: {e}")
            return []

    async def fetch_origin_games(self) -> List[Dict]:
        """Fetch free games from Origin/EA."""
        try:
            params = {
                "free": "true",
                "locale": "en_US"
            }
            
            async with self.session.get(self.origin_api_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    free_games = []
                    
                    for game in data.get("games", []):
                        game_type = "dlc" if game.get("downloadable_content", False) else "full_game"
                        
                        free_games.append({
                            "title": game.get("name", "Unknown"),
                            "store_url": f"https://www.origin.com/store/{game.get('slug')}",
                            "image_url": game.get("image_url", ""),
                            "end_time": datetime.now(timezone.utc) + timedelta(days=1),
                            "description": game.get("description", ""),
                            "publisher": game.get("publisher", "EA"),
                            "type": game_type
                        })
                    
                    return free_games
                    
        except Exception as e:
            print(f"Error fetching Origin games: {e}")
            return []
      async def fetch_ubisoft_games(self) -> List[Dict]:
        """Fetch free games from Ubisoft Connect."""
        try:
            params = {
                "free": "true",
                "locale": "en-US"
            }
            
            async with self.session.get(self.ubisoft_api_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    free_games = []
                    
                    for game in data.get("games", []):
                        game_type = "dlc" if game.get("isDLC", False) else "full_game"
                        
                        free_games.append({
                            "title": game.get("name", "Unknown"),
                            "store_url": f"https://store.ubi.com/game/{game.get('slug')}",
                            "image_url": game.get("image", ""),
                            "end_time": datetime.now(timezone.utc) + timedelta(days=1),
                            "description": game.get("description", ""),
                            "publisher": "Ubisoft",
                            "type": game_type
                        })
                    
                    return free_games
                    
        except Exception as e:
            print(f"Error fetching Ubisoft games: {e}")
            return []

    async def fetch_itchio_games(self) -> List[Dict]:
        """Fetch free games from itch.io."""
        try:
            params = {
                "filter": "free",
                "sort": "newest"
            }
            
            async with self.session.get(self.itchio_api_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    free_games = []
                    
                    for game in data.get("games", []):
                        game_type = "full_game"  # itch.io doesn't clearly distinguish types
                        
                        free_games.append({
                            "title": game.get("title", "Unknown"),
                            "store_url": game.get("url", ""),
                            "image_url": game.get("cover_image", ""),
                            "end_time": datetime.now(timezone.utc) + timedelta(days=1),
                            "description": game.get("short_description", ""),
                            "publisher": game.get("user", {}).get("name", "Unknown Developer"),
                            "type": game_type
                        })
                    
                    return free_games
                    
        except Exception as e:
            print(f"Error fetching itch.io games: {e}")
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
            
        embed.add_field(name="Type", value=game["type"].replace("_", " ").title(), inline=True)
        embed.add_field(name="Publisher", value=game["publisher"], inline=True)
        embed.set_footer(text=f"Via {store.title()}")
        
        return embed

    async def should_notify_user(self, user_id: int, game: Dict, store: str) -> bool:
        """Check if a user should be notified about a game."""
        user_data = await self.config.user_from_id(user_id).all()
        
        # Check if user wants notifications for this game type
        if not user_data["notifications"].get(game["type"], False):
            return False
            
        # Check if user wants notifications from this store
        if not user_data["stores"].get(store, True):
            return False
            
        return True

    async def notify_users(self, guild: discord.Guild, game: Dict, store: str):
        """Send notifications to users who have opted in."""
        async for user_id in self.config.all_users():
            if await self.should_notify_user(user_id, game, store):
                user = guild.get_member(user_id)
                if user and (await self.config.user(user).dm_notifications()):
                    try:
                        embed = await self.create_game_embed(game, store)
                        await user.send(embed=embed)
                    except discord.Forbidden:
                        pass  # Can't send DM to user

    async def check_free_games_schedule(self):
        """Background task to check for free games."""
        await self.bot.wait_until_ready()
        
        while True:
            try:
                now = datetime.now(pytz.timezone('Europe/London'))
                
                # Check if it's 12 PM BST
                if now.hour == 12 and now.minute == 0:
                    all_games = []
                    stores = {
                        "epic": self.fetch_epic_games,
                        "steam": self.fetch_steam_games,
                        "gog": self.fetch_gog_games,
                        "humble": self.fetch_humble_games,
                        "origin": self.fetch_origin_games,
                        "ubisoft": self.fetch_ubisoft_games,
                        "itchio": self.fetch_itchio_games
                    }
                    
                    for store, fetch_func in stores.items():
                        games = await fetch_func()
                        for game in games:
                            all_games.append((store, game))
                    
                    # Post to all configured guilds
                    all_guilds = await self.config.all_guilds()
                    for guild_id, guild_data in all_guilds.items():
                        guild = self.bot.get_guild(guild_id)
                        if not guild:
                            continue
                            
                        channel_id = guild_data["channel_id"]
                        if not channel_id:
                            continue
                            
                        channel = guild.get_channel(channel_id)
                        if not channel:
                            continue
                            
                        store_threads = guild_data.get("store_threads", {})
                        notification_roles = guild_data.get("notification_roles", {})
                        
                        for store, game in all_games:
                            # Check if game was already posted
                            if await self.is_game_already_posted(guild_id, store, game["title"]):
                                continue
                                
                            embed = await self.create_game_embed(game, store)
                            
                            # Get role mention if configured for this game type
                            role_mention = ""
                            if game["type"] in notification_roles:
                                role_id = notification_roles[game["type"]]
                                role = guild.get_role(role_id)
                                if role:
                                    role_mention = role.mention
                            
                            # Post to appropriate thread or channel
                            if store in store_threads:
                                thread = guild.get_thread(store_threads[store])
                                if thread:
                                    await thread.send(content=role_mention, embed=embed)
                                    continue
                            
                            await channel.send(content=role_mention, embed=embed)
                            
                            # Mark game as posted
                            await self.mark_game_as_posted(guild_id, store, game["title"])
                            
                            # Send DM notifications
                            await self.notify_users(guild, game, store)
            
            except Exception as e:
                print(f"Error in free games check schedule: {e}")
            
            # Wait for 60 seconds before checking again
            await asyncio.sleep(60)

    async def is_game_already_posted(self, guild_id: int, store: str, game_title: str) -> bool:
        """Check if a game was already posted in the last 24 hours."""
        async with self.config.guild_from_id(guild_id).last_posted_games() as posted_games:
            store_games = posted_games.get(store, {})
            if game_title in store_games:
                posted_time = datetime.fromisoformat(store_games[game_title])
                if (datetime.now(timezone.utc) - posted_time).total_seconds() < 86400:
                    return True
            return False

    async def mark_game_as_posted(self, guild_id: int, store: str, game_title: str):
        """Mark a game as posted."""
        async with self.config.guild_from_id(guild_id).last_posted_games() as posted_games:
            if store not in posted_games:
                posted_games[store] = {}
            posted_games[store][game_title] = datetime.now(timezone.utc).isoformat()

    async def refresh_tokens_schedule(self):
        """Background task to refresh API tokens."""
        await self.bot.wait_until_ready()
        
        while True:
            try:
                # Refresh Epic Games token if needed
                if not await self.is_epic_token_valid():
                    await self.get_epic_token()
                
                # Add other token refresh logic here
                
            except Exception as e:
                print(f"Error in token refresh schedule: {e}")
                
            await asyncio.sleep(3600)  # Check every hour
      
