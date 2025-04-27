from redbot.core import commands, Config
import discord
from discord.ui import Button, View, Modal, TextInput, Select
from typing import List
import asyncio

class GameSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Minecraft", value="minecraft"),
            discord.SelectOption(label="League of Legends", value="lol"),
            discord.SelectOption(label="Valorant", value="valorant"),
            discord.SelectOption(label="CS:GO", value="csgo"),
            discord.SelectOption(label="Fortnite", value="fortnite"),
            discord.SelectOption(label="Other", value="other")
        ]
        super().__init__(
            placeholder="Select the games you play...",
            min_values=1,
            max_values=len(options),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"Games selected: {', '.join(self.values)}",
            ephemeral=True
        )

class GameSelectView(View):
    def __init__(self):
        super().__init__()
        self.add_item(GameSelect())

class ApplicationModal(Modal):
    def __init__(self):
        super().__init__(title="Community Application")
        
        # Basic Information
        self.age = TextInput(
            label="Age",
            placeholder="Please enter your age",
            required=True,
            min_length=1,
            max_length=3
        )
        
        self.location = TextInput(
            label="Location",
            placeholder="Please enter your country/region",
            required=True,
            min_length=2,
            max_length=100
        )

        self.discord_experience = TextInput(
            label="Discord Experience",
            placeholder="How long have you been using Discord?",
            required=True,
            min_length=1,
            max_length=100
        )

        self.about_me = TextInput(
            label="About Me",
            placeholder="Tell us a bit about yourself...",
            required=True,
            min_length=10,
            max_length=1000,
            style=discord.TextStyle.paragraph
        )

        self.availability = TextInput(
            label="Availability",
            placeholder="When are you usually online?",
            required=True,
            min_length=5,
            max_length=200
        )

        self.add_item(self.age)
        self.add_item(self.location)
        self.add_item(self.discord_experience)
        self.add_item(self.about_me)
        self.add_item(self.availability)

    async def callback(self, interaction: discord.Interaction):
        # Create an embed with the application information
        embed = discord.Embed(
            title="Application Submission",
            color=discord.Color.green()
        )
        embed.add_field(name="Age", value=self.age.value, inline=True)
        embed.add_field(name="Location", value=self.location.value, inline=True)
        embed.add_field(name="Discord Experience", value=self.discord_experience.value, inline=False)
        embed.add_field(name="About Me", value=self.about_me.value, inline=False)
        embed.add_field(name="Availability", value=self.availability.value, inline=False)

        # Send the initial response
        await interaction.response.send_message(
            "Thanks for submitting your application! Please select the games you play below:",
            embed=embed,
            view=GameSelectView(),
            ephemeral=True
        )

class ApplicationButtons(View):
    def __init__(self, moderator_role_id: int):
        super().__init__(timeout=None)
        self.moderator_role_id = moderator_role_id

    @discord.ui.button(label="Apply Now", style=discord.ButtonStyle.green)
    async def apply_button(self, interaction: discord.Interaction, button: Button):
        modal = ApplicationModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Contact Moderator", style=discord.ButtonStyle.red)
    async def contact_mod_button(self, interaction: discord.Interaction, button: Button):
        moderator_role = interaction.guild.get_role(self.moderator_role_id)
        await interaction.response.send_message(
            f"{moderator_role.mention} - User needs assistance in {interaction.channel.mention}!",
            allowed_mentions=discord.AllowedMentions(roles=True)
        )

class Applications(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "category_id": None,
            "moderator_role_id": None,
            "applications_channel_id": None
        }
        self.config.register_guild(**default_guild)

    @commands.group()
    @commands.admin_or_permissions(administrator=True)
    async def appset(self, ctx):
        """Application settings"""
        pass

    @appset.command()
    async def category(self, ctx, category: discord.CategoryChannel):
        """Set the category for application channels"""
        await self.config.guild(ctx.guild).category_id.set(category.id)
        await ctx.send(f"Application category set to {category.name}")

    @appset.command()
    async def modrole(self, ctx, role: discord.Role):
        """Set the moderator role for applications"""
        await self.config.guild(ctx.guild).moderator_role_id.set(role.id)
        await ctx.send(f"Moderator role set to {role.name}")

    @appset.command()
    async def appchannel(self, ctx, channel: discord.TextChannel):
        """Set the channel where completed applications will be sent"""
        await self.config.guild(ctx.guild).applications_channel_id.set(channel.id)
        await ctx.send(f"Applications will be sent to {channel.mention}")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild = member.guild
        category_id = await self.config.guild(guild).category_id()
        moderator_role_id = await self.config.guild(guild).moderator_role_id()

        if not category_id:
            return

        category = guild.get_channel(category_id)
        if not category:
            return

        # Create channel
        channel_name = f"{member.name.lower()}-application"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        # Add moderator role permissions if set
        if moderator_role_id:
            mod_role = guild.get_role(moderator_role_id)
            if mod_role:
                overwrites[mod_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        channel = await category.create_text_channel(
            name=channel_name,
            overwrites=overwrites
        )

        # Create and send embed
        embed = discord.Embed(
            title="Welcome to the Community!",
            description=(
                "Thank you for joining our community! To complete your application, please:\n\n"
                "1. Click the 'Apply Now' button below\n"
                "2. Fill out the application form\n"
                "3. Select the games you play\n\n"
                "If you need help, click the 'Contact Moderator' button."
            ),
            color=discord.Color.blue()
        )

        view = ApplicationButtons(moderator_role_id)
        await channel.send(
            content=f"{member.mention}",
            embed=embed,
            view=view
        )

async def setup(bot):
    await bot.add_cog(Applications(bot))
