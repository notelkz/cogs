import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from discord.ui import Button, View, Modal, TextInput
from typing import Dict, Optional
import asyncio
from datetime import datetime

class ApplicationModal(Modal):
    def __init__(self, questions):
        super().__init__(title="Server Application")
        self.questions = questions
        self.inputs = []
        
        for i, question in enumerate(questions):
            self.inputs.append(
                TextInput(
                    label=question[:45],  # Discord limit
                    style=discord.TextStyle.paragraph,
                    required=True,
                    max_length=1000
                )
            )
            self.add_item(self.inputs[-1])

    async def on_submit(self, interaction: discord.Interaction):
        responses = [input.value for input in self.inputs]
        await interaction.response.defer()
        return responses

class ModeratorView(View):
    def __init__(self, cog, user_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.user_id = user_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
    async def accept_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.defer()
        
        guild = interaction.guild
        member = guild.get_member(self.user_id)
        if member:
            role_id = await self.cog.config.guild(guild).member_role()
            role = guild.get_role(role_id)
            if role:
                try:
                    await member.add_roles(role)
                    await interaction.followup.send(f"Application accepted. {member.mention} has been given the {role.name} role.")
                    
                    # Archive the application
                    archive_channel_id = await self.cog.config.guild(guild).archive_channel()
                    archive_channel = guild.get_channel(archive_channel_id)
                    if archive_channel:
                        await interaction.message.edit(view=None)
                        await interaction.message.reply("Application accepted ✅")
                        await archive_channel.send(content=f"Accepted application from {member.mention}", embeds=[interaction.message.embeds[0]])
                        
                    await interaction.message.delete(delay=5)
                except discord.Forbidden:
                    await interaction.followup.send("I don't have permission to assign roles!")
            else:
                await interaction.followup.send("Member role not found!")
        else:
            await interaction.followup.send("Member not found in server!")

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red)
    async def decline_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(DeclineModal(self.cog, self.user_id))

