import discord
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
                user_id = int(channel.name.split("-")[0])
                member = guild.get_member(user_id)
                if member:
                    await channel.edit(category=apps_category)

    @commands.group(aliases=["da"])
    @commands.admin_or_permissions(administrator=True)
    async def disapps(self, ctx):
        """DisApps management commands"""
        pass

    @disapps.command()
    async def setup(self, ctx):
        """Initial setup for DisApps"""
        guild = ctx.guild
        
        await ctx.send("Starting setup process. Please mention the Moderator role:")
        try:
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=30)
            mod_role = await commands.RoleConverter().convert(ctx, msg.content)
        except:
            return await ctx.send("Setup failed: Invalid moderator role")

        await ctx.send("Please enter the Applications category ID:")
        try:
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=30)
            apps_category = await commands.CategoryChannelConverter().convert(ctx, msg.content)
        except:
            return await ctx.send("Setup failed: Invalid category ID")

        await ctx.send("Please enter the Archive category ID:")
        try:
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=30)
            archive_category = await commands.CategoryChannelConverter().convert(ctx, msg.content)
        except:
            return await ctx.send("Setup failed: Invalid category ID")

        await ctx.send("Enter game roles in format 'Game Name: @role' (one per message, type 'done' when finished):")
        game_roles = {}
        while True:
            try:
                msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=30)
                if msg.content.lower() == "done":
                    break
                game_name, role_mention = msg.content.split(":", 1)
                role = await commands.RoleConverter().convert(ctx, role_mention.strip())
                game_roles[game_name.strip()] = role.id
            except:
                await ctx.send("Invalid format, try again or type 'done'")

        await self.config.guild(guild).mod_role.set(mod_role.id)
        await self.config.guild(guild).apps_category.set(apps_category.id)
        await self.config.guild(guild).archive_category.set(archive_category.id)
        await self.config.guild(guild).game_roles.set(game_roles)
        await self.config.guild(guild).setup_complete.set(True)

        await ctx.send("Setup complete!")

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
