from redbot.core import commands, Config
from redbot.core.utils.predicates import MessagePredicate
from datetime import datetime, timedelta
import asyncio
import discord

class MemberTracker(commands.Cog):
    """Track how long members have specific roles."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=1234567890)
        default_guild = {
            "role_tracks": [],  # List of role tracking configurations
            "active_tracks": {}, # Dictionary of active role assignments
            "configured_roles": [] # List of role IDs that have been configured
        }
        self.config.register_guild(**default_guild)

    async def cog_load(self):
        """Load existing configurations when cog is loaded/reloaded"""
        for guild in self.bot.guilds:
            # Get existing role tracks
            role_tracks = await self.config.guild(guild).role_tracks()
            configured_roles = await self.config.guild(guild).configured_roles()
            
            # Update configured_roles based on existing role_tracks
            for track in role_tracks:
                role_id = str(track["role_id"])
                if role_id not in configured_roles:
                    configured_roles.append(role_id)
                if track["action"] == 2 and track["new_role_id"]:
                    new_role_id = str(track["new_role_id"])
                    if new_role_id not in configured_roles:
                        configured_roles.append(new_role_id)
            
            # Save updated configured_roles
            await self.config.guild(guild).configured_roles.set(configured_roles)

    @commands.group(aliases=["mt"])
    @commands.admin_or_permissions(administrator=True)
    async def memtrack(self, ctx):
        """Member role tracking commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send("Please use `!memtrack setup` to configure role tracking.")

    async def is_role_configured(self, guild: discord.Guild, role_id: int) -> bool:
        """Check if a role is already configured"""
        configured_roles = await self.config.guild(guild).configured_roles()
        return str(role_id) in configured_roles

    async def add_configured_role(self, guild: discord.Guild, role_id: int):
        """Add a role to the configured roles list"""
        configured_roles = await self.config.guild(guild).configured_roles()
        if str(role_id) not in configured_roles:
            configured_roles.append(str(role_id))
            await self.config.guild(guild).configured_roles.set(configured_roles)

    @memtrack.command()
    async def debug(self, ctx):
        """Debug command to show all configured roles"""
        guild = ctx.guild
        configured_roles = await self.config.guild(guild).configured_roles()
        role_tracks = await self.config.guild(guild).role_tracks()
        
        response = "**Currently Configured Roles:**\n"
        for role_id in configured_roles:
            role = guild.get_role(int(role_id))
            response += f"- {role.name if role else 'Deleted role'} (ID: {role_id})\n"
            
        response += "\n**Current Role Tracks:**\n"
        for track in role_tracks:
            initial_role = guild.get_role(track["role_id"])
            response += f"- {initial_role.name if initial_role else 'Deleted role'} "
            if track["action"] == 2:
                upgrade_role = guild.get_role(track["new_role_id"]) if track["new_role_id"] else None
                response += f"-> {upgrade_role.name if upgrade_role else 'Deleted role'}"
            else:
                response += "-> Remove role"
            response += "\n"
            
        await ctx.send(response)

    @memtrack.command()
    async def reset(self, ctx):
        """Reset all role configurations"""
        guild = ctx.guild
        await self.config.guild(guild).role_tracks.set([])
        await self.config.guild(guild).configured_roles.set([])
        await self.config.guild(guild).active_tracks.set({})
        await ctx.send("All role configurations have been reset.")

    @memtrack.command()
    async def list(self, ctx):
        """List all configured role tracks"""
        guild = ctx.guild
        role_tracks = await self.config.guild(guild).role_tracks()
        
        if not role_tracks:
            await ctx.send("No role tracks configured.")
            return

        response = "**Configured Role Tracks:**\n\n"
        for i, track in enumerate(role_tracks, 1):
            role = guild.get_role(track["role_id"])
            if not role:
                continue
                
            duration_days = track["duration"] / (24 * 60 * 60)
            
            if track["action"] == 1:
                action = "Remove role"
            else:
                new_role = guild.get_role(track["new_role_id"])
                action = f"Upgrade to {new_role.name if new_role else 'deleted role'}"
                
            response += f"{i}. Role: {role.name}\n"
            response += f"   Duration: {duration_days} days\n"
            response += f"   Action: {action}\n\n"
            
        await ctx.send(response)

    async def run_test(self, ctx, role_tracks):
        """Helper function to run a test of the role tracking system"""
        member = ctx.author
        guild = ctx.guild
        added_roles = []  # Track roles added during testing

        try:
            # Use first configured track for testing
            track = role_tracks[0]
            initial_role = guild.get_role(track["role_id"])
            upgrade_role = guild.get_role(track["new_role_id"]) if track["action"] == 2 else None

            if not initial_role or (track["action"] == 2 and not upgrade_role):
                await ctx.send("Configured roles not found. Test cancelled.")
                return

            # Start the test
            await ctx.send(f"Starting role tracking test for {member.mention}")
            await ctx.send("Phase 1: Adding initial role...")
            await member.add_roles(initial_role)
            added_roles.append(initial_role)
            
            # Wait for 30 seconds
            await ctx.send("Waiting 30 seconds...")
            await asyncio.sleep(30)

            if track["action"] == 1:
                await ctx.send("Phase 2: Removing role...")
                await member.remove_roles(initial_role)
            else:
                await ctx.send("Phase 2: Upgrading to second role...")
                await member.add_roles(upgrade_role)
                added_roles.append(upgrade_role)
                await member.remove_roles(initial_role)

            await ctx.send("Test completed!")

            # Cleanup - remove all added roles
            await asyncio.sleep(5)  # Wait a bit before cleaning up
            await ctx.send("Cleaning up test roles...")
            for role in added_roles:
                if role in member.roles:
                    await member.remove_roles(role)
            await ctx.send("Test cleanup completed!")

        except discord.Forbidden:
            await ctx.send("I don't have permission to manage roles!")
            # Try to clean up
            for role in added_roles:
                try:
                    if role in member.roles:
                        await member.remove_roles(role)
                except discord.HTTPException:
                    pass
        except discord.HTTPException as e:
            await ctx.send(f"An error occurred: {str(e)}")
            # Try to clean up
            for role in added_roles:
                try:
                    if role in member.roles:
                        await member.remove_roles(role)
                except discord.HTTPException:
                    pass

    @memtrack.command()
    async def setup(self, ctx):
        """Setup role tracking configuration"""
        guild = ctx.guild
        role_tracks = await self.config.guild(guild).role_tracks()
        
        while True:
            # Ask for the role to monitor
            await ctx.send("Please mention the role you want to monitor:")
            try:
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                
                if len(msg.role_mentions) == 0:
                    await ctx.send("No role mentioned. Setup cancelled.")
                    return
                
                role = msg.role_mentions[0]
                
                # Check if role is already configured
                if await self.is_role_configured(guild, role.id):
                    await ctx.send(f"The role {role.name} is already configured. Please choose a different role.")
                    continue
                
                # Ask for duration
                await ctx.send("How long should users keep this role? (Format: 1d, 30d, etc.)")
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                
                try:
                    duration = int(msg.content[:-1])
                    if msg.content.lower().endswith('d'):
                        duration = duration * 24 * 60 * 60  # Convert to seconds
                    else:
                        await ctx.send("Invalid duration format. Use format like '30d'. Setup cancelled.")
                        return
                except ValueError:
                    await ctx.send("Invalid duration. Setup cancelled.")
                    return
                
                # Ask about after-duration action
                await ctx.send("What should happen after the duration expires?\n1. Remove the role\n2. Assign a new role and remove old one\n\nType 1 or 2:")
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                
                action = int(msg.content)
                new_role = None
                
                if action == 2:
                    await ctx.send("Please mention the new role to assign:")
                    msg = await self.bot.wait_for(
                        "message",
                        check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                        timeout=30.0
                    )
                    if len(msg.role_mentions) == 0:
                        await ctx.send("No role mentioned. Setup cancelled.")
                        return
                    new_role = msg.role_mentions[0].id
                    
                    # Check if upgrade role is already configured
                    if await self.is_role_configured(guild, new_role):
                        await ctx.send(f"The upgrade role is already configured. Setup cancelled.")
                        return
                
                # Save the configuration
                track_config = {
                    "role_id": role.id,
                    "duration": duration,
                    "action": action,
                    "new_role_id": new_role
                }
                
                # Add roles to configured roles list
                await self.add_configured_role(guild, role.id)
                if new_role:
                    await self.add_configured_role(guild, new_role)
                
                role_tracks.append(track_config)
                
                # Ask if they want to add more roles
                await ctx.send("Do you want to configure another role? (yes/no)")
                msg = await self.bot.wait_for(
                    "message",
                    check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                    timeout=30.0
                )
                
                if msg.content.lower() != "yes":
                    break
                
            except asyncio.TimeoutError:
                await ctx.send("Setup timed out.")
                return
        
        # Save all configurations
        await self.config.guild(guild).role_tracks.set(role_tracks)
        await ctx.send("Role tracking setup complete!")

        # Ask if they want to run a test
        await ctx.send("Would you like to run a test of the role tracking system? (yes/no)")
        try:
            msg = await self.bot.wait_for(
                "message",
                check=lambda m: m.author == ctx.author and m.channel == ctx.channel,
                timeout=30.0
            )
            
            if msg.content.lower() == "yes":
                await self.run_test(ctx, role_tracks)
            else:
                await ctx.send("Setup completed without testing.")
        
        except asyncio.TimeoutError:
            await ctx.send("No response received. Setup completed without testing.")

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        """Monitor role changes and start tracking when necessary"""
        if before.roles == after.roles:
            return
            
        guild = after.guild
        role_tracks = await self.config.guild(guild).role_tracks()
        active_tracks = await self.config.guild(guild).active_tracks()
        
        # Check for new roles
        for role in after.roles:
            if role not in before.roles:
                for track in role_tracks:
                    if track["role_id"] == role.id:
                        # Start tracking this role
                        if str(after.id) not in active_tracks:
                            active_tracks[str(after.id)] = {}
                        
                        active_tracks[str(after.id)][str(role.id)] = {
                            "start_time": datetime.utcnow().timestamp(),
                            "duration": track["duration"],
                            "action": track["action"],
                            "new_role_id": track["new_role_id"]
                        }
                        
                        await self.config.guild(guild).active_tracks.set(active_tracks)
                        
                        # Start the expiration task
                        self.bot.loop.create_task(self.check_role_expiration(after, role, track))

    async def check_role_expiration(self, member, role, track_config):
        """Check if a role has expired and perform the necessary action"""
        await asyncio.sleep(track_config["duration"])
        
        # Verify member still has role and is still in guild
        if member.guild is None or role not in member.roles:
            return
            
        try:
            if track_config["action"] == 1:
                # Remove the role
                await member.remove_roles(role)
            elif track_config["action"] == 2:
                # Remove old role and add new one
                new_role = member.guild.get_role(track_config["new_role_id"])
                if new_role:
                    await member.add_roles(new_role)
                await member.remove_roles(role)
                
            # Remove from active tracking
            active_tracks = await self.config.guild(member.guild).active_tracks()
            if str(member.id) in active_tracks:
                if str(role.id) in active_tracks[str(member.id)]:
                    del active_tracks[str(member.id)][str(role.id)]
                    if not active_tracks[str(member.id)]:
                        del active_tracks[str(member.id)]
                    await self.config.guild(member.guild).active_tracks.set(active_tracks)
                    
        except discord.Forbidden:
            # Bot doesn't have permission to manage roles
            pass

def setup(bot):
    bot.add_cog(MemberTracker(bot))
