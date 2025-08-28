import discord
from redbot.core import commands, Config, checks
from redbot.core.utils.embed import EmbedWithEmoji
from typing import Optional
import asyncio
from datetime import datetime


class SuggestionBox(commands.Cog):
    """Anonymous suggestion box for server members."""
    
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        
        default_guild = {
            "suggestion_channel": None,
            "log_channel": None,
            "next_id": 1,
            "suggestions": {}
        }
        
        self.config.register_guild(**default_guild)
    
    @commands.group(name="suggestionbox", aliases=["sbox"])
    @commands.guild_only()
    async def suggestionbox(self, ctx):
        """Suggestion box commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)
    
    @suggestionbox.command(name="setup")
    @checks.admin_or_permissions(manage_guild=True)
    async def setup_suggestion_box(self, ctx, suggestion_channel: discord.TextChannel, log_channel: Optional[discord.TextChannel] = None):
        """Set up the suggestion box.
        
        Parameters:
        - suggestion_channel: Channel where suggestions will be posted
        - log_channel: Optional channel for logging (defaults to suggestion channel)
        """
        await self.config.guild(ctx.guild).suggestion_channel.set(suggestion_channel.id)
        
        log_chan = log_channel or suggestion_channel
        await self.config.guild(ctx.guild).log_channel.set(log_chan.id)
        
        embed = discord.Embed(
            title="✅ Suggestion Box Setup Complete",
            description=f"**Suggestion Channel:** {suggestion_channel.mention}\n**Log Channel:** {log_chan.mention}",
            color=discord.Color.green()
        )
        await ctx.send(embed=embed)
    
    @suggestionbox.command(name="suggest")
    @commands.guild_only()
    async def submit_suggestion(self, ctx, *, suggestion: str):
        """Submit an anonymous suggestion.
        
        Usage: `[p]suggest Your suggestion here`
        """
        # Delete the user's message to maintain anonymity
        try:
            await ctx.message.delete()
        except discord.NotFound:
            pass
        except discord.Forbidden:
            await ctx.send("⚠️ I need permission to delete messages to maintain anonymity. Please delete your message manually.", delete_after=10)
        
        guild_config = self.config.guild(ctx.guild)
        suggestion_channel_id = await guild_config.suggestion_channel()
        
        if not suggestion_channel_id:
            await ctx.author.send("❌ The suggestion box hasn't been set up yet. Please contact a server administrator.")
            return
        
        suggestion_channel = ctx.guild.get_channel(suggestion_channel_id)
        if not suggestion_channel:
            await ctx.author.send("❌ The suggestion channel no longer exists. Please contact a server administrator.")
            return
        
        # Get next suggestion ID
        suggestion_id = await guild_config.next_id()
        await guild_config.next_id.set(suggestion_id + 1)
        
        # Create suggestion embed - Fixed timestamp issue
        embed = discord.Embed(
            title=f"💡 Suggestion #{suggestion_id}",
            description=suggestion,
            color=discord.Color.blue(),
            timestamp=datetime.now()  # Changed from datetime.utcnow() which is deprecated
        )
        embed.set_footer(text="React with ✅ to approve, ❌ to deny, or 🤔 for consideration")
        
        try:
            suggestion_msg = await suggestion_channel.send(embed=embed)
            
            # Add reaction options
            await suggestion_msg.add_reaction("✅")
            await suggestion_msg.add_reaction("❌")
            await suggestion_msg.add_reaction("🤔")
            
            # Store suggestion data - Fixed timestamp serialization
            suggestions = await guild_config.suggestions()
            suggestions[str(suggestion_id)] = {
                "message_id": suggestion_msg.id,
                "channel_id": suggestion_channel.id,
                "content": suggestion,
                "timestamp": datetime.now().isoformat(),  # Changed from datetime.utcnow()
                "status": "pending"
            }
            await guild_config.suggestions.set(suggestions)
            
            # Send confirmation to user
            confirm_embed = discord.Embed(
                title="✅ Suggestion Submitted",
                description=f"Your suggestion has been submitted anonymously as **Suggestion #{suggestion_id}**.",
                color=discord.Color.green()
            )
            await ctx.author.send(embed=confirm_embed)
            
            # Log to log channel
            log_channel_id = await guild_config.log_channel()
            if log_channel_id and log_channel_id != suggestion_channel_id:
                log_channel = ctx.guild.get_channel(log_channel_id)
                if log_channel:
                    log_embed = discord.Embed(
                        title="📝 New Suggestion Logged",
                        description=f"**ID:** {suggestion_id}\n**Channel:** {suggestion_channel.mention}",
                        color=discord.Color.orange(),
                        timestamp=datetime.now()  # Changed from datetime.utcnow()
                    )
                    await log_channel.send(embed=log_embed)
        
        except discord.Forbidden:
            await ctx.author.send("❌ I don't have permission to send messages in the suggestion channel.")
        except Exception as e:
            await ctx.author.send(f"❌ An error occurred while submitting your suggestion: {str(e)}")
    
    @suggestionbox.command(name="status")
    @checks.mod_or_permissions(manage_messages=True)
    async def suggestion_status(self, ctx, suggestion_id: int, status: str, *, reason: Optional[str] = None):
        """Update the status of a suggestion.
        
        Status options: approved, denied, considering, implemented
        """
        valid_statuses = ["approved", "denied", "considering", "implemented"]
        status = status.lower()
        
        if status not in valid_statuses:
            await ctx.send(f"❌ Invalid status. Valid options: {', '.join(valid_statuses)}")
            return
        
        guild_config = self.config.guild(ctx.guild)
        suggestions = await guild_config.suggestions()
        
        if str(suggestion_id) not in suggestions:
            await ctx.send(f"❌ Suggestion #{suggestion_id} not found.")
            return
        
        suggestion_data = suggestions[str(suggestion_id)]
        
        # Update suggestion status - Fixed discriminator issue
        suggestion_data["status"] = status
        suggestion_data["moderator"] = str(ctx.author)  # Changed from name#discriminator format
        suggestion_data["reason"] = reason
        
        suggestions[str(suggestion_id)] = suggestion_data
        await guild_config.suggestions.set(suggestions)
        
        # Update the original message
        try:
            channel = ctx.guild.get_channel(suggestion_data["channel_id"])
            message = await channel.fetch_message(suggestion_data["message_id"])
            
            # Color mapping for statuses
            color_map = {
                "approved": discord.Color.green(),
                "denied": discord.Color.red(),
                "considering": discord.Color.orange(),
                "implemented": discord.Color.purple()
            }
            
            # Status emoji mapping
            emoji_map = {
                "approved": "✅",
                "denied": "❌",
                "considering": "🤔",
                "implemented": "🎉"
            }
            
            # Fixed datetime parsing issue
            try:
                timestamp = datetime.fromisoformat(suggestion_data["timestamp"])
            except (ValueError, KeyError):
                timestamp = datetime.now()
            
            embed = discord.Embed(
                title=f"{emoji_map[status]} Suggestion #{suggestion_id} - {status.title()}",
                description=suggestion_data["content"],
                color=color_map[status],
                timestamp=timestamp
            )
            
            if reason:
                embed.add_field(name="Moderator Note", value=reason, inline=False)
            
            embed.set_footer(text=f"Status updated by {ctx.author.display_name}")
            
            await message.edit(embed=embed)
            await message.clear_reactions()
            
            await ctx.send(f"✅ Updated suggestion #{suggestion_id} status to **{status}**.")
            
        except discord.NotFound:
            await ctx.send(f"⚠️ Could not find the original message for suggestion #{suggestion_id}, but status was updated in database.")
        except Exception as e:
            await ctx.send(f"❌ Error updating suggestion: {str(e)}")
    
    @suggestionbox.command(name="list")
    @checks.mod_or_permissions(manage_messages=True)
    async def list_suggestions(self, ctx, status: Optional[str] = None):
        """List all suggestions or filter by status.
        
        Status options: pending, approved, denied, considering, implemented
        """
        guild_config = self.config.guild(ctx.guild)
        suggestions = await guild_config.suggestions()
        
        if not suggestions:
            await ctx.send("📭 No suggestions found.")
            return
        
        # Filter by status if provided
        if status:
            status = status.lower()
            filtered_suggestions = {k: v for k, v in suggestions.items() if v.get("status", "pending") == status}
            if not filtered_suggestions:
                await ctx.send(f"📭 No suggestions found with status: {status}")
                return
            suggestions = filtered_suggestions
        
        # Create paginated embed
        embed = discord.Embed(
            title=f"📋 Suggestions List" + (f" - {status.title()}" if status else ""),
            color=discord.Color.blue()
        )
        
        suggestion_list = []
        for suggestion_id, data in list(suggestions.items())[:10]:  # Limit to 10 for readability
            status_emoji = {
                "pending": "⏳",
                "approved": "✅", 
                "denied": "❌",
                "considering": "🤔",
                "implemented": "🎉"
            }
            
            current_status = data.get("status", "pending")
            emoji = status_emoji.get(current_status, "❓")
            
            content_preview = data["content"][:50] + ("..." if len(data["content"]) > 50 else "")
            suggestion_list.append(f"{emoji} **#{suggestion_id}** - {content_preview}")
        
        if suggestion_list:
            embed.description = "\n".join(suggestion_list)
            
            total_count = len(suggestions)
            if total_count > 10:
                embed.set_footer(text=f"Showing 10 of {total_count} suggestions")
        
        await ctx.send(embed=embed)
    
    @suggestionbox.command(name="view")
    @checks.mod_or_permissions(manage_messages=True)
    async def view_suggestion(self, ctx, suggestion_id: int):
        """View details of a specific suggestion."""
        guild_config = self.config.guild(ctx.guild)
        suggestions = await guild_config.suggestions()
        
        if str(suggestion_id) not in suggestions:
            await ctx.send(f"❌ Suggestion #{suggestion_id} not found.")
            return
        
        data = suggestions[str(suggestion_id)]
        
        color_map = {
            "pending": discord.Color.blue(),
            "approved": discord.Color.green(),
            "denied": discord.Color.red(),
            "considering": discord.Color.orange(),
            "implemented": discord.Color.purple()
        }
        
        status = data.get("status", "pending")
        
        # Fixed datetime parsing issue
        try:
            timestamp = datetime.fromisoformat(data["timestamp"])
        except (ValueError, KeyError):
            timestamp = datetime.now()
        
        embed = discord.Embed(
            title=f"💡 Suggestion #{suggestion_id}",
            description=data["content"],
            color=color_map.get(status, discord.Color.blue()),
            timestamp=timestamp
        )
        
        embed.add_field(name="Status", value=status.title(), inline=True)
        
        if data.get("moderator"):
            embed.add_field(name="Reviewed By", value=data["moderator"], inline=True)
        
        if data.get("reason"):
            embed.add_field(name="Moderator Note", value=data["reason"], inline=False)
        
        await ctx.send(embed=embed)
    
    @suggestionbox.command(name="config")
    @checks.admin_or_permissions(manage_guild=True)
    async def show_config(self, ctx):
        """Show current suggestion box configuration."""
        guild_config = self.config.guild(ctx.guild)
        
        suggestion_channel_id = await guild_config.suggestion_channel()
        log_channel_id = await guild_config.log_channel()
        next_id = await guild_config.next_id()
        suggestions_count = len(await guild_config.suggestions())
        
        embed = discord.Embed(
            title="⚙️ Suggestion Box Configuration",
            color=discord.Color.blue()
        )
        
        if suggestion_channel_id:
            suggestion_channel = ctx.guild.get_channel(suggestion_channel_id)
            embed.add_field(
                name="Suggestion Channel", 
                value=suggestion_channel.mention if suggestion_channel else "❌ Channel not found",
                inline=False
            )
        else:
            embed.add_field(name="Suggestion Channel", value="❌ Not configured", inline=False)
        
        if log_channel_id:
            log_channel = ctx.guild.get_channel(log_channel_id)
            embed.add_field(
                name="Log Channel",
                value=log_channel.mention if log_channel else "❌ Channel not found",
                inline=False
            )
        
        embed.add_field(name="Next Suggestion ID", value=f"#{next_id}", inline=True)
        embed.add_field(name="Total Suggestions", value=suggestions_count, inline=True)
        
        await ctx.send(embed=embed)