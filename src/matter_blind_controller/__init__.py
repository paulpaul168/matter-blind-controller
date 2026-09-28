"""Matter blind controller — contact-sensor monitoring foundation."""

__version__ = "0.1.0"

from matter_blind_controller.api import BlindSensors
from matter_blind_controller.config import AppConfig, load_config
from matter_blind_controller.sensors import SensorRegistry

__all__ = [
    "AppConfig",
    "BlindSensors",
    "SensorRegistry",
    "load_config",
    "__version__",
]
