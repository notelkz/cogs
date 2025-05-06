import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from typing import Optional
import asyncio

class AppTest(commands.Cog):
    """Application management for new users."""

    __version__ = "1.0.2"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xAABBCCDD)
        default_guild = {
            "applications_category": None,
            "archive_category": None,
            "moderator_role": None,
            "application_questions": [],
            "application_embed": None,
            "accept_role": None,
        }
        default_member = {
            "application": None,  # dict with application data
            "decline_reason": None,
        }
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)

    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def apptest(self, ctx):
        """Application Test Setup"""
        pass

    @apptest.command()
    async def setup(self, ctx):
        """Setup application system."""
        await ctx.send("Let's set up your application system!\n"
                       "Reply with the **ID** of the category for new applications.")
        def check(m): return m.author == ctx.author and m.channel == ctx.channel
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=60)
            app_cat_id = int(msg.content)
            cat = ctx.guild.get_channel(app_cat_id)
            if not isinstance(cat, discord.CategoryChannel):
                await ctx.send("That's not a valid category ID.")
                return
            await self.config.guild(ctx.guild).applications_category.set(app_cat_id)
        except Exception:
            await ctx.send("Setup cancelled.")
            return

        await ctx.send("Now reply with the **ID** of the category for archived applications.")
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=60)
            arch_cat_id = int(msg.content)
            cat = ctx.guild.get_channel(arch_cat_id)
            if not isinstance(cat, discord.CategoryChannel):
                await ctx.send("That's not a valid category ID.")
                return
            await self.config.guild(ctx.guild).archive_category.set(arch_cat_id)
        except Exception:
            await ctx.send("Setup cancelled.")
            return

        await ctx.send("Now reply with the **ID** of the Moderator role (to ping).")
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=60)
            mod_role_id = int(msg.content)
            role = ctx.guild.get_role(mod_role_id)
            if not role:
                await ctx.send("That's not a valid role ID.")
                return
            await self.config.guild(ctx.guild).moderator_role.set(mod_role_id)
        except Exception:
            await ctx.send("Setup cancelled.")
            return

        await ctx.send("Now reply with the **ID** of the role to give on acceptance.")
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=60)
            accept_role_id = int(msg.content)
            role = ctx.guild.get_role(accept_role_id)
            if not role:
                await ctx.send("That's not a valid role ID.")
                return
            await self.config.guild(ctx.guild).accept_role.set(accept_role_id)
        except Exception:
            await ctx.send("Setup cancelled.")
            return

        await ctx.send("Now paste your application embed as JSON (single message).")
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=180)
            import json
            embed_json = json.loads(msg.content)
            await self.config.guild(ctx.guild).application_embed.set(embed_json)
        except Exception:
            await ctx.send("Invalid JSON or timeout. Setup cancelled.")
            return

        await ctx.send("Now enter the application questions, one per message. Type `done` when finished.")
        questions = []
        while True:
            try:
                msg = await self.bot.wait_for("message", check=check, timeout=120)
                if msg.content.lower() == "done":
                    break
                questions.append(msg.content)
            except asyncio.TimeoutError:
                break
        if not questions:
            await ctx.send("No questions set. Setup cancelled.")
            return
        await self.config.guild(ctx.guild).application_questions.set(questions)
        await ctx.send("Setup complete!")

    @apptest.command()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def test(self, ctx, member: Optional[discord.Member] = None):
        """
        Emulate the application process for a member (or yourself if no member is given).
        """
        member = member or ctx.author
        await ctx.send(f"Emulating application process for {member.mention}...")
        prev_app = await self.config.member(member).application()
        if prev_app:
            await self._reopen_application(member, prev_app)
        else:
            await self._open_new_application(member)
        await ctx.send("Test application process started.")

    # --- Application Logic ---

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        # Store user ID (Red's config is persistent)
        await self.config.member(member).application.set(None)
        # Check for previous application
        prev_app = await self.config.member(member).application()
        if prev_app:
            # Reopen old application
            await self._reopen_application(member, prev_app)
        else:
            # Open new application
            await self._open_new_application(member)

    async def _reopen_application(self, member, prev_app):
        guild = member.guild
        mod_role_id = await self.config.guild(guild).moderator_role()
        mod_role = guild.get_role(mod_role_id)
        # Find or create a channel for this application
        app_cat_id = await self.config.guild(guild).applications_category()
        app_cat = guild.get_channel(app_cat_id)
        channel_name = f"application-{member.id}"
        channel = discord.utils.get(app_cat.channels, name=channel_name)
        if not channel:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                mod_role: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            channel = await guild.create_text_channel(channel_name, category=app_cat, overwrites=overwrites)
        await channel.send(f"{member.mention}, you have previously applied. Why are you reapplying or why did you leave before? Please reply below.")

        def check(m): return m.author == member and m.channel == channel
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=300)
            # Notify moderators
            await self._ping_mods(channel, mod_role)
        except asyncio.TimeoutError:
            await channel.send("No response received. Please reapply when ready.")

    async def _open_new_application(self, member):
        guild = member.guild
        app_cat_id = await self.config.guild(guild).applications_category()
        app_cat = guild.get_channel(app_cat_id)
        mod_role_id = await self.config.guild(guild).moderator_role()
        mod_role = guild.get_role(mod_role_id)
        channel_name = f"application-{member.id}"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            mod_role: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }
        channel = await guild.create_text_channel(channel_name, category=app_cat, overwrites=overwrites)
        # Send the welcome embed
        embed_json = await self.config.guild(guild).application_embed()
        embed = discord.Embed.from_dict(embed_json)
        await channel.send(embed=embed)
        # Send the buttons (not as an embed)
        view = ApplicationView(self, member, channel)
        await channel.send(view=view)

    async def _ping_mods(self, channel, mod_role):
        online_mods = [m for m in mod_role.members if m.status != discord.Status.offline]
        if online_mods:
            await channel.send(f"{' '.join(m.mention for m in online_mods)}, attention needed!")
        else:
            await channel.send(f"{mod_role.mention}, attention needed!")

    async def _move_to_archive(self, channel):
        guild = channel.guild
        archive_cat_id = await self.config.guild(guild).archive_category()
        archive_cat = guild.get_channel(archive_cat_id)
        await channel.edit(category=archive_cat)

