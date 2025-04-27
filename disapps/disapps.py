import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import json
import os
from typing import Optional
from datetime import datetime

class DisApps(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = {}
        self.load_config()
        self.check_rejoins.start()
        
    def load_config(self):
        try:
            with open('disapps_config.json', 'r') as f:
                self.config = json.load(f)
        except FileNotFoundError:
            self.config = {
                'guild_id': None,
                'mod_role_id': None,
                'apps_category_id': None,
                'archive_category_id': None,
                'game_roles': {}
            }
            self.save_config()

    def save_config(self):
        with open('disapps_config.json', 'w') as f:
            json.dump(self.config, f, indent=4)

    @commands.group(aliases=['da'])
    @commands.has_permissions(administrator=True)
    async def disapps(self, ctx):
        if ctx.invoked_subcommand is None:
            await ctx.send("Available commands: setup, test")

    @disapps.command()
    async def setup(self, ctx):
        """Run through the initial setup process"""
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        await ctx.send("Starting setup process. Please mention the Moderator role:")
        try:
            msg = await self.bot.wait_for('message', check=check, timeout=60)
            mod_role = msg.role_mentions[0] if msg.role_mentions else None
            if not mod_role:
                return await ctx.send("No role mentioned. Setup cancelled.")

            await ctx.send("Please create or mention the Applications category:")
            msg = await self.bot.wait_for('message', check=check, timeout=60)
            apps_category = msg.channel_mentions[0].category if msg.channel_mentions else None
            if not apps_category:
                return await ctx.send("No category mentioned. Setup cancelled.")

            await ctx.send("Please create or mention the Archive category:")
            msg = await self.bot.wait_for('message', check=check, timeout=60)
            archive_category = msg.channel_mentions[0].category if msg.channel_mentions else None
            if not archive_category:
                return await ctx.send("No category mentioned. Setup cancelled.")

            await ctx.send("Enter game names and mention their corresponding roles (one per message, type 'done' when finished):")
            game_roles = {}
            while True:
                msg = await self.bot.wait_for('message', check=check, timeout=60)
                if msg.content.lower() == 'done':
                    break
                if msg.role_mentions:
                    game_name = msg.content.split(' ')[0]
                    game_roles[game_name] = msg.role_mentions[0].id

            self.config = {
                'guild_id': ctx.guild.id,
                'mod_role_id': mod_role.id,
                'apps_category_id': apps_category.id,
                'archive_category_id': archive_category.id,
                'game_roles': game_roles
            }
            self.save_config()
            await ctx.send("Setup completed successfully!")

        except asyncio.TimeoutError:
            await ctx.send("Setup timed out. Please try again.")

    @disapps.command()
    async def test(self, ctx):
        """Test the application system"""
        await self.create_application_channel(ctx.author)
        await ctx.send("Test application channel created!")

    async def create_application_channel(self, member):
        guild = self.bot.get_guild(self.config['guild_id'])
        if not guild:
            return

        category = guild.get_channel(self.config['apps_category_id'])
        if not category:
            return

        channel_name = f"{member.name.lower()}-application"
        
        # Check if channel exists in archive
        archive_category = guild.get_channel(self.config['archive_category_id'])
        existing_channel = discord.utils.get(archive_category.channels, name=channel_name)
        
        if existing_channel:
            await existing_channel.edit(category=category)
            channel = existing_channel
        else:
            # Create new channel
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                member: discord.PermissionOverwrite(read_messages=True),
                guild.get_role(self.config['mod_role_id']): discord.PermissionOverwrite(read_messages=True)
            }
            
            channel = await category.create_text_channel(
                name=channel_name,
                overwrites=overwrites
            )

        embed = discord.Embed(
            title="Welcome to Zero Lives Left!",
            description="[Your server description here]",
            color=discord.Color.blue()
        )

        class ApplicationButtons(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=None)

            @discord.ui.button(label="Apply Now", style=discord.ButtonStyle.green)
            async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                await self.show_application_form(interaction)
                button.disabled = True
                await interaction.message.edit(view=self)

            @discord.ui.button(label="Contact Mod", style=discord.ButtonStyle.red)
            async def contact_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                mod_role = interaction.guild.get_role(self.config['mod_role_id'])
                online_mods = [m for m in mod_role.members if m.status != discord.Status.offline]
                
                if online_mods:
                    await interaction.response.send_message(
                        f"Online moderators: {', '.join([m.mention for m in online_mods])}"
                    )
                else:
                    await interaction.response.send_message(f"{mod_role.mention} No moderators are currently online.")

            async def show_application_form(self, interaction):
                class ApplicationForm(discord.ui.Modal, title="Application Form"):
                    age = discord.ui.TextInput(label="Age")
                    location = discord.ui.TextInput(label="Location")
                    steam_id = discord.ui.TextInput(label="Steam ID")
                    
                    async def on_submit(self, interaction: discord.Interaction):
                        embed = discord.Embed(
                            title="New Application",
                            description=f"From: {interaction.user.mention}\n"
                                      f"Age: {self.age.value}\n"
                                      f"Location: {self.location.value}\n"
                                      f"Steam ID: {self.steam_id.value}",
                            color=discord.Color.green()
                        )
                        
                        mod_role = interaction.guild.get_role(self.config['mod_role_id'])
                        online_mods = [m for m in mod_role.members if m.status != discord.Status.offline]
                        
                        if online_mods:
                            ping_str = ", ".join([m.mention for m in online_mods])
                        else:
                            ping_str = mod_role.mention
                            
                        await interaction.response.send_message(
                            f"{ping_str} New application submitted!",
                            embed=embed
                        )

                await interaction.response.send_modal(ApplicationForm())

        await channel.send(f"{member.mention}", embed=embed, view=ApplicationButtons())

    @tasks.loop(hours=1)
    async def check_rejoins(self):
        """Check for users who have rejoined the server"""
        guild = self.bot.get_guild(self.config['guild_id'])
        if not guild:
            return

        archive_category = guild.get_channel(self.config['archive_category_id'])
        apps_category = guild.get_channel(self.config['apps_category_id'])
        
        for channel in archive_category.channels:
            if channel.name.endswith('-application'):
                username = channel.name.replace('-application', '')
                member = discord.utils.get(guild.members, name=username)
                
                if member:
                    await channel.edit(category=apps_category)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        await self.create_application_channel(member)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        guild = member.guild
        if guild.id != self.config['guild_id']:
            return

        apps_category = guild.get_channel(self.config['apps_category_id'])
        archive_category = guild.get_channel(self.config['archive_category_id'])
        
        channel_name = f"{member.name.lower()}-application"
        channel = discord.utils.get(apps_category.channels, name=channel_name)
        
        if channel:
            await channel.edit(category=archive_category)

async def setup(bot):
    await bot.add_cog(DisApps(bot))
