"""
ThatsmyBIS Session Authentication Script.

This script opens a browser window for the user to authenticate with ThatsmyBIS
via Discord, then captures the session cookies for API access.

The script uses PySide6 Qt WebEngine to open a controlled browser window and
capture cookies after successful login.

Usage:
    python tmb_authenticate.py
"""

from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional
import json
import sys
import time

# Add src to path for imports
src_path = Path(__file__).resolve().parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from wowlc.core.paths import get_path_manager

THATSMYBIS_BASE_URL = "https://thatsmybis.com"

# Get PathManager instance
paths = get_path_manager()
COOKIE_STORAGE_PATH = paths.get_tmb_session_path()


@dataclass
class StoredSession:
    cookies: list[dict]
    created_at: str
    expires_at: Optional[str] = None


def save_cookies(cookies: list[dict]) -> None:
    """Save cookies to storage file."""
    COOKIE_STORAGE_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Find the longest-lived TMB cookie for expiration
    # Priority: remember_web_* (1 year) > thats_my_bis_session (8 hours)
    expires_at = None
    for cookie in cookies:
        if cookie.get("domain", "").endswith("thatsmybis.com") and cookie.get("expires"):
            cookie_expires = cookie["expires"]
            if cookie_expires > 0:  # Ignore session cookies with -1
                if expires_at is None or cookie_expires > expires_at:
                    expires_at = cookie_expires

    # If no expiration found from cookies, default to 30 days
    if expires_at is None:
        expires_at = time.time() + (30 * 24 * 60 * 60)  # 30 days from now

    session = StoredSession(
        cookies=cookies,
        created_at=datetime.now().isoformat(),
        expires_at=datetime.fromtimestamp(expires_at).isoformat()
    )

    with open(COOKIE_STORAGE_PATH, "w") as f:
        json.dump(asdict(session), f, indent=2)

    print(f"Cookies saved to {COOKIE_STORAGE_PATH}")


def authenticate_with_webview(timeout_seconds: int = 300) -> list[dict]:
    """
    Authenticate using Qt WebEngine with native cookie capture.

    Uses PySide6 QtWebEngine to capture all cookies including HttpOnly session cookies.

    Args:
        timeout_seconds: Maximum time to wait for authentication (default 5 minutes)

    Returns:
        List of captured TMB cookies

    Raises:
        Exception: If authentication times out or fails
    """
    from wowlc.qt.auth_webview import authenticate_with_qt

    print("Opening browser for ThatsmyBIS authentication...")
    print("   Please complete the Discord login process.")
    print("   The window will close automatically once you're logged in.\n")

    cookies = authenticate_with_qt(timeout_seconds=timeout_seconds)

    # Filter for TMB cookies only
    tmb_cookies = [c for c in cookies if "thatsmybis" in c.get("domain", "")]

    if not tmb_cookies:
        raise Exception("No ThatsmyBIS cookies captured.")

    save_cookies(tmb_cookies)
    return tmb_cookies


def _run_webview_in_process(result_queue):
    """
    Worker function to run webview authentication in a separate process.
    This is needed because pywebview requires the main thread.
    """
    try:
        cookies = authenticate_with_webview()
        result_queue.put({"success": True, "cookies": cookies})
    except Exception as e:
        result_queue.put({"success": False, "error": str(e)})


def authenticate_subprocess() -> list[dict]:
    """
    Authenticate with ThatsmyBIS by running webview in a subprocess.

    pywebview requires the main thread, so when called from a GUI application
    (like NiceGUI), we must run the authentication in a separate process.

    Returns:
        List of captured cookies.

    Raises:
        Exception: If authentication fails or is cancelled.
    """
    import multiprocessing

    # Use spawn context explicitly to avoid fork issues with Qt on Linux.
    # fork() copies the parent's Qt state which causes thread crashes.
    # On Windows, this is a no-op since spawn is already the default.
    if sys.platform.startswith('linux'):
        ctx = multiprocessing.get_context('spawn')
    else:
        ctx = multiprocessing

    result_queue = ctx.Queue()
    process = ctx.Process(
        target=_run_webview_in_process,
        args=(result_queue,)
    )
    process.start()
    process.join(timeout=310)  # 5 min timeout + 10 sec buffer

    if process.is_alive():
        process.terminate()
        process.join()
        raise Exception("Authentication timed out")

    if result_queue.empty():
        raise Exception("Authentication process ended without result")

    result = result_queue.get()

    if not result.get("success"):
        raise Exception(result.get("error", "Unknown error"))

    # Load the saved cookies (they were saved by the subprocess)
    if COOKIE_STORAGE_PATH.exists():
        with open(COOKIE_STORAGE_PATH) as f:
            data = json.load(f)
        return data.get("cookies", [])
    else:
        raise Exception("Authentication completed but no session file found")


def authenticate() -> list[dict]:
    """
    Authenticate with ThatsmyBIS.

    Opens a browser window for Discord login and captures session cookies.
    When called from another application (e.g., NiceGUI), runs in a subprocess
    to avoid main thread conflicts with pywebview.

    Returns:
        List of captured cookies.

    Raises:
        Exception: If authentication fails or is cancelled.
    """
    # Check if we're being called from the main module or imported
    # If imported (e.g., from NiceGUI), use subprocess to avoid main thread issues
    import __main__
    if hasattr(__main__, '__file__') and __main__.__file__ and Path(__main__.__file__).name == "tmb_authenticate.py":
        # We're running as the main script, use direct webview
        return authenticate_with_webview()
    else:
        # We're imported from another application, use subprocess
        return authenticate_subprocess()


def load_existing_session() -> Optional[dict]:
    """Load existing session from storage if it exists and is valid."""
    if not COOKIE_STORAGE_PATH.exists():
        return None

    try:
        with open(COOKIE_STORAGE_PATH) as f:
            data = json.load(f)

        # Check if expired
        if data.get("expires_at"):
            expires_at = datetime.fromisoformat(data["expires_at"])
            if datetime.now() > expires_at:
                print("Warning: Existing session has expired")
                return None

        return data
    except Exception as e:
        print(f"Warning: Could not load existing session: {e}")
        return None


if __name__ == "__main__":
    # Check for --no-prompt flag (used when called as subprocess)
    no_prompt = "--no-prompt" in sys.argv

    if not no_prompt:
        print("=" * 60)
        print("ThatsmyBIS Session Authentication")
        print("=" * 60)

        # Check for existing session (only in interactive mode)
        existing = load_existing_session()
        if existing:
            print(f"\nFound existing session (created: {existing.get('created_at', 'unknown')})")
            print(f"  Expires: {existing.get('expires_at', 'unknown')}")
            response = input("\nDo you want to get a new session anyway? (y/N): ")
            if response.lower() != "y":
                print(f"\nUsing existing session with {len(existing.get('cookies', []))} cookies")
                exit(0)

    try:
        # Always use direct webview when running as main script
        cookies = authenticate_with_webview()

        if not no_prompt:
            print("\n" + "=" * 60)
            print("Authentication successful!")
            print("=" * 60)
            print(f"\nCaptured {len(cookies)} cookies")
            print(f"\nSession saved to:")
            print(f"   {COOKIE_STORAGE_PATH}")
            print(f"\nYou can now use the TMB features in the application.")

    except Exception as e:
        print(f"\nAuthentication failed: {e}", file=sys.stderr)
        exit(1)
