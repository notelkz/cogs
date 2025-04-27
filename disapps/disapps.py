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
            "setup_complete": False,
            "applications_category": None,
            "recruit_role": None,
            "moderator_role": None,
            "game_roles": {},
            "active_applications": {}
        }
        self.config.register_guild(**default_guild)

    @commands.group(aliases=["da"])
    @commands.admin_or_permissions(administrator=True)
    async def disapps(self, ctx):
        """DisApps management commands"""
        pass

    @disapps.command()
    async def setup(self, ctx):
        """Initial setup for the DisApps system"""
        guild = ctx.guild
        
        await ctx.send("Starting DisApps setup...\n\nPlease mention or provide the ID of the Applications category:")
        try:
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=60)
            try:
                category_id = int(msg.content)
                category = discord.utils.get(guild.categories, id=category_id)
            except ValueError:
                await ctx.send("Invalid category ID. Setup cancelled.")
                return
            
            if not category:
                await ctx.send("Category not found. Setup cancelled.")
                return
            
            await self.config.guild(guild).applications_category.set(category.id)
            
            await ctx.send("Please mention or provide the ID of the Recruit role:")
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=60)
            role = None
            
            try:
                role_id = int(''.join(filter(str.isdigit, msg.content)))
                role = guild.get_role(role_id)
            except ValueError:
                await ctx.send("Invalid role ID. Setup cancelled.")
                return
                
            if not role:
                await ctx.send("Role not found. Setup cancelled.")
                return
                
            await self.config.guild(guild).recruit_role.set(role.id)
            
            await ctx.send("Please mention or provide the ID of the Moderator role:")
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=60)
            mod_role = None
            
            try:
                role_id = int(''.join(filter(str.isdigit, msg.content)))
                mod_role = guild.get_role(role_id)
            except ValueError:
                await ctx.send("Invalid role ID. Setup cancelled.")
                return
                
            if not mod_role:
                await ctx.send("Role not found. Setup cancelled.")
                return
                
            await self.config.guild(guild).moderator_role.set(mod_role.id)
            
            # Game roles setup
            game_roles = {}
            await ctx.send("Now, let's set up game roles. Type 'done' when finished.\nEnter game name followed by role mention/ID (e.g., 'Minecraft @MinecraftRole'):")
            
            while True:
                msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author, timeout=60)
                
                if msg.content.lower() == 'done':
                    break
                    
                try:
                    game_name, role_mention = msg.content.rsplit(" ", 1)
                    role_id = int(''.join(filter(str.isdigit, role_mention)))
                    role = guild.get_role(role_id)
                    
                    if role:
                        game_roles[game_name] = role.id
                        await ctx.send(f"Added {game_name} with role {role.name}")
                    else:
                        await ctx.send("Role not found, skipping...")
                except:
                    await ctx.send("Invalid format, skipping...")
            
            await self.config.guild(guild).game_roles.set(game_roles)
            await self.config.guild(guild).setup_complete.set(True)
            await ctx.send("Setup complete! You can now use the application system.")
            
        except asyncio.TimeoutError:
            await ctx.send("Setup timed out. Please try again.")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        guild = member.guild
        if not await self.config.guild(guild).setup_complete():
            return
            
        category_id = await self.config.guild(guild).applications_category()
        category = guild.get_channel(category_id)
        
        if not category:
            return
            
        # Create application channel
        channel_name = f"{member.name.lower()}-application"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        
        mod_role_id = await self.config.guild(guild).moderator_role()
        mod_role = guild.get_role(mod_role_id)
        if mod_role:
            overwrites[mod_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            
        channel = await category.create_text_channel(channel_name, overwrites=overwrites)
        
        # Send welcome message and buttons
        embed = discord.Embed(
            title="Welcome to Zero Lives Left",
            description="Information about Zero Lives Left will be displayed here.",
            color=discord.Color.blue()
        )
        
        apply_button = discord.ui.Button(style=discord.ButtonStyle.green, label="Apply Now", custom_id="apply_now")
        contact_mod_button = discord.ui.Button(style=discord.ButtonStyle.red, label="Contact Mod", custom_id="contact_mod")
        
        view = discord.ui.View()
        view.add_item(apply_button)
        view.add_item(contact_mod_button)
        
        await channel.send(f"{member.mention}", embed=embed, view=view)

    @disapps.command()
    async def test(self, ctx):
        """Test the application system with a fake member"""
        if not await self.config.guild(ctx.guild).setup_complete():
            await ctx.send("Please complete setup first using !disapps setup")
            return
            
        class FakeMember:
            def __init__(self, guild):
                self.name = "test_user"
                self.guild = guild
                self.mention = "@test_user"
                
        fake_member = FakeMember(ctx.guild)
        await self.on_member_join(fake_member)
        await ctx.send("Test application channel created!")

async def setup(bot):
    await bot.add_cog(DisApps(bot))
