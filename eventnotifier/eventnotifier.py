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
                        unix_timestamp = int(event_time.timestamp())
                        
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
                                            description=f"Event starts <t:{unix_timestamp}:R>!\n\n{event_data['description']}",
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
    async def events(self, ctx):
        """Event management commands"""
        pass

    @events.command()
    @commands.mod()
    async def timezone(self, ctx, timezone_name: str):
        """Set the timezone for the guild (e.g., 'US/Pacific', 'Europe/London')"""
        try:
            pytz.timezone(timezone_name)
            await self.config.guild(ctx.guild).timezone.set(timezone_name)
            await ctx.send(f"Timezone set to {timezone_name}")
        except pytz.exceptions.UnknownTimeZoneError:
            await ctx.send("Invalid timezone. Please use a valid timezone name from the IANA timezone database.")

    @events.command()
    @commands.mod()
    async def setreminders(self, ctx, *minutes: int):
        """Set when to send reminders before events (in minutes)
        Example: !events setreminders 60 30 10"""
        if not minutes:
            await ctx.send("Please provide at least one reminder time in minutes")
            return
            
        reminder_times = sorted(minutes, reverse=True)
        await self.config.guild(ctx.guild).reminder_times.set(reminder_times)
        await ctx.send(f"Reminder times set to: {', '.join(str(m) + ' minutes' for m in reminder_times)}")

    @events.command()
    @commands.mod()
    async def showreminders(self, ctx):
        """Show current reminder times"""
        reminder_times = await self.config.guild(ctx.guild).reminder_times()
        await ctx.send(f"Current reminder times: {', '.join(str(m) + ' minutes' for m in reminder_times)}")

    @events.command()
    @commands.mod()
    async def create(self, ctx, name: str, *, time_and_description: str):
        """Create a new event. Time can be natural language like 'tomorrow at 3pm' or 'in 2 hours'"""
        try:
            # Split the time and description
            parts = time_and_description.split(" - ", 1)
            if len(parts) != 2:
                await ctx.send("Please provide both time and description separated by ' - '")
                return
                
            time_str, description = parts
            
            # Get guild timezone
            guild_tz = await self.config.guild(ctx.guild).timezone()
            settings = {'TIMEZONE': guild_tz, 'RETURN_AS_TIMEZONE_AWARE': True}
            
            # Parse the time
            event_time = dateparser.parse(time_str, settings=settings)
            if not event_time:
                await ctx.send("Couldn't understand that time format. Try something like 'tomorrow at 3pm' or 'in 2 hours'")
                return
            
            event_id = str(len((await self.config.guild(ctx.guild).events()).keys()) + 1)
            
            # Create the event embed
            embed = await self.create_event_embed(
                name, 
                event_time, 
                description, 
                event_id, 
                guild_tz,
                []
            )
            
            # Send the embed and add reaction options
            event_message = await ctx.send(embed=embed)
            await event_message.add_reaction(self.YES_EMOJI)
            await event_message.add_reaction(self.MAYBE_EMOJI)
            await event_message.add_reaction(self.NO_EMOJI)
            
            # Save the event
            async with self.config.guild(ctx.guild).events() as events:
                events[event_id] = {
                    "name": name,
                    "time": event_time.isoformat(),
                    "description": description,
                    "interested_users": [],
                    "maybe_users": [],
                    "declined_users": [],
                    "message_id": event_message.id,
                    "channel_id": ctx.channel.id
                }
            
        except Exception as e:
            await ctx.send(f"Error creating event: {str(e)}")

    @events.command()
    async def list(self, ctx):
        """List all upcoming events"""
        events = await self.config.guild(ctx.guild).events()
        
        if not events:
            await ctx.send("No upcoming events!")
            return
            
        embed = discord.Embed(
            title="Upcoming Events",
            color=discord.Color.blue()
        )
        
        for event_id, event_data in events.items():
            event_time = datetime.fromisoformat(event_data["time"])
            unix_timestamp = int(event_time.timestamp())
            interested_count = len(event_data["interested_users"])
            maybe_count = len(event_data["maybe_users"])
            declined_count = len(event_data["declined_users"])
            
            field_value = (
                f"Time: <t:{unix_timestamp}:F>\n"
                f"Relative: <t:{unix_timestamp}:R>\n"
                f"Description: {event_data['description']}\n"
                f"Going: {interested_count} | Maybe: {maybe_count} | Not Going: {declined_count}"
            )
            
            embed.add_field(
                name=f"{event_data['name']} (ID: {event_id})",
                value=field_value,
                inline=False
            )
            
        await ctx.send(embed=embed)

    async def create_event_embed(self, name, event_time, description, event_id, guild_tz, interested_users=None, maybe_users=None, declined_users=None):
        """Create an embed for the event with Discord timestamp"""
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
        
        # Convert to Unix timestamp for Discord's timestamp format
        unix_timestamp = int(event_time.timestamp())
        time_field = (
            f"Event Time: <t:{unix_timestamp}:F>\n"
            f"Relative Time: <t:{unix_timestamp}:R>"
        )
        
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

    @commands.Cog.listener()
    async def on_reaction_remove(self, reaction, user):
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
            
            # Remove user from the appropriate list
            if emoji == self.YES_EMOJI and user.id in event["interested_users"]:
                event["interested_users"].remove(user.id)
                await self.remove_event_role(guild, user)
            elif emoji == self.MAYBE_EMOJI and user.id in event["maybe_users"]:
                event["maybe_users"].remove(user.id)
            elif emoji == self.NO_EMOJI and user.id in event["declined_users"]:
                event["declined_users"].remove(user.id)
                
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
