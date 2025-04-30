from .rolesync import RoleSync

__red_end_user_data_statement__ = "This cog stores Discord IDs and role information for role synchronization purposes."

async def setup(bot):
    await bot.add_cog(RoleSync(bot))