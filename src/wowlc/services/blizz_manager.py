"""
Blizzard API Client for WoW Loot Council MCP Server.

This module handles OAuth2 authentication and API queries to the
Blizzard World of Warcraft API. It supports client credentials flow
for accessing WoW Classic character equipment and profile data.

Usage:
    # Get access token
    token = get_access_token()

    # Fetch character equipment
    gear = fetch_character_gear_names(token)
"""

import requests

from ..core.config import get_config_manager


def get_access_token():
    """
    Obtains the OAuth client credentials token.
    """
    config = get_config_manager()
    client_id = config.get_blizzard_client_id()
    client_secret = config.get_blizzard_client_secret()

    if not client_id or not client_secret:
        print("Error: Blizzard API credentials not configured")
        return None

    url = "https://oauth.battle.net/token"

    body = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret
    }
    
    try:
        response = requests.post(url, data=body)
        response.raise_for_status()
        return response.json().get("access_token")
    except requests.exceptions.RequestException as e:
        print(f"Error getting token: {e}")
        return None

def fetch_character_gear_names(access_token, region, realm, character, namespace=None):
    """
    Fetches the equipped gear for a WoW Classic character.

    Args:
        access_token: OAuth access token from get_access_token()
        region: Region code
        realm: Realm slug
        character: Character name
        namespace: API namespace override. Defaults to profile-classic1x-{region} (Classic Era).
                   Use profile-classic-{region} for TBC/Wrath/Anniversary.

    Returns:
        Dictionary mapping slot names to item names
    """
    url = f"https://{region}.api.blizzard.com/profile/wow/character/{realm}/{character}/equipment"

    # Use provided namespace or default to Classic Era
    if namespace is None:
        namespace = f"profile-classic1x-{region}"

    params = {
        "namespace": namespace,
        "locale": "en_GB"
    }

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

        gear_dict = {}

        for entry in data.get("equipped_items", []):
            # Extract the user-friendly slot name (e.g., "Head", "Trinket 1")
            slot_name = entry["slot"]["name"]

            # Extract the item name
            item_name = entry["name"]

            # Add to dictionary
            gear_dict[slot_name] = item_name

        return gear_dict

    except requests.exceptions.RequestException as e:
        print(f"Error fetching gear: {e}")
        return {}