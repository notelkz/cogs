import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from datetime import datetime
import asyncio

class DisApps(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "applications_category": None,
            "archive_category": None,
            "game_roles": {},  # Will store as {"Game Name": role_id}
            "mod_role": None
        }
        self.config.register_guild(**default_guild)
        self.check_rejoin_task = self.bot.loop.create_task(self.check_rejoins())

    @disapps.command(name="addrole")
    async def add_game_role(self, ctx, role: discord.Role, *, game_name: str):
        """Add a game role for applications
        
        Parameters:
        -----------
        role: The Discord role to assign
        game_name: The name of the game (can include spaces)
        
        Example:
        --------
        [p]disapps addrole @Minecraft Minecraft
        [p]disapps addrole @GTA "Grand Theft Auto V"
        """
        async with self.config.guild(ctx.guild).game_roles() as roles:
            roles[game_name] = role.id
        await ctx.send(f"Added role for '{game_name}'")

    @disapps.command(name="removerole")
    async def remove_game_role(self, ctx, *, game_name: str):
        """Remove a game role from applications
        
        Parameters:
        -----------
        game_name: The name of the game to remove (can include spaces)
        
        Example:
        --------
        [p]disapps removerole Minecraft
        [p]disapps removerole "Grand Theft Auto V"
        """
        async with self.config.guild(ctx.guild).game_roles() as roles:
            if game_name in roles:
                del roles[game_name]
                await ctx.send(f"Removed role for '{game_name}'")
            else:
                await ctx.send(f"No role found for '{game_name}'")

    @disapps.command(name="listroles")
    async def list_game_roles(self, ctx):
        """List all configured game roles"""
        roles = await self.config.guild(ctx.guild).game_roles()
        if not roles:
            await ctx.send("No game roles have been configured.")
            return

        embed = discord.Embed(
            title="Configured Game Roles",
            color=discord.Color.blue()
        )
        
        for game, role_id in roles.items():
            role = ctx.guild.get_role(role_id)
            role_status = f"@{role.name}" if role else "Role not found"
            embed.add_field(name=game, value=role_status, inline=False)

        await ctx.send(embed=embed)

    async def create_application_form(self, interaction: discord.Interaction):
        """Creates and returns the application form with game checkboxes"""
        game_roles = await self.config.guild(interaction.guild).game_roles()
        
        # Create the modal with basic information fields
        class GameApplicationModal(discord.ui.Modal):
            def __init__(self):
                super().__init__(title="Application Form")
                self.add_item(discord.ui.TextInput(
                    label="Age",
                    placeholder="Enter your age",
                    required=True,
                    min_length=1,
                    max_length=3
                ))
                self.add_item(discord.ui.TextInput(
                    label="Location",
                    placeholder="Enter your location",
                    required=True
                ))
                self.add_item(discord.ui.TextInput(
                    label="Steam ID",
                    placeholder="Enter your Steam ID",
                    required=True
                ))

            async def on_submit(self, interaction: discord.Interaction):
                # Create the game selection view after basic info is submitted
                view = GameSelectionView(game_roles)
                await interaction.response.send_message(
                    "Please select the games you play:",
                    view=view,
                    ephemeral=True
                )

        # Create the view for game selection
        class GameSelectionView(discord.ui.View):
            def __init__(self, game_roles):
                super().__init__()
                self.game_selections = {}
                
                # Add a button for each game
                for game in game_roles.keys():
                    self.add_item(GameButton(game))

            async def on_complete(self, interaction: discord.Interaction):
                selected_games = [game for game, selected in self.game_selections.items() if selected]
                
                # Assign roles based on selections
                member = interaction.user
                for game in selected_games:
                    role_id = game_roles[game]
                    role = interaction.guild.get_role(role_id)
                    if role:
                        try:
                            await member.add_roles(role)
                        except discord.Forbidden:
                            await interaction.response.send_message(
                                f"Failed to assign role for {game}. Missing permissions.",
                                ephemeral=True
                            )

                # Disable the original Apply Now button
                original_message = interaction.message
                if original_message:
                    try:
                        view = original_message.components[0]
                        for child in view.children:
                            if child.custom_id == "apply_button":
                                child.disabled = True
                        await original_message.edit(view=view)
                    except:
                        pass

                # Ping moderators
                mod_role_id = await self.config.guild(interaction.guild).mod_role()
                mod_role = interaction.guild.get_role(mod_role_id)
                if mod_role:
                    online_mods = [member for member in mod_role.members if member.status != discord.Status.offline]
                    if online_mods:
                        await interaction.channel.send(
                            f"New application submitted! {' '.join([mod.mention for mod in online_mods])}",
                            allowed_mentions=discord.AllowedMentions(users=True)
                        )
                    else:
                        await interaction.channel.send(
                            f"New application submitted! {mod_role.mention}",
                            allowed_mentions=discord.AllowedMentions(roles=True)
                        )

        class GameButton(discord.ui.Button):
            def __init__(self, game_name: str):
                super().__init__(
                    label=game_name,
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"game_{game_name}"
                )
                self.game_name = game_name

            async def callback(self, interaction: discord.Interaction):
                view: GameSelectionView = self.view
                view.game_selections[self.game_name] = not view.game_selections.get(self.game_name, False)
                
                # Update button style based on selection
                self.style = discord.ButtonStyle.success if view.game_selections[self.game_name] else discord.ButtonStyle.secondary
                
                await interaction.response.edit_message(view=view)

        return GameApplicationModal()

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return

        if interaction.custom_id == "apply_button":
            modal = await self.create_application_form(interaction)
            await interaction.response.send_modal(modal)
