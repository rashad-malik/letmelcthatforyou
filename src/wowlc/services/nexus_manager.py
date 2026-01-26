"""
NexusItemManager - WoW Classic item database lookups using nexus-devs data.

This module provides item lookups for World of Warcraft Classic/TBC items,
fetching data from the nexus-devs wow-classic-items GitHub repository.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.request import urlopen
from urllib.error import URLError, HTTPError

from ..core.paths import get_path_manager

logger = logging.getLogger(__name__)

# Get PathManager instance
paths = get_path_manager()

NEXUS_DATA_URL = (
    "https://raw.githubusercontent.com/nexus-devs/wow-classic-items/"
    "refs/heads/master/data/json/data.json"
)


class NexusDataLoadError(Exception):
    """Raised when item database cannot be fetched or parsed."""
    pass


@dataclass
class NexusCachedData:
    """Container for cached Nexus item data."""
    items_by_id: dict[int, dict] = field(default_factory=dict)
    loaded: bool = False
    last_refresh: datetime | None = None

    def clear(self) -> None:
        """Clear all cached data."""
        self.items_by_id = {}
        self.loaded = False
        self.last_refresh = None


# =============================================================================
# Module-level Shared Cache (all instances share this)
# =============================================================================
_shared_cache = NexusCachedData()


class NexusItemManager:
    """
    Manages item lookups using the nexus-devs wow-classic-items database.
    
    Data is fetched from GitHub and cached in memory on first access.
    Local file caching is supported via the NEXUS_CACHE_PATH environment variable.
    
    Usage:
        manager = NexusItemManager()
        
        # Lookup by ID
        name = manager.get_item_name(34234)  # "Muramasa"
        
        # Lookup by name
        item_id = manager.get_item_id("Muramasa")  # 34234
        
        # Get full item data
        item = manager.get_item(34234)  # Returns full dict or None
        
        # Get specific attributes
        ilvl = manager.get_item_level(34234)  # 159
        slot = manager.get_item_slot(34234)  # "One-Hand"
    """
    
    def __init__(self) -> None:
        """Initialize the manager. Uses module-level shared cache."""
        # No instance-level cache needed - using module-level _shared_cache
        pass
    
    def is_loaded(self) -> bool:
        """Check if data has been loaded into shared cache."""
        return _shared_cache.loaded
    
    def _get_cache_path(self) -> Optional[Path]:
        """Get the cache file path from PathManager or environment variable."""
        return paths.get_nexus_cache_path()
    
    def _load_from_cache(self, cache_path: Path) -> Optional[list[dict]]:
        """Load item data from local cache file."""
        try:
            if cache_path.exists():
                logger.info(f"Loading item database from cache: {cache_path}")
                with open(cache_path, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load from cache: {e}")
        return None
    
    def _save_to_cache(self, cache_path: Path, data: list[dict]) -> None:
        """Save item data to local cache file."""
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            logger.info(f"Saved item database to cache: {cache_path}")
        except IOError as e:
            logger.warning(f"Failed to save to cache: {e}")
    
    def _fetch_from_github(self) -> list[dict]:
        """Fetch item data from GitHub."""
        logger.info(f"Fetching item database from GitHub: {NEXUS_DATA_URL}")
        try:
            with urlopen(NEXUS_DATA_URL, timeout=60) as response:
                raw_data = response.read()
                return json.loads(raw_data)
        except HTTPError as e:
            raise NexusDataLoadError(f"HTTP error fetching item database: {e.code} {e.reason}")
        except URLError as e:
            raise NexusDataLoadError(f"Network error fetching item database: {e.reason}")
        except json.JSONDecodeError as e:
            raise NexusDataLoadError(f"Failed to parse item database JSON: {e}")
    
    def load_data(self) -> None:
        """
        Fetch and parse the item database.

        Called automatically on first lookup. Checks shared cache first,
        then local file cache, then falls back to GitHub fetch.

        Raises:
            NexusDataLoadError: If data cannot be loaded from any source.
        """
        global _shared_cache

        if _shared_cache.loaded:
            return

        cache_path = self._get_cache_path()
        items: Optional[list[dict]] = None

        # Try loading from local file cache first
        if cache_path:
            items = self._load_from_cache(cache_path)

        # Fetch from GitHub if not cached locally
        if items is None:
            items = self._fetch_from_github()

            # Save to local file cache for future use
            if cache_path:
                self._save_to_cache(cache_path, items)

        # Build the lookup dictionary in shared cache
        _shared_cache.items_by_id = {}
        for item in items:
            item_id = item.get("itemId")
            if item_id is not None:
                _shared_cache.items_by_id[item_id] = item

        _shared_cache.loaded = True
        _shared_cache.last_refresh = datetime.now()
        logger.info(f"Loaded {len(_shared_cache.items_by_id)} items into shared cache")

    def refresh_data(self) -> None:
        """
        Force refresh data from source, clearing the shared cache.

        This will clear the in-memory shared cache and reload data from GitHub.
        The local file cache will also be updated.

        Raises:
            NexusDataLoadError: If data cannot be loaded from any source.
        """
        global _shared_cache

        logger.info("Refreshing Nexus item data (clearing shared cache)...")
        _shared_cache.clear()

        # Force fetch from GitHub to get fresh data
        items = self._fetch_from_github()

        # Update local file cache
        cache_path = self._get_cache_path()
        if cache_path:
            self._save_to_cache(cache_path, items)

        # Build the lookup dictionary in shared cache
        _shared_cache.items_by_id = {}
        for item in items:
            item_id = item.get("itemId")
            if item_id is not None:
                _shared_cache.items_by_id[item_id] = item

        _shared_cache.loaded = True
        _shared_cache.last_refresh = datetime.now()
        logger.info(f"Refreshed {len(_shared_cache.items_by_id)} items into shared cache")

    def _ensure_loaded(self) -> None:
        """Ensure data is loaded into shared cache before performing lookups."""
        if not _shared_cache.loaded:
            self.load_data()
    
    def get_item(self, item_id: int) -> Optional[dict]:
        """
        Get full item data by ID.

        Args:
            item_id: The item ID to look up.

        Returns:
            Full item dictionary or None if not found.
        """
        self._ensure_loaded()
        return _shared_cache.items_by_id.get(item_id)
    
    def get_item_name(self, item_id: int) -> str:
        """
        Get item name by ID.
        
        Args:
            item_id: The item ID to look up.
            
        Returns:
            Item name, or "Item {id}" if not found.
        """
        item = self.get_item(item_id)
        if item and "name" in item:
            return item["name"]
        return f"Item {item_id}"
    
    def get_item_id(self, item_name: str) -> Optional[int]:
        """
        Get item ID by name (case-insensitive).

        Args:
            item_name: The item name to search for.

        Returns:
            Item ID or None if not found. Returns first match if duplicates exist.
        """
        self._ensure_loaded()
        item_name_lower = item_name.lower()

        for item_id, item in _shared_cache.items_by_id.items():
            name = item.get("name", "")
            if name.lower() == item_name_lower:
                return item_id

        return None
    
    def get_item_level(self, item_id: int) -> Optional[int]:
        """
        Get item level by ID.
        
        Args:
            item_id: The item ID to look up.
            
        Returns:
            Item level or None if not found.
        """
        item = self.get_item(item_id)
        if item:
            return item.get("itemLevel")
        return None
    
    def get_item_slot(self, item_id: int) -> Optional[str]:
        """
        Get item slot by ID.
        
        Args:
            item_id: The item ID to look up.
            
        Returns:
            Item slot (e.g., "Head", "Chest", "One-Hand") or None if not found.
        """
        item = self.get_item(item_id)
        if item:
            return item.get("slot")
        return None
    
    def search_items(self, query: str) -> list[dict]:
        """
        Search items by partial name match (case-insensitive).

        Args:
            query: Search query string.

        Returns:
            List of matching item dictionaries.
        """
        self._ensure_loaded()
        query_lower = query.lower()

        results = []
        for item in _shared_cache.items_by_id.values():
            name = item.get("name", "")
            if query_lower in name.lower():
                results.append(item)

        return results


# =============================================================================
# Module-level Utility Functions
# =============================================================================


def get_nexus_cache_info() -> dict:
    """
    Get information about the shared Nexus cache.

    Returns:
        Dictionary with cache status information.
    """
    return {
        "loaded": _shared_cache.loaded,
        "item_count": len(_shared_cache.items_by_id),
        "last_refresh": _shared_cache.last_refresh.isoformat() if _shared_cache.last_refresh else None,
    }


def clear_nexus_cache() -> None:
    """
    Clear the shared Nexus cache.

    Useful for testing or forcing a fresh load on next access.
    """
    global _shared_cache
    _shared_cache.clear()
    logger.info("Shared Nexus cache cleared")


if __name__ == "__main__":
    # Configure logging for testing
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    print("=" * 60)
    print("NexusItemManager Test Suite")
    print("=" * 60)
    
    manager = NexusItemManager()
    
    # Test 1: Load data
    print("\n[Test 1] Loading item database...")
    try:
        manager.load_data()
        cache_info = get_nexus_cache_info()
        print(f"✓ Loaded {cache_info['item_count']} items")
    except NexusDataLoadError as e:
        print(f"✗ Failed to load data: {e}")
        exit(1)
    
    # Test 2: Check loaded state
    print("\n[Test 2] Checking loaded state...")
    print(f"✓ is_loaded() = {manager.is_loaded()}")
    
    # Test 3: Get item name by ID
    print("\n[Test 3] Testing get_item_name()...")
    test_id = 34234  # Muramasa
    name = manager.get_item_name(test_id)
    print(f"✓ Item {test_id}: {name}")
    
    # Test 4: Get item ID by name
    print("\n[Test 4] Testing get_item_id()...")
    test_name = "Sunflare"
    item_id = manager.get_item_id(test_name)
    if item_id:
        print(f"✓ '{test_name}' -> ID {item_id}")
    else:
        # Try another item if Sunflare doesn't exist
        test_name = "Thunderfury, Blessed Blade of the Windseeker"
        item_id = manager.get_item_id(test_name)
        if item_id:
            print(f"✓ '{test_name}' -> ID {item_id}")
        else:
            print(f"? Could not find test item by name")
    
    # Test 5: Case-insensitive name lookup
    print("\n[Test 5] Testing case-insensitive lookup...")
    if item_id:
        upper_result = manager.get_item_id(test_name.upper())
        lower_result = manager.get_item_id(test_name.lower())
        if upper_result == lower_result == item_id:
            print(f"✓ Case-insensitive lookup works")
        else:
            print(f"✗ Case-insensitive lookup failed")
    
    # Test 6: Get item level and slot
    print("\n[Test 6] Testing get_item_level() and get_item_slot()...")
    test_id = 34234  # Muramasa
    ilvl = manager.get_item_level(test_id)
    slot = manager.get_item_slot(test_id)
    print(f"✓ Item {test_id}: ilvl={ilvl}, slot={slot}")
    
    # Test 7: Get full item data
    print("\n[Test 7] Testing get_item()...")
    item = manager.get_item(test_id)
    if item:
        print(f"✓ Full item data for {test_id}:")
        print(f"  - name: {item.get('name')}")
        print(f"  - itemLevel: {item.get('itemLevel')}")
        print(f"  - quality: {item.get('quality')}")
        print(f"  - slot: {item.get('slot')}")
        print(f"  - class: {item.get('class')}")
        if item.get('source'):
            print(f"  - source: {item.get('source')}")
    
    # Test 8: Search items
    print("\n[Test 8] Testing search_items()...")
    search_results = manager.search_items("Warglaive")
    print(f"✓ Found {len(search_results)} items matching 'Warglaive':")
    for result in search_results[:5]:  # Show first 5
        print(f"  - {result.get('name')} (ID: {result.get('itemId')})")
    if len(search_results) > 5:
        print(f"  ... and {len(search_results) - 5} more")
    
    # Test 9: Not found cases
    print("\n[Test 9] Testing not-found cases...")
    fake_id = 99999999
    fake_name = manager.get_item_name(fake_id)
    print(f"✓ get_item_name({fake_id}) = '{fake_name}'")
    
    fake_item = manager.get_item(fake_id)
    print(f"✓ get_item({fake_id}) = {fake_item}")
    
    fake_lookup = manager.get_item_id("This Item Does Not Exist 12345")
    print(f"✓ get_item_id('nonexistent') = {fake_lookup}")
    
    fake_ilvl = manager.get_item_level(fake_id)
    print(f"✓ get_item_level({fake_id}) = {fake_ilvl}")
    
    fake_slot = manager.get_item_slot(fake_id)
    print(f"✓ get_item_slot({fake_id}) = {fake_slot}")

    # Test 10: Shared caching
    print("\n[Test 10] Testing shared caching...")
    manager2 = NexusItemManager()
    # manager2 should already have data loaded (from shared cache)
    print(f"  manager.is_loaded() = {manager.is_loaded()}")
    print(f"  manager2.is_loaded() = {manager2.is_loaded()}")
    if manager2.is_loaded():
        print("✓ Shared caching works: second instance sees loaded data")
    else:
        print("✗ Shared caching failed: second instance does not see loaded data")

    # Test 11: Cache info
    print("\n[Test 11] Testing get_nexus_cache_info()...")
    cache_info = get_nexus_cache_info()
    print(f"✓ Cache info: {cache_info}")

    # Test 12: Cache clear
    print("\n[Test 12] Testing clear_nexus_cache()...")
    clear_nexus_cache()
    print(f"  After clear - manager.is_loaded() = {manager.is_loaded()}")
    print(f"  After clear - manager2.is_loaded() = {manager2.is_loaded()}")
    if not manager.is_loaded() and not manager2.is_loaded():
        print("✓ Cache clear works: both instances see unloaded state")
    else:
        print("✗ Cache clear failed")

    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)