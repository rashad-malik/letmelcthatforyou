"""
Centralized path management for WoW Loot Council.

Handles path resolution for both development and PyInstaller packaged environments.
Provides consistent access to user data, app data, and bundled resources.
"""

from pathlib import Path
import sys
import os
import platform
from typing import Optional
import json


def _get_xdg_documents_dir() -> Path:
    """
    Get the XDG documents directory on Linux.

    Reads from XDG_DOCUMENTS_DIR env var, or parses ~/.config/user-dirs.dirs,
    or falls back to ~/Documents.
    """
    # Check environment variable first
    xdg_docs = os.getenv('XDG_DOCUMENTS_DIR')
    if xdg_docs:
        return Path(xdg_docs)

    # Try to parse user-dirs.dirs
    user_dirs_file = Path.home() / ".config" / "user-dirs.dirs"
    if user_dirs_file.exists():
        try:
            content = user_dirs_file.read_text()
            for line in content.splitlines():
                if line.startswith('XDG_DOCUMENTS_DIR='):
                    # Format: XDG_DOCUMENTS_DIR="$HOME/Documents"
                    value = line.split('=', 1)[1].strip().strip('"')
                    # Replace $HOME with actual home path
                    value = value.replace('$HOME', str(Path.home()))
                    return Path(value)
        except Exception:
            pass

    # Default fallback
    return Path.home() / "Documents"


