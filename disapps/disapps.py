import discord
from redbot.core import commands, Config, checks
from redbot.core.utils.predicates import MessagePredicate
from redbot.core.utils.menus import start_adding_reactions
from datetime import datetime
import asyncio

class DeclineModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Decline Application")
        self.add_item(
            discord.ui.TextInput(
                label="Reason for Declining",
                style=discord.TextStyle.paragraph,
                placeholder="Please provide a detailed reason for declining this application...",
                required=True,
                max_length=1000
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.decline_reason = self.children[0].value

class ModButtons(discord.ui.View):
    def __init__(self, cog, applicant):
        super().__init__(timeout=None)
        self.cog = cog
        self.applicant = applicant
        self.accept_button.disabled = False
        self.decline_button.disabled = False

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("You don't have permission to use this button.", ephemeral=True)
            return

        try:
            # Disable both buttons
            self.accept_button.disabled = True
            self.decline_button.disabled = True
            await interaction.message.edit(view=self)

            # Add the accepted role
            role_id = await self.cog.config.guild(interaction.guild).accepted_role()
            role = interaction.guild.get_role(role_id)
            await self.applicant.add_roles(role)

            # Send confirmation messages
            await interaction.response.send_message(
                f"Application accepted! {self.applicant.mention} has been given the {role.name} role."
            )
            
            try:
                await self.applicant.send(f"Congratulations! Your application to {interaction.guild.name} has been accepted!")
            except discord.Forbidden:
                await interaction.followup.send("Could not DM the user, but their application has been accepted.")

            # Optional: Close or archive the channel after a delay
            await asyncio.sleep(300)  # 5 minute delay
            await interaction.channel.send("This channel will be archived in 1 minute...")
            await asyncio.sleep(60)
            await interaction.channel.edit(archived=True)

        except discord.Forbidden:
            await interaction.response.send_message("I don't have permission to manage roles!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"An error occurred: {str(e)}", ephemeral=True)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red)
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.user.guild_permissions.manage_roles:
            await interaction.response.send_message("You don't have permission to use this button.", ephemeral=True)
            return

        # Create and send the decline modal
        modal = DeclineModal()
        await interaction.response.send_modal(modal)
        
        try:
            await modal.wait()  # Wait for the modal to be submitted
            
            # Disable both buttons
            self.accept_button.disabled = True
            self.decline_button.disabled = True
            await interaction.message.edit(view=self)

            # Send decline message to the applicant
            try:
                await self.applicant.send(
                    f"Your application to {interaction.guild.name} has been declined.\n"
                    f"Reason: {modal.decline_reason}"
                )
                await interaction.channel.send(
                    f"Application declined. A DM has been sent to {self.applicant.mention} with the reason."
                )
            except discord.Forbidden:
                await interaction.channel.send(
                    f"Could not DM the user, but the application has been declined.\n"
                    f"Reason: {modal.decline_reason}"
                )

            # Optional: Close or archive the channel after a delay
            await asyncio.sleep(300)  # 5 minute delay
            await interaction.channel.send("This channel will be archived in 1 minute...")
            await asyncio.sleep(60)
            await interaction.channel.edit(archived=True)

        except asyncio.TimeoutError:
            await interaction.followup.send("The decline action has timed out.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"An error occurred: {str(e)}", ephemeral=True)

class ApplicationModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Application Form")
        self.add_item(
            discord.ui.TextInput(
                label="Age",
                placeholder="Enter your age (13-99)",
                min_length=2,
                max_length=2,
                required=True
            )
        )
        self.add_item(
            discord.ui.TextInput(
                label="Location",
                placeholder="Enter your location",
                max_length=50,
                required=True
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        # Validate age
        try:
            age = int(self.children[0].value)
            if age < 13 or age > 99:
                await interaction.response.send_message("Age must be between 13 and 99.", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("Age must be a number.", ephemeral=True)
            return

        # Validate location (text only)
        location = self.children[1].value
        if not location.replace(" ", "").isalpha():
            await interaction.response.send_message("Location must contain only letters and spaces.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Application Submitted",
            description="A moderator will review your application shortly.",
            color=discord.Color.green()
        )
        embed.add_field(name="Age", value=age)
        embed.add_field(name="Location", value=location)
        embed.set_footer(text=f"Submitted by {interaction.user}")
        
        await interaction.response.send_message(embed=embed)

        # Add moderator buttons with the applicant parameter
        mod_view = ModButtons(interaction.client.get_cog("DisApps"), interaction.user)
        await interaction.channel.send(
            "Moderator Controls (buttons will be disabled after use):",
            view=mod_view
        )


class ApplicationButtons(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
        self.contact_mod_used = False

    @discord.ui.button(label="Apply Now", style=discord.ButtonStyle.green)
    async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ApplicationModal()
        await interaction.response.send_modal(modal)
        button.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Contact Mod", style=discord.ButtonStyle.red)
    async def contact_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.contact_mod_used:
            await interaction.response.send_message("This button has already been used.", ephemeral=True)
            return

        mod_role_id = await self.cog.config.guild(interaction.guild).mod_role()
        mod_role = interaction.guild.get_role(mod_role_id)
        
        # Check for online moderators
        online_mods = [member for member in interaction.guild.members 
                      if mod_role in member.roles and member.status != discord.Status.offline]
        
        if online_mods:
            mentions = " ".join([mod.mention for mod in online_mods])
            await interaction.response.send_message(f"Online moderators: {mentions}")
        else:
            await interaction.response.send_message(f"{mod_role.mention} - No moderators are currently online.")

        self.contact_mod_used = True
        button.disabled = True
        await interaction.message.edit(view=self)

class DisApps(commands.Cog):
    """Discord Applications Management System"""

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
    @checks.admin_or_permissions(administrator=True)
    async def disapps(self, ctx):
        """Discord Applications Management Commands"""
        pass

    @disapps.command()
    async def setup(self, ctx):
        """Setup the applications system"""
        guild = ctx.guild
        
        # Reset config
        await self.config.guild(guild).clear()
        
        await ctx.send("Starting setup process. Please mention the Moderator role or provide its ID:")
        try:
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=30.0)
            try:
                mod_role = await commands.RoleConverter().convert(ctx, msg.content)
                await self.config.guild(guild).mod_role.set(mod_role.id)
            except:
                await ctx.send("Invalid role. Setup cancelled.")
                return
            
            await ctx.send("Please mention the role for accepted applicants or provide its ID:")
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=30.0)
            try:
                accepted_role = await commands.RoleConverter().convert(ctx, msg.content)
                await self.config.guild(guild).accepted_role.set(accepted_role.id)
            except:
                await ctx.send("Invalid role. Setup cancelled.")
                return
            
            await ctx.send("Please mention the Applications category or provide its ID:")
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=30.0)
            try:
                category = await commands.CategoryChannelConverter().convert(ctx, msg.content)
                await self.config.guild(guild).applications_category.set(category.id)
            except:
                await ctx.send("Invalid category. Setup cancelled.")
                return
            
            await self.config.guild(guild).setup_complete.set(True)
            await ctx.send("Setup complete!")
            
        except asyncio.TimeoutError:
            await ctx.send("Setup timed out.")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild = member.guild
        if not await self.config.guild(guild).setup_complete():
            return
            
        category_id = await self.config.guild(guild).applications_category()
        category = guild.get_channel(category_id)
        
        # Get the moderator role
        mod_role_id = await self.config.guild(guild).mod_role()
        mod_role = guild.get_role(mod_role_id)
        
        channel_name = f"{member.name}-application"
        channel = await category.create_text_channel(
            name=channel_name,
            overwrites={
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                mod_role: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_messages=True)
            }
        )

        embed = discord.Embed(
            title="Welcome to the Application Process!",
            description="Please click the buttons below to begin.",
            color=discord.Color.blue()
        )

        await channel.send(content=member.mention, embed=embed, view=ApplicationButtons(self))

    @disapps.command()
    async def test(self, ctx):
        """Test the application system with a fake member join"""
        await self.on_member_join(ctx.author)
        await ctx.send("Test application created!")

def setup(bot):
    bot.add_cog(DisApps(bot))
