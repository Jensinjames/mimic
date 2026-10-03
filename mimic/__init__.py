"""mimic — intercept an app, then call it from Python like a library."""
from .session import App, Session

__all__ = ["Session", "App"]
__version__ = "0.2.0"
