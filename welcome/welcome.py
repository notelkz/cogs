import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import Optional, List, Dict
import datetime
import asyncio
from collections import deque

class Welcome(commands.Cog):
    """Welcome/goodbye messages with customizable embeds and raid protection"""
    
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "welcome_channel": None,
            "goodbye_channel": None,
            "welcome_message": "Welcome {member} to {server}!",
            "goodbye_message": "Goodbye {member}! They left the server.",
            "ban_message": "{member} was banned! 🔨",
            "welcome_enabled": False,
            "goodbye_enabled": False,
            "embed_color": 0x2ECC71,
            "ping_user": True,
            # Raid Protection Settings
            "raid_protection": False,
            "join_window": 30,  # Time window in seconds
            "join_threshold": 5,  # Number of joins within window to trigger
            "minimum_account_age": 7,  # Days
            "action_on_raid": "lockdown",  # lockdown, kick, or ban
            "alert_channel": None,
            "protected_roles": [],
            "lockdown_duration": 300,  # 5 minutes
        }
        self.config.register_guild(**default_guild)
        
        # Recent joins tracker
        self.recent_joins: Dict[int, deque] = {}
        # Lockdown status tracker
        self.lockdown_status: Dict[int, bool] = {}

    # ... (previous commands remain the same) ...

    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def raidprotect(self, ctx):
        """Raid protection configuration"""
        pass

    @raidprotect.command(name="toggle")
    async def toggle_raid_protection(self, ctx):
        """Toggle raid protection on/off"""
        current = await self.config.guild(ctx.guild).raid_protection()
        await self.config.guild(ctx.guild).raid_protection.set(not current)
        state = "enabled" if not current else "disabled"
        await ctx.send(f"Raid protection {state}")

    @raidprotect.command(name="settings")
    async def raid_settings(self, ctx, join_window: int = None, join_threshold: int = None, 
                          account_age: int = None):
        """Configure raid protection settings
        
        join_window: Time window in seconds to monitor joins
        join_threshold: Number of joins within window to trigger protection
        account_age: Minimum account age in days"""
        
        if join_window:
            await self.config.guild(ctx.guild).join_window.set(join_window)
        if join_threshold:
            await self.config.guild(ctx.guild).join_threshold.set(join_threshold)
        if account_age:
            await self.config.guild(ctx.guild).minimum_account_age.set(account_age)

        # Show current settings
        settings = {
            "Window": f"{await self.config.guild(ctx.guild).join_window()}s",
            "Threshold": await self.config.guild(ctx.guild).join_threshold(),
            "Min Account Age": f"{await self.config.guild(ctx.guild).minimum_account_age()} days"
        }
        
        embed = discord.Embed(title="Raid Protection Settings", color=await self.config.guild(ctx.guild).embed_color())
        for key, value in settings.items():
            embed.add_field(name=key, value=value)
        await ctx.send(embed=embed)

    @raidprotect.command(name="action")
    async def set_raid_action(self, ctx, action: str):
        """Set action to take during raid (lockdown/kick/ban)"""
        if action.lower() not in ["lockdown", "kick", "ban"]:
            await ctx.send("Invalid action. Choose: lockdown, kick, or ban")
            return
        
        await self.config.guild(ctx.guild).action_on_raid.set(action.lower())
        await ctx.send(f"Raid action set to: {action}")

    @raidprotect.command(name="alertchannel")
    async def set_alert_channel(self, ctx, channel: discord.TextChannel):
        """Set channel for raid alerts"""
        await self.config.guild(ctx.guild).alert_channel.set(channel.id)
        await ctx.send(f"Raid alerts will be sent to {channel.mention}")

    async def check_raid(self, member: discord.Member) -> bool:
        """Check if current join is part of a raid"""
        guild = member.guild
        
        if not await self.config.guild(guild).raid_protection():
            return False

        # Initialize recent joins tracker for this guild if needed
        if guild.id not in self.recent_joins:
            self.recent_joins[guild.id] = deque(maxlen=50)

        current_time = datetime.datetime.utcnow()
        join_window = await self.config.guild(guild).join_window()
        join_threshold = await self.config.guild(guild).join_threshold()

        # Add the new join
        self.recent_joins[guild.id].append(current_time)

        # Remove old joins outside the window
        while self.recent_joins[guild.id] and \
              (current_time - self.recent_joins[guild.id][0]).total_seconds() > join_window:
            self.recent_joins[guild.id].popleft()

        # Check if we've hit the threshold
        return len(self.recent_joins[guild.id]) >= join_threshold

    async def handle_raid(self, guild: discord.Guild):
        """Handle an ongoing raid"""
        action = await self.config.guild(guild).action_on_raid()
        alert_channel_id = await self.config.guild(guild).alert_channel()
        
        if alert_channel_id:
            alert_channel = guild.get_channel(alert_channel_id)
            if alert_channel:
                await alert_channel.send(f"🚨 **RAID DETECTED!** Taking action: {action}")

        recent_members = [member for member in guild.members 
                         if member.joined_at and 
                         (datetime.datetime.utcnow() - member.joined_at).total_seconds() < 
                         await self.config.guild(guild).join_window()]

        if action == "lockdown":
            # Enable server lockdown
            self.lockdown_status[guild.id] = True
            lockdown_duration = await self.config.guild(guild).lockdown_duration()
            
            # Disable join permissions
            try:
                await guild.default_role.edit(permissions=discord.Permissions.none())
                if alert_channel_id:
                    alert_channel = guild.get_channel(alert_channel_id)
                    if alert_channel:
                        await alert_channel.send(f"🔒 Server locked down for {lockdown_duration} seconds")
                
                # Schedule lockdown removal
                await asyncio.sleep(lockdown_duration)
                await guild.default_role.edit(permissions=discord.Permissions.general())
                self.lockdown_status[guild.id] = False
                
                if alert_channel:
                    await alert_channel.send("🔓 Lockdown lifted")
            except discord.Forbidden:
                if alert_channel:
                    await alert_channel.send("⚠️ Failed to lockdown server - insufficient permissions")

        elif action == "kick":
            for member in recent_members:
                try:
                    await member.kick(reason="Raid protection")
                except discord.Forbidden:
                    continue

        elif action == "ban":
            for member in recent_members:
                try:
                    await member.ban(reason="Raid protection", delete_message_days=1)
                except discord.Forbidden:
                    continue

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Enhanced member join handler with raid protection"""
        guild = member.guild

        # Check for raid protection
        if await self.config.guild(guild).raid_protection():
            # Check account age
            account_age = (datetime.datetime.utcnow() - member.created_at).days
            min_age = await self.config.guild(guild).minimum_account_age()
            
            if account_age < min_age:
                try:
                    await member.kick(reason=f"Account too new ({account_age} days old)")
                    return
                except discord.Forbidden:
                    pass

            # Check if server is in lockdown
            if self.lockdown_status.get(guild.id, False):
                try:
                    await member.kick(reason="Server is in lockdown mode")
                    return
                except discord.Forbidden:
                    pass

            # Check for raid
            if await self.check_raid(member):
                await self.handle_raid(guild)
                return

        # If we get here, proceed with normal welcome message
        await super().on_member_join(member)  # Call original welcome message logic

def setup(bot: Red):
    bot.add_cog(Welcome(bot))
