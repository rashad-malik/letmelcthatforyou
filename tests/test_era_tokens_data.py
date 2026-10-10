"""Data-validation tests for the Era (vanilla) sections of data/tokens.json.

Every item name referenced by the Era tier tokens, exchange items and recipes
must resolve in the Nexus item database — a typo here would silently break
wishlist fan-out for that item. Also guards the version scoping of the
tokens.json loaders (Era data must not leak into TBC classification and
vice versa).
"""

import json
from pathlib import Path

import pytest

from wowlc.core.zones import VERSION_ERA, VERSION_TBC
from wowlc.services.nexus_manager import NexusItemManager
from wowlc.tools import get_item_candidates as gic
from wowlc.tools.fetching_current_items import GEAR_SLOTS, split_slots

TOKENS_PATH = Path(__file__).resolve().parent.parent / "data" / "tokens.json"

KNOWN_PROFESSIONS = {
    "Alchemy",
    "Blacksmithing",
    "Enchanting",
    "Engineering",
    "Jewelcrafting",
    "Leatherworking",
    "Tailoring",
}


@pytest.fixture(scope="module")
def tokens_data() -> dict:
    return json.loads(TOKENS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def nexus() -> NexusItemManager:
    return NexusItemManager()


def test_era_sections_present(tokens_data: dict) -> None:
    assert "Era" in tokens_data
    assert "exchange_items_era" in tokens_data
    assert "recipes_era" in tokens_data

    token_count = sum(len(g["tokens"]) for g in tokens_data["Era"])
    assert token_count == 32  # 8 AQ40 (Tier 2.5) + 24 Naxxramas (Tier 3)
    assert len(tokens_data["exchange_items_era"]) == 16
    assert len(tokens_data["recipes_era"]) == 19


def test_era_tier_token_names_resolve(tokens_data: dict, nexus: NexusItemManager) -> None:
    missing = []
    for tier_group in tokens_data["Era"]:
        for token in tier_group["tokens"]:
            if not nexus.get_item_id(token["token_name"]):
                missing.append(token["token_name"])
            for compatible in token["compatible_items"]:
                if not nexus.get_item_id(compatible):
                    missing.append(compatible)
    assert not missing, f"Names not found in Nexus DB: {missing}"


def test_era_exchange_names_resolve(tokens_data: dict, nexus: NexusItemManager) -> None:
    missing = []
    for source_name, entry in tokens_data["exchange_items_era"].items():
        if not (nexus.get_item_id(source_name) or entry.get("item_id")):
            missing.append(source_name)
        for reward in entry["items"]:
            if not nexus.get_item_id(reward):
                missing.append(reward)
    assert not missing, f"Names not found in Nexus DB: {missing}"


def test_era_recipe_names_resolve(tokens_data: dict, nexus: NexusItemManager) -> None:
    missing = []
    for recipe_name, entry in tokens_data["recipes_era"].items():
        # A recipe key may legitimately not resolve (TMB and Nexus disagree on
        # the name) as long as the entry pins the ID explicitly
        if not (nexus.get_item_id(recipe_name) or entry.get("item_id")):
            missing.append(recipe_name)
        for crafted in entry["items"]:
            if not nexus.get_item_id(crafted):
                missing.append(crafted)
    assert not missing, f"Names not found in Nexus DB: {missing}"


def test_era_token_slots_and_ilvls_valid(tokens_data: dict) -> None:
    for tier_group in tokens_data["Era"]:
        for token in tier_group["tokens"]:
            parts = split_slots(token["slot"])
            assert parts, f"{token['token_name']} has an empty slot"
            for part in parts:
                assert part.lower() in GEAR_SLOTS, (
                    f"{token['token_name']} slot part '{part}' is not a known gear slot"
                )
            assert isinstance(token["ilvl"], int) and token["ilvl"] > 0


def test_era_recipe_professions_valid(tokens_data: dict) -> None:
    for recipe_name, entry in tokens_data["recipes_era"].items():
        assert entry["profession"] in KNOWN_PROFESSIONS, (
            f"{recipe_name} has unknown profession '{entry['profession']}'"
        )


def test_classifiers_are_version_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gic, "current_version_key", lambda: VERSION_ERA)
    assert gic.is_tier_token("Desecrated Helmet")
    assert not gic.is_tier_token("Helm of the Fallen Defender")
    assert gic.is_exchange_item("Head of Onyxia")
    assert gic.is_recipe("Plans: Lionheart Helm")

    monkeypatch.setattr(gic, "current_version_key", lambda: VERSION_TBC)
    assert gic.is_tier_token("Helm of the Fallen Defender")
    assert not gic.is_tier_token("Desecrated Helmet")
    assert not gic.is_exchange_item("Head of Onyxia")
    assert not gic.is_recipe("Plans: Lionheart Helm")
