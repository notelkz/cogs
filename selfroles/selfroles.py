import discord
from redbot.core import commands, Config
from discord.ui import Button, View
from typing import Optional
import asyncio

class SelfRoleButton(Button):
    def __init__(self, role_id: str, label: str, style: discord.ButtonStyle, emoji: Optional[str] = None):
        super().__init__(label=label, style=style, emoji=emoji)
        self.role_id = role_id

    async def callback(self, interaction: discord.Interaction):
        try:
            member = interaction.user
            role = interaction.guild.get_role(int(self.role_id))
            
            if not role:
                await interaction.response.send_message("Role not found!", ephemeral=True)
                return
                
            if role in member.roles:
                await member.remove_roles(role, reason="Self-role removed")
                await interaction.response.send_message(f"Removed role: {role.name}", ephemeral=True)
            else:
                await member.add_roles(role, reason="Self-role added")
                await interaction.response.send_message(f"Added role: {role.name}", ephemeral=True)
                
        except Exception as e:
            await interaction.response.send_message(f"Error: {e}", ephemeral=True)

class SelfRoleView(View):
    def __init__(self, roles_config: dict):
        super().__init__(timeout=None)
        # Add buttons for each role
        for role_key, role_id in roles_config.items():
            # Create a button for each role
            button = SelfRoleButton(
                role_id=role_id,
                label=role_key.title(),
                style=discord.ButtonStyle.secondary
            )
            self.add_item(button)

class SelfRoles(commands.Cog):
    """Self-role management system"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        
        default_guild = {
            "roles": {}
        }
        
        self.config.register_guild(**default_guild)

    @commands.group(name="selfrole", aliases=["srole", "roles"])
    @commands.guild_only()
    async def selfrole(self, ctx):
        """Self-role management commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @selfrole.command(name="add")
    @commands.has_permissions(manage_roles=True)
    async def selfrole_add(self, ctx, role: discord.Role):
        """Add a role to the self-role system"""
        async with self.config.guild(ctx.guild).roles() as roles:
            roles[role.name.lower()] = str(role.id)
        await ctx.send(f"Added role **{role.name}** to self-role system")

    @selfrole.command(name="post")
    @commands.has_permissions(manage_roles=True)
    async def selfrole_post(self, ctx, channel: discord.TextChannel = None):
        """Post the self-role buttons in a channel"""
        roles = await self.config.guild(ctx.guild).roles()
        
        if not roles:
            await ctx.send("No self-roles configured. Use `selfrole add` to add some.")
            return

        try:
            view = SelfRoleView(roles)
            message = await (channel or ctx.channel).send("Select your roles:", view=view)
            await ctx.send("Self-role buttons posted successfully!")
        except Exception as e:
            await ctx.send(f"Error posting buttons: {e}")

def setup(bot):
    bot.add_cog(SelfRoles(bot))
