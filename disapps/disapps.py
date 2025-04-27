import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from discord.ui import Button, View, Modal, TextInput
from discord import ButtonStyle
import aiohttp
from datetime import datetime

class ApplicationModal(Modal):
    def __init__(self):
        super().__init__(title="Rejection Reason")
        self.reason = TextInput(
            label="Reason for rejection",
            style=discord.TextStyle.paragraph,
            placeholder="Enter the reason for rejection...",
            required=True
        )
        self.add_item(self.reason)

class DisApps(commands.Cog):
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "application_category": None,
            "approved_role": None,
            "mod_role": None
        }
        self.config.register_guild(**default_guild)

    @commands.group()
    @commands.admin()
    async def disapps(self, ctx):
        """Configuration commands for the DisApps system"""
        pass

    @disapps.command(name="setcategory")
    async def disapps_setcategory(self, ctx, category: discord.CategoryChannel):
        """Set the applications category"""
        await self.config.guild(ctx.guild).application_category.set(category.id)
        await ctx.send(f"Applications category set to {category.name}")

    @disapps.command(name="setrole")
    async def disapps_setrole(self, ctx, role: discord.Role):
        """Set the approved role"""
        await self.config.guild(ctx.guild).approved_role.set(role.id)
        await ctx.send(f"Approved role set to {role.name}")

    @disapps.command(name="setmodrole")
    async def disapps_setmodrole(self, ctx, role: discord.Role):
        """Set the moderator role"""
        await self.config.guild(ctx.guild).mod_role.set(role.id)
        await ctx.send(f"Moderator role set to {role.name}")

    async def create_application_channel(self, guild, user):
        category_id = await self.config.guild(guild).application_category()
        if not category_id:
            return None

        category = guild.get_channel(category_id)
        if not category:
            return None

        channel_name = f"{user.name.lower()}-application"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        mod_role_id = await self.config.guild(guild).mod_role()
        if mod_role_id:
            mod_role = guild.get_role(mod_role_id)
            if mod_role:
                overwrites[mod_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        channel = await category.create_text_channel(channel_name, overwrites=overwrites)
        return channel

    async def create_application_embed(self, user):
        embed = discord.Embed(
            title="Zero Lives Left Application",
            description="[Application information will go here]",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text=f"Application for {user.name}")
        return embed

    async def create_application_buttons(self):
        class ApplicationView(View):
            def __init__(self, cog):
                super().__init__(timeout=None)
                self.cog = cog

            @discord.ui.button(label="Accept", style=ButtonStyle.green)
            async def accept_button(self, interaction: discord.Interaction, button: Button):
                if not await self.cog.check_mod_permissions(interaction.user, interaction.guild):
                    await interaction.response.send_message("You don't have permission to do this.", ephemeral=True)
                    return

                role_id = await self.cog.config.guild(interaction.guild).approved_role()
                if not role_id:
                    await interaction.response.send_message("Approved role not set.", ephemeral=True)
                    return

                role = interaction.guild.get_role(role_id)
                member = interaction.guild.get_member(int(interaction.channel.name.split('-')[0]))
                if member and role:
                    await member.add_roles(role)
                    await interaction.response.send_message(f"Application approved! Role {role.name} added to {member.name}")
                    await interaction.channel.send(f"{member.mention}, your application has been approved!")

            @discord.ui.button(label="Reject", style=ButtonStyle.red)
            async def reject_button(self, interaction: discord.Interaction, button: Button):
                if not await self.cog.check_mod_permissions(interaction.user, interaction.guild):
                    await interaction.response.send_message("You don't have permission to do this.", ephemeral=True)
                    return

                modal = ApplicationModal()
                await interaction.response.send_modal(modal)
                await modal.wait()
                
                member = interaction.guild.get_member(int(interaction.channel.name.split('-')[0]))
                if member:
                    await interaction.channel.send(f"{member.mention}, your application has been rejected.\nReason: {modal.reason.value}")

        return ApplicationView(self)

    async def check_mod_permissions(self, user, guild):
        mod_role_id = await self.config.guild(guild).mod_role()
        if not mod_role_id:
            return False
        
        mod_role = guild.get_role(mod_role_id)
        return mod_role in user.roles

    @commands.Cog.listener()
    async def on_member_join(self, member):
        channel = await self.create_application_channel(member.guild, member)
        if channel:
            embed = await self.create_application_embed(member)
            view = await self.create_application_buttons()
            await channel.send(f"{member.mention}", embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(DisApps(bot))
