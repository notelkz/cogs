import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from datetime import datetime, timezone, timedelta
import asyncio
import dateparser
import pytz

class EventNotifier(commands.Cog):
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
        self.event_check_task = self.bot.loop.create_task(self.check_events())
        self.role_cleanup_task = self.bot.loop.create_task(self.cleanup_roles())
        
        self.YES_EMOJI = "✅"
        self.NO_EMOJI = "❌"
        self.MAYBE_EMOJI = "❔"

    def cog_unload(self):
        if self.event_check_task:
            self.event_check_task.cancel()
        if self.role_cleanup_task:
            self.role_cleanup_task.cancel()

    # Define the event group first
    @commands.group()
    async def event(self, ctx):
        """Event management commands"""
        pass

    # Then add all the event subcommands
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
    async def setreminders(self, ctx, *minutes: int):
        """Set when to send reminders before events (in minutes)
        Example: !event setreminders 60 30 10"""
        if not minutes:
            await ctx.send("Please provide at least one reminder time in minutes")
            return
            
        reminder_times = sorted(minutes, reverse=True)
        await self.config.guild(ctx.guild).reminder_times.set(reminder_times)
        await ctx.send(f"Reminder times set to: {', '.join(str(m) + ' minutes' for m in reminder_times)}")

    @event.command()
    @commands.mod()
    async def showreminders(self, ctx):
        """Show current reminder times"""
        reminder_times = await self.config.guild(ctx.guild).reminder_times()
        await ctx.send(f"Current reminder times: {', '.join(str(m) + ' minutes' for m in reminder_times)}")

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

    # Rest of the methods remain the same
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

    # Add the rest of your methods here (assign_event_role, remove_event_role, 
    # on_reaction_add, check_events, cleanup_roles) exactly as they were in the previous code

async def setup(bot):
    await bot.add_cog(EventNotifier(bot))
