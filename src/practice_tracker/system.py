"""Self-update bridge. The app runs unprivileged, so it never updates itself: it writes a request
file into its data dir, a systemd path unit starts the root update hook, and the hook writes its
progress to update-status.json. Without the hook installed (dev, Docker) nothing picks it up.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .config import Settings
from .errors import Conflict

REQUEST_FILE = "update-requested"
STATUS_FILE = "update-status.json"
STALE_REQUEST = timedelta(minutes=2)


def update_status(settings: Settings) -> dict:
    status_path = settings.data_dir / STATUS_FILE
    request_path = settings.data_dir / REQUEST_FILE
    try:
        status = json.loads(status_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        status = None
    pending = request_path.exists()
    return {
        "hook_installed": status is not None,
        "requested": pending,
        "stale_request": pending and _age(request_path) > STALE_REQUEST,  # nobody picked it up
        "last": status,
    }


def request_update(settings: Settings, force: bool = False) -> dict:
    current = update_status(settings)
    if current["last"] and current["last"].get("state") == "running":
        raise Conflict("an update is already running")
    (settings.data_dir / REQUEST_FILE).write_text("force\n" if force else "\n")
    return update_status(settings)


def _age(path) -> timedelta:
    mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return datetime.now(timezone.utc) - mtime
