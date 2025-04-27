import discord
from redbot.core import commands, Config
from redbot.core.utils.predicates import MessagePredicate
from redbot.core.utils.chat_formatting import box
from datetime import datetime
import asyncio
from typing import Optional
import aiohttp

class DisApps(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "apps_category": None,
            "archive_category": None,
            "mod_role": None,
            "game_roles": {},
            "applications": {}
        }
        self.config.register_guild(**default_guild)
        self.check_task = self.bot.loop.create_task(self.periodic_check())

    def cog_unload(self):
        if self.check_task:
            self.check_task.cancel()

    async def periodic_check(self):
        while True:
            await asyncio.sleep(3600)  # Check every hour
            for guild in self.bot.guilds:
                await self.check_rejoined_users(guild)

    @commands.group(aliases=["da"])
    @commands.admin_or_permissions(administrator=True)
    async def disapps(self, ctx):
        """DisApps configuration commands"""
        pass

    @disapps.command()
    async def setup(self, ctx):
        """Setup the application system"""
        await ctx.send("Please mention the Applications category ID:")
        try:
            msg = await self.bot.wait_for("message", check=MessagePredicate.same_context(ctx), timeout=30)
            apps_category = int(msg.content)
            
            await ctx.send("Please mention the Archive category ID:")
            msg = await self.bot.wait_for("message", check=MessagePredicate.same_context(ctx), timeout=30)
            archive_category = int(msg.content)
            
            await ctx.send("Please mention the Moderator role:")
            msg = await self.bot.wait_for("message", check=MessagePredicate.same_context(ctx), timeout=30)
            mod_role = await commands.RoleConverter().convert(ctx, msg.content)
            
            await self.config.guild(ctx.guild).apps_category.set(apps_category)
            await self.config.guild(ctx.guild).archive_category.set(archive_category)
            await self.config.guild(ctx.guild).mod_role.set(mod_role.id)
            
            await ctx.send("Setup complete! Now let's set up game roles.")
            await self.setup_game_roles(ctx)
            
        except asyncio.TimeoutError:
            await ctx.send("Setup timed out.")

    async def setup_game_roles(self, ctx):
        """Setup game roles"""
        game_roles = {}
        await ctx.send("Enter game names and mention their corresponding roles. Type 'done' when finished.\nFormat: Game Name @Role")
        
        while True:
            try:
                msg = await self.bot.wait_for("message", check=MessagePredicate.same_context(ctx), timeout=60)
                if msg.content.lower() == "done":
                    break
                
                game_name, role_mention = msg.content.rsplit(" ", 1)
                role = await commands.RoleConverter().convert(ctx, role_mention)
                game_roles[game_name] = role.id
                await ctx.send(f"Added {game_name} with role {role.name}")
                
            except (ValueError, commands.RoleNotFound):
                await ctx.send("Invalid format. Please use: Game Name @Role")
            except asyncio.TimeoutError:
                await ctx.send("Setup timed out.")
                return

        await self.config.guild(ctx.guild).game_roles.set(game_roles)
        await ctx.send("Game roles setup complete!")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Handle new member joins"""
        guild = member.guild
        apps_category_id = await self.config.guild(guild).apps_category()
        if not apps_category_id:
            return

        apps_category = discord.utils.get(guild.categories, id=apps_category_id)
        if not apps_category:
            return

        channel_name = f"{member.name.lower()}-application"
        existing_channel = discord.utils.get(guild.channels, name=channel_name)
        
        if existing_channel:
            if existing_channel.category.id == await self.config.guild(guild).archive_category():
                await existing_channel.edit(category=apps_category)
        else:
            channel = await guild.create_text_channel(
                channel_name,
                category=apps_category
            )
            await self.send_application_message(channel, member)

    async def send_application_message(self, channel, member):
        """Send the application message with buttons"""
        embed = discord.Embed(
            title="Welcome to Zero Lives Left!",
            description="[Your server description here]",
            color=discord.Color.blue()
        )

        class ApplicationButtons(discord.ui.View):
            def __init__(self, cog):
                super().__init__(timeout=None)
                self.cog = cog

            @discord.ui.button(label="Apply Now", style=discord.ButtonStyle.green)
            async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                await self.cog.show_application_form(interaction, member)
                button.disabled = True
                await interaction.message.edit(view=self)

            @discord.ui.button(label="Contact Mod", style=discord.ButtonStyle.red)
            async def contact_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                await self.cog.contact_moderator(interaction)

        await channel.send(f"{member.mention}", embed=embed, view=ApplicationButtons(self))

    async def show_application_form(self, interaction: discord.Interaction, member):
        """Show the application form"""
        modal = ApplicationModal(self, member)
        await interaction.response.send_modal(modal)

    async def contact_moderator(self, interaction: discord.Interaction):
        """Contact an online moderator"""
        guild = interaction.guild
        mod_role_id = await self.config.guild(guild).mod_role()
        mod_role = guild.get_role(mod_role_id)
        
        online_mods = [member for member in mod_role.members if member.status != discord.Status.offline]
        
        if online_mods:
            mod_mentions = " ".join([mod.mention for mod in online_mods])
            await interaction.response.send_message(f"{mod_mentions} - Assistance requested!")
        else:
            await interaction.response.send_message(f"{mod_role.mention} - No moderators are currently online, but they will be notified!")

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Handle member leaves"""
        guild = member.guild
        channel_name = f"{member.name.lower()}-application"
        channel = discord.utils.get(guild.channels, name=channel_name)
        
        if channel:
            archive_category_id = await self.config.guild(guild).archive_category()
            archive_category = discord.utils.get(guild.categories, id=archive_category_id)
            if archive_category:
                await channel.edit(category=archive_category)

    async def check_rejoined_users(self, guild):
        """Check for rejoined users and move their channels back"""
        apps_category_id = await self.config.guild(guild).apps_category()
        archive_category_id = await self.config.guild(guild).archive_category()
        
        if not (apps_category_id and archive_category_id):
            return
            
        apps_category = discord.utils.get(guild.categories, id=apps_category_id)
        archive_category = discord.utils.get(guild.categories, id=archive_category_id)
        
        if not (apps_category and archive_category):
            return
            
        for channel in archive_category.channels:
            if channel.name.endswith("-application"):
                username = channel.name[:-12]
                member = discord.utils.get(guild.members, name=username)
                if member:
                    await channel.edit(category=apps_category)

class ApplicationModal(discord.ui.Modal):
    def __init__(self, cog, member):
        super().__init__(title="Zero Lives Left Application")
        self.cog = cog
        self.member = member
        
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
        guild = interaction.guild
        game_roles = await self.cog.config.guild(guild).game_roles()
        
        options = []
        for game in game_roles.keys():
            options.append(discord.SelectOption(label=game, value=game))

        class GameSelect(discord.ui.Select):
            def __init__(self):
                super().__init__(
                    placeholder="Select the games you play",
                    min_values=1,
                    max_values=len(options),
                    options=options
                )

        class GameView(discord.ui.View):
            def __init__(self):
                super().__init__()
                self.add_item(GameSelect())

        await interaction.response.send_message("Please select the games you play:", view=GameView())

async def setup(bot):
    await bot.add_cog(DisApps(bot))
