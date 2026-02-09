"""
Fetching Current Items - WoW Character Gear Lookup

This module provides functions to fetch character gear data:
1. get_equipped_items() - All equipped items from most recent WCL raid log
2. get_equipped_items_blizzard() - All equipped items from Blizzard API
3. get_equipped_items_for_source() - Dispatcher that uses configured API source
4. get_last_received_items() - Last mainspec item received in each slot from TMB

Used to build a cache of player gear profiles.
"""

from datetime import date, datetime
from pathlib import Path
from typing import Optional, Any
import json
import pandas as pd
import sys
import logging

# Configure logging
logging.basicConfig(
    level=logging.WARNING,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from ..core.config import get_config_manager
from ..core.paths import get_path_manager
from ..services.tmb_manager import TMBDataManager
from ..services.wcl_client import WarcraftLogsClient
from ..services.nexus_manager import NexusItemManager
from ..services.blizz_manager import get_access_token, fetch_character_gear_names


# Tier token slot mapping cache (lazy-loaded)
# Maps token_name (lowercase) -> {"slot": slot, "ilvl": ilvl}
_TOKEN_SLOT_MAP: Optional[dict[str, dict]] = None


def _build_token_slot_mapping() -> dict[str, dict]:
    """
    Build a mapping from tier token names to their slot and ilvl.

    Returns:
        Dictionary mapping token_name (lowercase) -> {"slot": slot, "ilvl": ilvl}
        e.g., {"helm of the fallen defender": {"slot": "head", "ilvl": 120}}
    """
    # Use the bundled tokens.json file
    tokens_file = Path(__file__).resolve().parent.parent.parent.parent / "data" / "tokens.json"

    if not tokens_file.exists():
        logger.warning(f"Tier tokens file not found: {tokens_file}")
        return {}

    try:
        with open(tokens_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Failed to load tier tokens: {e}")
        return {}

    mapping = {}

    # tokens.json structure: {"TBC": [{"tier_version": "...", "tokens": [...]}], "exchange_items_tbc": {...}}
    # Only process tier token keys (lists), skip exchange_items_* keys (dicts)
    for expansion_data in data.values():
        if not isinstance(expansion_data, list):
            continue
        for tier_group in expansion_data:
            for token in tier_group.get("tokens", []):
                token_name = token.get("token_name", "")
                slot = token.get("slot", "")
                ilvl = token.get("ilvl", 0)
                if token_name and slot:
                    mapping[token_name.lower()] = {
                        "slot": slot.lower(),
                        "ilvl": ilvl
                    }

    logger.debug(f"Built token slot mapping with {len(mapping)} tokens")
    return mapping


def get_token_slot_map() -> dict[str, dict]:
    """Get the tier token to slot mapping (lazy-loaded singleton)."""
    global _TOKEN_SLOT_MAP
    if _TOKEN_SLOT_MAP is None:
        _TOKEN_SLOT_MAP = _build_token_slot_mapping()
    return _TOKEN_SLOT_MAP


# Compatible items to tier mapping cache (lazy-loaded)
# Maps item_name (lowercase) -> tier_version
_COMPATIBLE_ITEMS_MAP: Optional[dict[str, str]] = None


def _build_compatible_items_mapping(expansion: str = "TBC") -> dict[str, str]:
    """
    Build a mapping from tier set item names (compatible items) to their tier version.

    Args:
        expansion: Expansion key in tokens.json (default "TBC")

    Returns:
        Dictionary mapping item_name (lowercase) -> tier_version
        e.g., {"warbringer greathelm": "Tier 4", "destroyer chestguard": "Tier 5"}
    """
    tokens_file = Path(__file__).resolve().parent.parent.parent.parent / "data" / "tokens.json"

    if not tokens_file.exists():
        logger.warning(f"Tier tokens file not found: {tokens_file}")
        return {}

    try:
        with open(tokens_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Failed to load tier tokens: {e}")
        return {}

    mapping = {}

    # Get expansion-specific data
    expansion_data = data.get(expansion)
    if not expansion_data:
        logger.warning(f"Expansion '{expansion}' not found in tokens.json")
        return {}

    for tier_group in expansion_data:
        tier_version = tier_group.get("tier_version", "Unknown")
        for token in tier_group.get("tokens", []):
            for compatible_item in token.get("compatible_items", []):
                # Handle string format (item name)
                if isinstance(compatible_item, str):
                    mapping[compatible_item.lower()] = tier_version

    logger.debug(f"Built compatible items mapping with {len(mapping)} items for {expansion}")
    return mapping


def get_compatible_items_map(expansion: str = "TBC") -> dict[str, str]:
    """Get the compatible items to tier mapping (lazy-loaded singleton)."""
    global _COMPATIBLE_ITEMS_MAP
    if _COMPATIBLE_ITEMS_MAP is None:
        _COMPATIBLE_ITEMS_MAP = _build_compatible_items_mapping(expansion)
    return _COMPATIBLE_ITEMS_MAP


def count_tier_tokens_for_raider(
    equipped: dict,
    compatible_items_map: dict[str, str] = None
) -> dict[str, int]:
    """
    Count how many tier set pieces a raider has equipped, grouped by tier.

    Args:
        equipped: Dictionary of equipped items (from cache structure)
                  e.g., {"head": {"item_name": "...", "ilvl": 120}, ...}
        compatible_items_map: Pre-built mapping from item names to tier versions.
                              If None, will be loaded automatically.

    Returns:
        Dictionary mapping tier_version -> count
        e.g., {"Tier 4": 2, "Tier 5": 3, "Tier 6": 0}
    """
    if compatible_items_map is None:
        compatible_items_map = get_compatible_items_map()

    # Initialize counts for all known tiers
    tier_counts = {
        "Tier 4": 0,
        "Tier 5": 0,
        "Tier 6": 0
    }

    # Handle error case
    if not equipped or "error" in equipped:
        return tier_counts

    # Iterate through all equipped slots
    for slot_name, slot_data in equipped.items():
        if not slot_data:
            continue

        # Handle multi-slot items (finger, trinket)
        if isinstance(slot_data, list):
            for item in slot_data:
                if item and item.get("item_name"):
                    item_name_lower = item["item_name"].lower()
                    tier = compatible_items_map.get(item_name_lower)
                    if tier:
                        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        else:
            # Single slot item
            if slot_data.get("item_name"):
                item_name_lower = slot_data["item_name"].lower()
                tier = compatible_items_map.get(item_name_lower)
                if tier:
                    tier_counts[tier] = tier_counts.get(tier, 0) + 1

    return tier_counts


# Slot name mapping for gear array indices
SLOT_NAME_MAP = {
    0: "head",
    1: "neck",
    2: "shoulder",
    14: "back",
    4: "chest",
    5: "waist",
    6: "legs",
    7: "feet",
    8: "wrist",
    9: "hands",
    10: "finger",     # First ring
    11: "finger",     # Second ring
    12: "trinket",    # First trinket
    13: "trinket",    # Second trinket
    15: "main_hand",
    16: "off_hand",
    17: "ranged"
}

# All slot names for iteration
ALL_SLOT_NAMES = [
    "head", "neck", "shoulder", "back", "chest",
    "waist", "legs", "feet", "wrist", "hands",
    "finger", "trinket",
    "main_hand", "off_hand",
    "ranged"
]




# Gear slot mapping for WoW (0-indexed as they appear in the gear array)
GEAR_SLOTS = {
    "head": [0],
    "neck": [1],
    "shoulder": [2],
    "back": [14],
    "chest": [4],
    "waist": [5],
    "legs": [6],
    "feet": [7],
    "wrist": [8],
    "hands": [9],
    "finger": [10, 11],
    "trinket": [12, 13],
    "main hand": [15, 16],
    "held in off-hand": [15, 16],
    "off hand": [15, 16],
    "one-hand": [15, 16],
    "two-hand": [15, 16],
    "shield": [15, 16],
    "ranged": [17],
    "relic": [17],
    "libram": [17],
    "totem": [17],
    "idol": [17],
    "thrown": [17],
}


# Slot groups for matching received items
SLOT_GROUPS = {
    "main hand": ["main hand", "one-hand", "two-hand", "held in off-hand", "off hand"],
    "main_hand": ["main hand", "one-hand", "two-hand", "held in off-hand", "off hand"],
    "one-hand": ["main hand", "one-hand", "two-hand", "held in off-hand", "off hand"],
    "two-hand": ["main hand", "one-hand", "two-hand", "held in off-hand", "off hand"],
    "held in off-hand": ["main hand", "one-hand", "two-hand", "held in off-hand", "off hand", "shield"],
    "off hand": ["main hand", "one-hand", "two-hand", "held in off-hand", "off hand", "shield"],
    "off_hand": ["main hand", "one-hand", "two-hand", "held in off-hand", "off hand", "shield"],
    "shield": ["held in off-hand", "off hand", "shield"],
    "finger": ["finger"],
    "trinket": ["trinket"],
    "ranged": ["ranged", "relic", "libram", "totem", "idol", "thrown"],
    "relic": ["ranged", "relic", "libram", "totem", "idol", "thrown"],
    "libram": ["ranged", "relic", "libram", "totem", "idol", "thrown"],
    "totem": ["ranged", "relic", "libram", "totem", "idol", "thrown"],
    "idol": ["ranged", "relic", "libram", "totem", "idol", "thrown"],
    "thrown": ["ranged", "relic", "libram", "totem", "idol", "thrown"],
}

# Blizzard API slot name mapping to internal slot names
BLIZZARD_SLOT_MAP = {
    "Head": "head",
    "Neck": "neck",
    "Shoulder": "shoulder",
    "Shoulders": "shoulder",  # Blizzard API uses plural
    "Back": "back",
    "Chest": "chest",
    "Waist": "waist",
    "Legs": "legs",
    "Feet": "feet",
    "Wrist": "wrist",
    "Hands": "hands",
    "Finger 1": "finger",
    "Finger 2": "finger",
    "Ring 1": "finger",  # Blizzard API may use Ring instead of Finger
    "Ring 2": "finger",
    "Trinket 1": "trinket",
    "Trinket 2": "trinket",
    "Main Hand": "main_hand",
    "Off Hand": "off_hand",
    "Ranged": "ranged",
    # Additional slot name variants that might appear
    "Shirt": None,  # Ignore cosmetic slots
    "Tabard": None,
}

# WCL Zone IDs by game version
TBC_ZONE_IDS = {1007, 1008, 1010, 1011, 1012, 1013}  # Kara, Gruul/Mag, SSC/TK, BT/Hyjal, ZA, Sunwell
ERA_ZONE_IDS = {1028, 1034, 1035, 1036}  # MC, BWL, AQ40, Naxx


def get_valid_zone_ids() -> set[int]:
    """Get valid WCL zone IDs for the current game version."""
    config = get_config_manager()
    client_version = config.get_wcl_client_version().strip().lower()

    if client_version in ("era", "fresh"):
        return ERA_ZONE_IDS
    else:  # tbc, tbc anniversary
        return TBC_ZONE_IDS


def get_reference_date() -> date:
    """
    Get the reference date for loot council calculations.

    Returns the configured reference date only when Pyrewood Developer Mode
    is enabled (for testing/development). Otherwise always returns today's date.
    """
    config = get_config_manager()

    # Only use reference date when Pyrewood Developer Mode is enabled
    if config.get_pyrewood_dev_mode():
        ref_date_str = config.get_reference_date()
        if ref_date_str:
            ref_date = datetime.strptime(ref_date_str, "%Y-%m-%d").date()
            logger.info(f"Using reference date from config (dev mode): {ref_date}")
            return ref_date

    ref_date = date.today()
    logger.info(f"Using today's date as reference: {ref_date}")
    return ref_date




def get_slot_indices_for_item(item_slot: str) -> Optional[list[int]]:
    """
    Get the gear array indices for a given item slot name.
    """
    if not item_slot:
        logger.warning("Empty item_slot provided")
        return None

    slot_key = item_slot.lower().strip()
    indices = GEAR_SLOTS.get(slot_key)
    logger.debug(f"Slot '{item_slot}' -> key '{slot_key}' -> indices {indices}")
    return indices


def get_slots_for_matching(item_slot: str) -> list[str]:
    """
    Get all slot names that should be considered when looking for received items.
    """
    if not item_slot:
        logger.warning("Empty item_slot provided for matching")
        return []

    slot_key = item_slot.lower().strip()
    slots = SLOT_GROUPS.get(slot_key, [slot_key])
    logger.debug(f"Slot '{item_slot}' matches slots: {slots}")
    return slots


def find_most_recent_raid_report(
    wcl_client: WarcraftLogsClient,
    character_name: str,
    server_slug: str,
    server_region: str,
    reference_date: date
) -> Optional[dict]:
    """
    Find the most recent raid report for a character.

    Uses the recentReports endpoint to efficiently fetch the character's
    recent raid logs in a single API call.

    Args:
        wcl_client: WarcraftLogs client
        character_name: Character name
        server_slug: Server slug (e.g., "pyrewood-village")
        server_region: Server region (e.g., "EU")
        reference_date: Date to search up to

    Returns:
        {"code": "abc123", "date": date(2025, 1, 15), "zone_id": 1013}
        or None if no reports found
    """
    logger.info(f"Finding most recent raid report for '{character_name}' on {server_slug}-{server_region} before {reference_date}")

    # Use higher limit in dev mode to access older historical reports
    config = get_config_manager()
    report_limit = 100 if config.get_pyrewood_dev_mode() else 50

    query = """
    query GetRecentReports($name: String!, $serverSlug: String!, $serverRegion: String!, $limit: Int!) {
        characterData {
            character(name: $name, serverSlug: $serverSlug, serverRegion: $serverRegion) {
                recentReports(limit: $limit) {
                    data {
                        code
                        startTime
                        zone {
                            id
                        }
                    }
                }
            }
        }
    }
    """

    try:
        result = wcl_client.query(query, {
            "name": character_name,
            "serverSlug": server_slug,
            "serverRegion": server_region,
            "limit": report_limit
        })

        character = result.get("characterData", {}).get("character")
        if not character:
            logger.warning(f"Character '{character_name}' not found on WCL")
            return None

        recent_reports = character.get("recentReports", {}).get("data", [])
        if not recent_reports:
            logger.warning(f"No reports found for '{character_name}'")
            return None

        logger.info(f"Found {len(recent_reports)} recent reports in single API call")

        # Get valid zone IDs for the current game version
        valid_zones = get_valid_zone_ids()

        # Reports are already sorted by date (most recent first)
        # Find the first report on or before the reference date from a valid zone
        for report in recent_reports:
            start_time = report.get("startTime")
            if not start_time:
                continue

            zone_id = report.get("zone", {}).get("id")

            # Skip reports from wrong game version
            if zone_id not in valid_zones:
                continue

            report_date = datetime.utcfromtimestamp(start_time / 1000).date()

            if report_date <= reference_date:
                report_code = report.get("code")

                logger.info(f"Selected report: {report_code} from {report_date} in zone {zone_id}")
                return {
                    "code": report_code,
                    "date": report_date,
                    "zone_id": zone_id
                }

        logger.warning(f"No valid reports found on or before {reference_date} for game version zones {valid_zones}")
        return None

    except Exception as e:
        logger.error(f"Error fetching recent reports: {type(e).__name__}: {str(e)}")
        return None


def extract_all_gear_from_report(
    wcl_client: WarcraftLogsClient,
    nexus_manager: NexusItemManager,
    report_code: str,
    character_name: str
) -> dict:
    """
    Extract all 18 gear slots from a WCL report for a character.

    Uses the gear array from the last boss kill in the report to get
    the most current gear setup.

    Args:
        wcl_client: WarcraftLogs client
        nexus_manager: Nexus item manager
        report_code: WCL report code
        character_name: Character name

    Returns:
        Dictionary mapping slot names to equipped items:
        {
            "head": {"item_name": "...", "ilvl": 159},
            "finger": [{"item_name": "...", "ilvl": 159}, {...}],
            ...
        }
        or {"error": "Character not found in log"}
    """
    logger.info(f"Extracting all gear from report {report_code} for '{character_name}'")

    report_query = """
    query GetReport($code: String!) {
        reportData {
            report(code: $code) {
                fights {
                    id
                    encounterID
                    kill
                }
            }
        }
    }
    """

    try:
        report_result = wcl_client.query(report_query, {"code": report_code})
        fights = report_result.get("reportData", {}).get("report", {}).get("fights", [])
        logger.debug(f"Report has {len(fights)} fights")

        # Find a boss kill to get gear from - use the LAST kill for most recent gear
        boss_kills = [f for f in fights if f.get("encounterID", 0) > 0 and f.get("kill", False)]
        if not boss_kills:
            logger.debug("No boss kills found, looking for any boss attempts")
            boss_kills = [f for f in fights if f.get("encounterID", 0) > 0]

        if not boss_kills:
            logger.warning("No boss encounters found in report")
            return {"error": "No boss encounters in report"}

        # Use the LAST boss kill instead of the first for most current gear
        fight_id = boss_kills[-1]["id"]
        encounter_id = boss_kills[-1].get("encounterID", "unknown")
        logger.debug(f"Using fight {fight_id} (encounter {encounter_id}) - last of {len(boss_kills)} boss kills")

        # Get gear from CombatantInfo
        gear_query = """
        query GetFightCombatantInfo($code: String!, $fightID: Int!) {
            reportData {
                report(code: $code) {
                    events(
                        fightIDs: [$fightID]
                        dataType: CombatantInfo
                        limit: 100
                    ) {
                        data
                    }
                    masterData {
                        actors(type: "Player") {
                            id
                            name
                        }
                    }
                }
            }
        }
        """

        gear_result = wcl_client.query(gear_query, {"code": report_code, "fightID": fight_id})
        gear_report = gear_result.get("reportData", {}).get("report", {})
        actors = gear_report.get("masterData", {}).get("actors", [])
        actor_map = {actor["id"]: actor for actor in actors}
        combatant_data = gear_report.get("events", {}).get("data", [])

        logger.debug(f"Found {len(actors)} actors and {len(combatant_data)} combatant info entries")

        # Find the character's gear
        for combatant in combatant_data:
            source_id = combatant.get("sourceID")
            actor = actor_map.get(source_id, {})
            actor_name = actor.get("name", "")

            if actor_name.lower() == character_name.lower():
                logger.debug(f"Found character '{actor_name}' (sourceID: {source_id})")
                gear = combatant.get("gear", [])
                logger.debug(f"Gear array length: {len(gear)}")
                logger.debug(f"Full gear array: {gear}")

                # Build result dict with all slots
                result = {}

                for slot_idx, slot_name in SLOT_NAME_MAP.items():
                    if slot_idx < len(gear):
                        item = gear[slot_idx]
                        item_id = item.get("id", 0)
                        item_level = item.get("itemLevel", 0)

                        logger.debug(f"Slot {slot_idx} ({slot_name}): item_id={item_id}, itemLevel={item_level}")

                        if item_id and item_id > 0:
                            item_name = nexus_manager.get_item_name(item_id)
                            if not item_name:
                                item_name = f"Unknown Item ({item_id})"

                            item_data = {"item_name": item_name, "ilvl": item_level}

                            # Multi-slot items (finger, trinket) go in lists
                            if slot_name in ["finger", "trinket"]:
                                if slot_name not in result:
                                    result[slot_name] = []
                                result[slot_name].append(item_data)
                            else:
                                result[slot_name] = item_data

                            logger.debug(f"  -> {item_name} ({item_level})")
                        else:
                            # Empty slot - only set if not already present (for multi-slots)
                            if slot_name not in ["finger", "trinket"]:
                                result[slot_name] = None
                            logger.debug(f"  -> Empty slot")
                    else:
                        logger.warning(f"Slot {slot_idx} ({slot_name}) exceeds gear array length {len(gear)}")
                        if slot_name not in ["finger", "trinket"]:
                            result[slot_name] = None

                # Ensure all slots are present
                for slot_name in ALL_SLOT_NAMES:
                    if slot_name not in result:
                        if slot_name in ["finger", "trinket"]:
                            result[slot_name] = []
                        else:
                            result[slot_name] = None

                logger.info(f"Extracted all gear for '{character_name}'")
                return result

        logger.warning(f"Character '{character_name}' not found in combatant data")
        logger.debug(f"Available characters: {[actor_map[c.get('sourceID', 0)].get('name', 'unknown') for c in combatant_data if c.get('sourceID') in actor_map]}")
        return {"error": "Character not found in log"}

    except Exception as e:
        logger.error(f"Error extracting gear from report: {type(e).__name__}: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return {"error": f"Error extracting gear: {str(e)}"}


def get_equipped_items(
    character_name: str,
    server_slug: str = None,
    server_region: str = None,
    reference_date: date = None
) -> dict:
    """
    Get all equipped items from a character's most recent raid log.

    Searches across all zones to find the character's most recent raid report,
    then extracts all equipped items from that log.

    Args:
        character_name: Character name to query
        server_slug: WCL server slug (defaults to config; forced to pyrewood-village in dev mode)
        server_region: WCL server region (defaults to config; forced to EU in dev mode)
        reference_date: Date to search up to (defaults to today or dev mode date)

    Returns:
        Dictionary mapping slot names to equipped items:
        {
            "head": {"item_name": "Cowl of the Grand Engineer", "ilvl": 159},
            "neck": {"item_name": "Choker of Vile Intent", "ilvl": 159},
            "shoulder": {"item_name": "Mantle of the Elven Kings", "ilvl": 154},
            ...
            "finger": [
                {"item_name": "Ring of Cryptic Dreams", "ilvl": 159},
                {"item_name": "Band of the Eternal Defender", "ilvl": 146}
            ],
            "trinket": [
                {"item_name": "Darkmoon Card: Crusade", "ilvl": 60},
                {"item_name": "Icon of Unyielding Courage", "ilvl": 159}
            ],
            "main_hand": {"item_name": "Hammer of the Naaru", "ilvl": 159},
            "off_hand": {"item_name": "Cosmic Infuser", "ilvl": 146},
            "ranged": {"item_name": "Wand of the Whispering Dead", "ilvl": 115}
        }

        Empty slots: None
        Errors: {"error": "No recent logs found"}
    """
    logger.info(f"=== Getting equipped items for '{character_name}' ===")

    # Initialize managers
    config = get_config_manager()
    wcl = WarcraftLogsClient()
    nexus = NexusItemManager()

    # Set defaults - dev mode forces pyrewood-village/EU, otherwise use passed values or config
    if config.get_pyrewood_dev_mode():
        server_slug = "pyrewood-village"
        server_region = "EU"
    else:
        server_slug = server_slug or config.get_wcl_server_slug()
        server_region = server_region or config.get_wcl_server_region()
    reference_date = reference_date or get_reference_date()

    logger.info(f"Server: {server_slug}-{server_region}, Reference date: {reference_date}")

    # Find most recent raid report
    report = find_most_recent_raid_report(
        wcl, character_name, server_slug, server_region, reference_date
    )

    if not report:
        logger.warning(f"No recent logs found for '{character_name}'")
        return {"error": "No recent logs found"}

    logger.info(f"Using report {report['code']} from {report['date']} in zone {report['zone_id']}")

    # Extract all gear
    equipped = extract_all_gear_from_report(
        wcl, nexus, report["code"], character_name
    )

    if "error" in equipped:
        logger.warning(f"Error extracting gear: {equipped['error']}")
        return equipped

    logger.info(f"Successfully extracted all gear for '{character_name}'")
    return equipped


def get_equipped_items_blizzard(
    character_name: str,
    server_slug: str = None,
    server_region: str = None
) -> dict:
    """
    Get all equipped items from a character via Blizzard API.

    Args:
        character_name: Character name to query
        server_slug: Server slug (defaults to config; forced to pyrewood-village in dev mode)
        server_region: Server region (defaults to config; forced to EU in dev mode)

    Returns:
        Dictionary mapping slot names to equipped items in same format as get_equipped_items():
        {
            "head": {"item_name": "Cowl of the Grand Engineer", "ilvl": 159},
            "finger": [{"item_name": "Ring of ...", "ilvl": 159}, {...}],
            ...
        }

        Empty slots: None
        Errors: {"error": "..."}
    """
    logger.info(f"=== Getting equipped items from Blizzard API for '{character_name}' ===")

    # Initialize managers
    config = get_config_manager()
    nexus = NexusItemManager()

    # Set defaults - dev modes force specific servers, otherwise use passed values or config
    if config.get_pyrewood_dev_mode():
        server_slug = "pyrewood-village"
        server_region = "EU"
    elif config.get_thunderstrike_dev_mode():
        server_slug = "thunderstrike"
        server_region = "EU"
    else:
        server_slug = server_slug or config.get_wcl_server_slug()
        server_region = server_region or config.get_wcl_server_region()

    logger.info(f"Server: {server_slug}-{server_region}")

    # Determine namespace based on dev mode
    # Thunderstrike dev mode uses TBC Anniversary namespace (profile-classic)
    # Otherwise use Classic Era namespace (profile-classic1x)
    if config.get_thunderstrike_dev_mode():
        namespace = f"profile-classic-{server_region.lower()}"
        logger.info(f"Using TBC Anniversary namespace: {namespace}")
    else:
        namespace = f"profile-classic1x-{server_region.lower()}"
        logger.info(f"Using Classic Era namespace: {namespace}")

    # Get OAuth token
    token = get_access_token()
    if not token:
        logger.error("Failed to get Blizzard API access token")
        return {"error": "Failed to get Blizzard API access token"}

    # Fetch gear from Blizzard API (character name must be lowercase)
    blizz_gear = fetch_character_gear_names(
        token,
        server_region.lower(),
        server_slug,
        character_name.lower(),
        namespace=namespace
    )

    if not blizz_gear:
        logger.warning(f"No gear data returned from Blizzard API for '{character_name}'")
        return {"error": "Character not found or no gear data"}

    logger.debug(f"Blizzard API returned gear for {len(blizz_gear)} slots")

    # Initialize result with empty slots
    result = {}
    for slot_name in ALL_SLOT_NAMES:
        if slot_name in ["finger", "trinket"]:
            result[slot_name] = []
        else:
            result[slot_name] = None

    # Convert Blizzard slots to internal format
    for blizz_slot, item_name in blizz_gear.items():
        cache_slot = BLIZZARD_SLOT_MAP.get(blizz_slot)

        if cache_slot is None:
            # Slot explicitly ignored (cosmetic like Shirt/Tabard) or unknown
            logger.debug(f"Ignoring Blizzard slot '{blizz_slot}'")
            continue

        # Lookup item ID and ilvl from Nexus
        item_id = nexus.get_item_id(item_name)
        ilvl = None
        if item_id:
            ilvl = nexus.get_item_level(item_id)
            logger.debug(f"Resolved '{item_name}' -> ID {item_id}, ilvl {ilvl}")
        else:
            logger.warning(f"Could not find item ID for '{item_name}' in Nexus database")

        item_data = {"item_name": item_name, "ilvl": ilvl}

        # Multi-slot items (finger, trinket) go in lists
        if cache_slot in ["finger", "trinket"]:
            result[cache_slot].append(item_data)
        else:
            result[cache_slot] = item_data

    logger.info(f"Successfully extracted gear from Blizzard API for '{character_name}'")
    return result


def get_equipped_items_for_source(
    character_name: str,
    api_source: str = None,
    server_slug: str = None,
    server_region: str = None,
    reference_date: date = None
) -> dict:
    """
    Get equipped items using the configured API source.

    Dispatcher function that routes to either Blizzard API or Warcraftlogs API
    based on configuration.

    Args:
        character_name: Character name to query
        api_source: "blizzard" or "warcraftlogs" (defaults to config setting)
        server_slug: Server slug (defaults to config)
        server_region: Server region (defaults to config)
        reference_date: Date to search up to (only used for WCL)

    Returns:
        Dictionary mapping slot names to equipped items (same format for both sources)
    """
    config = get_config_manager()
    api_source = api_source or config.get_currently_equipped_api_source()

    logger.info(f"Getting equipped items for '{character_name}' using {api_source} API")

    if api_source == "blizzard":
        return get_equipped_items_blizzard(
            character_name,
            server_slug=server_slug,
            server_region=server_region
        )
    else:
        return get_equipped_items(
            character_name,
            server_slug=server_slug,
            server_region=server_region,
            reference_date=reference_date
        )


def get_last_received_items(
    character_name: str,
    reference_date: date = None
) -> dict:
    """
    Get the most recent mainspec item received in each slot from TMB loot history.

    Args:
        character_name: Character name to query
        reference_date: Date to search up to (defaults to today or dev mode date)

    Returns:
        Dictionary mapping slot names to last received items:
        {
            "head": {
                "item_name": "Cowl of the Grand Engineer",
                "ilvl": 159,
                "received_at": date(2025, 1, 15)
            },
            "neck": None,
            "shoulder": {
                "item_name": "Mantle of the Elven Kings",
                "ilvl": 154,
                "received_at": date(2025, 1, 10)
            },
            ...
        }

        Returns None for slots with no received items.
        Only mainspec items included (is_offspec=False).
    """
    logger.info(f"=== Getting last received items for '{character_name}' ===")

    # Initialize managers
    tmb = TMBDataManager()
    nexus = NexusItemManager()
    reference_date = reference_date or get_reference_date()

    logger.info(f"Reference date: {reference_date}")

    # Get TMB received data
    received_df = tmb.get_raider_received()
    logger.debug(f"TMB data has {len(received_df)} raiders")

    # Find character
    char_row = received_df[
        received_df["name"].str.lower() == character_name.lower()
    ]

    if char_row.empty:
        logger.warning(f"Character '{character_name}' not found in TMB data")
        return {}  # Character not found, return empty dict

    logger.debug(f"Found character '{character_name}' in TMB data")

    # Process all slots
    result = {}

    for slot_name in ALL_SLOT_NAMES:
        last_item = find_last_received_for_slot(
            char_row, nexus, slot_name, reference_date
        )
        result[slot_name] = last_item

    logger.info(f"Successfully processed all slots for '{character_name}'")
    return result




def find_last_received_for_slot(
    char_row: pd.DataFrame,
    nexus_manager: NexusItemManager,
    slot_name: str,
    reference_date: date
) -> Optional[dict]:
    """
    Find the most recent item received in a slot for a character.

    Uses SLOT_GROUPS for weapon/ranged slot matching (e.g., "main_hand"
    matches one-hand, two-hand, etc.). Also recognizes tier tokens
    (e.g., "Helm of the Fallen Defender") as items for their target slots.

    Args:
        char_row: Single-row DataFrame with character's received data
        nexus_manager: Nexus item manager
        slot_name: Slot name (e.g., "head", "main_hand", "finger")
        reference_date: Date to search up to

    Returns:
        {"item_name": "...", "ilvl": 159, "received_at": date(...)}
        or None if no items found
    """
    logger.debug(f"Finding last received item in slot '{slot_name}'")

    received_list = char_row.iloc[0]["received"]
    logger.debug(f"Character has {len(received_list) if received_list else 0} received items")

    if not received_list:
        logger.debug(f"No received items")
        return None

    # Get slots to match (handles weapon/ranged slot groups)
    slots_to_match = get_slots_for_matching(slot_name)
    slots_to_match_lower = [s.lower() for s in slots_to_match]
    logger.debug(f"Matching against slots: {slots_to_match}")

    matching_items = []

    for item in received_list:
        # Skip offspec items
        if item.get("is_offspec", False):
            logger.debug(f"Skipping offspec item: {item.get('name', 'unknown')}")
            continue

        # Check received date
        received_at = item.get("received_at")
        if received_at is None:
            logger.debug(f"Skipping item with no received_at: {item.get('name', 'unknown')}")
            continue

        if isinstance(received_at, datetime):
            received_at = received_at.date()

        if received_at > reference_date:
            logger.debug(f"Skipping item received after reference date: {item.get('name', 'unknown')} on {received_at}")
            continue

        # Get item info
        item_id = item.get("item_id")
        item_name = item.get("name", "")

        if not item_id:
            logger.debug(f"Skipping item with no item_id: {item_name or 'unknown'}")
            continue

        # First, check if this item is a tier token
        token_slot_map = get_token_slot_map()
        token_info = token_slot_map.get(item_name.lower()) if item_name else None

        if token_info:
            # This is a tier token - use the token's slot and ilvl
            token_slot = token_info["slot"]
            token_ilvl = token_info["ilvl"]
            logger.debug(f"Item '{item_name}' is a tier token for slot '{token_slot}' (ilvl {token_ilvl})")
            if token_slot in slots_to_match_lower:
                logger.debug(f"Matched tier token: {item_name} (slot: {token_slot}, ilvl: {token_ilvl}) received on {received_at}")
                matching_items.append({
                    "item_name": item_name,
                    "ilvl": token_ilvl,
                    "received_at": received_at
                })
            continue  # Skip Nexus lookup for tokens

        # Not a tier token - use Nexus lookup
        item_data = nexus_manager.get_item(item_id)
        if not item_data:
            logger.debug(f"Skipping item not found in Nexus: {item_id}")
            continue

        # Check if slot matches
        item_slot_from_nexus = item_data.get("slot", "")
        if item_slot_from_nexus and item_slot_from_nexus.lower() in slots_to_match_lower:
            item_name = item_name or nexus_manager.get_item_name(item_id)
            ilvl = nexus_manager.get_item_level(item_id)

            logger.debug(f"Matched item: {item_name} (slot: {item_slot_from_nexus}) received on {received_at}")
            matching_items.append({
                "item_name": item_name,
                "ilvl": ilvl,
                "received_at": received_at
            })
        else:
            logger.debug(f"Item slot '{item_slot_from_nexus}' not in match list: {item_name or 'unknown'}")

    if not matching_items:
        logger.debug(f"No matching items found in slot '{slot_name}'")
        return None

    # Return most recent
    most_recent = max(matching_items, key=lambda x: x["received_at"])
    logger.debug(f"Most recent match: {most_recent['item_name']} on {most_recent['received_at']}")
    return most_recent


def get_cache_info() -> Optional[dict]:
    """
    Get information about the raider gear cache.

    Returns:
        Dictionary with cache info:
        {
            "exists": True,
            "created_at": datetime(2025, 1, 24, 10, 30, 0),
            "age_hours": 2.5,
            "raider_count": 25,
            "api_source": "blizzard"
        }
        or {"exists": False} if no cache exists.
    """
    paths = get_path_manager()
    cache_path = paths.get_raider_gear_cache_path()

    if not cache_path.exists():
        return {"exists": False}

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        created_at_str = data.get("created_at", "")
        if created_at_str:
            created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
            age_hours = (datetime.now(created_at.tzinfo) - created_at).total_seconds() / 3600
        else:
            created_at = None
            age_hours = None

        raider_count = len(data.get("raiders", {}))
        api_source = data.get("api_source", "warcraftlogs")  # Default for legacy caches

        return {
            "exists": True,
            "created_at": created_at,
            "age_hours": age_hours,
            "raider_count": raider_count,
            "api_source": api_source
        }
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Error reading cache info: {e}")
        return {"exists": False}


def get_cached_raider_gear() -> Optional[dict]:
    """
    Load the cached raider gear data.

    Returns:
        The full cache dictionary, or None if no cache exists or loading fails.
    """
    paths = get_path_manager()
    cache_path = paths.get_raider_gear_cache_path()

    if not cache_path.exists():
        return None

    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Error loading raider gear cache: {e}")
        return None


def cache_all_raiders_gear(
    progress_callback: Optional[callable] = None,
    server_slug: str = None,
    server_region: str = None,
    api_source: str = None
) -> Path:
    """
    Cache equipped items and last received items for all raiders.

    Fetches data from WCL or Blizzard API for each raider (based on api_source)
    and saves to a JSON file in the cache directory.

    Args:
        progress_callback: Optional function called with (current, total, raider_name)
                          for progress updates
        server_slug: Server slug (defaults to config value)
        server_region: Server region (defaults to config value)
        api_source: "blizzard" or "warcraftlogs" (defaults to config setting)

    Returns:
        Path to the saved cache file

    Cache structure:
    {
        "created_at": "2025-01-24T10:30:00Z",
        "server_slug": "pyrewood-village",
        "server_region": "EU",
        "game_version": "TBC Anniversary",
        "api_source": "blizzard",
        "raiders": {
            "RaiderName": {
                "equipped": {...}  # from get_equipped_items_for_source()
            },
            ...
        }
    }
    """
    logger.info("=== Starting cache_all_raiders_gear ===")

    # Initialize managers
    config = get_config_manager()
    paths = get_path_manager()
    tmb = TMBDataManager()

    # Set defaults - dev mode forces pyrewood-village/EU, otherwise use passed values or config
    if config.get_pyrewood_dev_mode():
        server_slug = "pyrewood-village"
        server_region = "EU"
    else:
        server_slug = server_slug or config.get_wcl_server_slug()
        server_region = server_region or config.get_wcl_server_region()

    logger.info(f"Server: {server_slug}-{server_region}")

    # Get API source from config if not provided
    api_source = api_source or config.get_currently_equipped_api_source()
    logger.info(f"Using API source: {api_source}")

    # Get all raiders from TMB
    try:
        profiles_df = tmb.get_raider_profiles()
    except Exception as e:
        logger.error(f"Failed to get raider profiles: {e}")
        raise

    raider_names = profiles_df["name"].tolist()
    total_raiders = len(raider_names)
    logger.info(f"Found {total_raiders} raiders to cache")

    # Build cache data
    cache_data = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "server_slug": server_slug,
        "server_region": server_region,
        "game_version": config.get_wcl_client_version(),
        "api_source": api_source,
        "raiders": {}
    }

    reference_date = get_reference_date()

    # Load tier token mapping once for all raiders
    compatible_items_map = get_compatible_items_map()

    for i, raider_name in enumerate(raider_names):
        logger.info(f"Processing raider {i+1}/{total_raiders}: {raider_name}")

        if progress_callback:
            try:
                progress_callback(i, total_raiders, raider_name)
            except Exception as e:
                logger.warning(f"Progress callback error: {e}")

        # Get equipped items using configured API source
        equipped = get_equipped_items_for_source(
            raider_name,
            api_source=api_source,
            server_slug=server_slug,
            server_region=server_region,
            reference_date=reference_date
        )

        # Count tier tokens for this raider
        tier_token_counts = count_tier_tokens_for_raider(equipped, compatible_items_map)

        cache_data["raiders"][raider_name] = {
            "equipped": equipped,
            "tier_token_counts": tier_token_counts
        }

    # Final progress callback
    if progress_callback:
        try:
            progress_callback(total_raiders, total_raiders, "Complete")
        except Exception as e:
            logger.warning(f"Progress callback error: {e}")

    # Save cache
    cache_path = paths.get_raider_gear_cache_path()
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2, default=str, ensure_ascii=False)

    logger.info(f"Cache saved to {cache_path}")
    return cache_path


if __name__ == "__main__":
    import json

    # Example usage
    if len(sys.argv) > 1:
        character = sys.argv[1]
    else:
        character = "Exampleraider"

    print(f"\n=== Equipped Items for {character} ===")
    equipped = get_equipped_items(character)
    print(json.dumps(equipped, indent=2, default=str))

    print(f"\n=== Last Received Items for {character} ===")
    received = get_last_received_items(character)
    print(json.dumps(received, indent=2, default=str))