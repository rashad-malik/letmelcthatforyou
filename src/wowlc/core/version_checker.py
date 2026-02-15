"""
Check GitHub releases for the latest application version.
"""

import requests
from wowlc._version import __version__

GITHUB_API_URL = "https://api.github.com/repos/rashad-malik/letmelcthatforyou/releases/latest"
RELEASES_URL = "https://github.com/rashad-malik/letmelcthatforyou/releases"


def get_current_version() -> str:
    """Return the current application version string."""
    return __version__


def fetch_latest_release() -> dict:
    """
    Query GitHub for the latest release and compare to the current version.

    Returns a dict with keys:
        current       – current app version (str)
        latest        – latest release version from GitHub (str or None)
        update_available – True if a newer release exists (bool)
        release_url   – URL to the releases page (str)
        error         – error message on failure, else None
    """
    current = get_current_version()
    result = {
        "current": current,
        "latest": None,
        "update_available": False,
        "release_url": RELEASES_URL,
        "error": None,
    }

    try:
        response = requests.get(GITHUB_API_URL, timeout=10)
        response.raise_for_status()
        tag = response.json().get("tag_name", "")
        # Strip leading 'v' for comparison (tags are "v2.0.3-beta")
        latest = tag.lstrip("v") if tag else ""
        if latest:
            result["latest"] = latest
            result["update_available"] = latest != current
    except requests.exceptions.RequestException as e:
        result["error"] = str(e)

    return result