class PathManager:
    """
    Manages all file paths for the application.

    Detects if running as PyInstaller executable and provides appropriate paths
    for user data (Documents), application data (AppData), and bundled resources.
    """

    _instance = None

    def __new__(cls):
        """Singleton pattern to ensure one PathManager instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        """Initialize path manager (called once due to singleton pattern)."""
        if self._initialized:
            return

        self._initialized = True

        # Detect if running as PyInstaller executable
        self._is_frozen = getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')

        # Initialize paths
        if self._is_frozen:
            # Running as PyInstaller executable
            self._bundle_dir = Path(sys._MEIPASS)
            self._app_dir = Path(sys.executable).parent  # Directory containing .exe
        else:
            # Running as script
            # Navigate from paths.py: core -> wowlc -> src -> project_root
            self._bundle_dir = Path(__file__).resolve().parent.parent.parent.parent
            self._app_dir = self._bundle_dir

        # Platform-specific paths
        system = platform.system()

        if system == "Windows":
            # Windows: Documents + AppData\Local
            self._user_dir = Path.home() / "Documents" / "Let Me LC That For You"
            self._appdata_dir = Path(os.getenv('LOCALAPPDATA', Path.home() / "AppData" / "Local")) / "letmelcthatforyou"
        elif system == "Darwin":
            # macOS: Documents + Library/Application Support
            self._user_dir = Path.home() / "Documents" / "letmelcthatforyou"
            self._appdata_dir = Path.home() / "Library" / "Application Support" / "letmelcthatforyou"
        else:
            # Linux/other: XDG standard
            # User-visible files (Documents): exports, logs, raider_notes
            xdg_docs = _get_xdg_documents_dir()
            self._user_dir = xdg_docs / "Let Me LC That For You"

            # Application config/cache (Config home): config.json, auth, cache
            xdg_config = os.getenv('XDG_CONFIG_HOME', str(Path.home() / ".config"))
            self._appdata_dir = Path(xdg_config) / "letmelcthatforyou"

        # Ensure directories exist
        self._ensure_directories()

        # Load or create config
        self._config = self._load_config()

    def _load_config(self) -> dict:
        """Load user configuration from Documents/letmelcthatforyou/config.json."""
        config_path = self._user_dir / "config.json"

        default_config = {
            "export_path": str(self._user_dir / "Exports"),
            "version": "1.0"
        }

        if not config_path.exists():
            return default_config

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
                # Ensure export_path exists
                if "export_path" not in config:
                    config["export_path"] = default_config["export_path"]
                return config
        except (json.JSONDecodeError, IOError):
            return default_config

    def _save_config(self) -> None:
        """Save user configuration to Documents/letmelcthatforyou/config.json."""
        config_path = self._user_dir / "config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)

        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(self._config, f, indent=2)

    def _ensure_directories(self) -> None:
        """Create necessary directories on first run."""
        # User directories
        (self._user_dir / "Exports").mkdir(parents=True, exist_ok=True)

        # AppData directories
        (self._appdata_dir / "auth").mkdir(parents=True, exist_ok=True)
        (self._appdata_dir / "cache").mkdir(parents=True, exist_ok=True)

    # =========================================================================
    # Public API: Bundled Resources (read-only, inside exe)
    # =========================================================================

    def get_bundled_resource(self, relative_path: str) -> Optional[Path]:
        """
        Get path to a bundled resource file.

        Args:
            relative_path: Path relative to bundle root (e.g., "data/tbc_tokens.json")

        Returns:
            Absolute path to the resource, or None if not found.
        """
        if self._is_frozen:
            # Resources are in _MEIPASS
            resource_path = self._bundle_dir / relative_path
        else:
            # Resources are in project root
            resource_path = self._bundle_dir / relative_path

        return resource_path if resource_path.exists() else None

    def get_tbc_tokens_path(self) -> Optional[Path]:
        """Get path to TBC tier tokens data (bundled resource)."""
        return self.get_bundled_resource("data/tokens.json")

    # =========================================================================
    # Public API: User-Editable Files (Documents)
    # =========================================================================

    def get_guild_policy_path(self) -> Path:
        """Get path to guild loot policy (user-editable in Documents)."""
        return self._user_dir / "guild_loot_policy.md"

    def get_export_path(self, filename: str = "") -> Path:
        """
        Get path for CSV exports.

        Args:
            filename: Optional filename to append (e.g., "loot_decisions.csv")

        Returns:
            Full path to export file or directory.
        """
        export_dir = Path(self._config.get("export_path", str(self._user_dir / "Exports")))
        export_dir.mkdir(parents=True, exist_ok=True)

        if filename:
            return export_dir / filename
        return export_dir

    def set_export_path(self, new_path: str) -> None:
        """
        Set custom export path.

        Args:
            new_path: New directory path for exports.
        """
        self._config["export_path"] = new_path
        self._save_config()

    def get_user_config_path(self) -> Path:
        """Get path to user configuration file."""
        return self._user_dir / "config.json"

    # =========================================================================
    # Public API: Application Data (AppData)
    # =========================================================================

    def get_wcl_token_path(self) -> Path:
        """Get path to WCL user token."""
        return self._appdata_dir / "auth" / "wcl_user_token.json"

    def get_tmb_session_path(self) -> Path:
        """Get path to TMB session cookies."""
        return self._appdata_dir / "auth" / "tmb_session.json"

    def get_wcl_browser_profile_dir(self) -> Path:
        """Get path to WCL browser profile directory."""
        return self._appdata_dir / "auth" / ".wcl_browser_profile"

    def get_raider_cache_path(self) -> Path:
        """Get path to raider data cache."""
        return self._appdata_dir / "cache" / "raider_data_cache.pkl"

    def get_raider_gear_cache_path(self) -> Path:
        """Get path to raider gear cache (equipped items from WCL)."""
        return self._appdata_dir / "cache" / "raider_gear_cache.json"

    def get_nexus_cache_path(self) -> Path:
        """Get path to Nexus items cache."""
        return self._appdata_dir / "cache" / "nexus_items_cache.json"

    def get_openrouter_models_cache_path(self) -> Path:
        """Get path to OpenRouter models display name cache."""
        return self._appdata_dir / "cache" / "openrouter_models.json"

    def get_app_config_path(self) -> Path:
        """Get path to main application config file in AppData."""
        return self._appdata_dir / "config.json"

    def get_log_dir(self) -> Path:
        """Get path to log directory."""
        log_dir = self._user_dir / "Logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def is_frozen(self) -> bool:
        """Check if running as PyInstaller executable."""
        return self._is_frozen

    def get_app_dir(self) -> Path:
        """Get application directory (where .exe lives, or project root)."""
        return self._app_dir


# Global singleton instance
_path_manager = PathManager()


# Convenience functions for backward compatibility
def get_path_manager() -> PathManager:
    """Get the global PathManager instance."""
    return _path_manager
