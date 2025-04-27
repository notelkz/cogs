import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from datetime import datetime
import asyncio
from typing import Dict, Optional
import aiohttp
from discord.ui import Button, View, Modal, TextInput
from discord import ButtonStyle, Interaction, TextStyle

class GameCheckbox(discord.ui.View):
    def __init__(self, games: Dict[str, int]):
        super().__init__()
        self.games = games
        self.selected_games = []
        
        for game, role_id in games.items():
            button = discord.ui.Button(label=game, custom_id=f"game_{role_id}", style=ButtonStyle.secondary)
            button.callback = self.button_callback
            self.add_item(button)
    
    async def button_callback(self, interaction: Interaction):
        button = interaction.component
        if button.style == ButtonStyle.secondary:
            button.style = ButtonStyle.success
            self.selected_games.append(int(button.custom_id.split('_')[1]))
        else:
            button.style = ButtonStyle.secondary
            self.selected_games.remove(int(button.custom_id.split('_')[1]))
        await interaction.response.edit_message(view=self)

class ApplicationModal(Modal):
    def __init__(self):
        super().__init__(title="Application Form")
        self.age = TextInput(
            label="Age",
            placeholder="Enter your age",
            required=True,
            style=TextStyle.short
        )
        self.location = TextInput(
            label="Location",
            placeholder="Enter your location",
            required=True,
            style=TextStyle.short
        )
        self.steam_id = TextInput(
            label="Steam ID",
            placeholder="Enter your Steam ID",
            required=True,
            style=TextStyle.short
        )
        self.add_item(self.age)
        self.add_item(self.location)
        self.add_item(self.steam_id)

class DisApps(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "applications_category": None,
            "archive_category": None,
            "moderator_role": None,
            "game_roles": {},
            "application_channels": {},
            "welcome_message": "Welcome to Zero Lives Left!"
        }
        self.config.register_guild(**default_guild)
        self.check_rejoin_task = self.bot.loop.create_task(self.check_rejoins())

    async def cog_unload(self):
        if self.check_rejoin_task:
            self.check_rejoin_task.cancel()

    @commands.group()
    @commands.admin_or_permissions(administrator=True)
    async def disappsset(self, ctx):
        """Configure the DisApps cog"""
        pass

    @disappsset.command()
    async def categories(self, ctx, applications: discord.CategoryChannel, archive: discord.CategoryChannel):
        """Set the applications and archive categories"""
        await self.config.guild(ctx.guild).applications_category.set(applications.id)
        await self.config.guild(ctx.guild).archive_category.set(archive.id)
        await ctx.send("Categories have been set!")

    @disappsset.command()
    async def modrole(self, ctx, role: discord.Role):
        """Set the moderator role"""
        await self.config.guild(ctx.guild).moderator_role.set(role.id)
        await ctx.send("Moderator role has been set!")

    @disappsset.command()
    async def addgame(self, ctx, role: discord.Role, *, game_name: str):
        """Add a game and its associated role"""
        async with self.config.guild(ctx.guild).game_roles() as games:
            games[game_name] = role.id
        await ctx.send(f"Added {game_name} with role {role.name}")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild = member.guild
        apps_category_id = await self.config.guild(guild).applications_category()
        if not apps_category_id:
            return

        channel_name = f"{member.name.lower()}-application"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True)
        }

        category = guild.get_channel(apps_category_id)
        channel = await category.create_text_channel(channel_name, overwrites=overwrites)

        welcome_embed = discord.Embed(
            title="Welcome to Zero Lives Left!",
            description=await self.config.guild(guild).welcome_message(),
            color=discord.Color.blue()
        )

        class ApplicationView(View):
            def __init__(self, cog):
                super().__init__()
                self.cog = cog

            @discord.ui.button(label="Apply Now", style=ButtonStyle.green)
            async def apply_button(self, interaction: Interaction, button: Button):
                modal = ApplicationModal()
                await interaction.response.send_modal(modal)
                await modal.wait()
                
                games_view = GameCheckbox(await self.cog.config.guild(guild).game_roles())
                await interaction.followup.send("Select the games you play:", view=games_view)
                await games_view.wait()

                # Assign roles and notify moderators
                for role_id in games_view.selected_games:
                    role = guild.get_role(role_id)
                    if role:
                        await member.add_roles(role)

                button.disabled = True
                await interaction.message.edit(view=self)
                
                mod_role_id = await self.cog.config.guild(guild).moderator_role()
                mod_role = guild.get_role(mod_role_id)
                if mod_role:
                    await channel.send(f"{mod_role.mention} New application submitted!")

            @discord.ui.button(label="Contact Mod", style=ButtonStyle.red)
            async def contact_button(self, interaction: Interaction, button: Button):
                mod_role_id = await self.cog.config.guild(guild).moderator_role()
                mod_role = guild.get_role(mod_role_id)
                if mod_role:
                    await channel.send(f"{mod_role.mention} User requested assistance!")
                await interaction.response.defer()

        await channel.send(f"{member.mention}", embed=welcome_embed, view=ApplicationView(self))
        
        async with self.config.guild(guild).application_channels() as channels:
            channels[str(member.id)] = channel.id

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        guild = member.guild
        archive_category_id = await self.config.guild(guild).archive_category()
        if not archive_category_id:
            return

        async with self.config.guild(guild).application_channels() as channels:
            if str(member.id) in channels:
                channel = guild.get_channel(channels[str(member.id)])
                if channel:
                    archive_category = guild.get_channel(archive_category_id)
                    await channel.edit(category=archive_category)

    async def check_rejoins(self):
        while True:
            try:
                await asyncio.sleep(3600)  # Check every hour
                for guild in self.bot.guilds:
                    archive_category_id = await self.config.guild(guild).archive_category()
                    apps_category_id = await self.config.guild(guild).applications_category()
                    if not (archive_category_id and apps_category_id):
                        continue

                    async with self.config.guild(guild).application_channels() as channels:
                        for user_id, channel_id in channels.items():
                            member = guild.get_member(int(user_id))
                            channel = guild.get_channel(channel_id)
                            if channel and member:
                                correct_category = guild.get_channel(apps_category_id)
                                if channel.category_id != apps_category_id:
                                    await channel.edit(category=correct_category)

            except Exception as e:
                print(f"Error in check_rejoins: {e}")
                await asyncio.sleep(60)

async def setup(bot):
    await bot.add_cog(DisApps(bot))
