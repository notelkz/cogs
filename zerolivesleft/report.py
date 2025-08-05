import discord
from discord.ext import commands
from redbot.core import commands as red_commands, Config, checks
from redbot.core.utils.chat_formatting import box, pagify
import asyncio
from datetime import datetime
from typing import Optional

class ReportModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Submit a Report", timeout=300)
        
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
        # Get the cog instance to access config
        cog = interaction.client.get_cog("Report")
        if not cog:
            await interaction.response.send_message("❌ Report system is not available.", ephemeral=True)
            return
            
        # Get report channel
        report_channel_id = await cog.config.guild(interaction.guild).report_channel()
        if not report_channel_id:
            await interaction.response.send_message(
                "❌ No report channel has been configured. Please contact an administrator.",
                ephemeral=True
            )
            return
            
        report_channel = interaction.guild.get_channel(report_channel_id)
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
            value=self.reported_user.value,
            inline=False
        )
        
        embed.add_field(
            name="⚠️ Reason",
            value=self.reason.value,
            inline=False
        )
        
        embed.add_field(
            name="📝 Description",
            value=self.description.value,
            inline=False
        )
        
        if self.evidence.value:
            embed.add_field(
                name="🔍 Evidence",
                value=self.evidence.value,
                inline=False
            )
            
        if self.when_occurred.value:
            embed.add_field(
                name="📅 When",
                value=self.when_occurred.value,
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
            await cog.log_report(interaction.guild, interaction.user, self.reported_user.value, self.reason.value)
            
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

class Report(red_commands.Cog):
    """A comprehensive reporting system for server moderation."""
    
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890, force_registration=True)
        
        default_guild = {
            "report_channel": None,
            "use_forum": False,
            "forum_channel": None,
            "log_reports": True,
            "report_cooldown": 300,  # 5 minutes
            "allowed_roles": [],  # Empty means everyone can report
        }
        
        self.config.register_guild(**default_guild)
        self.user_cooldowns = {}
    
    async def log_report(self, guild, reporter, reported_user, reason):
        """Log report submissions for tracking purposes."""
        if not await self.config.guild(guild).log_reports():
            return
            
        # You can expand this to log to a database or file
        print(f"[REPORT LOG] {guild.name} - {reporter} reported {reported_user} for: {reason}")
    
    def check_cooldown(self, user_id: int) -> bool:
        """Check if user is on cooldown."""
        if user_id in self.user_cooldowns:
            remaining = self.user_cooldowns[user_id] - datetime.utcnow().timestamp()
            return remaining > 0
        return False
    
    def set_cooldown(self, user_id: int, cooldown_seconds: int):
        """Set cooldown for user."""
        self.user_cooldowns[user_id] = datetime.utcnow().timestamp() + cooldown_seconds

    @red_commands.hybrid_command(name="report")
    async def report_command(self, ctx):
        """Submit a report using an interactive form."""
        
        # Check if user has permission
        allowed_roles = await self.config.guild(ctx.guild).allowed_roles()
        if allowed_roles:
            user_role_ids = [role.id for role in ctx.author.roles]
            if not any(role_id in allowed_roles for role_id in user_role_ids):
                await ctx.send("❌ You don't have permission to submit reports.", ephemeral=True)
                return
        
        # Check cooldown
        cooldown_time = await self.config.guild(ctx.guild).report_cooldown()
        if self.check_cooldown(ctx.author.id):
            remaining = self.user_cooldowns[ctx.author.id] - datetime.utcnow().timestamp()
            await ctx.send(
                f"⏱️ You're on cooldown. Please wait {int(remaining)} seconds before submitting another report.",
                ephemeral=True
            )
            return
        
        # Check if report channel is configured
        report_channel_id = await self.config.guild(ctx.guild).report_channel()
        if not report_channel_id:
            await ctx.send(
                "❌ The report system hasn't been configured yet. Please contact an administrator.",
                ephemeral=True
            )
            return
        
        # Set cooldown
        self.set_cooldown(ctx.author.id, cooldown_time)
        
        # Create and send modal
        modal = ReportModal()
        await ctx.interaction.response.send_modal(modal)

    @red_commands.group(name="reportset")
    @checks.admin_or_permissions(manage_guild=True)
    async def report_settings(self, ctx):
        """Configure the report system."""
        if ctx.invoked_subcommand is None:
            settings = await self.config.guild(ctx.guild).all()
            
            embed = discord.Embed(
                title="Report System Configuration",
                color=discord.Color.blue()
            )
            
            report_channel = ctx.guild.get_channel(settings["report_channel"]) if settings["report_channel"] else None
            embed.add_field(
                name="Report Channel",
                value=report_channel.mention if report_channel else "Not set",
                inline=False
            )
            
            embed.add_field(
                name="Cooldown",
                value=f"{settings['report_cooldown']} seconds",
                inline=True
            )
            
            embed.add_field(
                name="Log Reports",
                value="✅ Yes" if settings["log_reports"] else "❌ No",
                inline=True
            )
            
            if settings["allowed_roles"]:
                roles = [ctx.guild.get_role(role_id) for role_id in settings["allowed_roles"]]
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

    @report_settings.command(name="channel")
    async def set_report_channel(self, ctx, channel: discord.TextChannel):
        """Set the channel where reports will be sent."""
        await self.config.guild(ctx.guild).report_channel.set(channel.id)
        await ctx.send(f"✅ Report channel set to {channel.mention}")

    @report_settings.command(name="cooldown")
    async def set_cooldown(self, ctx, seconds: int):
        """Set the cooldown between reports (in seconds)."""
        if seconds < 0:
            await ctx.send("❌ Cooldown cannot be negative.")
            return
            
        await self.config.guild(ctx.guild).report_cooldown.set(seconds)
        await ctx.send(f"✅ Report cooldown set to {seconds} seconds.")

    @report_settings.command(name="addrole")
    async def add_allowed_role(self, ctx, role: discord.Role):
        """Add a role that can submit reports."""
        async with self.config.guild(ctx.guild).allowed_roles() as roles:
            if role.id not in roles:
                roles.append(role.id)
                await ctx.send(f"✅ {role.mention} can now submit reports.")
            else:
                await ctx.send(f"❌ {role.mention} is already allowed to submit reports.")

    @report_settings.command(name="removerole")
    async def remove_allowed_role(self, ctx, role: discord.Role):
        """Remove a role from being able to submit reports."""
        async with self.config.guild(ctx.guild).allowed_roles() as roles:
            if role.id in roles:
                roles.remove(role.id)
                await ctx.send(f"✅ {role.mention} can no longer submit reports.")
            else:
                await ctx.send(f"❌ {role.mention} wasn't allowed to submit reports.")

    @report_settings.command(name="clearroles")
    async def clear_allowed_roles(self, ctx):
        """Clear all role restrictions (everyone can report)."""
        await self.config.guild(ctx.guild).allowed_roles.set([])
        await ctx.send("✅ Role restrictions cleared. Everyone can now submit reports.")

    @red_commands.command(name="reportstats")
    @checks.mod_or_permissions(manage_messages=True)
    async def report_stats(self, ctx):
        """View report system statistics."""
        # This is a placeholder - you could expand this to track actual statistics
        embed = discord.Embed(
            title="Report Statistics",
            description="Report tracking is enabled but detailed statistics are not yet implemented.",
            color=discord.Color.blue()
        )
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Report(bot))