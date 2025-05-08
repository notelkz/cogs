from .membercount import MemberCount

async def setup(bot):
    await bot.add_cog(MemberCount(bot))
