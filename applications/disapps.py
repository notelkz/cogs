from redbot.core import commands, Config
import discord
from discord.ui import Button, View, Modal, TextInput, Select
from typing import List
import asyncio

class GameSelect(Select):
    def __init__(self):
        options = [
            discord.SelectOption(label="Minecraft", value="minecraft"),
            discord.SelectOption(label="League of Legends", value="lol"),
            discord.SelectOption(label="Valorant", value="valorant"),
            discord.SelectOption(label="CS:GO", value="csgo"),
            discord.SelectOption(label="Fortnite", value="fortnite"),
            discord.SelectOption(label="Other", value="other")
        ]
        super().__init__(
            placeholder="Select the games you play...",
            min_values=1,
            max_values=len(options),
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"Games selected: {', '.join(self.values)}",
            ephemeral=True
        )

class GameSelectView(View):
    def __i
