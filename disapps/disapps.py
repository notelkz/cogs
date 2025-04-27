import discord
from redbot.core import commands, Config
from redbot.core.utils.predicates import MessagePredicate
from redbot.core.utils.chat_formatting import box
from datetime import datetime
import asyncio

class DisApps(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "setup_complete": False,
            "applications_category": None,
            "archive_category": None,
            "moderator_role": None,
            "game_roles": {},
            "user_channels": {}
        }
        self.config.register_guild(**default_guild)
        self.check_task = self.bot.loop.create_task(self.channel_check_loop())

    def cog_unload(self):
        if self.check_task:
            self.check_task.cancel()

    async def channel_check_loop(self):
        while True:
            await asyncio.sleep(3600)  # Check every hour
            for guild in self.bot.guilds:
                await self.check_channels(guild)

    @commands.group(aliases=["da"])
    @commands.admin_or_permissions(administrator=True)
    async def disapps(self, ctx):
        """DisApps management commands"""
        pass

    @disapps.command()
    async def setup(self, ctx):
        """Initial setup for DisApps"""
        guild = ctx.guild
        
        await ctx.send("Starting setup process. Please answer the following questions.")
        
        # Applications Category
        await ctx.send("Please create an Applications category and enter its ID:")
        pred = MessagePredicate.valid_int(ctx)
        await self.bot.wait_for("message", check=pred)
        apps_category = pred.result
        
        # Archive Category
        await ctx.send("Please create an Archive category and enter its ID:")
        pred = MessagePredicate.valid_int(ctx)
        await self.bot.wait_for("message", check=pred)
        archive_category = pred.result
        
        # Moderator Role
        await ctx.send("Please enter the Moderator role ID:")
        pred = MessagePredicate.valid_int(ctx)
        await self.bot.wait_for("message", check=pred)
        mod_role = pred.result
        
        # Game Roles
        game_roles = {}
        await ctx.send("Enter game names and their corresponding role IDs (format: 'Game Name:role_id'). Type 'done' when finished.")
        while True:
            message = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author)
            if message.content.lower() == "done":
                break
            try:
                game, role_id = message.content.split(":", 1)
                game_roles[game.strip()] = int(role_id.strip())
            except ValueError:
                await ctx.send("Invalid format. Please use 'Game Name:role_id'")
                continue

        # Save configuration
        await self.config.guild(guild).applications_category.set(apps_category)
        await self.config.guild(guild).archive_category.set(archive_category)
        await self.config.guild(guild).moderator_role.set(mod_role)
        await self.config.guild(guild).game_roles.set(game_roles)
        await self.config.guild(guild).setup_complete.set(True)
        
        await ctx.send("Setup complete!")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild = member.guild
        if not await self.config.guild(guild).setup_complete():
            return

        # Create or move channel
        channel_name = f"{member.name.lower()}-application"
        category_id = await self.config.guild(guild).applications_category()
        category = discord.utils.get(guild.categories, id=category_id)
        
        existing_channels = await self.config.guild(guild).user_channels()
        
        if str(member.id) in existing_channels:
            channel = guild.get_channel(existing_channels[str(member.id)])
            if channel:
                await channel.edit(category=category)
        else:
            channel = await category.create_text_channel(channel_name)
            existing_channels[str(member.id)] = channel.id
            await self.config.guild(guild).user_channels.set(existing_channels)

        # Set permissions
        await channel.set_permissions(member, read_messages=True, send_messages=True)
        
        # Send welcome message and buttons
        embed = discord.Embed(
            title="Welcome to Zero Lives Left",
            description="[Description placeholder]",
            color=discord.Color.blue()
        )
        
        class ApplicationButtons(discord.ui.View):
            def __init__(self, cog):
                super().__init__(timeout=None)
                self.cog = cog

            @discord.ui.button(label="Apply Now", style=discord.ButtonStyle.green)
            async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                await self.cog.show_application_form(interaction, button)

            @discord.ui.button(label="Contact Mod", style=discord.ButtonStyle.red)
            async def contact_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                await self.cog.contact_moderator(interaction)

        await channel.send(f"{member.mention}", embed=embed, view=ApplicationButtons(self))

    async def show_application_form(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Create modal for application
        class ApplicationForm(discord.ui.Modal):
            def __init__(self):
                super().__init__(title="Application Form")
                self.add_item(discord.ui.TextInput(label="Age", placeholder="Enter your age"))
                self.add_item(discord.ui.TextInput(label="Location", placeholder="Enter your location"))
                self.add_item(discord.ui.TextInput(label="Steam ID", placeholder="Enter your Steam ID"))
                
            async def on_submit(self, interaction: discord.Interaction):
                # Process form submission
                await interaction.response.send_message("Application submitted!", ephemeral=True)
                button.disabled = True
                await interaction.message.edit(view=button.view)
                
                # Ping moderators
                mod_role = interaction.guild.get_role(await self.cog.config.guild(interaction.guild).moderator_role())
                await interaction.channel.send(f"{mod_role.mention} New application submitted!")

        await interaction.response.send_modal(ApplicationForm())

    async def contact_moderator(self, interaction: discord.Interaction):
        guild = interaction.guild
        mod_role_id = await self.config.guild(guild).moderator_role()
        mod_role = guild.get_role(mod_role_id)
        
        online_mods = [member for member in mod_role.members if member.status != discord.Status.offline]
        
        if online_mods:
            await interaction.response.send_message(f"{' '.join([mod.mention for mod in online_mods])} Assistance requested!")
        else:
            await interaction.response.send_message(f"{mod_role.mention} No moderators are currently online, but they will see this when they return!")

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        guild = member.guild
        existing_channels = await self.config.guild(guild).user_channels()
        
        if str(member.id) in existing_channels:
            channel = guild.get_channel(existing_channels[str(member.id)])
            if channel:
                archive_category_id = await self.config.guild(guild).archive_category()
                archive_category = guild.get_channel(archive_category_id)
                await channel.edit(category=archive_category)

    async def check_channels(self, guild):
        """Check and move channels for rejoined members"""
        user_channels = await self.config.guild(guild).user_channels()
        apps_category_id = await self.config.guild(guild).applications_category()
        apps_category = guild.get_channel(apps_category_id)
        
        for user_id, channel_id in user_channels.items():
            channel = guild.get_channel(channel_id)
            if channel:
                member = guild.get_member(int(user_id))
                if member and channel.category_id != apps_category_id:
                    await channel.edit(category=apps_cat
