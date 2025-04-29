from .base import StoreAPI, GameData
from .epic import EpicGamesAPI
from .steam import SteamAPI
from .gog import GOGApi
from .humble import HumbleBundleAPI
from .itch import ItchioAPI
from .origin import OriginAPI
from .ubisoft import UbisoftAPI

__all__ = [
    'StoreAPI',
    'GameData',
    'EpicGamesAPI',
    'SteamAPI',
    'GOGApi',
    'HumbleBundleAPI',
    'ItchioAPI',
    'OriginAPI',
    'UbisoftAPI'
]
