import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from datetime import datetime
import asyncio

class EventNotifier(commands.Cog):
    """Sends notifications for events to interested users."""
    
    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "events": {}  # {event_id: {name, time, description, interested_users}}
        }
        self.config.register_guild(**default_guild)
        self.event_check_task = self.bot.loop.create_task(self.check_events())
        
    def cog_unload(self):
        if self.event_check_task:
            self.event_check_task.cancel()
    
    @commands.group()
    async def event(self, ctx):
        """Event management commands"""
        pass
    
    @event.command()
    @commands.mod()
    async def create(self, ctx, name: str, time: str, *, description: str = "No description provided"):
        """Create a new event"""
        try:
            event_time = datetime.strptime(time, "%Y-%m-%d %H:%M")
            event_id = str(len((await self.config.guild(ctx.guild).events()).keys()) + 1)
            
            async with self.config.guild(ctx.guild).events() as events:
                events[event_id] = {
                    "name": name,
                    "time": event_time.strftime("%Y-%m-%d %H:%M"),
                    "description": description,
                    "interested_users": []
                }
            
            embed = discord.Embed(
                title=f"New Event: {name}",
                description=description,
                color=discord.Color.blue()
            )
            embed.add_field(name="Time", value=event_time.strftime("%Y-%m-%d %H:%M"))
            embed.add_field(name="Event ID", value=event_id)
            
            await ctx.send(embed=embed)
            
        except ValueError:
            await ctx.send("Invalid time format. Please use YYYY-MM-DD HH:MM")
    
    @event.command()
    async def interest(self, ctx, event_id: str):
        """Mark your interest in an event"""
        async with self.config.guild(ctx.guild).events() as events:
            if event_id not in events:
                await ctx.send("Event not found!")
                return
                
            if ctx.author.id not in events[event_id]["interested_users"]:
                events[event_id]["interested_users"].append(ctx.author.id)
                await ctx.send(f"You are now marked as interested in {events[event_id]['name']}!")
            else:
                events[event_id]["interested_users"].remove(ctx.author.id)
                await ctx.send(f"You are no longer marked as interested in {events[event_id]['name']}!")
    
    @event.command()
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
            interested_count = len(event_data["interested_users"])
            embed.add_field(
                name=f"{event_data['name']} (ID: {event_id})",
                value=f"Time: {event_data['time']}\nDescription: {event_data['description']}\nInterested Users: {interested_count}",
                inline=False
            )
            
        await ctx.send(embed=embed)
    
    async def check_events(self):
        """Background task to check for starting events and send notifications"""
        await self.bot.wait_until_ready()
        while self is self.bot.get_cog("EventNotifier"):
            try:
                for guild in self.bot.guilds:
                    events = await self.config.guild(guild).events()
                    current_time = datetime.now()
                    
                    for event_id, event_data in events.items():
                        event_time = datetime.strptime(event_data["time"], "%Y-%m-%d %H:%M")
                        
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
                                    embed.add_field(name="Time", value=event_data["time"])
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

def setup(bot):
    bot.add_cog(EventNotifier(bot))
