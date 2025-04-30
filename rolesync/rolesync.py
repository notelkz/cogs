from redbot.core import commands, Config
from redbot.core.bot import Red
import discord
import aiohttp
import json

class RoleSync(commands.Cog):
    """Sync roles between website and Discord"""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1274316388424745030)
        self.website_url = "https://notelkz.net/zerolivesleft"  # Your website URL
        
        # Define assignable roles
        self.assignable_roles = {
            "1274316388424745030": "Battlefield 2042"  # Add more roles as needed
        }

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        """Listen for role changes and sync them"""
        if before.roles != after.roles:
            # Get the roles that changed
            added_roles = set(after.roles) - set(before.roles)
            removed_roles = set(before.roles) - set(after.roles)
            
            # Log role changes
            if added_roles:
                await self.log_role_changes(after, added_roles, "added")
            if removed_roles:
                await self.log_role_changes(after, removed_roles, "removed")

    @commands.group(name="rolesync")
    @commands.admin()
    async def rolesync(self, ctx):
        """Role sync commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send("Please specify a subcommand. Use `[p]help rolesync` for more info.")

    @rolesync.command(name="add")
    async def add_role(self, ctx, member: discord.Member, role: discord.Role):
        """Add a role to a member"""
        if str(role.id) in self.assignable_roles:
            try:
                await member.add_roles(role)
                await ctx.send(f"Added {role.name} to {member.name}")
            except discord.Forbidden:
                await ctx.send("I don't have permission to manage roles.")
        else:
            await ctx.send("This role cannot be self-assigned.")

    @rolesync.command(name="remove")
    async def remove_role(self, ctx, member: discord.Member, role: discord.Role):
        """Remove a role from a member"""
        if str(role.id) in self.assignable_roles:
            try:
                await member.remove_roles(role)
                await ctx.send(f"Removed {role.name} from {member.name}")
            except discord.Forbidden:
                await ctx.send("I don't have permission to manage roles.")
        else:
            await ctx.send("This role cannot be self-managed.")

    @rolesync.command(name="sync")
    async def sync_roles(self, ctx, member: discord.Member = None):
        """Sync roles for a member or yourself"""
        target = member or ctx.author
        roles = [role.id for role in target.roles]
        await ctx.send(f"Syncing roles for {target.name}...")
        # Here you would implement the website sync logic

    async def log_role_changes(self, member: discord.Member, roles, action: str):
        """Log role changes to a channel"""
        log_channel = await self.config.guild(member.guild).log_channel()
        if log_channel:
            channel = self.bot.get_channel(log_channel)
            if channel:
                role_names = ", ".join(role.name for role in roles)
                await channel.send(f"Role {action}: {member.name} - {role_names}")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Handle role reactions"""
        if str(payload.emoji) in self.role_emojis:
            guild = self.bot.get_guild(payload.guild_id)
            member = guild.get_member(payload.user_id)
            role_id = self.role_emojis[str(payload.emoji)]
            role = guild.get_role(role_id)
            if role:
                await member.add_roles(role)

def setup(bot):
    bot.add_cog(RoleSync(bot))
