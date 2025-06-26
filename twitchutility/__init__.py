import asyncio
import logging
import aiohttp
import discord
from redbot.core import commands, Config
from redbot.core.bot import Red
from redbot.core.tasks import loop
from redbot.core.utils.predicates import MessagePredicate
from PIL import Image, ImageDraw, ImageFont # Core library for image generation
import io
import os
import datetime
from datetime import timedelta
import pytz # For timezone handling
import dateutil.parser # For parsing ISO 8601 dates from Twitch API
import re # For time format validation
import traceback # For detailed error logging

log = logging.getLogger("red.twitchutility")

# Define default settings for the combined cog's configuration
DEFAULT_GUILD_SETTINGS = {
    # --- Live Notifier Settings ---
    "twitch_user_id": None,  # The Twitch user ID to monitor for live status
    "notification_channel_id": None,  # The Discord channel ID for go-live notifications
    "ping_role_ids": [],  # List of Discord role IDs to ping for go-live notifications
    "last_stream_status": "offline",  # To prevent duplicate notifications ('online' or 'offline')
    "notification_message": "{streamer} is now LIVE on Twitch! Go check out the stream: {url}",
    "twitch_username": None, # The Twitch username (used for both features)

    # --- Schedule Scheduler Settings ---
    "schedule_channel_id": None,  # Discord channel ID to post the schedule image
    "auto_update_days": [],  # List of integers (0=Monday, 6=Sunday) for automatic update days
    "auto_update_time": None,  # String (HH:MM) for the time of automatic updates
    "schedule_message_id": None,  # The ID of the last posted schedule message (for pin/delete)
    "schedule_ping_role_id": None,  # Role ID to ping for schedule updates (renamed for clarity)
    "display_event_count": 5,  # Number of upcoming events to display on the image (1-10)
    "display_timezone": "Europe/London",  # Timezone for displaying times on the image and text list
    "last_auto_update_date": None, # Stores the date (YYYY-MM-DD) of the last successful auto-update
    "font_url": "https://zerolivesleft.net/notelkz/P22.ttf", # Default URL for the font file
    "template_image_url": "https://zerolivesleft.net/notelkz/schedule.png", # Default URL for the template image
}

