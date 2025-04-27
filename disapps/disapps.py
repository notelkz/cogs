import discord
from redbot.core import commands, Config, checks
from redbot.core.utils.predicates import MessagePredicate
from redbot.core.utils.menus import start_adding_reactions
from datetime import datetime
import asyncio

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
        
        channel_name = f"{member.name}-application"
        channel = await category.create_text_channel(
            name=channel_name,
            overwrites={
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
        )

        embed = discord.Embed(
            title="Welcome to the Application Process!",
            description="Please click the buttons below to begin.",
            color=discord.Color.blue()
        )
        
        class ApplicationButtons(discord.ui.View):
            def __init__(self, cog):
                super().__init__(timeout=None)
                self.cog = cog

            @discord.ui.button(label="Apply Now", style=discord.ButtonStyle.green)
            async def apply_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                modal = ApplicationModal()
                await interaction.response.send_modal(modal)
                button.disabled = True
                await interaction.message.edit(view=self)

            @discord.ui.button(label="Contact Mod", style=discord.ButtonStyle.red)
            async def contact_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                mod_role_id = await self.cog.config.guild(interaction.guild).mod_role()
                mod_role = interaction.guild.get_role(mod_role_id)
                await interaction.response.send_message(f"{mod_role.mention} - Help needed!")
                button.disabled = True
                await interaction.message.edit(view=self)

        class ApplicationModal(discord.ui.Modal):
            def __init__(self):
                super().__init__(title="Application Form")
                self.add_item(discord.ui.TextInput(label="Age", required=True))
                self.add_item(discord.ui.TextInput(label="Location", required=True))
                self.add_item(discord.ui.TextInput(label="Gaming Username", required=True))

            async def on_submit(self, interaction: discord.Interaction):
                embed = discord.Embed(
                    title="Application Submitted",
                    description="A moderator will review your application shortly.",
                    color=discord.Color.green()
                )
                await interaction.response.send_message(embed=embed)

                # Add moderator buttons
                class ModButtons(discord.ui.View):
                    def __init__(self, cog):
                        super().__init__(timeout=None)
                        self.cog = cog

                    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
                    async def accept_button(self, mod_interaction: discord.Interaction, button: discord.ui.Button):
                        role_id = await self.cog.config.guild(mod_interaction.guild).accepted_role()
                        role = mod_interaction.guild.get_role(role_id)
                        await interaction.user.add_roles(role)
                        await mod_interaction.response.send_message("Application accepted!")

                    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red)
                    async def decline_button(self, mod_interaction: discord.Interaction, button: discord.ui.Button):
                        await mod_interaction.response.send_message("Please provide a reason for declining:")
                        try:
                            reason_msg = await self.cog.bot.wait_for(
                                "message",
                                check=lambda m: m.author == mod_interaction.user,
                                timeout=60.0
                            )
                            await interaction.user.send(f"Your application was declined. Reason: {reason_msg.content}")
                        except asyncio.TimeoutError:
                            await mod_interaction.followup.send("No reason provided, application remains pending.")

                await interaction.channel.send("Moderator Controls:", view=ModButtons(interaction.client.get_cog("DisApps")))

        await channel.send(content=member.mention, embed=embed, view=ApplicationButtons(self))

    @disapps.command()
    async def test(self, ctx):
        """Test the application system with a fake member join"""
        await self.on_member_join(ctx.author)
        await ctx.send("Test application created!")

def setup(bot):
    bot.add_cog(DisApps(bot))
