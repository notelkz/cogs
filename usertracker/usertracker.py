import discord
from discord.ext import commands
from redbot.core import checks, Config
from redbot.core.utils.chat_formatting import humanize_timedelta
from datetime import datetime, timedelta
import time

class UserTracker(commands.Cog):
    """Track user activities in the server
    
    Commands:
    - userinfo/info [member] - Show detailed user activity
    - voicetime/vt <member> [days] - Show voice time for specific period
    - topvoice/tv [limit] - Show top users by voice time
    - topmessages/tm [limit] - Show top message senders
    - resetstats (Admin only) - Reset statistics
    """
    
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=856428963,  # Unique identifier for your cog
            force_registration=True
        )

        # Default values
        default_guild = {
            "voice_time": {},
            "message_count": {},
            "voice_sessions": {}
        }

        self.config.register_guild(**default_guild)
        self.voice_sessions = {}  # Temporary storage for active voice sessions

    async def initialize(self):
        """Load voice sessions from config"""
        for guild in self.bot.guilds:
            async with self.config.guild(guild).voice_sessions() as sessions:
                for user_id, timestamp in sessions.items():
                    self.voice_sessions[int(user_id)] = timestamp

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Track voice channel time"""
        if before.channel is None and after.channel is not None:
            # User joined a voice channel
            self.voice_sessions[member.id] = time.time()
            async with self.config.guild(member.guild).voice_sessions() as sessions:
                sessions[str(member.id)] = time.time()

        elif before.channel is not None and after.channel is None:
            # User left a voice channel
            if member.id in self.voice_sessions:
                start_time = self.voice_sessions[member.id]
                duration = time.time() - start_time

                async with self.config.guild(member.guild).voice_time() as voice_time:
                    if str(member.id) not in voice_time:
                        voice_time[str(member.id)] = 0
                    voice_time[str(member.id)] += duration

                # Clean up session data
                del self.voice_sessions[member.id]
                async with self.config.guild(member.guild).voice_sessions() as sessions:
                    if str(member.id) in sessions:
                        del sessions[str(member.id)]

    @commands.Cog.listener()
    async def on_message(self, message):
        """Track message count"""
        if not message.author.bot:
            async with self.config.guild(message.guild).message_count() as message_count:
                if str(message.author.id) not in message_count:
                    message_count[str(message.author.id)] = 0
                message_count[str(message.author.id)] += 1

    @commands.command(aliases=["ut", "track"])
    @checks.mod_or_permissions(manage_messages=True)
    async def usertracker(self, ctx):
        """Show UserTracker help and command list"""
        embed = discord.Embed(
            title="UserTracker Commands",
            description="Track user activities in the server",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )

        commands_info = {
            "User Info": [
                "!userinfo [@user] or !info [@user]",
                "Shows detailed user activity information"
            ],
            "Voice Time": [
                "!voicetime [@user] [days] or !vt [@user] [days]",
                "Shows voice time for specific period"
            ],
            "Top Voice Users": [
                "!topvoice [limit] or !tv [limit]",
                "Shows top users by voice time"
            ],
            "Top Message Senders": [
                "!topmessages [limit] or !tm [limit]",
                "Shows top users by message count"
            ],
            "Reset Stats": [
                "!resetstats [type]",
                "Reset statistics (Admin only)\nTypes: all, voice, messages"
            ]
        }

        for title, (usage, description) in commands_info.items():
            embed.add_field(
                name=title,
                value=f"Usage: `{usage}`\n{description}",
                inline=False
            )

        embed.set_footer(text="All commands require Moderator permissions")
        await ctx.send(embed=embed)

    @commands.command(aliases=["info"])
    @checks.mod_or_permissions(manage_messages=True)
    async def userinfo(self, ctx, member: discord.Member = None):
        """Show detailed user activity information"""
        if member is None:
            member = ctx.author

        # Calculate join date and time ago
        join_date = member.joined_at
        time_ago = humanize_timedelta(timedelta=datetime.utcnow() - join_date)
        formatted_join_date = join_date.strftime("%d/%m/%Y %H:%M:%S")

        # Get voice time
        voice_time_data = await self.config.guild(ctx.guild).voice_time()
        voice_time = voice_time_data.get(str(member.id), 0)

        # Add current session time if user is in voice
        if member.id in self.voice_sessions:
            current_session = time.time() - self.voice_sessions[member.id]
            voice_time += current_session

        voice_hours = voice_time / 3600  # Convert seconds to hours

        # Get message count
        message_count_data = await self.config.guild(ctx.guild).message_count()
        messages = message_count_data.get(str(member.id), 0)

        # Create embed
        embed = discord.Embed(
            title=f"User Activity: {member.name}",
            color=member.color,
            timestamp=datetime.utcnow()
        )
        
        embed.set_thumbnail(url=member.avatar_url)
        embed.add_field(
            name="Join Date", 
            value=f"```{formatted_join_date}\n({time_ago} ago)```",
            inline=False
        )
        embed.add_field(
            name="Voice Channel Time", 
            value=f"```{voice_hours:.2f} hours```",
            inline=True
        )
        embed.add_field(
            name="Messages Sent", 
            value=f"```{messages}```",
            inline=True
        )
        
        embed.set_footer(text=f"ID: {member.id}")

        await ctx.send(embed=embed)

    @commands.command(aliases=["vt"])
    @checks.mod_or_permissions(manage_messages=True)
    async def voicetime(self, ctx, member: discord.Member, days: int = 7):
        """Show voice time for a specific period"""
        voice_time_data = await self.config.guild(ctx.guild).voice_time()
        voice_time = voice_time_data.get(str(member.id), 0)

        # Add current session time if user is in voice
        if member.id in self.voice_sessions:
            current_session = time.time() - self.voice_sessions[member.id]
            voice_time += current_session
        
        voice_hours = voice_time / 3600  # Convert seconds to hours

        embed = discord.Embed(
            title=f"Voice Activity: {member.name}",
            description=f"Voice channel time in the last {days} days",
            color=member.color,
            timestamp=datetime.utcnow()
        )
        
        embed.set_thumbnail(url=member.avatar_url)
        embed.add_field(
            name="Total Time", 
            value=f"```{voice_hours:.2f} hours```",
            inline=False
        )
        
        embed.set_footer(text=f"ID: {member.id}")

        await ctx.send(embed=embed)

    @commands.command(aliases=["tv"])
    @checks.mod_or_permissions(manage_messages=True)
    async def topvoice(self, ctx, limit: int = 10):
        """Show top users by voice time"""
        voice_time_data = await self.config.guild(ctx.guild).voice_time()
        
        # Convert to list of tuples and sort
        voice_times = [(user_id, time) for user_id, time in voice_time_data.items()]
        voice_times.sort(key=lambda x: x[1], reverse=True)
        
        embed = discord.Embed(
            title="Top Voice Users",
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        
        description = ""
        for i, (user_id, time) in enumerate(voice_times[:limit], 1):
            member = ctx.guild.get_member(int(user_id))
            if member:
                hours = time / 3600
                description += f"{i}. {member.name}: {hours:.2f} hours\n"
        
        embed.description = description if description else "No voice activity recorded"
        await ctx.send(embed=embed)

    @commands.command(aliases=["tm"])
    @checks.mod_or_permissions(manage_messages=True)
    async def topmessages(self, ctx, limit: int = 10):
        """Show top users by message count"""
        message_count_data = await self.config.guild(ctx.guild).message_count()
        
        # Convert to list of tuples and sort
        message_counts = [(user_id, count) for user_id, count in message_count_data.items()]
        message_counts.sort(key=lambda x: x[1], reverse=True)
        
        embed = discord.Embed(
            title="Top Message Senders",
            color=discord.Color.green(),
            timestamp=datetime.utcnow()
        )
        
        description = ""
        for i, (user_id, count) in enumerate(message_counts[:limit], 1):
            member = ctx.guild.get_member(int(user_id))
            if member:
                description += f"{i}. {member.name}: {count} messages\n"
        
        embed.description = description if description else "No messages recorded"
        await ctx.send(embed=embed)

    @commands.command()
    @checks.admin()
    async def resetstats(self, ctx, stat_type: str = "all"):
        """Reset statistics for the server
        
        stat_type can be: all, voice, messages"""
        if stat_type.lower() not in ["all", "voice", "messages"]:
            await ctx.send("Invalid stat type. Use 'all', 'voice', or 'messages'")
            return

        if stat_type.lower() in ["all", "voice"]:
            await self.config.guild(ctx.guild).voice_time.set({})
            await self.config.guild(ctx.guild).voice_sessions.set({})
            self.voice_sessions = {}  # Clear memory cache too

        if stat_type.lower() in ["all", "messages"]:
            await self.config.guild(ctx.guild).message_count.set({})

        await ctx.send(f"Successfully reset {stat_type} statistics!")

def setup(bot):
   !cog = UserTracker(bot)
    bot.add_cog(cog)
    bot.loop.create_task(cog.initialize())
