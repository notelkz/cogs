import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from typing import Optional
import asyncio
import datetime
from datetime import timedelta

WELCOME_EMBED = {
    "author": {
        "name": "Community Applications"
    },
    "description": (
        "Welcome to our community application system!\n\n"
        "**Please click the button below to begin your application process. "
        "Make sure to answer all questions thoroughly.**"
    ),
    "footer": {
        "text": "If you need assistance, use the 'Contact Staff' button."
    },
    "color": 3447003
}

DEFAULT_QUESTIONS = [
    "How old are you?",
    "Where are you based?",
    "What is your preferred platform? (PC/Console)",
    "What games do you play?",
    "Why do you want to join our community?"
]

def safe_channel_name(name):
    return ''.join(c for c in name.lower().replace(" ", "-") if c.isalnum() or c == "-") + "-application"

class CommunityApplications(commands.Cog):
    """Advanced community application management system."""

    __version__ = "2.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xCA123456)
        default_guild = {
            "applications_category": None,
            "archive_category": None,
            "staff_role": None,
            "application_embed": None,
            "accept_role": None,
            "application_questions": DEFAULT_QUESTIONS,
            "question_timeout": 300,
            "application_timeout": 24,  # hours
            "templates": {},
            "stats": {
                "total": 0,
                "accepted": 0,
                "declined": 0,
                "pending": 0
            }
        }
        default_member = {
            "application": None,
            "decline_reason": None,
            "notes": [],
            "application_history": [],
            "last_application": None
        }
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        self.application_tasks = {}
        self.timeout_task = self.bot.loop.create_task(self.check_application_timeouts())

    def cog_unload(self):
        if self.timeout_task:
            self.timeout_task.cancel()
        for task in self.application_tasks.values():
            task.cancel()

    async def check_application_timeouts(self):
        """Background task to check for timed-out applications."""
        await self.bot.wait_until_ready()
        while True:
            try:
                for guild in self.bot.guilds:
                    timeout = await self.config.guild(guild).application_timeout()
                    if not timeout:
                        continue
                    
                    category_id = await self.config.guild(guild).applications_category()
                    category = guild.get_channel(category_id)
                    if not category:
                        continue

                    for channel in category.channels:
                        if (datetime.datetime.utcnow() - channel.created_at).total_seconds() > timeout * 3600:
                            await self._move_to_archive(channel)
                            await channel.send("Application auto-archived due to inactivity.")

                await asyncio.sleep(3600)  # Check every hour
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Error in application timeout checker: {e}")
                await asyncio.sleep(3600)

    async def update_stats(self, guild, action):
        """Update application statistics."""
        async with self.config.guild(guild).stats() as stats:
            stats["total"] += 1
            if action == "accept":
                stats["accepted"] += 1
                stats["pending"] -= 1
            elif action == "decline":
                stats["declined"] += 1
                stats["pending"] -= 1
            elif action == "new":
                stats["pending"] += 1

    async def get_application_embed(self, guild):
        """Get the application embed for a guild."""
        embed_data = await self.config.guild(guild).application_embed()
        if not embed_data:
            embed_data = WELCOME_EMBED
        return discord.Embed.from_dict(embed_data)
    async def _open_new_application(self, member):
        """Create a new application channel for a member."""
        guild = member.guild
        app_cat_id = await self.config.guild(guild).applications_category()
        app_cat = guild.get_channel(app_cat_id)
        staff_role_id = await self.config.guild(guild).staff_role()
        staff_role = guild.get_role(staff_role_id)
        
        if not app_cat or not staff_role:
            return

        channel_name = safe_channel_name(member.name)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            staff_role: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        channel = await guild.create_text_channel(
            channel_name, 
            category=app_cat, 
            overwrites=overwrites,
            topic=f"Application for {member.name} ({member.id})"
        )

        embed = await self.get_application_embed(guild)
        view = ApplicationView(self, member, channel)
        await channel.send(content=member.mention, embed=embed, view=view)

        # Update statistics
        await self.update_stats(guild, "new")

        # Start timeout task
        timeout = await self.config.guild(guild).application_timeout()
        if timeout:
            self.application_tasks[channel.id] = asyncio.create_task(
                self._application_timeout(channel, timeout)
            )

    async def _reopen_application(self, member, prev_app):
        """Reopen a previous application channel."""
        guild = member.guild
        app_cat_id = await self.config.guild(guild).applications_category()
        app_cat = guild.get_channel(app_cat_id)
        staff_role_id = await self.config.guild(guild).staff_role()
        staff_role = guild.get_role(staff_role_id)
        archive_cat_id = await self.config.guild(guild).archive_category()
        archive_cat = guild.get_channel(archive_cat_id)

        if not all([app_cat, staff_role, archive_cat]):
            return

        channel_name = safe_channel_name(member.name)
        channel = discord.utils.get(app_cat.channels, name=channel_name)

        if not channel:
            channel = discord.utils.get(archive_cat.channels, name=channel_name)
            if channel:
                await channel.edit(category=app_cat)
                await channel.set_permissions(member, read_messages=True, send_messages=True)
                await channel.set_permissions(staff_role, read_messages=True, send_messages=True)
            else:
                await self._open_new_application(member)
                return

        # Store previous application in history
        async with self.config.member(member).application_history() as history:
            if prev_app:
                history.append({
                    "application": prev_app,
                    "timestamp": datetime.datetime.utcnow().isoformat(),
                    "status": "reopened"
                })

        await channel.send(
            f"{member.mention}, your previous application has been found. "
            "Please explain why you're reapplying or why you left previously."
        )

        def check(m):
            return m.author == member and m.channel == channel

        try:
            msg = await self.bot.wait_for(
                "message",
                check=check,
                timeout=await self.config.guild(guild).question_timeout()
            )
            await self._ping_staff(channel, staff_role)
        except asyncio.TimeoutError:
            await channel.send("No response received. Please try again when ready.")

    async def _application_timeout(self, channel, hours):
        """Handle application timeout."""
        try:
            await asyncio.sleep(hours * 3600)
            await channel.send(
                "⚠️ This application has timed out due to inactivity. "
                "The channel will be archived in 1 hour unless there is new activity."
            )
            try:
                await asyncio.sleep(3600)
                await self._move_to_archive(channel)
                await channel.send("📁 Application archived due to timeout.")
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            pass

    async def _ping_staff(self, channel, staff_role):
        """Ping online staff members."""
        online_staff = [m for m in staff_role.members if m.status != discord.Status.offline]
        if online_staff:
            staff_list = " ".join(m.mention for m in online_staff[:3])  # Limit to 3 mentions
            await channel.send(
                f"{staff_list} + {len(online_staff) - 3} more staff" if len(online_staff) > 3
                else f"{staff_list}, application needs review!"
            )
        else:
            await channel.send(f"{staff_role.mention}, application needs review!")

    async def _move_to_archive(self, channel, member=None):
        """Move application channel to archive category."""
        guild = channel.guild
        archive_cat_id = await self.config.guild(guild).archive_category()
        archive_cat = guild.get_channel(archive_cat_id)
        
        if not archive_cat:
            return

        await channel.edit(category=archive_cat)
        
        if member:
            await channel.set_permissions(member, read_messages=False, send_messages=False)
        
        # Cancel any active timeout task
        task = self.application_tasks.pop(channel.id, None)
        if task:
            task.cancel()

        # Update channel topic
        current_topic = channel.topic or ""
        await channel.edit(topic=f"[ARCHIVED] {current_topic}")
    @commands.group(aliases=["memapps"])
    @commands.guild_only()
    async def applications(self, ctx):
        """Community Applications Management System"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @applications.command()
    @checks.admin_or_permissions(manage_guild=True)
    async def setup(self, ctx):
        """Interactive setup for the application system."""
        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel

        try:
            # Applications Category
            await ctx.send("Please provide the **ID** of the category for new applications:")
            msg = await self.bot.wait_for("message", check=check, timeout=60)
            app_cat_id = int(msg.content)
            cat = ctx.guild.get_channel(app_cat_id)
            if not isinstance(cat, discord.CategoryChannel):
                await ctx.send("Invalid category ID. Setup cancelled.")
                return
            await self.config.guild(ctx.guild).applications_category.set(app_cat_id)

            # Archive Category
            await ctx.send("Please provide the **ID** of the category for archived applications:")
            msg = await self.bot.wait_for("message", check=check, timeout=60)
            arch_cat_id = int(msg.content)
            cat = ctx.guild.get_channel(arch_cat_id)
            if not isinstance(cat, discord.CategoryChannel):
                await ctx.send("Invalid category ID. Setup cancelled.")
                return
            await self.config.guild(ctx.guild).archive_category.set(arch_cat_id)

            # Staff Role
            await ctx.send("Please provide the **ID** of the Staff role:")
            msg = await self.bot.wait_for("message", check=check, timeout=60)
            staff_role_id = int(msg.content)
            role = ctx.guild.get_role(staff_role_id)
            if not role:
                await ctx.send("Invalid role ID. Setup cancelled.")
                return
            await self.config.guild(ctx.guild).staff_role.set(staff_role_id)

            # Accept Role
            await ctx.send("Please provide the **ID** of the role to give on acceptance:")
            msg = await self.bot.wait_for("message", check=check, timeout=60)
            accept_role_id = int(msg.content)
            role = ctx.guild.get_role(accept_role_id)
            if not role:
                await ctx.send("Invalid role ID. Setup cancelled.")
                return
            await self.config.guild(ctx.guild).accept_role.set(accept_role_id)

            # Application Timeout
            await ctx.send("How many hours should applications remain open before auto-archiving? (0 to disable)")
            msg = await self.bot.wait_for("message", check=check, timeout=60)
            timeout = int(msg.content)
            await self.config.guild(ctx.guild).application_timeout.set(timeout)

            await ctx.send("Setup complete! Use `!applications questions` to customize application questions.")

        except asyncio.TimeoutError:
            await ctx.send("Setup timed out. Please try again.")
        except ValueError:
            await ctx.send("Invalid input. Please provide valid IDs.")

    @applications.group(name="questions")
    @checks.admin_or_permissions(manage_guild=True)
    async def questions_group(self, ctx):
        """Manage application questions."""
        if ctx.invoked_subcommand is None:
            questions = await self.config.guild(ctx.guild).application_questions()
            msg = "**Current Application Questions:**\n\n"
            for i, q in enumerate(questions, 1):
                msg += f"{i}. {q}\n"
            await ctx.send(msg)

    @questions_group.command(name="add")
    async def add_question(self, ctx, *, question: str):
        """Add a new application question."""
        async with self.config.guild(ctx.guild).application_questions() as questions:
            questions.append(question)
        await ctx.send(f"Added question: {question}")

    @questions_group.command(name="remove")
    async def remove_question(self, ctx, index: int):
        """Remove a question by its number."""
        async with self.config.guild(ctx.guild).application_questions() as questions:
            if 1 <= index <= len(questions):
                removed = questions.pop(index - 1)
                await ctx.send(f"Removed question: {removed}")
            else:
                await ctx.send("Invalid question number.")

    @questions_group.command(name="clear")
    async def clear_questions(self, ctx):
        """Reset questions to default."""
        await self.config.guild(ctx.guild).application_questions.set(DEFAULT_QUESTIONS)
        await ctx.send("Questions reset to default.")

    @applications.command(name="stats")
    @checks.mod_or_permissions(manage_messages=True)
    async def show_stats(self, ctx):
        """Show application statistics."""
        stats = await self.config.guild(ctx.guild).stats()
        
        embed = discord.Embed(
            title="Application Statistics",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.utcnow()
        )
        
        embed.add_field(name="Total Applications", value=stats["total"])
        embed.add_field(name="Accepted", value=stats["accepted"])
        embed.add_field(name="Declined", value=stats["declined"])
        embed.add_field(name="Pending", value=stats["pending"])
        
        if stats["total"] > 0:
            accept_rate = (stats["accepted"] / stats["total"]) * 100
            embed.add_field(name="Acceptance Rate", value=f"{accept_rate:.1f}%")
        
        await ctx.send(embed=embed)

    @applications.command(name="queue")
    @checks.mod_or_permissions(manage_messages=True)
    async def show_queue(self, ctx):
        """Show pending applications in order."""
        category_id = await self.config.guild(ctx.guild).applications_category()
        category = ctx.guild.get_channel(category_id)
        if not category:
            await ctx.send("Applications category not found.")
            return

        pending = []
        for channel in category.channels:
            created_at = channel.created_at
            if channel.topic:
                member_id = int(channel.topic.split('(')[-1].split(')')[0])
                member = ctx.guild.get_member(member_id)
                if member:
                    pending.append((created_at, member, channel))

        if not pending:
            await ctx.send("No pending applications.")
            return

        pending.sort(key=lambda x: x[0])
        embed = discord.Embed(
            title="Application Queue",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.utcnow()
        )

        for i, (created, member, channel) in enumerate(pending, 1):
            waiting_time = datetime.datetime.utcnow() - created
            embed.add_field(
                name=f"{i}. {member.display_name}",
                value=f"Waiting: {waiting_time.days}d {waiting_time.seconds//3600}h\n{channel.mention}",
                inline=False
            )

        await ctx.send(embed=embed)
    @applications.group(name="notes")
    @checks.mod_or_permissions(manage_messages=True)
    async def notes_group(self, ctx):
        """Manage application notes."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @notes_group.command(name="add")
    async def add_note(self, ctx, member: discord.Member, *, note: str):
        """Add a note to a member's application."""
        async with self.config.member(member).notes() as notes:
            notes.append({
                "author": ctx.author.id,
                "note": note,
                "timestamp": datetime.datetime.utcnow().isoformat()
            })
        await ctx.send("Note added.")

    @notes_group.command(name="view")
    async def view_notes(self, ctx, member: discord.Member):
        """View notes for a member's application."""
        notes = await self.config.member(member).notes()
        if not notes:
            await ctx.send("No notes found.")
            return

        embed = discord.Embed(
            title=f"Notes for {member.display_name}",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.utcnow()
        )
        
        for note in notes:
            author = ctx.guild.get_member(note["author"])
            timestamp = datetime.datetime.fromisoformat(note["timestamp"])
            embed.add_field(
                name=f"By {author.display_name} on {timestamp.strftime('%Y-%m-%d %H:%M')}",
                value=note["note"],
                inline=False
            )
        
        await ctx.send(embed=embed)

    @applications.group(name="template")
    @checks.admin_or_permissions(manage_guild=True)
    async def template_group(self, ctx):
        """Manage application templates."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @template_group.command(name="save")
    async def save_template(self, ctx, name: str):
        """Save current application settings as a template."""
        guild_data = await self.config.guild(ctx.guild).all()
        template_data = {
            "questions": guild_data["application_questions"],
            "timeout": guild_data["application_timeout"],
            "embed": guild_data["application_embed"]
        }
        async with self.config.guild(ctx.guild).templates() as templates:
            templates[name] = template_data
        await ctx.send(f"Saved template: {name}")

    @template_group.command(name="load")
    async def load_template(self, ctx, name: str):
        """Load a saved application template."""
        templates = await self.config.guild(ctx.guild).templates()
        if name not in templates:
            await ctx.send("Template not found.")
            return

        template = templates[name]
        await self.config.guild(ctx.guild).application_questions.set(template["questions"])
        await self.config.guild(ctx.guild).application_timeout.set(template["timeout"])
        await self.config.guild(ctx.guild).application_embed.set(template["embed"])
        await ctx.send(f"Loaded template: {name}")

    @template_group.command(name="list")
    async def list_templates(self, ctx):
        """List saved templates."""
        templates = await self.config.guild(ctx.guild).templates()
        if not templates:
            await ctx.send("No templates saved.")
            return

        embed = discord.Embed(
            title="Saved Templates",
            color=discord.Color.blue(),
            description="\n".join(templates.keys())
        )
        await ctx.send(embed=embed)

# UI Components

class ApplicationView(discord.ui.View):
    def __init__(self, cog, member, channel):
        super().__init__(timeout=None)
        self.cog = cog
        self.member = member
        self.channel = channel
        self.applied = False
        self.contact_clicked = False

    @discord.ui.button(label="Begin Application", style=discord.ButtonStyle.green, custom_id="begin_app")
    async def begin_application(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("This is not your application!", ephemeral=True)
            return
        if self.applied:
            await interaction.response.send_message("You have already started your application.", ephemeral=True)
            return

        questions = await self.cog.config.guild(interaction.guild).application_questions()
        modal = ApplicationModal(self.cog, self.member, self.channel, self, questions)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Contact Staff", style=discord.ButtonStyle.red, custom_id="contact_staff")
    async def contact_staff(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("This is not your application!", ephemeral=True)
            return
        if self.contact_clicked:
            await interaction.response.send_message("Staff have already been contacted.", ephemeral=True)
            return

        staff_role_id = await self.cog.config.guild(self.channel.guild).staff_role()
        staff_role = self.channel.guild.get_role(staff_role_id)
        await self.cog._ping_staff(self.channel, staff_role)
        
        self.contact_clicked = True
        button.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message("Staff have been notified.", ephemeral=True)

class ApplicationModal(discord.ui.Modal):
    def __init__(self, cog, member, channel, view, questions):
        super().__init__(title="Application Form")
        self.cog = cog
        self.member = member
        self.channel = channel
        self.view = view
        
        for i, question in enumerate(questions):
            self.add_item(
                discord.ui.TextInput(
                    label=question[:45],  # Discord limit
                    custom_id=f"q{i}",
                    style=discord.TextStyle.paragraph,
                    required=True,
                    max_length=1000
                )
            )

    async def on_submit(self, interaction: discord.Interaction):
        answers = {self.children[i].label: self.children[i].value for i in range(len(self.children))}
        
        # Save application
        await self.cog.config.member(self.member).application.set(answers)
        await self.cog.config.member(self.member).last_application.set(
            datetime.datetime.utcnow().isoformat()
        )

        # Update view
        self.view.applied = True
        for child in self.view.children:
            if child.custom_id == "begin_app":
                child.disabled = True
        await interaction.message.edit(view=self.view)

        # Send confirmation
        await interaction.response.send_message(
            "Your application has been submitted! Staff will review it soon.",
            ephemeral=True
        )

        # Format and send application for staff
        embed = discord.Embed(
            title=f"Application from {self.member.display_name}",
            color=discord.Color.blue(),
            timestamp=datetime.datetime.utcnow()
        )
        
        for question, answer in answers.items():
            embed.add_field(
                name=question,
                value=answer[:1024],  # Discord field value limit
                inline=False
            )

        embed.set_footer(text=f"User ID: {self.member.id}")
        
        await self.channel.send(embed=embed)
        
        # Add review buttons
        view = StaffReviewView(self.cog, self.member, self.channel)
        await self.channel.send(
            f"Staff, please review this application.",
            view=view
        )

        # Notify staff
        staff_role_id = await self.cog.config.guild(self.channel.guild).staff_role()
        staff_role = self.channel.guild.get_role(staff_role_id)
        await self.cog._ping_staff(self.channel, staff_role)
class StaffReviewView(discord.ui.View):
    def __init__(self, cog, member, channel):
        super().__init__(timeout=None)
        self.cog = cog
        self.member = member
        self.channel = channel

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green, custom_id="accept_app")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        staff_role_id = await self.cog.config.guild(self.channel.guild).staff_role()
        staff_role = self.channel.guild.get_role(staff_role_id)
        
        if staff_role not in interaction.user.roles:
            await interaction.response.send_message("Only staff can use this.", ephemeral=True)
            return

        # Give accepted role
        accept_role_id = await self.cog.config.guild(self.channel.guild).accept_role()
        accept_role = self.channel.guild.get_role(accept_role_id)
        
        try:
            await self.member.add_roles(accept_role, reason=f"Application accepted by {interaction.user}")
        except discord.HTTPException:
            await interaction.response.send_message(
                "Failed to add role. Please check my permissions.",
                ephemeral=True
            )
            return

        # Update application history
        async with self.config.member(self.member).application_history() as history:
            history.append({
                "status": "accepted",
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "by": interaction.user.id,
                "application": await self.cog.config.member(self.member).application()
            })

        # Update stats
        await self.cog.update_stats(self.channel.guild, "accept")

        # Notify member
        try:
            await self.member.send(
                f"Your application to {self.channel.guild.name} has been accepted! "
                f"Welcome to the community!"
            )
        except discord.HTTPException:
            pass  # Member might have DMs closed

        # Archive channel
        await self.cog._move_to_archive(self.channel, self.member)
        
        # Disable buttons
        self.disable_all_buttons()
        await interaction.message.edit(view=self)
        
        await interaction.response.send_message(
            f"✅ Application accepted by {interaction.user.mention}. "
            f"{self.member.mention} has been given the {accept_role.name} role."
        )

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red, custom_id="decline_app")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        staff_role_id = await self.cog.config.guild(self.channel.guild).staff_role()
        staff_role = self.channel.guild.get_role(staff_role_id)
        
        if staff_role not in interaction.user.roles:
            await interaction.response.send_message("Only staff can use this.", ephemeral=True)
            return

        await interaction.response.send_modal(
            DeclineModal(self.cog, self.member, self.channel, self)
        )

    @discord.ui.button(label="Ask Question", style=discord.ButtonStyle.blurple, custom_id="ask_question")
    async def ask_question(self, interaction: discord.Interaction, button: discord.ui.Button):
        staff_role_id = await self.cog.config.guild(self.channel.guild).staff_role()
        staff_role = self.channel.guild.get_role(staff_role_id)
        
        if staff_role not in interaction.user.roles:
            await interaction.response.send_message("Only staff can use this.", ephemeral=True)
            return

        await interaction.response.send_modal(
            QuestionModal(self.member, self.channel)
        )

    def disable_all_buttons(self):
        for item in self.children:
            item.disabled = True

class DeclineModal(discord.ui.Modal):
    def __init__(self, cog, member, channel, view):
        super().__init__(title="Decline Application")
        self.cog = cog
        self.member = member
        self.channel = channel
        self.view = view
        
        self.add_item(
            discord.ui.TextInput(
                label="Reason for declining",
                placeholder="Please provide a reason for declining this application...",
                style=discord.TextStyle.paragraph,
                required=True,
                max_length=1000
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        reason = self.children[0].value

        # Store decline reason
        await self.cog.config.member(self.member).decline_reason.set(reason)

        # Update application history
        async with self.cog.config.member(self.member).application_history() as history:
            history.append({
                "status": "declined",
                "timestamp": datetime.datetime.utcnow().isoformat(),
                "by": interaction.user.id,
                "reason": reason,
                "application": await self.cog.config.member(self.member).application()
            })

        # Update stats
        await self.cog.update_stats(self.channel.guild, "decline")

        # Notify member
        try:
            await self.member.send(
                f"Your application to {self.channel.guild.name} has been declined.\n"
                f"Reason: {reason}\n\n"
                "You may reapply after addressing the concerns mentioned."
            )
        except discord.HTTPException:
            pass  # Member might have DMs closed

        # Archive channel
        await self.cog._move_to_archive(self.channel, self.member)
        
        # Disable buttons in original view
        self.view.disable_all_buttons()
        await interaction.message.edit(view=self.view)
        
        await interaction.response.send_message(
            f"❌ Application declined by {interaction.user.mention}.\n"
            f"Reason: {reason}"
        )

class QuestionModal(discord.ui.Modal):
    def __init__(self, member, channel):
        super().__init__(title="Ask Applicant")
        self.member = member
        self.channel = channel
        
        self.add_item(
            discord.ui.TextInput(
                label="Question",
                placeholder="What would you like to ask the applicant?",
                style=discord.TextStyle.paragraph,
                required=True,
                max_length=1000
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        question = self.children[0].value
        
        await self.channel.send(
            f"{self.member.mention}, **Question from {interaction.user.display_name}**:\n"
            f"{question}"
        )
        
        await interaction.response.send_message(
            "Question sent to applicant.",
            ephemeral=True
        )

# Event Listeners

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Handle new member applications."""
        if member.bot:
            return

        prev_app = await self.config.member(member).application()
        if prev_app:
            await self._reopen_application(member, prev_app)
        else:
            await self._open_new_application(member)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Handle member leaving during application process."""
        if member.bot:
            return

        # Find and update any active application channels
        app_cat_id = await self.config.guild(member.guild).applications_category()
        if not app_cat_id:
            return

        category = member.guild.get_channel(app_cat_id)
        if not category:
            return

        channel_name = safe_channel_name(member.name)
        channel = discord.utils.get(category.channels, name=channel_name)
        
        if channel:
            await channel.send(
                f"⚠️ {member.mention} has left the server during the application process. "
                "Their application has been archived."
            )
            await self._move_to_archive(channel)

