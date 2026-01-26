"""
TMB Data Manager - Fetches and caches data from That's My BIS website.

This module provides a central data layer for accessing raider information,
wishlists, loot history, attendance, and item data from TMB.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, date
from io import StringIO
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from ..core.paths import get_path_manager
from ..core.config import get_config_manager

# Configure module logger
logger = logging.getLogger(__name__)

# Module directory for relative path resolution
MODULE_DIR = Path(__file__).parent

# Get PathManager instance
paths = get_path_manager()

# Default paths from PathManager
DEFAULT_SESSION_PATH = paths.get_tmb_session_path()


# =============================================================================
# Custom Exceptions
# =============================================================================


class TMBError(Exception):
    """Base exception for TMB-related errors."""

    pass


class TMBSessionNotFoundError(TMBError):
    """Raised when the session file doesn't exist."""

    pass


class TMBSessionExpiredError(TMBError):
    """Raised when the session is invalid or expired."""

    pass


class TMBFetchError(TMBError):
    """Raised on network or parsing errors."""

    pass


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class TMBSession:
    """Represents a TMB session loaded from storage."""

    cookies: list[dict[str, Any]]
    created_at: datetime
    expires_at: datetime | None

    @classmethod
    def from_file(cls, path: Path) -> TMBSession:
        """Load session from JSON file."""
        if not path.exists():
            raise TMBSessionNotFoundError(f"Session file not found: {path}")

        try:
            with open(path, "r") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise TMBSessionNotFoundError(f"Invalid session file format: {e}")

        cookies = data.get("cookies", [])
        created_at_str = data.get("created_at")
        expires_at_str = data.get("expires_at")

        if not created_at_str:
            raise TMBSessionNotFoundError("Session file missing 'created_at' field")

        created_at = datetime.fromisoformat(created_at_str)
        expires_at = datetime.fromisoformat(expires_at_str) if expires_at_str else None

        return cls(cookies=cookies, created_at=created_at, expires_at=expires_at)

    def is_valid(self) -> bool:
        """Check if the session is still valid based on cookie expiration."""
        if self.expires_at is None:
            # No expiration info available - assume invalid to prompt re-auth
            return False
        return datetime.now() < self.expires_at

    def get_httpx_cookies(self) -> dict[str, str]:
        """Convert cookies to httpx-compatible format."""
        return {cookie["name"]: cookie["value"] for cookie in self.cookies}


@dataclass
class CachedData:
    """Container for cached DataFrame data."""

    raider_profiles: pd.DataFrame | None = None
    raider_wishlists: pd.DataFrame | None = None
    raider_received: pd.DataFrame | None = None
    attendance: pd.DataFrame | None = None
    item_notes: pd.DataFrame | None = None
    last_refresh: datetime | None = None

    def clear(self) -> None:
        """Clear all cached data."""
        self.raider_profiles = None
        self.raider_wishlists = None
        self.raider_received = None
        self.attendance = None
        self.item_notes = None
        self.last_refresh = None


# =============================================================================
# Module-level Shared Cache (all instances share this)
# =============================================================================

_shared_cache = CachedData()
_shared_characters_raw: list[dict] | None = None
_shared_cache_guild_id: str | None = None


# =============================================================================
# Utility Functions
# =============================================================================


def is_tmb_session_valid(session_path: Path | None = None) -> bool:
    """
    Check if a TMB session file exists and is valid.

    This is a lightweight check that doesn't instantiate a full TMBDataManager.

    Args:
        session_path: Optional path to session file. Defaults to DEFAULT_SESSION_PATH.

    Returns:
        True if session exists and is not expired, False otherwise.
    """
    path = session_path or DEFAULT_SESSION_PATH
    try:
        session = TMBSession.from_file(path)
        return session.is_valid()
    except (TMBSessionNotFoundError, Exception):
        return False


# =============================================================================
# TMB Data Manager
# =============================================================================


