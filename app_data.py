"""Resolves the per-user, writable folder for this app's local data (settings, logs).

Using %APPDATA% instead of a path next to the script/exe means this keeps working
when run from a shared, possibly read-only, network location.
"""

import os

APP_DATA_DIR = os.path.join(os.getenv("APPDATA") or os.path.expanduser("~"), "AD-Multitool")


def get_app_data_dir():
    os.makedirs(APP_DATA_DIR, exist_ok=True)
    return APP_DATA_DIR
