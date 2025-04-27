import discord
from redbot.core import commands, Config
from redbot.core.utils.predicates import MessagePredicate
from redbot.core.utils.menus import start_adding_reactions
from datetime import datetime
from typing import Union, Optional
import asyncio

class Applications(commands.Cog):
    """Discord Member Applications Manager"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "mod_role": None,
            "accepted_role": None,
            "assignable_roles": [],
            "applications_category": None,
            "setup_complete": False
        }
        self.config.register_guild(**default_guild)

    @commands.group(aliases=["da"])
    @commands.admin_or_permissions(administrator=True)
    async def disapps(self, ctx):
        """Applications management commands"""
        pass

    @disapps.command()
    async def setup(self, ctx):
        """Setup the applications system"""
        
        # Check if setup is already complete
        if await self.config.guild(ctx.guild).setup_complete():
            return await ctx.send("Setup is already complete. Use `!disapps reset` to start over.")

        await ctx.send("Starting setup process...")

        # Get Moderator Role
        await ctx.send("Please mention the Moderator role or provide its ID:")
        try:
            msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                timeout=30.0
            )
            try:
                mod_role = await commands.RoleConverter().convert(ctx, msg.content)
                await self.config.guild(ctx.guild).mod_role.set(mod_role.id)
            except:
                return await ctx.send("Invalid role. Setup cancelled.")
        except asyncio.TimeoutError:
            return await ctx.send("Setup timed out.")

        # Get Accepted Role
        await ctx.send("Please mention the role for accepted applicants or provide its ID:")
        try:
            msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                timeout=30.0
            )
            try:
                accepted_role = await commands.RoleConverter().convert(ctx, msg.content)
                await self.config.guild(ctx.guild).accepted_role.set(accepted_role.id)
            except:
                return await ctx.send("Invalid role. Setup cancelled.")
        except asyncio.TimeoutError:
            return await ctx.send("Setup timed out.")

        # Get Applications Category
        await ctx.send("Please mention the Applications category or provide its ID:")
        try:
            msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                timeout=30.0
            )
            try:
                category = await commands.CategoryChannelConverter().convert(ctx, msg.content)
                await self.config.guild(ctx.guild).applications_category.set(category.id)
            except:
                return await ctx.send("Invalid category. Setup cancelled.")
        except asyncio.TimeoutError:
            return await ctx.send("Setup timed out.")

        await self.config.guild(ctx.guild).setup_complete.set(True)
        await ctx.send("Setup complete!")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Create application channel when a new member joins"""
        guild = member.guild
        if not await self.config.guild(guild).setup_complete():
            return

        category_id = await self.config.guild(guild).applications_category()
        category = discord.utils.get(guild.categories, id=category_id)
        
        if not category:
            return

        channel_name = f"{member.name}-application"
        channel = await category.create_text_channel(
            name=channel_name,
            overwrites={
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
        )

        embed = discord.Embed(
            title="Welcome to the Application Process!",
            description="Please click the buttons below to begin.",
            color=discord.Color.blue()
        )

        class ApplicationButtons(discord.ui.View):
            def __init__(self, cog):
                super().__init__(timeout=None)
                self.cog = cog

            @discord.ui.button(label="Apply Now", style=discord.ButtonStyle.green)
            async def apply_now(self, interaction: discord.Interaction, button: discord.ui.Button):
                modal = ApplicationModal()
                await interaction.response.send_modal(modal)
                button.disabled = True
                await interaction.message.edit(view=self)

            @discord.ui.button(label="Contact Mod", style=discord.ButtonStyle.red)
            async def contact_mod(self, interaction: discord.Interaction, button: discord.ui.Button):
                mod_role_id = await self.cog.config.guild(interaction.guild).mod_role()
                mod_role = interaction.guild.get_role(mod_role_id)
                await interaction.response.send_message(f"{mod_role.mention} - Help requested by {interaction.user.mention}")
                button.disabled = True
                await interaction.message.edit(view=self)

        class ApplicationModal(discord.ui.Modal, title="Application Form"):
            age = discord.ui.TextInput(
                label="Age",
                required=True
            )
            location = discord.ui.TextInput(
                label="Location",
                required=True
            )
            username = discord.ui.TextInput(
                label="Gaming Platform Username",
                required=True
            )

            async def on_submit(self, interaction: discord.Interaction):
                embed = discord.Embed(
                    title="Application Submitted",
                    description=f"Age: {self.age}\nLocation: {self.location}\nUsername: {self.username}",
                    color=discord.Color.green()
                )
                await interaction.response.send_message(embed=embed)

        await channel.send(content=member.mention, embed=embed, view=ApplicationButtons(self))

    @disapps.command()
    async def test(self, ctx):
        """Test the application system with a fake member"""
        class FakeMember:
            def __init__(self, guild):
                self.guild = guild
                self.name = "test-user"
                self.mention = "@test-user"

        fake_member = FakeMember(ctx.guild)
        await self.on_member_join(fake_member)
        await ctx.send("Test application channel created!")

def setup(bot):
    bot.add_cog(Applications(bot))
