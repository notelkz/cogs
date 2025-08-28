import discord
from redbot.core import commands, Config, checks
from redbot.core.bot import Red
from typing import Optional
import asyncio
from datetime import datetime


class SuggestionModal(discord.ui.Modal, title="Submit Your Suggestion"):
    def __init__(self, cog):
        super().__init__()
        self.cog = cog

    suggestion = discord.ui.TextInput(
        label="Your Suggestion",
        placeholder="Type your suggestion here...",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        guild_config = self.cog.config.guild(interaction.guild)
        log_channel_id = await guild_config.log_channel()
        
        if not log_channel_id:
            await interaction.followup.send("❌ The suggestion system hasn't been set up properly. Please contact an administrator.", ephemeral=True)
            return
        
        log_channel = interaction.guild.get_channel(log_channel_id)
        if not log_channel:
            await interaction.followup.send("❌ The suggestion log channel no longer exists. Please contact an administrator.", ephemeral=True)
            return
        
        # Get next suggestion ID
        suggestion_id = await guild_config.next_id()
        await guild_config.next_id.set(suggestion_id + 1)
        
        # Create suggestion embed for log channel
        embed = discord.Embed(
            title=f"💡 Suggestion #{suggestion_id}",
            description=self.suggestion.value,
            color=self.cog.status_colors["pending"],
            timestamp=datetime.now()
        )
        embed.set_footer(text="React with ✅ to approve or ❌ to deny")
        
        try:
            suggestion_msg = await log_channel.send(embed=embed)
            
            # Add reaction options
            await suggestion_msg.add_reaction("✅")
            await suggestion_msg.add_reaction("❌")
            
            # Store suggestion data
            suggestions = await guild_config.suggestions()
            suggestions[str(suggestion_id)] = {
                "message_id": suggestion_msg.id,
                "channel_id": log_channel.id,
                "content": self.suggestion.value,
                "timestamp": datetime.now().isoformat(),
                "status": "pending",
                "author_id": interaction.user.id  # Store for potential feedback
            }
            await guild_config.suggestions.set(suggestions)
            
            # Send confirmation to user
            await interaction.followup.send(
                f"✅ Your suggestion has been submitted anonymously as **Suggestion #{suggestion_id}**. "
                f"Thank you for your feedback!",
                ephemeral=True
            )
            
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to send messages in the suggestion log channel.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ An error occurred while submitting your suggestion: {str(e)}", ephemeral=True)


class SuggestionView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Submit Suggestion", 
        style=discord.ButtonStyle.primary, 
        emoji="💡",
        custom_id="suggestion_submit_button"
    )
    async def submit_suggestion(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SuggestionModal(self.cog)
        await interaction.response.send_modal(modal)


class SuggestionBox(commands.Cog):
    """Anonymous suggestion box for server members with interactive buttons."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        
        default_guild = {
            "suggestion_channel": None,
            "log_channel": None,
            "next_id": 1,
            "suggestions": {},
            "embed_message_id": None
        }
        
        self.config.register_guild(**default_guild)

        self.status_colors = {
            "pending": discord.Color.blue(),
            "approved": discord.Color.green(),
            "denied": discord.Color.red(),
            "considering": discord.Color.orange(),
            "implemented": discord.Color.purple()
        }
        self.status_emojis = {
            "pending": "⏳",
            "approved": "✅",
            "denied": "❌",
            "considering": "🤔",
            "implemented": "🎉"
        }

    async def cog_load(self):
        """Called when the cog is loaded."""
        # Add persistent view
        self.bot.add_view(SuggestionView(self))

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        """Handle reactions on suggestion messages."""
        if payload.user_id == self.bot.user.id:
            return
        
        # Check if it's a reaction on a suggestion message
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        
        user = guild.get_member(payload.user_id)
        if not user:
            return
        
        # Check if user has mod permissions
        if not (user.guild_permissions.manage_messages or await self.bot.is_mod(user)):
            return
        
        guild_config = self.config.guild(guild)
        suggestions = await guild_config.suggestions()
        
        # Find the suggestion with this message ID
        suggestion_id = None
        suggestion_data = None
        for sid, data in suggestions.items():
            if data.get("message_id") == payload.message_id:
                suggestion_id = sid
                suggestion_data = data
                break
        
        if not suggestion_data:
            return
        
        # Only handle ✅ and ❌ reactions
        if str(payload.emoji) not in ["✅", "❌"]:
            return
        
        channel = guild.get_channel(payload.channel_id)
        if not channel:
            return
        
        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return
        
        # Determine status based on reaction
        if str(payload.emoji) == "✅":
            new_status = "approved"
            status_text = "approved"
            response_message = "✅ **Your suggestion has been approved!** Thank you for your valuable input."
        else:  # ❌
            new_status = "denied"
            status_text = "denied"
            response_message = "❌ **Your suggestion has been reviewed and will not be implemented at this time.** Thank you for your feedback."
        
        # Update suggestion data
        suggestion_data["status"] = new_status
        suggestion_data["moderator"] = str(user)
        suggestion_data["moderator_id"] = user.id
        suggestions[suggestion_id] = suggestion_data
        await guild_config.suggestions.set(suggestions)
        
        # Update the embed
        try:
            timestamp = datetime.fromisoformat(suggestion_data["timestamp"])
        except (ValueError, KeyError):
            timestamp = datetime.now()
        
        updated_embed = discord.Embed(
            title=f"{self.status_emojis[new_status]} Suggestion #{suggestion_id} - {new_status.title()}",
            description=suggestion_data["content"],
            color=self.status_colors[new_status],
            timestamp=timestamp
        )
        updated_embed.set_footer(text=f"Status updated by {user.display_name}")
        
        await message.edit(embed=updated_embed)
        await message.clear_reactions()
        
        # Send feedback to the original author
        try:
            original_author = guild.get_member(suggestion_data.get("author_id"))
            if original_author:
                feedback_embed = discord.Embed(
                    title=f"Suggestion #{suggestion_id} Update",
                    description=response_message,
                    color=self.status_colors[new_status]
                )
                feedback_embed.add_field(
                    name="Your Suggestion",
                    value=suggestion_data["content"][:500] + ("..." if len(suggestion_data["content"]) > 500 else ""),
                    inline=False
                )
                await original_author.send(embed=feedback_embed)
        except (discord.Forbidden, discord.NotFound, AttributeError):
            pass  # Can't send DM or user not found

    @commands.group(name="suggestionbox", aliases=["sbox"])
    @commands.guild_only()
    async def suggestionbox(self, ctx):
        """Suggestion box commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @suggestionbox.command(name="setup")
    @checks.admin_or_permissions(manage_guild=True)
    async def setup_suggestion_box(self, ctx, suggestion_channel: discord.TextChannel, log_channel: discord.TextChannel):
        """Set up the suggestion box with interactive embed.

        Parameters:
        - suggestion_channel: Channel where the suggestion embed will be posted
        - log_channel: Channel where suggestions will be logged for review
        """
        # Store configuration
        guild_config = self.config.guild(ctx.guild)
        await guild_config.suggestion_channel.set(suggestion_channel.id)
        await guild_config.log_channel.set(log_channel.id)
        
        # Create and send the interactive embed
        embed = discord.Embed(
            title="💡 Suggestion Box",
            description=(
                "Have an idea to improve our server? We'd love to hear it!\n\n"
                "Click the button below to submit your suggestion anonymously. "
                "Your feedback helps us make this community better for everyone.\n\n"
                "**Guidelines:**\n"
                "• Keep suggestions constructive and specific\n"
                "• One suggestion per submission\n"
                "• All suggestions are reviewed by our team"
            ),
            color=discord.Color.blurple()
        )
        embed.set_footer(text="Your suggestions are completely anonymous")
        
        view = SuggestionView(self)
        
        try:
            # Delete old embed if it exists
            old_message_id = await guild_config.embed_message_id()
            if old_message_id:
                try:
                    old_message = await suggestion_channel.fetch_message(old_message_id)
                    await old_message.delete()
                except discord.NotFound:
                    pass
            
            # Send new embed
            embed_message = await suggestion_channel.send(embed=embed, view=view)
            await guild_config.embed_message_id.set(embed_message.id)
            
            # Confirmation message
            setup_embed = discord.Embed(
                title="✅ Suggestion Box Setup Complete",
                description=(
                    f"**Suggestion Channel:** {suggestion_channel.mention}\n"
                    f"**Log Channel:** {log_channel.mention}\n\n"
                    f"The interactive suggestion embed has been posted in {suggestion_channel.mention}. "
                    f"All submissions will be logged in {log_channel.mention} for review."
                ),
                color=discord.Color.green()
            )
            await ctx.send(embed=setup_embed)
            
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to send messages in the suggestion channel.")
        except Exception as e:
            await ctx.send(f"❌ An error occurred during setup: {str(e)}")

    @suggestionbox.command(name="status")
    @checks.mod_or_permissions(manage_messages=True)
    async def suggestion_status(self, ctx, suggestion_id: int, status: str, *, reason: Optional[str] = None):
        """Update the status of a suggestion manually.
        
        Status options: approved, denied, considering, implemented
        """
        status = status.lower()
        
        if status not in self.status_emojis:
            await ctx.send(f"❌ Invalid status. Valid options: {', '.join(self.status_emojis.keys())}")
            return
        
        guild_config = self.config.guild(ctx.guild)
        suggestions = await guild_config.suggestions()
        
        if str(suggestion_id) not in suggestions:
            await ctx.send(f"❌ Suggestion #{suggestion_id} not found.")
            return
        
        suggestion_data = suggestions[str(suggestion_id)]
        
        # Update suggestion status
        suggestion_data["status"] = status
        suggestion_data["moderator"] = str(ctx.author)
        suggestion_data["moderator_id"] = ctx.author.id
        suggestion_data["reason"] = reason
        
        suggestions[str(suggestion_id)] = suggestion_data
        await guild_config.suggestions.set(suggestions)
        
        # Update the original message
        try:
            channel = ctx.guild.get_channel(suggestion_data["channel_id"])
            message = await channel.fetch_message(suggestion_data["message_id"])
            
            try:
                timestamp = datetime.fromisoformat(suggestion_data["timestamp"])
            except (ValueError, KeyError):
                timestamp = datetime.now()
            
            embed = discord.Embed(
                title=f"{self.status_emojis[status]} Suggestion #{suggestion_id} - {status.title()}",
                description=suggestion_data["content"],
                color=self.status_colors[status],
                timestamp=timestamp
            )
            
            if reason:
                embed.add_field(name="Moderator Note", value=reason, inline=False)
            
            embed.set_footer(text=f"Status updated by {ctx.author.display_name}")
            
            await message.edit(embed=embed)
            await message.clear_reactions()
            
            await ctx.send(f"✅ Updated suggestion #{suggestion_id} status to **{status}**.")
            
            # Send feedback to original author if status is approved/denied
            if status in ["approved", "denied"] and suggestion_data.get("author_id"):
                try:
                    original_author = ctx.guild.get_member(suggestion_data["author_id"])
                    if original_author:
                        if status == "approved":
                            response_message = "✅ **Your suggestion has been approved!** Thank you for your valuable input."
                        else:
                            response_message = "❌ **Your suggestion has been reviewed and will not be implemented at this time.** Thank you for your feedback."
                        
                        feedback_embed = discord.Embed(
                            title=f"Suggestion #{suggestion_id} Update",
                            description=response_message,
                            color=self.status_colors[status]
                        )
                        feedback_embed.add_field(
                            name="Your Suggestion",
                            value=suggestion_data["content"][:500] + ("..." if len(suggestion_data["content"]) > 500 else ""),
                            inline=False
                        )
                        if reason:
                            feedback_embed.add_field(name="Moderator Note", value=reason, inline=False)
                        
                        await original_author.send(embed=feedback_embed)
                except (discord.Forbidden, discord.NotFound, AttributeError):
                    pass
            
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
            if status not in self.status_emojis:
                return await ctx.send(f"❌ Invalid status. Valid options: {', '.join(self.status_emojis.keys())}")
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
            current_status = data.get("status", "pending")
            emoji = self.status_emojis.get(current_status, "❓")
            
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
        status = data.get("status", "pending")
        
        try:
            timestamp = datetime.fromisoformat(data["timestamp"])
        except (ValueError, KeyError):
            timestamp = datetime.now()
        
        embed = discord.Embed(
            title=f"💡 Suggestion #{suggestion_id}",
            description=data["content"],
            color=self.status_colors.get(status, discord.Color.blue()),
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
        embed_message_id = await guild_config.embed_message_id()
        
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
        else:
            embed.add_field(name="Log Channel", value="❌ Not configured", inline=False)
        
        embed.add_field(name="Next Suggestion ID", value=f"#{next_id}", inline=True)
        embed.add_field(name="Total Suggestions", value=suggestions_count, inline=True)
        
        if embed_message_id:
            embed.add_field(name="Interactive Embed", value="✅ Active", inline=True)
        else:
            embed.add_field(name="Interactive Embed", value="❌ Not found", inline=True)
        
        await ctx.send(embed=embed)

    @suggestionbox.command(name="refresh")
    @checks.admin_or_permissions(manage_guild=True)
    async def refresh_embed(self, ctx):
        """Refresh the interactive suggestion embed (useful after bot restart)."""
        guild_config = self.config.guild(ctx.guild)
        suggestion_channel_id = await guild_config.suggestion_channel()
        embed_message_id = await guild_config.embed_message_id()
        
        if not suggestion_channel_id:
            await ctx.send("❌ Suggestion box not set up. Use `sbox setup` first.")
            return
        
        suggestion_channel = ctx.guild.get_channel(suggestion_channel_id)
        if not suggestion_channel:
            await ctx.send("❌ Suggestion channel not found.")
            return
        
        # Create the embed and view
        embed = discord.Embed(
            title="💡 Suggestion Box",
            description=(
                "Have an idea to improve our server? We'd love to hear it!\n\n"
                "Click the button below to submit your suggestion anonymously. "
                "Your feedback helps us make this community better for everyone.\n\n"
                "**Guidelines:**\n"
                "• Keep suggestions constructive and specific\n"
                "• One suggestion per submission\n"
                "• All suggestions are reviewed by our team"
            ),
            color=discord.Color.blurple()
        )
        embed.set_footer(text="Your suggestions are completely anonymous")
        
        view = SuggestionView(self)
        
        try:
            # Try to edit existing message first
            if embed_message_id:
                try:
                    existing_message = await suggestion_channel.fetch_message(embed_message_id)
                    await existing_message.edit(embed=embed, view=view)
                    await ctx.send("✅ Suggestion embed refreshed successfully.")
                    return
                except discord.NotFound:
                    pass
            
            # If no existing message or it wasn't found, create a new one
            embed_message = await suggestion_channel.send(embed=embed, view=view)
            await guild_config.embed_message_id.set(embed_message.id)
            await ctx.send("✅ New suggestion embed created successfully.")
            
        except discord.Forbidden:
            await ctx.send("❌ I don't have permission to send messages in the suggestion channel.")
        except Exception as e:
            await ctx.send(f"❌ Error refreshing embed: {str(e)}")