class DeclineModal(Modal):
    def __init__(self, cog, user_id):
        super().__init__(title="Decline Application")
        self.cog = cog
        self.user_id = user_id
        self.reason = TextInput(
            label="Reason for declining",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        guild = interaction.guild
        member = guild.get_member(self.user_id)
        
        # Store decline reason
        async with self.cog.config.guild(guild).applications_archive() as archive:
            archive[str(self.user_id)] = {
                "status": "declined",
                "reason": self.reason.value,
                "timestamp": datetime.now().isoformat()
            }
        
        if member:
            try:
                await member.send(f"Your application has been declined. Reason: {self.reason.value}")
            except discord.Forbidden:
                pass
        
        # Archive the application
        archive_channel_id = await self.cog.config.guild(guild).archive_channel()
        archive_channel = guild.get_channel(archive_channel_id)
        if archive_channel:
            await interaction.message.edit(view=None)
            await interaction.message.reply("Application declined ❌")
            await archive_channel.send(
                content=f"Declined application from <@{self.user_id}>\nReason: {self.reason.value}",
                embeds=[interaction.message.embeds[0]]
            )
        
        await interaction.message.delete(delay=5)

class ApplicationView(View):
    def __init__(self, cog, user_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.user_id = user_id
        self.has_applied = False
        self.has_contacted = False

    @discord.ui.button(label="Apply to Join", style=discord.ButtonStyle.green)
    async def apply_button(self, interaction: discord.Interaction, button: Button):
        if self.has_applied:
            await interaction.response.send_message("You have already submitted an application.", ephemeral=True)
            return
        
        questions = await self.cog.config.guild(interaction.guild).application_questions()
        modal = ApplicationModal(questions)
        await interaction.response.send_modal(modal)
        
        try:
            responses = await modal.wait()
            self.has_applied = True
            
            # Create response embed
            embed = discord.Embed(
                title="New Application",
                description=f"Application from {interaction.user.mention}",
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            
            for question, response in zip(questions, responses):
                embed.add_field(name=question, value=response, inline=False)
            
            # Get moderators and send notification
            mods_role = interaction.guild.get_role(await self.cog.config.guild(interaction.guild).moderator_role())
            online_mods = [member for member in mods_role.members if member.status != discord.Status.offline] if mods_role else []
            
            notification = f"{' '.join(mod.mention for mod in online_mods) if online_mods else mods_role.mention}"
            
            await interaction.channel.send(
                content=notification,
                embed=embed,
                view=ModeratorView(self.cog, interaction.user.id)
            )
            
            await interaction.followup.send("Your application has been submitted and will be reviewed soon!", ephemeral=True)
            
        except asyncio.TimeoutError:
            await interaction.followup.send("Application timed out. Please try again.", ephemeral=True)

    @discord.ui.button(label="Contact Moderators", style=discord.ButtonStyle.red)
    async def contact_button(self, interaction: discord.Interaction, button: Button):
        if self.has_contacted:
            await interaction.response.send_message("You have already contacted moderators.", ephemeral=True)
            return
        
        self.has_contacted = True
        
        # Get moderators and send notification
        mods_role = interaction.guild.get_role(await self.cog.config.guild(interaction.guild).moderator_role())
        online_mods = [member for member in mods_role.members if member.status != discord.Status.offline] if mods_role else []
        
        notification = f"{' '.join(mod.mention for mod in online_mods) if online_mods else mods_role.mention}"
        await interaction.response.send_message(
            f"{notification} - {interaction.user.mention} needs assistance with their application!",
            allowed_mentions=discord.AllowedMentions(roles=True, users=True)
        )

class Disappstest(commands.Cog):
    """Discord Applications Management Cog (Test Version)"""
    
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567891)
        default_guild = {
            "archive_channel": None,
            "applications_category": None,
            "member_role": None,
            "moderator_role": None,
            "application_questions": [],
            "applications_archive": {}
        }
        self.config.register_guild(**default_guild)

    @commands.group(aliases=["dat"])
    @commands.admin_or_permissions(administrator=True)
    async def disappstest(self, ctx):
        """Applications management commands (Test Version)"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @disappstest.command()
    async def setup(self, ctx):
        """Setup the applications system (Test Version)"""
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            # Setup applications category
            await ctx.send("Please mention or provide the ID of the category for applications:")
            msg = await self.bot.wait_for('message', check=check, timeout=30)
            try:
                category_id = int(msg.content.strip())
                category = ctx.guild.get_channel(category_id)
            except ValueError:
                if msg.channel_mentions:
                    category = msg.channel_mentions[0].category
                else:
                    await ctx.send("Invalid category. Setup cancelled.")
                    return
            
            if not category:
                await ctx.send("Category not found. Setup cancelled.")
                return
            
            await self.config.guild(ctx.guild).applications_category.set(category.id)

            # Setup archive channel
            await ctx.send("Please mention or provide the ID of the archive channel:")
            msg = await self.bot.wait_for('message', check=check, timeout=30)
            try:
                channel_id = int(msg.content.strip())
                channel = ctx.guild.get_channel(channel_id)
            except ValueError:
                if msg.channel_mentions:
                    channel = msg.channel_mentions[0]
                else:
                    await ctx.send("Invalid channel. Setup cancelled.")
                    return
            
            if not channel:
                await ctx.send("Channel not found. Setup cancelled.")
                return
            
            await self.config.guild(ctx.guild).archive_channel.set(channel.id)

            # Setup member role
            await ctx.send("Please mention or provide the ID of the member role:")
            msg = await self.bot.wait_for('message', check=check, timeout=30)
            try:
                role_id = int(msg.content.strip())
                role = ctx.guild.get_role(role_id)
            except ValueError:
                if msg.role_mentions:
                    role = msg.role_mentions[0]
                else:
                    await ctx.send("Invalid role. Setup cancelled.")
                    return
            
            if not role:
                await ctx.send("Role not found. Setup cancelled.")
                return
            
            await self.config.guild(ctx.guild).member_role.set(role.id)

            # Setup moderator role
            await ctx.send("Please mention or provide the ID of the moderator role:")
            msg = await self.bot.wait_for('message', check=check, timeout=30)
            try:
                role_id = int(msg.content.strip())
                role = ctx.guild.get_role(role_id)
            except ValueError:
                if msg.role_mentions:
                    role = msg.role_mentions[0]
                else:
                    await ctx.send("Invalid role. Setup cancelled.")
                    return
            
            if not role:
                await ctx.send("Role not found. Setup cancelled.")
                return
            
            await self.config.guild(ctx.guild).moderator_role.set(role.id)

            # Setup application questions
            questions = []
            await ctx.send("Enter your application questions one by one. Type 'done' when finished:")
            
            while True:
                msg = await self.bot.wait_for('message', check=check, timeout=30)
                if msg.content.lower() == 'done':
                    break
                questions.append(msg.content)
            
            if not questions:
                await ctx.send("No questions added. Setup cancelled.")
                return
            
            await self.config.guild(ctx.guild).application_questions.set(questions)
            
            await ctx.send("Setup completed successfully! ✅")

        except asyncio.TimeoutError:
            await ctx.send("Setup timed out. Please try again.")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Handle new member joins"""
        # Check if applications are set up for this guild
        category_id = await self.config.guild(member.guild).applications_category()
        if not category_id:
            return

        category = member.guild.get_channel(category_id)
        if not category:
            return

        # Check for previous applications
        archive = await self.config.guild(member.guild).applications_archive()
        previous_application = archive.get(str(member.id))

        # Create application channel
        channel_name = f"application-{member.name.lower()}"
        channel = await category.create_text_channel(
            name=channel_name,
            overwrites={
                member.guild.default_role: discord.PermissionOverwrite(read_messages=False),
                member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                member.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
        )

        if previous_application:
            # Handle reapplication
            embed = discord.Embed(
                title="Reapplication Detected",
                description="Please explain why you are reapplying/why you left before:",
                color=discord.Color.orange()
            )
            await channel.send(f"{member.mention}", embed=embed)
        else:
            # New application
            embed = discord.Embed(
                title="Welcome to the Server!",
                description="To join our community, please complete the application process below.",
                color=discord.Color.blue()
            )
            await channel.send(
                content=member.mention,
                embed=embed,
                view=ApplicationView(self, member.id)
            )