class TMBDataManager:
    """
    Manages fetching and caching of That's My BIS guild data.

    The manager caches data once per session. Data is fetched from the server
    on first access and returned from cache on subsequent accesses. Use
    refresh_all() to manually re-fetch all data from the server.

    Usage:
        manager = TMBDataManager(
            guild_id="900",
            guild_slug="off-topic",
            session_path=Path("./data/tmb_session.json")
        )

        # Get data (fetches on first call, returns cached thereafter)
        profiles = manager.get_raider_profiles()
        wishlists = manager.get_raider_wishlists()
        received = manager.get_raider_received()
        attendance = manager.get_attendance()
        items = manager.get_item_notes()

        # Force refresh from server (clears cache and re-fetches)
        manager.refresh_all()

        # Check session status
        if manager.is_session_valid():
            ...

    Environment Variables:
        TMB_GUILD_ID: Guild ID (required if not passed as parameter)
        TMB_GUILD_SLUG: Guild slug (required if not passed as parameter)
        TMB_SESSION_PATH: Path to session file (optional)
    """

    BASE_URL = "https://thatsmybis.com"

    # Endpoints relative to guild path
    ENDPOINTS = {
        "characters": "/export/characters-with-items/html",
        "attendance": "/export/attendance/html",
        "item_notes": "/export/item-notes/html",
    }

    def __init__(
        self,
        guild_id: str | None = None,
        guild_slug: str | None = None,
        session_path: Path | str | None = None,
    ):
        """
        Initialize the TMB Data Manager.

        Args:
            guild_id: TMB guild ID (defaults to config value)
            guild_slug: TMB guild slug (defaults to "placeholder" since it's not used in API)
            session_path: Path to tmb_session.json (defaults to PathManager path)
        """
        global _shared_cache, _shared_characters_raw, _shared_cache_guild_id

        config = get_config_manager()
        self.guild_id = guild_id or config.get_tmb_guild_id()
        self.guild_slug = guild_slug or "placeholder"  # Slug is not used in API endpoints

        if not self.guild_id:
            raise ValueError("guild_id is required (configure in GUI or config.json)")

        # Resolve session path - always use PathManager
        if session_path is not None:
            self.session_path = Path(session_path)
        else:
            self.session_path = DEFAULT_SESSION_PATH

        # Clear shared cache if guild ID changed
        if _shared_cache_guild_id is not None and _shared_cache_guild_id != self.guild_id:
            logger.info(f"Guild ID changed from {_shared_cache_guild_id} to {self.guild_id}, clearing cache")
            _shared_cache.clear()
            _shared_characters_raw = None

        _shared_cache_guild_id = self.guild_id

        # Session is still instance-level (different instances might use different session files)
        self._session: TMBSession | None = None

        logger.debug(f"TMBDataManager initialized for guild {self.guild_id}/{self.guild_slug}")

    @property
    def guild_url(self) -> str:
        """Build the base guild URL."""
        return f"{self.BASE_URL}/{self.guild_id}/{self.guild_slug}"

    def _load_session(self) -> TMBSession:
        """Load and cache session from file."""
        if self._session is None:
            self._session = TMBSession.from_file(self.session_path)
            logger.debug(f"Session loaded from {self.session_path}")
        return self._session

    def is_session_valid(self) -> bool:
        """Check if the current session is valid."""
        try:
            session = self._load_session()
            return session.is_valid()
        except TMBSessionNotFoundError:
            return False

    def _fetch_url(self, endpoint: str) -> str:
        """
        Fetch content from a TMB endpoint.

        Args:
            endpoint: Endpoint path relative to guild URL

        Returns:
            Response text content

        Raises:
            TMBSessionExpiredError: If session is expired or server redirects to login
            TMBFetchError: On network or other errors
        """
        session = self._load_session()

        if not session.is_valid():
            raise TMBSessionExpiredError("Session has expired based on local timestamp")

        url = f"{self.guild_url}{endpoint}"
        logger.debug(f"Fetching: {url}")

        try:
            with httpx.Client(
                cookies=session.get_httpx_cookies(),
                follow_redirects=False,
                timeout=30.0,
            ) as client:
                response = client.get(url)

                # Check for redirect to login page
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location", "")
                    if "/login" in location:
                        raise TMBSessionExpiredError(
                            "Session expired: server redirected to login"
                        )

                response.raise_for_status()
                return response.text

        except httpx.HTTPStatusError as e:
            raise TMBFetchError(f"HTTP error fetching {url}: {e}")
        except httpx.RequestError as e:
            raise TMBFetchError(f"Request error fetching {url}: {e}")

    def _fetch_characters_data(self) -> list[dict]:
        """Fetch and parse characters JSON data."""
        global _shared_characters_raw

        if _shared_characters_raw is not None:
            return _shared_characters_raw

        content = self._fetch_url(self.ENDPOINTS["characters"])

        try:
            _shared_characters_raw = json.loads(content)
            logger.info(f"Fetched {len(_shared_characters_raw)} characters from TMB")
            return _shared_characters_raw
        except json.JSONDecodeError as e:
            raise TMBFetchError(f"Failed to parse characters JSON: {e}")

    def _parse_date(self, date_str: str | None) -> date | None:
        """Parse a date string in 'YYYY-MM-DD HH:MM:SS' format to date object."""
        if not date_str:
            return None
        try:
            return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S").date()
        except ValueError:
            logger.warning(f"Failed to parse date: {date_str}")
            return None

    def _transform_wishlist_entry(self, item: dict) -> dict:
        """Transform a raw wishlist item into cleaned format."""
        pivot = item.get("pivot", {})
        return {
            "item_id": item.get("item_id"),
            "name": item.get("name"),
            "order": pivot.get("order"),
            "is_offspec": bool(pivot.get("is_offspec", 0)),
            "is_received": bool(pivot.get("is_received", 0)),
            "received_at": self._parse_date(pivot.get("received_at")),
        }

    def _transform_received_entry(self, item: dict) -> dict:
        """Transform a raw received item into cleaned format."""
        pivot = item.get("pivot", {})
        return {
            "item_id": item.get("item_id"),
            "name": item.get("name"),
            "is_offspec": bool(pivot.get("is_offspec", 0)),
            "received_at": self._parse_date(pivot.get("received_at")),
        }

    def get_raider_profiles(self) -> pd.DataFrame:
        """
        Get raider profiles DataFrame.

        Returns:
            DataFrame with columns: name, race, class, spec, archetype,
            profession_1, profession_2, is_alt
        """
        if _shared_cache.raider_profiles is not None:
            return _shared_cache.raider_profiles

        characters = self._fetch_characters_data()

        profiles = []
        for char in characters:
            profiles.append(
                {
                    "name": char.get("name"),
                    "race": char.get("race"),
                    "class": char.get("class"),
                    "spec": char.get("spec"),
                    "archetype": char.get("archetype"),
                    "profession_1": char.get("profession_1"),
                    "profession_2": char.get("profession_2"),
                    "is_alt": bool(char.get("is_alt", 0)),
                }
            )

        _shared_cache.raider_profiles = pd.DataFrame(profiles)
        _shared_cache.last_refresh = datetime.now()
        logger.info(f"Parsed {len(profiles)} raider profiles")
        return _shared_cache.raider_profiles

    def get_raider_wishlists(self) -> pd.DataFrame:
        """
        Get raider wishlists DataFrame.

        Returns:
            DataFrame with columns: name, wishlist
            The wishlist column contains a list of dicts with:
            item_id, name, order, is_offspec, is_received, received_at
        """
        if _shared_cache.raider_wishlists is not None:
            return _shared_cache.raider_wishlists

        characters = self._fetch_characters_data()

        wishlists = []
        for char in characters:
            wishlist_items = char.get("wishlist", [])
            cleaned_items = [
                self._transform_wishlist_entry(item) for item in wishlist_items
            ]
            wishlists.append({"name": char.get("name"), "wishlist": cleaned_items})

        _shared_cache.raider_wishlists = pd.DataFrame(wishlists)
        _shared_cache.last_refresh = datetime.now()
        logger.info(f"Parsed wishlists for {len(wishlists)} raiders")
        return _shared_cache.raider_wishlists

    def get_raider_received(self) -> pd.DataFrame:
        """
        Get raider received loot DataFrame.

        Returns:
            DataFrame with columns: name, received
            The received column contains a list of dicts with:
            item_id, name, is_offspec, received_at
        """
        if _shared_cache.raider_received is not None:
            return _shared_cache.raider_received

        characters = self._fetch_characters_data()

        received_data = []
        for char in characters:
            received_items = char.get("received", [])
            cleaned_items = [
                self._transform_received_entry(item) for item in received_items
            ]
            received_data.append({"name": char.get("name"), "received": cleaned_items})

        _shared_cache.raider_received = pd.DataFrame(received_data)
        _shared_cache.last_refresh = datetime.now()
        logger.info(f"Parsed received loot for {len(received_data)} raiders")
        return _shared_cache.raider_received

    def get_attendance(self) -> pd.DataFrame:
        """
        Get attendance DataFrame.

        Returns:
            DataFrame with columns: raid_date, raid_name, character_name, credit, remark
        """
        if _shared_cache.attendance is not None:
            return _shared_cache.attendance

        content = self._fetch_url(self.ENDPOINTS["attendance"])

        try:
            df = pd.read_csv(StringIO(content))
        except Exception as e:
            raise TMBFetchError(f"Failed to parse attendance CSV: {e}")

        # Select and rename columns
        column_mapping = {
            "raid_date": "raid_date",
            "raid_name": "raid_name",
            "character_name": "character_name",
            "credit": "credit",
            "remark": "remark",
        }

        # Keep only the columns we need
        available_cols = [col for col in column_mapping.keys() if col in df.columns]
        df = df[available_cols].copy()

        # Convert raid_date to date
        if "raid_date" in df.columns:
            df["raid_date"] = pd.to_datetime(df["raid_date"]).dt.date

        _shared_cache.attendance = df
        _shared_cache.last_refresh = datetime.now()
        logger.info(f"Parsed {len(df)} attendance records")
        return _shared_cache.attendance

    def get_item_notes(self) -> pd.DataFrame:
        """
        Get item notes DataFrame.

        Returns:
            DataFrame with columns: id, name, instance_name,
            tier, and prio_note
        """
        if _shared_cache.item_notes is not None:
            return _shared_cache.item_notes

        content = self._fetch_url(self.ENDPOINTS["item_notes"])

        try:
            df = pd.read_csv(StringIO(content))
        except Exception as e:
            raise TMBFetchError(f"Failed to parse item-notes CSV: {e}")

        # Select and rename columns
        column_mapping = {
            "id": "id",
            "name": "name",
            "instance_name": "instance_name",
            "tier": "tier",
            "prio_note": "prio_note",
        }

        # Keep only the columns we need
        available_cols = [col for col in column_mapping.keys() if col in df.columns]
        df = df[available_cols].copy()

        _shared_cache.item_notes = df
        _shared_cache.last_refresh = datetime.now()
        logger.info(f"Parsed {len(df)} item notes")
        return _shared_cache.item_notes

    def refresh_all(self) -> None:
        """Force refresh all cached data from server."""
        global _shared_cache, _shared_characters_raw

        logger.info("Refreshing all cached data...")
        _shared_cache.clear()
        _shared_characters_raw = None

        # Fetch all data types
        self.get_raider_profiles()
        self.get_raider_wishlists()
        self.get_raider_received()
        self.get_attendance()
        self.get_item_notes()

        logger.info("All data refreshed successfully")

    def get_session_info(self) -> dict[str, Any]:
        """Get information about the current session."""
        try:
            session = self._load_session()
            return {
                "valid": session.is_valid(),
                "created_at": session.created_at.isoformat(),
                "expires_at": session.expires_at.isoformat()
                if session.expires_at
                else None,
                "session_path": str(self.session_path),
                "cookie_count": len(session.cookies),
            }
        except TMBSessionNotFoundError as e:
            return {
                "valid": False,
                "error": str(e),
                "session_path": str(self.session_path),
            }


