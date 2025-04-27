import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from datetime import datetime
import asyncio
from typing import Dict, Optional
import aiohttp

class DisApps(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "applications_category": None,
            "archive_category": None,
            "moderator_role": None,
            "game_roles": {},
            "application_channels": {}
        }
        self.config.register_guild(**default_guild)
        self.check_task = self.bot.loop.create_task(self.check_rejoins())

    def cog_unload(self):
        if self.check_task:
            self.check_task.cancel()

    async def create_application_channel(self, guild: discord.Guild, member: discord.Member):
        config = await self.config.guild(guild).all()
        category = guild.get_channel(config["applications_category"])
        
        if not category:
            return None

        channel_name = f"{member.name.lower()}-application"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        channel = await category.create_text_channel(channel_name, overwrites=overwrites)
        
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
        
        async with self.config.guild(guild).application_channels() as channels:
            channels[str(member.id)] = channel.id

        return channel

    @commands.group(aliases=["da"])
    @commands.admin_or_permissions(administrator=True)
    async def disapp(self, ctx: commands.Context):
        """DisApps configuration commands"""
        pass

    @disapp.command()
    async def setcategory(self, ctx: commands.Context, category: discord.CategoryChannel):
        """Set the applications category"""
        await self.config.guild(ctx.guild).applications_category.set(category.id)
        await ctx.send(f"Applications category set to {category.name}")

    @disapp.command()
    async def setarchive(self, ctx: commands.Context, category: discord.CategoryChannel):
        """Set the archive category"""
        await self.config.guild(ctx.guild).archive_category.set(category.id)
        await ctx.send(f"Archive category set to {category.name}")

    @disapp.command()
    async def setmodrole(self, ctx: commands.Context, role: discord.Role):
        """Set the moderator role"""
        await self.config.guild(ctx.guild).moderator_role.set(role.id)
        await ctx.send(f"Moderator role set to {role.name}")

    @disapp.command()
    async def addgame(self, ctx: commands.Context, role: discord.Role, *, game_name: str):
        """Add a game role"""
        async with self.config.guild(ctx.guild).game_roles() as games:
            games[game_name] = role.id
        await ctx.send(f"Added {game_name} with role {role.name}")

    async def check_rejoins(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                for guild in self.bot.guilds:
                    config = await self.config.guild(guild).all()
                    channels = config["application_channels"]
                    
                    for user_id, channel_id in channels.items():
                        channel = guild.get_channel(channel_id)
                        if not channel:
                            continue
                            
                        member = guild.get_member(int(user_id))
                        apps_category = guild.get_channel(config["applications_category"])
                        archive_category = guild.get_channel(config["archive_category"])
                        
                        if member and channel.category == archive_category:
                            await channel.edit(category=apps_category)
                        elif not member and channel.category == apps_category:
                            await channel.edit(category=archive_category)
                            
            except Exception as e:
                print(f"Error in check_rejoins: {e}")
                
            await asyncio.sleep(3600)  # Check every hour

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        await self.create_application_channel(member.guild, member)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if not interaction.type == discord.InteractionType.component:
            return

        if interaction.custom_id == "apply_button":
            # Create application modal
            modal = ApplicationModal()
            await interaction.response.send_modal(modal)

        elif interaction.custom_id == "contact_mod":
            config = await self.config.guild(interaction.guild).all()
            mod_role = interaction.guild.get_role(config["moderator_role"])
            
            online_mods = [member for member in mod_role.members 
                         if member.status != discord.Status.offline]

            if online_mods:
                await interaction.response.send_message(
                    f"Contacting online moderators: {', '.join([mod.mention for mod in online_mods])}")
            else:
                await interaction.response.send_message(f"{mod_role.mention} No moderators are currently online.")

class ApplicationModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Application Form")
        
        self.add_item(discord.ui.TextInput(
            label="Age",
            placeholder="Enter your age",
            custom_id="age",
            style=discord.TextStyle.short
        ))
        
        self.add_item(discord.ui.TextInput(
            label="Location",
            placeholder="Enter your location",
            custom_id="location",
            style=discord.TextStyle.short
        ))
        
        self.add_item(discord.ui.TextInput(
            label="Steam ID",
            placeholder="Enter your Steam ID",
            custom_id="steam_id",
            style=discord.TextStyle.short
        ))

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="New Application",
            color=discord.Color.green()
        )
        
        embed.add_field(name="Age", value=self.children[0].value)
        embed.add_field(name="Location", value=self.children[1].value)
        embed.add_field(name="Steam ID", value=self.children[2].value)
        
        await interaction.response.send_message(embed=embed)
        
        # Disable the Apply Now button
        for item in interaction.message.components[0].children:
            if item.custom_id == "apply_button":
                item.disabled = True
        
        await interaction.message.edit(view=interaction.message.components[0])

async def setup(bot):
    await bot.add_cog(DisApps(bot))
