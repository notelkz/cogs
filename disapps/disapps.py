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

        # Add moderator role permissions if set
        if config["moderator_role"]:
            mod_role = guild.get_role(config["moderator_role"])
            if mod_role:
                overwrites[mod_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        channel = await category.create_text_channel(channel_name, overwrites=overwrites)
        
        embed = discord.Embed(
            title="Welcome to Zero Lives Left",
            description="[Your provided description will go here]",
            color=discord.Color.blue()
        )

        class ApplicationView(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=None)

            @discord.ui.button(label="Apply Now", style=discord.ButtonStyle.green, custom_id="apply_button")
            async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                modal = ApplicationModal()
                await interaction.response.send_modal(modal)
                button.disabled = True
                await interaction.message.edit(view=self)

            @discord.ui.button(label="Contact Mod", style=discord.ButtonStyle.red, custom_id="contact_mod")
            async def contact_mod(self, interaction: discord.Interaction, button: discord.ui.Button):
                config = await self.bot.get_cog("DisApps").config.guild(interaction.guild).all()
                mod_role = interaction.guild.get_role(config["moderator_role"])
                
                if not mod_role:
                    await interaction.response.send_message("Moderator role not configured.", ephemeral=True)
                    return

                online_mods = [member for member in mod_role.members 
                             if member.status != discord.Status.offline]

                if online_mods:
                    await interaction.response.send_message(
                        f"Contacting online moderators: {', '.join([mod.mention for mod in online_mods])}")
                else:
                    await interaction.response.send_message(f"{mod_role.mention} No moderators are currently online.")

        view = ApplicationView()
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
    async def on_member_remove(self, member: discord.Member):
        config = await self.config.guild(member.guild).all()
        channels = config["application_channels"]
        
        if str(member.id) in channels:
            channel = member.guild.get_channel(channels[str(member.id)])
            if channel:
                archive_category = member.guild.get_channel(config["archive_category"])
                if archive_category:
                    await channel.edit(category=archive_category)

class ApplicationModal(discord.ui.Modal, title="Application Form"):
    def __init__(self):
        super().__init__()
        
        self.age = discord.ui.TextInput(
            label="Age",
            placeholder="Enter your age",
            required=True,
            min_length=1,
            max_length=3
        )
        self.add_item(self.age)

        self.location = discord.ui.TextInput(
            label="Location",
            placeholder="Enter your location",
            required=True
        )
        self.add_item(self.location)

        self.steam_id = discord.ui.TextInput(
            label="Steam ID",
            placeholder="Enter your Steam ID",
            required=True
        )
        self.add_item(self.steam_id)

    async def on_submit(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="New Application Submitted",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        
        embed.add_field(name="User", value=interaction.user.mention, inline=False)
        embed.add_field(name="Age", value=self.age.value, inline=True)
        embed.add_field(name="Location", value=self.location.value, inline=True)
        embed.add_field(name="Steam ID", value=self.steam_id.value, inline=True)

        # Get config for moderator role
        config = await interaction.client.get_cog("DisApps").config.guild(interaction.guild).all()
        mod_role = interaction.guild.get_role(config["moderator_role"])
        
        if mod_role:
            online_mods = [member for member in mod_role.members 
                         if member.status != discord.Status.offline]
            
            if online_mods:
                ping_text = ", ".join([mod.mention for mod in online_mods])
            else:
                ping_text = mod_role.mention
                
            await interaction.response.send_message(
                f"{ping_text}\nNew application submitted:",
                embed=embed
            )
        else:
            await interaction.response.send_message(
                "Application submitted:",
                embed=embed
            )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await interaction.response.send_message(
            "An error occurred while processing your application. Please try again later.",
            ephemeral=True
        )

async def setup(bot):
    await bot.add_cog(DisApps(bot))
