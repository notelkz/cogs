import discord
from redbot.core import commands, Config
from redbot.core.utils.predicates import MessagePredicate
from typing import Optional
import asyncio

class DisApps(commands.Cog):
    """Discord Application System"""
    
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "recruit_role": None,
            "mod_role": None,
            "application_category": None,
            "game_roles": {},
            "setup_complete": False
        }
        self.config.register_guild(**default_guild)

    class ApplicationForm(discord.ui.Modal):
        def __init__(self, game_roles, *args, **kwargs):
            super().__init__(title="Application Form", *args, **kwargs)
            
            self.add_item(discord.ui.TextInput(
                label="Age",
                placeholder="Enter your age",
                custom_id="age",
                min_length=1,
                max_length=3
            ))
            
            self.add_item(discord.ui.TextInput(
                label="Location",
                placeholder="Enter your location",
                custom_id="location",
                min_length=1,
                max_length=100
            ))
            
            self.add_item(discord.ui.TextInput(
                label="Steam ID",
                placeholder="Enter your Steam ID",
                custom_id="steam_id",
                min_length=1,
                max_length=100
            ))
            
            self.game_roles = game_roles

        async def callback(self, interaction: discord.Interaction):
            embed = discord.Embed(
                title="Application Submission",
                color=discord.Color.blue()
            )
            
            embed.add_field(name="Age", value=self.children[0].value, inline=False)
            embed.add_field(name="Location", value=self.children[1].value, inline=False)
            embed.add_field(name="Steam ID", value=self.children[2].value, inline=False)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            await self.handle_submission(interaction)

    class ApplicationButtons(discord.ui.View):
        def __init__(self, cog, game_roles):
            super().__init__(timeout=None)
            self.cog = cog
            self.game_roles = game_roles

        @discord.ui.button(label="Apply Now", style=discord.ButtonStyle.green)
        async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            modal = DisApps.ApplicationForm(self.game_roles)
            await interaction.response.send_modal(modal)
            button.disabled = True
            await interaction.message.edit(view=self)

        @discord.ui.button(label="Contact Mod", style=discord.ButtonStyle.red)
        async def contact_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            guild = interaction.guild
            mod_role_id = await self.cog.config.guild(guild).mod_role()
            mod_role = guild.get_role(mod_role_id)
            
            online_mods = [member for member in guild.members 
                          if mod_role in member.roles and member.status != discord.Status.offline]
            
            if online_mods:
                mod_mentions = " ".join([mod.mention for mod in online_mods])
                await interaction.response.send_message(
                    f"Contacting online moderators: {mod_mentions}",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"{mod_role.mention} - No moderators are currently online.",
                    ephemeral=True
                )

    @commands.group(aliases=["da"])
    @commands.admin_or_permissions(administrator=True)
    async def disapps(self, ctx):
        """DisApps configuration commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send("Please specify a subcommand. Use `!help disapps` for more information.")

    @disapps.command()
    async def setup(self, ctx):
        """Initial setup for the DisApps system."""
        if await self.config.guild(ctx.guild).setup_complete():
            await ctx.send("Setup has already been completed. Use `!disapps reset` to start over.")
            return

        await ctx.send("Welcome to DisApps setup! Let's configure your application system.")
        
        # Get recruit role
        await ctx.send("Please mention or provide the ID of the recruit role:")
        try:
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=30)
            role = await self.get_role_from_message(ctx, msg)
            if not role:
                await ctx.send("Invalid role. Setup cancelled.")
                return
            await self.config.guild(ctx.guild).recruit_role.set(role.id)
        except asyncio.TimeoutError:
            await ctx.send("Setup timed out.")
            return

        # Get moderator role
        await ctx.send("Please mention or provide the ID of the moderator role:")
        try:
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=30)
            role = await self.get_role_from_message(ctx, msg)
            if not role:
                await ctx.send("Invalid role. Setup cancelled.")
                return
            await self.config.guild(ctx.guild).mod_role.set(role.id)
        except asyncio.TimeoutError:
            await ctx.send("Setup timed out.")
            return

        # Get application category
        await ctx.send("Please provide the ID of the applications category:")
        try:
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=30)
            category = ctx.guild.get_channel(int(msg.content))
            if not isinstance(category, discord.CategoryChannel):
                await ctx.send("Invalid category. Setup cancelled.")
                return
            await self.config.guild(ctx.guild).application_category.set(category.id)
        except (ValueError, asyncio.TimeoutError):
            await ctx.send("Invalid input or setup timed out.")
            return

        # Setup game roles
        await ctx.send("Let's set up game roles. Send 'done' when finished.\nFormat: Game Name | @role")
        game_roles = {}
        while True:
            try:
                msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=30)
                if msg.content.lower() == "done":
                    break
                
                if "|" not in msg.content:
                    await ctx.send("Invalid format. Use: Game Name | @role")
                    continue

                game, role_mention = msg.content.split("|", 1)
                game = game.strip()
                role = await self.get_role_from_message(ctx, msg)
                
                if not role:
                    await ctx.send("Invalid role. Try again.")
                    continue

                game_roles[game] = role.id
                await ctx.send(f"Added {game} with role {role.name}")

            except asyncio.TimeoutError:
                await ctx.send("Setup timed out.")
                return

        await self.config.guild(ctx.guild).game_roles.set(game_roles)
        await self.config.guild(ctx.guild).setup_complete.set(True)
        await ctx.send("Setup completed successfully!")

    @disapps.command()
    async def reset(self, ctx):
        """Reset all DisApps configuration for this server."""
        await ctx.send("Are you sure you want to reset all DisApps configuration? This cannot be undone.\nType `yes` to confirm.")
        
        try:
            msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                timeout=30
            )
            
            if msg.content.lower() == "yes":
                await self.config.guild(ctx.guild).clear()
                await ctx.send("All DisApps configuration has been reset. Use `!disapps setup` to configure again.")
            else:
                await ctx.send("Reset cancelled.")
                
        except asyncio.TimeoutError:
            await ctx.send("Reset timed out.")

    @disapps.command()
    async def test(self, ctx):
        """Test the application system with a fake new member."""
        if not await self.config.guild(ctx.guild).setup_complete():
            await ctx.send("Please complete setup first using `!disapps setup`")
            return

        await self.create_application_channel(ctx.author)
        await ctx.send("Test application channel created!")

    async def create_application_channel(self, member):
        """Create a new application channel for a member."""
        guild = member.guild
        category_id = await self.config.guild(guild).application_category()
        category = guild.get_channel(category_id)
        mod_role_id = await self.config.guild(guild).mod_role()
        mod_role = guild.get_role(mod_role_id)

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            mod_role: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        channel = await category.create_text_channel(
            f"{member.name.lower()}-application",
            overwrites=overwrites
        )

        embed = discord.Embed(
            title="Welcome to Zero Lives Left",
            description="[Your organization description here]",
            color=discord.Color.blue()
        )

        game_roles = await self.config.guild(guild).game_roles()
        view = self.ApplicationButtons(self, game_roles)
        await channel.send(f"{member.mention}", embed=embed, view=view)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Create application channel when a new member joins."""
        if await self.config.guild(member.guild).setup_complete():
            await self.create_application_channel(member)

    async def get_role_from_message(self, ctx, message) -> Optional[discord.Role]:
        """Helper function to get a role from a message."""
        if message.role_mentions:
            return message.role_mentions[0]
        try:
            role_id = int(message.content)
            return ctx.guild.get_role(role_id)
        except ValueError:
            return None

