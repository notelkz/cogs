import discord
from discord.ui import Button, View
from redbot.core import commands, Config
from redbot.core.bot import Red
from datetime import datetime, timezone, timedelta
import asyncio
import dateparser
import pytz

class RSVPView(View):
    def __init__(self, cog, event_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.event_id = event_id

        # Add buttons
        self.add_item(Button(
            style=discord.ButtonStyle.green,
            label="Going",
            emoji="✅",
            custom_id=f"rsvp_yes_{event_id}"
        ))
        self.add_item(Button(
            style=discord.ButtonStyle.gray,
            label="Maybe",
            emoji="❔",
            custom_id=f"rsvp_maybe_{event_id}"
        ))
        self.add_item(Button(
            style=discord.ButtonStyle.red,
            label="Not Going",
            emoji="❌",
            custom_id=f"rsvp_no_{event_id}"
        ))

class EventNotifier(commands.Cog):
    """A cog for managing events with RSVP functionality"""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "events": {},  # {event_id: {name, time, description, interested_users, message_id, channel_id}}
            "timezone": "UTC",
            "reminder_times": [30, 5],  # Minutes before event to send reminders
            "event_role_id": None,
            "default_channel": None  # Default channel for event announcements
        }
        self.config.register_guild(**default_guild)
        
        self.YES_EMOJI = "✅"
        self.NO_EMOJI = "❌"
        self.MAYBE_EMOJI = "❔"
        
        self.event_check_task = None
        self.role_cleanup_task = None

        # Add persistent views for buttons
        self.persistent_views_added = False

    async def initialize(self):
        """Start background tasks and add persistent views"""
        self.event_check_task = self.bot.loop.create_task(self.check_events())
        self.role_cleanup_task = self.bot.loop.create_task(self.cleanup_roles())

        # Add persistent views if not already added
        if not self.persistent_views_added:
            await self.add_persistent_views()
            self.persistent_views_added = True

    async def add_persistent_views(self):
        """Add persistent views for existing events"""
        for guild in self.bot.guilds:
            events = await self.config.guild(guild).events()
            for event_id in events.keys():
                self.bot.add_view(RSVPView(self, event_id))

    def cog_unload(self):
        if self.event_check_task:
            self.event_check_task.cancel()
        if self.role_cleanup_task:
            self.role_cleanup_task.cancel()

    # Define the events group first
    @commands.group(name="events")
    async def events_group(self, ctx):
        """Event management commands"""
        pass

    @events_group.command(name="setup")
    @commands.admin()
    async def events_setup(self, ctx):
        """Interactive setup for the events system"""
        if not ctx.guild:
            await ctx.send("This command must be used in a server!")
            return

        try:
            # Ask for timezone
            await ctx.send("What timezone should be used for events? (e.g., 'US/Pacific', 'Europe/London')")
            try:
                timezone_msg = await self.bot.wait_for(
                    "message",
                    timeout=30.0,
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel
                )
                
                try:
                    pytz.timezone(timezone_msg.content)
                    await self.config.guild(ctx.guild).timezone.set(timezone_msg.content)
                    await ctx.send(f"✅ Timezone set to {timezone_msg.content}")
                except pytz.exceptions.UnknownTimeZoneError:
                    await ctx.send("❌ Invalid timezone. Setup cancelled. Please try again with a valid timezone.")
                    return
            except asyncio.TimeoutError:
                await ctx.send("Setup timed out. Please try again.")
                return

            # Ask for event role
            await ctx.send("Please mention the role that should be assigned to event participants:")
            try:
                role_msg = await self.bot.wait_for(
                    "message",
                    timeout=30.0,
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel
                )
                
                if role_msg.role_mentions:
                    role = role_msg.role_mentions[0]
                    await self.config.guild(ctx.guild).event_role_id.set(role.id)
                    await ctx.send(f"✅ Event role set to {role.name}")
                else:
                    await ctx.send("❌ No role mentioned. Setup cancelled. Please try again and mention a role.")
                    return
            except asyncio.TimeoutError:
                await ctx.send("Setup timed out. Please try again.")
                return

            # Ask for reminder times
            await ctx.send("Enter reminder times in minutes, separated by spaces (e.g., '60 30 10' for reminders at 60, 30, and 10 minutes before events):")
            try:
                times_msg = await self.bot.wait_for(
                    "message",
                    timeout=30.0,
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel
                )
                
                try:
                    times = [int(x) for x in times_msg.content.split()]
                    if not times:
                        raise ValueError
                    reminder_times = sorted(times, reverse=True)
                    await self.config.guild(ctx.guild).reminder_times.set(reminder_times)
                    await ctx.send(f"✅ Reminder times set to: {', '.join(str(m) + ' minutes' for m in reminder_times)}")
                except ValueError:
                    await ctx.send("❌ Invalid reminder times. Setup cancelled. Please try again with valid numbers.")
                    return
            except asyncio.TimeoutError:
                await ctx.send("Setup timed out. Please try again.")
                return

            # Ask for default announcements channel
            await ctx.send("Please mention the default channel for event announcements (or type 'skip' to use the channel where events are created):")
            try:
                channel_msg = await self.bot.wait_for(
                    "message",
                    timeout=30.0,
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel
                )
                
                if channel_msg.content.lower() == 'skip':
                    await self.config.guild(ctx.guild).default_channel.set(None)
                    await ctx.send("✅ Events will be posted in the channel where they are created")
                elif channel_msg.channel_mentions:
                    channel = channel_msg.channel_mentions[0]
                    await self.config.guild(ctx.guild).default_channel.set(channel.id)
                    await ctx.send(f"✅ Default announcements channel set to {channel.mention}")
                else:
                    await ctx.send("❌ No channel mentioned. Events will be posted in the channel where they are created.")
                    await self.config.guild(ctx.guild).default_channel.set(None)
            except asyncio.TimeoutError:
                await ctx.send("Setup timed out. Please try again.")
                return

            await ctx.send("✅ Setup complete! You can now create events using `!events create`")

        except Exception as e:
            await ctx.send(f"An error occurred during setup: {str(e)}")

    @events_group.command(name="timezone")
    @commands.mod()
    async def events_timezone(self, ctx, timezone_name: str):
        """Set the timezone for the guild (e.g., 'US/Pacific', 'Europe/London')"""
        try:
            pytz.timezone(timezone_name)
            await self.config.guild(ctx.guild).timezone.set(timezone_name)
            await ctx.send(f"Timezone set to {timezone_name}")
        except pytz.exceptions.UnknownTimeZoneError:
            await ctx.send("Invalid timezone. Please use a valid timezone name from the IANA timezone database.")

    @events_group.command(name="setreminders")
    @commands.mod()
    async def events_setreminders(self, ctx, *minutes: int):
        """Set when to send reminders before events (in minutes)
        Example: !events setreminders 60 30 10"""
        if not minutes:
            await ctx.send("Please provide at least one reminder time in minutes")
            return
            
        reminder_times = sorted(minutes, reverse=True)
        await self.config.guild(ctx.guild).reminder_times.set(reminder_times)
        await ctx.send(f"Reminder times set to: {', '.join(str(m) + ' minutes' for m in reminder_times)}")

    @events_group.command(name="showreminders")
    @commands.mod()
    async def events_showreminders(self, ctx):
        """Show current reminder times"""
        reminder_times = await self.config.guild(ctx.guild).reminder_times()
        await ctx.send(f"Current reminder times: {', '.join(str(m) + ' minutes' for m in reminder_times)}")

    @events_group.command(name="create")
    @commands.mod()
    async def events_create(self, ctx, name: str, *, time_and_description: str):
        """Create a new event. Time can be natural language like 'tomorrow at 3pm' or 'in 2 hours'"""
        try:
            # [Rest of the create method remains the same]
            # Just rename the method to events_create
            pass

    @events_group.command(name="list")
    async def events_list(self, ctx):
        """List all upcoming events"""
        # [Rest of the list method remains the same]
        # Just rename the method to events_list
        pass

    # [Rest of the methods remain the same]

async def setup(bot):
    cog = EventNotifier(bot)
    await cog.initialize()
    await bot.add_cog(cog)
