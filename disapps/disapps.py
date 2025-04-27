import discord
from discord.ext import tasks
from redbot.core import commands, Config
from redbot.core.utils.predicates import MessagePredicate
from redbot.core.utils.chat_formatting import box
from typing import Optional
import asyncio
from datetime import datetime

class DisApps(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "mod_role": None,
            "apps_category": None,
            "archive_category": None,
            "game_roles": {},
            "setup_complete": False
        }
        self.config.register_guild(**default_guild)
        self.check_channels.start()

    def cog_unload(self):
        self.check_channels.cancel()

    @tasks.loop(hours=1)
    async def check_channels(self):
        for guild in self.bot.guilds:
            config = await self.config.guild(guild).all()
            if not config["setup_complete"]:
                continue

            apps_category = guild.get_channel(config["apps_category"])
            archive_category = guild.get_channel(config["archive_category"])

            if not apps_category or not archive_category:
                continue

            for channel in archive_category.channels:
                if "-application" not in channel.name:
                    continue
                try:
                    user_id = int(channel.name.split("-")[0])
                    member = guild.get_member(user_id)
                    if member:
                        await channel.edit(category=apps_category)
                except ValueError:
                    continue

    @commands.group(aliases=["da"])
    @commands.admin_or_permissions(administrator=True)
    async def disapps(self, ctx):
        """DisApps management commands"""
        pass

    @disapps.command()
    async def setup(self, ctx):
        """Initial setup for DisApps"""
        guild = ctx.guild
        
        await ctx.send("Starting setup process. Please mention the Moderator role or provide its ID:")
        try:
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=30)
            try:
                # First try to get role from mention or ID
                if msg.role_mentions:
                    mod_role = msg.role_mentions[0]
                else:
                    role_id = int(msg.content.strip())
                    mod_role = guild.get_role(role_id)
                    
                if not mod_role:
                    return await ctx.send("Setup failed: Could not find that role")
            except ValueError:
                return await ctx.send("Setup failed: Please provide a valid role mention or ID")
        except asyncio.TimeoutError:
            return await ctx.send("Setup timed out")

        await ctx.send("Please enter the Applications category ID:")
        try:
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=30)
            try:
                category_id = int(msg.content.strip())
                apps_category = guild.get_channel(category_id)
                if not apps_category or not isinstance(apps_category, discord.CategoryChannel):
                    return await ctx.send("Setup failed: Invalid category ID")
            except ValueError:
                return await ctx.send("Setup failed: Please provide a valid category ID")
        except asyncio.TimeoutError:
            return await ctx.send("Setup timed out")

        await ctx.send("Please enter the Archive category ID:")
        try:
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=30)
            try:
                category_id = int(msg.content.strip())
                archive_category = guild.get_channel(category_id)
                if not archive_category or not isinstance(archive_category, discord.CategoryChannel):
                    return await ctx.send("Setup failed: Invalid category ID")
            except ValueError:
                return await ctx.send("Setup failed: Please provide a valid category ID")
        except asyncio.TimeoutError:
            return await ctx.send("Setup timed out")

        await ctx.send("Enter game roles in format 'Game Name: @role' or 'Game Name: role_id' (one per message, type 'done' when finished):")
        game_roles = {}
        while True:
            try:
                msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=30)
                if msg.content.lower() == "done":
                    if not game_roles:
                        return await ctx.send("Setup failed: No game roles were added")
                    break
                    
                try:
                    game_name, role_input = msg.content.split(":", 1)
                    game_name = game_name.strip()
                    role_input = role_input.strip()
                    
                    # Try to get role from mention first
                    if msg.role_mentions:
                        role = msg.role_mentions[0]
                    else:
                        # Try to get role from ID
                        try:
                            role_id = int(role_input)
                            role = guild.get_role(role_id)
                        except ValueError:
                            await ctx.send("Invalid role format. Please use either a role mention or role ID")
                            continue

                    if not role:
                        await ctx.send("Could not find that role. Please try again or type 'done'")
                        continue
                        
                    game_roles[game_name] = role.id
                    await ctx.send(f"Added {game_name} with role {role.name}")
                    
                except ValueError:
                    await ctx.send("Invalid format. Use 'Game Name: @role' or 'Game Name: role_id'")
                    continue
                    
            except asyncio.TimeoutError:
                if not game_roles:
                    return await ctx.send("Setup timed out: No game roles were added")
                break

        await self.config.guild(guild).mod_role.set(mod_role.id)
        await self.config.guild(guild).apps_category.set(apps_category.id)
        await self.config.guild(guild).archive_category.set(archive_category.id)
        await self.config.guild(guild).game_roles.set(game_roles)
        await self.config.guild(guild).setup_complete.set(True)

        setup_embed = discord.Embed(
            title="Setup Complete",
            color=discord.Color.green(),
            description="DisApps has been configured with the following settings:"
        )
        setup_embed.add_field(name="Moderator Role", value=f"{mod_role.name} ({mod_role.id})", inline=False)
        setup_embed.add_field(name="Applications Category", value=f"{apps_category.name} ({apps_category.id})", inline=False)
        setup_embed.add_field(name="Archive Category", value=f"{archive_category.name} ({archive_category.id})", inline=False)
        
        games_text = "\n".join(f"{game}: {guild.get_role(role_id).name} ({role_id})" 
                              for game, role_id in game_roles.items())
        setup_embed.add_field(name="Game Roles", value=games_text or "None", inline=False)

        await ctx.send(embed=setup_embed)

    @disapps.command()
    async def test(self, ctx):
        """Test the application system"""
        config = await self.config.guild(ctx.guild).all()
        if not config["setup_complete"]:
            return await ctx.send("Please complete setup first using `!disapps setup`")

        category = ctx.guild.get_channel(config["apps_category"])
        if not category:
            return await ctx.send("Applications category not found")

        channel_name = f"test-application"
        overwrites = {
            ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            ctx.author: discord.PermissionOverwrite(read_messages=True),
            ctx.guild.get_role(config["mod_role"]): discord.PermissionOverwrite(read_messages=True)
        }

        channel = await category.create_text_channel(channel_name, overwrites=overwrites)
        await self.send_application_message(channel, ctx.author, config)
        await ctx.send(f"Test channel created: {channel.mention}")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild = member.guild
        config = await self.config.guild(guild).all()
        if not config["setup_complete"]:
            return

        category = guild.get_channel(config["apps_category"])
        if not category:
            return

        channel_name = f"{member.name.lower()}-application"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True),
            guild.get_role(config["mod_role"]): discord.PermissionOverwrite(read_messages=True)
        }

        channel = await category.create_text_channel(channel_name, overwrites=overwrites)
        await self.send_application_message(channel, member, config)

    async def send_application_message(self, channel, member, config):
        embed = discord.Embed(
            title="Welcome to Zero Lives Left",
            description="[Your server description here]",
            color=discord.Color.blue()
        )

        class ApplicationButtons(discord.ui.View):
            def __init__(self, cog):
                super().__init__(timeout=None)
                self.cog = cog

            @discord.ui.button(label="Apply Now", style=discord.ButtonStyle.green)
            async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                await self.cog.show_application_form(interaction, config)
                button.disabled = True
                await interaction.message.edit(view=self)

            @discord.ui.button(label="Contact Mod", style=discord.ButtonStyle.red)
            async def contact_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                await self.cog.contact_moderator(interaction, config)

        await channel.send(f"{member.mention}", embed=embed, view=ApplicationButtons(self))

    async def show_application_form(self, interaction: discord.Interaction, config):
        modal = ApplicationModal(self, config)
        await interaction.response.send_modal(modal)

    async def contact_moderator(self, interaction: discord.Interaction, config):
        guild = interaction.guild
        online_mods = [m for m in guild.members if any(r.id == config["mod_role"] for r in m.roles) and m.status != discord.Status.offline]
        
        if online_mods:
            mod_mentions = " ".join(m.mention for m in online_mods)
            await interaction.response.send_message(f"Contacting online moderators: {mod_mentions}")
        else:
            mod_role = guild.get_role(config["mod_role"])
            await interaction.response.send_message(f"No moderators are currently online. {mod_role.mention}")

