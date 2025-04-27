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
            "setup_complete": False,
            "applications_category": None,
            "archive_category": None,
            "moderator_role": None,
            "game_roles": {},
            "application_channels": {},
            "webhook_url": None
        }
        self.config.register_guild(**default_guild)
        self.check_rejoin_task = self.bot.loop.create_task(self.check_rejoins())

    def cog_unload(self):
        if self.check_rejoin_task:
            self.check_rejoin_task.cancel()

    async def check_rejoins(self):
        while True:
            try:
                await asyncio.sleep(3600)  # Check every hour
                for guild in self.bot.guilds:
                    async with self.config.guild(guild).application_channels() as channels:
                        for user_id, channel_id in channels.items():
                            channel = guild.get_channel(channel_id)
                            if channel:
                                member = guild.get_member(int(user_id))
                                if member:
                                    if channel.category_id != (await self.config.guild(guild).applications_category()):
                                        apps_category = guild.get_channel(await self.config.guild(guild).applications_category())
                                        await channel.edit(category=apps_category)
            except Exception as e:
                print(f"Error in check_rejoins: {e}")

    @commands.group(aliases=["da"])
    @commands.admin_or_permissions(administrator=True)
    async def disapps(self, ctx):
        """DisApps management commands"""
        pass

    @disapps.command()
    async def setup(self, ctx):
        """Initial setup for DisApps"""
        try:
            await ctx.send("Starting DisApps setup. Please answer the following questions.")

            # Applications Category
            await ctx.send("Please create an 'Applications' category and paste its ID:")
            pred = MessagePredicate.valid_int(ctx)
            await self.bot.wait_for("message", check=pred, timeout=60)
            apps_category = pred.result

            # Archive Category
            await ctx.send("Please create an 'Archive' category and paste its ID:")
            pred = MessagePredicate.valid_int(ctx)
            await self.bot.wait_for("message", check=pred, timeout=60)
            archive_category = pred.result

            # Moderator Role
            await ctx.send("Please paste the Moderator role ID:")
            pred = MessagePredicate.valid_int(ctx)
            await self.bot.wait_for("message", check=pred, timeout=60)
            mod_role = pred.result

            # Game Roles
            game_roles = {}
            await ctx.send("Enter game names and their corresponding role IDs (format: 'Game Name:role_id'). Type 'done' when finished.")
            while True:
                msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=60)
                if msg.content.lower() == "done":
                    break
                try:
                    game, role_id = msg.content.split(":", 1)
                    game_roles[game.strip()] = int(role_id.strip())
                except ValueError:
                    await ctx.send("Invalid format. Please use 'Game Name:role_id'")

            # Save Configuration
            guild = ctx.guild
            async with self.config.guild(guild).all() as settings:
                settings["setup_complete"] = True
                settings["applications_category"] = apps_category
                settings["archive_category"] = archive_category
                settings["moderator_role"] = mod_role
                settings["game_roles"] = game_roles

            await ctx.send("Setup complete! The system is now ready to use.")

        except asyncio.TimeoutError:
            await ctx.send("Setup timed out. Please try again.")
        except Exception as e:
            await ctx.send(f"An error occurred during setup: {e}")

    @disapps.command()
    async def test(self, ctx):
        """Test the application system"""
        try:
            if not await self.config.guild(ctx.guild).setup_complete():
                await ctx.send("Please run setup first using !disapps setup")
                return

            # Create test channel
            category = ctx.guild.get_channel(await self.config.guild(ctx.guild).applications_category())
            channel = await ctx.guild.create_text_channel(
                f"test-application",
                category=category,
                overwrites={
                    ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    ctx.author: discord.PermissionOverwrite(read_messages=True),
                    ctx.guild.get_role(await self.config.guild(ctx.guild).moderator_role()): 
                        discord.PermissionOverwrite(read_messages=True)
                }
            )

            # Create embed
            embed = discord.Embed(
                title="Welcome to Zero Lives Left",
                description="Test application system",
                color=discord.Color.blue()
            )

            # Create buttons
            class ApplicationButtons(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=None)

                @discord.ui.button(label="Apply Now", style=discord.ButtonStyle.green)
                async def apply_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
                    await interaction.response.send_message("Test application form would appear here.")
                    button.disabled = True
                    await interaction.message.edit(view=self)

                @discord.ui.button(label="Contact Mod", style=discord.ButtonStyle.red)
                async def contact_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
                    await interaction.response.send_message("Test mod contact system.")

            await channel.send(f"{ctx.author.mention}", embed=embed, view=ApplicationButtons())
            await ctx.send(f"Test channel created: {channel.mention}")

        except Exception as e:
            await ctx.send(f"An error occurred during testing: {e}")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Handle new member joins"""
        try:
            guild = member.guild
            if not await self.config.guild(guild).setup_complete():
                return

            # Create application channel
            category = guild.get_channel(await self.config.guild(guild).applications_category())
            channel = await guild.create_text_channel(
                f"{member.name.lower()}-application",
                category=category,
                overwrites={
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    member: discord.PermissionOverwrite(read_messages=True),
                    guild.get_role(await self.config.guild(guild).moderator_role()): 
                        discord.PermissionOverwrite(read_messages=True)
                }
            )

            # Store channel information
            async with self.config.guild(guild).application_channels() as channels:
                channels[str(member.id)] = channel.id

            # Create embed and buttons
            embed = discord.Embed(
                title="Welcome to Zero Lives Left",
                description="Your application process starts here",
                color=discord.Color.blue()
            )

            class ApplicationButtons(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=None)

                @discord.ui.button(label="Apply Now", style=discord.ButtonStyle.green)
                async def apply_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
                    # Create application form
                    await interaction.response.send_modal(ApplicationForm())
                    button.disabled = True
                    await interaction.message.edit(view=self)

                @discord.ui.button(label="Contact Mod", style=discord.ButtonStyle.red)
                async def contact_callback(self, interaction: discord.Interaction, button: discord.ui.Button):
                    mod_role = interaction.guild.get_role(await self.config.guild(interaction.guild).moderator_role())
                    online_mods = [m for m in mod_role.members if m.status != discord.Status.offline]
                    if online_mods:
                        await interaction.response.send_message(f"{' '.join([m.mention for m in online_mods])} - Help requested!")
                    else:
                        await interaction.response.send_message(f"{mod_role.mention} - Help requested! (No mods currently online)")

            await channel.send(f"{member.mention}", embed=embed, view=ApplicationButtons())

        except Exception as e:
            print(f"Error in on_member_join: {e}")

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Handle member leaves"""
        try:
            guild = member.guild
            async with self.config.guild(guild).application_channels() as channels:
                if str(member.id) in channels:
                    channel = guild.get_channel(channels[str(member.id)])
                    if channel:
                        archive_category = guild.get_channel(await self.config.guild(guild).archive_category())
                        await channel.edit(category=archive_category)

        except Exception as e:
            print(f"Error in on_member_remove: {e}")

