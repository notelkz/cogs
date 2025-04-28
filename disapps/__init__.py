from .disapps import DisApps

def setup(bot):
    bot.add_cog(DisApps(bot))

    def __init__(self, bot):
    self.bot = bot
    self.config = Config.get_conf(self, identifier=1234567890)
    default_guild = {
        "mod_role": None,
        "accepted_role": None,
        "assignable_roles": [],
        "applications_category": None,
        "archive_category": None,
        "setup_complete": False,
        "applications": {},
        "decline_counts": {}  # Add this new field
    }
        self.config.register_guild(**default_guild)
        self.bot.loop.create_task(self.initialize())

    async def initialize(self):
        """Initialize the cog and migrate data if necessary"""
        await self.bot.wait_until_ready()
        
        # Perform data migration for all guilds
        all_guilds = await self.config.all_guilds()
        for guild_id, guild_data in all_guilds.items():
            guild = self.bot.get_guild(guild_id)
            if not guild:
                continue

            # Check version and perform migrations if needed
            version = guild_data.get("version", "0.0.0")
            if version != "1.0.0":
                await self.migrate_data(guild, version)

    async def migrate_data(self, guild, old_version):
        """Migrate data from old versions to new version"""
        async with self.config.guild(guild).all() as guild_data:
            if old_version == "0.0.0":
                # Migrate from pre-versioned data
                applications = guild_data.get("applications", {})
                for app_id, app_data in applications.items():
                    if "previously_accepted" not in app_data:
                        app_data["previously_accepted"] = False
                    if "application_history" not in app_data:
                        app_data["application_history"] = []
                        if app_data.get("status") in ["accepted", "declined"]:
                            app_data["application_history"].append({
                                "status": app_data["status"],
                                "timestamp": app_data.get("timestamp", datetime.utcnow().timestamp()),
                                "reason": app_data.get("decline_reason", "")
                            })
                guild_data["applications"] = applications
            
            # Update version
            guild_data["version"] = "1.0.0"