class ApplicationModal(discord.ui.Modal):
    def __init__(self, cog, config):
        super().__init__(title="Application Form")
        self.cog = cog
        self.config = config

        self.add_item(discord.ui.TextInput(label="Age", placeholder="Enter your age"))
        self.add_item(discord.ui.TextInput(label="Location", placeholder="Enter your location"))
        self.add_item(discord.ui.TextInput(label="Steam ID", placeholder="Enter your Steam ID"))
        
        games_text = "\n".join(f"[ ] {game}" for game in config["game_roles"].keys())
        self.add_item(discord.ui.TextInput(
            label="Games (Check with [x])",
            style=discord.TextStyle.paragraph,
            placeholder="Check the games you play",
            default=games_text
        ))

    async def on_submit(self, interaction: discord.Interaction):
        selected_games = []
        games_input = self.children[3].value.split("\n")
        
        for game_line in games_input:
            if "[x]" in game_line.lower():
                game_name = game_line[game_line.find("]")+1:].strip()
                if game_name in self.config["game_roles"]:
                    role_id = self.config["game_roles"][game_name]
                    role = interaction.guild.get_role(role_id)
                    if role:
                        await interaction.user.add_roles(role)
                        selected_games.append(game_name)

        embed = discord.Embed(
            title="Application Submitted",
            description=f"Age: {self.children[0].value}\nLocation: {self.children[1].value}\nSteam ID: {self.children[2].value}\nGames: {', '.join(selected_games)}",
            color=discord.Color.green()
        )

        await interaction.response.send_message(embed=embed)
        await self.cog.contact_moderator(interaction, self.config)

def setup(bot):
    bot.add_cog(DisApps(bot))
