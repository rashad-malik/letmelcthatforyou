"""
In-memory parse cache for WarcraftLogs data.

This module provides caching for raider parse data fetched from WarcraftLogs.
The cache is stored in-memory at the module level, meaning it persists for
the lifetime of the application and is cleared on restart.
"""
from dataclasses import dataclass
from typing import Optional, Dict


@dataclass
class ParseData:
    """Container for a raider's parse data for a specific zone."""
    best_avg: Optional[float]
    median_avg: Optional[float]


# Module-level cache: {zone_id: {raider_name: ParseData}}
_parse_cache: Dict[int, Dict[str, ParseData]] = {}


def get_cached_parse(zone_id: int, raider_name: str) -> Optional[ParseData]:
    """
    Get cached parse data for a raider in a zone.

    Args:
        zone_id: WarcraftLogs zone ID
        raider_name: Name of the raider

    Returns:
        ParseData if cached, None otherwise
    """
    zone_cache = _parse_cache.get(zone_id, {})
    return zone_cache.get(raider_name)


def cache_parse(zone_id: int, raider_name: str, best: Optional[float], median: Optional[float]) -> None:
    """
    Cache parse data for a raider.

    Args:
        zone_id: WarcraftLogs zone ID
        raider_name: Name of the raider
        best: Best performance average (or None if not found)
        median: Median performance average (or None if not found)
    """
    if zone_id not in _parse_cache:
        _parse_cache[zone_id] = {}
    _parse_cache[zone_id][raider_name] = ParseData(best_avg=best, median_avg=median)


def is_raider_cached(zone_id: int, raider_name: str) -> bool:
    """
    Check if raider's parse data is cached for this zone.

    Args:
        zone_id: WarcraftLogs zone ID
        raider_name: Name of the raider

    Returns:
        True if cached, False otherwise
    """
    return raider_name in _parse_cache.get(zone_id, {})


def clear_cache() -> None:
    """Clear all cached parse data."""
    global _parse_cache
    _parse_cache = {}


def get_cache_stats() -> Dict[int, int]:
    """
    Get statistics about the current cache.

    Returns:
        Dict mapping zone_id to count of cached raiders
    """
    return {zone_id: len(raiders) for zone_id, raiders in _parse_cache.items()}
