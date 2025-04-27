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

    class ModeratorButtons(discord.ui.View):
        def __init__(self, cog):
            super().__init__(timeout=None)
            self.cog = cog

        @discord.ui.button(label="Accept", style=discord.ButtonStyle.green, custom_id="accept_button")
        async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not interaction.permissions.manage_roles:
                await interaction.response.send_message("You don't have permission to accept applications.", ephemeral=True)
                return

            guild = interaction.guild
            recruit_role_id = await self.cog.config.guild(guild).recruit_role()
            recruit_role = guild.get_role(recruit_role_id)
            
            member = [m for m in interaction.channel.members if not m.bot][0]
            await member.add_roles(recruit_role)
            await interaction.response.send_message(
                f"Application accepted! {member.mention} has been given the {recruit_role.name} role.",
                allowed_mentions=discord.AllowedMentions(users=True)
            )
            
            for child in self.children:
                child.disabled = True
            await interaction.message.edit(view=self)

        @discord.ui.button(label="Reject", style=discord.ButtonStyle.red, custom_id="reject_button")
        async def reject_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not interaction.permissions.kick_members:
                await interaction.response.send_message("You don't have permission to reject applications.", ephemeral=True)
                return

            await interaction.response.send_message("Please provide the reason for rejection:", ephemeral=True)
            
            try:
                reason_msg = await self.cog.bot.wait_for(
                    "message",
                    check=lambda m: m.author == interaction.user and m.channel == interaction.channel,
                    timeout=60
                )
                
                member = [m for m in interaction.channel.members if not m.bot][0]
                try:
                    await member.send(f"Your application has been rejected. Reason: {reason_msg.content}")
                except discord.Forbidden:
                    await interaction.followup.send("Could not DM the user with the rejection reason.")
                    
                await member.kick(reason=f"Application rejected: {reason_msg.content}")
                await interaction.followup.send(f"Application rejected. User has been kicked.")
                
                for child in self.children:
                    child.disabled = True
                await interaction.message.edit(view=self)
                
            except asyncio.TimeoutError:
                await interaction.followup.send("Rejection timed out.")

    class ApplicationForm(discord.ui.Modal):
        def __init__(self, game_roles):
            super().__init__(title="Application Form")
            
            self.game_roles = game_roles
            
            self.age = discord.ui.TextInput(
                label="Age",
                placeholder="Enter your age",
                min_length=1,
                max_length=3,
                required=True
            )
            self.add_item(self.age)
            
            self.location = discord.ui.TextInput(
                label="Location",
                placeholder="Enter your location",
                min_length=1,
                max_length=100,
                required=True
            )
            self.add_item(self.location)
            
            self.steam_id = discord.ui.TextInput(
                label="Steam ID",
                placeholder="Enter your Steam ID",
                min_length=1,
                max_length=100,
                required=True
            )
            self.add_item(self.steam_id)
            
            self.games = discord.ui.TextInput(
                label="Games",
                placeholder="List the games you play (separate with commas)",
                style=discord.TextStyle.paragraph,
                required=True
            )
            self.add_item(self.games)

        async def on_submit(self, interaction: discord.Interaction):
            try:
                embed = discord.Embed(
                    title="New Application Submission",
                    color=discord.Color.blue(),
                    timestamp=discord.utils.utcnow()
                )
                
                embed.add_field(name="Applicant", value=interaction.user.mention, inline=False)
                embed.add_field(name="Age", value=self.age.value, inline=True)
                embed.add_field(name="Location", value=self.location.value, inline=True)
                embed.add_field(name="Steam ID", value=self.steam_id.value, inline=True)
                embed.add_field(name="Games", value=self.games.value, inline=False)
                
                # Create moderator buttons view
                mod_view = DisApps.ModeratorButtons(interaction.client.get_cog("DisApps"))
                
                # Send confirmation to applicant
                await interaction.response.send_message("Your application has been submitted!", ephemeral=True)
                
                # Get mod role and ping online mods
                guild = interaction.guild
                cog = interaction.client.get_cog("DisApps")
                mod_role_id = await cog.config.guild(guild).mod_role()
                mod_role = guild.get_role(mod_role_id)
                
                online_mods = [member for member in guild.members 
                              if mod_role in member.roles and member.status != discord.Status.offline]
                
                if online_mods:
                    mod_ping = " ".join([mod.mention for mod in online_mods])
                else:
                    mod_ping = mod_role.mention
                
                # Send application to channel
                await interaction.channel.send(
                    f"{mod_ping} - New application submitted!",
                    embed=embed,
                    view=mod_view
                )
                
            except Exception as e:
                print(f"Error in application submission: {str(e)}")  # For debugging
                await interaction.response.send_message(
                    "An error occurred while processing your application. Please try again.",
                    ephemeral=True
                )

        async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
            print(f"Modal error: {str(error)}")  # For debugging
            await interaction.response.send_message(
                "An error occurred while processing your application. Please try again.",
                ephemeral=True
            )

    class ApplicationButtons(discord.ui.View):
        def __init__(self, cog, game_roles):
            super().__init__(timeout=None)
            self.cog = cog
            self.game_roles = game_roles

        @discord.ui.button(label="Apply Now", style=discord.ButtonStyle.green, custom_id="apply_button")
        async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            try:
                # Create and send the application form
                modal = DisApps.ApplicationForm(self.game_roles)
                await interaction.response.send_modal(modal)
                
                # Disable the apply button after submission
                button.disabled = True
                await interaction.message.edit(view=self)
                
            except Exception as e:
                print(f"Error in apply button: {str(e)}")  # For debugging
                await interaction.response.send_message(
                    "An error occurred while opening the application form. Please try again.",
                    ephemeral=True
                )

        @discord.ui.button(label="Contact Mod", style=discord.ButtonStyle.red, custom_id="contact_button")
        async def contact_button(self, interaction: discord.Interaction, button: discord.ui.Button):
            try:
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
                        allowed_mentions=discord.AllowedMentions(roles=True)
                    )
                    
            except Exception as e:
                print(f"Error in contact button: {str(e)}")  # For debugging
                await interaction.response.send_message(
                    "An error occurred while contacting moderators. Please try again.",
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

def setup(bot):
    bot.add_cog(DisApps(bot))
