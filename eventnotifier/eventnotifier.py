import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from datetime import datetime, timezone
import asyncio
import dateparser
import pytz

class EventNotifier(commands.Cog):
    """Sends notifications for events to interested users."""
    
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "events": {},  # {event_id: {name, time, description, interested_users, message_id, channel_id}}
            "timezone": "UTC"  # Default timezone for the guild
        }
        self.config.register_guild(**default_guild)
        self.event_check_task = self.bot.loop.create_task(self.check_events())
        
        # Emoji reactions for RSVP
        self.YES_EMOJI = "✅"
        self.NO_EMOJI = "❌"
        self.MAYBE_EMOJI = "❔"
        
    def cog_unload(self):
        if self.event_check_task:
            self.event_check_task.cancel()

    @commands.group()
    async def event(self, ctx):
        """Event management commands"""
        pass

    @event.command()
    @commands.mod()
    async def timezone(self, ctx, timezone_name: str):
        """Set the timezone for the guild (e.g., 'US/Pacific', 'Europe/London')"""
        try:
            pytz.timezone(timezone_name)
            await self.config.guild(ctx.guild).timezone.set(timezone_name)
            await ctx.send(f"Timezone set to {timezone_name}")
        except pytz.exceptions.UnknownTimeZoneError:
            await ctx.send("Invalid timezone. Please use a valid timezone name from the IANA timezone database.")

    @event.command()
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
            # Find the event this message belongs to
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
                
            # Add user to appropriate list
            if emoji == self.YES_EMOJI:
                event["interested_users"].append(user.id)
            elif emoji == self.MAYBE_EMOJI:
                event["maybe_users"].append(user.id)
            elif emoji == self.NO_EMOJI:
                event["declined_users"].append(user.id)
                
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
            
        # Similar to on_reaction_add but remove from lists
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

    async def check_events(self):
        """Background task to check for starting events and send notifications"""
        await self.bot.wait_until_ready()
        while self is self.bot.get_cog("EventNotifier"):
            try:
                for guild in self.bot.guilds:
                    events = await self.config.guild(guild).events()
                    current_time = datetime.now(timezone.utc)
                    
                    for event_id, event_data in events.items():
                        event_time = datetime.fromisoformat(event_data["time"])
                        
                        # Check if event is starting within the next minute
                        time_diff = (event_time - current_time).total_seconds()
                        if 0 <= time_diff <= 60:
                            # Send notifications to interested users
                            for user_id in event_data["interested_users"]:
                                user = guild.get_member(user_id)
                                if user:
                                    embed = discord.Embed(
                                        title=f"Event Starting: {event_data['name']}",
                                        description=event_data['description'],
                                        color=discord.Color.green()
                                    )
                                    embed.add_field(name="Time", value=event_time.strftime("%Y-%m-%d %I:%M %p %Z"))
                                    try:
                                        await user.send(embed=embed)
                                    except discord.HTTPException:
                                        continue
                            
                            # Remove the event after it starts
                            async with self.config.guild(guild).events() as events:
                                del events[event_id]
                                
            except Exception as e:
                print(f"Error in event checker: {e}")
                
            await asyncio.sleep(60)  # Check every minute

async def setup(bot):
    await bot.add_cog(EventNotifier(bot))
