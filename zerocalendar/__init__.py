def setup(bot):
    # Import the class directly inside the setup function
    from zerocalendar.zerocalendar import ZeroCalendar
    bot.add_cog(ZeroCalendar(bot))
