import discord
from redbot.core import commands, Config
from discord.ui import Button, View
from typing import Optional
import asyncio

class PlatformView(View):
    def __init__(self, roles: dict):
        super().__init__(timeout=None)
        
        # PC/Steam (Dark blue and black)
        if roles.get('pc'):
            self.add_item(Button(
                style=discord.ButtonStyle.primary,
                label="PC",
                custom_id="platform_pc"
            ))
            
        # Nintendo (Red)
        if roles.get('nintendo'):
            self.add_item(Button(
                style=discord.ButtonStyle.danger,
                label="Nintendo",
                custom_id="platform_nintendo"
            ))
            
        # PlayStation (Blue)
        if roles.get('playstation'):
            self.add_item(Button(
                style=discord.ButtonStyle.primary,
                label="PlayStation",
                custom_id="platform_playstation"
            ))
            
        # Xbox (Green)
        if roles.get('xbox'):
            self.add_item(Button(
                style=discord.ButtonStyle.success,
                label="Xbox",
                custom_id="platform_xbox"
            ))

class Platforms(commands.Cog):
    """Gaming platform role selection"""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567891)
        default_guild = {
            "message_id": None,
            "channel_id": None,
            "roles": {
                "pc": None,
                "nintendo": None,
                "playstation": None,
                "xbox": None
            }
        }
        self.config.register_guild(**default_guild)
        
        # Start listening for button interactions
        bot.add_listener(self.button_listener, "on_interaction")

    async def button_listener(self, interaction: discord.Interaction):
        if not interaction.data or "custom_id" not in interaction.data:
            return
            
        custom_id = interaction.data["custom_id"]
        if not custom_id.startswith("platform_"):
            return
            
        platform = custom_id.split("_")[1]
        roles = await self.config.guild(interaction.guild).roles()
        role_id = roles.get(platform)
        
        if not role_id:
            await interaction.response.send_message("Role not configured.", ephemeral=True)
            return
            
        role = interaction.guild.get_role(role_id)
        if not role:
            await interaction.response.send_message("Role not found.", ephemeral=True)
            return
            
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role)
            await interaction.response.send_message(f"Removed {role.name} role.", ephemeral=True)
        else:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(f"Added {role.name} role.", ephemeral=True)

    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def platforms(self, ctx):
        """Gaming platform role management"""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @platforms.command(name="setup")
    async def setup_platforms(self, ctx):
        """Interactive setup for platform roles"""
        await ctx.send("Starting platform roles setup...\n"
                      "For each platform, mention the role or enter its ID (or 'skip' to disable):")
        
        platforms = {
            "PC": "pc",
            "Nintendo": "nintendo",
            "PlayStation": "playstation",
            "Xbox": "xbox"
        }
        
        roles = {}
        for platform_name, platform_key in platforms.items():
            await ctx.send(f"Please mention the role or enter the role ID for {platform_name}:")
            
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                
                if msg.content.lower() == "skip":
                    roles[platform_key] = None
                    await ctx.send(f"Skipped {platform_name} role.")
                    continue
                
                role_id = None
                if msg.role_mentions:
                    role_id = msg.role_mentions[0].id
                else:
                    try:
                        role_id = int(msg.content)
                    except ValueError:
                        await ctx.send(f"Invalid role for {platform_name}, skipping.")
                        continue
                
                if role_id:
                    role = ctx.guild.get_role(role_id)
                    if role:
                        roles[platform_key] = role_id
                        await ctx.send(f"Set {platform_name} role to {role.name}")
                    else:
                        await ctx.send(f"Could not find role with ID {role_id}, skipping.")
                        roles[platform_key] = None
                
            except asyncio.TimeoutError:
                await ctx.send(f"Timed out, skipping {platform_name}.")
                roles[platform_key] = None
        
        await self.config.guild(ctx.guild).roles.set(roles)
        await ctx.send("Setup complete! Use `!platforms post` to create the role selection message.")

    @platforms.command(name="post")
    async def post_platform_message(self, ctx, channel: Optional[discord.TextChannel] = None):
        """Post the platform selection message"""
        if channel is None:
            channel = ctx.channel
            
        roles = await self.config.guild(ctx.guild).roles()
        
        embed = discord.Embed(
            title="Gaming Platforms",
            description="Select your gaming platforms to access their specific channels!\n"
                       "Click a button below to add or remove the role.",
            color=discord.Color.blurple()
        )
        
        # Add fields for each configured platform
        platform_emojis = {
            "pc": "🖥️",
            "nintendo": "🎮",
            "playstation": "🎮",
            "xbox": "🎮"
        }
        
        for platform, role_id in roles.items():
            if role_id:
                role = ctx.guild.get_role(role_id)
                if role:
                    emoji = platform_emojis.get(platform, "")
                    embed.add_field(
                        name=f"{emoji} {platform.title()}",
                        value=f"Role: {role.mention}",
                        inline=True
                    )
        
        view = PlatformView(roles)
        message = await channel.send(embed=embed, view=view)
        
        # Save message and channel ID for future reference
        await self.config.guild(ctx.guild).message_id.set(message.id)
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        
        await ctx.send("Platform selection message posted!")

    @platforms.command(name="update")
    async def update_platform_message(self, ctx):
        """Update the existing platform selection message"""
        message_id = await self.config.guild(ctx.guild).message_id()
        channel_id = await self.config.guild(ctx.guild).channel_id()
        
        if not message_id or not channel_id:
            await ctx.send("No existing message found. Use `!platforms post` to create a new one.")
            return
            
        channel = ctx.guild.get_channel(channel_id)
        if not channel:
            await ctx.send("Cannot find the original channel.")
            return
            
        try:
            message = await channel.fetch_message(message_id)
        except discord.NotFound:
            await ctx.send("Cannot find the original message. Use `!platforms post` to create a new one.")
            return
            
        roles = await self.config.guild(ctx.guild).roles()
        
        embed = discord.Embed(
            title="Gaming Platforms",
            description="Select your gaming platforms to access their specific channels!\n"
                       "Click a button below to add or remove the role.",
            color=discord.Color.blurple()
        )
        
        platform_emojis = {
            "pc": "🖥️",
            "nintendo": "🎮",
            "playstation": "🎮",
            "xbox": "🎮"
        }
        
        for platform, role_id in roles.items():
            if role_id:
                role = ctx.guild.get_role(role_id)
                if role:
                    emoji = platform_emojis.get(platform, "")
                    embed.add_field(
                        name=f"{emoji} {platform.title()}",
                        value=f"Role: {role.mention}",
                        inline=True
                    )
        
        view = PlatformView(roles)
        await message.edit(embed=embed, view=view)
        await ctx.send("Platform selection message updated!")
