# lfg_logic.py
import discord
from discord.ext import tasks
from redbot.core import commands
from datetime import datetime, timedelta
import asyncio
import logging

log = logging.getLogger("red.Elkz.zerolivesleft.lfg")

class LFGView(discord.ui.View):
    """View with buttons for LFG interactions"""
    
    def __init__(self, lfg_logic, lfg_data):
        super().__init__(timeout=None)
        self.lfg_logic = lfg_logic
        self.lfg_data = lfg_data
        
    @discord.ui.button(label="Join Group", emoji="🎮", style=discord.ButtonStyle.green)
    async def join_group(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.lfg_logic.handle_join(interaction, self.lfg_data)
    
    @discord.ui.button(label="Leave Group", emoji="❌", style=discord.ButtonStyle.red)
    async def leave_group(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.lfg_logic.handle_leave(interaction, self.lfg_data)
    
    @discord.ui.button(label="Close Group", emoji="🔒", style=discord.ButtonStyle.secondary)
    async def close_group(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.lfg_logic.handle_close(interaction, self.lfg_data)

class LFGLogic:
    """
    Looking for Group system using Discord Forums with Buttons
    """
    
    def __init__(self, parent_cog):
        self.parent_cog = parent_cog
        self.bot = parent_cog.bot
        self.config = parent_cog.config
        
        # Start cleanup task
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
            if not cleanup_hours:
                cleanup_hours = 24
            cutoff_time = datetime.utcnow() - timedelta(hours=cleanup_hours)
            
            for thread in forum.threads:
                if thread.created_at < cutoff_time and thread.name.startswith("[LFG]"):
                    try:
                        await thread.delete()
                    except discord.HTTPException:
                        pass
        except Exception as e:
            log.error(f"Error accessing LFG config for guild {guild.name}: {e}")
    
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
                "Click the **Join Group** button on any LFG post to be added to the group!\n"
                "Use **Leave Group** to leave, and hosts can use **Close Group** to end the session."
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
            content="**This is a pinned explanation post - create your LFG requests using the !lfg command!**",
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
            
            forum = ctx.guild.get_channel(config.get("lfg_forum_id"))
            embed.add_field(name="Forum Channel", value=forum.mention if forum else "Not set", inline=False)
            embed.add_field(name="Required Role", value=config.get("lfg_required_role", "Recruit"), inline=True)
            embed.add_field(name="Cleanup Hours", value=config.get("lfg_cleanup_hours", 24), inline=True)
            embed.add_field(name="Max Players", value=config.get("lfg_max_players", 10), inline=True)
            
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
        try:
            # Check if user has required role
            if not await self._has_required_role(ctx.author, ctx.guild):
                required_role = await self.config.guild(ctx.guild).lfg_required_role()
                if not required_role:
                    required_role = "Recruit"
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
            if not max_players:
                max_players = 10  # Default fallback
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
            embed.add_field(name="👥 Players Needed", value=f"1/{players_needed}", inline=True)
            embed.add_field(name="🕐 Time", value=time or "Not specified", inline=True)
            
            # Add voice channel info if host is in voice
            if ctx.author.voice and ctx.author.voice.channel:
                embed.add_field(name="🔊 Voice Channel", value=ctx.author.voice.channel.mention, inline=True)
            else:
                embed.add_field(name="🔊 Voice Channel", value="Not in voice", inline=True)
            
            embed.add_field(name="📋 Current Players", value=ctx.author.display_name, inline=False)
            
            embed.set_thumbnail(url=ctx.author.display_avatar.url)
            embed.set_footer(text="Use the buttons below to join or leave this group! 🎮 Join will also connect you to voice if available.")
            
            # Create thread title
            thread_title = f"[LFG] {game} - {players_needed} players needed"
            if time:
                thread_title += f" @ {time}"
            
            # Store LFG data
            lfg_data = {
                "host_id": ctx.author.id,
                "game": game,
                "players_needed": players_needed,
                "current_players": [ctx.author.id],
                "description": description,
                "time": time,
                "closed": False,
                "guild_id": ctx.guild.id,
                "voice_channel_id": ctx.author.voice.channel.id if ctx.author.voice and ctx.author.voice.channel else None
            }
            
            # Create view with buttons
            view = LFGView(self, lfg_data)
            
            # Create the forum thread
            thread = await forum.create_thread(
                name=thread_title[:100],  # Discord has a 100 char limit
                content=f"**{ctx.author.mention} is looking for group!**",
                embed=embed,
                view=view
            )
            
            # Copy permissions from the forum's parent category to the new thread
            try:
                if forum.category:
                    # Get the category's permission overwrites
                    category_overwrites = forum.category.overwrites
                    
                    # Apply the same overwrites to the thread
                    for target, overwrite in category_overwrites.items():
                        try:
                            await thread.thread.set_permissions(target, overwrite=overwrite)
                        except discord.HTTPException as perm_error:
                            log.warning(f"Failed to set permissions for {target} on LFG thread: {perm_error}")
                    
                    log.info(f"Applied category permissions to LFG thread: {thread.thread.name}")
                else:
                    log.info(f"Forum {forum.name} has no parent category - using default permissions")
            except Exception as perm_error:
                log.error(f"Error copying permissions to LFG thread: {perm_error}")
                # Don't fail the whole operation, just log the error
            
            # Update LFG data with thread and message info
            lfg_data["thread_id"] = thread.thread.id
            lfg_data["message_id"] = thread.message.id
            
            # Store LFG data in channel config
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
        except Exception as e:
            log.error(f"Error creating LFG post: {e}")
            await ctx.send("❌ An error occurred while creating the LFG post.")
    
    async def handle_join(self, interaction: discord.Interaction, lfg_data):
        """Handle join button click"""
        try:
            user = interaction.user
            
            # Check if already in group
            if user.id in lfg_data["current_players"]:
                await interaction.response.send_message("❌ You're already in this group!", ephemeral=True)
                return
            
            # Check if group is full
            if len(lfg_data["current_players"]) >= lfg_data["players_needed"]:
                await interaction.response.send_message("❌ This group is already full!", ephemeral=True)
                return
            
            # Check if user has required role
            if not await self._has_required_role(user, interaction.guild):
                required_role = await self.config.guild(interaction.guild).lfg_required_role()
                if not required_role:
                    required_role = "Recruit"
                await interaction.response.send_message(f"❌ You need the `{required_role}` role to join LFG groups.", ephemeral=True)
                return
            
            # Add user to group
            lfg_data["current_players"].append(user.id)
            await self.config.channel(interaction.channel).lfg_data.set(lfg_data)
            
            # Try to connect user to host's voice channel
            voice_channel_id = lfg_data.get("voice_channel_id")
            voice_connection_msg = ""
            
            if voice_channel_id:
                voice_channel = interaction.guild.get_channel(voice_channel_id)
                if voice_channel and isinstance(voice_channel, discord.VoiceChannel):
                    # Check if user is currently in voice
                    if user.voice and user.voice.channel:
                        try:
                            # Move user to the LFG voice channel
                            await user.move_to(voice_channel)
                            voice_connection_msg = f" and moved to {voice_channel.mention}"
                        except discord.HTTPException as e:
                            if "permissions" in str(e).lower():
                                voice_connection_msg = f" (couldn't move you to voice - check permissions)"
                            else:
                                voice_connection_msg = f" (couldn't move you to voice - {e})"
                    else:
                        # User not in voice, just mention the channel
                        voice_connection_msg = f" Join {voice_channel.mention} to play together!"
                else:
                    # Voice channel no longer exists or host left
                    voice_connection_msg = " (host's voice channel unavailable)"
            
            # Update embed
            await self._update_lfg_embed(interaction.message, lfg_data)
            
            # Send response with voice info
            response_msg = f"✅ {user.mention} joined the group for **{lfg_data['game']}**{voice_connection_msg}"
            await interaction.response.send_message(response_msg)
            
        except Exception as e:
            log.error(f"Error handling join: {e}")
            await interaction.response.send_message("❌ An error occurred while joining the group.", ephemeral=True)
    
    async def handle_leave(self, interaction: discord.Interaction, lfg_data):
        """Handle leave button click"""
        try:
            user = interaction.user
            
            # Check if user is host
            if user.id == lfg_data["host_id"]:
                await interaction.response.send_message("❌ The host cannot leave the group. Use 'Close Group' instead.", ephemeral=True)
                return
            
            # Check if user is in group
            if user.id not in lfg_data["current_players"]:
                await interaction.response.send_message("❌ You're not in this group!", ephemeral=True)
                return
            
            # Remove user from group
            lfg_data["current_players"].remove(user.id)
            await self.config.channel(interaction.channel).lfg_data.set(lfg_data)
            
            # Update embed
            await self._update_lfg_embed(interaction.message, lfg_data)
            
            # Send response
            await interaction.response.send_message(f"👋 {user.mention} left the group for **{lfg_data['game']}**.")
            
        except Exception as e:
            log.error(f"Error handling leave: {e}")
            await interaction.response.send_message("❌ An error occurred while leaving the group.", ephemeral=True)
    
    async def handle_close(self, interaction: discord.Interaction, lfg_data):
        """Handle close button click"""
        try:
            user = interaction.user
            
            # Check if user is host
            if user.id != lfg_data["host_id"]:
                await interaction.response.send_message("❌ Only the host can close the group!", ephemeral=True)
                return
            
            # Close the group
            lfg_data["closed"] = True
            await self.config.channel(interaction.channel).lfg_data.set(lfg_data)
            
            # Update embed to show closed
            await self._update_lfg_embed(interaction.message, lfg_data)
            
            # Disable all buttons
            view = discord.ui.View()
            for item in interaction.message.components[0].children:
                button = discord.ui.Button(
                    label=item.label,
                    emoji=item.emoji,
                    style=item.style,
                    disabled=True
                )
                view.add_item(button)
            
            await interaction.message.edit(view=view)
            
            # Send response
            await interaction.response.send_message(f"🔒 The host has closed this LFG group for **{lfg_data['game']}**.")
            
        except Exception as e:
            log.error(f"Error handling close: {e}")
            await interaction.response.send_message("❌ An error occurred while closing the group.", ephemeral=True)
    
    async def _update_lfg_embed(self, message, lfg_data):
        """Update the LFG embed with current player information"""
        try:
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
            
            # Add voice channel info
            voice_channel_id = lfg_data.get("voice_channel_id")
            if voice_channel_id:
                voice_channel = message.guild.get_channel(voice_channel_id)
                if voice_channel:
                    embed.add_field(name="🔊 Voice Channel", value=voice_channel.mention, inline=True)
                else:
                    embed.add_field(name="🔊 Voice Channel", value="Channel unavailable", inline=True)
            else:
                embed.add_field(name="🔊 Voice Channel", value="Not in voice", inline=True)
            
            embed.add_field(name="📋 Current Players", value="\n".join(player_names) or "None", inline=False)
            
            if host:
                embed.set_thumbnail(url=host.display_avatar.url)
            
            if not closed:
                embed.set_footer(text="🎮 Join (auto-connects to voice) | ❌ Leave | 🔒 Close (Host only)")
            else:
                embed.set_footer(text="This group is closed.")
            
            await message.edit(embed=embed)
            
        except Exception as e:
            log.error(f"Error updating LFG embed: {e}")
    
    # This method is no longer needed since we're using buttons
    async def on_reaction_add(self, reaction, user):
        """Legacy method - now using buttons instead"""
        pass
    
    async def on_message(self, message):
        """Handle messages in LFG forum channels"""
        try:
            # Ignore bot messages
            if message.author.bot:
                return
            
            # Check if this is in an LFG forum channel
            if not isinstance(message.channel, discord.Thread):
                return
            
            if not message.channel.parent or not isinstance(message.channel.parent, discord.ForumChannel):
                return
            
            # Get the configured LFG forum ID for this guild
            forum_id = await self.config.guild(message.guild).lfg_forum_id()
            if not forum_id or message.channel.parent.id != forum_id:
                return
            
            # ONLY filter messages in the pinned "How to Use" thread
            if not message.channel.name.startswith("📌 How to Use"):
                return  # Allow all messages in actual LFG threads
            
            # Check if message starts with !lfg (allow some flexibility with spacing)
            message_content = message.content.strip().lower()
            if message_content.startswith('!lfg'):
                return  # Allow LFG commands in the instruction thread
            
            # This is a non-LFG message in the "How to Use" thread - delete it and warn the user
            try:
                # Delete the message first
                await message.delete()
                
                # Send a helpful reminder to the user via DM
                reminder_embed = discord.Embed(
                    title="❌ LFG Instructions Thread",
                    description=f"Hi {message.author.display_name}! Your message in the LFG instructions thread was removed.",
                    color=0xff6b6b
                )
                
                forum_name = message.channel.parent.name
                reminder_embed.add_field(
                    name="📋 Instructions Thread Guidelines",
                    value=(
                        f"The **📌 How to Use LFG System** thread is for:\n"
                        "• Reading the LFG instructions\n"
                        "• Testing LFG commands (messages starting with `!lfg`)\n\n"
                        "**Regular chat messages are not allowed here.**"
                    ),
                    inline=False
                )
                
                reminder_embed.add_field(
                    name="🎮 To Create Your LFG Post",
                    value=(
                        "Use: `!lfg <game> <players_needed> [description] [time]`\n\n"
                        "**Examples:**\n"
                        "• `!lfg \"Valorant\" 3 \"Ranked games\" \"8pm EST\"`\n"
                        "• `!lfg Minecraft 5 \"Building project\"`\n\n"
                        "This creates a **new thread** where you and others can chat freely!"
                    ),
                    inline=False
                )
                
                reminder_embed.add_field(
                    name="💬 For General Discussion",
                    value="Use other channels or create your own LFG thread for conversation!",
                    inline=False
                )
                
                reminder_embed.set_footer(text=f"Server: {message.guild.name}")
                
                # Try to send DM, with better error handling
                dm_sent = False
                try:
                    await message.author.send(embed=reminder_embed)
                    dm_sent = True
                except discord.Forbidden:
                    # User has DMs disabled
                    pass
                except discord.HTTPException as dm_error:
                    log.warning(f"Failed to send DM to {message.author}: {dm_error}")
                
                # If DM failed, try to send a brief message in the channel
                if not dm_sent:
                    try:
                        warning_msg = await message.channel.send(
                            f"{message.author.mention} The instructions thread is for reading guidelines and testing `!lfg` commands only. "
                            f"Create your own LFG post to chat with others! (This message will delete in 15 seconds)",
                            delete_after=15
                        )
                    except discord.HTTPException as channel_error:
                        log.warning(f"Failed to send channel warning: {channel_error}")
                        # At least log that we deleted the message
                        log.info(f"Deleted non-LFG message from {message.author} in instructions thread")
                
            except discord.NotFound:
                # Message was already deleted
                pass
            except discord.Forbidden:
                # No permission to delete
                log.warning(f"No permission to delete message in {message.guild.name}")
            except discord.HTTPException as delete_error:
                log.error(f"Failed to delete non-LFG message: {delete_error}")
                
        except Exception as e:
            log.error(f"Error in LFG message filter: {e}")