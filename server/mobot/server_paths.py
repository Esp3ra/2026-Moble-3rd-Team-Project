"""Machine-local application state stays outside the project checkout."""
import os
from pathlib import Path


def instance_directory():
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "MOBOT" / "instance"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "mobot" / "instance"
