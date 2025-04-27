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

            # Update application status and history
            await self.cog.add_application_history(
                interaction.guild,
                self.applicant.id,
                "accepted"
            )

            # Send confirmation messages
            await interaction.response.send_message(
                f"Application accepted! {self.applicant.mention} has been given the {role.name} role."
            )
            
            try:
                await self.applicant.send(f"Congratulations! Your application to {interaction.guild.name} has been accepted!")
            except discord.Forbidden:
                await interaction.followup.send("Could not DM the user, but their application has been accepted.")

            # Move to archive
            await self.cog.move_to_archive(interaction.channel, interaction.guild, "Application accepted")

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

            # Update application status and history
            applications = await self.cog.config.guild(interaction.guild).applications()
            user_application = applications.get(str(self.applicant.id), {})
            
            # Check if this is a returning member being declined
            is_returning = False
            if user_application.get('status') == 'pending' and user_application.get('previously_accepted', False):
                is_returning = True

            await self.cog.add_application_history(
                interaction.guild,
                self.applicant.id,
                "declined",
                modal.decline_reason
            )

            # Send decline message to the applicant
            try:
                if is_returning:
                    await self.applicant.send(
                        f"Your application to rejoin {interaction.guild.name} has been declined.\n"
                        f"Reason: {modal.decline_reason}\n\n"
                        "As this was your second attempt, you will no longer be able to participate in server channels."
                    )
                else:
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

            # If this is a returning member being declined, remove their permissions
            if is_returning:
                try:
                    # Remove permissions from all channels
                    for channel in interaction.guild.channels:
                        if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel)):
                            await channel.set_permissions(self.applicant, read_messages=False, send_messages=False)
                    
                    await interaction.channel.send(
                        f"As this was a returning member, {self.applicant.mention}'s permissions have been removed from all channels."
                    )
                except discord.Forbidden:
                    await interaction.channel.send(
                        "Failed to remove user's permissions. Please check bot permissions."
                    )

            # Move to archive
            await self.cog.move_to_archive(interaction.channel, interaction.guild, f"Application declined: {modal.decline_reason}")

        except asyncio.TimeoutError:
            await interaction.followup.send("The decline action has timed out.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"An error occurred: {str(e)}", ephemeral=True)

class ApplicationModal(discord.ui.Modal):
    def __init__(self, original_view):
        super().__init__(title="Application Form")
        self.original_view = original_view  # Store reference to the original view
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
                # Re-enable the Apply Now button
                self.original_view.apply_button.disabled = False
                await self.original_view.message.edit(view=self.original_view)
                return
        except ValueError:
            await interaction.response.send_message("Age must be a number.", ephemeral=True)
            # Re-enable the Apply Now button
            self.original_view.apply_button.disabled = False
            await self.original_view.message.edit(view=self.original_view)
            return

        # Validate location (text only)
        location = self.children[1].value
        if not location.replace(" ", "").isalpha():
            await interaction.response.send_message("Location must contain only letters and spaces.", ephemeral=True)
            # Re-enable the Apply Now button
            self.original_view.apply_button.disabled = False
            await self.original_view.message.edit(view=self.original_view)
            return

        # Update application status
        applications = await self.original_view.cog.config.guild(interaction.guild).applications()
        if str(interaction.user.id) in applications:
            applications[str(interaction.user.id)]['status'] = 'pending'
            await self.original_view.cog.config.guild(interaction.guild).applications.set(applications)

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

        # Get moderator role and check for online moderators
        mod_role_id = await self.original_view.cog.config.guild(interaction.guild).mod_role()
        mod_role = interaction.guild.get_role(mod_role_id)
        
        # Check for online moderators
        online_mods = [member for member in interaction.guild.members 
                      if mod_role in member.roles and member.status != discord.Status.offline]
        
        # Send notification
        if online_mods:
            mentions = " ".join([mod.mention for mod in online_mods])
            await interaction.channel.send(
                f"{mentions}\nNew application submitted by {interaction.user.mention}"
            )
        else:
            await interaction.channel.send(
                f"{mod_role.mention}\nNew application submitted by {interaction.user.mention} (No moderators are currently online)"
            )

class ApplicationButtons(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
        self.contact_mod_used = False
        self.message = None  # Store the message reference

    @discord.ui.button(label="Apply Now", style=discord.ButtonStyle.green)
    async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Store the message reference when the button is first clicked
        if not self.message:
            self.message = interaction.message
        
        modal = ApplicationModal(self)  # Pass the view instance to the modal
        await interaction.response.send_modal(modal)
        button.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Contact Mod", style=discord.ButtonStyle.red)
    async def contact_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.message:
            self.message = interaction.message
            
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
            "archive_category": None,
            "setup_complete": False,
            "applications": {},
            "version": "1.0.0"
        }
        self.config.register_guild(**default_guild)
        self.bot.loop.create_task(self.initialize())

    async def initialize(self):
        """Initialize the cog and migrate data if necessary"""
        await self.bot.wait_until_ready()
        
        # Perform data migration for all guilds
        all_guilds = await self.config.all_guilds()
        for guild_id, guild_data in all_guilds.items():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            # Check version and perform migrations if needed
            version = guild_data.get("version", "0.0.0")
            if version != "1.0.0":
                await self.migrate_data(guild, version)

    async def migrate_data(self, guild, old_version):
        """Migrate data from old versions to new version"""
        async with self.config.guild(guild).all() as guild_data:
            if old_version == "0.0.0":
                # Migrate from pre-versioned data
                applications = guild_data.get("applications", {})
                for app_id, app_data in applications.items():
                    if "previously_accepted" not in app_data:
                        app_data["previously_accepted"] = False
                    if "application_history" not in app_data:
                        app_data["application_history"] = []
                        if app_data.get("status") in ["accepted", "declined"]:
                            app_data["application_history"].append({
                                "status": app_data["status"],
                                "timestamp": app_data.get("timestamp", datetime.utcnow().timestamp()),
                                "reason": app_data.get("decline_reason", "")
                            })
                guild_data["applications"] = applications
            
            # Update version
            guild_data["version"] = "1.0.0"

    async def add_application_history(self, guild, user_id, status, reason=""):
        """Add an entry to user's application history"""
        async with self.config.guild(guild).applications() as applications:
            if str(user_id) not in applications:
                applications[str(user_id)] = {
                    "status": status,
                    "timestamp": datetime.utcnow().timestamp(),
                    "previously_accepted": False,
                    "application_history": []
                }
            
            applications[str(user_id)]["application_history"].append({
                "status": status,
                "timestamp": datetime.utcnow().timestamp(),
                "reason": reason
            })
            applications[str(user_id)]["status"] = status

    def cog_unload(self):
        """Called when the cog is unloaded"""
        # Save any pending data
        for guild in self.bot.guilds:
            self.bot.loop.create_task(self.config.guild(guild).save())

    async def move_to_archive(self, channel, guild, reason=""):
        """Helper function to move channels to archive"""
        archive_id = await self.config.guild(guild).archive_category()
        archive_category = guild.get_channel(archive_id)
        
        if archive_category:
            # Get the moderator role
            mod_role_id = await self.config.guild(guild).mod_role()
            mod_role = guild.get_role(mod_role_id)
            
            # Find the user overwrite (there should only be one non-role member)
            user = None
            for target, _ in channel.overwrites.items():
                if isinstance(target, discord.Member):
                    user = target
                    break
            
            if user:
                # Set permissions: user can read but not send messages
                await channel.set_permissions(user, read_messages=True, send_messages=False)
            
            # Move to archive category
            await channel.edit(category=archive_category)
            await channel.send(f"Channel archived. Reason: {reason}")

    async def restore_channel(self, channel, guild, member):
        """Helper function to restore channel from archive"""
        applications_id = await self.config.guild(guild).applications_category()
        applications_category = guild.get_channel(applications_id)
        
        if applications_category:
            # Restore user's ability to send messages
            await channel.set_permissions(member, read_messages=True, send_messages=True)
            
            # Move channel back to applications category
            await channel.edit(category=applications_category)
            
            await channel.send(f"Channel restored for {member.mention}")

    @commands.group(aliases=["da"])
    @checks.admin_or_permissions(administrator=True)
    async def disapps(self, ctx):
        """Discord Applications Management Commands"""
        pass

    @disapps.command()
    @checks.admin_or_permissions(administrator=True)
    async def history(self, ctx, user: discord.Member):
        """View application history for a user"""
        applications = await self.config.guild(ctx.guild).applications()
        user_data = applications.get(str(user.id))
        
        if not user_data or not user_data.get("application_history"):
            await ctx.send(f"No application history found for {user.mention}")
            return

        embed = discord.Embed(
            title=f"Application History for {user}",
            color=discord.Color.blue()
        )
        
        for entry in user_data["application_history"]:
            timestamp = datetime.fromtimestamp(entry["timestamp"]).strftime("%Y-%m-%d %H:%M:%S")
            value = f"Status: {entry['status']}\n"
            if entry.get("reason"):
                value += f"Reason: {entry['reason']}\n"
            embed.add_field(
                name=f"Application on {timestamp}",
                value=value,
                inline=False
            )

        await ctx.send(embed=embed)

    # [Previous setup, on_member_remove, on_member_join, and test commands remain the same]

def setup(bot):
    bot.add_cog(DisApps(bot))
