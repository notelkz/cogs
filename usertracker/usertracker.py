# In your existing GameCounter cog or as a new cog

import discord
import asyncio
from datetime import datetime, timedelta
from redbot.core import commands, Config

class ActivityTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=987654321)
        
        # Default settings
        default_guild = {
            "recruit_role_id": None,
            "member_role_id": None,
            "promotion_threshold_hours": 1.0,  # Default 1 hour
            "promotion_channel_id": None
        }
        
        default_member = {
            "voice_time_minutes": 0,
            "last_voice_join": None,
            "current_role": "recruit"
        }
        
        self.config.register_guild(**default_guild)
        self.config.register_member(**default_member)
        
        self.voice_tracking = {}  # {user_id: join_timestamp}
        self.promotion_task = self.bot.loop.create_task(self.check_for_promotions())
        
    def cog_unload(self):
        self.promotion_task.cancel()
    
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # Skip bots
        if member.bot:
            return
            
        # Check if user has the recruit role
        recruit_role_id = await self.config.guild(member.guild).recruit_role_id()
        if recruit_role_id:
            recruit_role = member.guild.get_role(recruit_role_id)
            if not recruit_role or recruit_role not in member.roles:
                return  # Not a recruit, don't track
        
        # User joined a voice channel
        if before.channel is None and after.channel is not None:
            self.voice_tracking[member.id] = datetime.now()
            
        # User left a voice channel
        elif before.channel is not None and after.channel is None:
            if member.id in self.voice_tracking:
                join_time = self.voice_tracking.pop(member.id)
                duration = (datetime.now() - join_time).total_seconds() / 60
                
                # Update their total time
                async with self.config.member(member).all() as member_data:
                    member_data["voice_time_minutes"] += duration
                    
                # Send update to website API
                await self._update_website_activity(member.id, duration)
    
    async def _update_website_activity(self, discord_id, minutes_to_add):
        """Send activity update to website API"""
        api_url = await self.config.api_url()
        api_key = await self.config.api_key()
        
        if not api_url or not api_key:
            return
            
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {
            "discord_id": str(discord_id),
            "voice_minutes": minutes_to_add
        }
        
        try:
            async with self.bot.session.post(
                f"{api_url}/api/update_activity/", 
                headers=headers, 
                json=payload
            ) as resp:
                if resp.status != 200:
                    print(f"Error updating activity: {await resp.text()}")
        except Exception as e:
            print(f"Failed to update website: {e}")
    
    async def check_for_promotions(self):
        """Background task to check for promotions"""
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                # For each guild
                for guild in self.bot.guilds:
                    # Get role IDs
                    recruit_role_id = await self.config.guild(guild).recruit_role_id()
                    member_role_id = await self.config.guild(guild).member_role_id()
                    promotion_threshold_hours = await self.config.guild(guild).promotion_threshold_hours()
                    
                    # Convert hours to minutes for comparison
                    promotion_threshold_minutes = promotion_threshold_hours * 60
                    
                    if not (recruit_role_id and member_role_id):
                        continue
                        
                    recruit_role = guild.get_role(recruit_role_id)
                    member_role = guild.get_role(member_role_id)
                    
                    if not (recruit_role and member_role):
                        continue
                    
                    # Check each recruit
                    for member in recruit_role.members:
                        voice_time = await self.config.member(member).voice_time_minutes()
                        
                        # If they've met the threshold
                        if voice_time >= promotion_threshold_minutes:
                            # Promote them
                            try:
                                await member.remove_roles(recruit_role)
                                await member.add_roles(member_role)
                                
                                # Update their status
                                await self.config.member(member).current_role.set("member")
                                
                                # Notify the website
                                await self._notify_promotion(member.id, "member")
                                
                                # Announce in a channel
                                channel_id = await self.config.guild(guild).promotion_channel_id()
                                if channel_id:
                                    channel = guild.get_channel(channel_id)
                                    if channel:
                                        hours = voice_time / 60
                                        await channel.send(
                                            f"🎉 Congratulations {member.mention}! "
                                            f"You've been promoted to full Member status after "
                                            f"{hours:.1f} hours of voice activity!"
                                        )
                            except discord.Forbidden:
                                print(f"Missing permissions to promote {member}")
                            except Exception as e:
                                print(f"Error promoting {member}: {e}")
            except Exception as e:
                print(f"Error in promotion check: {e}")
                
            # Check every 5 minutes
            await asyncio.sleep(300)
    
    async def _notify_promotion(self, discord_id, new_role):
        """Notify website of role promotion"""
        api_url = await self.config.api_url()
        api_key = await self.config.api_key()
        
        if not api_url or not api_key:
            return
            
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        payload = {
            "discord_id": str(discord_id),
            "new_role": new_role
        }
        
        try:
            async with self.bot.session.post(
                f"{api_url}/api/update_role/", 
                headers=headers, 
                json=payload
            ) as resp:
                if resp.status != 200:
                    print(f"Error updating role: {await resp.text()}")
        except Exception as e:
            print(f"Failed to update website: {e}")
    
    @commands.group(name="activitytracker")
    @commands.admin_or_permissions(administrator=True)
    async def activitytracker(self, ctx):
        """Activity tracker settings"""
        pass
    
    @activitytracker.command(name="setroles")
    async def set_roles(self, ctx, recruit_role: discord.Role, member_role: discord.Role):
        """Set the recruit and member roles for promotion"""
        await self.config.guild(ctx.guild).recruit_role_id.set(recruit_role.id)
        await self.config.guild(ctx.guild).member_role_id.set(member_role.id)
        await ctx.send(f"Roles set: Recruits = {recruit_role.name}, Members = {member_role.name}")
    
    @activitytracker.command(name="setthreshold")
    async def set_threshold(self, ctx, hours: float):
        """Set the voice activity threshold for promotion (in hours)"""
        if hours <= 0:
            return await ctx.send("Threshold must be greater than 0 hours.")
            
        await self.config.guild(ctx.guild).promotion_threshold_hours.set(hours)
        await ctx.send(f"Promotion threshold set to {hours} hours of voice activity")
    
    @activitytracker.command(name="setchannel")
    async def set_channel(self, ctx, channel: discord.TextChannel = None):
        """Set the channel for promotion announcements (leave empty to disable)"""
        if channel:
            await self.config.guild(ctx.guild).promotion_channel_id.set(channel.id)
            await ctx.send(f"Promotion announcements will be sent to {channel.mention}")
        else:
            await self.config.guild(ctx.guild).promotion_channel_id.set(None)
            await ctx.send("Promotion announcements disabled")
    
    @activitytracker.command(name="status")
    async def status(self, ctx, member: discord.Member = None):
        """Check activity status for a member or yourself"""
        target = member or ctx.author
        
        voice_time_minutes = await self.config.member(target).voice_time_minutes()
        voice_time_hours = voice_time_minutes / 60
        current_role = await self.config.member(target).current_role()
        threshold_hours = await self.config.guild(ctx.guild).promotion_threshold_hours()
        
        embed = discord.Embed(
            title="Activity Status",
            color=discord.Color.blue()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Member", value=target.mention, inline=False)
        embed.add_field(name="Current Role", value=current_role.capitalize(), inline=True)
        embed.add_field(name="Voice Activity", value=f"{voice_time_hours:.2f} hours", inline=True)
        
        if current_role == "recruit":
            progress = min(voice_time_hours / threshold_hours * 100, 100)
            embed.add_field(
                name="Progress to Member", 
                value=f"{progress:.1f}% ({voice_time_hours:.2f}/{threshold_hours} hours)", 
                inline=False
            )
            
        await ctx.send(embed=embed)
