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
        self.persistent_views_added = False

    async def initialize(self):
        """Start background tasks and add persistent views"""
        self.event_check_task = self.bot.loop.create_task(self.check_events())
        self.role_cleanup_task = self.bot.loop.create_task(self.cleanup_roles())

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
            # Check if there's a channel mention at the start of the description
            parts = time_and_description.split(" - ", 1)
            if len(parts) != 2:
                await ctx.send("Please provide both time and description separated by ' - '")
                return
                
            time_str, description = parts
            
            # Check for channel mention at the start of description
            target_channel = ctx.channel
            if description.startswith("<#") and ">" in description:
                channel_id = description[2:description.index(">")]
                try:
                    mentioned_channel = ctx.guild.get_channel(int(channel_id))
                    if mentioned_channel:
                        target_channel = mentioned_channel
                        description = description[description.index(">")+1:].strip()
                except ValueError:
                    pass
            else:
                # Check for default announcement channel
                default_channel_id = await self.config.guild(ctx.guild).default_channel()
                if default_channel_id:
                    default_channel = ctx.guild.get_channel(default_channel_id)
                    if default_channel:
                        target_channel = default_channel
            
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
            
            # Create and send the embed with buttons
            view = RSVPView(self, event_id)
            event_message = await target_channel.send(embed=embed, view=view)
            
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
                    "channel_id": target_channel.id
                }
            
            if target_channel != ctx.channel:
                await ctx.send(f"Event created in {target_channel.mention}")
            
        except Exception as e:
            await ctx.send(f"Error creating event: {str(e)}")

    @events_group.command(name="list")
    async def events_list(self, ctx):
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
    async def on_interaction(self, interaction: discord.Interaction):
        if not interaction.data or not interaction.guild:
            return

        custom_id = interaction.data.get("custom_id", "")
        if not custom_id.startswith("rsvp_"):
            return

        # Parse the custom_id
        action, event_id = custom_id.split("_")[1:]
        
        async with self.config.guild(interaction.guild).events() as events:
            if event_id not in events:
                await interaction.response.send_message("This event no longer exists.", ephemeral=True)
                return

            event = events[event_id]
            user_id = interaction.user.id

            # Remove user from all lists first
            if user_id in event["interested_users"]:
                event["interested_users"].remove(user_id)
            if user_id in event["maybe_users"]:
                event["maybe_users"].remove(user_id)
            if user_id in event["declined_users"]:
                event["declined_users"].remove(user_id)

            # Add user to appropriate list and handle role
            if action == "yes":
                event["interested_users"].append(user_id)
                await self.assign_event_role(interaction.guild, interaction.user)
                response = "You're going to the event!"
            elif action == "maybe":
                event["maybe_users"].append(user_id)
                await self.remove_event_role(interaction.guild, interaction.user)
                response = "You might go to the event."
            elif action == "no":
                event["declined_users"].append(user_id)
                await self.remove_event_role(interaction.guild, interaction.user)
                response = "You're not going to the event."

            # Update the embed
            try:
                guild_tz = await self.config.guild(interaction.guild).timezone()
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
                await interaction.message.edit(embed=new_embed)
                await interaction.response.send_message(response, ephemeral=True)
            except discord.HTTPException as e:
                await interaction.response.send_message(f"Error updating RSVP: {str(e)}", ephemeral=True)

async def setup(bot):
    cog = EventNotifier(bot)
    await cog.initialize()
    await bot.add_cog(cog)
