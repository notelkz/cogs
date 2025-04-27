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
            "setup_complete": False,
            "user_channels": {}  # Store user ID -> channel ID mapping
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

    async def get_or_create_application_channel(self, member):
        """Get existing application channel or create a new one."""
        guild = member.guild
        
        # Get stored channel data
        user_channels = await self.config.guild(guild).user_channels()
        
        # Check if user already has a channel
        if str(member.id) in user_channels:
            channel_id = user_channels[str(member.id)]
            channel = guild.get_channel(channel_id)
            
            if channel:
                # Channel exists, update permissions and return it
                mod_role_id = await self.config.guild(guild).mod_role()
                mod_role = guild.get_role(mod_role_id)
                
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(read_messages=False),
                    member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                    mod_role: discord.PermissionOverwrite(read_messages=True, send_messages=True)
                }
                
                await channel.edit(overwrites=overwrites)
                return channel
            else:
                # Channel was deleted, remove from storage
                user_channels.pop(str(member.id))
                await self.config.guild(guild).user_channels.set(user_channels)
        
        # Create new channel
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
        
        # Store the new channel
        user_channels[str(member.id)] = channel.id
        await self.config.guild(guild).user_channels.set(user_channels)
        
        return channel

    async def create_application_channel(self, member):
        """Create or get existing application channel for a member."""
        channel = await self.get_or_create_application_channel(member)
        
        embed = discord.Embed(
            title="Welcome to Zero Lives Left",
            description="[Your organization description here]",
            color=discord.Color.blue()
        )

        game_roles = await self.config.guild(member.guild).game_roles()
        view = self.ApplicationButtons(self, game_roles)
        await channel.send(f"{member.mention}", embed=embed, view=view)

    @commands.group(aliases=["da"])
    @commands.admin_or_permissions(administrator=True)
    async def disapps(self, ctx):
        """DisApps configuration commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send("Please specify a subcommand. Use `!help disapps` for more information.")

    @disapps.command()
    async def cleanup(self, ctx):
        """Clean up deleted channels from the storage."""
        user_channels = await self.config.guild(ctx.guild).user_channels()
        cleaned = 0
        
        for user_id, channel_id in list(user_channels.items()):
            channel = ctx.guild.get_channel(channel_id)
            if not channel:
                user_channels.pop(user_id)
                cleaned += 1
        
        await self.config.guild(ctx.guild).user_channels.set(user_channels)
        await ctx.send(f"Cleaned up {cleaned} deleted channel(s) from storage.")

    @disapps.command()
    async def channels(self, ctx):
        """List all application channels."""
        user_channels = await self.config.guild(ctx.guild).user_channels()
        
        if not user_channels:
            await ctx.send("No application channels found.")
            return
        
        embed = discord.Embed(
            title="Application Channels",
            color=discord.Color.blue()
        )
        
        for user_id, channel_id in user_channels.items():
            channel = ctx.guild.get_channel(channel_id)
            user = ctx.guild.get_member(int(user_id))
            
            if channel and user:
                embed.add_field(
                    name=f"{user.name}",
                    value=f"Channel: {channel.mention}",
                    inline=False
                )
        
        await ctx.send(embed=embed)

    @disapps.command()
    async def delchannel(self, ctx, user: discord.Member):
        """Delete a user's application channel."""
        user_channels = await self.config.guild(ctx.guild).user_channels()
        
        if str(user.id) not in user_channels:
            await ctx.send(f"No application channel found for {user.name}.")
            return
        
        channel_id = user_channels[str(user.id)]
        channel = ctx.guild.get_channel(channel_id)
        
        if channel:
            await channel.delete()
        
        user_channels.pop(str(user.id))
        await self.config.guild(ctx.guild).user_channels.set(user_channels)
        await ctx.send(f"Deleted application channel for {user.name}.")

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
