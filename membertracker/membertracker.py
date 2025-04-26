from redbot.core import commands, Config
import discord
from datetime import datetime, timedelta
import asyncio

class MemberTracker(commands.Cog):
    """Basic member tracking cog"""
    
    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "notification_channel": None,
            "wait_period": 14,
            "member_joins": {},
            "roles_to_add": [],
            "roles_to_remove": [],
            "notify_role_changes": True
        }
        self.config.register_guild(**default_guild)
        self.bg_task = self.bot.loop.create_task(self.check_member_duration())

    def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.bg_task.cancel()

    async def check_member_duration(self):
        """Background task to check member duration and manage roles"""
        await self.bot.wait_until_ready()
        while True:
            try:
                all_guilds = await self.config.all_guilds()
                for guild_id, guild_data in all_guilds.items():
                    guild = self.bot.get_guild(guild_id)
                    if not guild:
                        continue

                    channel_id = guild_data.get("notification_channel")
                    if not channel_id:
                        continue

                    channel = guild.get_channel(channel_id)
                    if not channel:
                        continue

                    wait_period = guild_data.get("wait_period", 14)
                    member_joins = guild_data.get("member_joins", {})
                    notify_changes = guild_data.get("notify_role_changes", True)
                    current_date = datetime.utcnow()

                    for member_id, join_date_str in member_joins.copy().items():
                        join_date = datetime.strptime(join_date_str, "%d/%m/%Y")
                        days_passed = (current_date - join_date).days

                        if days_passed >= wait_period:
                            member = guild.get_member(int(member_id))
                            if member:
                                roles_to_add = [guild.get_role(role_id) for role_id in guild_data.get("roles_to_add", [])]
                                roles_to_remove = [guild.get_role(role_id) for role_id in guild_data.get("roles_to_remove", [])]

                                try:
                                    # Add roles
                                    for role in roles_to_add:
                                        if role and role not in member.roles:
                                            await member.add_roles(role)

                                    # Remove roles
                                    for role in roles_to_remove:
                                        if role and role in member.roles:
                                            await member.remove_roles(role)

                                    # Base notification
                                    message = (
                                        f"🎉 {member.mention} has been a member of the server for {wait_period} days! "
                                        f"They joined on {join_date_str}"
                                    )

                                    # Add role change information if enabled
                                    if notify_changes:
                                        role_changes = []
                                        if roles_to_add:
                                            role_changes.append(f"Added roles: {', '.join(role.name for role in roles_to_add if role)}")
                                        if roles_to_remove:
                                            role_changes.append(f"Removed roles: {', '.join(role.name for role in roles_to_remove if role)}")
                                        if role_changes:
                                            message += f"\n{' | '.join(role_changes)}"

                                    await channel.send(message)

                                except discord.Forbidden:
                                    await channel.send(f"⚠️ Failed to modify roles for {member.mention} due to permissions.")

                            # Remove the member from tracking after processing
                            async with self.config.guild(guild).member_joins() as joins:
                                joins.pop(str(member_id), None)

            except Exception as e:
                print(f"Error in member duration check: {e}")

            # Check every hour
            await asyncio.sleep(3600)

    @commands.group()
    @commands.admin_or_permissions(administrator=True)
    async def membertrack(self, ctx):
        """Member tracking commands"""
        pass

    @membertrack.command()
    async def test(self, ctx):
        """Test if the cog is working"""
        await ctx.send("MemberTracker cog is working!")

    @membertrack.command()
    async def setchannel(self, ctx, channel: discord.TextChannel):
        """Set the notification channel"""
        await self.config.guild(ctx.guild).notification_channel.set(channel.id)
        await ctx.send(f"Channel set to {channel.mention}")

    @membertrack.command()
    async def setperiod(self, ctx, days: int):
        """Set the waiting period in days"""
        if days < 1:
            await ctx.send("The waiting period must be at least 1 day.")
            return
        
        await self.config.guild(ctx.guild).wait_period.set(days)
        await ctx.send(f"Waiting period set to {days} days.")

    @membertrack.command()
    async def setroles(self, ctx):
        """Set roles to add/remove after the waiting period"""
        # Helper function to get role selection
        async def get_roles(prompt):
            await ctx.send(prompt)
            try:
                response = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=60
                )
                if response.content.lower() == "none":
                    return []
                
                roles = []
                role_ids = [role.strip() for role in response.content.split(",")]
                for role_id in role_ids:
                    try:
                        if role_id.startswith("<@&") and role_id.endswith(">"):
                            role_id = role_id[3:-1]
                        role = ctx.guild.get_role(int(role_id))
                        if role:
                            roles.append(role.id)
                    except ValueError:
                        continue
                return roles
            except asyncio.TimeoutError:
                await ctx.send("Setup timed out. Please try again.")
                return None

        await ctx.send("Please enter the roles to add after the waiting period.\n"
                      "You can use role IDs or mentions, separated by commas.\n"
                      "Type 'none' if you don't want to add any roles.")
        
        add_roles = await get_roles("Which roles should be added?")
        if add_roles is None:
            return

        remove_roles = await get_roles("Which roles should be removed? (Type 'none' for no roles)")
        if remove_roles is None:
            return

        await ctx.send("Would you like to receive notifications when roles are added/removed? (yes/no)")
        try:
            response = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel and m.content.lower() in ["yes", "no"],
                timeout=30
            )
            notify_changes = response.content.lower() == "yes"
        except asyncio.TimeoutError:
            await ctx.send("No response received, defaulting to showing notifications.")
            notify_changes = True

        async with self.config.guild(ctx.guild).all() as guild_data:
            guild_data["roles_to_add"] = add_roles
            guild_data["roles_to_remove"] = remove_roles
            guild_data["notify_role_changes"] = notify_changes

        await ctx.send("Role settings have been updated!")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        """Log when a member joins the server"""
        if member.bot:
            return

        join_date = datetime.utcnow().strftime("%d/%m/%Y")
        async with self.config.guild(member.guild).member_joins() as joins:
            joins[str(member.id)] = join_date

    @membertrack.command()
    async def settings(self, ctx):
        """View current settings"""
        guild_data = await self.config.guild(ctx.guild).all()
        
        channel = ctx.guild.get_channel(guild_data["notification_channel"])
        channel_mention = channel.mention if channel else "Not set"
        
        add_roles = [ctx.guild.get_role(role_id).name for role_id in guild_data["roles_to_add"] if ctx.guild.get_role(role_id)]
        remove_roles = [ctx.guild.get_role(role_id).name for role_id in guild_data["roles_to_remove"] if ctx.guild.get_role(role_id)]

        settings_msg = (
            f"**Current Settings**\n"
            f"Notification Channel: {channel_mention}\n"
            f"Wait Period: {guild_data['wait_period']} days\n"
            f"Roles to Add: {', '.join(add_roles) if add_roles else 'None'}\n"
            f"Roles to Remove: {', '.join(remove_roles) if remove_roles else 'None'}\n"
            f"Role Change Notifications: {'Enabled' if guild_data['notify_role_changes'] else 'Disabled'}"
        )
        await ctx.send(settings_msg)
