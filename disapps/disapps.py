import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from discord.ui import Button, View, Modal, TextInput, Select
from discord import ButtonStyle, SelectOption
import aiohttp
from datetime import datetime
import typing

class ApplicationModal(Modal):
    def __init__(self):
        super().__init__(title="Application Form")
        self.age = TextInput(
            label="Age",
            style=discord.TextStyle.short,
            placeholder="Enter your age...",
            required=True
        )
        self.location = TextInput(
            label="Location",
            style=discord.TextStyle.short,
            placeholder="Enter your location...",
            required=True
        )
        self.steam_id = TextInput(
            label="Steam ID",
            style=discord.TextStyle.short,
            placeholder="Enter your Steam ID...",
            required=True
        )
        self.add_item(self.age)
        self.add_item(self.location)
        self.add_item(self.steam_id)

class GameSelect(View):
    def __init__(self, games_roles):
        super().__init__(timeout=None)
        self.games_roles = games_roles
        
        options = [
            SelectOption(label=game, value=str(role_id))
            for game, role_id in games_roles.items()
        ]
        
        select = Select(
            placeholder="Select the games you play...",
            min_values=0,
            max_values=len(options),
            options=options
        )
        
        async def select_callback(interaction: discord.Interaction):
            await interaction.response.defer()
            member = interaction.user
            for value in select.values:
                role = interaction.guild.get_role(int(value))
                if role:
                    await member.add_roles(role)
            await interaction.followup.send("Your game roles have been updated!", ephemeral=True)
            
        select.callback = select_callback
        self.add_item(select)

class ApplicationView(View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
        self.application_submitted = False

    @discord.ui.button(label="Apply Now", style=ButtonStyle.green, custom_id="apply_now")
    async def apply_button(self, interaction: discord.Interaction, button: Button):
        if self.application_submitted:
            await interaction.response.send_message("You have already submitted an application.", ephemeral=True)
            return

        modal = ApplicationModal()
        await interaction.response.send_modal(modal)
        
        # Wait for the modal to be submitted
        try:
            await modal.wait()
        except:
            return
            
        # Create and send the application embed
        embed = discord.Embed(
            title="Application Submitted",
            color=discord.Color.green()
        )
        embed.add_field(name="Age", value=modal.age.value)
        embed.add_field(name="Location", value=modal.location.value)
        embed.add_field(name="Steam ID", value=modal.steam_id.value)
        
        # Send the embed as a new message
        await interaction.channel.send(embed=embed)
        
        # Disable the Apply Now button
        self.application_submitted = True
        button.disabled = True
        try:
            await interaction.message.edit(view=self)
        except:
            pass

        # Notify moderators
        mod_role_id = await self.cog.config.guild(interaction.guild).mod_role()
        if mod_role_id:
            mod_role = interaction.guild.get_role(mod_role_id)
            if mod_role:
                online_mods = [member for member in mod_role.members if member.status != discord.Status.offline]
                if online_mods:
                    mods_mention = " ".join([mod.mention for mod in online_mods])
                    await interaction.channel.send(
                        f"{mods_mention}\nNew application submitted by {interaction.user.mention}"
                    )
                else:
                    await interaction.channel.send(
                        f"{mod_role.mention}\nNew application submitted by {interaction.user.mention}"
                    )
        
        # Create and send game selection view
        games_roles = await self.cog.config.guild(interaction.guild).games_roles()
        if games_roles:
            game_select = GameSelect(games_roles)
            await interaction.channel.send("Please select the games you play:", view=game_select)
        else:
            await interaction.channel.send("No games have been configured yet.")

    @discord.ui.button(label="Contact Mod", style=ButtonStyle.red)
    async def contact_mod_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        
        mod_role_id = await self.cog.config.guild(interaction.guild).mod_role()
        if not mod_role_id:
            await interaction.followup.send("Moderator role not configured.", ephemeral=True)
            return

        mod_role = interaction.guild.get_role(mod_role_id)
        if not mod_role:
            await interaction.followup.send("Moderator role not found.", ephemeral=True)
            return

        online_mods = [member for member in mod_role.members if member.status != discord.Status.offline]
        
        if online_mods:
            mods_mention = " ".join([mod.mention for mod in online_mods])
            await interaction.channel.send(f"{mods_mention} - Help requested by {interaction.user.mention}")
        else:
            await interaction.channel.send(f"{mod_role.mention} - Help requested by {interaction.user.mention}")
        
        await interaction.followup.send("A moderator has been notified.", ephemeral=True)

class DisApps(commands.Cog):
    """A cog for handling Discord applications and game role assignments"""
    
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "application_category": None,
            "mod_role": None,
            "games_roles": {}
        }
        self.config.register_guild(**default_guild)

    group = commands.group(name="disapps")
    @group
    @commands.admin()
    async def disapps(self, ctx):
        """Configuration commands for the DisApps system"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @disapps.command()
    @commands.admin()
    async def setcategory(self, ctx, category: discord.CategoryChannel):
        """Set the applications category"""
        await self.config.guild(ctx.guild).application_category.set(category.id)
        await ctx.send(f"Applications category set to {category.name}")

    @disapps.command()
    @commands.admin()
    async def setmodrole(self, ctx, role: discord.Role):
        """Set the moderator role"""
        await self.config.guild(ctx.guild).mod_role.set(role.id)
        await ctx.send(f"Moderator role set to {role.name}")

    @disapps.command()
    @commands.admin()
    async def addgame(self, ctx, role: discord.Role, *, game_name: str):
        """Add a game and its associated role
        
        Example:
        [p]disapps addgame @Battlefield1Role Battlefield 1
        """
        async with self.config.guild(ctx.guild).games_roles() as games_roles:
            games_roles[game_name] = role.id
        await ctx.send(f"Added '{game_name}' with role {role.name}")

    @disapps.command()
    @commands.admin()
    async def removegame(self, ctx, *, game_name: str):
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

    @disapps.command()
    @commands.admin()
    async def listgames(self, ctx):
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

    async def create_application_channel(self, guild, user):
        category_id = await self.config.guild(guild).application_category()
        if not category_id:
            return None

        category = guild.get_channel(category_id)
        if not category:
            return None

        channel_name = f"{user.name.lower()}-application"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        mod_role_id = await self.config.guild(guild).mod_role()
        if mod_role_id:
            mod_role = guild.get_role(mod_role_id)
            if mod_role:
                overwrites[mod_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        channel = await category.create_text_channel(channel_name, overwrites=overwrites)
        return channel

    async def create_application_embed(self, user):
        embed = discord.Embed(
            title="Zero Lives Left Application",
            description="Welcome to Zero Lives Left! Please click the 'Apply Now' button below to begin your application process.",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text=f"Application for {user.name}")
        return embed

    @commands.Cog.listener()
    async def on_member_join(self, member):
        channel = await self.create_application_channel(member.guild, member)
        if channel:
            embed = await self.create_application_embed(member)
            view = ApplicationView(self)
            await channel.send(f"{member.mention}", embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(DisApps(bot))
