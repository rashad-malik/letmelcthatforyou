"""
Shared constants, globals, and utilities for the GUI configuration interface.
"""
import sys
from pathlib import Path

# Add src to path so we can import wowlc modules
src_path = Path(__file__).resolve().parent.parent.parent.parent
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from wowlc.core.paths import get_path_manager
from wowlc.core.config import get_config_manager

# Get the ConfigManager instance
config = get_config_manager()

# Get the PathManager instance
paths = get_path_manager()

# Path to raider notes JSON file (now in AppData)
RAIDER_NOTES_PATH = str(paths.get_raider_notes_path())

# Path to guild loot policy markdown file (now in Documents - FIXES PATH MISMATCH BUG)
POLICY_PATH = str(paths.get_guild_policy_path())

# Global variables for raider notes UI
raider_note_inputs = {}
raider_note_unsaved_labels = {}
raider_note_original_values = {}
raider_note_expansions = {}  # Maps raider_name -> (expansion_element, base_header_text)
raider_table_container = None

# Callback registry for cross-tab notifications
_metric_change_callbacks = []
_tmb_auth_callbacks = []
_game_version_callbacks = []
_blizzard_cred_callbacks = []
_connection_save_callbacks = []
_currently_equipped_callbacks = []


def clear_metric_change_callbacks():
    """Clear all registered metric change callbacks."""
    global _metric_change_callbacks
    _metric_change_callbacks = []


def register_metric_change_callback(callback):
    """Register a callback to be called when metric toggles change."""
    global _metric_change_callbacks
    # Clear existing callbacks first to avoid duplicates on page reload
    clear_metric_change_callbacks()
    _metric_change_callbacks.append(callback)
    print(f"[DEBUG] Registered metric change callback, total callbacks: {len(_metric_change_callbacks)}")


def notify_metric_change():
    """Notify all registered callbacks that metrics have changed."""
    print(f"[DEBUG] notify_metric_change called, {len(_metric_change_callbacks)} callbacks registered")
    for callback in _metric_change_callbacks:
        try:
            print(f"[DEBUG] Calling callback: {callback}")
            callback()
            print(f"[DEBUG] Callback completed successfully")
        except Exception as e:
            print(f"[DEBUG] Callback error: {e}")
            import traceback
            traceback.print_exc()


def clear_tmb_auth_callbacks():
    """Clear all registered TMB auth callbacks."""
    global _tmb_auth_callbacks
    _tmb_auth_callbacks = []


def register_tmb_auth_callback(callback):
    """Register a callback to be called when TMB authentication status changes."""
    # Clear existing callbacks first to avoid duplicates on page reload
    clear_tmb_auth_callbacks()
    _tmb_auth_callbacks.append(callback)


def notify_tmb_auth_change():
    """Notify all registered callbacks that TMB auth status has changed."""
    for callback in _tmb_auth_callbacks:
        try:
            callback()
        except Exception:
            pass  # Silently ignore errors in callbacks


def clear_game_version_callbacks():
    """Clear all registered game version callbacks."""
    global _game_version_callbacks
    _game_version_callbacks = []


def register_game_version_callback(callback):
    """Register a callback to be called when game version changes."""
    _game_version_callbacks.append(callback)


def notify_game_version_change():
    """Notify all registered callbacks that game version has changed."""
    for callback in _game_version_callbacks:
        try:
            callback()
        except Exception:
            pass  # Silently ignore errors in callbacks


def clear_blizzard_cred_callbacks():
    """Clear all registered Blizzard credential callbacks."""
    global _blizzard_cred_callbacks
    _blizzard_cred_callbacks = []


def register_blizzard_cred_callback(callback):
    """Register a callback to be called when Blizzard credentials are saved."""
    # Clear existing callbacks first to avoid duplicates on page reload
    clear_blizzard_cred_callbacks()
    _blizzard_cred_callbacks.append(callback)


def notify_blizzard_cred_change():
    """Notify all registered callbacks that Blizzard credentials have changed."""
    for callback in _blizzard_cred_callbacks:
        try:
            callback()
        except Exception:
            pass  # Silently ignore errors in callbacks


def clear_connection_save_callbacks():
    """Clear all registered connection save callbacks."""
    global _connection_save_callbacks
    _connection_save_callbacks = []


def register_connection_save_callback(callback):
    """Register a callback to be called when any core connection settings are saved."""
    # Clear existing callbacks first to avoid duplicates on page reload
    clear_connection_save_callbacks()
    _connection_save_callbacks.append(callback)


def notify_connection_save():
    """Notify all registered callbacks that connection settings have been saved."""
    for callback in _connection_save_callbacks:
        try:
            callback()
        except Exception:
            pass  # Silently ignore errors in callbacks


def clear_currently_equipped_callbacks():
    """Clear all registered currently equipped callbacks."""
    global _currently_equipped_callbacks
    _currently_equipped_callbacks = []


def register_currently_equipped_callback(callback):
    """Register a callback to be called when currently equipped settings change."""
    # Clear existing callbacks first to avoid duplicates on page reload
    clear_currently_equipped_callbacks()
    _currently_equipped_callbacks.append(callback)


def notify_currently_equipped_change():
    """Notify all registered callbacks that currently equipped settings have changed."""
    for callback in _currently_equipped_callbacks:
        try:
            callback()
        except Exception:
            pass  # Silently ignore errors in callbacks


# Field tracking for unsaved changes
_field_original_values: dict[str, any] = {}
_field_changed_indicators: dict[str, any] = {}


def clear_field_tracking():
    """Clear all field tracking (called on page reload)."""
    global _field_original_values, _field_changed_indicators
    _field_original_values = {}
    _field_changed_indicators = {}


def register_field_for_tracking(field_id: str, original_value: any, indicator_label):
    """Register a field for unsaved changes tracking."""
    _field_original_values[field_id] = original_value
    _field_changed_indicators[field_id] = indicator_label


def check_field_changed(field_id: str, current_value: any) -> bool:
    """Check if field value differs from original and update indicator."""
    original = _field_original_values.get(field_id)
    is_changed = str(current_value) != str(original)
    indicator = _field_changed_indicators.get(field_id)
    if indicator:
        indicator.visible = is_changed
    return is_changed


def mark_field_saved(field_id: str, new_value: any):
    """Update original value after save (clears changed state)."""
    _field_original_values[field_id] = new_value
    indicator = _field_changed_indicators.get(field_id)
    if indicator:
        indicator.visible = False
