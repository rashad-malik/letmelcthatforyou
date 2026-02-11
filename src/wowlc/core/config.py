"""
Centralized configuration management for WoW LC MCP.

Provides a single source of truth for all application configuration,
replacing the previous .env file approach.
"""

from pathlib import Path
import os
import platform
import json
from typing import Optional, List


# The 7 decision metrics that have independent per-mode (Simple/Custom) toggle state
_DECISION_METRIC_KEYS = [
    "show_attendance", "show_recent_loot", "show_wishlist_position",
    "show_parses", "show_ilvl_comparisons", "show_tier_token_counts",
    "show_last_item_received",
]

# Default per-mode metric states
_DEFAULT_MODE_METRICS = {
    "show_attendance": False,
    "show_recent_loot": False,
    "show_wishlist_position": False,
    "show_parses": False,
    "show_ilvl_comparisons": False,
    "show_tier_token_counts": False,
    "show_last_item_received": False,
}


class ConfigManager:
    """
    Manages all application configuration.

    Stores configuration in AppData/Local/letmelcthatforyou/config.json.
    Uses singleton pattern to ensure consistent configuration across the application.
    """

    _instance = None

    # Default configuration values
    DEFAULTS = {
        "version": "2.0",
        "wcl": {
            "client_id": "",
            "client_secret": "",
            "user_token": "",
            "redirect_uri": "http://localhost:8765/callback",
            "client_version": "Fresh",
            "server_slug": "",
            "server_region": "US",
            "reference_date": ""
        },
        "dev": {
            "pyrewood_mode": False,
            "thunderstrike_mode": False
        },
        "blizzard": {
            "client_id": "",
            "client_secret": ""
        },
        "tmb": {
            "guild_id": ""
        },
        "lookback": {
            "attendance_days": 60,
            "loot_days": 14
        },
        "player_metrics": {
            "show_attendance": False,
            "show_recent_loot": False,
            "show_alt_status": False,
            "mains_over_alts": False,
            "show_wishlist_position": False,
            "show_parses": False,
            "parse_zone_id": None,
            "parse_zone_label": "",
            "parse_filter_mode": "dps_only",
            "policy_mode": "simple",
            "metric_order": ["attendance", "ilvl_comparison", "last_item_received", "parses", "recent_loot", "tier_token_counts", "wishlist_position"],
            "currently_equipped_enabled": False,
            "currently_equipped_api_source": "blizzard",
            "show_ilvl_comparisons": False,
            "show_tier_token_counts": False,
            "tank_priority": False,
            "show_raider_notes": False,
            "raider_note_source": "public_note",
            "show_last_item_received": False,
            "simple_mode_metrics": dict(_DEFAULT_MODE_METRICS),
            "custom_mode_metrics": dict(_DEFAULT_MODE_METRICS),
        },
        "export_path": "",
        "llm": {
            "active_provider": "anthropic",
            "active_model": "claude-sonnet-4-20250514",
            "api_keys": {
                "anthropic": "",
                "openai": "",
                "gemini": "",
                "mistral": "",
                "groq": "",
                "xai": "",
                "cohere": "",
                "together_ai": "",
                "deepseek": ""
            },
            "delay_seconds": 2.0
        },
        "ui": {
            "dark_mode": False
        }
    }

    def __new__(cls):
        """Singleton pattern to ensure one ConfigManager instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize config manager (called once due to singleton pattern)."""
        if self._initialized:
            return

        self._initialized = True

        # Determine config file path based on platform
        system = platform.system()

        if system == "Windows":
            appdata_dir = Path(os.getenv('LOCALAPPDATA', Path.home() / "AppData" / "Local")) / "letmelcthatforyou"
            default_export = Path.home() / "Documents" / "Let Me LC That For You" / "Exports"
        elif system == "Darwin":
            appdata_dir = Path.home() / "Library" / "Application Support" / "letmelcthatforyou"
            default_export = Path.home() / "Documents" / "letmelcthatforyou" / "Exports"
        else:
            # Linux: Use XDG_CONFIG_HOME for config
            xdg_config = os.getenv('XDG_CONFIG_HOME', str(Path.home() / ".config"))
            appdata_dir = Path(xdg_config) / "letmelcthatforyou"
            # Use XDG documents dir for exports
            from wowlc.core.paths import _get_xdg_documents_dir
            xdg_docs = _get_xdg_documents_dir()
            default_export = xdg_docs / "Let Me LC That For You" / "Exports"

        appdata_dir.mkdir(parents=True, exist_ok=True)
        self._config_path = appdata_dir / "config.json"

        # Set default export path
        self.DEFAULTS["export_path"] = str(default_export)

        # Load configuration
        self._config = self._load_config()

    def _load_config(self) -> dict:
        """Load configuration from JSON file, merging with defaults."""
        if not self._config_path.exists():
            return self._deep_copy(self.DEFAULTS)

        try:
            with open(self._config_path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                # Merge with defaults to ensure all keys exist
                return self._merge_with_defaults(loaded)
        except (json.JSONDecodeError, IOError):
            return self._deep_copy(self.DEFAULTS)

    def _deep_copy(self, d: dict) -> dict:
        """Create a deep copy of a dictionary."""
        return json.loads(json.dumps(d))

    def _merge_with_defaults(self, loaded: dict) -> dict:
        """Merge loaded config with defaults, preserving loaded values."""
        result = self._deep_copy(self.DEFAULTS)

        for key, value in loaded.items():
            if key in result:
                if isinstance(value, dict) and isinstance(result[key], dict):
                    # Merge nested dicts
                    for sub_key, sub_value in value.items():
                        if sub_key in result[key]:
                            result[key][sub_key] = sub_value
                else:
                    result[key] = value

        return result

    def _save_config(self) -> None:
        """Save configuration to JSON file."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)

        with open(self._config_path, 'w', encoding='utf-8') as f:
            json.dump(self._config, f, indent=2)

    def get_config_path(self) -> Path:
        """Get the path to the config file."""
        return self._config_path

    # =========================================================================
    # WarcraftLogs Configuration
    # =========================================================================

    def get_wcl_client_id(self) -> str:
        """Get WarcraftLogs OAuth client ID."""
        return self._config["wcl"]["client_id"]

    def set_wcl_client_id(self, value: str) -> None:
        """Set WarcraftLogs OAuth client ID."""
        self._config["wcl"]["client_id"] = value
        self._save_config()

    def get_wcl_client_secret(self) -> str:
        """Get WarcraftLogs OAuth client secret."""
        return self._config["wcl"]["client_secret"]

    def set_wcl_client_secret(self, value: str) -> None:
        """Set WarcraftLogs OAuth client secret."""
        self._config["wcl"]["client_secret"] = value
        self._save_config()

    def get_wcl_user_token(self) -> str:
        """Get WarcraftLogs user access token."""
        return self._config["wcl"]["user_token"]

    def set_wcl_user_token(self, value: str) -> None:
        """Set WarcraftLogs user access token."""
        self._config["wcl"]["user_token"] = value
        self._save_config()

    def get_wcl_redirect_uri(self) -> str:
        """Get WarcraftLogs OAuth redirect URI."""
        return self._config["wcl"]["redirect_uri"]

    def set_wcl_redirect_uri(self, value: str) -> None:
        """Set WarcraftLogs OAuth redirect URI."""
        self._config["wcl"]["redirect_uri"] = value
        self._save_config()

    def get_wcl_client_version(self) -> str:
        """Get WCL client version (TBC, ERA, or Fresh)."""
        return self._config["wcl"]["client_version"]

    def set_wcl_client_version(self, value: str) -> None:
        """Set WCL client version (TBC, ERA, or Fresh)."""
        self._config["wcl"]["client_version"] = value
        self._save_config()

    def get_wcl_server_slug(self) -> str:
        """Get WCL server slug. Returns override if dev mode is enabled."""
        if self.get_pyrewood_dev_mode():
            return "pyrewood-village"
        if self.get_thunderstrike_dev_mode():
            return "thunderstrike"
        return self._config["wcl"]["server_slug"]

    def get_wcl_server_slug_raw(self) -> str:
        """Get WCL server slug without Pyrewood dev mode override."""
        return self._config["wcl"]["server_slug"]

    def set_wcl_server_slug(self, value: str) -> None:
        """Set WCL server slug."""
        self._config["wcl"]["server_slug"] = value
        self._save_config()

    def get_wcl_server_region(self) -> str:
        """Get WCL server region (US or EU). Returns 'EU' if dev mode is enabled."""
        if self.get_pyrewood_dev_mode():
            return "EU"
        if self.get_thunderstrike_dev_mode():
            return "EU"
        return self._config["wcl"]["server_region"]

    def get_wcl_server_region_raw(self) -> str:
        """Get WCL server region without Pyrewood dev mode override."""
        return self._config["wcl"]["server_region"]

    def set_wcl_server_region(self, value: str) -> None:
        """Set WCL server region (US or EU)."""
        self._config["wcl"]["server_region"] = value
        self._save_config()

    # =========================================================================
    # Developer Mode Configuration
    # =========================================================================

    def get_pyrewood_dev_mode(self) -> bool:
        """Get whether Pyrewood Developer Mode is enabled."""
        return self._config.get("dev", {}).get("pyrewood_mode", False)

    def set_pyrewood_dev_mode(self, value: bool) -> None:
        """Set Pyrewood Developer Mode (forces EU region and pyrewood-village server)."""
        if "dev" not in self._config:
            self._config["dev"] = {"pyrewood_mode": False, "thunderstrike_mode": False}
        self._config["dev"]["pyrewood_mode"] = value
        # Disable thunderstrike mode if enabling pyrewood
        if value:
            self._config["dev"]["thunderstrike_mode"] = False
        self._save_config()

    def get_thunderstrike_dev_mode(self) -> bool:
        """Get whether Thunderstrike Developer Mode is enabled."""
        return self._config.get("dev", {}).get("thunderstrike_mode", False)

    def set_thunderstrike_dev_mode(self, value: bool) -> None:
        """Set Thunderstrike Developer Mode (forces EU region and thunderstrike server)."""
        if "dev" not in self._config:
            self._config["dev"] = {"pyrewood_mode": False, "thunderstrike_mode": False}
        self._config["dev"]["thunderstrike_mode"] = value
        # Disable pyrewood mode if enabling thunderstrike
        if value:
            self._config["dev"]["pyrewood_mode"] = False
        self._save_config()

    def get_reference_date(self) -> str:
        """Get reference date for testing (YYYY-MM-DD format)."""
        return self._config["wcl"]["reference_date"]

    def set_reference_date(self, value: str) -> None:
        """Set reference date for testing (YYYY-MM-DD format)."""
        self._config["wcl"]["reference_date"] = value
        self._save_config()

    # =========================================================================
    # Blizzard API Configuration
    # =========================================================================

    def get_blizzard_client_id(self) -> str:
        """Get Blizzard API client ID."""
        return self._config["blizzard"]["client_id"]

    def set_blizzard_client_id(self, value: str) -> None:
        """Set Blizzard API client ID."""
        self._config["blizzard"]["client_id"] = value
        self._save_config()

    def get_blizzard_client_secret(self) -> str:
        """Get Blizzard API client secret."""
        return self._config["blizzard"]["client_secret"]

    def set_blizzard_client_secret(self, value: str) -> None:
        """Set Blizzard API client secret."""
        self._config["blizzard"]["client_secret"] = value
        self._save_config()

    # =========================================================================
    # TMB Configuration
    # =========================================================================

    def get_tmb_guild_id(self) -> str:
        """Get ThatsmyBIS guild ID."""
        return self._config["tmb"]["guild_id"]

    def set_tmb_guild_id(self, value: str) -> None:
        """Set ThatsmyBIS guild ID."""
        self._config["tmb"]["guild_id"] = value
        self._save_config()

    # =========================================================================
    # Lookback Configuration
    # =========================================================================

    def get_attendance_lookback_days(self) -> int:
        """Get attendance lookback period in days."""
        value = self._config["lookback"]["attendance_days"]
        return int(value) if value else 60

    def set_attendance_lookback_days(self, value: int) -> None:
        """Set attendance lookback period in days."""
        self._config["lookback"]["attendance_days"] = value
        self._save_config()

    def get_loot_lookback_days(self) -> int:
        """Get loot history lookback period in days."""
        value = self._config["lookback"]["loot_days"]
        return int(value) if value else 14

    def set_loot_lookback_days(self, value: int) -> None:
        """Set loot history lookback period in days."""
        self._config["lookback"]["loot_days"] = value
        self._save_config()

    # =========================================================================
    # Player Metrics Configuration
    # =========================================================================

    def get_show_attendance(self) -> bool:
        """Get whether to show attendance metric in prompts."""
        return self._config.get("player_metrics", {}).get("show_attendance", True)

    def set_show_attendance(self, value: bool) -> None:
        """Set whether to show attendance metric in prompts."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {"show_attendance": True, "show_recent_loot": True, "show_alt_status": True}
        self._config["player_metrics"]["show_attendance"] = value
        self._save_config()

    def get_show_recent_loot(self) -> bool:
        """Get whether to show recent loot metric in prompts."""
        return self._config.get("player_metrics", {}).get("show_recent_loot", True)

    def set_show_recent_loot(self, value: bool) -> None:
        """Set whether to show recent loot metric in prompts."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {"show_attendance": True, "show_recent_loot": True, "show_alt_status": True}
        self._config["player_metrics"]["show_recent_loot"] = value
        self._save_config()

    def get_show_alt_status(self) -> bool:
        """Get whether to show alt status metric in prompts."""
        return self._config.get("player_metrics", {}).get("show_alt_status", True)

    def set_show_alt_status(self, value: bool) -> None:
        """Set whether to show alt status metric in prompts."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {"show_attendance": True, "show_recent_loot": True, "show_alt_status": True}
        self._config["player_metrics"]["show_alt_status"] = value
        self._save_config()

    def get_mains_over_alts(self) -> bool:
        """Get whether to prefer mains over alts (only applies when show_alt_status is True)."""
        return self._config.get("player_metrics", {}).get("mains_over_alts", True)

    def set_mains_over_alts(self, value: bool) -> None:
        """Set whether to prefer mains over alts."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {}
        self._config["player_metrics"]["mains_over_alts"] = value
        self._save_config()

    def get_show_wishlist_position(self) -> bool:
        """Get whether to show wishlist position metric in prompts."""
        return self._config.get("player_metrics", {}).get("show_wishlist_position", True)

    def set_show_wishlist_position(self, value: bool) -> None:
        """Set whether to show wishlist position metric in prompts."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {}
        self._config["player_metrics"]["show_wishlist_position"] = value
        self._save_config()

    def get_show_parses(self) -> bool:
        """Get whether to show parse data metric in prompts."""
        return self._config.get("player_metrics", {}).get("show_parses", False)

    def set_show_parses(self, value: bool) -> None:
        """Set whether to show parse data metric in prompts."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {}
        self._config["player_metrics"]["show_parses"] = value
        self._save_config()

    def get_parse_zone_id(self) -> Optional[int]:
        """Get the selected parse zone ID."""
        return self._config.get("player_metrics", {}).get("parse_zone_id")

    def set_parse_zone_id(self, value: Optional[int]) -> None:
        """Set the selected parse zone ID."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {}
        self._config["player_metrics"]["parse_zone_id"] = value
        self._save_config()

    def get_parse_zone_label(self) -> str:
        """Get the selected parse zone label."""
        return self._config.get("player_metrics", {}).get("parse_zone_label", "")

    def set_parse_zone_label(self, value: str) -> None:
        """Set the selected parse zone label."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {}
        self._config["player_metrics"]["parse_zone_label"] = value
        self._save_config()

    def get_parse_filter_mode(self) -> str:
        """Get the parse filter mode ('dps_only' or 'everyone')."""
        return self._config.get("player_metrics", {}).get("parse_filter_mode", "dps_only")

    def set_parse_filter_mode(self, value: str) -> None:
        """Set the parse filter mode ('dps_only' or 'everyone')."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {}
        self._config["player_metrics"]["parse_filter_mode"] = value
        self._save_config()

    def get_policy_mode(self) -> str:
        """Get policy mode ('simple' or 'custom')."""
        return self._config.get("player_metrics", {}).get("policy_mode", "simple")

    def set_policy_mode(self, value: str) -> None:
        """Set policy mode ('simple' or 'custom')."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {}
        self._config["player_metrics"]["policy_mode"] = value
        self._save_config()

    def save_mode_metrics(self, mode: str) -> None:
        """Save current active metric flags into the specified mode's shadow storage.

        Args:
            mode: Either 'simple' or 'custom'
        """
        pm = self._config.setdefault("player_metrics", {})
        storage_key = f"{mode}_mode_metrics"
        pm[storage_key] = {
            key: pm.get(key, _DEFAULT_MODE_METRICS.get(key, False))
            for key in _DECISION_METRIC_KEYS
        }
        self._save_config()

    def load_mode_metrics(self, mode: str) -> None:
        """Load metric flags from the specified mode's shadow storage into active flags.

        Args:
            mode: Either 'simple' or 'custom'
        """
        pm = self._config.setdefault("player_metrics", {})
        storage_key = f"{mode}_mode_metrics"
        stored = pm.get(storage_key, {})
        for key in _DECISION_METRIC_KEYS:
            if key in stored:
                pm[key] = stored[key]
        self._save_config()

    def get_metric_order(self) -> List[str]:
        """Get the ordered list of metrics for simple policy mode (Decision Priorities)."""
        default_order = [
            "wishlist_position", "attendance", "recent_loot", "ilvl_comparison",
            "parses", "tank_priority", "raider_notes", "last_item_received", "tier_token_counts"
        ]
        return self._config.get("player_metrics", {}).get("metric_order", default_order)

    def set_metric_order(self, order: List[str]) -> None:
        """Set the ordered list of metrics for simple policy mode."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {}
        self._config["player_metrics"]["metric_order"] = order
        self._save_config()

    def get_show_ilvl_comparisons(self) -> bool:
        """Get whether to show ilvl comparisons in loot council decisions."""
        return self._config.get("player_metrics", {}).get("show_ilvl_comparisons", False)

    def set_show_ilvl_comparisons(self, value: bool) -> None:
        """Set whether to show ilvl comparisons in loot council decisions."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {}
        self._config["player_metrics"]["show_ilvl_comparisons"] = value
        self._save_config()

    def get_show_tier_token_counts(self) -> bool:
        """Get whether to show tier token counts in loot council decisions."""
        return self._config.get("player_metrics", {}).get("show_tier_token_counts", False)

    def set_show_tier_token_counts(self, value: bool) -> None:
        """Set whether to show tier token counts in loot council decisions."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {}
        self._config["player_metrics"]["show_tier_token_counts"] = value
        self._save_config()

    def get_currently_equipped_enabled(self) -> bool:
        """Get whether currently equipped metric is enabled."""
        return self._config.get("player_metrics", {}).get("currently_equipped_enabled", False)

    def set_currently_equipped_enabled(self, value: bool) -> None:
        """Set whether currently equipped metric is enabled."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {}
        self._config["player_metrics"]["currently_equipped_enabled"] = value
        self._save_config()

    def get_currently_equipped_api_source(self) -> str:
        """Get the API source for currently equipped data ('blizzard' or 'warcraftlogs')."""
        return self._config.get("player_metrics", {}).get("currently_equipped_api_source", "blizzard")

    def set_currently_equipped_api_source(self, value: str) -> None:
        """Set the API source for currently equipped data ('blizzard' or 'warcraftlogs')."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {}
        self._config["player_metrics"]["currently_equipped_api_source"] = value
        self._save_config()

    def get_tank_priority(self) -> bool:
        """Get whether tank priority is enabled for loot council decisions."""
        return self._config.get("player_metrics", {}).get("tank_priority", False)

    def set_tank_priority(self, value: bool) -> None:
        """Set whether tank priority is enabled for loot council decisions."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {}
        self._config["player_metrics"]["tank_priority"] = value
        self._save_config()

    def get_show_raider_notes(self) -> bool:
        """Get whether to include raider notes in loot council prompts."""
        return self._config.get("player_metrics", {}).get("show_raider_notes", False)

    def set_show_raider_notes(self, value: bool) -> None:
        """Set whether to include raider notes in loot council prompts."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {}
        self._config["player_metrics"]["show_raider_notes"] = value
        self._save_config()

    def get_raider_note_source(self) -> str:
        """Get which TMB note field to use: 'public_note' or 'officer_note'."""
        return self._config.get("player_metrics", {}).get("raider_note_source", "public_note")

    def set_raider_note_source(self, value: str) -> None:
        """Set which TMB note field to use: 'public_note' or 'officer_note'."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {}
        self._config["player_metrics"]["raider_note_source"] = value
        self._save_config()

    def get_show_last_item_received(self) -> bool:
        """Get whether to show last item received for slot in loot council prompts."""
        return self._config.get("player_metrics", {}).get("show_last_item_received", False)

    def set_show_last_item_received(self, value: bool) -> None:
        """Set whether to show last item received for slot in loot council prompts."""
        if "player_metrics" not in self._config:
            self._config["player_metrics"] = {}
        self._config["player_metrics"]["show_last_item_received"] = value
        self._save_config()

    # =========================================================================
    # Export Path Configuration
    # =========================================================================

    def get_export_path(self) -> str:
        """Get export directory path."""
        if self._config["export_path"]:
            return self._config["export_path"]
        # Return default based on platform
        return self.DEFAULTS["export_path"]

    def set_export_path(self, value: str) -> None:
        """Set export directory path."""
        self._config["export_path"] = value
        self._save_config()

    # =========================================================================
    # LLM Configuration
    # =========================================================================

    def get_llm_provider(self) -> str:
        """Get the active LLM provider."""
        return self._config.get("llm", {}).get("active_provider", "anthropic")

    def set_llm_provider(self, value: str) -> None:
        """Set the active LLM provider."""
        if "llm" not in self._config:
            self._config["llm"] = self._deep_copy(self.DEFAULTS["llm"])
        self._config["llm"]["active_provider"] = value
        self._save_config()

    def get_llm_model(self) -> str:
        """Get the active LLM model."""
        return self._config.get("llm", {}).get("active_model", "claude-sonnet-4-20250514")

    def set_llm_model(self, value: str) -> None:
        """Set the active LLM model."""
        if "llm" not in self._config:
            self._config["llm"] = self._deep_copy(self.DEFAULTS["llm"])
        self._config["llm"]["active_model"] = value
        self._save_config()

    def get_llm_api_key(self, provider: str = None) -> str:
        """Get API key for a specific provider (or active provider if not specified)."""
        if provider is None:
            provider = self.get_llm_provider()
        return self._config.get("llm", {}).get("api_keys", {}).get(provider, "")

    def set_llm_api_key(self, api_key: str, provider: str = None) -> None:
        """Set API key for a specific provider (or active provider if not specified)."""
        if provider is None:
            provider = self.get_llm_provider()
        if "llm" not in self._config:
            self._config["llm"] = self._deep_copy(self.DEFAULTS["llm"])
        if "api_keys" not in self._config["llm"]:
            self._config["llm"]["api_keys"] = {}
        self._config["llm"]["api_keys"][provider] = api_key
        self._save_config()

    def get_llm_delay_seconds(self) -> float:
        """Get delay between API calls."""
        return self._config.get("llm", {}).get("delay_seconds", 2.0)

    def set_llm_delay_seconds(self, value: float) -> None:
        """Set delay between API calls."""
        if "llm" not in self._config:
            self._config["llm"] = self._deep_copy(self.DEFAULTS["llm"])
        self._config["llm"]["delay_seconds"] = value
        self._save_config()

    # =========================================================================
    # UI Configuration
    # =========================================================================

    def get_dark_mode(self) -> bool:
        """Get whether dark mode is enabled."""
        return self._config.get("ui", {}).get("dark_mode", False)

    def set_dark_mode(self, value: bool) -> None:
        """Set dark mode preference."""
        if "ui" not in self._config:
            self._config["ui"] = {"dark_mode": False}
        self._config["ui"]["dark_mode"] = value
        self._save_config()

    # =========================================================================
    # Bulk Operations
    # =========================================================================

    def save_all(self) -> None:
        """Force save current configuration to file."""
        self._save_config()

    def get_all(self) -> dict:
        """Get a copy of all configuration values."""
        return self._deep_copy(self._config)


# Global singleton instance
_config_manager = ConfigManager()


def get_config_manager() -> ConfigManager:
    """Get the global ConfigManager instance."""
    return _config_manager
