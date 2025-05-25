import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from typing import Optional, List, Dict
import datetime
import asyncio
import json
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
            "kick_message": "{member} was kicked! 👢",
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
            "log_channel": None,  # For logging all events
            # Custom Embed Settings
            "custom_welcome_embed": None,
            "use_custom_embed": False,
        }
        self.config.register_guild(**default_guild)
        
        # Recent joins tracker
        self.recent_joins: Dict[int, deque] = {}
        # Lockdown status tracker
        self.lockdown_status: Dict[int, bool] = {}

    def get_ordinal(self, number: int) -> str:
        """Convert a number to its ordinal representation (1st, 2nd, 3rd, etc.)"""
        if 10 <= number % 100 <= 20:
            suffix = 'th'
        else:
            suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')
        return f"{number}{suffix}"

    def get_milestone_message(self, count: int) -> str:
        """Get special message for milestone counts"""
        milestones = {
            100: "🎉 Wow! Our 100th member!",
            500: "🎊 Amazing! Member #500!",
            1000: "⭐ Incredible! Member #1000!",
            5000: "🌟 Phenomenal! Member #5000!",
            10000: "💫 Legendary! Member #10000!"
        }
        return milestones.get(count, "")

    async def log_event(self, guild: discord.Guild, embed: discord.Embed):
        """Log events to the designated logging channel"""
        log_channel_id = await self.config.guild(guild).log_channel()
        if log_channel_id:
            log_channel = guild.get_channel(log_channel_id)
            if log_channel:
                try:
                    await log_channel.send(embed=embed)
                except discord.Forbidden:
                    pass
    @commands.group()
    @commands.admin_or_permissions(manage_guild=True)
    async def welcomeset(self, ctx):
        """Welcome message configuration"""
        pass

    @welcomeset.command(name="channel")
    async def set_welcome_channel(self, ctx, channel: discord.TextChannel):
        """Set the welcome channel"""
        await self.config.guild(ctx.guild).welcome_channel.set(channel.id)
        await self.config.guild(ctx.guild).welcome_enabled.set(True)
        await ctx.send(f"Welcome channel set to {channel.mention}")

    @welcomeset.command(name="goodbye")
    async def set_goodbye_channel(self, ctx, channel: discord.TextChannel):
        """Set the goodbye channel"""
        await self.config.guild(ctx.guild).goodbye_channel.set(channel.id)
        await self.config.guild(ctx.guild).goodbye_enabled.set(True)
        await ctx.send(f"Goodbye channel set to {channel.mention}")

    @welcomeset.command(name="log")
    async def set_log_channel(self, ctx, channel: discord.TextChannel):
        """Set the logging channel for all events"""
        await self.config.guild(ctx.guild).log_channel.set(channel.id)
        await ctx.send(f"Log channel set to {channel.mention}")

    @welcomeset.command(name="message")
    async def set_welcome_message(self, ctx, *, message: str):
        """Set the welcome message"""
        await self.config.guild(ctx.guild).welcome_message.set(message)
        await ctx.send("Welcome message set!")

    @welcomeset.command(name="goodbyemsg")
    async def set_goodbye_message(self, ctx, *, message: str):
        """Set the goodbye message"""
        await self.config.guild(ctx.guild).goodbye_message.set(message)
        await ctx.send("Goodbye message set!")

    @welcomeset.command(name="color")
    async def set_color(self, ctx, color: discord.Color):
        """Set the embed color (hex format)"""
        await self.config.guild(ctx.guild).embed_color.set(color.value)
        await ctx.send(f"Embed color set to {color}")

    @welcomeset.command(name="embedjson")
    @commands.admin_or_permissions(manage_guild=True)
    async def set_welcome_embed_json(self, ctx, *, json_str: str = None):
        """Set a custom welcome embed using JSON
        
        Available variables: 
        - {member} - Member's name
        - {member.name} - Member's name
        - {member.mention} - Member's mention
        - {member.avatar_url} - Member's avatar URL
        - {server} - Server name
        - {server.member_count} - Server member count
        - {count} - Current member count
        - {count.ordinal} - Ordinal member count (1st, 2nd, etc.)
        - {milestone} - Special milestone message
        - {is_milestone} - "true" if milestone, "false" if not
        
        Example:
        ```json
        {
            "title": "Welcome to {server}!",
            "description": "Hey {member.mention}, you're our {count.ordinal} member! {milestone}",
            "color": 3066993,
            "thumbnail": {
                "url": "{member.avatar_url}"
            },
            "fields": [
                {
                    "name": "Member Count",
                    "value": "You are member #{count}!",
                    "inline": true
                }
            ],
            "footer": {
                "text": "Welcome to {server}!"
            },
            "timestamp": true
        }
        ```
        """
        if json_str is None:
            example = {
                "title": "Welcome to {server}!",
                "description": "Hey {member.mention}, you're our {count.ordinal} member! {milestone}",
                "color": 3066993,
                "thumbnail": {
                    "url": "{member.avatar_url}"
                },
                "fields": [
                    {
                        "name": "Member Count",
                        "value": "You are member #{count}!",
                        "inline": True
                    }
                ],
                "footer": {
                    "text": "Welcome to {server}!"
                },
                "timestamp": True
            }
            await ctx.send(f"Please provide JSON for the embed. Example:\n```json\n{json.dumps(example, indent=2)}\n```")
            return

        try:
            # Validate JSON format
            embed_data = json.loads(json_str)
            
            # Basic validation of required embed structure
            if not isinstance(embed_data, dict):
                await ctx.send("Error: JSON must be an object")
                return
            
            # Test embed creation
            test_embed = await self.create_custom_embed(embed_data, ctx.author, ctx.guild)
            
            # Save the embed JSON
            await self.config.guild(ctx.guild).custom_welcome_embed.set(embed_data)
            await self.config.guild(ctx.guild).use_custom_embed.set(True)
            
            await ctx.send("Custom embed set! Here's how it looks:", embed=test_embed)
            
        except json.JSONDecodeError:
            await ctx.send("Error: Invalid JSON format")
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

    @welcomeset.command(name="previewembed")
    @commands.admin_or_permissions(manage_guild=True)
    async def preview_welcome_embed(self, ctx):
        """Preview the current welcome embed"""
        use_custom = await self.config.guild(ctx.guild).use_custom_embed()
        if use_custom:
            embed_data = await self.config.guild(ctx.guild).custom_welcome_embed()
            if embed_data:
                embed = await self.create_custom_embed(embed_data, ctx.author, ctx.guild)
                await ctx.send("Current welcome embed:", embed=embed)
            else:
                await ctx.send("No custom embed set")
        else:
            # Show default embed
            message = await self.config.guild(ctx.guild).welcome_message()
            color = await self.config.guild(ctx.guild).embed_color()
            ping = await self.config.guild(ctx.guild).ping_user()
            
            embed = discord.Embed(
                title="👋 New Member!",
                description=message.format(
                    member=ctx.author.mention if ping else ctx.author.name,
                    server=ctx.guild.name
                ),
                color=color,
                timestamp=datetime.datetime.utcnow()
            )
            embed.set_thumbnail(url=ctx.author.avatar.url if ctx.author.avatar else ctx.author.default_avatar.url)
            embed.set_footer(text=f"Member #{len(ctx.guild.members)}")
            
            await ctx.send("Current welcome embed:", embed=embed)

    @welcomeset.command(name="resetembed")
    @commands.admin_or_permissions(manage_guild=True)
    async def reset_welcome_embed(self, ctx):
        """Reset to default welcome embed"""
        await self.config.guild(ctx.guild).use_custom_embed.set(False)
        await self.config.guild(ctx.guild).custom_welcome_embed.set(None)
        await ctx.send("Reset to default welcome embed")

    async def create_custom_embed(self, embed_data: dict, member: discord.Member, guild: discord.Guild) -> discord.Embed:
        """Create a custom embed from JSON data with variable replacement"""
        # Create a copy of the embed data to modify
        embed_data = json.loads(json.dumps(embed_data))
        
        # Get member count and ordinal
        member_count = len(guild.members)
        ordinal_count = self.get_ordinal(member_count)
        milestone_msg = self.get_milestone_message(member_count)
        
        # Create variable mapping
        variables = {
            # Member variables
            "{member}": member.name,
            "{member.name}": member.name,
            "{member.mention}": member.mention,
            "{member.avatar_url}": str(member.avatar.url if member.avatar else member.default_avatar.url),
            "{member.id}": str(member.id),
            "{member.created_at}": member.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "{member.joined_at}": member.joined_at.strftime("%Y-%m-%d %H:%M:%S") if member.joined_at else "Unknown",
            
            # Server variables
            "{server}": guild.name,
            "{server.member_count}": str(member_count),
            "{server.id}": str(guild.id),
            "{server.owner}": str(guild.owner),
            "{server.created_at}": guild.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            
            # Count variables
            "{count}": str(member_count),
            "{count.ordinal}": ordinal_count,
            "{milestone}": milestone_msg,
            "{is_milestone}": "true" if milestone_msg else "false",
            
            # Timestamp
            "{timestamp}": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        }
        
        # Replace variables in strings
        def replace_vars(obj):
            if isinstance(obj, str):
                result = obj
                for var, value in variables.items():
                    result = result.replace(var, value)
                return result
            elif isinstance(obj, dict):
                return {k: replace_vars(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [replace_vars(i) for i in obj]
            return obj
        
        # Process all variables in the embed data
        embed_data = replace_vars(embed_data)
        
        # Create the embed
        embed = discord.Embed.from_dict(embed_data)
        return embed
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

        embed = discord.Embed(
            title="Raid Protection Status Changed",
            description=f"Raid protection has been {state}",
            color=await self.config.guild(ctx.guild).embed_color(),
            timestamp=datetime.datetime.utcnow()
        )
        embed.add_field(name="Changed By", value=ctx.author.mention)
        await self.log_event(ctx.guild, embed)

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

        settings = {
            "Window": f"{await self.config.guild(ctx.guild).join_window()}s",
            "Threshold": await self.config.guild(ctx.guild).join_threshold(),
            "Min Account Age": f"{await self.config.guild(ctx.guild).minimum_account_age()} days"
        }
        
        embed = discord.Embed(
            title="Raid Protection Settings",
            color=await self.config.guild(ctx.guild).embed_color(),
            timestamp=datetime.datetime.utcnow()
        )
        for key, value in settings.items():
            embed.add_field(name=key, value=value)
        
        await ctx.send(embed=embed)
        await self.log_event(ctx.guild, embed)

    @raidprotect.command(name="action")
    async def set_raid_action(self, ctx, action: str):
        """Set action to take during raid (lockdown/kick/ban)"""
        if action.lower() not in ["lockdown", "kick", "ban"]:
            await ctx.send("Invalid action. Choose: lockdown, kick, or ban")
            return
        
        await self.config.guild(ctx.guild).action_on_raid.set(action.lower())
        await ctx.send(f"Raid action set to: {action}")

        embed = discord.Embed(
            title="Raid Action Changed",
            description=f"Raid action has been set to: {action}",
            color=await self.config.guild(ctx.guild).embed_color(),
            timestamp=datetime.datetime.utcnow()
        )
        embed.add_field(name="Changed By", value=ctx.author.mention)
        await self.log_event(ctx.guild, embed)

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

        if guild.id not in self.recent_joins:
            self.recent_joins[guild.id] = deque(maxlen=50)

        current_time = datetime.datetime.utcnow()
        join_window = await self.config.guild(guild).join_window()
        join_threshold = await self.config.guild(guild).join_threshold()

        self.recent_joins[guild.id].append(current_time)

        while self.recent_joins[guild.id] and \
              (current_time - self.recent_joins[guild.id][0]).total_seconds() > join_window:
            self.recent_joins[guild.id].popleft()

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

        embed = discord.Embed(
            title="🚨 Raid Detected",
            description=f"Action taken: {action}\nAffected members: {len(recent_members)}",
            color=discord.Color.red(),
            timestamp=datetime.datetime.utcnow()
        )

        if action == "lockdown":
            self.lockdown_status[guild.id] = True
            lockdown_duration = await self.config.guild(guild).lockdown_duration()
            
            try:
                await guild.default_role.edit(permissions=discord.Permissions.none())
                if alert_channel_id:
                    alert_channel = guild.get_channel(alert_channel_id)
                    if alert_channel:
                        await alert_channel.send(f"🔒 Server locked down for {lockdown_duration} seconds")
                
                embed.add_field(name="Lockdown Duration", value=f"{lockdown_duration} seconds")
                await self.log_event(guild, embed)
                
                await asyncio.sleep(lockdown_duration)
                await guild.default_role.edit(permissions=discord.Permissions.general())
                self.lockdown_status[guild.id] = False
                
                if alert_channel:
                    await alert_channel.send("🔓 Lockdown lifted")
                
                embed = discord.Embed(
                    title="🔓 Lockdown Lifted",
                    description="Server permissions restored to normal",
                    color=discord.Color.green(),
                    timestamp=datetime.datetime.utcnow()
                )
                await self.log_event(guild, embed)
                
            except discord.Forbidden:
                if alert_channel:
                    await alert_channel.send("⚠️ Failed to lockdown server - insufficient permissions")

        elif action == "kick":
            kicked_members = []
            for member in recent_members:
                try:
                    await member.kick(reason="Raid protection")
                    kicked_members.append(str(member))
                except discord.Forbidden:
                    continue
            
            embed.add_field(name="Kicked Members", value="\n".join(kicked_members) if kicked_members else "None")
            await self.log_event(guild, embed)

        elif action == "ban":
            banned_members = []
            for member in recent_members:
                try:
                    await member.ban(reason="Raid protection", delete_message_days=1)
                    banned_members.append(str(member))
                except discord.Forbidden:
                    continue
            
            embed.add_field(name="Banned Members", value="\n".join(banned_members) if banned_members else "None")
            await self.log_event(guild, embed)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Handle member joins with welcome messages and raid protection"""
        guild = member.guild

        # Check for raid protection
        if await self.config.guild(guild).raid_protection():
            # Check account age
            account_age = (datetime.datetime.utcnow() - member.created_at).days
            min_age = await self.config.guild(guild).minimum_account_age()
            
            if account_age < min_age:
                try:
                    await member.kick(reason=f"Account too new ({account_age} days old)")
                    embed = discord.Embed(
                        title="Member Kicked - Account Too New",
                        description=f"{member.mention} was kicked for having a new account",
                        color=discord.Color.orange(),
                        timestamp=datetime.datetime.utcnow()
                    )
                    embed.add_field(name="Account Age", value=f"{account_age} days")
                    embed.add_field(name="Minimum Required", value=f"{min_age} days")
                    await self.log_event(guild, embed)
                    return
                except discord.Forbidden:
                    pass

            # Check if server is in lockdown
            if self.lockdown_status.get(guild.id, False):
                try:
                    await member.kick(reason="Server is in lockdown mode")
                    embed = discord.Embed(
                        title="Member Kicked - Server Lockdown",
                        description=f"{member.mention} was kicked due to server lockdown",
                        color=discord.Color.orange(),
                        timestamp=datetime.datetime.utcnow()
                    )
                    await self.log_event(guild, embed)
                    return
                except discord.Forbidden:
                    pass

            # Check for raid
            if await self.check_raid(member):
                await self.handle_raid(guild)
                return

        # Process welcome message
        if not await self.config.guild(guild).welcome_enabled():
            return

        channel_id = await self.config.guild(guild).welcome_channel()
        if not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            return

        # Check for custom embed
        use_custom = await self.config.guild(guild).use_custom_embed()
        if use_custom:
            embed_data = await self.config.guild(guild).custom_welcome_embed()
            if embed_data:
                try:
                    embed = await self.create_custom_embed(embed_data, member, guild)
                    await channel.send(embed=embed)
                    return
                except Exception:
                    use_custom = False

        # Default embed
        message = await self.config.guild(guild).welcome_message()
        color = await self.config.guild(guild).embed_color()
        ping = await self.config.guild(guild).ping_user()

        embed = discord.Embed(
            title="👋 New Member!",
            description=message.format(
                member=member.mention if ping else member.name,
                server=guild.name
            ),
            color=color,
            timestamp=datetime.datetime.utcnow()
        )
        
        embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
        embed.set_footer(text=f"Member #{len(guild.members)}")

        await channel.send(
            content=member.mention if ping else None,
            embed=embed
        )
        
        # Log the join
        log_embed = discord.Embed(
            title="Member Joined",
            description=f"{member.mention} joined the server",
            color=color,
            timestamp=datetime.datetime.utcnow()
        )
        log_embed.add_field(name="Account Created", value=member.created_at.strftime("%Y-%m-%d %H:%M:%S"))
        log_embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
        await self.log_event(guild, log_embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        """Handle member leaves"""
        guild = member.guild
        if not await self.config.guild(guild).goodbye_enabled():
            return

        channel_id = await self.config.guild(guild).goodbye_channel()
        if not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            return

        message = await self.config.guild(guild).goodbye_message()
        color = await self.config.guild(guild).embed_color()

        embed = discord.Embed(
            title="👋 Member Left",
            description=message.format(
                member=member.name,
                server=guild.name
            ),
            color=color,
            timestamp=datetime.datetime.utcnow()
        )
        
        embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
        await channel.send(embed=embed)
        
        # Log the leave
        log_embed = discord.Embed(
            title="Member Left",
            description=f"{member.mention} left the server",
            color=color,
            timestamp=datetime.datetime.utcnow()
        )
        log_embed.add_field(name="Joined At", value=member.joined_at.strftime("%Y-%m-%d %H:%M:%S") if member.joined_at else "Unknown")
        log_embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
        await self.log_event(guild, log_embed)

    @commands.Cog.listener()
    async def on_member_ban(self, guild, member):
        """Handle member bans"""
        if not await self.config.guild(guild).goodbye_enabled():
            return

        channel_id = await self.config.guild(guild).goodbye_channel()
        if not channel_id:
            return

        channel = guild.get_channel(channel_id)
        if not channel:
            return

        message = await self.config.guild(guild).ban_message()
        color = await self.config.guild(guild).embed_color()

        embed = discord.Embed(
            title="🔨 Member Banned",
            description=message.format(
                member=member.name,
                server=guild.name
            ),
            color=color,
            timestamp=datetime.datetime.utcnow()
        )
        
        embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
        await channel.send(embed=embed)
        
        # Log the ban
        log_embed = discord.Embed(
            title="Member Banned",
            description=f"{member.mention} was banned from the server",
            color=discord.Color.red(),
            timestamp=datetime.datetime.utcnow()
        )
        log_embed.set_thumbnail(url=member.avatar.url if member.avatar else member.default_avatar.url)
        await self.log_event(guild, log_embed)
