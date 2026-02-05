"""
Get Item Candidates Tool for API-based Loot Council

This module generates a compact, LLM-ready prompt for single-item loot decisions.
It identifies eligible candidates from TMB data and returns a formatted prompt string
for LLM-based loot council decisions.

Returns: Formatted prompt string ready for direct API call
"""

import json
import pickle
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional, Dict, List
import pandas as pd

from ..core.paths import get_path_manager
from ..core.config import get_config_manager
from ..services.tmb_manager import TMBDataManager
from ..services.nexus_manager import NexusItemManager
from ..services.parse_cache import get_cached_parse, cache_parse, is_raider_cached, ParseData
from .fetching_current_items import get_cached_raider_gear, find_last_received_for_slot

# Get PathManager instance
paths = get_path_manager()

# Module-level cache for tier token names
_tier_token_names_cache: Optional[set] = None

# Module-level cache for exchange items (TBC)
_exchange_items_tbc_cache: Optional[Dict[str, Dict]] = None


def get_tier_token_names() -> set:
    """
    Get a set of all tier token names for efficient lookup.
    Loads from tokens.json on first call and caches the result.

    Returns:
        Set of lowercase tier token names (e.g., "helm of the fallen defender")
    """
    global _tier_token_names_cache

    if _tier_token_names_cache is not None:
        return _tier_token_names_cache

    _tier_token_names_cache = set()
    tokens_file = paths.get_tbc_tokens_path()

    if not tokens_file or not tokens_file.exists():
        return _tier_token_names_cache

    try:
        with open(tokens_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Structure: {"TBC": [{"tier_version": "...", "tokens": [...]}], "exchange_items_tbc": {...}}
        # Only process tier token keys (lists), skip exchange_items_* keys (dicts)
        for expansion_key, tier_groups in data.items():
            # Skip exchange items - they have a different structure
            if expansion_key.startswith("exchange_items"):
                continue
            if not isinstance(tier_groups, list):
                continue
            for tier_group in tier_groups:
                for token in tier_group.get("tokens", []):
                    token_name = token.get("token_name", "")
                    if token_name:
                        _tier_token_names_cache.add(token_name.lower())
    except (json.JSONDecodeError, IOError, KeyError):
        pass

    return _tier_token_names_cache


def is_tier_token(item_name: str) -> bool:
    """
    Check if an item name is a tier token.

    Args:
        item_name: Name of the item to check

    Returns:
        True if the item is a tier token, False otherwise
    """
    return item_name.lower() in get_tier_token_names()


def get_exchange_items_tbc() -> Dict[str, Dict]:
    """
    Load exchange items mapping from tokens.json.

    Returns:
        Dict of {source_name: {"ilvl": int, "items": [str, ...]}}
    """
    global _exchange_items_tbc_cache

    if _exchange_items_tbc_cache is not None:
        return _exchange_items_tbc_cache

    _exchange_items_tbc_cache = {}
    tokens_file = paths.get_tbc_tokens_path()

    if not tokens_file or not tokens_file.exists():
        return _exchange_items_tbc_cache

    try:
        with open(tokens_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        _exchange_items_tbc_cache = data.get("exchange_items_tbc", {})
    except (json.JSONDecodeError, IOError):
        pass

    return _exchange_items_tbc_cache


def is_exchange_item(item_name: str) -> bool:
    """
    Check if item is an exchange source item.

    Args:
        item_name: Name of the item to check

    Returns:
        True if the item is an exchange source item, False otherwise
    """
    return item_name.lower() in {k.lower() for k in get_exchange_items_tbc().keys()}


def find_exchange_item(item_name: str) -> Optional[Dict]:
    """
    Find exchange item by source name OR exchangeable item name.

    Args:
        item_name: Name of the source item or any exchangeable item

    Returns:
        Dict with source_name, ilvl, and items list, or None if not found
    """
    exchange_items_tbc = get_exchange_items_tbc()
    item_lower = item_name.lower()

    for source_name, data in exchange_items_tbc.items():
        exchangeable_items = data.get("items", [])
        ilvl = data.get("ilvl")

        # Check if it matches the source item name
        if source_name.lower() == item_lower:
            return {"source_name": source_name, "ilvl": ilvl, "items": exchangeable_items}

        # Check if it matches any of the exchangeable items
        for ex_item in exchangeable_items:
            if ex_item.lower() == item_lower:
                return {"source_name": source_name, "ilvl": ilvl, "items": exchangeable_items}

    return None


@dataclass
class CheckingCandidatesResult:
    """Container for checking candidates tool output."""
    header: str
    item_id: int
    item_slot: Optional[str]
    item_ilvl: Optional[int]
    item_note: Optional[str]
    candidates_df: pd.DataFrame
    tier_bonuses_df: Optional[pd.DataFrame] = None
    tier_version: Optional[str] = None


def find_tier_token_with_version(item_name: str) -> Optional[tuple]:
    """
    Check if the given item is a tier token and return its data with tier version.

    Args:
        item_name: Name of the item to check

    Returns:
        Tuple of (token_dict, tier_version) if found, None otherwise
        e.g., ({"token_name": "Helm...", ...}, "Tier 5")
    """
    tokens_file = paths.get_tbc_tokens_path()

    if not tokens_file or not tokens_file.exists():
        return None

    with open(tokens_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    item_lower = item_name.lower()

    # Handle structure: {"TBC": [{"tier_version": "...", "tokens": [...]}]}
    # Skip exchange_items keys - they have a different structure
    for expansion_key, tier_groups in data.items():
        if expansion_key.startswith("exchange_items"):
            continue
        if not isinstance(tier_groups, list):
            continue
        for tier_group in tier_groups:
            tier_version = tier_group.get("tier_version")
            for token in tier_group.get("tokens", []):
                # Check if it matches the token name
                if token.get("token_name", "").lower() == item_lower:
                    return (token, tier_version)

                # Check if it matches any of the compatible items
                # compatible_items is a list of strings
                for compatible_item in token.get("compatible_items", []):
                    if isinstance(compatible_item, str):
                        if compatible_item.lower() == item_lower:
                            return (token, tier_version)

    return None


def get_tier_set_bonuses(token: Dict) -> pd.DataFrame:
    """
    Extract tier set bonuses from a tier token into a DataFrame.

    Args:
        token: Tier token dictionary

    Returns:
        DataFrame with columns: Item Name, Class, Role, Set Name, 2pc Bonus, 4pc Bonus
        Returns DataFrame with just Item Name if compatible_items are strings.
    """
    bonuses_data = []

    for compatible_item in token.get("compatible_items", []):
        # Handle both string and dict formats for compatible_items
        if isinstance(compatible_item, str):
            # Simple string format - just the item name
            bonuses_data.append({
                "Item Name": compatible_item,
                "Class": "",
                "Role": "",
                "Set Name": "",
                "2pc Bonus": "",
                "4pc Bonus": ""
            })
        else:
            # Dict format with full details
            set_bonuses = compatible_item.get("set_bonuses", {})
            bonuses_data.append({
                "Item Name": compatible_item.get("item_name", ""),
                "Class": compatible_item.get("class", ""),
                "Role": compatible_item.get("role", ""),
                "Set Name": compatible_item.get("set_name", ""),
                "2pc Bonus": set_bonuses.get("2pc", ""),
                "4pc Bonus": set_bonuses.get("4pc", "")
            })

    return pd.DataFrame(bonuses_data)


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
            return datetime.strptime(ref_date_str, "%Y-%m-%d").date()

    return date.today()


def calculate_attendance_percentage(
    attendance_df: pd.DataFrame,
    character_name: str,
    reference_date: date,
    lookback_days: int = 60
) -> float:
    """
    Calculate attendance percentage for a character over a given period.

    Args:
        attendance_df: DataFrame with columns: raid_date, raid_name, character_name, credit, remark
        character_name: Name of the character to calculate attendance for
        reference_date: The reference date to calculate from
        lookback_days: Number of days to look back (default 60)

    Returns:
        Attendance percentage as a float (0-100)
    """
    start_date = reference_date - timedelta(days=lookback_days)

    # Filter attendance to the date range
    period_attendance = attendance_df[
        (attendance_df["raid_date"] >= start_date) &
        (attendance_df["raid_date"] <= reference_date)
    ]

    if period_attendance.empty:
        return 0.0

    # Get total possible raids (unique raid dates)
    total_raids = period_attendance["raid_date"].nunique()

    if total_raids == 0:
        return 0.0

    # Get character's attendance with credit weighting
    char_attendance = period_attendance[
        period_attendance["character_name"].str.lower() == character_name.lower()
    ]

    # Sum up credits (accounts for partial attendance)
    total_credit = char_attendance["credit"].sum()

    # Calculate percentage
    return (total_credit / total_raids) * 100


def count_recent_loot(
    received_df: pd.DataFrame,
    character_name: str,
    reference_date: date,
    lookback_days: int = 14
) -> int:
    """
    Count how many main-spec items a character has received recently.

    Args:
        received_df: DataFrame with columns: name, received (list of dicts)
        character_name: Name of the character
        reference_date: The reference date to calculate from
        lookback_days: Number of days to look back (default 14)

    Returns:
        Count of main-spec items received
    """
    start_date = reference_date - timedelta(days=lookback_days)

    # Find the character's received data
    char_row = received_df[
        received_df["name"].str.lower() == character_name.lower()
    ]

    if char_row.empty:
        return 0

    received_list = char_row.iloc[0]["received"]

    # Count MS loot received in the date range
    count = 0
    for item in received_list:
        # Skip offspec items
        if item.get("is_offspec", False):
            continue

        received_at = item.get("received_at")
        if received_at is None:
            continue

        # Handle both date and datetime objects
        if isinstance(received_at, datetime):
            received_at = received_at.date()

        if start_date <= received_at <= reference_date:
            count += 1

    return count


def get_item_note(
    item_notes_df: pd.DataFrame,
    item_id: int,
    item_name: str
) -> Optional[str]:
    """
    Get the priority note for an item if it exists.

    Args:
        item_notes_df: DataFrame with columns: id, name, instance_name, tier, prio_note
        item_id: The item ID to look up
        item_name: The item name (fallback for matching)

    Returns:
        Priority note string or None
    """
    # Try to find by ID first
    note_row = item_notes_df[item_notes_df["id"] == item_id]

    if note_row.empty:
        # Fallback to name matching
        note_row = item_notes_df[
            item_notes_df["name"].str.lower() == item_name.lower()
        ]

    if not note_row.empty:
        prio_note = note_row.iloc[0].get("prio_note")
        if prio_note and pd.notna(prio_note) and str(prio_note).strip():
            return str(prio_note)

    return None


def generate_checking_candidates(item_name: str) -> CheckingCandidatesResult:
    """
    Generate a list of eligible candidates for the given item.

    This is the first stage of loot decision - identifying who wants the item
    and their basic info without making expensive WCL API calls.

    Args:
        item_name: Name of the item to analyze

    Returns:
        CheckingCandidatesResult containing header, item info, and candidates DataFrame

    Raises:
        ValueError: If the item is not found
    """
    # Initialize managers
    tmb = TMBDataManager()
    nexus = NexusItemManager()

    # Get reference date
    reference_date = get_reference_date()

    # Check if client version is TBC and if this is a tier token
    config = get_config_manager()
    client_version = config.get_wcl_client_version().strip().lower()
    tier_token = None
    tier_bonuses_df = None
    tier_version = None
    item_ids_to_check = []

    # Check for tier tokens first (TBC only)
    exchange_item = None
    if client_version in ["tbc", "tbc anniversary"]:
        result = find_tier_token_with_version(item_name)
        if result:
            tier_token, tier_version = result

        if tier_token:
            # Get tier bonuses for display
            tier_bonuses_df = get_tier_set_bonuses(tier_token)

            # Build list of item IDs to check (token + all compatible items)
            token_id = nexus.get_item_id(tier_token["token_name"])
            if token_id:
                item_ids_to_check.append(token_id)

            for compatible_item in tier_token.get("compatible_items", []):
                # Handle both string and dict formats
                if isinstance(compatible_item, str):
                    item_name_to_check = compatible_item
                else:
                    item_name_to_check = compatible_item.get("item_name", "")
                compatible_id = nexus.get_item_id(item_name_to_check)
                if compatible_id:
                    item_ids_to_check.append(compatible_id)

        # Check for exchange items (non-tier items with sub-items)
        if not tier_token:
            exchange_item = find_exchange_item(item_name)
            if exchange_item:
                # Build list of item IDs to check (source + all exchangeable items)
                source_id = nexus.get_item_id(exchange_item["source_name"])
                if source_id:
                    item_ids_to_check.append(source_id)

                for ex_item_name in exchange_item["items"]:
                    ex_id = nexus.get_item_id(ex_item_name)
                    if ex_id:
                        item_ids_to_check.append(ex_id)

    # Look up item in Nexus (for non-tier tokens or fallback)
    item_id = nexus.get_item_id(item_name)
    if item_id is None:
        raise ValueError(f"Item '{item_name}' not found in item database")

    # If not a tier token or exchange item, just check the single item
    if not item_ids_to_check:
        item_ids_to_check = [item_id]

    # Use ilvl and slot from token/exchange data, otherwise use Nexus
    if tier_token:
        item_ilvl = tier_token.get("ilvl")
        item_slot = tier_token.get("slot")
    elif exchange_item:
        item_ilvl = exchange_item.get("ilvl")
        item_slot = nexus.get_item_slot(item_id)  # Get slot from Nexus for exchange items
    else:
        item_ilvl = nexus.get_item_level(item_id)
        item_slot = nexus.get_item_slot(item_id)
    canonical_name = nexus.get_item_name(item_id)

    # Build header
    ilvl_str = f"ilvl {item_ilvl}" if item_ilvl else "ilvl ?"
    slot_str = item_slot if item_slot else "Unknown Slot"
    tier_note = ""
    if tier_token:
        tier_note = " [TIER TOKEN]"
    elif exchange_item:
        tier_note = " [EXCHANGE ITEM]"
    header = f"Candidates for: {canonical_name} ({ilvl_str}) ({slot_str}){tier_note}"

    # Get item note from TMB
    item_notes_df = tmb.get_item_notes()
    item_note = get_item_note(item_notes_df, item_id, canonical_name)

    # Get wishlists and find raiders who want this item
    wishlists_df = tmb.get_raider_wishlists()
    profiles_df = tmb.get_raider_profiles()
    received_df = tmb.get_raider_received()
    attendance_df = tmb.get_attendance()

    # Find raiders who have this item on their wishlist and haven't received it
    eligible_raiders = []

    for _, row in wishlists_df.iterrows():
        raider_name = row["name"]
        wishlist = row["wishlist"]

        for wish_item in wishlist:
            # Check if wishlist item matches any of the IDs we're looking for
            if wish_item["item_id"] in item_ids_to_check:
                # Check if not received (as of reference_date)
                received_at = wish_item.get("received_at")

                # Skip if already received before or on reference date
                if received_at is not None:
                    if isinstance(received_at, datetime):
                        received_at = received_at.date()
                    if received_at <= reference_date:
                        continue

                # Also check is_received flag
                if wish_item.get("is_received", False):
                    # Double-check with date if available
                    if received_at is None:
                        continue

                # Check if raider is an alt
                profile = profiles_df[profiles_df["name"].str.lower() == raider_name.lower()]
                is_alt = False
                if not profile.empty:
                    is_alt = profile.iloc[0].get("is_alt", False)

                eligible_raiders.append({
                    "name": raider_name,
                    "wishlist_order": wish_item["order"],
                    "is_offspec": wish_item.get("is_offspec", False),
                    "is_alt": is_alt
                })
                break  # Only count once per raider

    # Filter out alts if Alt Status is disabled
    config = get_config_manager()
    if not config.get_show_alt_status():
        eligible_raiders = [r for r in eligible_raiders if not r["is_alt"]]

    if not eligible_raiders:
        empty_df = pd.DataFrame(columns=[
            "Raider Name", "Class/Spec", "Role", "Spec Type", "Is Alt?",
            "Wishlist Order", "Attendance %", "Recent Loot"
        ])
        return CheckingCandidatesResult(
            header=header,
            item_id=item_id,
            item_slot=item_slot,
            item_ilvl=item_ilvl,
            item_note=item_note,
            candidates_df=empty_df,
            tier_bonuses_df=tier_bonuses_df,
            tier_version=tier_version
        )

    # Build candidates data
    candidates_data = []

    for raider in eligible_raiders:
        raider_name = raider["name"]

        # Get profile info
        profile = profiles_df[profiles_df["name"].str.lower() == raider_name.lower()]

        if not profile.empty:
            profile_row = profile.iloc[0]
            class_name = profile_row.get("class", "Unknown")
            spec = profile_row.get("spec", "Unknown")
            class_spec = f"{class_name}/{spec}"
            role = profile_row.get("archetype", "Unknown")
        else:
            class_spec = "Unknown"
            role = "Unknown"

        # Calculate attendance (configurable via config, default 60 days)
        attendance_lookback = config.get_attendance_lookback_days()
        attendance_pct = calculate_attendance_percentage(
            attendance_df, raider_name, reference_date, lookback_days=attendance_lookback
        )

        # Count recent MS loot (configurable via config, default 14 days)
        loot_lookback = config.get_loot_lookback_days()
        recent_loot = count_recent_loot(
            received_df, raider_name, reference_date, lookback_days=loot_lookback
        )

        candidates_data.append({
            "Raider Name": raider_name,
            "Class/Spec": class_spec,
            "Role": role,
            "Spec Type": "Offspec" if raider["is_offspec"] else "Mainspec",
            "Is Alt?": raider["is_alt"],
            "Wishlist Order": raider["wishlist_order"],
            "Attendance %": round(attendance_pct, 1),
            "Recent Loot": recent_loot,
        })

    # Create DataFrame and sort by wishlist order
    candidates_df = pd.DataFrame(candidates_data)
    candidates_df = candidates_df.sort_values("Wishlist Order").reset_index(drop=True)

    return CheckingCandidatesResult(
        header=header,
        item_id=item_id,
        item_slot=item_slot,
        item_ilvl=item_ilvl,
        item_note=item_note,
        candidates_df=candidates_df,
        tier_bonuses_df=tier_bonuses_df,
        tier_version=tier_version
    )


def load_raider_notes() -> dict:
    """Load raider notes from JSON file."""
    notes_path = paths.get_raider_notes_path()
    if not notes_path.exists():
        return {}
    try:
        with open(notes_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def normalize_slot_for_cache(nexus_slot: str) -> Optional[str]:
    """
    Convert Nexus slot name to cache slot name.

    Args:
        nexus_slot: Slot name from Nexus (e.g., "Head", "One-Hand")

    Returns:
        Cache slot name (e.g., "head", "main_hand") or None if unmapped
    """
    if not nexus_slot:
        return None

    slot_lower = nexus_slot.lower()

    # Direct mappings
    direct_slots = ["head", "neck", "shoulder", "back", "chest",
                    "waist", "legs", "feet", "wrist", "hands",
                    "finger", "trinket", "ranged"]
    if slot_lower in direct_slots:
        return slot_lower

    # Weapon slots -> main_hand
    if slot_lower in ["main hand", "one-hand", "two-hand"]:
        return "main_hand"

    # Off-hand slots
    if slot_lower in ["held in off-hand", "off hand", "shield"]:
        return "off_hand"

    # Ranged variants
    if slot_lower in ["relic", "libram", "totem", "idol", "thrown"]:
        return "ranged"

    return None


def get_equipped_ilvl_from_cache(
    raider_name: str,
    slot_name: str,
    cache_data: Optional[dict]
) -> Optional[int]:
    """
    Get the equipped item level for a raider in a specific slot from cache.

    Args:
        raider_name: Name of the raider
        slot_name: Cache-normalized slot name (e.g., "head", "main_hand")
        cache_data: The loaded cache dictionary

    Returns:
        The equipped item level, or None if not found
    """
    if not cache_data or not slot_name:
        return None

    raiders = cache_data.get("raiders", {})

    # Try exact match first, then case-insensitive
    raider_data = raiders.get(raider_name)
    if not raider_data:
        # Case-insensitive fallback
        for name, data in raiders.items():
            if name.lower() == raider_name.lower():
                raider_data = data
                break

    if not raider_data:
        return None

    equipped = raider_data.get("equipped", {})

    # Check for error in equipped data
    if "error" in equipped:
        return None

    slot_data = equipped.get(slot_name)

    if not slot_data:
        return None

    # Handle multi-slot items (finger, trinket) - return highest ilvl
    if isinstance(slot_data, list):
        ilvls = [item.get("ilvl") for item in slot_data if item and item.get("ilvl")]
        return max(ilvls) if ilvls else None

    return slot_data.get("ilvl")


def get_equipped_ilvls_for_slot(
    raider_name: str,
    nexus_slot: str,
    cache_data: Optional[dict]
) -> Optional[List[int]]:
    """
    Get all equipped item levels for a raider's slot(s).

    For dual-slot items (rings, trinkets) returns both equipped ilvls.
    For one-hand weapons, checks if dual-wielding and returns both if so.
    For single-slot items, returns a list with one ilvl.

    Args:
        raider_name: Name of the raider
        nexus_slot: Original Nexus slot name (e.g., "Finger", "One-Hand")
        cache_data: The loaded cache dictionary

    Returns:
        List of equipped ilvls (1-2 items), or None if no equipped data
    """
    if not cache_data or not nexus_slot:
        return None

    raiders = cache_data.get("raiders", {})

    # Find raider data (case-insensitive)
    raider_data = raiders.get(raider_name)
    if not raider_data:
        for name, data in raiders.items():
            if name.lower() == raider_name.lower():
                raider_data = data
                break

    if not raider_data:
        return None

    equipped = raider_data.get("equipped", {})

    if "error" in equipped:
        return None

    slot_lower = nexus_slot.lower()

    # Handle dual-slot items: finger, trinket
    if slot_lower in ["finger", "trinket"]:
        slot_data = equipped.get(slot_lower)
        if not slot_data or not isinstance(slot_data, list):
            return None
        ilvls = [item.get("ilvl") for item in slot_data if item and item.get("ilvl")]
        return ilvls if ilvls else None

    # Handle one-hand weapons - check for dual-wield
    if slot_lower == "one-hand":
        main_hand = equipped.get("main_hand")
        off_hand = equipped.get("off_hand")

        ilvls = []
        if main_hand and main_hand.get("ilvl"):
            ilvls.append(main_hand.get("ilvl"))
        if off_hand and off_hand.get("ilvl"):
            ilvls.append(off_hand.get("ilvl"))

        return ilvls if ilvls else None

    # Handle two-hand weapons - only main_hand slot
    if slot_lower == "two-hand":
        main_hand = equipped.get("main_hand")
        if main_hand and main_hand.get("ilvl"):
            return [main_hand.get("ilvl")]
        return None

    # Handle main hand slot (could be 1H or 2H equipped)
    if slot_lower == "main hand":
        main_hand = equipped.get("main_hand")
        if main_hand and main_hand.get("ilvl"):
            return [main_hand.get("ilvl")]
        return None

    # Handle off-hand items (held in off-hand, shield, off hand)
    if slot_lower in ["held in off-hand", "off hand", "shield"]:
        off_hand = equipped.get("off_hand")
        if off_hand and off_hand.get("ilvl"):
            return [off_hand.get("ilvl")]
        return None

    # Handle ranged variants
    if slot_lower in ["ranged", "relic", "libram", "totem", "idol", "thrown"]:
        ranged = equipped.get("ranged")
        if ranged and ranged.get("ilvl"):
            return [ranged.get("ilvl")]
        return None

    # Handle single-slot armor items
    cache_slot = normalize_slot_for_cache(nexus_slot)
    if cache_slot:
        slot_data = equipped.get(cache_slot)
        if slot_data and slot_data.get("ilvl"):
            return [slot_data.get("ilvl")]

    return None


def get_or_fetch_parse(
    raider_name: str,
    zone_id: int,
    server_slug: str,
    server_region: str,
    archetype: Optional[str] = None
) -> Optional[ParseData]:
    """
    Get parse data for a raider, fetching from WCL if not cached.

    Args:
        raider_name: Name of the raider
        zone_id: WarcraftLogs zone ID
        server_slug: Server slug for WCL
        server_region: Server region for WCL
        archetype: Character archetype (DPS/Tank/Healer) to determine metric

    Returns:
        ParseData if found, None otherwise
    """
    from ..services.wcl_client import WarcraftLogsClient
    from .fetching_parses import get_raider_parses, get_metric_from_archetype

    # Check cache first
    if is_raider_cached(zone_id, raider_name):
        return get_cached_parse(zone_id, raider_name)

    # Validate required parameters
    if not server_slug or not server_region:
        # Can't fetch without server info, cache as None
        cache_parse(zone_id, raider_name, None, None)
        return get_cached_parse(zone_id, raider_name)

    # Fetch from WCL
    try:
        wcl = WarcraftLogsClient()
        metric = get_metric_from_archetype(archetype)

        parses = get_raider_parses(wcl, raider_name, server_slug, server_region, zone_id, metric)

        # Cache result (even if None values)
        cache_parse(zone_id, raider_name, parses.get("best_avg"), parses.get("median_avg"))

        return get_cached_parse(zone_id, raider_name)
    except Exception as e:
        # On any error, cache None values to avoid repeated failed lookups
        import logging
        logging.getLogger(__name__).warning(f"Failed to fetch parse for {raider_name}: {e}")
        cache_parse(zone_id, raider_name, None, None)
        return get_cached_parse(zone_id, raider_name)


def get_guild_policy_summary() -> str:
    """
    Get a condensed version of the guild policy for inclusion in prompts.
    Returns first 500 chars or key rules if policy is longer.
    """
    policy_path = paths.get_guild_policy_path()
    if not policy_path.exists():
        return "No guild policy found."

    policy_text = policy_path.read_text(encoding='utf-8')

    # If policy is short, return it all
    if len(policy_text) <= 800:
        return policy_text

    # Otherwise truncate with indicator
    return policy_text[:800] + "\n... (policy truncated for brevity)"


# Rule templates for simple policy mode
# Note: alt_status is handled separately in IMPORTANT CONTEXT section, not in policy rules
METRIC_RULE_TEMPLATES = {
    "attendance": "Give preference to raiders with higher attendance percentage.",
    "recent_loot": "Give preference to raiders who have received fewer items recently.",
    "wishlist_position": "Give preference to raiders who ranked this item higher on their wishlist (lower position = more desired).",
    "parses": "Give preference to raiders with better parse performance.",
    "ilvl_comparison": "Give preference to raiders with a larger ilvl difference.",
    "tier_token_counts": "Prioritise raiders who are closer to completing 2 or 4 set tier bonus.",
    "last_item_received": "Give preference to raiders who received an item for this slot longest ago."
}


def generate_simple_policy_rules() -> str:
    """
    Generate numbered policy rules from metric order and enabled toggles.

    Returns:
        Formatted string of numbered rules for LLM prompt
    """
    config = get_config_manager()
    metric_order = config.get_metric_order()
    currently_equipped_enabled = config.get_currently_equipped_enabled()

    # Ensure all known metrics are in the order (handles config migration)
    all_metrics = list(METRIC_RULE_TEMPLATES.keys())
    seen = set(metric_order)
    for m in all_metrics:
        if m not in seen:
            metric_order = list(metric_order) + [m]

    enabled = {
        "attendance": config.get_show_attendance(),
        "recent_loot": config.get_show_recent_loot(),
        "wishlist_position": config.get_show_wishlist_position(),
        "parses": config.get_show_parses(),
        # ilvl comparison: requires currently equipped AND show_ilvl_comparisons
        "ilvl_comparison": currently_equipped_enabled and config.get_show_ilvl_comparisons(),
        # tier_token_counts: requires currently equipped AND show_tier_token_counts
        "tier_token_counts": currently_equipped_enabled and config.get_show_tier_token_counts(),
        "last_item_received": config.get_show_last_item_received(),
    }

    rules = []
    rule_num = 1
    for metric in metric_order:
        if enabled.get(metric) and metric in METRIC_RULE_TEMPLATES:
            rules.append(f"RULE {rule_num}: {METRIC_RULE_TEMPLATES[metric]}")
            rule_num += 1

    return "\n".join(rules) if rules else "No additional rules configured."




def get_raider_slot_history(raider_name: str, item_slot: str) -> Optional[str]:
    """
    Get what item the raider last received in this slot.

    Args:
        raider_name: Name of the raider
        item_slot: Equipment slot (e.g., "Trinket", "Head")

    Returns:
        String describing last item or None
    """
    cache_file = paths.get_raider_cache_path()

    if not cache_file.exists():
        return None

    try:
        with open(cache_file, 'rb') as f:
            raider_data_result = pickle.load(f)
            raiders_df = raider_data_result.raiders_df

        raider_match = raiders_df[
            raiders_df["Raider Name"].str.lower() == raider_name.lower()
        ]

        if raider_match.empty:
            return None

        raider_row = raider_match.iloc[0]
        last_loot = raider_row.get("Last Loot Received", {})

        if not isinstance(last_loot, dict):
            return None

        # Try various capitalizations
        for variant in [item_slot, item_slot.title(), item_slot.lower()]:
            if variant in last_loot and last_loot[variant] != "None":
                return last_loot[variant]

        return None

    except Exception:
        return None


def get_item_candidates_prompt(
    item_name: str,
    session_allocations: Optional[Dict[str, int]] = None
) -> Dict:
    """
    Generate a compact prompt for LLM-based loot decision on a single item.

    This function:
    1. Gets eligible candidates using existing logic
    2. Enriches with slot history for each candidate
    3. Includes session allocation counts for relevant candidates
    4. Formats into a prompt for the LLM

    Args:
        item_name: Name of the item to analyze
        session_allocations: Dict of {player_name: suggestion_1_count} from current session

    Returns:
        Dict with:
        - success: bool
        - item_name: str
        - item_slot: str
        - prompt: str (the formatted prompt for the LLM)
        - error: str (if success=False)
    """
    if session_allocations is None:
        session_allocations = {}
    try:
        # Use existing logic to get candidates
        result = generate_checking_candidates(item_name)

        candidates_df = result.candidates_df

        if candidates_df.empty:
            return {
                "success": False,
                "item_name": item_name,
                "item_slot": result.item_slot,
                "prompt": "",
                "error": f"No eligible candidates found for {item_name}"
            }

        # Work with a copy of the candidates DataFrame
        candidates_df = candidates_df.copy()

        # Get config values once
        config = get_config_manager()
        loot_lookback_days = config.get_loot_lookback_days()
        show_attendance = config.get_show_attendance()
        show_recent_loot = config.get_show_recent_loot()
        show_alt_status = config.get_show_alt_status()
        show_wishlist_position = config.get_show_wishlist_position()
        show_parses = config.get_show_parses()
        parse_zone_id = config.get_parse_zone_id()
        parse_zone_label = config.get_parse_zone_label()
        parse_filter_mode = config.get_parse_filter_mode()
        server_slug = config.get_wcl_server_slug()
        server_region = config.get_wcl_server_region()

        # Load cache data for ilvl comparisons and tier token counts (once for all candidates)
        cache_data = None
        item_ilvl = result.item_ilvl  # Use pre-calculated ilvl from result
        show_ilvl_upgrade = config.get_currently_equipped_enabled() and config.get_show_ilvl_comparisons()
        show_tier_token_counts = config.get_currently_equipped_enabled() and config.get_show_tier_token_counts() and result.tier_version

        if show_ilvl_upgrade or show_tier_token_counts:
            cache_data = get_cached_raider_gear()

        # Load raider notes if enabled
        show_raider_notes = config.get_show_raider_notes()
        raider_notes = load_raider_notes() if show_raider_notes else {}

        # Load TMB received data for last item received metric
        show_last_item_received = config.get_show_last_item_received()
        tmb_received_df = None
        reference_date = get_reference_date()
        if show_last_item_received:
            tmb = TMBDataManager()
            tmb_received_df = tmb.get_raider_received()

        # Build prompt
        prompt_lines = []

        # Item header
        prompt_lines.append(f"## Item: {item_name}")
        if result.item_slot:
            prompt_lines.append(f"Slot: {result.item_slot}")
        if result.item_note and pd.notna(result.item_note):
            prompt_lines.append(f"Guild Priority Note: {result.item_note}")
        prompt_lines.append("")

        # Candidates section
        prompt_lines.append("## Candidates")
        prompt_lines.append("")

        # Track if any candidate has custom notes
        has_custom_notes = False

        for idx, (_, row) in enumerate(candidates_df.iterrows(), 1):
            raider_name = row['Raider Name']
            class_spec = row['Class/Spec']
            role = row['Role']
            is_offspec = row.get('Spec Type', 'Mainspec') == 'Offspec'
            wishlist = row['Wishlist Order']
            attendance = row['Attendance %']
            recent = row['Recent Loot']
            is_alt = row['Is Alt?']

            # Normalize role names to full words
            role_display = {
                "Heal": "Healer",
                "Tank": "Tank",
                "DPS": "DPS",
                "Melee": "Melee DPS",
                "Ranged": "Ranged DPS"
            }.get(role, role)

            # Add [ALT] marker to name if applicable, and [OFFSPEC] if item is for offspec
            name_display = raider_name
            if show_alt_status and is_alt:
                name_display += " [ALT]"
            if is_offspec:
                name_display += " [OFFSPEC]"

            prompt_lines.append(f"### {idx}. {name_display}")
            prompt_lines.append(f"- Class/Spec: {class_spec}")
            prompt_lines.append(f"- Role: {role_display}")
            prompt_lines.append(f"- Item Priority: {'Offspec (for alternate role)' if is_offspec else 'Mainspec'}")
            if show_wishlist_position:
                prompt_lines.append(f"- Wishlist Position: #{wishlist}")
            if show_attendance:
                prompt_lines.append(f"- Attendance: {attendance}%")
            if show_recent_loot:
                prompt_lines.append(f"- Items Won (Last {loot_lookback_days} Days): {recent}")

            # Add session allocation count if player has received items this session
            if raider_name in session_allocations:
                alloc_count = session_allocations[raider_name]
                prompt_lines.append(f"- Items assigned this session: {alloc_count}")

            if show_alt_status and is_alt:
                prompt_lines.append(f"- This is an ALT character")

            # Add last item received for slot if enabled
            if show_last_item_received and result.item_slot and tmb_received_df is not None:
                # Find character's received data
                char_row = tmb_received_df[
                    tmb_received_df["name"].str.lower() == raider_name.lower()
                ]
                if not char_row.empty:
                    # Normalize slot name for matching
                    cache_slot = normalize_slot_for_cache(result.item_slot)
                    if cache_slot:
                        nexus_for_slot = NexusItemManager()
                        last_item_data = find_last_received_for_slot(
                            char_row, nexus_for_slot, cache_slot, reference_date
                        )
                        if last_item_data and last_item_data.get("received_at"):
                            days_ago = (reference_date - last_item_data["received_at"]).days
                            prompt_lines.append(f"- Last {result.item_slot} received: {days_ago} days ago")
                        else:
                            prompt_lines.append(f"- Last {result.item_slot} received: Never")
                    else:
                        prompt_lines.append(f"- Last {result.item_slot} received: Never")
                else:
                    prompt_lines.append(f"- Last {result.item_slot} received: Never")

            # Add parse data if enabled
            if show_parses and parse_zone_id and server_slug and server_region:
                # Check if we should fetch parses for this role based on filter mode
                # DPS roles include "DPS", "Melee", "Ranged"
                is_dps_role = role in ["DPS", "Melee", "Ranged"]
                should_fetch_parse = (parse_filter_mode == "everyone") or is_dps_role

                if should_fetch_parse:
                    # Get archetype from role for metric determination
                    archetype = role if role in ["Healer", "Tank", "DPS"] else "DPS"
                    parse_data = get_or_fetch_parse(
                        raider_name, parse_zone_id, server_slug, server_region, archetype
                    )
                    if parse_data and (parse_data.best_avg is not None or parse_data.median_avg is not None):
                        best_str = f"{parse_data.best_avg:.1f}" if parse_data.best_avg else "N/A"
                        median_str = f"{parse_data.median_avg:.1f}" if parse_data.median_avg else "N/A"
                        prompt_lines.append(f"- {parse_zone_label} Parses: Best {best_str}, Median {median_str}")
                    else:
                        prompt_lines.append(f"- {parse_zone_label} Parses: None recorded.")

            # Add ilvl upgrade if enabled
            if show_ilvl_upgrade and item_ilvl:
                equipped_ilvls = get_equipped_ilvls_for_slot(raider_name, result.item_slot, cache_data)
                if equipped_ilvls:
                    if len(equipped_ilvls) == 1:
                        # Single slot - simple display
                        upgrade = item_ilvl - equipped_ilvls[0]
                        prompt_lines.append(f"- Upgrade size: {upgrade} item levels")
                    else:
                        # Dual slot - show both upgrades
                        upgrades = [item_ilvl - ilvl for ilvl in equipped_ilvls]
                        prompt_lines.append(f"- Upgrade size: {upgrades[0]} / {upgrades[1]} item levels")
                else:
                    prompt_lines.append(f"- Upgrade size: Unknown (no equipped data)")

            # Add tier token count if enabled and item is a tier token
            if show_tier_token_counts and cache_data:
                raiders = cache_data.get("raiders", {})
                # Case-insensitive lookup for raider
                raider_cache = None
                for name, data in raiders.items():
                    if name.lower() == raider_name.lower():
                        raider_cache = data
                        break

                if raider_cache:
                    tier_counts = raider_cache.get("tier_token_counts", {})
                    count = tier_counts.get(result.tier_version, 0)
                    prompt_lines.append(f"- Tier tokens equipped: {count}")

            # Add raider notes if enabled
            if show_raider_notes:
                note = raider_notes.get(raider_name, "")
                if note:
                    prompt_lines.append(f"- Custom Note: {note}")
                    has_custom_notes = True

            prompt_lines.append("")

        # Guild policy - Simple or Custom mode
        prompt_lines.append("## Guild Loot Policy Rules")

        # Tank priority rule appears first if enabled (regardless of policy mode)
        tank_priority_enabled = config.get_tank_priority()
        if tank_priority_enabled:
            prompt_lines.append("Always prioritise tank-role characters for any mainspec items.")

        # Mains over alts rule (when alts are shown and mains priority is enabled)
        if config.get_show_alt_status() and config.get_mains_over_alts():
            prompt_lines.append("Give preference to main characters over alt characters.")

        if config.get_policy_mode() == "simple":
            prompt_lines.append("For the following rules, apply them in STRICT ORDER (Rule 1 = highest priority):")
            prompt_lines.append(generate_simple_policy_rules())
        else:
            prompt_lines.append(get_guild_policy_summary())
        prompt_lines.append("")

        # Instructions
        prompt_lines.append("## Your Task")
        prompt_lines.append("Select Suggestion 1, Suggestion 2, and Suggestion 3 recipients for this item.")
        prompt_lines.append("- If fewer than 3 eligible candidates exist, use \"None\" for empty slots")
        prompt_lines.append("- Briefly reference which policy rule(s) determined your Suggestion 1 choice")
        prompt_lines.append("")
        prompt_lines.append("Respond in this exact format:")
        prompt_lines.append("Suggestion 1: [Name]")
        prompt_lines.append("Suggestion 2: [Name or None]")
        prompt_lines.append("Suggestion 3: [Name or None]")
        prompt_lines.append("Rationale: [1-2 sentences referencing the deciding policy rule]")

        prompt = "\n".join(prompt_lines)

        return {
            "success": True,
            "item_name": item_name,
            "item_slot": result.item_slot,
            "prompt": prompt,
            "has_custom_notes": has_custom_notes,
            "has_wishlist_position": show_wishlist_position,
            "has_ilvl_comparison": show_ilvl_upgrade,
            "has_guild_priority_note": bool(result.item_note and pd.notna(result.item_note)),
            "error": None
        }

    except ValueError as e:
        return {
            "success": False,
            "item_name": item_name,
            "item_slot": None,
            "prompt": "",
            "error": str(e)
        }
    except Exception as e:
        return {
            "success": False,
            "item_name": item_name,
            "item_slot": None,
            "prompt": "",
            "error": f"Unexpected error: {str(e)}"
        }


def get_zone_items(zone_name: str) -> List[str]:
    """
    Get all unique equippable item names from a raid zone, sorted by tier.
    Tier tokens and exchange items are placed at the END of the list, sorted alphabetically.

    Args:
        zone_name: Name of the raid zone (e.g., "Sunwell Plateau")

    Returns:
        List of unique item names sorted by tier (ascending), with tier tokens
        and exchange items at the end in alphabetical order.
        Duplicate item names are removed (first occurrence kept).
    """
    tmb = TMBDataManager()
    nexus = NexusItemManager()

    item_notes_df = tmb.get_item_notes()

    # Filter by zone
    zone_items = item_notes_df[
        item_notes_df["instance_name"].str.lower() == zone_name.lower()
    ]

    if zone_items.empty:
        return []

    # Collect equippable items with tier info for sorting
    # Use seen set to deduplicate by item name (keep first occurrence)
    seen_names = set()
    equippable_items = []  # Regular equippable items
    tier_tokens = []       # Tier token and exchange items (separate list)

    for _, item_row in zone_items.iterrows():
        item_id = item_row.get("id")
        item_name = item_row["name"]
        if item_id and item_name not in seen_names:
            slot = nexus.get_item_slot(item_id)

            # Check if it's a tier token or exchange item first
            if is_tier_token(item_name) or is_exchange_item(item_name):
                tier_tokens.append(item_name)
                seen_names.add(item_name)
            elif slot and slot.lower() not in ("non-equippable", "bag"):
                # Regular equippable item
                tier = item_row.get("tier")
                equippable_items.append({"name": item_name, "tier": tier})
                seen_names.add(item_name)

    # Sort regular items by tier ascending (items with no tier go to end)
    equippable_items.sort(key=lambda x: x["tier"] if x["tier"] is not None else float('inf'))

    # Sort tier tokens/exchange items alphabetically (case-insensitive)
    tier_tokens.sort(key=str.lower)

    # Combine: tier-sorted regular items first, then alphabetical tier tokens/exchange items
    result = [item["name"] for item in equippable_items]
    result.extend(tier_tokens)

    return result


