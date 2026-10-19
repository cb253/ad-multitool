"""Persists non-secret connection settings between launches. Never stores passwords."""

import json
import logging
import os

from app_data import get_app_data_dir

SETTINGS_PATH = os.path.join(get_app_data_dir(), "settings.json")

DEFAULTS = {
    "server": "",
    "base_dn": "",
    "domain": "",
    "username": "",
}

_logger = logging.getLogger(__name__)


def load_settings():
    if not os.path.exists(SETTINGS_PATH):
        return dict(DEFAULTS)
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        _logger.warning("Ignoring unreadable settings file %s", SETTINGS_PATH, exc_info=True)
        return dict(DEFAULTS)
    merged = dict(DEFAULTS)
    # Only known keys are kept, so settings from older versions (e.g. use_ssl) are dropped.
    merged.update({key: data[key] for key in DEFAULTS if key in data})
    return merged


def save_settings(settings):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump({key: settings[key] for key in DEFAULTS}, f, indent=2)
    except OSError:
        _logger.warning("Could not save settings to %s", SETTINGS_PATH, exc_info=True)
