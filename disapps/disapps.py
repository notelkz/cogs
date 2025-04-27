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
            "game_roles": {},
            "mod_role": None
        }
        self.config.register_guild(**default_guild)
        self.check_rejoin_task = self.bot.loop.create_task(self.check_rejoins())

    async def check_rejoins(self):
        while True:
            try:
                await asyncio.sleep(3600)  # Check every hour
                for guild in self.bot.guilds:
                    archive_category = await self.config.guild(guild).archive_category()
                    apps_category = await self.config.guild(guild).applications_category()
                    
                    if not archive_category or not apps_category:
                        continue

                    archive_cat = guild.get_channel(archive_category)
                    apps_cat = guild.get_channel(apps_category)

                    for channel in archive_cat.channels:
                        if channel.name.endswith('-application'):
                            user_name = channel.name.replace('-application', '')
                            member = guild.get_member_named(user_name)
                            if member:
                                await channel.edit(category=apps_cat)
            except Exception as e:
                print(f"Error in check_rejoins: {e}")

    @commands.group()
    @commands.admin_or_permissions(administrator=True)
    async def disapps(self, ctx):
        """DisApps configuration commands"""
        pass

    @disapps.command()
    async def setup(self, ctx, apps_category: discord.CategoryChannel, archive_category: discord.CategoryChannel, mod_role: discord.Role):
        """Setup the application system"""
        await self.config.guild(ctx.guild).applications_category.set(apps_category.id)
        await self.config.guild(ctx.guild).archive_category.set(archive_category.id)
        await self.config.guild(ctx.guild).mod_role.set(mod_role.id)
        await ctx.send("Setup complete!")

    @disapps.command()
    async def addrole(self, ctx, game: str, role: discord.Role):
        """Add a game role for applications"""
        async with self.config.guild(ctx.guild).game_roles() as roles:
            roles[game] = role.id
        await ctx.send(f"Added role for {game}")

    async def create_application_channel(self, member: discord.Member):
        category_id = await self.config.guild(member.guild).applications_category()
        category = member.guild.get_channel(category_id)
        
        channel = await member.guild.create_text_channel(
            f"{member.name.lower()}-application",
            category=category
        )
        
        embed = discord.Embed(
            title="Welcome to Zero Lives Left",
            description="[Your provided description will go here]",
            color=discord.Color.blue()
        )

        apply_button = discord.ui.Button(style=discord.ButtonStyle.green, label="Apply Now", custom_id="apply_button")
        contact_mod_button = discord.ui.Button(style=discord.ButtonStyle.red, label="Contact Mod", custom_id="contact_mod")
        
        view = discord.ui.View()
        view.add_item(apply_button)
        view.add_item(contact_mod_button)

        await channel.send(f"{member.mention}", embed=embed, view=view)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        await self.create_application_channel(member)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        archive_category_id = await self.config.guild(member.guild).archive_category()
        archive_category = member.guild.get_channel(archive_category_id)
        
        for channel in member.guild.channels:
            if channel.name == f"{member.name.lower()}-application":
                await channel.edit(category=archive_category)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type != discord.InteractionType.component:
            return

        if interaction.custom_id == "apply_button":
            # Create application modal
            modal = ApplicationModal()
            await interaction.response.send_modal(modal)

        elif interaction.custom_id == "contact_mod":
            mod_role_id = await self.config.guild(interaction.guild).mod_role()
            mod_role = interaction.guild.get_role(mod_role_id)
            
            online_mods = [member for member in mod_role.members if member.status != discord.Status.offline]
            
            if online_mods:
                await interaction.response.send_message(f"{' '.join([mod.mention for mod in online_mods])}", allowed_mentions=discord.AllowedMentions(roles=True))
            else:
                await interaction.response.send_message(f"{mod_role.mention}", allowed_mentions=discord.AllowedMentions(roles=True))

class ApplicationModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Application Form")
        self.add_item(discord.ui.TextInput(label="Age", placeholder="Enter your age"))
        self.add_item(discord.ui.TextInput(label="Location", placeholder="Enter your location"))
        self.add_item(discord.ui.TextInput(label="Steam ID", placeholder="Enter your Steam ID"))
        # Games checkboxes will be handled separately due to Discord UI limitations

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.send_message("Application submitted!", ephemeral=True)
        # Handle form submission and role assignment here

def setup(bot):
    bot.add_cog(DisApps(bot))
