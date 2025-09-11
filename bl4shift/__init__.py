"""
Borderlands 4 SHIFT Code Monitor Cog for Red-DiscordBot

Monitors specified subreddits for Borderlands 4 SHIFT codes and posts them to Discord channels.
"""

from .bl4shift import BL4ShiftCodes, setup

__red_end_user_data_statement__ = (
    "This cog stores guild configuration data including channel IDs, subreddit preferences, "
    "check intervals, keywords, and a cache of posted SHIFT codes to prevent duplicates. "
    "No personal user data is stored."
)

__author__ = ["elkz"]
__version__ = "1.0.0"