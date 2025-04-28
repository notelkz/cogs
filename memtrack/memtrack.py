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
            "active_tracks": {} # Dictionary of active role assignments
        }
        self.config.register_guild(**default_guild)

    @commands.group(aliases=["mt"])
    @commands.admin_or_permissions(administrator=True)
    async def memtrack(self, ctx):
        """Member role tracking commands"""
        if ctx.invoked_subcommand is None:
            await ctx.send("Please use `!memtrack setup` to configure role tracking.")

    @memtrack.command()
    async def test(self, ctx, member: discord.Member = None):
        """Test role tracking with a 30-second timer using configured roles or test roles"""
        if member is None:
            member = ctx.author

        guild = ctx.guild
        role_tracks = await self.config.guild(guild).role_tracks()

        async def cleanup_test_roles(test_roles):
            """Helper function to clean up temporary test roles"""
            await ctx.send("Cleaning up test roles...")
            for role in test_roles:
                try:
                    await role.delete()
                except discord.HTTPException:
                    pass
            await ctx.send("Test cleanup completed!")

        try:
            if role_tracks:
                # Use existing configuration
                track = role_tracks[0]  # Use first configured track
                initial_role = guild.get_role(track["role_id"])
                upgrade_role = guild.get_role(track["new_role_id"]) if track["action"] == 2 else None

                if not initial_role or (track["action"] == 2 and not upgrade_role):
                    await ctx.send("Configured roles not found. Creating temporary test roles...")
                    # Fall back to creating temporary roles
                    test_roles = []
                    initial_role = await guild.create_role(name="MemberTracker Test Role")
                    test_roles.append(initial_role)
                    if track["action"] == 2:
                        upgrade_role = await guild.create_role(name="MemberTracker Test Role 2")
                        test_roles.append(upgrade_role)
                else:
                    test_roles = []  # No test roles to clean up
                    await ctx.send(f"Using configured roles for testing:\nInitial Role: {initial_role.name}")
                    if upgrade_role:
                        await ctx.send(f"Upgrade Role: {upgrade_role.name}")

            else:
                # No configurations found, create temporary test roles
                await ctx.send("No role configurations found. Creating temporary test roles...")
                test_roles = []
                initial_role = await guild.create_role(name="MemberTracker Test Role")
                test_roles.append(initial_role)
                upgrade_role = await guild.create_role(name="MemberTracker Test Role 2")
                test_roles.append(upgrade_role)
                
                track = {
                    "role_id": initial_role.id,
                    "duration": 30,  # 30 seconds
                    "action": 2,
                    "new_role_id": upgrade_role.id
                }

            # Start the test
            await ctx.send(f"Starting role tracking test for {member.mention}")
            await ctx.send("Phase 1: Adding initial role...")
            await member.add_roles(initial_role)
            
            # Wait for duration
            await ctx.send("Waiting 30 seconds...")
            await asyncio.sleep(30)

            if track["action"] == 1:
                await ctx.send("Phase 2: Removing role...")
                await member.remove_roles(initial_role)
            else:
                await ctx.send("Phase 2: Upgrading to second role...")
                await member.add_roles(upgrade_role)
                await member.remove_roles(initial_role)

            await ctx.send("Test completed!")

            # Cleanup temporary test roles if any were created
            if test_roles:
                await asyncio.sleep(5)  # Wait a bit before cleaning up
                await cleanup_test_roles(test_roles)

        except discord.Forbidden:
            await ctx.send("I don't have permission to manage roles!")
            if 'test_roles' in locals() and test_roles:
                await cleanup_test_roles(test_roles)
        except discord.HTTPException as e:
            await ctx.send(f"An error occurred: {str(e)}")
            if 'test_roles' in locals() and test_roles:
                await cleanup_test_roles(test_roles)

    @memtrack.command()
    async def setup(self, ctx):
        """Setup role tracking configuration"""
        guild = ctx.guild
        role_tracks = []
        
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
                
                # Save the configuration
                track_config = {
                    "role_id": role.id,
                    "duration": duration,
                    "action": action,
                    "new_role_id": new_role
                }
                
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
