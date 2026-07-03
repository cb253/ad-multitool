"""Local audit trail for actions taken through the tool (e.g. account unlocks)."""

import os
from datetime import datetime

from app_data import get_app_data_dir

LOG_PATH = os.path.join(get_app_data_dir(), "audit.log")


def log_event(actor, action, target, detail=""):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} | actor={actor or 'unknown'} | action={action} | target={target}"
    if detail:
        line += f" | {detail}"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass  # best-effort logging; don't block the action it's recording