class TwitchUtility(commands.Cog):
    """
    A comprehensive utility cog for Twitch streamers, offering live notifications
    and automatic schedule posting.
    """

    def __init__(self, bot: Red):
        self.bot = bot
        # Initialize Config for guild-specific settings
        # Use a new unique identifier for this combined cog
        self.config = Config.get_conf(
            self,
            identifier=9876543210, # New unique identifier for the combined cog
            force_registration=True
        )
        self.config.register_guild(**DEFAULT_GUILD_SETTINGS)

        # aiohttp ClientSession for making ALL HTTP requests to Twitch API and file downloads
        self.session = aiohttp.ClientSession()

        # Twitch API credentials and token management (shared by both features)
        self.twitch_client_id = None
        self.twitch_client_secret = None
        self.twitch_access_token = None
        self.twitch_token_expiry = 0 # Unix timestamp when the token is considered expired

        # Paths for cached external resources (font, template image)
        self.cache_directory = os.path.join(os.path.dirname(__file__), "cached_assets")
        self.font_file_path = os.path.join(self.cache_directory, "UtilityFont.ttf") # Renamed for new cog
        self.template_image_path = os.path.join(self.cache_directory, "UtilityScheduleTemplate.png") # Renamed

        # Ensure the cache directory exists
        if not os.path.exists(self.cache_directory):
            os.makedirs(self.cache_directory)
            log.info(f"Created asset cache directory: {self.cache_directory}")

        # Asyncio Event to ensure the cog is fully initialized and credentials are ready
        self.initialization_complete = asyncio.Event()

    async def cog_load(self):
        """
        Actions to perform when the cog is loaded.
        Includes fetching Twitch credentials, ensuring resources, and starting background tasks.
        """
        log.info("TwitchUtility cog loading...")
        await self._load_twitch_api_credentials()
        # On cog load, attempt to download default assets to ensure basic functionality
        await self._ensure_required_assets_on_load() 
        self.check_twitch_stream_status.start() # Start live notification loop
        self.auto_schedule_updater.start() # Start schedule update loop
        self.initialization_complete.set() # Mark initialization as complete
        log.info("TwitchUtility cog loaded and tasks started.")

    async def cog_unload(self):
        """
        Actions to perform when the cog is unloaded.
        Includes stopping background tasks and closing the aiohttp session.
        """
        log.info("TwitchUtility cog unloading...")
        self.check_twitch_stream_status.cancel() # Stop live notification loop
        self.auto_schedule_updater.cancel() # Stop schedule update loop
        if self.session:
            await self.session.close() # Close the aiohttp session gracefully
        log.info("TwitchUtility cog unloaded successfully.")

    # --- Shared Twitch API Authentication and Helpers ---

    async def _load_twitch_api_credentials(self):
        """
        Loads Twitch Client ID and Client Secret from bot's shared API tokens.
        Initiates the process to get an application access token.
        """
        try:
            credentials = await self.bot.get_shared_api_tokens("twitch")
            self.twitch_client_id = credentials.get("client_id")
            self.twitch_client_secret = credentials.get("client_secret")

            if not self.twitch_client_id or not self.twitch_client_secret:
                log.warning("Twitch API Client ID or Client Secret not configured. "
                            "Please set them using `[p]set api twitch client_id YOUR_CLIENT_ID client_secret YOUR_CLIENT_SECRET`.")
            else:
                log.info("Twitch API credentials successfully loaded.")
                await self._refresh_twitch_app_access_token() # Immediately get an access token
        except Exception as e:
            log.error(f"Error loading Twitch API credentials: {e}", exc_info=True)

    async def _refresh_twitch_app_access_token(self):
        """
        Obtains a new Twitch application access token using the client credentials flow.
        This token is used for making most Twitch API requests.
        """
        if not self.twitch_client_id or not self.twitch_client_secret:
            log.warning("Cannot refresh Twitch access token: Client ID or Secret are missing.")
            self.twitch_access_token = None
            return

        token_endpoint = "https://id.twitch.tv/oauth2/token"
        payload = {
            "client_id": self.twitch_client_id,
            "client_secret": self.twitch_client_secret,
            "grant_type": "client_credentials"
        }
        try:
            async with self.session.post(token_endpoint, data=payload) as response:
                response.raise_for_status() # Raises an exception for HTTP error codes (4xx, 5xx)
                data = await response.json()
                self.twitch_access_token = data.get("access_token")
                # Set expiry time to 60 seconds before actual expiry to ensure timely refresh
                self.twitch_token_expiry = asyncio.get_event_loop().time() + data.get("expires_in", 3600) - 60
                log.info("Successfully refreshed Twitch application access token.")
        except aiohttp.ClientError as e:
            log.error(f"Failed to refresh Twitch access token (HTTP Error): {e}", exc_info=True)
            self.twitch_access_token = None
        except Exception as e:
            log.error(f"An unexpected error occurred during Twitch token refresh: {e}", exc_info=True)
            self.twitch_access_token = None

    async def _get_twitch_request_headers(self):
        """
        Constructs and returns the necessary HTTP headers for Twitch API requests.
        Refreshes the access token if it's expired or missing.
        """
        # Check if the current token is expired or not yet obtained
        if not self.twitch_access_token or asyncio.get_event_loop().time() >= self.twitch_token_expiry:
            log.debug("Twitch access token is expired or missing. Attempting to refresh.")
            await self._refresh_twitch_app_access_token()

        if not self.twitch_access_token:
            log.error("Unable to get valid Twitch access token for API request.")
            return None

        return {
            "Client-ID": self.twitch_client_id,
            "Authorization": f"Bearer {self.twitch_access_token}"
        }

    async def _fetch_twitch_user_id(self, username: str) -> str | None:
        """
        Fetches the Twitch user ID for a given Twitch username.
        Returns the user ID as a string, or None if not found/error.
        """
        headers = await self._get_twitch_request_headers()
        if not headers:
            return None

        api_url = f"https://api.twitch.tv/helix/users?login={username}"
        try:
            async with self.session.get(api_url, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
                if data and data.get("data"):
                    user_id = data["data"][0]["id"]
                    log.debug(f"Resolved Twitch username '{username}' to ID '{user_id}'.")
                    return user_id
                log.warning(f"Twitch user ID not found for username '{username}'. API response: {data}")
                return None
        except aiohttp.ClientError as e:
            log.error(f"HTTP error fetching Twitch user ID for '{username}': {e}", exc_info=True)
            return None
        except Exception as e:
            log.error(f"Unexpected error fetching Twitch user ID for '{username}': {e}", exc_info=True)
            return None

    # --- Live Notification Specifics ---

    async def _is_streamer_live(self, twitch_user_id: str) -> bool | None:
        """
        Checks if a Twitch streamer is currently live.
        Returns True if live, False if offline, None if error/not found.
        """
        headers = await self._get_twitch_request_headers()
        if not headers:
            return None

        api_url = f"https://api.twitch.tv/helix/streams?user_id={twitch_user_id}"
        try:
            async with self.session.get(api_url, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
                # If 'data' is not empty, the streamer is live
                return bool(data and data.get("data"))
        except aiohttp.ClientError as e:
            log.error(f"Error checking live status for {twitch_user_id}: {e}", exc_info=True)
            return None
        except Exception as e:
            log.error(f"Unexpected error checking live status for {twitch_user_id}: {e}", exc_info=True)
            return None

    async def _send_go_live_notification(self, guild: discord.Guild, channel: discord.TextChannel, settings: dict):
        """
        Constructs and sends the go-live notification message.
        """
        twitch_username = settings["twitch_username"]
        ping_role_ids = settings["ping_role_ids"]
        notification_message_template = settings["notification_message"]

        # Construct role mentions
        role_mentions = []
        for role_id in ping_role_ids:
            role = guild.get_role(role_id)
            if role:
                role_mentions.append(role.mention)
        role_pings = " ".join(role_mentions)

        stream_url = f"https://twitch.tv/{twitch_username}"

        # Format the message content
        message_content = notification_message_template.format_map(
            {"streamer": twitch_username, "url": stream_url}
        )

        full_message = f"{role_pings} {message_content}".strip()

        try:
            await channel.send(full_message, allowed_mentions=discord.AllowedMentions(roles=True))
            log.info(f"Sent go-live notification to {channel.name} in {guild.name}.")
        except discord.Forbidden:
            log.warning(f"Missing permissions to send message to {channel.name} in {guild.name}.")
        except Exception as e:
            log.error(f"Error sending go-live notification to {channel.name} in {guild.name}: {e}", exc_info=True)

    # --- Schedule Specifics ---

    async def _fetch_twitch_schedule_segments(self, broadcaster_id: str, start_time_utc: datetime.datetime, end_time_utc: datetime.datetime) -> list | None:
        """
        Fetches Twitch schedule segments for a broadcaster within a UTC time range.
        Returns a list of schedule segments, an empty list if no schedule, or None on error.
        """
        headers = await self._get_twitch_request_headers()
        if not headers:
            return None

        api_url = f"https://api.twitch.tv/helix/schedule?broadcaster_id={broadcaster_id}"
        
        try:
            async with self.session.get(api_url, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
                segments = data.get("data", {}).get("segments", [])

                filtered_segments = []
                for seg in segments:
                    segment_start_utc = dateutil.parser.isoparse(seg["start_time"])
                    if segment_start_utc.tzinfo is None:
                        segment_start_utc = segment_start_utc.replace(tzinfo=datetime.timezone.utc)

                    if start_time_utc <= segment_start_utc <= end_time_utc:
                        filtered_segments.append(seg)

                filtered_segments.sort(key=lambda s: dateutil.parser.isoparse(s["start_time"]))
                log.info(f"Fetched {len(filtered_segments)} relevant schedule segments for broadcaster ID {broadcaster_id}.")
                return filtered_segments
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                log.warning(f"No Twitch schedule found for broadcaster ID {broadcaster_id}.")
                return []
            log.error(f"HTTP error fetching schedule for {broadcaster_id}: {e.status} - {e.message}", exc_info=True)
            return None
        except aiohttp.ClientError as e:
            log.error(f"Network error fetching schedule for {broadcaster_id}: {e}", exc_info=True)
            return None
        except Exception as e:
            log.error(f"Unexpected error fetching schedule for {broadcaster_id}: {e}", exc_info=True)
            return None

    async def _fetch_twitch_category_info(self, category_id: str) -> dict | None:
        """
        Fetches information about a Twitch game category (game).
        """
        headers = await self._get_twitch_request_headers()
        if not headers:
            return None

        api_url = f"https://api.twitch.tv/helix/games?id={category_id}"
        try:
            async with self.session.get(api_url, headers=headers) as response:
                response.raise_for_status()
                data = await response.json()
                if data and data.get("data"):
                    log.debug(f"Fetched category info for ID {category_id}: {data['data'][0].get('name')}")
                    return data["data"][0]
                log.warning(f"No category info found for ID: {category_id}. API response: {data}")
                return None
        except aiohttp.ClientError as e:
            log.error(f"HTTP error fetching category info for {category_id}: {e}", exc_info=True)
            return None
        except Exception as e:
            log.error(f"Unexpected error fetching category info for {category_id}: {e}", exc_info=True)
            return None

    # --- Asset Management (Font and Template Image) ---

    async def _download_asset(self, url: str, destination_path: str) -> bool:
        """
        Downloads a file from a given URL and saves it to a specified local path.
        Returns True on success, False on failure.
        """
        try:
            os.makedirs(os.path.dirname(destination_path), exist_ok=True)
            async with self.session.get(url) as response:
                response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
                content = await response.read()
                with open(destination_path, 'wb') as f:
                    f.write(content)
                log.info(f"Successfully downloaded asset from {url} to {destination_path}")
                return True
        except aiohttp.ClientError as e:
            log.error(f"HTTP error downloading asset from {url}: {e}", exc_info=True)
            return False
        except Exception as e:
            log.error(f"Unexpected error downloading asset from {url} to {destination_path}: {e}", exc_info=True)
            return False

    async def _ensure_required_assets_on_load(self):
        """
        Checks for the presence of required font and template image files using DEFAULT URLs.
        Downloads them if they are missing. This runs on cog_load.
        """
        font_url_to_use = DEFAULT_GUILD_SETTINGS["font_url"]
        template_url_to_use = DEFAULT_GUILD_SETTINGS["template_image_url"]

        font_exists = os.path.exists(self.font_file_path)
        template_exists = os.path.exists(self.template_image_path)

        download_tasks = []
        if not font_exists:
            log.info(f"Font file missing at {self.font_file_path}. Attempting download from {font_url_to_use}.")
            download_tasks.append(self._download_asset(font_url_to_use, self.font_file_path))
        if not template_exists:
            log.info(f"Template image missing at {self.template_image_path}. Attempting download from {template_url_to_use}.")
            download_tasks.append(self._download_asset(template_url_to_use, self.template_image_path))

        if download_tasks:
            results = await asyncio.gather(*download_tasks, return_exceptions=True)
            for res in results:
                if isinstance(res, Exception):
                    log.error(f"One or more asset downloads failed during initial load: {res}")
            log.info("Asset download attempts completed during initial load.")
        else:
            log.debug("All required assets are already present on initial load.")

        return os.path.exists(self.font_file_path) and os.path.exists(self.template_image_path)

    async def _ensure_guild_assets(self, guild: discord.Guild):
        """
        Ensures that guild-specific font and template assets are downloaded.
        This should be called before generating an image for a specific guild.
        """
        guild_font_url = await self.config.guild(guild).font_url()
        guild_template_url = await self.config.guild(guild).template_image_url()

        # Attempt to download/update font if URL differs or file doesn't exist
        if guild_font_url and (not os.path.exists(self.font_file_path) or guild_font_url != (await self.config.guild(guild).font_url())):
            if not await self._download_asset(guild_font_url, self.font_file_path):
                log.warning(f"Could not download custom font for guild {guild.name} from {guild_font_url}. Image generation might use old/default.")
        elif not os.path.exists(self.font_file_path): # Fallback to default if no custom URL and default is missing
             if not await self._download_asset(DEFAULT_GUILD_SETTINGS["font_url"], self.font_file_path):
                log.error("Failed to download default font for guild {guild.name}. Image generation will likely fail.")

        # Attempt to download/update template if URL differs or file doesn't exist
        if guild_template_url and (not os.path.exists(self.template_image_path) or guild_template_url != (await self.config.guild(guild).template_image_url())):
            if not await self._download_asset(guild_template_url, self.template_image_path):
                log.warning(f"Could not download custom template for guild {guild.name} from {guild_template_url}. Image generation might use old/default.")
        elif not os.path.exists(self.template_image_path): # Fallback to default if no custom URL and default is missing
             if not await self._download_asset(DEFAULT_GUILD_SETTINGS["template_image_url"], self.template_image_path):
                log.error("Failed to download default template for guild {guild.name}. Image generation will likely fail.")

        return os.path.exists(self.font_file_path) and os.path.exists(self.template_image_path)

    # --- Image Generation Logic ---

    async def _generate_schedule_image(self, schedule_segments: list, guild: discord.Guild, week_start_date_utc: datetime.datetime = None) -> io.BytesIO | None:
        """
        Generates the schedule image from a list of schedule segments using Pillow.
        `week_start_date_utc`: The UTC datetime object representing the start of the week for the image title.
        """
        # Ensure the correct guild-specific assets are available for this image generation
        if not await self._ensure_guild_assets(guild):
            log.error("Cannot generate schedule image: Required assets are not available for this guild.")
            return None

        try:
            img = Image.open(self.template_image_path).convert("RGBA") # Ensure RGBA for potential transparency
            draw = ImageDraw.Draw(img)

            # Get guild's configured timezone
            guild_timezone_str = await self.config.guild(guild).display_timezone()
            try:
                display_tz = pytz.timezone(guild_timezone_str)
            except pytz.UnknownTimeZoneError:
                log.error(f"Unknown timezone configured for guild {guild.id}: {guild_timezone_str}. Defaulting to Europe/London.")
                display_tz = pytz.timezone("Europe/London")

            # Determine the start of the week for the image title
            if week_start_date_utc:
                # Convert the provided UTC start date to the display timezone
                current_week_start = week_start_date_utc.astimezone(display_tz)
            else:
                # Calculate start of the current week (Monday) in the display timezone
                today_in_tz = datetime.datetime.now(display_tz)
                # Weekday() returns 0 for Monday, 6 for Sunday
                days_since_monday = today_in_tz.weekday()
                current_week_start = today_in_tz - timedelta(days=days_since_monday)
                current_week_start = current_week_start.replace(hour=0, minute=0, second=0, microsecond=0)

            # --- Text and Font Definitions (adjust sizes and positions based on your template) ---
            title_font_size = 90
            date_font_size = 40
            event_font_size = 42
            small_event_font_size = 30 # For longer titles/game names

            # Load fonts. Error handling for font loading.
            try:
                title_font = ImageFont.truetype(self.font_file_path, title_font_size)
                date_font = ImageFont.truetype(self.font_file_path, date_font_size)
                event_font = ImageFont.truetype(self.font_file_path, event_font_size)
                small_event_font = ImageFont.truetype(self.font_file_path, small_event_font_size)
            except IOError:
                log.error(f"Could not load font file from {self.font_file_path}. Please ensure it exists and is valid.")
                return None

            # --- Dynamic Image Resizing based on event count ---
            max_events_to_show = await self.config.guild(guild).display_event_count()
            actual_events_rendered = min(len(schedule_segments), max_events_to_show)

            # Assuming your template has a fixed header part and then repeating rows
            # This logic needs to be tailored to your specific `schedule.png` template.
            # Here's a generic example based on typical schedule layouts:
            header_height = 350 # Height of the top part of your template (e.g., "Week of" title)
            single_event_row_height = 150 # Height allocated for each event row
            footer_height = 100 # Height of the bottom part of your template (if any)

            # Calculate the required height for the image
            new_image_height = header_height + (actual_events_rendered * single_event_row_height) + footer_height

            # Create a new image with the calculated height if it's different from the template's original height
            original_width, original_height = img.size
            if new_image_height < original_height: # Crop if fewer events than template allows
                # Create a new blank image with the desired dimensions
                resized_img = Image.new("RGBA", (original_width, new_image_height), (0,0,0,0)) # Transparent background
                # Paste the header part
                resized_img.paste(img.crop((0, 0, original_width, header_height)), (0, 0))
                # Paste the required number of event rows
                for i in range(actual_events_rendered):
                    source_y = header_height + (i * single_event_row_height)
                    dest_y = header_height + (i * single_event_row_height)
                    resized_img.paste(img.crop((0, source_y, original_width, source_y + single_event_row_height)), (0, dest_y))
                # Paste the footer part at the new bottom
                resized_img.paste(img.crop((0, original_height - footer_height, original_width, original_height)), (0, new_image_height - footer_height))
                img = resized_img
                draw = ImageDraw.Draw(img) # Re-initialize draw object for the new image
            elif new_image_height > original_height: # Extend if more events needed (requires more complex template logic or a simple background extension)
                log.warning(f"Calculated image height ({new_image_height}) exceeds original template height ({original_height}). "
                            f"Displaying only {actual_events_rendered} events. Consider a taller template or fewer events.")
                pass
            else: # If height matches, use original image
                pass


            # --- Drawing Title (Week of...) ---
            week_of_text = "Week of"
            date_text_for_title = current_week_start.strftime("%B %d")
            
            # Text color (White)
            text_color = (255, 255, 255)

            # Measure text sizes (Pillow's getsize/getbbox methods)
            try:
                week_of_bbox = title_font.getbbox(week_of_text)
                week_of_width = week_of_bbox[2] - week_of_bbox[0]
                week_of_height = week_of_bbox[3] - week_of_bbox[1]

                date_bbox = date_font.getbbox(date_text_for_title)
                date_width = date_bbox[2] - date_bbox[0]
                date_height = date_bbox[3] - date_bbox[1]
            except AttributeError: # Fallback for older Pillow versions
                week_of_width, week_of_height = title_font.getsize(week_of_text)
                date_width, date_height = date_font.getsize(date_text_for_title)


            # Positioning (adjust these based on your template's layout)
            img_width, _ = img.size
            right_margin = 100
            
            week_of_x = img_width - right_margin - week_of_width
            week_of_y = 100 # Example Y coordinate
            
            date_x = img_width - right_margin - date_width
            date_y = week_of_y + week_of_height + 20 # Below "Week of"

            draw.text((week_of_x, week_of_y), week_of_text, font=title_font, fill=text_color)
            draw.text((date_x, date_y), date_text_for_title, font=date_font, fill=text_color)

            # --- Drawing Schedule Events ---
            # Starting Y position for the first event, and vertical spacing
            event_start_y = header_height + 20 # Start below the header
            event_line_spacing = 5 # Space between date/time and title/game
            
            # X positions for event text
            day_time_x = 125
            title_game_x = 125

            for i, segment in enumerate(schedule_segments):
                if i >= actual_events_rendered:
                    break # Stop if we've drawn enough events

                # Calculate Y position for the current event row
                current_event_y_top = event_start_y + (i * single_event_row_height)

                # Parse and format time for display
                segment_start_utc = dateutil.parser.isoparse(segment["start_time"])
                if segment_start_utc.tzinfo is None:
                    segment_start_utc = segment_start_utc.replace(tzinfo=datetime.timezone.utc)
                
                # Convert to display timezone
                segment_start_display_tz = segment_start_utc.astimezone(display_tz)
                
                day_time_text = segment_start_display_tz.strftime("%A // %I:%M%p").upper()
                stream_title = segment["title"]

                # Fetch game name (category)
                game_name = "No Category"
                category_info = segment.get("category")
                if category_info and category_info.get("id"):
                    game_name = category_info.get("name", "No Category")

                # Text positioning for each event
                draw.text((day_time_x, current_event_y_top), day_time_text, font=event_font, fill=text_color)
                
                # Adjust font size for title if it's too long
                title_bbox = event_font.getbbox(stream_title) if hasattr(event_font, 'getbbox') else event_font.getsize(stream_title)
                title_width = title_bbox[2] - title_bbox[0] if hasattr(event_font, 'getbbox') else title_bbox[0]
                
                # Assuming max width for title, adjust as per your template design
                max_title_width = img_width - title_game_x - right_margin
                
                current_title_font = event_font
                if title_width > max_title_width and small_event_font is not None:
                    current_title_font = small_event_font
                    title_bbox = current_title_font.getbbox(stream_title) if hasattr(current_title_font, 'getbbox') else current_title_font.getsize(stream_title)
                    title_width = title_bbox[2] - title_bbox[0] if hasattr(current_title_font, 'getbbox') else title_bbox[0]

                # If still too long, truncate
                while title_width > max_title_width and len(stream_title) > 3:
                    stream_title = stream_title[:-1] # Remove last character
                    title_bbox = current_title_font.getbbox(stream_title + "...") if hasattr(current_title_font, 'getbbox') else current_title_font.getsize(stream_title + "...")
                    title_width = title_bbox[2] - title_bbox[0] if hasattr(current_title_font, 'getbbox') else title_bbox[0]
                if title_width > max_title_width and len(stream_title) <= 3: # To avoid infinite loop for very short words
                     stream_title = stream_title[:1] + "..."
                elif len(stream_title) > 0 and title_width > max_title_width: # If truncation happened, add ellipses
                    stream_title += "..."


                draw.text((title_game_x, current_event_y_top + event_font_size + event_line_spacing), stream_title, font=current_title_font, fill=text_color)
                
            # Convert the Pillow Image to a BytesIO object (in-memory file)
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            buffer.seek(0) # Rewind to the beginning of the buffer
            log.info("Schedule image generated successfully.")
            return buffer

        except Exception as e:
            log.error(f"Error generating schedule image: {e}", exc_info=True)
            return None

    # --- Discord Posting Logic ---

    async def _post_schedule_to_discord(self, guild: discord.Guild, channel: discord.TextChannel, schedule_segments: list, week_start_date_utc: datetime.datetime = None):
        """
        Posts the generated schedule image to the specified Discord channel.
        Handles warning message, previous message deletion, and pinning.
        """
        try:
            # Send a warning message about updating
            ping_role_id = await self.config.guild(guild).schedule_ping_role_id()
            ping_role = guild.get_role(ping_role_id) if ping_role_id else None
            
            warning_content = "⚠️ Updating schedule... Previous schedule messages will be deleted shortly."
            if ping_role:
                warning_content = f"{ping_role.mention}\n{warning_content}"
            
            try:
                warning_msg = await channel.send(warning_content, allowed_mentions=discord.AllowedMentions(roles=True))
                await asyncio.sleep(5) # Give users a moment to see the warning
            except discord.Forbidden:
                log.warning(f"Missing permissions to send warning message to {channel.name} in {guild.name}.")
                warning_msg = None # Cannot send warning, proceed without it
            except Exception as e:
                log.error(f"Error sending warning message to {channel.name} in {guild.name}: {e}", exc_info=True)
                warning_msg = None


            # Delete previous bot messages in the channel to keep it clean
            log.info(f"Attempting to delete old schedule messages in {channel.name} ({guild.name})...")
            deleted_count = 0
            async for message in channel.history(limit=50): # Look at last 50 messages
                if message.author == self.bot.user and (warning_msg is None or message.id != warning_msg.id):
                    try:
                        await message.delete()
                        deleted_count += 1
                        await asyncio.sleep(0.5) # Add a small delay to avoid rate limits
                    except discord.NotFound:
                        pass # Message already deleted
                    except discord.Forbidden:
                        log.warning(f"Missing permissions to delete messages in {channel.name} in {guild.name}. Stopping deletion.")
                        break # Cannot delete, stop trying
                    except Exception as e:
                        log.error(f"Error deleting message {message.id} in {channel.name}: {e}", exc_info=True)
                        break # Something else went wrong, stop
            log.info(f"Deleted {deleted_count} old messages in {channel.name} ({guild.name}).")

            if warning_msg:
                try:
                    await warning_msg.delete() # Delete the warning message itself
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    log.warning(f"Missing permissions to delete warning message in {channel.name} in {guild.name}.")
                except Exception as e:
                    log.error(f"Error deleting warning message {warning_msg.id}: {e}", exc_info=True)

            # Generate the schedule image
            image_buffer = await self._generate_schedule_image(schedule_segments, guild, week_start_date_utc)
            if not image_buffer:
                await channel.send("❌ Failed to generate schedule image. Please check bot logs.")
                return

            # Post the new schedule image
            try:
                schedule_file = discord.File(image_buffer, filename="twitch_schedule.png")
                posted_message = await channel.send(file=schedule_file)
                log.info(f"Successfully posted new schedule image to {channel.name} in {guild.name}.")
            except discord.Forbidden:
                log.error(f"Missing permissions to send files/messages to {channel.name} in {guild.name}.")
                return
            except Exception as e:
                log.error(f"Error sending schedule image to {channel.name} in {guild.name}: {e}", exc_info=True)
                return

            # Pin the new schedule message (optional, can fail if too many pins)
            try:
                await posted_message.pin()
                await self.config.guild(guild).schedule_message_id.set(posted_message.id)
                log.info(f"Pinned new schedule message {posted_message.id}.")
            except discord.Forbidden:
                log.warning(f"Missing permissions to pin messages in {channel.name} in {guild.name}.")
            except discord.HTTPException as e:
                if e.code == 50019: # 50019 is "Maximum number of pins reached"
                    log.warning(f"Could not pin message in {channel.name}: Maximum number of pins reached.")
                else:
                    log.warning(f"Error pinning schedule message {posted_message.id}: {e}", exc_info=True)
            except Exception as e:
                log.error(f"Unexpected error pinning schedule message {posted_message.id}: {e}", exc_info=True)

        except Exception as e:
            log.critical(f"Critical error in _post_schedule_to_discord for guild {guild.id}: {e}", exc_info=True)
            try:
                # Attempt to notify the channel even if there's a critical error
                await channel.send("An unexpected error occurred while trying to post the schedule. Please check bot logs.")
            except discord.Forbidden:
                pass # Can't do anything if permissions are truly messed up.

    # --- Background Tasks (Combined) ---

    @loop(minutes=2) # Check every 2 minutes for go-live status
    async def check_twitch_stream_status(self):
        """
        Background task to periodically check Twitch stream status for all guilds
        and send go-live notifications.
        """
        await self.bot.wait_until_ready() # Ensure bot is fully connected to Discord
        await self.initialization_complete.wait() # Ensure cog's internal setup is done

        if not self.twitch_client_id or not self.twitch_client_secret:
            log.warning("Twitch credentials not set. Skipping live stream checks.")
            return

        for guild in self.bot.guilds:
            try:
                settings = await self.config.guild(guild).all()
                twitch_user_id = settings["twitch_user_id"]
                notification_channel_id = settings["notification_channel_id"]
                last_stream_status = settings["last_stream_status"]
                twitch_username = settings["twitch_username"]

                if not twitch_user_id or not notification_channel_id:
                    # log.debug(f"Guild {guild.name} ({guild.id}) has incomplete live notification setup. Skipping.")
                    continue

                notification_channel = guild.get_channel(notification_channel_id)
                if not notification_channel:
                    log.warning(f"Live notification channel for guild {guild.name} ({guild.id}) not found or accessible. Skipping.")
                    continue

                is_live = await self._is_streamer_live(twitch_user_id)

                if is_live is None: # Error occurred during API call
                    log.error(f"Failed to get live status for {twitch_username} ({twitch_user_id}) in guild {guild.name}.")
                    continue

                if is_live and last_stream_status == "offline":
                    # Stream just went live! Send notification.
                    log.info(f"Streamer {twitch_username} just went LIVE in guild {guild.name}.")
                    await self._send_go_live_notification(guild, notification_channel, settings)
                    await self.config.guild(guild).last_stream_status.set("online")
                elif not is_live and last_stream_status == "online":
                    # Stream just went offline. Update status.
                    log.info(f"Streamer {twitch_username} just went OFFLINE in guild {guild.name}.")
                    await self.config.guild(guild).last_stream_status.set("offline")
                # else: Stream status unchanged, do nothing.

            except Exception as e:
                log.error(f"Error in check_twitch_stream_status for guild {guild.name} ({guild.id}): {e}", exc_info=True)


    @loop(minutes=5) # Check every 5 minutes to see if an update is due for schedule
    async def auto_schedule_updater(self):
        """
        Background task that checks if it's time to automatically update the schedule
        image for each configured guild.
        """
        await self.bot.wait_until_ready() # Ensure bot is fully connected to Discord
        await self.initialization_complete.wait() # Ensure cog's internal setup is done

        log.debug("Running auto_schedule_updater loop.")

        for guild in self.bot.guilds:
            try:
                guild_settings = await self.config.guild(guild).all()
                schedule_channel_id = guild_settings["schedule_channel_id"]
                twitch_username = guild_settings["twitch_username"]
                auto_update_days = guild_settings["auto_update_days"]
                auto_update_time_str = guild_settings["auto_update_time"]
                last_auto_update_date = guild_settings["last_auto_update_date"]
                display_timezone_str = guild_settings["display_timezone"]

                # Skip if essential settings for schedule are not configured
                if not schedule_channel_id or not twitch_username or not auto_update_days or not auto_update_time_str:
                    # log.debug(f"Guild {guild.name} ({guild.id}) has incomplete schedule auto-update settings. Skipping.")
                    continue

                schedule_channel = guild.get_channel(schedule_channel_id)
                if not schedule_channel:
                    log.warning(f"Configured schedule channel {schedule_channel_id} not found or accessible for guild {guild.name} ({guild.id}). Skipping.")
                    continue
                
                try:
                    display_tz = pytz.timezone(display_timezone_str)
                except pytz.UnknownTimeZoneError:
                    log.error(f"Invalid timezone '{display_timezone_str}' configured for guild {guild.name}. Skipping auto-update.")
                    continue

                now_in_display_tz = datetime.datetime.now(display_tz)
                current_day_of_week = now_in_display_tz.weekday() # Monday is 0, Sunday is 6
                current_time_hhmm = now_in_display_tz.strftime("%H:%M")
                current_date_yyyymmdd = now_in_display_tz.strftime("%Y-%m-%d")

                # Check if it's the correct day and time for an update AND if we haven't updated today yet
                if (current_day_of_week in auto_update_days and
                    current_time_hhmm == auto_update_time_str and
                    last_auto_update_date != current_date_yyyymmdd):

                    log.info(f"Initiating auto-schedule update for guild {guild.name} ({guild.id}).")
                    
                    # Calculate current week's schedule range
                    start_of_current_week_display_tz = now_in_display_tz - timedelta(days=current_day_of_week)
                    start_of_current_week_display_tz = start_of_current_week_display_tz.replace(hour=0, minute=0, second=0, microsecond=0)
                    end_of_current_week_display_tz = start_of_current_week_display_tz + timedelta(days=6, hours=23, minutes=59, seconds=59)

                    broadcaster_id = await self._fetch_twitch_user_id(twitch_username)
                    if not broadcaster_id:
                        log.error(f"Could not resolve Twitch username '{twitch_username}' for guild {guild.name} during auto-update.")
                        continue

                    schedule = await self._fetch_twitch_schedule_segments(
                        broadcaster_id,
                        start_of_current_week_display_tz.astimezone(datetime.timezone.utc),
                        end_of_current_week_display_tz.astimezone(datetime.timezone.utc)
                    )

                    if schedule is not None:
                        await self._post_schedule_to_discord(guild, schedule_channel, schedule, start_of_current_week_display_tz.astimezone(datetime.timezone.utc))
                        await self.config.guild(guild).last_auto_update_date.set(current_date_yyyymmdd) # Mark as updated today
                        log.info(f"Auto-schedule update completed for guild {guild.name}.")
                    else:
                        log.error(f"Failed to fetch schedule during auto-update for guild {guild.name}.")

            except Exception as e:
                log.error(f"Error in auto_schedule_updater for guild {guild.name} ({guild.id}): {e}", exc_info=True)


    # --- Combined Commands ---
    @commands.group(aliases=["tutil"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def twitchutility(self, ctx: commands.Context):
        """
        Manage Twitch live notifications and schedule posting for this server.
        """
        if ctx.subcommand_passed is None:
            await ctx.send_help(ctx.command)

    @twitchutility.command(name="setup")
    async def twitchutility_setup(self, ctx: commands.Context):
        """
        Starts an interactive setup process for TwitchUtility.
        Configures both live notifications and schedule posting.
        """
        await ctx.send("Starting interactive setup for Twitch Utility. Please answer the following questions.")

        def check_author_channel(m):
            return m.author == ctx.author and m.channel == ctx.channel

        # --- Shared Settings: Twitch Username ---
        await ctx.send("What is the exact Twitch username of the streamer whose live status and schedule I should track?")
        try:
            msg = await self.bot.wait_for("message", check=check_author_channel, timeout=60.0)
            twitch_username = msg.content.strip()
            await ctx.send(f"Attempting to verify Twitch username `{twitch_username}`...")
            broadcaster_id = await self._fetch_twitch_user_id(twitch_username)
            if not broadcaster_id:
                return await ctx.send(f"❌ Could not find a Twitch user with username `{twitch_username}`. Please check spelling. Setup cancelled.")
            await self.config.guild(ctx.guild).twitch_username.set(twitch_username)
            await self.config.guild(ctx.guild).twitch_user_id.set(broadcaster_id) # Store ID for live checks
            await ctx.send(f"✅ Twitch username set to `{twitch_username}`.")
        except asyncio.TimeoutError:
            return await ctx.send("⌛ Setup timed out. Please run `[p]tutil setup` again.")

        # --- Live Notification Settings ---
        await ctx.send("--- Live Notification Setup ---")
        await ctx.send("Which Discord channel should I send go-live notifications to? (Mention the channel, e.g., `#live-alerts`)")
        try:
            msg = await self.bot.wait_for("message", check=check_author_channel, timeout=60.0)
            if not msg.channel_mentions:
                return await ctx.send("❌ No channel mentioned. Live notification setup cancelled.")
            notification_channel = msg.channel_mentions[0]
            await self.config.guild(ctx.guild).notification_channel_id.set(notification_channel.id)
            await ctx.send(f"✅ Live notification channel set to {notification_channel.mention}.")
        except asyncio.TimeoutError:
            await ctx.send("⌛ Live notification channel setup timed out. Moving to next step.")

        await ctx.send("Please provide the default go-live notification message. "
                       "Use `{streamer}` for the Twitch username and `{url}` for the stream URL. "
                       "Example: `{streamer} is LIVE! Watch now at {url}` (Default provided if skipped)")
        try:
            msg = await self.bot.wait_for("message", check=check_author_channel, timeout=90.0)
            message_text = msg.content.strip()
            if message_text:
                if len(message_text) > 1500:
                    await ctx.send("⚠️ Message too long (max 1500 chars). Using default message.")
                else:
                    await self.config.guild(ctx.guild).notification_message.set(message_text)
                    await ctx.send("✅ Notification message set.")
            else:
                await ctx.send("No custom message provided. Using default.")
        except asyncio.TimeoutError:
            await ctx.send("⌛ Message setup timed out. Using default message.")
        
        await ctx.send("Would you like to add any roles to ping when you go live? "
                       "Mention them now, separated by spaces (e.g., `@LiveRole1 @LiveRole2`). Type `none` to skip.")
        try:
            msg = await self.bot.wait_for("message", check=check_author_channel, timeout=60.0)
            if msg.content.strip().lower() != "none":
                ping_role_ids = []
                for role_mention in msg.role_mentions:
                    ping_role_ids.append(role_mention.id)
                await self.config.guild(ctx.guild).ping_role_ids.set(ping_role_ids)
                if ping_role_ids:
                    await ctx.send(f"✅ Roles set for live pings: {', '.join(r.name for r in msg.role_mentions)}.")
                else:
                    await ctx.send("No valid roles mentioned. Live pings skipped.")
            else:
                await ctx.send("No roles set for live pings.")
        except asyncio.TimeoutError:
            await ctx.send("⌛ Role setup timed out. No roles set for live pings.")

        # --- Schedule Posting Settings ---
        await ctx.send("\n--- Schedule Posting Setup ---")
        await ctx.send("Which Discord channel should I post the schedule image in? (Mention the channel, e.g., `#schedule-board`)")
        try:
            msg = await self.bot.wait_for("message", check=check_author_channel, timeout=60.0)
            if not msg.channel_mentions:
                return await ctx.send("❌ No channel mentioned. Schedule setup cancelled.")
            schedule_channel = msg.channel_mentions[0]
            await self.config.guild(ctx.guild).schedule_channel_id.set(schedule_channel.id)
            await ctx.send(f"✅ Schedule channel set to {schedule_channel.mention}.")
        except asyncio.TimeoutError:
            return await ctx.send("⌛ Schedule channel setup timed out. Please run `[p]tutil setup` again.")

        await ctx.send("On which days of the week should the schedule automatically update? "
                       "Enter numbers separated by spaces (0=Monday, 6=Sunday). E.g., `6` for Sunday, `0 6` for Monday and Sunday.")
        try:
            msg = await self.bot.wait_for("message", check=check_author_channel, timeout=60.0)
            try:
                days_input = [int(d.strip()) for d in msg.content.split()]
                if not all(0 <= d <= 6 for d in days_input):
                    return await ctx.send("❌ Invalid day numbers. Please use numbers between 0 and 6. Setup cancelled.")
                await self.config.guild(ctx.guild).auto_update_days.set(sorted(list(set(days_input))))
                day_names = [["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][d] for d in sorted(list(set(days_input)))]
                await ctx.send(f"✅ Schedule will update on: {', '.join(day_names)}.")
            except ValueError:
                return await ctx.send("❌ Invalid input for days. Please enter numbers. Setup cancelled.")
        except asyncio.TimeoutError:
            return await ctx.send("⌛ Setup timed out. Please run `[p]tutil setup` again.")

        await ctx.send("At what time should the schedule update on those days? (Use 24-hour format, e.g., `09:00` or `23:30`)")
        try:
            msg = await self.bot.wait_for("message", check=check_author_channel, timeout=60.0)
            update_time = msg.content.strip()
            if not re.fullmatch(r"^(?:2[0-3]|[01]?[0-9]):[0-5][0-9]$", update_time):
                return await ctx.send("❌ Invalid time format. Please use HH:MM (24-hour). Setup cancelled.")
            await self.config.guild(ctx.guild).auto_update_time.set(update_time)
            await ctx.send(f"✅ Schedule will update daily at `{update_time}`.")
        except asyncio.TimeoutError:
            return await ctx.send("⌛ Setup timed out. Please run `[p]tutil setup` again.")
        
        await ctx.send("What timezone should be used for displaying times on the schedule? "
                       "(e.g., `America/New_York`, `Europe/London`, `Asia/Tokyo`). "
                       "You can find a list here: <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones>")
        try:
            msg = await self.bot.wait_for("message", check=check_author_channel, timeout=90.0)
            tz_input = msg.content.strip()
            try:
                pytz.timezone(tz_input)
                await self.config.guild(ctx.guild).display_timezone.set(tz_input)
                await ctx.send(f"✅ Display timezone set to `{tz_input}`.")
            except pytz.UnknownTimeZoneError:
                await ctx.send("❌ That is not a valid timezone. Please try again with a valid TZ database name. Setup cancelled.")
                return
        except asyncio.TimeoutError:
            return await ctx.send("⌛ Setup timed out. Please run `[p]tutil setup` again.")

        await ctx.send("Please provide the direct URL to your custom font file (e.g., a .ttf file). "
                       "This will be downloaded and used for schedule image generation. Default will be used if left blank or download fails.")
        try:
            msg = await self.bot.wait_for("message", check=check_author_channel, timeout=120.0)
            font_url = msg.content.strip()
            if font_url and (font_url.startswith("http://") or font_url.startswith("https://")):
                await self.config.guild(ctx.guild).font_url.set(font_url)
                await ctx.send(f"🔄 Attempting to download font from `{font_url}`...")
                if not await self._download_asset(font_url, self.font_file_path):
                    await ctx.send("❌ Failed to download font from the provided URL. Using default font.")
            else:
                await ctx.send("No valid font URL provided. Using default font.")
        except asyncio.TimeoutError:
            await ctx.send("⌛ Font URL setup timed out. Using default font.")

        await ctx.send("Please provide the direct URL to your custom schedule template image (e.g., a .png file). "
                       "This will be downloaded and used for schedule image generation. Default will be used if left blank or download fails.")
        try:
            msg = await self.bot.wait_for("message", check=check_author_channel, timeout=120.0)
            template_url = msg.content.strip()
            if template_url and (template_url.startswith("http://") or template_url.startswith("https://")):
                await self.config.guild(ctx.guild).template_image_url.set(template_url)
                await ctx.send(f"🔄 Attempting to download template image from `{template_url}`...")
                if not await self._download_asset(template_url, self.template_image_path):
                    await ctx.send("❌ Failed to download template image from the provided URL. Using default template.")
            else:
                await ctx.send("No valid template URL provided. Using default template.")
        except asyncio.TimeoutError:
            await ctx.send("⌛ Template URL setup timed out. Using default template.")

        await ctx.send("🎉 Twitch Utility setup is complete!")
        await self.show_settings(ctx) # Show current settings

    # --- Live Notification Commands (previously streamset) ---

    @twitchutility.group(name="livenotify")
    async def live_notify_group(self, ctx: commands.Context):
        """Manage Twitch live notification settings."""
        if ctx.subcommand_passed is None:
            await ctx.send_help(ctx.command)

    @live_notify_group.command(name="setchannel")
    async def set_notification_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Sets the Discord channel where go-live notifications will be sent.
        """
        await self.config.guild(ctx.guild).notification_channel_id.set(channel.id)
        await ctx.send(f"✅ Live notification channel set to {channel.mention}.")

    @live_notify_group.command(name="addrole")
    async def add_live_ping_role(self, ctx: commands.Context, role: discord.Role):
        """
        Adds a role to be pinged when a go-live notification is sent.
        """
        async with self.config.guild(ctx.guild).ping_role_ids() as ping_role_ids:
            if role.id not in ping_role_ids:
                ping_role_ids.append(role.id)
                await ctx.send(f"✅ Added role `{role.name}` to the live ping list.")
            else:
                await ctx.send(f"ℹ️ Role `{role.name}` is already in the live ping list.")

    @live_notify_group.command(name="removerole")
    async def remove_live_ping_role(self, ctx: commands.Context, role: discord.Role):
        """
        Removes a role from the ping list for go-live notifications.
        """
        async with self.config.guild(ctx.guild).ping_role_ids() as ping_role_ids:
            if role.id in ping_role_ids:
                ping_role_ids.remove(role.id)
                await ctx.send(f"✅ Removed role `{role.name}` from the live ping list.")
            else:
                await ctx.send(f"ℹ️ Role `{role.name}` was not found in the live ping list.")

    @live_notify_group.command(name="setmessage")
    async def set_live_notification_message(self, ctx: commands.Context, *, message_text: str):
        """
        Sets the custom go-live notification message.
        Use `{streamer}` for the Twitch username and `{url}` for the stream URL.
        """
        if len(message_text) > 1500:
            return await ctx.send("❌ Your message is too long. Please keep it under 1500 characters.")
        await self.config.guild(ctx.guild).notification_message.set(message_text)
        await ctx.send(f"✅ Live notification message set to:\n```\n{message_text}\n```")

    # --- Schedule Commands (previously streamscheduler) ---

    @twitchutility.group(name="schedule")
    async def schedule_group(self, ctx: commands.Context):
        """Manage Twitch schedule posting settings and actions."""
        if ctx.subcommand_passed is None:
            await ctx.send_help(ctx.command)

    @schedule_group.command(name="setchannel")
    async def set_schedule_post_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """
        Sets the Discord text channel for schedule posts.
        """
        await self.config.guild(ctx.guild).schedule_channel_id.set(channel.id)
        await ctx.send(f"✅ Schedule posting channel set to {channel.mention}.")

    @schedule_group.command(name="setautoupdatedays")
    async def set_auto_update_days(self, ctx: commands.Context, *days: int):
        """
        Sets the days of the week for automatic schedule updates.
        Enter numbers separated by spaces (0=Monday, 6=Sunday).
        Example: `[p]tutil schedule setautoupdatedays 0 6` (for Monday and Sunday)
        """
        if not days:
            await self.config.guild(ctx.guild).auto_update_days.set([])
            return await ctx.send("✅ Automatic schedule update days cleared. Schedule will no longer auto-update on a specific day.")
        
        valid_days = []
        for day in days:
            if 0 <= day <= 6:
                valid_days.append(day)
            else:
                return await ctx.send(f"❌ Invalid day `{day}`. Days must be numbers between 0 (Monday) and 6 (Sunday).")
        
        unique_sorted_days = sorted(list(set(valid_days)))
        await self.config.guild(ctx.guild).auto_update_days.set(unique_sorted_days)
        day_names = [["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][d] for d in unique_sorted_days]
        await ctx.send(f"✅ Automatic schedule update days set to: {', '.join(day_names)}.")
        await self.config.guild(ctx.guild).last_auto_update_date.set(None)

    @schedule_group.command(name="setautoupdatetime")
    async def set_auto_update_time(self, ctx: commands.Context, time_str: str):
        """
        Sets the time of day for automatic schedule updates (24-hour format).
        """
        if not re.fullmatch(r"^(?:2[0-3]|[01]?[0-9]):[0-5][0-9]$", time_str):
            return await ctx.send("❌ Invalid time format. Please use HH:MM (24-hour format), e.g., `14:00`.")
        
        await self.config.guild(ctx.guild).auto_update_time.set(time_str)
        await ctx.send(f"✅ Automatic schedule update time set to `{time_str}`.")
        await self.config.guild(ctx.guild).last_auto_update_date.set(None)

    @schedule_group.command(name="setnotifyrole")
    async def set_schedule_notify_role(self, ctx: commands.Context, role: discord.Role = None):
        """
        Sets the role to ping when a new schedule is posted.
        Leave `role` blank to clear the role.
        """
        if role is None:
            await self.config.guild(ctx.guild).schedule_ping_role_id.set(None)
            await ctx.send("✅ Schedule notification role cleared.")
        else:
            await self.config.guild(ctx.guild).schedule_ping_role_id.set(role.id)
            await ctx.send(f"✅ Schedule notification role set to {role.mention}.")

    @schedule_group.command(name="seteventcount")
    async def set_display_event_count(self, ctx: commands.Context, count: int):
        """
        Sets the number of upcoming events to display on the schedule image (1-10).
        """
        if not 1 <= count <= 10:
            return await ctx.send("❌ Display event count must be between 1 and 10.")
        
        await self.config.guild(ctx.guild).display_event_count.set(count)
        await ctx.send(f"✅ Display event count set to `{count}`.")

    @schedule_group.command(name="settimezone")
    async def set_display_timezone(self, ctx: commands.Context, *, timezone_name: str):
        """
        Sets the timezone for displaying times on the schedule image and list.
        Find valid names here: <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones>
        """
        try:
            pytz.timezone(timezone_name)
            await self.config.guild(ctx.guild).display_timezone.set(timezone_name)
            await ctx.send(f"✅ Display timezone set to `{timezone_name}`.")
        except pytz.UnknownTimeZoneError:
            await ctx.send("❌ That is not a valid timezone. Please use a valid TZ database name.")

    @schedule_group.command(name="setfonturl")
    async def set_font_url(self, ctx: commands.Context, font_url: str):
        """
        Sets the URL for the custom font file (.ttf).
        The bot will attempt to download this font for image generation.
        """
        if not (font_url.startswith("http://") or font_url.startswith("https://")):
            return await ctx.send("❌ Invalid URL. Please provide a direct HTTP/HTTPS link to the font file.")
        
        await self.config.guild(ctx.guild).font_url.set(font_url)
        await ctx.send(f"🔄 Font URL set to `{font_url}`. Attempting to download now...")
        
        if await self._download_asset(font_url, self.font_file_path):
            await ctx.send("✅ Font downloaded successfully!")
        else:
            await ctx.send("❌ Failed to download font from the provided URL. Please check the URL and bot logs.")

    @schedule_group.command(name="settemplateurl")
    async def set_template_image_url(self, ctx: commands.Context, template_url: str):
        """
        Sets the URL for the custom schedule template image (.png).
        The bot will attempt to download this image for schedule generation.
        """
        if not (template_url.startswith("http://") or template_url.startswith("https://")):
            return await ctx.send("❌ Invalid URL. Please provide a direct HTTP/HTTPS link to the image file.")
        
        await self.config.guild(ctx.guild).template_image_url.set(template_url)
        await ctx.send(f"🔄 Template image URL set to `{template_url}`. Attempting to download now...")
        
        if await self._download_asset(template_url, self.template_image_path):
            await ctx.send("✅ Template image downloaded successfully!")
        else:
            await ctx.send("❌ Failed to download template image from the provided URL. Please check the URL and bot logs.")

    @schedule_group.command(name="postnow")
    async def post_schedule_now(self, ctx: commands.Context):
        """
        Forces an immediate post of the current week's schedule to the configured channel.
        """
        await self.initialization_complete.wait()

        guild_settings = await self.config.guild(ctx.guild).all()
        twitch_username = guild_settings["twitch_username"]
        schedule_channel_id = guild_settings["schedule_channel_id"]
        display_tz_str = guild_settings["display_timezone"]

        if not twitch_username or not schedule_channel_id:
            return await ctx.send("❌ Please complete the setup first using `[p]tutil setup` or set essential schedule settings.")

        target_channel = ctx.guild.get_channel(schedule_channel_id)
        if not target_channel:
            return await ctx.send("❌ The configured schedule channel was not found or is inaccessible.")

        await ctx.send("🔄 Fetching and posting current week's schedule...")
        
        try:
            display_tz = pytz.timezone(display_tz_str)
            now_in_tz = datetime.datetime.now(display_tz)
            start_of_week_tz = now_in_tz - timedelta(days=now_in_tz.weekday())
            start_of_week_tz = start_of_week_tz.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_week_tz = start_of_week_tz + timedelta(days=6, hours=23, minutes=59, seconds=59)

            broadcaster_id = await self._fetch_twitch_user_id(twitch_username)
            if not broadcaster_id:
                return await ctx.send(f"❌ Failed to resolve Twitch username `{twitch_username}`.")

            schedule = await self._fetch_twitch_schedule_segments(
                broadcaster_id,
                start_of_week_tz.astimezone(datetime.timezone.utc),
                end_of_week_tz.astimezone(datetime.timezone.utc)
            )

            if schedule is not None:
                await self._post_schedule_to_discord(ctx.guild, target_channel, schedule, start_of_week_tz.astimezone(datetime.timezone.utc))
                await ctx.send("✅ Schedule posted successfully!")
            else:
                await ctx.send("❌ Failed to fetch schedule from Twitch. Please check bot logs.")
        except Exception as e:
            log.error(f"Error forcing schedule post: {e}", exc_info=True)
            await ctx.send("❌ An unexpected error occurred while forcing the schedule update.")

    @schedule_group.command(name="testpost")
    async def test_post_to_channel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """
        Tests posting the current week's schedule to a specified channel (or current).
        This command does NOT update settings or affect auto-posting.
        """
        await self.initialization_complete.wait()

        target_channel = channel or ctx.channel
        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        display_tz_str = await self.config.guild(ctx.guild).display_timezone()

        if not twitch_username:
            return await ctx.send("❌ Please set a Twitch username first using `[p]tutil setusername`.")

        await ctx.send(f"🔄 Testing schedule post to {target_channel.mention}...")
        
        try:
            display_tz = pytz.timezone(display_tz_str)
            now_in_tz = datetime.datetime.now(display_tz)
            start_of_week_tz = now_in_tz - timedelta(days=now_in_tz.weekday())
            start_of_week_tz = start_of_week_tz.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_week_tz = start_of_week_tz + timedelta(days=6, hours=23, minutes=59, seconds=59)

            broadcaster_id = await self._fetch_twitch_user_id(twitch_username)
            if not broadcaster_id:
                return await ctx.send(f"❌ Failed to resolve Twitch username `{twitch_username}`.")

            schedule = await self._fetch_twitch_schedule_segments(
                broadcaster_id,
                start_of_week_tz.astimezone(datetime.timezone.utc),
                end_of_week_tz.astimezone(datetime.timezone.utc)
            )

            if schedule is not None:
                await self._post_schedule_to_discord_test_mode(target_channel, schedule, start_of_week_tz.astimezone(datetime.timezone.utc))
                await ctx.send(f"✅ Test post complete to {target_channel.mention}!")
            else:
                await ctx.send("❌ Failed to fetch schedule from Twitch for test post.")
        except Exception as e:
            log.error(f"Error during test post to {target_channel.name}: {e}", exc_info=True)
            await ctx.send("❌ An unexpected error occurred during the test post.")

    async def _post_schedule_to_discord_test_mode(self, channel: discord.TextChannel, schedule_segments: list, week_start_date_utc: datetime.datetime):
        """
        Helper for testpost command - posts image without affecting stored message IDs/pins.
        """
        try:
            image_buffer = await self._generate_schedule_image(schedule_segments, channel.guild, week_start_date_utc)
            if not image_buffer:
                await channel.send("❌ Failed to generate schedule image for test post.")
                return

            schedule_file = discord.File(image_buffer, filename="test_schedule.png")
            await channel.send("This is a test post:", file=schedule_file)
            log.info(f"Successfully sent test schedule image to {channel.name} in {channel.guild.name}.")
        except discord.Forbidden:
            log.error(f"Missing permissions to send files/messages to {channel.name} during test post.")
        except Exception as e:
            log.error(f"Error sending test schedule image to {channel.name}: {e}", exc_info=True)

    @schedule_group.command(name="reloadassets")
    async def reload_assets(self, ctx: commands.Context):
        """
        Forces a redownload of the font and schedule template image files.
        Uses the URLs configured with `setfonturl` and `settemplateurl`, or defaults.
        """
        await ctx.send("🔄 Forcing redownload of schedule assets (font and template image)...")
        
        # Remove existing files to ensure fresh download
        if os.path.exists(self.font_file_path):
            os.remove(self.font_file_path)
            log.debug(f"Removed old font file: {self.font_file_path}")
        if os.path.exists(self.template_image_path):
            os.remove(self.template_image_path)
            log.debug(f"Removed old template image: {self.template_image_path}")

        # Attempt to re-download using the *guild's configured URLs*
        guild_font_url = await self.config.guild(ctx.guild).font_url()
        guild_template_url = await self.config.guild(ctx.guild).template_image_url()

        font_download_success = await self._download_asset(guild_font_url, self.font_file_path)
        template_download_success = await self._download_asset(guild_template_url, self.template_image_path)
        
        if font_download_success and template_download_success:
            await ctx.send("✅ Successfully reloaded assets!")
        else:
            await ctx.send("❌ Failed to reload some assets. Check bot logs for details and ensure the URLs are correct.")

    @schedule_group.command(name="currentweek")
    async def show_current_week_schedule(self, ctx: commands.Context):
        """
        Displays the current week's schedule as a text list.
        """
        await self.initialization_complete.wait()

        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        display_tz_str = await self.config.guild(ctx.guild).display_timezone()

        if not twitch_username:
            return await ctx.send("❌ Please set a Twitch username first using `[p]tutil setusername`.")

        try:
            display_tz = pytz.timezone(display_tz_str)
            now_in_tz = datetime.datetime.now(display_tz)
            
            start_of_week_tz = now_in_tz - timedelta(days=now_in_tz.weekday())
            start_of_week_tz = start_of_week_tz.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_week_tz = start_of_week_tz + timedelta(days=6, hours=23, minutes=59, seconds=59)

            broadcaster_id = await self._fetch_twitch_user_id(twitch_username)
            if not broadcaster_id:
                return await ctx.send(f"❌ Failed to resolve Twitch username `{twitch_username}`.")

            schedule = await self._fetch_twitch_schedule_segments(
                broadcaster_id,
                start_of_week_tz.astimezone(datetime.timezone.utc),
                end_of_week_tz.astimezone(datetime.timezone.utc)
            )

            if schedule is None:
                return await ctx.send("❌ Failed to fetch schedule from Twitch.")
            elif not schedule:
                return await ctx.send("📅 No streams scheduled for the current week.")
            
            description = f"**{twitch_username}'s Schedule - Week of {start_of_week_tz.strftime('%B %d')} ({display_tz.tzname(start_of_week_tz)})**\n\n"
            for segment in schedule:
                start_time_utc = dateutil.parser.isoparse(segment["start_time"])
                start_time_display = start_time_utc.astimezone(display_tz)
                
                title = segment["title"]
                game_name = segment.get("category", {}).get("name", "No Category")

                description += (f"**{start_time_display.strftime('%A, %b %d at %I:%M %p')}**: "
                                f"{title} (Game: {game_name})\n")
            
            embed = discord.Embed(
                title=f"{twitch_username}'s Current Week Schedule",
                description=description,
                color=discord.Color.blue()
            )
            await ctx.send(embed=embed)

        except Exception as e:
            log.error(f"Error displaying current week schedule: {e}", exc_info=True)
            await ctx.send("❌ An unexpected error occurred while fetching the schedule.")


    @schedule_group.command(name="nextweek")
    async def show_next_week_schedule(self, ctx: commands.Context):
        """
        Displays the next week's schedule as a text list.
        """
        await self.initialization_complete.wait()

        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        display_tz_str = await self.config.guild(ctx.guild).display_timezone()

        if not twitch_username:
            return await ctx.send("❌ Please set a Twitch username first using `[p]tutil setusername`.")

        try:
            display_tz = pytz.timezone(display_tz_str)
            now_in_tz = datetime.datetime.now(display_tz)
            
            days_until_monday = (7 - now_in_tz.weekday()) % 7
            if days_until_monday == 0:
                next_monday_tz = now_in_tz + timedelta(days=7)
            else:
                next_monday_tz = now_in_tz + timedelta(days=days_until_monday)
            
            next_monday_tz = next_monday_tz.replace(hour=0, minute=0, second=0, microsecond=0)
            next_sunday_tz = next_monday_tz + timedelta(days=6, hours=23, minutes=59, seconds=59)

            broadcaster_id = await self._fetch_twitch_user_id(twitch_username)
            if not broadcaster_id:
                return await ctx.send(f"❌ Failed to resolve Twitch username `{twitch_username}`.")

            schedule = await self._fetch_twitch_schedule_segments(
                broadcaster_id,
                next_monday_tz.astimezone(datetime.timezone.utc),
                next_sunday_tz.astimezone(datetime.timezone.utc)
            )

            if schedule is None:
                return await ctx.send("❌ Failed to fetch schedule from Twitch.")
            elif not schedule:
                return await ctx.send("📅 No streams scheduled for next week.")
            
            description = f"**{twitch_username}'s Schedule - Week of {next_monday_tz.strftime('%B %d')} ({display_tz.tzname(next_monday_tz)})**\n\n"
            for segment in schedule:
                start_time_utc = dateutil.parser.isoparse(segment["start_time"])
                start_time_display = start_time_utc.astimezone(display_tz)
                
                title = segment["title"]
                game_name = segment.get("category", {}).get("name", "No Category")

                description += (f"**{start_time_display.strftime('%A, %b %d at %I:%M %p')}**: "
                                f"{title} (Game: {game_name})\n")
            
            embed = discord.Embed(
                title=f"{twitch_username}'s Next Week Schedule",
                description=description,
                color=discord.Color.blue()
            )
            await ctx.send(embed=embed)

        except Exception as e:
            log.error(f"Error displaying next week schedule: {e}", exc_info=True)
            await ctx.send("❌ An unexpected error occurred while fetching the schedule.")

    @schedule_group.command(name="weekimage")
    async def show_specific_week_image(self, ctx: commands.Context, date_str: str):
        """
        Posts the schedule image for a specific week based on a date.
        Format: DD/MM/YYYY or DD/MM (current year assumed).
        """
        await self.initialization_complete.wait()

        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        if not twitch_username:
            return await ctx.send("❌ Please set a Twitch username first using `[p]tutil setusername`.")

        display_tz_str = await self.config.guild(ctx.guild).display_timezone()
        try:
            display_tz = pytz.timezone(display_tz_str)
        except pytz.UnknownTimeZoneError:
            return await ctx.send(f"❌ Invalid display timezone configured: `{display_tz_str}`. Please set a valid one with `[p]tutil schedule settimezone`.")

        try:
            # Parse the date string
            if len(date_str.split('/')) == 2: # DD/MM format
                current_year = datetime.datetime.now(display_tz).year
                date_obj = datetime.datetime.strptime(f"{date_str}/{current_year}", "%d/%m/%Y").replace(tzinfo=display_tz)
            elif len(date_str.split('/')) == 3: # DD/MM/YYYY format
                date_obj = datetime.datetime.strptime(date_str, "%d/%m/%Y").replace(tzinfo=display_tz)
            else:
                return await ctx.send("❌ Invalid date format. Please use DD/MM or DD/MM/YYYY.")

            # Calculate the start and end of the week for the given date in the display timezone
            start_of_week_tz = date_obj - timedelta(days=date_obj.weekday())
            start_of_week_tz = start_of_week_tz.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_week_tz = start_of_week_tz + timedelta(days=6, hours=23, minutes=59, seconds=59)

            await ctx.send(f"🔄 Generating schedule image for week of {start_of_week_tz.strftime('%B %d, %Y')}...")

            broadcaster_id = await self._fetch_twitch_user_id(twitch_username)
            if not broadcaster_id:
                return await ctx.send(f"❌ Failed to resolve Twitch username `{twitch_username}`.")

            schedule = await self._fetch_twitch_schedule_segments(
                broadcaster_id,
                start_of_week_tz.astimezone(datetime.timezone.utc), # Convert to UTC for API call
                end_of_week_tz.astimezone(datetime.timezone.utc)
            )

            if schedule is None:
                return await ctx.send("❌ Failed to fetch schedule from Twitch for the specified week.")
            
            image_buffer = await self._generate_schedule_image(schedule, ctx.guild, start_of_week_tz.astimezone(datetime.timezone.utc))
            if image_buffer:
                schedule_file = discord.File(image_buffer, filename=f"schedule_week_of_{start_of_week_tz.strftime('%Y%m%d')}.png")
                await ctx.send(f"Here is the schedule for the week of {start_of_week_tz.strftime('%B %d, %Y')}:", file=schedule_file)
            else:
                await ctx.send("❌ Failed to generate the schedule image.")

        except ValueError:
            await ctx.send("❌ Invalid date format. Please use DD/MM or DD/MM/YYYY.")
        except Exception as e:
            log.error(f"Error showing specific week schedule image: {e}", exc_info=True)
            await ctx.send("❌ An unexpected error occurred while processing your request.")

    @schedule_group.command(name="liststreams")
    async def list_upcoming_streams(self, ctx: commands.Context):
        """
        Lists all upcoming streams in text format (up to the display limit).
        """
        await self.initialization_complete.wait()

        twitch_username = await self.config.guild(ctx.guild).twitch_username()
        display_tz_str = await self.config.guild(ctx.guild).display_timezone()
        display_count = await self.config.guild(ctx.guild).display_event_count()

        if not twitch_username:
            return await ctx.send("❌ Please set a Twitch username first using `[p]tutil setusername`.")

        try:
            display_tz = pytz.timezone(display_tz_str)
            now_in_tz = datetime.datetime.now(display_tz)
            
            end_time_for_fetch = now_in_tz + timedelta(days=60) # Fetch up to 60 days out
            
            broadcaster_id = await self._fetch_twitch_user_id(twitch_username)
            if not broadcaster_id:
                return await ctx.send(f"❌ Failed to resolve Twitch username `{twitch_username}`.")

            schedule_raw = await self._fetch_twitch_schedule_segments(
                broadcaster_id,
                now_in_tz.astimezone(datetime.timezone.utc),
                end_time_for_fetch.astimezone(datetime.timezone.utc)
            )

            if schedule_raw is None:
                return await ctx.send("❌ Failed to fetch schedule from Twitch.")
            
            upcoming_schedule = []
            for seg in schedule_raw:
                start_time_utc = dateutil.parser.isoparse(seg["start_time"])
                if start_time_utc.tzinfo is None:
                    start_time_utc = start_time_utc.replace(tzinfo=datetime.timezone.utc)
                if start_time_utc > datetime.datetime.now(datetime.timezone.utc): # Only include events strictly in the future
                    upcoming_schedule.append(seg)

            if not upcoming_schedule:
                return await ctx.send("📅 No upcoming streams found in the schedule.")
            
            display_list = upcoming_schedule[:display_count]

            description = f"**Upcoming Streams for {twitch_username} (Timezone: {display_tz.tzname(now_in_tz)})**\n\n"
            for segment in display_list:
                start_time_utc = dateutil.parser.isoparse(segment["start_time"])
                start_time_display = start_time_utc.astimezone(display_tz)
                
                title = segment["title"]
                game_name = segment.get("category", {}).get("name", "No Category")

                description += (f"**{start_time_display.strftime('%a, %b %d %I:%M %p')}**: "
                                f"{title} (Game: {game_name})\n")
            
            embed = discord.Embed(
                title=f"{twitch_username}'s Upcoming Schedule",
                description=description,
                color=discord.Color.blue()
            )
            await ctx.send(embed=embed)

        except Exception as e:
            log.error(f"Error listing upcoming streams: {e}", exc_info=True)
            await ctx.send("❌ An unexpected error occurred while fetching the schedule.")


def setup(bot):
    """
    Adds the TwitchUtility cog to the bot.
    """
    bot.add_cog(TwitchUtility(bot))
