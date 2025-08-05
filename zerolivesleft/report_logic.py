import discord
from datetime import datetime
import logging

log = logging.getLogger("red.Elkz.zerolivesleft.report")

class ReportButtonView(discord.ui.View):
    def __init__(self, modal):
        super().__init__(timeout=300)
        self.modal = modal

    @discord.ui.button(label="📋 Submit Report", style=discord.ButtonStyle.primary)
    async def submit_report_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(self.modal)

class ModeratorResponseModal(discord.ui.Modal):
    def __init__(self, report_logic, original_report_embed, original_message, reporter_id):
        super().__init__(title="Respond to Report", timeout=300)
        self.report_logic = report_logic
        self.original_report_embed = original_report_embed
        self.original_message = original_message
        self.reporter_id = reporter_id
        
        self.response_text = discord.ui.TextInput(
            label="Your Response",
            placeholder="Type your response or question to the reporter...",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=True
        )
        self.add_item(self.response_text)

    async def on_submit(self, interaction: discord.Interaction):
        await self.report_logic.handle_moderator_response(
            interaction,
            self.original_report_embed,
            self.original_message,
            self.reporter_id,
            self.response_text.value
        )

class FinalResponseModal(discord.ui.Modal):
    def __init__(self, report_logic, original_report_embed, original_message, reporter_id):
        super().__init__(title="Final Report Resolution", timeout=300)
        self.report_logic = report_logic
        self.original_report_embed = original_report_embed
        self.original_message = original_message
        self.reporter_id = reporter_id
        
        self.final_response = discord.ui.TextInput(
            label="Final Resolution",
            placeholder="Type your final response to close this report...",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=True
        )
        self.add_item(self.final_response)

    async def on_submit(self, interaction: discord.Interaction):
        await self.report_logic.handle_final_response(
            interaction,
            self.original_report_embed,
            self.original_message,
            self.reporter_id,
            self.final_response.value
        )

