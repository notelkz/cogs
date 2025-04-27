import discord
from redbot.core import commands, Config
from redbot.core.utils.predicates import MessagePredicate
from typing import Optional
import asyncio

class DisApps(commands.Cog):
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

    @commands.group(aliases=["da"])
    @commands.admin_or_permissions(administrator=True)
    async def disapps(self, ctx):
        """DisApps configuration commands."""
        pass

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

        apply_button = discord.ui.Button(style=discord.ButtonStyle.green, label="Apply Now")
        contact_mod_button = discord.ui.Button(style=discord.ButtonStyle.red, label="Contact Mod")

        view = discord.ui.View()
        view.add_item(apply_button)
        view.add_item(contact_mod_button)

        await channel.send(f"{member.mention}", embed=embed, view=view)

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
