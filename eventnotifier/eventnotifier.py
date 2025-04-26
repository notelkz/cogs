import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from datetime import datetime, timezone, timedelta
import asyncio
import dateparser
import pytz

class EventNotifier(commands.Cog):
    """A cog for managing events with RSVP functionality"""

    async def check_events(self):
        """Background task to check for starting events and send reminders"""
        await self.bot.wait_until_ready()
        while self is self.bot.get_cog("EventNotifier"):
            try:
                for guild in self.bot.guilds:
                    events = await self.config.guild(guild).events()
                    reminder_times = await self.config.guild(guild).reminder_times()
                    current_time = datetime.now(timezone.utc)
                    role_id = await self.config.guild(guild).event_role_id()
                    event_role = guild.get_role(role_id)
                    
                    for event_id, event_data in events.items():
                        event_time = datetime.fromisoformat(event_data["time"])
                        
                        # Check for reminders
                        for reminder_minutes in reminder_times:
                            reminder_threshold = event_time - timedelta(minutes=reminder_minutes)
                            time_until_reminder = (reminder_threshold - current_time).total_seconds()
                            
                            if 0 <= time_until_reminder <= 60:  # Within the next minute
                                if event_role and event_data["interested_users"]:
                                    channel = guild.get_channel(event_data["channel_id"])
                                    if channel:
                                        reminder_embed = discord.Embed(
                                            title=f"Event Reminder: {event_data['name']}",
                                            description=f"Event starts in {reminder_minutes} minutes!\n\n{event_data['description']}",
                                            color=discord.Color.gold()
                                        )
                                        await channel.send(
                                            content=f"{event_role.mention}",
                                            embed=reminder_embed
                                        )
                        
                        # Check if event is starting
                        time_diff = (event_time - current_time).total_seconds()
                        if 0 <= time_diff <= 60:
                            # Send start notification
                            if event_role and event_data["interested_users"]:
                                channel = guild.get_channel(event_data["channel_id"])
                                if channel:
                                    start_embed = discord.Embed(
                                        title=f"Event Starting: {event_data['name']}",
                                        description=event_data['description'],
                                        color=discord.Color.green()
                                    )
                                    await channel.send(
                                        content=f"{event_role.mention} The event is starting now!",
                                        embed=start_embed
                                    )
                            
                            # Add cleanup time to the event data
                            event_data["cleanup_time"] = (event_time + timedelta(minutes=60)).isoformat()
                            
                            # Update the event data
                            async with self.config.guild(guild).events() as events:
                                events[event_id] = event_data
                                
            except Exception as e:
                print(f"Error in event checker: {e}")
                
            await asyncio.sleep(60)  # Check every minute

    async def cleanup_roles(self):
        """Background task to remove roles after events"""
        await self.bot.wait_until_ready()
        while self is self.bot.get_cog("EventNotifier"):
            try:
                current_time = datetime.now(timezone.utc)
                
                for guild in self.bot.guilds:
                    async with self.config.guild(guild).events() as events:
                        events_to_remove = []
                        
                        for event_id, event_data in events.items():
                            if "cleanup_time" in event_data:
                                cleanup_time = datetime.fromisoformat(event_data["cleanup_time"])
                                
                                if current_time >= cleanup_time:
                                    # Remove roles from users
                                    for user_id in event_data["interested_users"]:
                                        user = guild.get_member(user_id)
                                        if user:
                                            await self.remove_event_role(guild, user)
                                    
                                    events_to_remove.append(event_id)
                        
                        # Remove completed events
                        for event_id in events_to_remove:
                            del events[event_id]
                            
            except Exception as e:
                print(f"Error in role cleanup: {e}")
                
            await asyncio.sleep(60)  # Check every minute

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "events": {},  # {event_id: {name, time, description, interested_users, message_id, channel_id}}
            "timezone": "UTC",
            "reminder_times": [30, 5],  # Minutes before event to send reminders
            "event_role_id": 1358213818362233030
        }
        self.config.register_guild(**default_guild)
        
        self.YES_EMOJI = "✅"
        self.NO_EMOJI = "❌"
        self.MAYBE_EMOJI = "❔"
        
        # Create tasks after all methods are defined
        self.event_check_task = None
        self.role_cleanup_task = None

    async def initialize(self):
        """Start background tasks"""
        self.event_check_task = self.bot.loop.create_task(self.check_events())
        self.role_cleanup_task = self.bot.loop.create_task(self.cleanup_roles())

    def cog_unload(self):
        if self.event_check_task:
            self.event_check_task.cancel()
        if self.role_cleanup_task:
            self.role_cleanup_task.cancel()

    async def assign_event_role(self, guild, user):
        """Assign the event role to a user"""
        role_id = await self.config.guild(guild).event_role_id()
        role = guild.get_role(role_id)
        if role and not role in user.roles:
            try:
                await user.add_roles(role, reason="Event RSVP")
            except discord.HTTPException:
                print(f"Failed to assign event role to {user.name}")

    async def remove_event_role(self, guild, user):
        """Remove the event role from a user"""
        role_id = await self.config.guild(guild).event_role_id()
        role = guild.get_role(role_id)
        if role and role in user.roles:
            try:
                await user.remove_roles(role, reason="Event ended")
            except discord.HTTPException:
                print(f"Failed to remove event role from {user.name}")

    @commands.group()
    async def event(self, ctx):
        """Event management commands"""
        pass

    # Add all your other command methods here...
    # (timezone, setreminders, showreminders, create, etc.)

    async def create_event_embed(self, name, event_time, description, event_id, guild_tz, interested_users=None, maybe_users=None, declined_users=None):
        """Create an embed for the event with timezone information"""
        if interested_users is None:
            interested_users = []
        if maybe_users is None:
            maybe_users = []
        if declined_users is None:
            declined_users = []
            
        embed = discord.Embed(
            title=f"Event: {name}",
            description=description,
            color=discord.Color.blue()
        )
        
        # Add time information for different timezones
        common_timezones = ['US/Pacific', 'US/Eastern', 'Europe/London', 'Europe/Paris', 'Asia/Tokyo']
        time_field = f"Local Time ({guild_tz}): {event_time.strftime('%Y-%m-%d %I:%M %p %Z')}\n\n"
        time_field += "Other Timezones:\n"
        
        for tz_name in common_timezones:
            if tz_name != guild_tz:
                tz = pytz.timezone(tz_name)
                converted_time = event_time.astimezone(tz)
                time_field += f"{tz_name}: {converted_time.strftime('%Y-%m-%d %I:%M %p %Z')}\n"
        
        embed.add_field(name="Time", value=time_field, inline=False)
        embed.add_field(name="Event ID", value=event_id, inline=False)
        
        # Add RSVP counts
        rsvp_field = f"{self.YES_EMOJI} Going: {len(interested_users)}\n"
        rsvp_field += f"{self.MAYBE_EMOJI} Maybe: {len(maybe_users)}\n"
        rsvp_field += f"{self.NO_EMOJI} Not Going: {len(declined_users)}"
        embed.add_field(name="RSVP Status", value=rsvp_field, inline=False)
        
        return embed

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction, user):
        if user.bot:
            return
            
        message = reaction.message
        guild = message.guild
        if not guild:
            return
            
        async with self.config.guild(guild).events() as events:
            event_id = None
            for eid, event_data in events.items():
                if event_data.get("message_id") == message.id:
                    event_id = eid
                    break
                    
            if not event_id:
                return
                
            emoji = str(reaction.emoji)
            event = events[event_id]
            
            # Remove user from all lists first
            if user.id in event["interested_users"]:
                event["interested_users"].remove(user.id)
            if user.id in event["maybe_users"]:
                event["maybe_users"].remove(user.id)
            if user.id in event["declined_users"]:
                event["declined_users"].remove(user.id)
                
            # Add user to appropriate list and handle role
            if emoji == self.YES_EMOJI:
                event["interested_users"].append(user.id)
                await self.assign_event_role(guild, user)
            elif emoji == self.MAYBE_EMOJI:
                event["maybe_users"].append(user.id)
                await self.remove_event_role(guild, user)
            elif emoji == self.NO_EMOJI:
                event["declined_users"].append(user.id)
                await self.remove_event_role(guild, user)
                
            # Update the embed
            try:
                guild_tz = await self.config.guild(guild).timezone()
                event_time = datetime.fromisoformat(event["time"])
                new_embed = await self.create_event_embed(
                    event["name"],
                    event_time,
                    event["description"],
                    event_id,
                    guild_tz,
                    event["interested_users"],
                    event["maybe_users"],
                    event["declined_users"]
                )
                await message.edit(embed=new_embed)
            except discord.HTTPException:
                pass

async def setup(bot):
    cog = EventNotifier(bot)
    await cog.initialize()
    await bot.add_cog(cog)
