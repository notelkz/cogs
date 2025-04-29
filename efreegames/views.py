import discord
from discord.ui import Modal, Button, View, TextInput
from typing import Optional, Callable
import logging

logger = logging.getLogger(__name__)

class StoreAPIModal(Modal):
    def __init__(self, store_name: str, callback: Callable):
        super().__init__(title=f"Configure {store_name} API")
        self.store_name = store_name
        self.callback = callback
        
        self.api_key = TextInput(
            label="API Key",
            placeholder="Enter your API key...",
            required=True
        )
        
        self.api_secret = TextInput(
            label="API Secret",
            placeholder="Enter your API secret...",
            required=False
        )
        
        self.add_item(self.api_key)
        self.add_item(self.api_secret)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await self.callback(
                self.store_name,
                self.api_key.value,
                self.api_secret.value
            )
            await interaction.response.send_message(
                f"{self.store_name} API configured successfully!",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"Error configuring {self.store_name} API: {str(e)}")
            await interaction.response.send_message(
                f"Error configuring API: {str(e)}",
                ephemeral=True
            )

class GameClaimView(View):
    def __init__(self, game_url: str, account_link_callback: Callable):
        super().__init__()
        self.game_url = game_url
        self.account_link_callback = account_link_callback

    @discord.ui.button(label="Claim Now", style=discord.ButtonStyle.green)
    async def claim_button(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_message(
            f"Claim your game here: {self.game_url}",
            ephemeral=True
        )

    @discord.ui.button(label="Link Account", style=discord.ButtonStyle.red)
    async def link_button(self, interaction: discord.Interaction, button: Button):
        await self.account_link_callback(interaction)

class ConfirmationView(View):
    def __init__(self, callback: Callable):
        super().__init__()
        self.callback = callback

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: Button):
        await self.callback(interaction, True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: Button):
        await self.callback(interaction, False)
