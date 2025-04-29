import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import Modal, Button, View
import aiohttp
import asyncio
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import logging
from pathlib import Path

from .api.base import GameData
from .api.epic import EpicGamesAPI
from .api.steam import SteamAPI
from .api.gog import GOGApi
from .api.humble import HumbleBundleAPI
from .api.itch import ItchioAPI
from .api.origin import OriginAPI
from .api.ubisoft import UbisoftAPI

# Rest of the efreegames.py code remains the same


class EFreeGames(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = self.load_config()
        self.cache = self.load_cache()
        self.stores = {}
        self.initialize_stores()
        self.check_free_games.start()

    def initialize_stores(self):
        store_classes = {
            'Epic': EpicGamesAPI,
            'Steam': SteamAPI,
            'GOG': GOGApi,
            'HumbleBundle': HumbleBundleAPI,
            'Itch.io': ItchioAPI,
            'Origin': OriginAPI,
            'Ubisoft': UbisoftAPI
        }

        for store_name, store_class in store_classes.items():
            if store_name in self.config['api_keys']:
                credentials = self.config['api_keys'][store_name]
                self.stores[store_name] = store_class(
                    credentials['key'],
                    credentials['secret']
                )

    def load_cache(self) -> Dict:
        try:
            with open('data/efreegames/cache.json', 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {'games': [], 'last_check': None}

    def save_cache(self):
        with open('data/efreegames/cache.json', 'w') as f:
            json.dump(self.cache, f, indent=4)

    @commands.group(name="efreegames", aliases=["fg"])
    async def efreegames(self, ctx):
        if ctx.invoked_subcommand is None:
            await self.show_current_free_games(ctx)

    @efreegames.command(name="link")
    async def link_account(self, ctx, store: str):
        """Link your store account"""
        store = store.lower()
        if store not in self.stores:
            await ctx.send("Invalid store name!")
            return

        # Create user accounts file if it doesn't exist
        user_accounts = self.load_user_accounts()
        
        # Start OAuth flow or account linking process
        await self.start_account_linking(ctx, store)

    async def start_account_linking(self, ctx, store: str):
        """Handle the account linking process"""
        # Implementation would vary by store
        store_obj = self.stores[store]
        
        try:
            auth_url = await store_obj.get_auth_url(ctx.author.id)
            embed = discord.Embed(
                title=f"Link your {store} account",
                description=f"Click [here]({auth_url}) to link your account",
                color=discord.Color.blue()
            )
            await ctx.author.send(embed=embed)
            await ctx.send("Check your DMs for the account linking process!", ephemeral=True)
        except Exception as e:
            await ctx.send(f"Error starting account linking process: {str(e)}", ephemeral=True)

    @efreegames.command(name="filter")
    async def set_filters(self, ctx, filter_type: str, value: str):
        """Set filters for free game notifications"""
        valid_filters = ['type', 'rating', 'region', 'adult']
        
        if filter_type not in valid_filters:
            await ctx.send(f"Invalid filter type. Valid types: {', '.join(valid_filters)}")
            return

        if filter_type == 'rating':
            try:
                value = float(value)
                if not 0 <= value <= 100:
                    raise ValueError
            except ValueError:
                await ctx.send("Rating must be a number between 0 and 100")
                return

        self.config['filters'][filter_type] = value
        self.save_config()
        await ctx.send(f"Filter updated successfully!")

    @efreegames.command(name="role")
    async def set_ping_role(self, ctx, role: discord.Role, store: Optional[str] = None):
        """Set role to ping for free game notifications"""
        if store:
            self.config['roles'][store] = role.id
        else:
            self.config['roles']['default'] = role.id
        self.save_config()
        await ctx.send(f"Role set successfully!")

    @efreegames.command(name="thread")
    async def set_thread(self, ctx, thread: discord.Thread, store: str):
        """Set thread for store notifications"""
        self.config['thread_ids'][store] = thread.id
        self.save_config()
        await ctx.send(f"Thread set for {store} notifications!")

    async def show_current_free_games(self, ctx):
        """Show currently available free games"""
        embed = discord.Embed(
            title="Currently Available Free Games",
            color=discord.Color.blue()
        )

        for store_name, store in self.stores.items():
            try:
                games = await store.get_free_games()
                if games:
                    game_list = "\n".join([f"• {game.title} (Ends: {game.end_date.strftime('%Y-%m-%d %H:%M')})" 
                                         for game in games])
                    embed.add_field(name=store_name, value=game_list, inline=False)
            except Exception as e:
                embed.add_field(name=store_name, value=f"Error: {str(e)}", inline=False)

        await ctx.send(embed=embed)

    @tasks.loop(hours=24)
    async def check_free_games(self):
        """Check for and post new free games"""
        for store_name, store in self.stores.items():
            try:
                games = await store.get_free_games()
                for game in games:
                    if self.should_post_game(game):
                        await self.post_game(game)
                        self.update_cache(game)
            except Exception as e:
                logger.error(f"Error checking {store_name}: {str(e)}")

    def should_post_game(self, game: GameData) -> bool:
        """Check if game should be posted based on filters and cache"""
        # Check cache
        if game.store_url in self.cache['games']:
            return False

        # Check filters
        filters = self.config.get('filters', {})
        
        if filters.get('type') and game.type != filters['type']:
            return False
            
        if filters.get('rating') and game.rating < filters['rating']:
            return False
            
        if filters.get('adult') is False and game.is_adult:
            return False
            
        if filters.get('region') and filters['region'] not in game.regions:
            return False
            
        return True

    async def post_game(self, game: GameData):
        """Post a free game announcement"""
        embed = discord.Embed(
            title=game.title,
            url=game.store_url,
            description=f"Free to claim until: <t:{int(game.end_date.timestamp())}:F>",
            color=discord.Color.from_rgb(*game.color)
        )
        
        embed.set_image(url=game.image_url)
        
        # Get channel or thread
        channel_id = self.config['thread_ids'].get(
            game.store,
            self.config['channels'].get(game.store, self.config['channels']['default'])
        )
        channel = self.bot.get_channel(channel_id)
        
        if not channel:
            logger.error(f"Could not find channel {channel_id}")
            return

        # Get role to ping
        role_id = self.config['roles'].get(game.store, self.config['roles'].get('default'))
        role_mention = f"<@&{role_id}>" if role_id else ""

        view = GameClaimView(game.store_url)
        await channel.send(content=role_mention, embed=embed, view=view)

    def update_cache(self, game: GameData):
        """Update the cache with new game"""
        self.cache['games'].append(game.store_url)
        if len(self.cache['games']) > 1000:  # Limit cache size
            self.cache['games'] = self.cache['games'][-1000:]
        self.cache['last_check'] = datetime.utcnow().isoformat()
        self.save_cache()

    @efreegames.command(name="clearcache")
    @commands.is_owner()
    async def clear_cache(self, ctx):
        """Clear the games cache"""
        self.cache = {'games': [], 'last_check': None}
        self.save_cache()
        await ctx.send("Cache cleared successfully!")

    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.check_free_games.cancel()
        for store in self.stores.values():
            asyncio.create_task(store.session.close())
