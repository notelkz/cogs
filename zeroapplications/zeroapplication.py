import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from typing import Optional
import asyncio

WELCOME_EMBED = {
    "author": {
        "name": "elkz - Founder"
    },
    "description": (
        "Unfortunately, due to timewasters, spam bots and other annoyances we've had to implement an application system in Discord.\n\n"
        "**If you are interested in joining us for any of the games we're currently playing, please click the button below and fill out the short form.**"
    ),
    "fields": [],
    "footer": {
        "text": "If you have any issues, use the 'Contact Moderator' button."
    },
    "timestamp": "2025-05-06T13:06:31.675Z",
    "color": 3447003
}

APPLICATION_QUESTIONS = [
    "How old are you?",
    "Where are you based?",
    "What is your preferred platform? (PC/Console)"
]

def safe_channel_name(name):
    # Remove spaces and special characters, lowercase
    return ''.join(c for c in name.lower().replace(" ", "-") if c.isalnum() or c == "-") + "-application"

class ZeroApplications(commands.Cog):
    """Application management for new users."""

    __version__ = "1.1.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=0xAABBCCDD)
        default_guild = {
            "applications_category": None,
            "archive_category": None,
            "moderator_role": None,
            "application_embed": None,
            "accept_role": None,
        }
        default_member = {
            "application": None,  # dict with application data
            "decline_reason": None,
        }
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        self._application_cache = {}  # {guild_id: {member_id: application_dict}}

    @property
    def applications(self):
        """
        Returns a dict of all applications in all guilds, cached.
        Format: {guild_id: {member_id: application_dict}}
        """
        return self._application_cache

    async def update_application_cache(self):
        """
        Loads all applications from config into the cache.
        """
        all_guilds = self.bot.guilds
        for guild in all_guilds:
            members = guild.members
            guild_cache = {}
            for member in members:
                app = await self.config.member(member).application()
                if app:
                    guild_cache[member.id] = app
            self._application_cache[guild.id] = guild_cache

    async def update_member_application_cache(self, member):
        """
        Updates the cache for a single member.
        """
        app = await self.config.member(member).application()
        if app:
            if member.guild.id not in self._application_cache:
                self._application_cache[member.guild.id] = {}
            self._application_cache[member.guild.id][member.id] = app
        else:
            if member.guild.id in self._application_cache:
                self._application_cache[member.guild.id].pop(member.id, None)

    @commands.Cog.listener()
    async def on_ready(self):
        await self.update_application_cache()

    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def zeroapplications(self, ctx):
        """ZeroApplications Setup"""
        pass

    @zeroapplications.command()
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

        await ctx.send("Setup complete! The application questions are now hardcoded.")

    @zeroapplications.command()
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
        prev_app = await self.config.member(member).application()
        if prev_app:
            await self._reopen_application(member, prev_app)
        else:
            await self._open_new_application(member)

    async def _reopen_application(self, member, prev_app):
        guild = member.guild
        mod_role_id = await self.config.guild(guild).moderator_role()
        mod_role = guild.get_role(mod_role_id)
        app_cat_id = await self.config.guild(guild).applications_category()
        app_cat = guild.get_channel(app_cat_id)
        archive_cat_id = await self.config.guild(guild).archive_category()
        archive_cat = guild.get_channel(archive_cat_id)
        channel_name = safe_channel_name(member.name)

        channel = discord.utils.get(app_cat.channels, name=channel_name)
        if not channel:
            channel = discord.utils.get(archive_cat.channels, name=channel_name)
            if channel:
                await channel.edit(category=app_cat)
                await channel.set_permissions(member, read_messages=True, send_messages=True)
                await channel.set_permissions(mod_role, read_messages=True, send_messages=True)
            else:
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                    mod_role: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                }
                channel = await guild.create_text_channel(channel_name, category=app_cat, overwrites=overwrites)
        else:
            await channel.set_permissions(member, read_messages=True, send_messages=True)
            await channel.set_permissions(mod_role, read_messages=True, send_messages=True)

        await channel.send(f"{member.mention}, you have previously applied. Why are you reapplying or why did you leave before? Please reply below.")

        def check(m): return m.author == member and m.channel == channel
        try:
            msg = await self.bot.wait_for("message", check=check, timeout=300)
            await self._ping_mods(channel, mod_role)
        except asyncio.TimeoutError:
            await channel.send("No response received. Please reapply when ready.")

    async def _open_new_application(self, member):
        guild = member.guild
        app_cat_id = await self.config.guild(guild).applications_category()
        app_cat = guild.get_channel(app_cat_id)
        mod_role_id = await self.config.guild(guild).moderator_role()
        mod_role = guild.get_role(mod_role_id)
        channel_name = safe_channel_name(member.name)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            mod_role: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }
        channel = await guild.create_text_channel(channel_name, category=app_cat, overwrites=overwrites)
        embed_json = await self.config.guild(guild).application_embed()
        if not embed_json or "description" not in embed_json:
            embed_json = WELCOME_EMBED
        embed = discord.Embed.from_dict(embed_json)
        await channel.send(content=member.mention, embed=embed)
        view = ApplicationView(self, member, channel)
        await channel.send(view=view)

    async def _ping_mods(self, channel, mod_role):
        online_mods = [m for m in mod_role.members if m.status != discord.Status.offline]
        if online_mods:
            await channel.send(f"{' '.join(m.mention for m in online_mods)}, attention needed!")
        else:
            await channel.send(f"{mod_role.mention}, attention needed!")

    async def _move_to_archive(self, channel, member=None):
        guild = channel.guild
        archive_cat_id = await self.config.guild(guild).archive_category()
        archive_cat = guild.get_channel(archive_cat_id)
        await channel.edit(category=archive_cat)
        if member:
            overwrite = discord.PermissionOverwrite()
            overwrite.read_messages = False
            overwrite.send_messages = False
            await channel.set_permissions(member, overwrite=overwrite)

    @zeroapplications.command(name="sendembed")
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def sendembed(self, ctx, channel_id: int):
        """
        Send the application embed with buttons to a specified channel by channel ID.
        Usage: !zeroapplications sendembed <channel_id>
        """
        channel = ctx.guild.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            await ctx.send("That is not a valid text channel ID in this server.")
            return

        hashtag = "#welcome"

        embed = discord.Embed(
            title="Welcome",
            description=(
                "Please use the button below to create a new application if one wasn't created for you automatically upon you joining the Discord.\n\n"
                "This can also be used if <@&1274512593842606080> wish to join and become a full <@&1018116224158273567>."
            ),
            color=6962372,
            timestamp=discord.utils.parse_time("2025-05-08T12:07:00.000Z")
        )
        embed.set_author(name="Zero Lives Left", url="http://zerolivesleft.net")
        embed.set_footer(text="If you have any issues, please use the Contact Moderator button.")
        embed.set_thumbnail(url="attachment://zerosmall.png")

        await channel.send(content=hashtag, embed=embed, view=PublicApplicationView(self, ctx.guild))
        await ctx.send(f"Embed sent to {channel.mention} with hashtag {hashtag}.")

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
        modal = ApplicationModal(self.cog, self.member, self.channel, self)
        await interaction.response.send_modal(modal)
        # Do NOT disable the button here!

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
    def __init__(self, cog, member, channel, view):
        super().__init__(title="Application Form")
        self.cog = cog
        self.member = member
        self.channel = channel
        self.view = view
        self.questions = APPLICATION_QUESTIONS
        for i, q in enumerate(self.questions):
            self.add_item(discord.ui.TextInput(label=q, custom_id=f"q{i}", style=discord.TextStyle.short, required=True))

    async def on_submit(self, interaction: discord.Interaction):
        answers = {q: self.children[i].value for i, q in enumerate(self.questions)}
        await self.cog.config.member(self.member).application.set(answers)
        await self.cog.update_member_application_cache(self.member)
        await interaction.response.send_message(
            "Your application has been submitted! Moderators will review it soon.",
            ephemeral=True
        )

        # Disable the Apply button in the original view
        for child in self.view.children:
            if isinstance(child, discord.ui.Button) and child.custom_id == "apply_to_join":
                child.disabled = True
        # Edit the original message (the one with the view)
        try:
            async for msg in self.channel.history(limit=20):
                if msg.components:
                    await msg.edit(view=self.view)
                    break
        except Exception:
            pass

        # Post answers for mods
        answer_lines = [f"**{q}**\n{a}" for q, a in answers.items()]
        answer_text = "\n\n".join(answer_lines)
        await self.channel.send(
            f"**Application from {self.member.mention}:**\n\n{answer_text}"
        )

        mod_role_id = await self.cog.config.guild(self.channel.guild).moderator_role()
        mod_role = self.channel.guild.get_role(mod_role_id)
        await self.cog._ping_mods(self.channel, mod_role)
        view = ModReviewView(self.cog, self.member, self.channel)
        await self.channel.send(
            f"Moderators, please review the application for {self.member.mention}.", view=view
        )

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
        await self.cog._move_to_archive(self.channel, self.member)
        await self.cog.config.member(self.member).application.set(None)
        await self.cog.update_member_application_cache(self.member)
        await interaction.response.send_message(f"{self.member.mention} has been accepted and given the role.", ephemeral=False)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red, custom_id="decline_app")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        mod_role_id = await self.cog.config.guild(self.channel.guild).moderator_role()
        mod_role = self.channel.guild.get_role(mod_role_id)
        if mod_role not in interaction.user.roles:
            await interaction.response.send_message("Only moderators can use this.", ephemeral=True)
            return
        await interaction.response.send_modal(DeclineReasonModal(self.cog, self.member, self.channel))