# --- UI Views and Modals ---

class ApplicationView(discord.ui.View):
    def __init__(self, cog, member, channel):
        super().__init__(timeout=None)
        self.cog = cog
        self.member = member
        self.channel = channel
        self.applied = False
        self.contact_clicked = False

    @discord.ui.button(label="Apply to Join", style=discord.ButtonStyle.green, custom_id="apply_to_join")
    async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("This is not your application!", ephemeral=True)
            return
        if self.applied:
            await interaction.response.send_message("You have already applied.", ephemeral=True)
            return
        # Launch modal
        questions = await self.cog.config.guild(self.channel.guild).application_questions()
        modal = ApplicationModal(self.cog, self.member, self.channel, questions)
        await interaction.response.send_modal(modal)
        self.applied = True
        button.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Contact Moderators", style=discord.ButtonStyle.red, custom_id="contact_mods")
    async def contact_mods(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.member.id:
            await interaction.response.send_message("This is not your application!", ephemeral=True)
            return
        if self.contact_clicked:
            await interaction.response.send_message("You have already contacted moderators.", ephemeral=True)
            return
        mod_role_id = await self.cog.config.guild(self.channel.guild).moderator_role()
        mod_role = self.channel.guild.get_role(mod_role_id)
        await self.cog._ping_mods(self.channel, mod_role)
        self.contact_clicked = True
        button.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message("Moderators have been notified.", ephemeral=True)

class ApplicationModal(discord.ui.Modal):
    def __init__(self, cog, member, channel, questions):
        super().__init__(title="Application Form")
        self.cog = cog
        self.member = member
        self.channel = channel
        self.questions = questions
        for i, q in enumerate(questions):
            self.add_item(discord.ui.InputText(label=q, custom_id=f"q{i}", style=discord.InputTextStyle.long, required=True))

    async def callback(self, interaction: discord.Interaction):
        answers = {q: self.children[i].value for i, q in enumerate(self.questions)}
        # Store application
        await self.cog.config.member(self.member).application.set(answers)
        await interaction.response.send_message("Your application has been submitted! Moderators will review it soon.", ephemeral=True)
        # Notify mods
        mod_role_id = await self.cog.config.guild(self.channel.guild).moderator_role()
        mod_role = self.channel.guild.get_role(mod_role_id)
        await self.cog._ping_mods(self.channel, mod_role)
        # Add mod-only buttons
        view = ModReviewView(self.cog, self.member, self.channel)
        await self.channel.send(f"Moderators, please review the application for {self.member.mention}.", view=view)

class ModReviewView(discord.ui.View):
    def __init__(self, cog, member, channel):
        super().__init__(timeout=None)
        self.cog = cog
        self.member = member
        self.channel = channel

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green, custom_id="accept_app")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        mod_role_id = await self.cog.config.guild(self.channel.guild).moderator_role()
        mod_role = self.channel.guild.get_role(mod_role_id)
        if mod_role not in interaction.user.roles:
            await interaction.response.send_message("Only moderators can use this.", ephemeral=True)
            return
        accept_role_id = await self.cog.config.guild(self.channel.guild).accept_role()
        accept_role = self.channel.guild.get_role(accept_role_id)
        await self.member.add_roles(accept_role, reason="Application accepted")
        await self.cog._move_to_archive(self.channel)
        await interaction.response.send_message(f"{self.member.mention} has been accepted and given the role.", ephemeral=False)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red, custom_id="decline_app")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        mod_role_id = await self.cog.config.guild(self.channel.guild).moderator_role()
        mod_role = self.channel.guild.get_role(mod_role_id)
        if mod_role not in interaction.user.roles:
            await interaction.response.send_message("Only moderators can use this.", ephemeral=True)
            return
        # Ask for reason
        await interaction.response.send_modal(DeclineReasonModal(self.cog, self.member, self.channel))

class DeclineReasonModal(discord.ui.Modal):
    def __init__(self, cog, member, channel):
        super().__init__(title="Decline Reason")
        self.cog = cog
        self.member = member
        self.channel = channel
        self.add_item(discord.ui.InputText(label="Reason for declining this user", custom_id="reason", style=discord.InputTextStyle.long, required=True))

    async def callback(self, interaction: discord.Interaction):
        reason = self.children[0].value
        await self.cog.config.member(self.member).decline_reason.set(reason)
        await self.cog._move_to_archive(self.channel)
        await interaction.response.send_message(
            f"{self.member.mention} has been declined. Reason stored for future reference.",
            ephemeral=False
        )
        try:
            await self.member.send(f"Your application was declined. Reason: {reason}")
        except Exception:
            pass  # User may have DMs closed