# =============================================================================
# Main Entry Point (for testing)
# =============================================================================


def main() -> None:
    """Test the TMB Data Manager."""
    # Configure logging for testing
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    print("=" * 60)
    print("TMB Data Manager Test")
    print("=" * 60)

    # Check for guild ID from config
    config = get_config_manager()
    guild_id = config.get_tmb_guild_id()
    guild_slug = "placeholder"  # Not used in API

    if not guild_id:
        print("\nNote: TMB Guild ID not configured.")
        print("Using example values for demonstration...")
        guild_id = "900"

    try:
        manager = TMBDataManager(guild_id=guild_id, guild_slug=guild_slug)
        print(f"\nGuild URL: {manager.guild_url}")
        print(f"Session Path: {manager.session_path}")

        # Check session status
        session_info = manager.get_session_info()
        print(f"\nSession Status:")
        print(f"  Valid: {session_info.get('valid', False)}")
        if "error" in session_info:
            print(f"  Error: {session_info['error']}")
        else:
            print(f"  Created: {session_info.get('created_at')}")
            print(f"  Expires: {session_info.get('expires_at', 'N/A')}")
            print(f"  Cookies: {session_info.get('cookie_count', 0)}")

        if not manager.is_session_valid():
            print("\n⚠️  Session is not valid. Please run authenticate.py first.")
            return

        print("\nFetching data...")
        print("-" * 40)

        # Fetch and display each data type
        try:
            profiles = manager.get_raider_profiles()
            print(f"✓ Raider Profiles: {profiles.shape[0]} rows, {profiles.shape[1]} columns")
            print(f"  Columns: {list(profiles.columns)}")
        except TMBFetchError as e:
            print(f"✗ Raider Profiles: {e}")

        try:
            wishlists = manager.get_raider_wishlists()
            print(f"✓ Raider Wishlists: {wishlists.shape[0]} rows, {wishlists.shape[1]} columns")
            print(f"  Columns: {list(wishlists.columns)}")
        except TMBFetchError as e:
            print(f"✗ Raider Wishlists: {e}")

        try:
            received = manager.get_raider_received()
            print(f"✓ Raider Received: {received.shape[0]} rows, {received.shape[1]} columns")
            print(f"  Columns: {list(received.columns)}")
        except TMBFetchError as e:
            print(f"✗ Raider Received: {e}")

        try:
            attendance = manager.get_attendance()
            print(f"✓ Attendance: {attendance.shape[0]} rows, {attendance.shape[1]} columns")
            print(f"  Columns: {list(attendance.columns)}")
        except TMBFetchError as e:
            print(f"✗ Attendance: {e}")

        try:
            items = manager.get_item_notes()
            print(f"✓ Item Notes: {items.shape[0]} rows, {items.shape[1]} columns")
            print(f"  Columns: {list(items.columns)}")
        except TMBFetchError as e:
            print(f"✗ Item Notes: {e}")

        print("-" * 40)
        print("Data fetch complete!")

    except TMBSessionNotFoundError as e:
        print(f"\n❌ Session Error: {e}")
        print("Please run authenticate.py to create a session file.")

    except TMBSessionExpiredError as e:
        print(f"\n❌ Session Expired: {e}")
        print("Please run authenticate.py to refresh your session.")

    except ValueError as e:
        print(f"\n❌ Configuration Error: {e}")

    except Exception as e:
        print(f"\n❌ Unexpected Error: {type(e).__name__}: {e}")
        logger.exception("Unexpected error in main")


if __name__ == "__main__":
    main()