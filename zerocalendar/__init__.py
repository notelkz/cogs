# Import directly from the file
import sys
import os
import importlib.util

# Get the path to the zerocalendar.py file
file_path = os.path.join(os.path.dirname(__file__), "zerocalendar.py")

# Import the module
spec = importlib.util.spec_from_file_location("zerocalendar_module", file_path)
zerocalendar_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zerocalendar_module)

# Get the ZeroCalendar class
ZeroCalendar = zerocalendar_module.ZeroCalendar

def setup(bot):
    bot.add_cog(ZeroCalendar(bot))
