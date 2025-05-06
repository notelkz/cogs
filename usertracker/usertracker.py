import discord
from discord.ext import commands
from datetime import datetime, timedelta
import json
import os

class UserTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.voice_tracking = {}
        self.message_tracking = {}
        self.bot.add_listener(self.on_voice_state_update)
        self.bot.add_listener(self.on_message)
        self.load_data()

    def save_data(self):
        data = {
            'voice': self.voice_tracking,
            'messages': self.message_tracking
        }
        with open('data/user_tracker.json', 'w') as f:
            json.dump(data, f)

    def load_data(self):
        if os.path.exists('data/user_tracker.json'):
            with open('data/user_tracker.json', 'r') as f:
                data = json.load(f)
                self.voice_tracking = data.get('voice', {})
                self.message_tracking = data.get('messages', {})

    @commands.command(name='usertracker', aliases=['ut', 'track'])
    @commands.has_permissions(administrator=True)
    async def user_tracker(self, ctx, member: discord.Member, period: str = None):
        """Track user activity"""
        embed = discord.Embed(title=f"User Activity: {member}", color=0x7289DA)
        
        # Join Date
        join_date = member.joined_at
        embed.add_field(name="Joined Server", value=f"{join_date.strftime('%d/%m/%Y')} ({(datetime.now() - join_date).days} days ago)", inline=False)
        
        # Voice Tracking
        voice_data = self.voice_tracking.get(member.id, [])
        if voice_data:
            # Calculate total time in voice channels
            total_seconds = 0
            for i in range(0, len(voice_data)-1, 2):
                start = voice_data[i]
                end = voice_data[i+1]
                total_seconds += (end - start).total_seconds()
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            seconds = total_seconds % 60
            embed.add_field(name="Time in Voice Channels", value=f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}", inline=False)
        else:
            embed.add_field(name="Time in Voice Channels", value="No voice data", inline=False)
        
        # Message Tracking
        message_data = self.message_tracking.get(member.id, [])
        if period:
            try:
                days = int(period)
                filtered_messages = [msg for msg in message_data if (datetime.now() - msg).days <= days]
                embed.add_field(name=f"Messages Sent (Last {days} days)", value=len(filtered_messages), inline=False)
            except ValueError:
                embed.add_field(name="Invalid period", value="Please specify a valid number of days", inline=False)
        else:
            embed.add_field(name="Messages Sent", value=len(message_data), inline=False)
        
        await ctx.send(embed=embed)

    async def on_voice_state_update(self, member, before, after):
        if before.channel is None and after.channel is not None:
            # User joined a voice channel
            if member.id not in self.voice_tracking:
                self.voice_tracking[member.id] = []
            self.voice_tracking[member.id].append(datetime.now())
            self.save_data()
        elif before.channel is not None and after.channel is None:
            # User left a voice channel
            if member.id in self.voice_tracking:
                self.voice_tracking[member.id].append(datetime.now())
                self.save_data()

    async def on_message(self, message):
        if message.author.bot:
            return
        if message.guild:
            if message.author.id not in self.message_tracking:
                self.message_tracking[message.author.id] = []
            self.message_tracking[message.author.id].append(datetime.now())
            self.save_data()

def setup(bot):
    cog = UserTracker(bot)
    bot.add_cog(cog)
