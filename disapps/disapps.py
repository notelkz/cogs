import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from discord.ui import Button, View, Modal, TextInput, Select
from discord import ButtonStyle, SelectOption
import aiohttp
from datetime import datetime
import typing

class DisApps(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "application_category": None,
            "mod_role": None,
            "games_roles": {}  # Dictionary of game names to role IDs
        }
        self.config.register_guild(**default_guild)

    # ... (previous code remains the same until addgame command)

    @disapps.command(name="addgame")
    async def disapps_addgame(self, ctx, role: discord.Role, *, game_name: str):
        """Add a game and its associated role
        
        Example:
        [p]disapps addgame @Battlefield1Role Battlefield 1
        """
        async with self.config.guild(ctx.guild).games_roles() as games_roles:
            games_roles[game_name] = role.id
        await ctx.send(f"Added '{game_name}' with role {role.name}")

    @disapps.command(name="removegame")
    async def disapps_removegame(self, ctx, *, game_name: str):
        """Remove a game and its associated role
        
        Example:
        [p]disapps removegame Battlefield 1
        """
        async with self.config.guild(ctx.guild).games_roles() as games_roles:
            if game_name in games_roles:
                del games_roles[game_name]
                await ctx.send(f"Removed '{game_name}'")
            else:
                await ctx.send(f"Game '{game_name}' not found")

    @disapps.command(name="listgames")
    async def disapps_listgames(self, ctx):
        """List all configured games and their roles"""
        games_roles = await self.config.guild(ctx.guild).games_roles()
        if not games_roles:
            await ctx.send("No games configured")
            return
        
        embed = discord.Embed(title="Configured Games and Roles", color=discord.Color.blue())
        for game, role_id in games_roles.items():
            role = ctx.guild.get_role(role_id)
            embed.add_field(name=game, value=role.name if role else "Role not found", inline=False)
        await ctx.send(embed=embed)

    # ... (rest of the code remains the same)