class ReportModerationView(discord.ui.View):
    def __init__(self, report_logic, report_embed, reporter_id):
        super().__init__(timeout=None)  # Persistent view
        self.report_logic = report_logic
        self.report_embed = report_embed
        self.reporter_id = reporter_id

    @discord.ui.button(label="Respond", style=discord.ButtonStyle.secondary, emoji="📝", custom_id="report_respond")
    async def respond_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ModeratorResponseModal(
            self.report_logic, 
            self.report_embed, 
            interaction.message,
            self.reporter_id
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Ask Question", style=discord.ButtonStyle.primary, emoji="❓", custom_id="report_question")
    async def ask_question_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ModeratorResponseModal(
            self.report_logic, 
            self.report_embed, 
            interaction.message,
            self.reporter_id
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Resolve", style=discord.ButtonStyle.success, emoji="✅", custom_id="report_resolve")
    async def resolve_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = FinalResponseModal(
            self.report_logic, 
            self.report_embed, 
            interaction.message,
            self.reporter_id
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Dismiss", style=discord.ButtonStyle.danger, emoji="❌", custom_id="report_dismiss")
    async def dismiss_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Update the original message to show dismissed status
        embed = self.report_embed.copy()
        embed.color = discord.Color.dark_gray()
        embed.set_footer(text=f"{embed.footer.text} • Dismissed by moderator")
        
        # Disable all buttons
        for item in self.children:
            item.disabled = True
        
        await interaction.response.edit_message(embed=embed, view=self)
        
        # Send anonymous dismissal message to reporter
        try:
            reporter = interaction.guild.get_member(self.reporter_id)
            if reporter:
                report_id = None
                for field in embed.fields:
                    if field.name == "🆔 Report ID":
                        report_id = field.value
                        break
                
                dm_embed = discord.Embed(
                    title="📋 Report Update",
                    description=f"Your report {report_id} has been reviewed and dismissed.",
                    color=discord.Color.dark_gray(),
                    timestamp=datetime.utcnow()
                )
                dm_embed.add_field(
                    name="Status",
                    value="Dismissed - No further action required",
                    inline=False
                )
                
                await reporter.send(embed=dm_embed)
        except Exception as e:
            log.error(f"Failed to send dismissal DM to reporter: {e}")

    @discord.ui.button(label="Under Review", style=discord.ButtonStyle.blurple, emoji="👀", custom_id="report_review")
    async def under_review_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Update the original message to show under review status
        embed = self.report_embed.copy()
        embed.color = discord.Color.blue()
        embed.set_footer(text=f"{embed.footer.text} • Under Review")
        
        await interaction.response.edit_message(embed=embed, view=self)
        
        # Send anonymous status update to reporter
        try:
            reporter = interaction.guild.get_member(self.reporter_id)
            if reporter:
                report_id = None
                for field in embed.fields:
                    if field.name == "🆔 Report ID":
                        report_id = field.value
                        break
                
                dm_embed = discord.Embed(
                    title="📋 Report Update",
                    description=f"Your report {report_id} is now under review.",
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )
                dm_embed.add_field(
                    name="Status",
                    value="Under Review - A moderator is investigating your report",
                    inline=False
                )
                
                await reporter.send(embed=dm_embed)
        except Exception as e:
            log.error(f"Failed to send under review DM to reporter: {e}")

class ReportModal(discord.ui.Modal):
    def __init__(self, report_logic):
        super().__init__(title="Submit a Report", timeout=300)
        self.report_logic = report_logic
        
        # User being reported
        self.reported_user = discord.ui.TextInput(
            label="User Being Reported",
            placeholder="Enter username, user ID, or @mention",
            max_length=100,
            required=True
        )
        self.add_item(self.reported_user)
        
        # Reason for report
        self.reason = discord.ui.TextInput(
            label="Reason for Report",
            placeholder="Briefly describe the violation (e.g., harassment, spam, etc.)",
            max_length=200,
            required=True
        )
        self.add_item(self.reason)
        
        # Detailed description
        self.description = discord.ui.TextInput(
            label="Detailed Description",
            placeholder="Provide detailed information about the incident",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=True
        )
        self.add_item(self.description)
        
        # Evidence/Links
        self.evidence = discord.ui.TextInput(
            label="Evidence (Optional)",
            placeholder="Message links, screenshot URLs, or other evidence",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=False
        )
        self.add_item(self.evidence)
        
        # When did this occur
        self.when_occurred = discord.ui.TextInput(
            label="When Did This Occur?",
            placeholder="e.g., 'Today at 3 PM', 'Yesterday', 'Last week'",
            max_length=100,
            required=False
        )
        self.add_item(self.when_occurred)

    async def on_submit(self, interaction: discord.Interaction):
        await self.report_logic.handle_report_submission(
            interaction,
            self.reported_user.value,
            self.reason.value,
            self.description.value,
            self.evidence.value,
            self.when_occurred.value
        )

class ReportLogic:
    """Logic for handling the report system within the main zerolivesleft cog."""
    
    def __init__(self, main_cog):
        self.main_cog = main_cog
        self.bot = main_cog.bot
        self.config = main_cog.config
        self.user_cooldowns = {}
        self.active_reports = {}  # Store active reports for DM tracking
        
        # Register report-specific config
        self._register_config()
    
    def _register_config(self):
        """Register report-specific configuration in the main config."""
        # This adds to the existing guild config structure
        default_report_config = {
            "report_channel": None,
            "report_cooldown": 300,  # 5 minutes
            "report_allowed_roles": [],  # Empty means everyone can report
            "report_log_enabled": True,
        }
        
        # Since we can't modify the existing registration, we'll handle this in the main cog
        log.info("Report system initialized within main cog")
    
    async def get_report_config(self, guild):
        """Get report configuration for a guild."""
        guild_config = await self.config.guild(guild).all()
        
        # Use existing keys or defaults if they don't exist
        return {
            'report_channel': guild_config.get('report_channel'),
            'report_cooldown': guild_config.get('report_cooldown', 300),
            'report_allowed_roles': guild_config.get('report_allowed_roles', []),
            'report_log_enabled': guild_config.get('report_log_enabled', True),
        }
    
    async def set_report_config(self, guild, key, value):
        """Set report configuration for a guild."""
        await self.config.guild(guild).set_raw(key, value=value)
    
    def check_user_cooldown(self, user_id: int, cooldown_seconds: int) -> bool:
        """Check if user is on cooldown."""
        if user_id in self.user_cooldowns:
            remaining = self.user_cooldowns[user_id] - datetime.utcnow().timestamp()
            return remaining > 0
        return False
    
    def set_user_cooldown(self, user_id: int, cooldown_seconds: int):
        """Set cooldown for user."""
        self.user_cooldowns[user_id] = datetime.utcnow().timestamp() + cooldown_seconds
    
    async def store_active_report(self, reporter_id, report_id, report_message):
        """Store active report for DM response tracking."""
        self.active_reports[reporter_id] = {
            'report_id': report_id,
            'report_message': report_message,
            'last_response_time': datetime.utcnow()
        }
    
    async def handle_dm_response(self, message):
        """Handle DM responses from reporters."""
        user_id = message.author.id
        
        if user_id not in self.active_reports:
            return False  # Not an active report response
        
        report_info = self.active_reports[user_id]
        report_message = report_info['report_message']
        report_id = report_info['report_id']
        
        try:
            # Create embed for the DM response
            response_embed = discord.Embed(
                title="📬 Reporter Response Received",
                description=f"The reporter has responded to {report_id}:",
                color=discord.Color.green(),
                timestamp=datetime.utcnow()
            )
            response_embed.add_field(
                name="Response",
                value=message.content[:1000] + ("..." if len(message.content) > 1000 else ""),
                inline=False
            )
            response_embed.add_field(
                name="Reporter",
                value=f"{message.author.mention} ({message.author})",
                inline=True
            )
            
            # Send the response to the report channel as a reply to the original report
            await report_message.reply(embed=response_embed)
            
            # Update the active report timestamp
            self.active_reports[user_id]['last_response_time'] = datetime.utcnow()
            
            # Send confirmation to the reporter
            confirm_embed = discord.Embed(
                title="✅ Response Received",
                description=f"Your response to {report_id} has been forwarded to the moderators.",
                color=discord.Color.green()
            )
            await message.author.send(embed=confirm_embed)
            
            return True
            
        except Exception as e:
            log.error(f"Error handling DM response: {e}")
            return False
    
    async def cleanup_old_reports(self):
        """Clean up old report tracking (call this periodically)."""
        cutoff_time = datetime.utcnow().timestamp() - (24 * 60 * 60)  # 24 hours
        
        to_remove = []
        for user_id, report_info in self.active_reports.items():
            if report_info['last_response_time'].timestamp() < cutoff_time:
                to_remove.append(user_id)
        
        for user_id in to_remove:
            del self.active_reports[user_id]
    
    async def log_report(self, guild, reporter, reported_user, reason):
        """Log report submissions for tracking purposes."""
        config = await self.get_report_config(guild)
        if not config['report_log_enabled']:
            return
            
        log.info(f"[REPORT] {guild.name} - {reporter} reported {reported_user} for: {reason}")
    
    async def submit_report(self, ctx):
        """Handle the !report command - opens the modal."""
        config = await self.get_report_config(ctx.guild)
        
        # Check if user has permission
        if config['report_allowed_roles']:
            user_role_ids = [role.id for role in ctx.author.roles]
            if not any(role_id in config['report_allowed_roles'] for role_id in user_role_ids):
                await ctx.send("❌ You don't have permission to submit reports.", ephemeral=True)
                return
        
        # Check cooldown
        if self.check_user_cooldown(ctx.author.id, config['report_cooldown']):
            remaining = self.user_cooldowns[ctx.author.id] - datetime.utcnow().timestamp()
            await ctx.send(
                f"⏱️ You're on cooldown. Please wait {int(remaining)} seconds before submitting another report.",
                ephemeral=True
            )
            return
        
        # Check if report channel is configured
        if not config['report_channel']:
            await ctx.send(
                "❌ The report system hasn't been configured yet. Please contact an administrator.",
                ephemeral=True
            )
            return
        
        # Set cooldown
        self.set_user_cooldown(ctx.author.id, config['report_cooldown'])
        
        # Create and send modal - handle both slash and regular commands
        modal = ReportModal(self)
        
        # Check if this is a slash command with interaction
        if hasattr(ctx, 'interaction') and ctx.interaction:
            await ctx.interaction.response.send_modal(modal)
        else:
            # For regular commands, we need to send a message with a button to open the modal
            view = ReportButtonView(modal)
            await ctx.send(
                "📋 Click the button below to open the report form:",
                view=view,
                ephemeral=True
            )
    
    async def handle_report_submission(self, interaction, reported_user, reason, description, evidence, when_occurred):
        """Handle the actual report submission from the modal."""
        config = await self.get_report_config(interaction.guild)
        
        # Get report channel
        report_channel = interaction.guild.get_channel(config['report_channel'])
        if not report_channel:
            await interaction.response.send_message(
                "❌ The configured report channel could not be found. Please contact an administrator.",
                ephemeral=True
            )
            return
        
        # Create report embed
        embed = discord.Embed(
            title="📋 New Report Submitted",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )
        
        embed.add_field(
            name="👤 Reported User",
            value=reported_user,
            inline=False
        )
        
        embed.add_field(
            name="⚠️ Reason",
            value=reason,
            inline=False
        )
        
        embed.add_field(
            name="📝 Description",
            value=description,
            inline=False
        )
        
        if evidence:
            embed.add_field(
                name="🔍 Evidence",
                value=evidence,
                inline=False
            )
            
        if when_occurred:
            embed.add_field(
                name="📅 When",
                value=when_occurred,
                inline=True
            )
        
        embed.add_field(
            name="📤 Submitted By",
            value=f"{interaction.user.mention} ({interaction.user})",
            inline=True
        )
        
        embed.add_field(
            name="🆔 Report ID",
            value=f"R-{interaction.id}",
            inline=True
        )
        
        embed.set_footer(text=f"Server: {interaction.guild.name}")
        
        try:
            # Send to report channel
            view = ReportModerationView(self, embed, interaction.user.id)
            report_message = await report_channel.send(embed=embed, view=view)
            
            # Log the report
            await self.log_report(interaction.guild, interaction.user, reported_user, reason)
            
            await interaction.response.send_message(
                f"✅ Your report has been submitted successfully!\n"
                f"Report ID: `R-{interaction.id}`\n"
                f"Moderators have been notified and will review your report. You'll receive updates via DM.",
                ephemeral=True
            )
            
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to send messages to the report channel. Please contact an administrator.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ An error occurred while submitting your report: {str(e)}",
                ephemeral=True
            )
    
    async def handle_moderator_response(self, interaction, original_embed, original_message, reporter_id, response_text):
        """Handle moderator response to a report."""
        try:
            # Send anonymous DM to the reporter
            reporter = interaction.guild.get_member(reporter_id)
            if reporter:
                # Get report ID from the embed
                report_id = None
                for field in original_embed.fields:
                    if field.name == "🆔 Report ID":
                        report_id = field.value
                        break
                
                dm_embed = discord.Embed(
                    title="📋 Report Update",
                    description=f"A moderator has responded to your report {report_id}:",
                    color=discord.Color.orange(),
                    timestamp=datetime.utcnow()
                )
                dm_embed.add_field(
                    name="Moderator Response",
                    value=response_text,
                    inline=False
                )
                dm_embed.add_field(
                    name="What's Next?",
                    value="You can reply to this DM with additional information if needed.",
                    inline=False
                )
                
                # Store the report info for tracking DM responses
                await self.store_active_report(reporter_id, report_id, original_message)
                
                await reporter.send(embed=dm_embed)
                
                # Add a note to the original report
                updated_embed = original_embed.copy()
                updated_embed.color = discord.Color.orange()
                updated_embed.add_field(
                    name="📨 Moderator Response Sent",
                    value=f"Response sent at {datetime.utcnow().strftime('%H:%M:%S')}",
                    inline=False
                )
                
                await original_message.edit(embed=updated_embed)
                
                await interaction.response.send_message(
                    f"✅ Your response has been sent anonymously to the reporter.",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ Could not find the reporter to send the response.",
                    ephemeral=True
                )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Could not send DM to the reporter. They may have DMs disabled.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Error sending response: {str(e)}",
                ephemeral=True
            )

    async def handle_final_response(self, interaction, original_embed, original_message, reporter_id, final_response):
        """Handle final resolution of a report."""
        try:
            # Send final response to the reporter
            reporter = interaction.guild.get_member(reporter_id)
            if reporter:
                # Get report ID from the embed
                report_id = None
                for field in original_embed.fields:
                    if field.name == "🆔 Report ID":
                        report_id = field.value
                        break
                
                dm_embed = discord.Embed(
                    title="📋 Report Resolved",
                    description=f"Your report {report_id} has been resolved:",
                    color=discord.Color.green(),
                    timestamp=datetime.utcnow()
                )
                dm_embed.add_field(
                    name="Final Resolution",
                    value=final_response,
                    inline=False
                )
                dm_embed.add_field(
                    name="Status",
                    value="✅ Closed - This report has been resolved",
                    inline=False
                )
                
                await reporter.send(embed=dm_embed)
                
                # Update the original report to show resolved status
                resolved_embed = original_embed.copy()
                resolved_embed.color = discord.Color.green()
                resolved_embed.set_footer(text=f"{original_embed.footer.text} • Resolved")
                resolved_embed.add_field(
                    name="✅ Resolution",
                    value=f"Resolved at {datetime.utcnow().strftime('%H:%M:%S')}",
                    inline=False
                )
                
                # Disable all buttons in the view
                view = ReportModerationView(self, resolved_embed, reporter_id)
                for item in view.children:
                    item.disabled = True
                
                await original_message.edit(embed=resolved_embed, view=view)
                
                # Remove from active reports tracking
                if reporter_id in self.active_reports:
                    del self.active_reports[reporter_id]
                
                await interaction.response.send_message(
                    f"✅ Report has been resolved and the reporter has been notified.",
                    ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "❌ Could not find the reporter to send the final response.",
                    ephemeral=True
                )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Could not send DM to the reporter. They may have DMs disabled.",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Error sending final response: {str(e)}",
                ephemeral=True
            )
    
    # Configuration commands
    async def set_report_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel where reports will be sent."""
        await self.set_report_config(ctx.guild, 'report_channel', channel.id)
        await ctx.send(f"✅ Report channel set to {channel.mention}")
    
    async def set_cooldown(self, ctx, seconds: int):
        """Set the cooldown between reports (in seconds)."""
        if seconds < 0:
            await ctx.send("❌ Cooldown cannot be negative.")
            return
            
        await self.set_report_config(ctx.guild, 'report_cooldown', seconds)
        await ctx.send(f"✅ Report cooldown set to {seconds} seconds.")
    
    async def add_allowed_role(self, ctx, role: discord.Role):
        """Add a role that can submit reports."""
        config = await self.get_report_config(ctx.guild)
        roles = config['report_allowed_roles']
        
        if role.id not in roles:
            roles.append(role.id)
            await self.set_report_config(ctx.guild, 'report_allowed_roles', roles)
            await ctx.send(f"✅ {role.mention} can now submit reports.")
        else:
            await ctx.send(f"❌ {role.mention} is already allowed to submit reports.")
    
    async def remove_allowed_role(self, ctx, role: discord.Role):
        """Remove a role from being able to submit reports."""
        config = await self.get_report_config(ctx.guild)
        roles = config['report_allowed_roles']
        
        if role.id in roles:
            roles.remove(role.id)
            await self.set_report_config(ctx.guild, 'report_allowed_roles', roles)
            await ctx.send(f"✅ {role.mention} can no longer submit reports.")
        else:
            await ctx.send(f"❌ {role.mention} wasn't allowed to submit reports.")
    
    async def clear_allowed_roles(self, ctx):
        """Clear all role restrictions (everyone can report)."""
        await self.set_report_config(ctx.guild, 'report_allowed_roles', [])
        await ctx.send("✅ Role restrictions cleared. Everyone can now submit reports.")
    
    async def show_config(self, ctx):
        """Show current report system configuration."""
        config = await self.get_report_config(ctx.guild)
        
        embed = discord.Embed(
            title="📋 Report System Configuration",
            color=discord.Color.blue()
        )
        
        report_channel = ctx.guild.get_channel(config['report_channel']) if config['report_channel'] else None
        embed.add_field(
            name="Report Channel",
            value=report_channel.mention if report_channel else "Not set",
            inline=False
        )
        
        embed.add_field(
            name="Cooldown",
            value=f"{config['report_cooldown']} seconds",
            inline=True
        )
        
        embed.add_field(
            name="Log Reports",
            value="✅ Yes" if config['report_log_enabled'] else "❌ No",
            inline=True
        )
        
        if config['report_allowed_roles']:
            roles = [ctx.guild.get_role(role_id) for role_id in config['report_allowed_roles']]
            roles = [role.mention for role in roles if role]
            embed.add_field(
                name="Allowed Roles",
                value=", ".join(roles) if roles else "None",
                inline=False
            )
        else:
            embed.add_field(
                name="Allowed Roles",
                value="Everyone can report",
                inline=False
            )
        
        await ctx.send(embed=embed)
    
    async def show_stats(self, ctx):
        """Show report system statistics."""
        embed = discord.Embed(
            title="📋 Report Statistics",
            color=discord.Color.blue()
        )
        
        active_count = len(self.active_reports)
        embed.add_field(
            name="Active Reports",
            value=f"{active_count} reports currently tracking DM responses",
            inline=False
        )
        
        if active_count > 0:
            recent_reports = []
            for user_id, report_info in list(self.active_reports.items())[:5]:
                time_ago = datetime.utcnow() - report_info['last_response_time']
                hours_ago = int(time_ago.total_seconds() / 3600)
                recent_reports.append(f"• {report_info['report_id']} ({hours_ago}h ago)")
            
            embed.add_field(
                name="Recent Active Reports",
                value="\n".join(recent_reports),
                inline=False
            )
        
        await ctx.send(embed=embed)