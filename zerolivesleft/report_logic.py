import discord
from datetime import datetime
import logging

log = logging.getLogger("red.Elkz.zerolivesleft.report")

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
    
    def check_cooldown(self, user_id: int, cooldown_seconds: int) -> bool:
        """Check if user is on cooldown."""
        if user_id in self.user_cooldowns:
            remaining = self.user_cooldowns[user_id] - datetime.utcnow().timestamp()
            return remaining > 0
        return False
    
    def set_cooldown(self, user_id: int, cooldown_seconds: int):
        """Set cooldown for user."""
        self.user_cooldowns[user_id] = datetime.utcnow().timestamp() + cooldown_seconds
    
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
        if self.check_cooldown(ctx.author.id, config['report_cooldown']):
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
        self.set_cooldown(ctx.author.id, config['report_cooldown'])
        
        # Create and send modal
        modal = ReportModal(self)
        await ctx.interaction.response.send_modal(modal)
    
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
            report_message = await report_channel.send(embed=embed)
            
            # Add reaction buttons for moderators
            await report_message.add_reaction("✅")  # Handled
            await report_message.add_reaction("❌")  # Dismissed
            await report_message.add_reaction("👀")  # Under review
            
            # Log the report
            await self.log_report(interaction.guild, interaction.user, reported_user, reason)
            
            await interaction.response.send_message(
                f"✅ Your report has been submitted successfully!\n"
                f"Report ID: `R-{interaction.id}`\n"
                f"Moderators have been notified and will review your report.",
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
        # This is a placeholder - you could expand this to track actual statistics
        embed = discord.Embed(
            title="📋 Report Statistics",
            description="Report tracking is enabled but detailed statistics are not yet implemented.",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)