class ApplicationForm(discord.ui.Modal, title="Application Form"):
    def __init__(self):
        super().__init__()
        self.add_item(discord.ui.TextInput(label="Age", placeholder="Enter your age"))
        self.add_item(discord.ui.TextInput(label="Location", placeholder="Enter your location"))
        self.add_item(discord.ui.TextInput(label="Steam ID", placeholder="Enter your Steam ID"))
        self.add_item(discord.ui.TextInput(label="Games", placeholder="List games you play"))

    async def on_submit(self, interaction: discord.Interaction):
        mod_role = interaction.guild.get_role(await self.bot.get_cog("DisApps").config.guild(interaction.guild).moderator_role())
        online_mods = [m for m in mod_role.members if m.status != discord.Status.offline]
        
        embed = discord.Embed(
            title="New Application Submitted",
            description=f"From: {interaction.user.mention}",
            color=discord.Color.green()
        )
        embed.add_field(name="Age", value=self.children[0].value)
        embed.add_field(name="Location", value=self.children[1].value)
        embed.add_field(name="Steam ID", value=self.children[2].value)
        embed.add_field(name="Games", value=self.children[3].value)

        await interaction.response.send_message(embed=embed)
        if online_mods:
            await interaction.followup.send(f"{' '.join([m.mention for m in online_mods])} - New application submitted!")
        else:
            await interaction.followup.send(f"{mod_role.mention} - New application submitted! (No mods currently online)")

async def setup(bot):
    await bot.add_cog(DisApps(bot))
