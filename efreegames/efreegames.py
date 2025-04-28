import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import box
import aiohttp
import asyncio
from datetime import datetime, timedelta
import feedparser
import logging
from typing import Dict, List, Optional
import xml.etree.ElementTree as ET
import re
import json

log = logging.getLogger("red.efreegames")

class GameDeal:
    def __init__(self, title: str, url: str, platform: str, image: str = None, 
                 end_date: datetime = None, original_price: str = None, 
                 description: str = None, deal_type: str = "game"):
        self.title = title
        self.url = url
        self.platform = platform
        self.image = image
        self.end_date = end_date
        self.original_price = original_price
        self.description = description
        self.deal_type = deal_type  # "game", "dlc", "addon", "ingame"

class EFreeGames(commands.Cog):
    """Track and notify about free games from various platforms."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.session = aiohttp.ClientSession()
        
        default_guild = {
            "channel_id": None,
            "enabled_services": {
                "steam": True,
                "epic": True,
                "gog": True,
                "itch": True,
                "humble": True,
                "ubisoft": True
            },
            "last_notification": {},
            "check_frequency": 3600,  # Default 1 hour
            "filters": {
                "games": True,
                "dlc": True,
                "addons": False,
                "ingame": False
            },
            "minimum_price": 0.00,  # Minimum original price to notify about
            "ping_role": None
        }
        
        self.config.register_guild(**default_guild)
        self.bg_task = self.bot.loop.create_task(self.check_free_games_loop())

    def cog_unload(self):
        if self.bg_task:
            self.bg_task.cancel()
        asyncio.create_task(self.session.close())

    async def check_free_games_loop(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await self.check_all_services()
                guild_data = await self.config.all_guilds()
                # Get the minimum check frequency across all guilds
                min_frequency = min(
                    (guild.get("check_frequency", 3600) for guild in guild_data.values()),
                    default=3600
                )
                await asyncio.sleep(min_frequency)
            except Exception as e:
                log.error(f"Error in free games check loop: {e}")
                await asyncio.sleep(300)

    async def fetch_epic_games(self) -> List[GameDeal]:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Accept": "application/json"
            }
            async with self.session.get("https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions", headers=headers) as resp:
                if resp.status != 200:
                    log.error(f"Epic Games API returned status {resp.status}")
                    return []
                data = await resp.json()
                free_games = []
                
                elements = data.get("data", {}).get("Catalog", {}).get("searchStore", {}).get("elements", [])
                log.info(f"Found {len(elements)} potential Epic games")
                
                for game in elements:
                    try:
                        promotions = game.get("promotions", {})
                        if not promotions:
                            continue
                            
                        promotional_offers = promotions.get("promotionalOffers", [])
                        if not promotional_offers or not promotional_offers[0].get("promotionalOffers"):
                            continue
                            
                        promo = promotional_offers[0]["promotionalOffers"][0]
                        
                        # Check if the game is currently free
                        if promo.get("discountSetting", {}).get("discountPercentage") != 0:
                            continue
                            
                        end_date = datetime.fromisoformat(promo["endDate"][:-1])
                        
                        # Get the correct store URL
                        slug = game.get('catalogNs', {}).get('slug') or game.get('urlSlug') or game.get('productSlug')
                        if not slug:
                            log.error(f"Could not find slug for game {game.get('title')}")
                            continue
                            
                        store_url = f"https://store.epicgames.com/en-US/p/{slug}"
                        
                        # Get the best image
                        image_url = ""
                        for img in game.get("keyImages", []):
                            if img.get("type") in ["OfferImageWide", "DieselStoreFrontWide"]:
                                image_url = img.get("url", "")
                                break
                        if not image_url and game.get("keyImages"):
                            image_url = game["keyImages"][0].get("url", "")
                        
                        free_games.append(GameDeal(
                            title=game["title"],
                            url=store_url,
                            image=image_url,
                            platform="Epic Games",
                            end_date=end_date,
                            original_price=game.get("price", {}).get("totalPrice", {}).get("fmtPrice", {}).get("originalPrice", "N/A"),
                            description=game.get("description", ""),
                            deal_type="dlc" if game.get("categories", [{}])[0].get("path") == "addons/dlc" else "game"
                        ))
                        
                        log.info(f"Added Epic game: {game['title']} with URL: {store_url}")
                        
                    except Exception as e:
                        log.error(f"Error processing Epic game {game.get('title', 'Unknown')}: {e}")
                        continue
                        
                log.info(f"Successfully processed {len(free_games)} free Epic games")
                return free_games
                
        except Exception as e:
            log.error(f"Error fetching Epic Games: {e}")
            return []

    async def fetch_steam_games(self) -> List[GameDeal]:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
            }
            async with self.session.get("https://steamcommunity.com/groups/freegamesoncommunity/rss/", headers=headers) as resp:
                if resp.status != 200:
                    log.error(f"Steam RSS returned status {resp.status}")
                    return []
                    
                feed = feedparser.parse(await resp.text())
                free_games = []
                
                log.info(f"Found {len(feed.entries)} potential Steam games")
                
                for entry in feed.entries:
                    try:
                        # Parse Steam store URL from entry
                        store_url = re.search(r'https://store\.steampowered\.com/app/\d+', entry.description)
                        if not store_url:
                            continue
                            
                        url = store_url.group(0)
                        app_id = url.split('/')[-1]
                        
                        # Fetch additional details from Steam store API
                        async with self.session.get(f"https://store.steampowered.com/api/appdetails?appids={app_id}", headers=headers) as store_resp:
                            if store_resp.status != 200:
                                continue
                                
                            store_data = await store_resp.json()
                            if not store_data or not store_data.get(app_id, {}).get("success"):
                                continue
                                
                            data = store_data[app_id]["data"]
                            
                            # Check if the game is actually free
                            if data.get("is_free") or (data.get("price_overview", {}).get("final") == 0):
                                free_games.append(GameDeal(
                                    title=data["name"],
                                    url=url,
                                    image=data.get("header_image"),
                                    platform="Steam",
                                    original_price=data.get("price_overview", {}).get("initial_formatted", "N/A"),
                                    description=data.get("short_description"),
                                    deal_type="dlc" if data.get("type") == "dlc" else "game"
                                ))
                                
                                log.info(f"Added Steam game: {data['name']} with URL: {url}")
                                
                    except Exception as e:
                        log.error(f"Error processing Steam game from entry {entry.get('title', 'Unknown')}: {e}")
                        continue
                        
                log.info(f"Successfully processed {len(free_games)} free Steam games")
                return free_games
                
        except Exception as e:
            log.error(f"Error fetching Steam games: {e}")
            return []

    async def fetch_gog_games(self) -> List[GameDeal]:
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                "Accept": "application/json"
            }

            url = "https://embed.gog.com/games/ajax/filtered"
            params = {
                "mediaType": "game",
                "sort": "popularity",
                "price": "free",
                "page": 1
            }
            
            async with self.session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    log.error(f"GOG API returned status {resp.status}")
                    return []
                    
                try:
                    data = await resp.json()
                    free_games = []
                    
                    products = data.get("products", [])
                    log.info(f"Found {len(products)} potential GOG games")
                    
                    for product in products:
                        try:
                            if product.get("isDiscounted") and product.get("price", {}).get("amount") == "0.00":
                                store_url = f"https://www.gog.com{product.get('url', '')}"
                                free_games.append(GameDeal(
                                    title=product.get("title", "Unknown"),
                                    url=store_url,
                                    image=product.get("image", ""),
                                    platform="GOG",
                                    original_price=f"${product.get('price', {}).get('baseAmount', 'N/A')}",
                                    description=product.get("title", ""),
                                    deal_type="game"
                                ))
                                
                                log.info(f"Added GOG game: {product.get('title', 'Unknown')} with URL: {store_url}")
                                
                        except Exception as e:
                            log.error(f"Error processing GOG game {product.get('title', 'Unknown')}: {e}")
                            continue
                    
                    log.info(f"Successfully processed {len(free_games)} free GOG games")
                    return free_games
                    
                except Exception as e:
                    log.error(f"Failed to parse GOG response: {e}")
                    log.error(f"Response content type: {resp.content_type}")
                    log.error(f"Response headers: {resp.headers}")
                    content = await resp.text()
                    log.error(f"Response content: {content[:500]}...")
                    return []
                
        except Exception as e:
            log.error(f"Error fetching GOG games: {e}")
            log.error(f"Full error: {str(e)}")
            return []

    async def should_notify(self, guild_id: int, game: GameDeal) -> bool:
        """Check if we should notify about this game based on guild settings."""
        try:
            guild_data = await self.config.guild_from_id(guild_id).all()
            
            # Check deal type filter
            if not guild_data["filters"].get(f"{game.deal_type}s", True):
                return False

            # Check minimum price filter
            min_price = guild_data["minimum_price"]
            if game.original_price and game.original_price != "N/A":
                try:
                    price = float(game.original_price.replace("$", "").strip())
                    if price < min_price:
                        return False
                except (ValueError, TypeError):
                    pass

            return True
            
        except Exception as e:
            log.error(f"Error in should_notify for game {game.title}: {e}")
            return False

    async def create_game_embed(self, game: GameDeal) -> discord.Embed:
        """Create a rich embed for a game."""
        try:
            embed = discord.Embed(
                title=f"Free: {game.title}",
                url=game.url,
                description=game.description[:200] + "..." if game.description and len(game.description) > 200 else game.description,
                color=discord.Color.green()
            )
            
            embed.add_field(name="Platform", value=game.platform)
            if game.original_price:
                embed.add_field(name="Original Price", value=game.original_price)
            if game.end_date:
                embed.add_field(name="Offer Ends", value=game.end_date.strftime("%Y-%m-%d %H:%M UTC"))
            embed.add_field(name="Type", value=game.deal_type.capitalize())
            
            if game.image:
                embed.set_thumbnail(url=game.image)
                
            embed.set_footer(text="Free Games Tracker")
            return embed
            
        except Exception as e:
            log.error(f"Error creating embed for game {game.title}: {e}")
            # Return a basic embed if there's an error
            return discord.Embed(
                title=f"Free: {game.title}",
                url=game.url,
                color=discord.Color.green()
            )

    async def check_all_services(self):
        try:
            all_guilds = await self.config.all_guilds()
            
            for guild_id, guild_data in all_guilds.items():
                try:
                    if not guild_data["channel_id"]:
                        continue
                        
                    channel = self.bot.get_channel(guild_data["channel_id"])
                    if not channel:
                        log.error(f"Could not find channel {guild_data['channel_id']} for guild {guild_id}")
                        continue

                    enabled_services = guild_data["enabled_services"]
                    last_notification = guild_data.get("last_notification", {})
                    ping_role = guild_data.get("ping_role")

                    free_games = []
                    
                    # Fetch games from each enabled service
                    if enabled_services.get("epic", False):
                        epic_games = await self.fetch_epic_games()
                        free_games.extend(epic_games)
                        log.info(f"Found {len(epic_games)} Epic games")
                    
                    if enabled_services.get("steam", False):
                        steam_games = await self.fetch_steam_games()
                        free_games.extend(steam_games)
                        log.info(f"Found {len(steam_games)} Steam games")
                    
                    if enabled_services.get("gog", False):
                        gog_games = await self.fetch_gog_games()
                        free_games.extend(gog_games)
                        log.info(f"Found {len(gog_games)} GOG games")

                    log.info(f"Total free games found: {len(free_games)}")

                    # Send notifications for new games
                    for game in free_games:
                        try:
                            game_key = f"{game.platform}:{game.title}"
                            if game_key not in last_notification and await self.should_notify(guild_id, game):
                                embed = await self.create_game_embed(game)
                                
                                content = None
                                if ping_role:
                                    role = channel.guild.get_role(ping_role)
                                    if role:
                                        content = role.mention
                                        
                                await channel.send(content=content, embed=embed)
                                log.info(f"Sent notification for {game.title} to channel {channel.id}")
                                
                                # Update last notification
                                current_notifications = dict(last_notification)
                                current_notifications[game_key] = datetime.now().isoformat()
                                await self.config.guild(channel.guild).last_notification.set(current_notifications)
                                
                        except Exception as e:
                            log.error(f"Error sending notification for {game.title}: {e}")
                            continue
                            
                except Exception as e:
                    log.error(f"Error processing guild {guild_id}: {e}")
                    continue
                    
        except Exception as e:
            log.error(f"Error in check_all_services: {e}")

    @commands.group(aliases=["efg"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def efreegames(self, ctx):
        """Configure free games notifications"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @efreegames.command()
    async def setchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel for free games notifications"""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"Free games notifications will be sent to {channel.mention}")

    @efreegames.command()
    async def toggle(self, ctx, service: str):
        """Toggle a service on/off (steam/epic/gog/itch/humble/ubisoft)"""
        service = service.lower()
        valid_services = ["steam", "epic", "gog", "itch", "humble", "ubisoft"]
        if service not in valid_services:
            await ctx.send(f"Invalid service. Available services: {', '.join(valid_services)}")
            return

        async with self.config.guild(ctx.guild).enabled_services() as services:
            services[service] = not services.get(service, True)
            status = "enabled" if services[service] else "disabled"
            await ctx.send(f"{service.capitalize()} notifications {status}")

    @efreegames.command()
    async def frequency(self, ctx, hours: float):
        """Set how often to check for new games (in hours)"""
        if hours < 0.5:
            await ctx.send("Minimum check frequency is 30 minutes (0.5 hours)")
            return
        
        await self.config.guild(ctx.guild).check_frequency.set(int(hours * 3600))
        await ctx.send(f"Check frequency set to {hours} hours")

    @efreegames.command()
    async def filters(self, ctx, deal_type: str, enabled: bool):
        """Configure what types of deals to notify about (games/dlc/addons/ingame)"""
        valid_types = ["games", "dlc", "addons", "ingame"]
        if deal_type not in valid_types:
            await ctx.send(f"Invalid deal type. Available types: {', '.join(valid_types)}")
            return

        async with self.config.guild(ctx.guild).filters() as filters:
            filters[deal_type] = enabled
            await ctx.send(f"{deal_type.capitalize()} notifications {'enabled' if enabled else 'disabled'}")

    @efreegames.command()
    async def minprice(self, ctx, price: float):
        """Set minimum original price to notify about ($0.00-$999.99)"""
        if price < 0 or price > 999.99:
            await ctx.send("Price must be between $0.00 and $999.99")
            return
            
        await self.config.guild(ctx.guild).minimum_price.set(price)
        await ctx.send(f"Minimum price set to ${price:.2f}")

    @efreegames.command()
    async def pingrole(self, ctx, role: Optional[discord.Role]):
        """Set a role to ping for new free games (leave empty to disable)"""
        await self.config.guild(ctx.guild).ping_role.set(role.id if role else None)
        await ctx.send(f"Ping role {'set to ' + role.name if role else 'disabled'}")

    @efreegames.command()
    async def check(self, ctx):
        """Force check for new free games"""
        await ctx.send("Checking for new free games...")
        await self.check_all_services()
        await ctx.send("Check complete!")

    @efreegames.command()
    @commands.admin_or_permissions(manage_guild=True)
    async def forcepost(self, ctx):
        """Force post all current free games to the set channel."""
        try:
            guild_data = await self.config.guild(ctx.guild).all()
            
            if not guild_data["channel_id"]:
                await ctx.send("No notification channel set. Use `setchannel` first.")
                return
                
            channel = self.bot.get_channel(guild_data["channel_id"])
            if not channel:
                await ctx.send("Could not find the configured notification channel. Please set it again.")
                return

            await ctx.send("Fetching and posting all current free games...")
            
            free_games = []
            enabled_services = guild_data["enabled_services"]
            
            # Fetch games from each enabled service
            if enabled_services.get("epic", False):
                epic_games = await self.fetch_epic_games()
                free_games.extend(epic_games)
                log.info(f"Found {len(epic_games)} Epic games")
            
            if enabled_services.get("steam", False):
                steam_games = await self.fetch_steam_games()
                free_games.extend(steam_games)
                log.info(f"Found {len(steam_games)} Steam games")
            
            if enabled_services.get("gog", False):
                gog_games = await self.fetch_gog_games()
                free_games.extend(gog_games)
                log.info(f"Found {len(gog_games)} GOG games")

            if not free_games:
                await ctx.send("No free games found from enabled services.")
                return

            posted_count = 0
            for game in free_games:
                if await self.should_notify(ctx.guild.id, game):
                    try:
                        embed = await self.create_game_embed(game)
                        
                        content = None
                        if guild_data.get("ping_role"):
                            role = ctx.guild.get_role(guild_data["ping_role"])
                            if role:
                                content = role.mention
                                
                        await channel.send(content=content, embed=embed)
                        posted_count += 1
                        
                        # Add small delay between posts to avoid rate limits
                        await asyncio.sleep(1)
                        
                    except Exception as e:
                        log.error(f"Error posting game {game.title}: {e}")
                        continue

            await ctx.send(f"Posted {posted_count} free games to {channel.mention}")
            
        except Exception as e:
            log.error(f"Error in forcepost: {e}")
            await ctx.send("An error occurred while trying to post free games.")

    @efreegames.command()
    async def status(self, ctx):
        """Show current configuration"""
        config = await self.config.guild(ctx.guild).all()
        channel = self.bot.get_channel(config["channel_id"])
        
        enabled = [s for s, v in config["enabled_services"].items() if v]
        disabled = [s for s, v in config["enabled_services"].items() if not v]
        
        enabled_filters = [f for f, v in config["filters"].items() if v]
        disabled_filters = [f for f, v in config["filters"].items() if not v]
        
        ping_role = ctx.guild.get_role(config["ping_role"]) if config["ping_role"] else None
        
        msg = [
            f"Notification channel: {channel.mention if channel else 'Not set'}",
            f"Check frequency: {config['check_frequency']/3600:.1f} hours",
            f"Minimum price: ${config['minimum_price']:.2f}",
            f"Ping role: {ping_role.name if ping_role else 'Disabled'}",
            "",
            "Enabled services:",
            ', '.join(enabled) or 'None',
            "",
            "Disabled services:",
            ', '.join(disabled) or 'None',
            "",
            "Enabled filters:",
            ', '.join(enabled_filters) or 'None',
            "",
            "Disabled filters:",
            ', '.join(disabled_filters) or 'None'
        ]
        
        await ctx.send(box('\n'.join(msg)))

def setup(bot):
    bot.add_cog(EFreeGames(bot))
