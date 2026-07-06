"""
WarcraftLogs zone data: bundled zones.json merged with user-added custom zones.

Lives in core so both the backend tools and the GUI can import it — the zone-ID
sets and the parse-zone dropdown must always agree.
"""
import json

from wowlc.core.paths import get_path_manager

# Canonical game version keys (match realms.json / custom_realms / custom_zones)
VERSION_ERA = "Era"
VERSION_TBC = "TBC Anniversary"
VERSION_TBC_LEGACY = "TBC Legacy"  # dev-only (Pyrewood mode); no custom zones

# Cached zone data loaded from zones.json
_zone_data: dict | None = None


def _load_zone_data() -> dict:
    """Load zone data from bundled zones.json (cached after first load)."""
    global _zone_data
    if _zone_data is None:
        zones_file = get_path_manager().get_bundled_resource("data/zones.json")
        if zones_file is None:
            raise FileNotFoundError("Bundled zones.json not found")
        with open(zones_file, "r", encoding="utf-8") as f:
            _zone_data = json.load(f)
    return _zone_data


def canonical_version_key(game_version: str) -> str:
    """Normalise any game-version label to a canonical key.

    Accepts the header toggle labels ('Era (WIP)', 'TBC Anniversary'), the
    legacy stored default ('Fresh'), and lowercase variants.
    """
    v = (game_version or "").strip().lower()
    if v.startswith("era") or v == "fresh":
        return VERSION_ERA
    return VERSION_TBC


def resolve_version_key(game_version: str) -> str:
    """Canonical key with the Pyrewood dev-mode override (TBC -> TBC Legacy)."""
    from wowlc.core.config import get_config_manager

    version_key = canonical_version_key(game_version)
    if version_key == VERSION_TBC and get_config_manager().get_pyrewood_dev_mode():
        return VERSION_TBC_LEGACY
    return version_key


def current_version_key() -> str:
    """Resolved version key for the configured client version (backend entry point)."""
    from wowlc.core.config import get_config_manager

    return resolve_version_key(get_config_manager().get_wcl_client_version())


def get_zone_options(version_key: str) -> dict[int, str]:
    """Get {zone_id: label} for a version key (bundled merged with custom zones)."""
    from wowlc.core.config import get_config_manager

    merged = dict(_load_zone_data().get(version_key, {}))
    if version_key != VERSION_TBC_LEGACY:
        merged.update(get_config_manager().get_custom_zones(version_key))

    options: dict[int, str] = {}
    for zone_id, label in merged.items():
        try:
            options[int(zone_id)] = label
        except (TypeError, ValueError):
            continue
    return options


def get_valid_zone_ids(version_key: str) -> set[int]:
    """Get the set of valid WCL zone IDs for a version key."""
    return set(get_zone_options(version_key).keys())
