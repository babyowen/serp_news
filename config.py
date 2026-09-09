"""Compatibility facade for the external versioned configuration store.

Initialize explicitly with config_cli.py before starting application processes.
This module never writes source files or silently loads repository defaults.
"""
import os
from runtime_config import load_environment, value

load_environment()
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
API_KEY = SERPAPI_KEY


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    return value(name)


def get_emoji(emoji_char):
    if value("USE_EMOJI_OUTPUT"):
        return emoji_char
    return value("EMOJI_MAP").get(emoji_char, emoji_char)
