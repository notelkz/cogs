class EFreeGames(commands.Cog):
    """Track and post free games from various storefronts."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        self.session = aiohttp.ClientSession()
        
        # API endpoints
        self.epic_api_url = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
        self.steam_api_url = "https://store.steampowered.com/api/featuredcategories"
        
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

async def setup(bot: Red):
    await bot.add_cog(EFreeGames(bot))
