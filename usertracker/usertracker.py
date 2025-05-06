import discord
import datetime
from redbot.core import commands, checks, Config
from redbot.core.utils.chat_formatting import humanize_timedelta

class UserTracker(commands.Cog):
    """Tracks user join date, voice time, and messages."""

    __author__ = "elkz"
    __version__ = "1.0.0"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "voice": {},  # {user_id: {start: timestamp, total: seconds}}
            "messages": {}  # {user_id: [{timestamp: int}]}
        }
        self.config.register_guild(**default_guild)
        self.voice_states = {}  # {guild_id: {user_id: join_time}}

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        # GDPR compliance
        guilds = self.bot.guilds
        for guild in guilds:
            async with self.config.guild(guild).voice() as voice:
                voice.pop(str(user_id), None)
            async with self.config.guild(guild).messages() as messages:
                messages.pop(str(user_id), None)

    async def cog_load(self):
        # Initialize voice_states for all guilds
        for guild in self.bot.guilds:
            self.voice_states[guild.id] = {}

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        guild = member.guild
        user_id = str(member.id)
        now = datetime.datetime.utcnow().timestamp()

        # Only track if not bot
        if member.bot:
            return

        # User joins voice
        if not before.channel and after.channel:
            # Save join time
            if guild.id not in self.voice_states:
                self.voice_states[guild.id] = {}
            self.voice_states[guild.id][user_id] = now

        # User leaves voice
        elif before.channel and not after.channel:
            join_time = self.voice_states.get(guild.id, {}).pop(user_id, None)
            if join_time:
                duration = int(now - join_time)
                async with self.config.guild(guild).voice() as voice:
                    user_data = voice.get(user_id, {"total": 0})
                    user_data["total"] = user_data.get("total", 0) + duration
                    voice[user_id] = user_data

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.guild is None or message.author.bot:
            return
        user_id = str(message.author.id)
        guild = message.guild
        now = int(message.created_at.timestamp())
        async with self.config.guild(guild).messages() as messages:
            if user_id not in messages:
                messages[user_id] = []
            messages[user_id].append(now)
            # Keep only last 90 days of messages to save space
            ninety_days_ago = int((datetime.datetime.utcnow() - datetime.timedelta(days=90)).timestamp())
            messages[user_id] = [ts for ts in messages[user_id] if ts >= ninety_days_ago]

    @commands.guild_only()
    @commands.command(name="usertracker", aliases=["ut", "track"])
    @checks.mod_or_permissions(administrator=True)
    async def usertracker(self, ctx, member: discord.Member = None, days: int = 7):
        """
        Track a user's join date, voice time, and messages sent over a period.

        Usage: !usertracker @user [days]
        """
        if member is None:
            await ctx.send_help()
            return

        if days < 1 or days > 90:
            await ctx.send("Please specify a period between 1 and 90 days.")
            return

        now = datetime.datetime.utcnow()
        since = now - datetime.timedelta(days=days)

        # Join date
        join_date = member.joined_at
        if join_date is None:
            join_str = "Unknown"
            days_ago = "?"
        else:
            join_str = join_date.strftime("%d/%m/%Y")
            days_ago = (now - join_date).days

        # Voice time
        async with self.config.guild(ctx.guild).voice() as voice:
            user_voice = voice.get(str(member.id), {})
            total_voice = user_voice.get("total", 0)
            # For period, we can't get per-period voice time unless we track per-session.
            # For now, show total voice time.
            voice_time_str = humanize_timedelta(seconds=total_voice)
            period_voice_time_str = "Unavailable (tracking from now on)"  # Could be improved with per-session tracking

        # Messages
        async with self.config.guild(ctx.guild).messages() as messages:
            user_msgs = messages.get(str(member.id), [])
            # Count messages in period
            period_ts = int(since.timestamp())
            msg_count = sum(1 for ts in user_msgs if ts >= period_ts)

        embed = discord.Embed(
            title=f"User Activity for {member.display_name}",
            color=member.color if member.color.value else discord.Color.blue()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Joined Server", value=f"{join_str} ({days_ago} days ago)", inline=False)
        embed.add_field(name=f"Messages (last {days} days)", value=str(msg_count), inline=False)
        embed.add_field(name="Voice Time (total)", value=voice_time_str, inline=False)
        embed.set_footer(text=f"Requested by {ctx.author}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

    @usertracker.error
    async def usertracker_error(self, ctx, error):
        if isinstance(error, commands.BadArgument):
            await ctx.send("Couldn't find that user. Please mention a valid user.")
        else:
            raise error

    @commands.command(name="usertrackerhelp")
    async def usertrackerhelp(self, ctx):
        """Show help for UserTracker."""
        msg = (
            "**UserTracker Commands:**\n"
            "`!usertracker @user [days]` - Show join date, messages, and voice time for a user (default 7 days, max 90).\n"
            "Aliases: `!ut`, `!track`\n"
            "Only admins/mods can use this command."
        )
        await ctx.send(msg)
