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

    # [Previous methods remain the same until the create method]

    @events.command()
    @commands.mod()
    async def create(self, ctx, name: str, *, time_and_description: str):
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

    # [All other methods remain the same, remove on_reaction_add and on_reaction_remove]

async def setup(bot):
    cog = EventNotifier(bot)
    await cog.initialize()
    await bot.add_cog(cog)