class DeclineReasonModal(discord.ui.Modal):
    def __init__(self, cog, member, channel):
        super().__init__(title="Decline Reason")
        self.cog = cog
        self.member = member
        self.channel = channel
        self.add_item(discord.ui.TextInput(
            label="Reason for declining this user",
            custom_id="reason",
            style=discord.TextStyle.long,
            required=True
        ))

    async def on_submit(self, interaction: discord.Interaction):
        reason = self.children[0].value
        await self.cog.config.member(self.member).decline_reason.set(reason)
        await self.cog._move_to_archive(self.channel, self.member)
        await self.cog.config.member(self.member).application.set(None)
        await self.cog.update_member_application_cache(self.member)
        await interaction.response.send_message(
            f"{self.member.mention} has been declined. Reason stored for future reference.",
            ephemeral=False
        )
        await self.channel.send(
            f"**{self.member.mention}'s application was declined.**\n**Reason:** {reason}"
        )
        try:
            await self.member.send(f"Your application was declined. Reason: {reason}")
        except Exception:
            pass  # User may have DMs closed

class PublicApplicationView(discord.ui.View):
    def __init__(self, cog, guild):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild = guild

    @discord.ui.button(label="Create New Application", style=discord.ButtonStyle.green, custom_id="public_create_app")
    async def create_app(self, interaction: discord.Interaction, button: discord.ui.Button):
        member = interaction.user
        prev_app = await self.cog.config.member(member).application()
        if prev_app:
            await self.cog._reopen_application(member, prev_app)
            await interaction.response.send_message("Your previous application has been reopened (check your application channel).", ephemeral=True)
        else:
            await self.cog._open_new_application(member)
            await interaction.response.send_message("A new application channel has been created for you.", ephemeral=True)

    @discord.ui.button(label="Contact Moderator", style=discord.ButtonStyle.red, custom_id="public_contact_mod")
    async def contact_mod(self, interaction: discord.Interaction, button: discord.ui.Button):
        mod_role_id = await self.cog.config.guild(self.guild).moderator_role()
        mod_role = self.guild.get_role(mod_role_id)
        channel = interaction.channel
        online_mods = [m for m in mod_role.members if m.status != discord.Status.offline]
        if online_mods:
            await channel.send(f"{' '.join(m.mention for m in online_mods)}, attention needed!")
        else:
            await channel.send(f"{mod_role.mention}, attention needed!")
        await interaction.response.send_message("Moderators have been notified.", ephemeral=True)

# --- End of cog file ---

async def setup(bot):
    await bot.add_cog(ZeroApplications(bot))
