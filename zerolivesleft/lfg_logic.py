# lfg_logic.py
import discord
from discord.ext import tasks
from redbot.core import commands
from datetime import datetime, timedelta
import asyncio
import logging

log = logging.getLogger("red.Elkz.zerolivesleft.lfg")

class LFGLogic:
    """
    Looking for Group system using Discord Forums
    """
    
    def __init__(self, parent_cog):
        self.parent_cog = parent_cog
        self.bot = parent_cog.bot
        self.config = parent_cog.config
        
        # LFG-specific config will be registered in the parent cog
        self.cleanup_task.start()
    
    def stop_tasks(self):
        """Stop all background tasks"""
        self.cleanup_task.cancel()
    
    @tasks.loop(hours=1)
    async def cleanup_task(self):
        """Clean up old LFG posts that are past their scheduled time"""
        for guild in self.bot.guilds:
            try:
                await self._cleanup_old_posts(guild)
            except Exception as e:
                log.error(f"Error cleaning up LFG posts in {guild.name}: {e}")
    
    @cleanup_task.before_loop
    async def before_cleanup_task(self):
        await self.bot.wait_until_ready()
    
    async def _cleanup_old_posts(self, guild):
        """Clean up old LFG posts"""
        try:
            forum_id = await self.config.guild(guild).lfg_forum_id()
            if not forum_id:
                return
                
            forum = guild.get_channel(forum_id)
            if not forum or not isinstance(forum, discord.ForumChannel):
                return
            
            cleanup_hours = await self.config.guild(guild).lfg_cleanup_hours()
            cutoff_time = datetime.utcnow() - timedelta(hours=cleanup_hours)
        except Exception as e:
            log.error(f"Error accessing LFG config for guild {guild.name}: {e}")
            return
        
        for thread in forum.threads:
            if thread.created_at < cutoff_time and thread.name.startswith("[LFG]"):
                try:
                    await thread.delete()
                except discord.HTTPException:
                    pass
    
    async def setup_lfg(self, ctx, forum_channel: discord.ForumChannel):
        """Set up the LFG system with a forum channel"""
        await self.config.guild(ctx.guild).lfg_forum_id.set(forum_channel.id)
        
        # Create the pinned explanation post
        embed = discord.Embed(
            title="🎮 Looking for Group (LFG) System",
            description="Welcome to the LFG board! Here's how to use it:",
            color=0x00ff00
        )
        
        embed.add_field(
            name="📝 Creating an LFG Post",
            value=(
                "Use the command: `!lfg <game> <players_needed> [description] [time]`\n\n"
                "**Examples:**\n"
                "`!lfg \"Valorant\" 3 \"Ranked games, be chill\" \"8pm EST\"`\n"
                "`!lfg Minecraft 5 \"Building project\"`\n"
                "`!lfg \"Among Us\" 8`"
            ),
            inline=False
        )
        
        embed.add_field(
            name="⚙️ Optional Parameters",
            value=(
                "**Time**: When you plan to play (e.g., \"8pm EST\", \"in 30 mins\")\n"
                "**Description**: Additional details about the session\n"
                "**Players needed**: How many more players you need"
            ),
            inline=False
        )
        
        embed.add_field(
            name="🔗 Joining a Group",
            value=(
                "Click the 🎮 **Join Group** button on any LFG post to be added to the group!\n"
                "The post creator can manage who joins using the reaction buttons."
            ),
            inline=False
        )
        
        embed.add_field(
            name="🧹 Automatic Cleanup",
            value="Old LFG posts are automatically cleaned up after 24 hours.",
            inline=False
        )
        
        embed.set_footer(text="Happy gaming! 🎮")
        
        # Create the pinned post
        thread = await forum_channel.create_thread(
            name="📌 How to Use LFG System",
            content="**This is a pinned explanation post - create your LFG requests as replies or new posts!**",
            embed=embed
        )
        
        # Pin the thread
        await thread.thread.edit(pinned=True)
        
        await ctx.send(f"✅ LFG system set up in {forum_channel.mention}!")
    
    async def config_lfg(self, ctx, setting: str = None, *, value: str = None):
        """Configure LFG settings"""
        if not setting:
            # Show current config
            config = await self.config.guild(ctx.guild).all()
            embed = discord.Embed(title="LFG Configuration", color=0x0099ff)
            
            forum = ctx.guild.get_channel(config["lfg_forum_id"])
            embed.add_field(name="Forum Channel", value=forum.mention if forum else "Not set", inline=False)
            embed.add_field(name="Required Role", value=config["lfg_required_role"], inline=True)
            embed.add_field(name="Cleanup Hours", value=config["lfg_cleanup_hours"], inline=True)
            embed.add_field(name="Max Players", value=config["lfg_max_players"], inline=True)
            
            await ctx.send(embed=embed)
            return
        
        if setting.lower() == "role" and value:
            await self.config.guild(ctx.guild).lfg_required_role.set(value)
            await ctx.send(f"✅ Required role set to: {value}")
        elif setting.lower() == "cleanup" and value:
            try:
                hours = int(value)
                await self.config.guild(ctx.guild).lfg_cleanup_hours.set(hours)
                await ctx.send(f"✅ Cleanup time set to: {hours} hours")
            except ValueError:
                await ctx.send("❌ Please provide a valid number of hours.")
        elif setting.lower() == "maxplayers" and value:
            try:
                max_players = int(value)
                await self.config.guild(ctx.guild).lfg_max_players.set(max_players)
                await ctx.send(f"✅ Max players set to: {max_players}")
            except ValueError:
                await ctx.send("❌ Please provide a valid number.")
        else:
            await ctx.send("❌ Valid settings: `role`, `cleanup`, `maxplayers`")
    
    async def _has_required_role(self, member, guild):
        """Check if member has the required role"""
        try:
            required_role_name = await self.config.guild(guild).lfg_required_role()
            if not required_role_name:
                required_role_name = "Recruit"  # Default fallback
            required_role = discord.utils.get(member.roles, name=required_role_name)
            return required_role is not None
        except Exception as e:
            log.error(f"Error checking required role for {member} in {guild.name}: {e}")
            return False
    
    async def create_lfg(self, ctx, game: str, players_needed: int, description: str = None, time: str = None):
        """
        Create a Looking for Group post
        
        Usage: !lfg <game> <players_needed> [description] [time]
        Example: !lfg "Valorant" 3 "Ranked games" "8pm EST"
        """
        # Check if user has required role
        if not await self._has_required_role(ctx.author, ctx.guild):
            required_role = await self.config.guild(ctx.guild).lfg_required_role()
            await ctx.send(f"❌ You need the `{required_role}` role or higher to use the LFG system.")
            return
        
        # Get forum channel
        forum_id = await self.config.guild(ctx.guild).lfg_forum_id()
        if not forum_id:
            await ctx.send("❌ LFG system not set up. Contact an admin.")
            return
        
        forum = ctx.guild.get_channel(forum_id)
        if not forum or not isinstance(forum, discord.ForumChannel):
            await ctx.send("❌ LFG forum channel not found.")
            return
        
        # Validate players needed
        max_players = await self.config.guild(ctx.guild).lfg_max_players()
        if players_needed < 1 or players_needed > max_players:
            await ctx.send(f"❌ Players needed must be between 1 and {max_players}.")
            return
        
        # Create embed for the LFG post
        embed = discord.Embed(
            title=f"🎮 {game}",
            description=description or "No description provided",
            color=0x00ff00,
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(name="👤 Host", value=ctx.author.mention, inline=True)
        embed.add_field(name="👥 Players Needed", value=f"{players_needed}/{players_needed}", inline=True)
        embed.add_field(name="🕐 Time", value=time or "Not specified", inline=True)
        embed.add_field(name="📋 Current Players", value=ctx.author.display_name, inline=False)
        
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        embed.set_footer(text="Click 🎮 to join this group!")
        
        # Create thread title
        thread_title = f"[LFG] {game} - {players_needed} players needed"
        if time:
            thread_title += f" @ {time}"
        
        # Create the forum thread
        try:
            thread = await forum.create_thread(
                name=thread_title[:100],  # Discord has a 100 char limit
                content=f"**{ctx.author.mention} is looking for group!**",
                embed=embed
            )
            
            # Add reaction buttons
            join_emoji = "🎮"
            leave_emoji = "❌" 
            close_emoji = "🔒"
            
            message = await thread.thread.fetch_message(thread.message.id)
            await message.add_reaction(join_emoji)
            await message.add_reaction(leave_emoji)
            await message.add_reaction(close_emoji)
            
            # Store LFG data
            lfg_data = {
                "host_id": ctx.author.id,
                "game": game,
                "players_needed": players_needed,
                "current_players": [ctx.author.id],
                "description": description,
                "time": time,
                "thread_id": thread.thread.id,
                "message_id": thread.message.id,
                "closed": False
            }
            
            await self.config.channel(thread.thread).lfg_data.set(lfg_data)
            
            # Delete the original command message
            try:
                await ctx.message.delete()
            except discord.HTTPException:
                pass
            
            # Send confirmation in original channel
            confirm_embed = discord.Embed(
                title="✅ LFG Post Created!",
                description=f"Your LFG post for **{game}** has been created in {thread.thread.mention}",
                color=0x00ff00
            )
            
            await ctx.send(embed=confirm_embed, delete_after=30)
            
        except discord.HTTPException as e:
            await ctx.send(f"❌ Failed to create LFG post: {e}")
    
    async def on_reaction_add(self, reaction, user):
        """Handle LFG reactions"""
        if user.bot:
            return
        
        message = reaction.message
        
        # Check if this is an LFG thread
        if not isinstance(message.channel, discord.Thread):
            return
        
        if not message.channel.parent or not isinstance(message.channel.parent, discord.ForumChannel):
            return
        
        # Get LFG data
        lfg_data = await self.config.channel(message.channel).lfg_data()
        if not lfg_data or message.id != lfg_data.get("message_id"):
            return
        
        if lfg_data.get("closed", False):
            await reaction.remove(user)
            return
        
        emoji = str(reaction.emoji)
        host_id = lfg_data["host_id"]
        current_players = lfg_data["current_players"]
        players_needed = lfg_data["players_needed"]
        
        if emoji == "🎮":  # Join group
            if user.id in current_players:
                await reaction.remove(user)
                return
            
            if len(current_players) >= players_needed:
                await reaction.remove(user)
                try:
                    await user.send("❌ This LFG group is already full!")
                except discord.HTTPException:
                    pass
                return
            
            # Check if user has required role
            if not await self._has_required_role(user, message.guild):
                await reaction.remove(user)
                try:
                    required_role = await self.config.guild(message.guild).lfg_required_role()
                    await user.send(f"❌ You need the `{required_role}` role to join LFG groups.")
                except discord.HTTPException:
                    pass
                return
            
            # Add user to group
            current_players.append(user.id)
            lfg_data["current_players"] = current_players
            await self.config.channel(message.channel).lfg_data.set(lfg_data)
            
            # Update embed
            await self._update_lfg_embed(message, lfg_data)
            
            # Send join message
            join_embed = discord.Embed(
                title="🎮 Player Joined!",
                description=f"{user.mention} joined the group for **{lfg_data['game']}**!",
                color=0x00ff00
            )
            await message.channel.send(embed=join_embed)
            
        elif emoji == "❌":  # Leave group
            if user.id == host_id:
                await reaction.remove(user)
                return
            
            if user.id not in current_players:
                await reaction.remove(user)
                return
            
            # Remove user from group
            current_players.remove(user.id)
            lfg_data["current_players"] = current_players
            await self.config.channel(message.channel).lfg_data.set(lfg_data)
            
            # Update embed
            await self._update_lfg_embed(message, lfg_data)
            
            # Send leave message
            leave_embed = discord.Embed(
                title="👋 Player Left",
                description=f"{user.mention} left the group for **{lfg_data['game']}**.",
                color=0xff9900
            )
            await message.channel.send(embed=leave_embed)
            
        elif emoji == "🔒" and user.id == host_id:  # Close group (host only)
            lfg_data["closed"] = True
            await self.config.channel(message.channel).lfg_data.set(lfg_data)
            
            # Update embed to show closed
            await self._update_lfg_embed(message, lfg_data)
            
            # Clear reactions
            await message.clear_reactions()
            
            # Send close message
            close_embed = discord.Embed(
                title="🔒 Group Closed",
                description=f"The host has closed this LFG group for **{lfg_data['game']}**.",
                color=0xff0000
            )
            await message.channel.send(embed=close_embed)
    
    async def _update_lfg_embed(self, message, lfg_data):
        """Update the LFG embed with current player information"""
        current_players = lfg_data["current_players"]
        players_needed = lfg_data["players_needed"]
        game = lfg_data["game"]
        description = lfg_data.get("description", "No description provided")
        time = lfg_data.get("time", "Not specified")
        closed = lfg_data.get("closed", False)
        
        # Get player names
        player_names = []
        for player_id in current_players:
            user = message.guild.get_member(player_id)
            if user:
                player_names.append(user.display_name)
        
        # Create updated embed
        color = 0xff0000 if closed else (0x00ff00 if len(current_players) < players_needed else 0xffff00)
        status = "🔒 CLOSED" if closed else ("🟢 OPEN" if len(current_players) < players_needed else "🟡 FULL")
        
        embed = discord.Embed(
            title=f"🎮 {game} {status}",
            description=description,
            color=color,
            timestamp=datetime.utcnow()
        )
        
        host = message.guild.get_member(lfg_data["host_id"])
        embed.add_field(name="👤 Host", value=host.mention if host else "Unknown", inline=True)
        embed.add_field(name="👥 Players", value=f"{len(current_players)}/{players_needed}", inline=True)
        embed.add_field(name="🕐 Time", value=time, inline=True)
        embed.add_field(name="📋 Current Players", value="\n".join(player_names) or "None", inline=False)
        
        if host:
            embed.set_thumbnail(url=host.display_avatar.url)
        
        if not closed:
            embed.set_footer(text="🎮 Join | ❌ Leave | 🔒 Close (Host only)")
        else:
            embed.set_footer(text="This group is closed.")
        
        try:
            await message.edit(embed=embed)
        except discord.HTTPException:
            pass