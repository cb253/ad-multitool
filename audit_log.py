"""Local audit trail for actions taken through the tool (e.g. account unlocks).

This file lives in the operator's own profile and they can edit it, so it is a
convenience record, not a tamper-proof one. The authoritative audit trail is the
domain controllers' Security log (event 4767 for unlocks, 4725 for disables).
"""

import logging
import os
import threading
from datetime import datetime

from app_data import get_app_data_dir

LOG_PATH = os.path.join(get_app_data_dir(), "audit.log")

_write_lock = threading.Lock()
_logger = logging.getLogger(__name__)


def log_event(actor, action, target, detail="", outcome="success"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{timestamp} | actor={actor or 'unknown'} | action={action} | outcome={outcome} | target={target}"
    if detail:
        line += f" | {detail}"
    try:
        with _write_lock, open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        # Best-effort; don't block the action it's recording, but leave a trace in the app log.
        _logger.exception("Could not write audit log entry: %s", line)
