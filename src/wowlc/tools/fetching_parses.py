"""
Fetching Parses Tool for Let Me LC That For You

This module provides a tool to fetch parse data from WarcraftLogs for a list of candidates.
This is the second stage of loot decision - getting performance metrics for selected raiders.

Returns: Parse columns (Best/Median for specified zones)
"""

from dataclasses import dataclass
from typing import Optional, List
import pandas as pd
import sys

from ..core.config import get_config_manager
from ..services.wcl_client import WarcraftLogsClient
from ..services.tmb_manager import TMBDataManager


@dataclass
class FetchingParsesResult:
    """Container for fetching parses tool output."""
    parse_zones: List[dict]
    parses_df: pd.DataFrame


def get_raider_parses(
    wcl_client: WarcraftLogsClient,
    character_name: str,
    server_slug: str,
    server_region: str,
    zone_id: int,
    metric: str = "dps"
) -> dict:
    """
    Get parse data for a raider from WarcraftLogs for a specific zone.

    Args:
        wcl_client: Authenticated WarcraftLogs client
        character_name: Name of the character
        server_slug: Server slug (e.g., "pyrewood-village")
        server_region: Server region (e.g., "EU")
        zone_id: Zone ID to get rankings for
        metric: Metric to use for rankings ("dps" or "hps")

    Returns:
        Dict with best_avg and median_avg, or None values if not found
    """
    query = """
    query GetZoneRankings($name: String!, $serverSlug: String!, $serverRegion: String!, $zoneID: Int!, $metric: CharacterPageRankingMetricType) {
        characterData {
            character(name: $name, serverSlug: $serverSlug, serverRegion: $serverRegion) {
                zoneRankings(zoneID: $zoneID, metric: $metric)
            }
        }
    }
    """

    try:
        result = wcl_client.query(query, {
            "name": character_name,
            "serverSlug": server_slug,
            "serverRegion": server_region,
            "zoneID": zone_id,
            "metric": metric
        })

        character = result.get("characterData", {}).get("character")
        if not character:
            return {"best_avg": None, "median_avg": None}

        rankings = character.get("zoneRankings", {})

        return {
            "best_avg": rankings.get("bestPerformanceAverage"),
            "median_avg": rankings.get("medianPerformanceAverage")
        }
    except Exception:
        return {"best_avg": None, "median_avg": None}


def get_metric_from_archetype(archetype: Optional[str]) -> str:
    """
    Determine the appropriate WCL metric based on character archetype.

    Args:
        archetype: Character archetype from TMB ("DPS", "Tank", "Healer", or None)

    Returns:
        "hps" for Healers, "dps" for DPS/Tanks/None
    """
    if archetype and archetype.lower() == "healer":
        return "hps"
    return "dps"


def get_all_raider_parses(
    wcl_client: WarcraftLogsClient,
    character_name: str,
    server_slug: str,
    server_region: str,
    parse_zones: list[dict],
    metric: str = "dps"
) -> dict:
    """
    Get parse data for a raider across multiple zones.

    Args:
        wcl_client: Authenticated WarcraftLogs client
        character_name: Name of the character
        server_slug: Server slug
        server_region: Server region
        parse_zones: List of zone configs with 'zone_id' and 'label' keys
        metric: Metric to use for rankings ("dps" or "hps")

    Returns:
        Dict with keys like "Zone Label Best", "Zone Label Median" for each zone
    """
    result = {}

    for zone_config in parse_zones:
        zone_id = zone_config["zone_id"]
        label = zone_config["label"]

        parses = get_raider_parses(
            wcl_client, character_name, server_slug, server_region, zone_id, metric
        )

        best_key = f"{label} Best"
        median_key = f"{label} Median"

        result[best_key] = round(parses["best_avg"], 1) if parses["best_avg"] else None
        result[median_key] = round(parses["median_avg"], 1) if parses["median_avg"] else None

    return result


