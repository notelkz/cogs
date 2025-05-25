from .welcome import Welcome

__red_end_user_data_statement__ = "This cog stores server configurations for welcome messages and raid protection settings. It temporarily stores recent join timestamps for raid detection but does not permanently store any user data."

async def setup(bot):
    await bot.add_cog(Welcome(bot))