class ModeratorButtons(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild = interaction.guild
        recruit_role_id = await self.cog.config.guild(guild).recruit_role()
        recruit_role = guild.get_role(recruit_role_id)
        
        member = interaction.channel.members[0]  # Get the applicant
        await member.add_roles(recruit_role)
        await interaction.response.send_message(f"Application accepted! {member.mention} has been given the {recruit_role.name} role.")
        
        # Disable both buttons
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.red)
    async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Ask for rejection reason
        await interaction.response.send_message("Please provide the reason for rejection:", ephemeral=True)
        
        try:
            reason_msg = await self.cog.bot.wait_for(
                "message",
                check=lambda m: m.author == interaction.user and m.channel == interaction.channel,
                timeout=60
            )
            
            member = interaction.channel.members[0]  # Get the applicant
            try:
                await member.send(f"Your application has been rejected. Reason: {reason_msg.content}")
            except discord.Forbidden:
                await interaction.channel.send("Could not DM the user with the rejection reason.")
                
            await member.kick(reason=f"Application rejected: {reason_msg.content}")
            await interaction.channel.send(f"Application rejected. User has been kicked.")
            
            # Disable both buttons
            for child in self.children:
                child.disabled = True
            await interaction.message.edit(view=self)
            
        except asyncio.TimeoutError:
            await interaction.channel.send("Rejection timed out.")

def setup(bot):
    bot.add_cog(DisApps(bot))
