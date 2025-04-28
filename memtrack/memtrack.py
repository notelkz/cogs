from redbot.core import commands, Config
from redbot.core.utils.predicates import MessagePredicate
from datetime import datetime, timedelta
from collections import defaultdict
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
    async def trackexisting(self, ctx, role: discord.Role = None):
        """
        Start tracking users who already have a configured role.
        Usage:
        !mt trackexisting - Track all configured roles
        !mt trackexisting @role - Track specific role
        """
        guild = ctx.guild
        role_tracks = await self.config.guild(guild).role_tracks()
        active_tracks = await self.config.guild(guild).active_tracks()
        
        if not role_tracks:
            await ctx.send("No role tracks configured. Please set up role tracking first.")
            return

        tracked_count = 0
        already_tracked = 0
        
        if role:
            # Check if role is configured
            track_config = None
            for track in role_tracks:
                if track["role_id"] == role.id:
                    track_config = track
                    break
            
            if not track_config:
                await ctx.send(f"The role {role.mention} is not configured for tracking.")
                return
                
            await ctx.send(f"Checking members with {role.name}...")
            
            # Track existing role assignments
            for member in role.members:
                if str(member.id) not in active_tracks:
                    active_tracks[str(member.id)] = {}
                
                # Check if already tracking this role for this member
                if str(role.id) in active_tracks[str(member.id)]:
                    already_tracked += 1
                    continue
                    
                active_tracks[str(member.id)][str(role.id)] = {
                    "start_time": datetime.utcnow().timestamp(),
                    "duration": track_config["duration"],
                    "action": track_config["action"],
                    "new_role_id": track_config["new_role_id"]
                }
                tracked_count += 1
                
                # Start the expiration task
                self.bot.loop.create_task(self.check_role_expiration(member, role, track_config))
        
        else:
            # Track all configured roles
            await ctx.send("Checking members for all configured roles...")
            
            for track in role_tracks:
                role = guild.get_role(track["role_id"])
                if not role:
                    continue
                    
                for member in role.members:
                    if str(member.id) not in active_tracks:
                        active_tracks[str(member.id)] = {}
                    
                    # Check if already tracking this role for this member
                    if str(role.id) in active_tracks[str(member.id)]:
                        already_tracked += 1
                        continue
                        
                    active_tracks[str(member.id)][str(role.id)] = {
                        "start_time": datetime.utcnow().timestamp(),
                        "duration": track["duration"],
                        "action": track["action"],
                        "new_role_id": track["new_role_id"]
                    }
                    tracked_count += 1
                    
                    # Start the expiration task
                    self.bot.loop.create_task(self.check_role_expiration(member, role, track))
        
        # Save updated tracking data
        await self.config.guild(guild).active_tracks.set(active_tracks)
        
        # Send summary
        response = f"Tracking started for {tracked_count} role assignments.\n"
        if already_tracked > 0:
            response += f"{already_tracked} role assignments were already being tracked."
        await ctx.send(response)

    @memtrack.command()
    async def trackuser(self, ctx, member: discord.Member):
        """
        Start tracking all configured roles that a specific user has.
        Usage: !mt trackuser @user
        """
        guild = ctx.guild
        role_tracks = await self.config.guild(guild).role_tracks()
        active_tracks = await self.config.guild(guild).active_tracks()
        
        if not role_tracks:
            await ctx.send("No role tracks configured. Please set up role tracking first.")
            return

        tracked_count = 0
        already_tracked = 0
        
        # Get all configured role IDs
        configured_role_ids = [track["role_id"] for track in role_tracks]
        
        # Check each of the member's roles
        for role in member.roles:
            if role.id in configured_role_ids:
                # Find the track configuration for this role
                track_config = next((track for track in role_tracks if track["role_id"] == role.id), None)
                
                if str(member.id) not in active_tracks:
                    active_tracks[str(member.id)] = {}
                
                # Check if already tracking this role for this member
                if str(role.id) in active_tracks[str(member.id)]:
                    already_tracked += 1
                    continue
                    
                active_tracks[str(member.id)][str(role.id)] = {
                    "start_time": datetime.utcnow().timestamp(),
                    "duration": track_config["duration"],
                    "action": track_config["action"],
                    "new_role_id": track_config["new_role_id"]
                }
                tracked_count += 1
                
                # Start the expiration task
                self.bot.loop.create_task(self.check_role_expiration(member, role, track_config))
        
        # Save updated tracking data
        await self.config.guild(guild).active_tracks.set(active_tracks)
        
        # Send summary
        if tracked_count == 0 and already_tracked == 0:
            await ctx.send(f"{member.mention} has no configured roles to track.")
            return
            
        response = f"Started tracking {tracked_count} roles for {member.mention}.\n"
        if already_tracked > 0:
            response += f"{already_tracked} roles were already being tracked."
        await ctx.send(response)
    @memtrack.command()
    async def duplicates(self, ctx, remove: bool = False):
        """
        Check for duplicate role configurations.
        Use '!mt duplicates true' to remove duplicates automatically.
        """
        guild = ctx.guild
        role_tracks = await self.config.guild(guild).role_tracks()
        configured_roles = await self.config.guild(guild).configured_roles()
        
        # Track duplicates
        role_counts = defaultdict(list)
        for i, track in enumerate(role_tracks):
            role_id = str(track["role_id"])
            role_counts[role_id].append(i)
        
        # Find duplicates
        duplicates = {role_id: indices for role_id, indices in role_counts.items() if len(indices) > 1}
        
        if not duplicates:
            await ctx.send("No duplicate role configurations found.")
            return
        
        response = "**Duplicate Role Configurations Found:**\n\n"
        for role_id, indices in duplicates.items():
            role = guild.get_role(int(role_id))
            role_name = role.name if role else f"Deleted Role (ID: {role_id})"
            response += f"Role: {role_name}\n"
            response += f"Found in configurations: {', '.join(str(i+1) for i in indices)}\n\n"
        
        if remove:
            # Remove duplicates keeping only the first occurrence
            new_tracks = []
            seen_roles = set()
            
            for track in role_tracks:
                role_id = str(track["role_id"])
                if role_id not in seen_roles:
                    new_tracks.append(track)
                    seen_roles.add(role_id)
            
            # Update configured_roles
            new_configured_roles = list(set(configured_roles))
            
            # Save changes
            await self.config.guild(guild).role_tracks.set(new_tracks)
            await self.config.guild(guild).configured_roles.set(new_configured_roles)
            
            response += "\nDuplicate configurations have been removed."
        else:
            response += "\nUse '!mt duplicates true' to remove duplicate configurations."
        
        await ctx.send(response)

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
    async def list(self, ctx, member: discord.Member = None):
        """
        List all configured role tracks or check a specific user's role duration.
        Usage: 
        !mt list - Show all role tracks
        !mt list @user - Show user's current tracked roles and their duration
        """
        guild = ctx.guild
        role_tracks = await self.config.guild(guild).role_tracks()
        
        if member:
            # Show specific user's role information
            active_tracks = await self.config.guild(guild).active_tracks()
            user_tracks = active_tracks.get(str(member.id), {})
            
            if not user_tracks:
                await ctx.send(f"{member.mention} has no actively tracked roles.")
                return

            response = f"**Tracked Roles for {member.mention}:**\n\n"
            current_time = datetime.utcnow().timestamp()
            
            for role_id, track_info in user_tracks.items():
                role = guild.get_role(int(role_id))
                if not role:
                    continue

                # Calculate time had role
                start_time = track_info["start_time"]
                time_had = current_time - start_time
                days_had = time_had / (24 * 60 * 60)
                
                # Format time had
                if days_had < 1:
                    hours = int(time_had // 3600)
                    minutes = int((time_had % 3600) // 60)
                    time_had_str = f"{hours} hours, {minutes} minutes"
                else:
                    time_had_str = f"{days_had:.1f} days"
                
                # Calculate time remaining
                total_duration = track_info["duration"]
                time_remaining = total_duration - time_had
                days_remaining = time_remaining / (24 * 60 * 60)
                
                response += f"Role: {role.name}\n"
                response += f"Time had: {time_had_str}\n"
                response += f"Time remaining: {days_remaining:.1f} days\n"
                
                if track_info["action"] == 1:
                    response += "Action: Role will be removed\n"
                else:
                    new_role = guild.get_role(track_info["new_role_id"])
                    response += f"Action: Will be upgraded to {new_role.name if new_role else 'deleted role'}\n"
                
                response += "\n"
                
            await ctx.send(response)
            return

        # Original list functionality for showing all tracks
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

        # Original list functionality for showing all tracks
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
    @commands.admin_or_permissions(administrator=True)
    async def skip(self, ctx, member: discord.Member):
        """
        Skip the waiting period for a user's tracked roles.
        Usage: !mt skip @user
        """
        guild = ctx.guild
        active_tracks = await self.config.guild(guild).active_tracks()
        user_tracks = active_tracks.get(str(member.id), {})
        
        if not user_tracks:
            await ctx.send(f"{member.mention} has no actively tracked roles.")
            return

        skipped_roles = []
        failed_roles = []
        
        for role_id, track_info in user_tracks.items():
            role = guild.get_role(int(role_id))
            if not role:
                continue

            try:
                if track_info["action"] == 1:
                    # Remove the role
                    await member.remove_roles(role)
                    skipped_roles.append(f"{role.name} (removed)")
                elif track_info["action"] == 2:
                    # Remove old role and add new one
                    new_role = guild.get_role(track_info["new_role_id"])
                    if new_role:
                        await member.add_roles(new_role)
                        await member.remove_roles(role)
                        skipped_roles.append(f"{role.name} → {new_role.name}")
                    else:
                        failed_roles.append(f"{role.name} (upgrade role not found)")
                        continue
            except discord.Forbidden:
                failed_roles.append(f"{role.name} (permission denied)")
                continue
            except discord.HTTPException:
                failed_roles.append(f"{role.name} (error occurred)")
                continue

        # Remove the tracked roles from active_tracks
        if str(member.id) in active_tracks:
            del active_tracks[str(member.id)]
            await self.config.guild(guild).active_tracks.set(active_tracks)

        # Build response message
        response = f"**Role Skip Results for {member.mention}:**\n\n"
        
        if skipped_roles:
            response += "Successfully processed:\n"
            for role in skipped_roles:
                response += f"- {role}\n"
            
        if failed_roles:
            response += "\nFailed to process:\n"
            for role in failed_roles:
                response += f"- {role}\n"
                
        if not skipped_roles and not failed_roles:
            response += "No roles were processed."

        await ctx.send(response)
    
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