def generate_fetching_parses(
    candidate_names: List[str],
    server_slug: str = None,
    server_region: str = None,
    parse_zones: Optional[List[dict]] = None
) -> FetchingParsesResult:
    """
    Fetch parse data for a list of candidate raiders.

    Args:
        candidate_names: List of raider names to fetch parses for
        server_slug: WarcraftLogs server slug (or set WCL_SERVER_SLUG env var)
        server_region: WarcraftLogs server region (or set WCL_SERVER_REGION env var)
        parse_zones: List of zone configs. Each dict should have 'zone_id' and 'label' keys.

    Returns:
        FetchingParsesResult containing parse zone info and parses DataFrame

    Raises:
        ValueError: If parse_zones is not provided
    """
    if parse_zones is None:
        raise ValueError("parse_zones must be provided")

    # Get configuration from config if not provided
    config = get_config_manager()
    server_slug = server_slug or config.get_wcl_server_slug() or "pyrewood-village"
    server_region = server_region or config.get_wcl_server_region() or "EU"

    # Initialize WCL client
    wcl = WarcraftLogsClient()

    if not candidate_names:
        # Build empty DataFrame with dynamic columns
        columns = ["Raider Name"]
        for zone_config in parse_zones:
            label = zone_config["label"]
            columns.extend([f"{label} Best", f"{label} Median"])

        empty_df = pd.DataFrame(columns=columns)
        return FetchingParsesResult(
            parse_zones=parse_zones,
            parses_df=empty_df
        )

    # Fetch archetype data from TMB to determine metric for each character
    archetype_map = {}
    try:
        tmb_manager = TMBDataManager()
        raider_profiles = tmb_manager.get_raider_profiles()

        # Build a name -> archetype mapping
        for _, row in raider_profiles.iterrows():
            archetype_map[row["name"]] = row.get("archetype")
    except Exception as e:
        # If TMB fetch fails, log and continue with default metric
        print(f"Warning: Could not fetch archetype data from TMB: {e}")

    # Fetch parses for each candidate
    parses_data = []

    for raider_name in candidate_names:
        # Determine metric based on archetype
        archetype = archetype_map.get(raider_name)
        metric = get_metric_from_archetype(archetype)

        parse_data = get_all_raider_parses(
            wcl, raider_name, server_slug, server_region, parse_zones, metric
        )

        row_data = {"Raider Name": raider_name}
        row_data.update(parse_data)

        parses_data.append(row_data)

    # Create DataFrame
    parses_df = pd.DataFrame(parses_data)

    return FetchingParsesResult(
        parse_zones=parse_zones,
        parses_df=parses_df
    )


def format_fetching_parses_output(result: FetchingParsesResult) -> str:
    """
    Format the fetching parses result for display.
    
    Args:
        result: FetchingParsesResult object
    
    Returns:
        Formatted string output
    """
    zone_labels = [z["label"] for z in result.parse_zones]
    lines = [f"Parse data for zones: {', '.join(zone_labels)}", ""]
    
    if result.parses_df.empty:
        lines.append("No candidates to fetch parses for.")
    else:
        lines.append(result.parses_df.to_string(index=False))
    
    return "\n".join(lines)


# Tool function
def fetching_parses_tool(candidate_names: List[str], parse_zones: Optional[List[dict]] = None) -> dict:
    """
    Fetch parse data for the given candidate raiders.

    This is the second stage of loot decision - fetching WarcraftLogs performance
    metrics for the candidates identified in stage 1.

    Args:
        candidate_names: List of raider names to fetch parses for
        parse_zones: Optional list of zone configs.
                     Each dict should have 'zone_id' and 'label' keys
                     Example: [{"zone_id": 1047, "label": "Kara"}, {"zone_id": 1048, "label": "Gruul/Mag"}]

    Returns:
        Dictionary containing:
        - parse_zones: List of zone configurations used
        - parses: List of dicts representing the DataFrame rows
        - formatted_output: Pre-formatted string for display
    """
    try:
        result = generate_fetching_parses(candidate_names, parse_zones=parse_zones)

        return {
            "success": True,
            "parse_zones": result.parse_zones,
            "parses": result.parses_df.to_dict(orient="records"),
            "formatted_output": format_fetching_parses_output(result)
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"An error occurred: {str(e)}"
        }


if __name__ == "__main__":
    # Example usage
    import sys
    
    if len(sys.argv) > 1:
        names = sys.argv[1:]
    else:
        names = ["Exampleraider", "Anotherraider"]
    
    result = fetching_parses_tool(names)
    
    if result["success"]:
        print(result["formatted_output"])
    else:
        print(f"Error: {result['error']}")