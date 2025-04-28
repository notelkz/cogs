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

            # Update application status
            applications = await self.cog.config.guild(interaction.guild).applications()
            if str(self.applicant.id) in applications:
                applications[str(self.applicant.id)]['status'] = 'accepted'
                await self.cog.config.guild(interaction.guild).applications.set(applications)

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
            await modal.wait()
            
            # Disable both buttons
            self.accept_button.disabled = True
            self.decline_button.disabled = True
            await interaction.message.edit(view=self)

            # Update application status
            applications = await self.cog.config.guild(interaction.guild).applications()
            user_application = applications.get(str(self.applicant.id), {})
            
            # Track number of declines and check if previously accepted
            declines = user_application.get('declines', 0) + 1
            previously_accepted = user_application.get('previously_accepted', False)
            user_application['declines'] = declines
            user_application['status'] = 'declined'
            await self.cog.config.guild(interaction.guild).applications.set(applications)

            try:
                # Handle previously accepted members who are now declined
                if previously_accepted:
                    # Remove all permissions except for the current channel
                    for channel in interaction.guild.channels:
                        if channel != interaction.channel:  # Skip the current application channel
                            if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel)):
                                try:
                                    await channel.set_permissions(self.applicant, read_messages=False, send_messages=False)
                                except discord.Forbidden:
                                    continue

                    # Set permissions for current channel to read-only
                    await interaction.channel.set_permissions(self.applicant, read_messages=True, send_messages=False)

                    await self.applicant.send(
                        f"Your application to rejoin {interaction.guild.name} has been declined.\n"
                        f"Reason: {modal.decline_reason}\n\n"
                        "As you were previously accepted but have now been declined, "
                        "your access to the server has been restricted."
                    )

                    await interaction.channel.send(
                        f"Application declined. {self.applicant.mention} was previously accepted but has now been declined.\n"
                        "Their permissions have been restricted to this channel only.\n"
                        f"Decline reason: {modal.decline_reason}"
                    )

                # Handle regular second declines
                elif declines >= 2:
                    # Remove all permissions except for the current channel
                    for channel in interaction.guild.channels:
                        if channel != interaction.channel:  # Skip the current application channel
                            if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel)):
                                try:
                                    await channel.set_permissions(self.applicant, read_messages=False, send_messages=False)
                                except discord.Forbidden:
                                    continue

                    # Set permissions for current channel to read-only
                    await interaction.channel.set_permissions(self.applicant, read_messages=True, send_messages=False)

                    await self.applicant.send(
                        f"Your application to {interaction.guild.name} has been declined.\n"
                        f"Reason: {modal.decline_reason}\n\n"
                        "As this is your second declined application, you will not be able to apply again in the future. "
                        "Your access to the server has been restricted."
                    )

                    await interaction.channel.send(
                        f"Application declined. This was {self.applicant.mention}'s second decline.\n"
                        "Their permissions have been restricted, and they can no longer apply again.\n"
                        f"Decline reason: {modal.decline_reason}"
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
                if previously_accepted:
                    await interaction.channel.send(
                        f"Could not DM the user, but their application has been declined.\n"
                        f"They were previously accepted but have now been declined. Their permissions have been restricted.\n"
                        f"Reason: {modal.decline_reason}"
                    )
                elif declines >= 2:
                    await interaction.channel.send(
                        f"Could not DM the user, but their application has been declined.\n"
                        f"This was their second decline. Their permissions have been restricted.\n"
                        f"Reason: {modal.decline_reason}"
                    )
                else:
                    await interaction.channel.send(
                        f"Could not DM the user, but the application has been declined.\n"
                        f"Reason: {modal.decline_reason}"
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
        self.original_view = original_view
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
        self.message = None

    @discord.ui.button(label="Apply Now", style=discord.ButtonStyle.green)
    async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.message:
            self.message = interaction.message
        
        modal = ApplicationModal(self)
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
            "applications": {}
        }
        self.config.register_guild(**default_guild)

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

            await ctx.send("Please mention the Archive category or provide its ID (only moderators will see this):")
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=30.0)
            try:
                archive_category = await commands.CategoryChannelConverter().convert(ctx, msg.content)
                await self.config.guild(guild).archive_category.set(archive_category.id)
                
                # Set archive category permissions for moderators only
                await archive_category.set_permissions(guild.default_role, read_messages=False)
                await archive_category.set_permissions(mod_role, read_messages=True)
                await archive_category.set_permissions(guild.me, read_messages=True)
                
            except:
                await ctx.send("Invalid category. Setup cancelled.")
                return
            
            await self.config.guild(guild).setup_complete.set(True)
            await ctx.send("Setup complete!")
            
        except asyncio.TimeoutError:
            await ctx.send("Setup timed out.")

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Handle member leaves"""
        guild = member.guild
        if not await self.config.guild(guild).setup_complete():
            return

        # Get applications data
        applications = await self.config.guild(guild).applications()
        channel_id = applications.get(str(member.id), {}).get('channel_id')
        
        if not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            return

        application_status = applications.get(str(member.id), {}).get('status', 'none')

        if application_status == 'none':
            # No application submitted, delete channel
            await channel.delete()
            del applications[str(member.id)]
            
        elif application_status == 'pending':
            # Application submitted but not reviewed, archive after 24 hours
            await channel.send(f"{member.name} has left the server. This channel will be archived in 24 hours.")
            await asyncio.sleep(86400)  # 24 hours
            await self.move_to_archive(channel, guild, f"User left server while application was pending")
            applications[str(member.id)]['status'] = 'archived'
            
        elif application_status == 'accepted':
            # User was accepted but left, keep the channel in archive
            await self.move_to_archive(channel, guild, f"Accepted user left server on {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}")
            # Don't delete the application data so we can restore it if they return
            
        await self.config.guild(guild).applications.set(applications)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild = member.guild
        if not await self.config.guild(guild).setup_complete():
            return

        # Get applications data
        applications = await self.config.guild(guild).applications()
        existing_application = applications.get(str(member.id), {})
        
        # Check if user has been declined twice
        if existing_application.get('declines', 0) >= 2:
            try:
                await member.send(
                    "Your previous applications to this server were declined. "
                    "You are not eligible to submit new applications."
                )
            except discord.Forbidden:
                pass
            
            # Kick the member
            try:
                await member.kick(reason="Previously declined applications")
            except discord.Forbidden:
                pass
            return
        
        if existing_application and existing_application.get('status') == 'accepted':
            # User was previously accepted, restore their channel
            channel = guild.get_channel(existing_application['channel_id'])
            if channel:
                mod_role_id = await self.config.guild(guild).mod_role()
                mod_role = guild.get_role(mod_role_id)

                # Move channel back to applications category and restore permissions
                await self.restore_channel(channel, guild, member)
                
                # Update application status and mark as previously accepted
                applications[str(member.id)]['status'] = 'pending'
                applications[str(member.id)]['previously_accepted'] = True
                await self.config.guild(guild).applications.set(applications)

                # Send notification
                embed = discord.Embed(
                    title="Previous Member Returned",
                    description="This user was previously accepted but left the server. Please review their application again.",
                    color=discord.Color.yellow()
                )
                
                mod_view = ModButtons(self, member)
                await channel.send(
                    content=f"{mod_role.mention}",
                    embed=embed,
                    view=mod_view
                )

                # Notify the user
                await channel.send(
                    f"{member.mention} Welcome back! Your previous application channel has been restored. "
                    "A moderator will review your application again."
                )
                
                return

        # If no previous accepted application exists, proceed with normal application process
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

        # Store channel information
        applications[str(member.id)] = {
            'channel_id': channel.id,
            'status': 'none',
            'timestamp': datetime.utcnow().timestamp()
        }
        await self.config.guild(guild).applications.set(applications)

        embed = discord.Embed(
            title="Welcome to Zero Lives Left",
            description="Unfortunately, due to timewasters, spam bots and other annoyances we've had to implement an application system in Discord.\n\n**If you are interested in joining us for any of the games we're currently playing, please click the button below and fill out the short form.**",
            color=3447003,
            timestamp=datetime.fromisoformat("2025-04-27T22:54:00.000Z")
        )
        
        # Set author
        embed.set_author(name="elkz - Admin")
        
        # Set thumbnail
        embed.set_thumbnail(url="https://notelkz.net/images/discordicon.png")
        
        # Set footer
        embed.set_footer(text="If you have any issues, use the 'Contact Mod' button.")

        await channel.send(content=member.mention, embed=embed, view=ApplicationButtons(self))

    @disapps.command()
    async def test(self, ctx):
        """Test the application system with a fake member join"""
        await self.on_member_join(ctx.author)
        await ctx.send("Test application created!")
