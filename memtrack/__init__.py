import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent))
from memtrack import setup

__all__ = ["setup"]
