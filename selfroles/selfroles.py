import discord
from redbot.core import commands, Config
import asyncio
import re

class ReactionRoles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=5566778899)
        self.config.register_guild(
            reaction_roles={}
        )
        bot.add_listener(self.reaction_add_listener, "on_raw_reaction_add")
        bot.add_listener(self.reaction_remove_listener, "on_raw_reaction_remove")

    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def reactionroles(self, ctx):
        """Reaction roles management"""
        pass

    @reactionroles.command()
    async def setup(self, ctx, channel: discord.TextChannel = None):
        """Setup reaction roles - follow the prompts"""
        channel = channel or ctx.channel
        await ctx.send(f"Starting reaction roles setup in {channel.mention}")
        await ctx.send("Please send the message you want to add reaction roles to (or type 'cancel' to exit).")

        try:
            msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=60.0)
            
            if msg.content.lower() == "cancel":
                return await ctx.send("Setup cancelled.")
                
            if not msg.reference:
                return await ctx.send("Please reply to a message with the reaction roles you want to add.")
                
            target_message = await channel.fetch_message(msg.reference.message_id)
            
            await ctx.send("Now send the reaction role setup in format: `emoji role_name` (one per line)")
            await ctx.send("Example: <:steam:1325786409084260434> @PC")
            await ctx.send("Type 'done' when finished.")
            
            reaction_roles = {}
            emoji_role_map = {}
            
            while True:
                try:
                    msg = await self.bot.wait_for("message", check=lambda m: m.author == ctx.author and m.channel == ctx.channel, timeout=60.0)
                    
                    if msg.content.lower() == "done":
                        break
                        
                    content = msg.content.strip()
                    if not content:
                        continue
                        
                    # Parse emoji and role
                    parts = content.split(" ", 1)
                    if len(parts) < 2:
                        await ctx.send("Invalid format. Use: `emoji role_name`")
                        continue
                        
                    emoji = parts[0]
                    role_input = parts[1]
                    
                    # Find role ID
                    role_id = None
                    if msg.role_mentions:
                        role_id = msg.role_mentions[0].id
                    else:
                        id_match = re.search(r'\d{17,20}', role_input)
                        if id_match:
                            role_id = int(id_match.group())
                        else:
                            # Try to find role by name
                            role_name = role_input.strip("<>@")
                            for role in ctx.guild.roles:
                                if role.name == role_name:
                                    role_id = role.id
                                    break
                            
                    if not role_id:
                        await ctx.send(f"Could not find role for: {role_input}")
                        continue
                        
                    # Validate emoji format
                    if not re.match(r'<a?:\w+:\d+>', emoji) and not re.match(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF]', emoji):
                        await ctx.send(f"Invalid emoji format: {emoji}")
                        continue
                        
                    # Store mapping
                    reaction_roles[emoji] = role_id
                    emoji_role_map[emoji] = role_input
                    
                    # Add reaction to message
                    try:
                        await target_message.add_reaction(emoji)
                        await ctx.send(f"Added reaction {emoji} for role {role_input}")
                    except discord.HTTPException:
                        await ctx.send(f"Failed to add reaction {emoji} - may not be valid")
                        
                except asyncio.TimeoutError:
                    await ctx.send("Setup timed out.")
                    break
                    
            if reaction_roles:
                # Save to config
                await self.config.guild(ctx.guild).reaction_roles.set(reaction_roles)
                await ctx.send("Reaction roles setup complete!")
                
                # Send confirmation
                embed = discord.Embed(title="Reaction Roles Setup", color=discord.Color.blue())
                for emoji, role_id in reaction_roles.items():
                    role = ctx.guild.get_role(role_id)
                    if role:
                        embed.add_field(name=f"{emoji}", value=f"Role: {role.name}", inline=False)
                await ctx.send(embed=embed)
            else:
                await ctx.send("No valid reaction roles were added.")
                
        except asyncio.TimeoutError:
            await ctx.send("Setup timed out.")

    @reactionroles.command()
    async def post(self, ctx, channel: discord.TextChannel = None, message_id: int = None):
        """Post a message with reaction roles"""
        channel = channel or ctx.channel
        
        # Create a simple message with reactions
        embed = discord.Embed(
            title="🎮 Gaming Platforms",
            description="Click the reactions below to get your platform roles!",
            color=discord.Color.blurple()
        )
        
        # Add platform emojis and roles
        platform_emojis = {
            "💻": "PC",
            " Nintendo": "Nintendo",
            "🎮": "Xbox",
            " PlayStation": "PlayStation"
        }
        
        message = await channel.send(embed=embed)
        
        # Add reactions
        for emoji in platform_emojis:
            try:
                await message.add_reaction(emoji)
            except discord.HTTPException:
                await ctx.send(f"Could not add reaction {emoji}")
                
        # Save the message ID for reaction handling
        await ctx.send(f"Reaction role message posted! Message ID: {message.id}")

    @reactionroles.command()
    async def list(self, ctx):
        """List current reaction roles"""
        data = await self.config.guild(ctx.guild).reaction_roles()
        if not data:
            return await ctx.send("No reaction roles configured.")
            
        embed = discord.Embed(title="Reaction Roles Configuration", color=discord.Color.blue())
        for emoji, role_id in data.items():
            role = ctx.guild.get_role(role_id)
            if role:
                embed.add_field(name=f"{emoji}", value=f"Role: {role.name}", inline=False)
            else:
                embed.add_field(name=f"{emoji}", value=f"Role ID: {role_id} (Role not found)", inline=False)
                
        await ctx.send(embed=embed)

    @reactionroles.command()
    async def clear(self, ctx):
        """Clear all reaction roles"""
        await self.config.guild(ctx.guild).reaction_roles.clear()
        await ctx.send("Reaction roles cleared.")

    async def reaction_add_listener(self, payload):
        """Handle reactions being added"""
        if payload.guild_id is None:
            return
            
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
            
        user = guild.get_member(payload.user_id)
        if not user or user.bot:
            return
            
        reaction_roles = await self.config.guild(guild).reaction_roles()
        if not reaction_roles:
            return
            
        # Check if this is a reaction we're tracking
        if str(payload.emoji) not in reaction_roles:
            return
            
        role_id = reaction_roles[str(payload.emoji)]
        role = guild.get_role(role_id)
        
        if not role:
            return
            
        try:
            await user.add_roles(role)
            # Optional: Send confirmation DM
            # await user.send(f"Added role: {role.name}")
        except discord.Forbidden:
            # Handle missing permissions
            pass

    async def reaction_remove_listener(self, payload):
        """Handle reactions being removed"""
        if payload.guild_id is None:
            return
            
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
            
        user = guild.get_member(payload.user_id)
        if not user or user.bot:
            return
            
        reaction_roles = await self.config.guild(guild).reaction_roles()
        if not reaction_roles:
            return
            
        # Check if this is a reaction we're tracking
        if str(payload.emoji) not in reaction_roles:
            return
            
        role_id = reaction_roles[str(payload.emoji)]
        role = guild.get_role(role_id)
        
        if not role:
            return
            
        try:
            await user.remove_roles(role)
            # Optional: Send confirmation DM
            # await user.send(f"Removed role: {role.name}")
        except discord.Forbidden:
            # Handle missing permissions
            pass

async def setup(bot):
    await bot.add_cog(ReactionRoles(bot))
