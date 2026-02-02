"""
Settings tab for the GUI configuration interface.
Combines General Settings (server, cache) and Council Settings (metrics, raider notes).
"""
from nicegui import ui
import os
import json
from ..shared import (
    config,
    RAIDER_NOTES_PATH,
    POLICY_PATH,
    raider_note_inputs,
    raider_note_unsaved_labels,
    raider_note_original_values,
    raider_note_expansions,
    notify_metric_change,
    notify_currently_equipped_change,
    register_game_version_callback,
    register_blizzard_cred_callback,
    register_currently_equipped_callback,
    clear_currently_equipped_callbacks,
    register_field_for_tracking,
    check_field_changed,
    mark_field_saved,
)
from wowlc.core.paths import get_path_manager
from ...blizz_manager import get_access_token, fetch_realms
from ...tmb_manager import (
    TMBDataManager,
    TMBSessionNotFoundError,
    TMBSessionExpiredError,
    TMBFetchError
)

# Module-level cache for current realm data (name -> slug mapping)
_current_realms: dict[str, str] = {}

# Global cache for all realms by region (populated at app startup)
_realm_cache: dict[str, list[dict]] = {}

# Hardcoded TBC Anniversary realms (not yet in Blizzard API)
TBC_ANNIVERSARY_REALMS: dict[str, dict[str, str]] = {
    "US": {
        "Dreamscythe": "dreamscythe",
        "Nightslayer": "nightslayer",
    },
    "EU": {
        "Spineshatter": "spineshatter",
        "Thunderstrike": "thunderstrike",
    },
}

# Decision Priority metric display labels
METRIC_LABELS = {
    "wishlist_position": "Wishlist Position",
    "attendance": "Attendance",
    "recent_loot": "Recent Loot History",
    "ilvl_comparison": "Gear Upgrade (ilvl)",
    "parses": "Parses / Performance",
    "last_item_received": "Last Item Received",
    "tier_token_counts": "Tier Token Counts",
}

# Rule templates for generated rules preview
METRIC_RULE_TEMPLATES = {
    "wishlist_position": "Give preference to raiders who want this item more.",
    "attendance": "Give preference to raiders with higher attendance.",
    "recent_loot": "Give preference to raiders who received fewer items recently.",
    "ilvl_comparison": "Give preference to raiders with a larger ilvl upgrade.",
    "parses": "Give preference to raiders with better parses.",
    "last_item_received": "Give preference to raiders who received an item for this slot longest ago.",
    "tier_token_counts": "Prioritise raiders who are closer to completing 2 or 4 set tier bonus.",
}

# Metrics that have sub-settings (show gear icon)
METRICS_WITH_SETTINGS = {"attendance", "recent_loot", "parses"}

# Metrics requiring Currently Equipped to be enabled
METRICS_REQUIRING_EQUIPPED = {"ilvl_comparison", "tier_token_counts"}

# Short descriptions for each metric
METRIC_DESCRIPTIONS = {
    "wishlist_position": "Where this item ranks on the raider's wishlist.",
    "attendance": "The raider's raid attendance percentage.",
    "recent_loot": "How many items the raider received recently.",
    "ilvl_comparison": "The ilvl upgrade this item would provide.",
    "parses": "The raider's combat log performance.",
    "last_item_received": "When the raider last received an item for this slot.",
    "tier_token_counts": "How many tier set pieces the raider has equipped.",
}

# Descriptions for candidate rules
CANDIDATE_RULE_DESCRIPTIONS = {
    "show_alt_status": "Include alt characters as candidates for loot.",
    "mains_over_alts": "When alts are allowed, main characters are prioritised over alts.",
    "tank_priority": "Tank-role characters get priority for tank-relevant items.",
    "raider_notes": "Custom notes you've written about the raider.",
}


# --- Policy file helpers (moved from run_lc.py) ---

def ensure_policy_file():
    """Ensure policy file exists, create if not."""
    if not os.path.exists(POLICY_PATH):
        os.makedirs(os.path.dirname(POLICY_PATH), exist_ok=True)
        with open(POLICY_PATH, 'w', encoding='utf-8') as f:
            f.write('')


def load_policy_content():
    """Load policy content from markdown file."""
    ensure_policy_file()
    try:
        with open(POLICY_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except IOError:
        return ''


def save_policy_content(policy_text):
    """Save policy content to markdown file."""
    try:
        os.makedirs(os.path.dirname(POLICY_PATH), exist_ok=True)
        with open(POLICY_PATH, 'w', encoding='utf-8') as f:
            f.write(policy_text)
        ui.notify('Policy saved successfully', type='positive')
    except IOError as e:
        ui.notify(f'Error saving policy: {str(e)}', type='negative')


# --- Blizzard realm helpers (from general.py) ---

def check_blizzard_credentials() -> bool:
    """Check if Blizzard credentials are configured."""
    return bool(config.get_blizzard_client_id() and config.get_blizzard_client_secret())


def get_namespace(region: str) -> str:
    """Build namespace string for Blizzard API."""
    return f"dynamic-classic1x-{region.lower()}"


def is_valid_realm_id(realm_id: int, _game_version: str, region: str) -> bool:
    """Check if a realm ID is valid for Era (TBC Anniversary uses hardcoded realms)."""
    id_str = str(realm_id)

    if region.upper() == "EU":
        # Era realms in EU: ID starts with 55, or ID starts with 52 and >= 5220 (excluding 5244 EU5 CWOW Web)
        if id_str.startswith("55"):
            return True
        if id_str.startswith("52") and realm_id >= 5220 and realm_id != 5244:
            return True
        return False
    else:  # US
        # Era realms in US: ID starts with 50, or ID starts with 51 and <= 5150
        if id_str.startswith("50"):
            return True
        if id_str.startswith("51") and realm_id <= 5150:
            return True
        return False


def prefetch_realms():
    """Fetch and cache realms for all regions at app startup."""
    if not check_blizzard_credentials():
        return

    token = get_access_token()
    if not token:
        return

    for region in ["eu", "us"]:
        namespace = get_namespace(region)
        realms = fetch_realms(token, region, namespace)
        _realm_cache[region.upper()] = realms


def get_cached_realms(region: str) -> list[dict]:
    """Get cached realms for a region, fetching if not cached."""
    region_upper = region.upper()
    if region_upper not in _realm_cache:
        if not check_blizzard_credentials():
            return []
        token = get_access_token()
        if not token:
            return []
        namespace = get_namespace(region)
        _realm_cache[region_upper] = fetch_realms(token, region.lower(), namespace)

    return _realm_cache.get(region_upper, [])


def fetch_realm_data(game_version: str, region: str) -> dict[str, str]:
    """Get filtered realms based on game version and region."""
    # TBC Anniversary realms are hardcoded (not in Blizzard API yet)
    if game_version == "TBC Anniversary":
        return TBC_ANNIVERSARY_REALMS.get(region.upper(), {})

    # Era realms come from the API
    realms = get_cached_realms(region)

    return {
        r["name"]: r["slug"]
        for r in realms
        if r.get("name") and r.get("slug") and is_valid_realm_id(r.get("id", 0), game_version, region)
    }


def update_server_options(server_region, server_slug, game_version_toggle):
    """Update the server dropdown based on selected region and game version."""
    global _current_realms

    region = server_region.value
    game_version = game_version_toggle.value

    if not check_blizzard_credentials():
        server_slug.options = []
        server_slug.value = None
        return

    if region and game_version:
        _current_realms = fetch_realm_data(game_version, region)

        if _current_realms:
            realm_names = sorted(_current_realms.keys())
            server_slug.options = realm_names
            server_slug.value = realm_names[0]
        else:
            server_slug.options = []
            server_slug.value = None
            ui.notify(f'No realms found for {region} - {game_version}', type='warning')
    else:
        server_slug.options = []
        server_slug.value = None


# --- Raider notes helpers (from council.py) ---

def ensure_raider_notes_file():
    """Ensure raider notes file exists, create if not."""
    if not os.path.exists(RAIDER_NOTES_PATH):
        os.makedirs(os.path.dirname(RAIDER_NOTES_PATH), exist_ok=True)
        with open(RAIDER_NOTES_PATH, 'w') as f:
            json.dump({}, f, indent=2)


def load_raider_notes():
    """Load existing raider notes from JSON file."""
    ensure_raider_notes_file()
    try:
        with open(RAIDER_NOTES_PATH, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_raider_notes(notes_dict):
    """Save raider notes to JSON file."""
    os.makedirs(os.path.dirname(RAIDER_NOTES_PATH), exist_ok=True)
    with open(RAIDER_NOTES_PATH, 'w') as f:
        json.dump(notes_dict, f, indent=2)


def fetch_raiders(tmb_guild_id, raider_table_container):
    """Fetch raiders from TMB and populate the table."""
    global raider_note_inputs, raider_note_unsaved_labels, raider_note_original_values, raider_note_expansions
    try:
        guild_id = tmb_guild_id.value.strip()
        if not guild_id:
            ui.notify('TMB Guild ID is required', type='negative')
            return

        guild_slug = "placeholder"
        manager = TMBDataManager(guild_id=guild_id, guild_slug=guild_slug)

        profiles_df = manager.get_raider_profiles()
        existing_notes = load_raider_notes()

        raider_table_container.clear()
        raider_note_inputs.clear()
        raider_note_unsaved_labels.clear()
        raider_note_original_values.clear()
        raider_note_expansions.clear()

        with raider_table_container:
            for _, raider in profiles_df.iterrows():
                raider_name = raider['name']
                class_spec = f"{raider['class']} ({raider['spec']})" if raider['spec'] else raider['class']
                main_alt = "Alt" if raider['is_alt'] else "Main"
                existing_note = existing_notes.get(raider_name, "")

                # Base header text (without emoji) and full header with emoji if has notes
                base_header = f"{raider_name} - {class_spec} ({main_alt})"
                has_note_marker = " 📝" if existing_note else ""
                header_text = f"{base_header}{has_note_marker}"

                expansion = ui.expansion(header_text, icon='person').classes('w-full')
                raider_note_expansions[raider_name] = (expansion, base_header)

                with expansion:
                    note_input = ui.input(
                        label='Custom Notes',
                        value=existing_note,
                        placeholder='Add custom notes for this raider...'
                    ).props('maxlength=200').classes('w-full')
                    note_input.raider_name = raider_name
                    raider_note_inputs[raider_name] = note_input
                    raider_note_original_values[raider_name] = existing_note

                    # Character counter and unsaved indicator row
                    with ui.row().classes('w-full justify-between items-center'):
                        char_count_label = ui.label(f'{len(existing_note)}/200').classes('text-xs text-gray-500')
                        unsaved_label = ui.label('Unsaved changes!').classes('text-xs text-red-500')
                        unsaved_label.visible = False
                        raider_note_unsaved_labels[raider_name] = unsaved_label

                    def make_change_handler(counter_label, unsaved_lbl, name):
                        def on_change(e):
                            new_value = e.args
                            counter_label.text = f'{len(new_value)}/200'
                            original = raider_note_original_values.get(name, "")
                            unsaved_lbl.visible = (new_value != original)
                        return on_change

                    note_input.on('update:model-value', make_change_handler(char_count_label, unsaved_label, raider_name))

        ui.notify(f'Loaded {len(profiles_df)} raiders', type='positive')

    except TMBSessionNotFoundError as e:
        ui.notify(f'TMB session not found: {str(e)}', type='negative')
    except TMBSessionExpiredError as e:
        ui.notify(f'TMB session expired: {str(e)}', type='negative')
    except TMBFetchError as e:
        ui.notify(f'Failed to fetch raiders: {str(e)}', type='negative')
    except Exception as e:
        ui.notify(f'Error fetching raiders: {str(e)}', type='negative')


def save_all_raider_notes():
    """Save all raider notes from the input fields to JSON."""
    notes_dict = {}
    for raider_name, note_input in raider_note_inputs.items():
        current_value = note_input.value.strip()
        if current_value:
            notes_dict[raider_name] = current_value
        # Update original value and hide unsaved label
        raider_note_original_values[raider_name] = current_value
        if raider_name in raider_note_unsaved_labels:
            raider_note_unsaved_labels[raider_name].visible = False
        # Update expansion header to show/hide note emoji
        if raider_name in raider_note_expansions:
            expansion, base_header = raider_note_expansions[raider_name]
            emoji = " 📝" if current_value else ""
            expansion._props['label'] = f"{base_header}{emoji}"
            expansion.update()

    save_raider_notes(notes_dict)
    ui.notify('Raider notes saved!', type='positive')


# --- Main tab creation ---

def create_settings_tab(tmb_guild_id_ref, game_version_toggle):
    """Build the combined Settings tab UI and return UI element references."""
    global _current_realms

    # Clear callbacks from previous page loads to avoid duplicates
    clear_currently_equipped_callbacks()

    ui_refs = {}

    # ==================== SECTION 1: Player Metrics ====================

    # Parse zone options by game version (used by parses settings)
    TBC_ZONE_OPTIONS = {
        1007: "Karazhan",
        1008: "Gruul/Mag",
        1010: "SSC/TK",
        1011: "BT/Hyjal",
        1012: "Zul'Aman",
        1013: "Sunwell",
    }

    ERA_ZONE_OPTIONS = {
        1028: "Molten Core",
        1034: "Blackwing Lair",
        1035: "Temple of Ahn'Qiraj",
        1036: "Naxxramas",
    }

    PARSE_FILTER_OPTIONS = {
        "dps_only": "DPS Only",
        "everyone": "Everyone",
    }

    def get_zone_options_for_version():
        version = game_version_toggle.value if hasattr(game_version_toggle, 'value') else 'Era'
        if version == 'Era':
            return ERA_ZONE_OPTIONS
        return TBC_ZONE_OPTIONS

    # --- Section 1A: Candidate Rules ---
    with ui.card().classes('w-full p-4 mb-4'):
        with ui.row().classes('w-full items-center gap-2 mb-2'):
            ui.icon('group')
            ui.label('Candidate Rules').classes('text-lg font-semibold')

        ui.label('Rules about who can be considered for loot.').classes('text-sm text-gray-500 mb-4')

        with ui.column().classes('w-full gap-2'):
            # Allow Alts toggle
            def on_alt_status_toggle(enabled: bool):
                config.set_show_alt_status(enabled)
                ui_refs['mains_over_alts_container'].set_visibility(enabled)
                notify_metric_change()

            with ui.row().classes('items-center gap-2 w-full'):
                ui_refs['show_alt_status'] = ui.checkbox(
                    value=config.get_show_alt_status(),
                    on_change=lambda e: on_alt_status_toggle(e.value)
                )
                with ui.column().classes('flex-1 gap-0'):
                    ui.label('Allow Alts').classes('font-medium')
                    ui.label(CANDIDATE_RULE_DESCRIPTIONS['show_alt_status']).classes('text-xs text-gray-500')

            # Mains over alts sub-option (indented)
            with ui.element('div').classes('pl-8') as mains_container:
                def on_mains_over_alts_toggle(enabled: bool):
                    config.set_mains_over_alts(enabled)
                    notify_metric_change()

                with ui.row().classes('items-center gap-2 w-full'):
                    ui_refs['mains_over_alts'] = ui.checkbox(
                        value=config.get_mains_over_alts(),
                        on_change=lambda e: on_mains_over_alts_toggle(e.value)
                    )
                    with ui.column().classes('flex-1 gap-0'):
                        ui.label('Give priority to Mains').classes('font-medium')
                        ui.label(CANDIDATE_RULE_DESCRIPTIONS['mains_over_alts']).classes('text-xs text-gray-500')

            ui_refs['mains_over_alts_container'] = mains_container
            mains_container.set_visibility(config.get_show_alt_status())

            # Tank Priority toggle
            def on_tank_priority_toggle(enabled: bool):
                config.set_tank_priority(enabled)
                notify_metric_change()

            with ui.row().classes('items-center gap-2 w-full'):
                ui_refs['tank_priority'] = ui.checkbox(
                    value=config.get_tank_priority(),
                    on_change=lambda e: on_tank_priority_toggle(e.value)
                )
                with ui.column().classes('flex-1 gap-0'):
                    ui.label('Tank Priority').classes('font-medium')
                    ui.label(CANDIDATE_RULE_DESCRIPTIONS['tank_priority']).classes('text-xs text-gray-500')

            # Raider Notes toggle (moved from metrics)
            def on_raider_notes_toggle(enabled: bool):
                config.set_show_raider_notes(enabled)
                notify_metric_change()

            with ui.row().classes('items-center gap-2 w-full'):
                ui_refs['show_raider_notes'] = ui.checkbox(
                    value=config.get_show_raider_notes(),
                    on_change=lambda e: on_raider_notes_toggle(e.value)
                )
                with ui.column().classes('flex-1 gap-0'):
                    ui.label('Include Raider Notes').classes('font-medium')
                    ui.label(CANDIDATE_RULE_DESCRIPTIONS['raider_notes']).classes('text-xs text-gray-500')

    # --- Section 1B: Policy Mode ---
    with ui.card().classes('w-full p-4 mb-4'):
        with ui.row().classes('w-full items-center gap-2 mb-2'):
            ui.icon('tune')
            ui.label('Policy Mode').classes('text-lg font-semibold')

        ui.label('Simple mode uses priority rules below. Custom mode uses your written policy.').classes('text-sm text-gray-500 mb-4')

        def on_policy_mode_change(e):
            mode = e.sender.value
            is_simple = mode == 'Simple'
            config.set_policy_mode('simple' if is_simple else 'custom')
            simple_mode_container.set_visibility(is_simple)
            custom_mode_container.set_visibility(not is_simple)
            notify_metric_change()

        ui_refs['policy_mode'] = ui.toggle(
            ['Simple', 'Custom'],
            value='Simple' if config.get_policy_mode() == 'simple' else 'Custom',
            on_change=on_policy_mode_change
        )

    # --- Shared helper functions for metrics (used by both Simple and Custom modes) ---
    metric_settings_panels = {}
    custom_metric_settings_panels = {}
    metric_checkboxes = {}
    custom_metric_checkboxes = {}
    metric_rows = {}

    def get_metric_enabled(metric_id: str) -> bool:
        """Get whether a metric is enabled in config."""
        mapping = {
            "wishlist_position": config.get_show_wishlist_position,
            "attendance": config.get_show_attendance,
            "recent_loot": config.get_show_recent_loot,
            "ilvl_comparison": config.get_show_ilvl_comparisons,
            "parses": config.get_show_parses,
            "last_item_received": config.get_show_last_item_received,
            "tier_token_counts": config.get_show_tier_token_counts,
        }
        return mapping.get(metric_id, lambda: False)()

    def set_metric_enabled(metric_id: str, enabled: bool):
        """Set whether a metric is enabled in config."""
        mapping = {
            "wishlist_position": config.set_show_wishlist_position,
            "attendance": config.set_show_attendance,
            "recent_loot": config.set_show_recent_loot,
            "ilvl_comparison": config.set_show_ilvl_comparisons,
            "parses": config.set_show_parses,
            "last_item_received": config.set_show_last_item_received,
            "tier_token_counts": config.set_show_tier_token_counts,
        }
        setter = mapping.get(metric_id)
        if setter:
            setter(enabled)

    def is_metric_available(metric_id: str) -> bool:
        """Check if a metric is available (not blocked by dependencies)."""
        if metric_id in METRICS_REQUIRING_EQUIPPED:
            return config.get_currently_equipped_enabled()
        return True

    def get_clean_metric_order():
        """Get metric order, ensuring all metrics are present."""
        all_metrics = list(METRIC_LABELS.keys())
        current_order = config.get_metric_order()
        seen = set()
        clean_order = []

        for m in current_order:
            if m not in seen and m in all_metrics:
                seen.add(m)
                clean_order.append(m)

        for m in all_metrics:
            if m not in seen:
                clean_order.append(m)
                seen.add(m)

        if clean_order != current_order:
            config.set_metric_order(clean_order)

        return clean_order

    # --- Section 1C: Decision Priorities (Simple mode) ---
    simple_mode_container = ui.column().classes('w-full')
    ui_refs['simple_mode_container'] = simple_mode_container

    with simple_mode_container:
        with ui.card().classes('w-full p-4 mb-4'):
            with ui.row().classes('w-full items-center gap-2 mb-2'):
                ui.icon('sort')
                ui.label('Decision Priorities').classes('text-lg font-semibold')

            ui.label('Drag metrics to set priority order. Top = highest priority.').classes('text-sm text-gray-500 mb-4')

            def toggle_settings_panel(metric_id: str):
                """Toggle visibility of a metric's settings panel."""
                panel = metric_settings_panels.get(metric_id)
                if panel:
                    panel.set_visibility(not panel.visible)

            def toggle_custom_settings_panel(metric_id: str):
                """Toggle visibility of a metric's settings panel in Custom mode."""
                panel = custom_metric_settings_panels.get(metric_id)
                if panel:
                    panel.set_visibility(not panel.visible)

            def on_metric_checkbox_change(metric_id: str, enabled: bool):
                """Handle metric checkbox toggle."""
                set_metric_enabled(metric_id, enabled)
                # Update row styling
                row = metric_rows.get(metric_id)
                if row:
                    if enabled:
                        row.classes(remove='opacity-50')
                    else:
                        row.classes(add='opacity-50')
                # Refresh rule preview
                rule_preview.refresh()
                notify_metric_change()

            def on_metric_reorder(new_order):
                """Handle metric reordering from drag-drop."""
                if new_order:
                    config.set_metric_order(new_order)
                    rule_preview.refresh()
                    notify_metric_change()

            def update_equipped_dependent_metrics():
                """Update state of metrics that depend on Currently Equipped."""
                equipped_enabled = config.get_currently_equipped_enabled()
                for metric_id in METRICS_REQUIRING_EQUIPPED:
                    checkbox = metric_checkboxes.get(metric_id)
                    row = metric_rows.get(metric_id)
                    if checkbox:
                        if equipped_enabled:
                            checkbox.enable()
                        else:
                            checkbox.disable()
                            # Also uncheck if disabled
                            if checkbox.value:
                                checkbox.value = False
                                set_metric_enabled(metric_id, False)
                    if row:
                        if not equipped_enabled:
                            row.classes(add='opacity-50')
                        elif get_metric_enabled(metric_id):
                            row.classes(remove='opacity-50')
                rule_preview.refresh()

            def update_custom_equipped_dependent_metrics():
                """Update state of metrics that depend on Currently Equipped in Custom mode."""
                equipped_enabled = config.get_currently_equipped_enabled()
                for metric_id in METRICS_REQUIRING_EQUIPPED:
                    checkbox = custom_metric_checkboxes.get(metric_id)
                    if checkbox:
                        if equipped_enabled:
                            checkbox.enable()
                        else:
                            checkbox.disable()
                            if checkbox.value:
                                checkbox.value = False
                                set_metric_enabled(metric_id, False)

            # Sortable container
            with ui.column().classes('w-full sortable-metrics gap-1'):
                metric_order = get_clean_metric_order()

                for idx, metric_id in enumerate(metric_order):
                    is_enabled = get_metric_enabled(metric_id)
                    is_available = is_metric_available(metric_id)
                    has_settings = metric_id in METRICS_WITH_SETTINGS

                    # Metric row card
                    row_classes = 'w-full metric-item p-2 rounded border'
                    if not is_enabled or not is_available:
                        row_classes += ' opacity-50'

                    with ui.card().classes(row_classes) as row:
                        row._props['data-id'] = metric_id
                        metric_rows[metric_id] = row

                        with ui.row().classes('items-center gap-2 w-full'):
                            # Drag handle
                            ui.icon('drag_indicator').classes('text-gray-400 cursor-grab drag-handle')

                            # Checkbox
                            checkbox = ui.checkbox(
                                value=is_enabled and is_available,
                            )
                            if not is_available:
                                checkbox.disable()
                            checkbox.on_value_change(
                                lambda e, mid=metric_id: on_metric_checkbox_change(mid, e.value)
                            )
                            metric_checkboxes[metric_id] = checkbox

                            # Label and description
                            with ui.column().classes('flex-1 gap-0'):
                                label_text = METRIC_LABELS.get(metric_id, metric_id)
                                if metric_id in METRICS_REQUIRING_EQUIPPED:
                                    label_text += " (requires Currently Equipped)"
                                ui.label(label_text).classes('font-medium')
                                ui.label(METRIC_DESCRIPTIONS.get(metric_id, '')).classes('text-xs text-gray-500')

                            # Settings gear icon (if metric has settings)
                            if has_settings:
                                ui.button(
                                    icon='settings',
                                    on_click=lambda mid=metric_id: toggle_settings_panel(mid)
                                ).props('flat dense round').classes('text-gray-500')

                        # Settings panel (hidden by default)
                        if has_settings:
                            with ui.element('div').classes('pl-8 pt-2 w-full') as settings_panel:
                                settings_panel.set_visibility(False)
                                metric_settings_panels[metric_id] = settings_panel

                                if metric_id == "attendance":
                                    def save_attendance_lookback(e):
                                        try:
                                            val = int(e.value) if e.value else 60
                                        except ValueError:
                                            val = 60
                                        config.set_attendance_lookback_days(val)

                                    ui_refs['attendance_lookback_days'] = ui.input(
                                        label='Attendance Lookback Days',
                                        value=str(config.get_attendance_lookback_days()),
                                        on_change=save_attendance_lookback
                                    ).classes('w-full max-w-xs')
                                    ui.label('Number of days to consider for attendance calculation.').classes('text-xs text-gray-500')

                                elif metric_id == "recent_loot":
                                    def save_loot_lookback(e):
                                        try:
                                            val = int(e.value) if e.value else 14
                                        except ValueError:
                                            val = 14
                                        config.set_loot_lookback_days(val)

                                    ui_refs['loot_lookback_days'] = ui.input(
                                        label='Loot Lookback Days',
                                        value=str(config.get_loot_lookback_days()),
                                        on_change=save_loot_lookback
                                    ).classes('w-full max-w-xs')
                                    ui.label('Number of days to consider for recent loot history.').classes('text-xs text-gray-500')

                                elif metric_id == "parses":
                                    def on_zone_change(e):
                                        zone_id = e.value
                                        if zone_id:
                                            zone_options = get_zone_options_for_version()
                                            zone_label = zone_options.get(zone_id, "")
                                            config.set_parse_zone_id(zone_id)
                                            config.set_parse_zone_label(zone_label)
                                        else:
                                            config.set_parse_zone_id(None)
                                            config.set_parse_zone_label("")

                                    ui_refs['parse_zone_select'] = ui.select(
                                        label='Parse Zone',
                                        options=get_zone_options_for_version(),
                                        value=config.get_parse_zone_id() if config.get_parse_zone_id() in get_zone_options_for_version() else None,
                                        on_change=on_zone_change
                                    ).classes('w-full max-w-xs')

                                    ui_refs['parse_filter_select'] = ui.select(
                                        label='Fetch Parses For',
                                        options=PARSE_FILTER_OPTIONS,
                                        value=config.get_parse_filter_mode(),
                                        on_change=lambda e: config.set_parse_filter_mode(e.value)
                                    ).classes('w-full max-w-xs')

                                    def refresh_parse_zone_options():
                                        new_options = get_zone_options_for_version()
                                        ui_refs['parse_zone_select'].options = new_options
                                        current_value = ui_refs['parse_zone_select'].value
                                        if current_value not in new_options:
                                            ui_refs['parse_zone_select'].value = None
                                            config.set_parse_zone_id(None)
                                            config.set_parse_zone_label("")
                                        ui_refs['parse_zone_select'].update()

                                    register_game_version_callback(refresh_parse_zone_options)

            # SortableJS integration
            ui.add_body_html('''
            <script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js"></script>
            <script>
            function initSortableMetrics() {
                const container = document.querySelector('.sortable-metrics');
                if (container && !container._sortableInit) {
                    container._sortableInit = true;
                    Sortable.create(container, {
                        animation: 150,
                        ghostClass: 'opacity-30',
                        handle: '.drag-handle',
                        onEnd: function(evt) {
                            const items = Array.from(container.querySelectorAll('.metric-item'))
                                .map(el => el.dataset.id)
                                .filter(id => id);
                            if (items.length > 0) {
                                emitEvent('metric-reorder', {order: items});
                            }
                        }
                    });
                }
            }
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', function() {
                    setTimeout(initSortableMetrics, 500);
                });
            } else {
                setTimeout(initSortableMetrics, 500);
            }
            new MutationObserver(function() {
                setTimeout(initSortableMetrics, 100);
            }).observe(document.body, {childList: true, subtree: true});
            </script>
            ''')

            ui.on('metric-reorder', lambda e: on_metric_reorder(e.args.get('order', [])))

        # --- Generated Rules Preview (inside Simple mode container) ---
        with ui.card().classes('w-full p-4 mb-4'):
            with ui.row().classes('w-full items-center gap-2 mb-2'):
                ui.icon('preview')
                ui.label('Generated Rules Preview').classes('text-lg font-semibold')

            ui.label('These rules will be sent to the LLM based on your settings above.').classes('text-sm text-gray-500 mb-4')

            @ui.refreshable
            def rule_preview():
                """Render the generated rules preview."""
                metric_order = get_clean_metric_order()

                with ui.column().classes('w-full bg-gray-100 dark:bg-gray-800 p-3 rounded'):
                    rule_num = 1

                    # Decision Priority rules only (Candidate Rules are not shown here)
                    for metric_id in metric_order:
                        if get_metric_enabled(metric_id) and is_metric_available(metric_id):
                            rule_text = METRIC_RULE_TEMPLATES.get(metric_id, "")
                            if rule_text:
                                ui.label(f"RULE {rule_num}: {rule_text}").classes('text-sm font-mono')
                                rule_num += 1

                    if rule_num == 1:
                        ui.label("No rules configured. Enable metrics above to generate rules.").classes('text-sm text-gray-500 italic')

            rule_preview()

    # --- Section 1D: Tracked Metrics + Custom Policy (Custom mode) ---
    custom_mode_container = ui.column().classes('w-full')
    ui_refs['custom_mode_container'] = custom_mode_container

    with custom_mode_container:
        # Tracked Metrics card (checkboxes only, no drag-drop)
        with ui.card().classes('w-full p-4 mb-4'):
            with ui.row().classes('w-full items-center gap-2 mb-2'):
                ui.icon('checklist')
                ui.label('Tracked Metrics').classes('text-lg font-semibold')

            ui.label('Select which metrics to display in candidate information.').classes('text-sm text-gray-500 mb-4')

            with ui.column().classes('w-full gap-2'):
                # Sort metrics alphabetically by display label
                for metric_id in sorted(METRIC_LABELS.keys(), key=lambda x: METRIC_LABELS[x]):
                    is_enabled = get_metric_enabled(metric_id)
                    is_available = is_metric_available(metric_id)
                    has_settings = metric_id in METRICS_WITH_SETTINGS

                    with ui.column().classes('w-full'):
                        with ui.row().classes('items-center gap-2 w-full'):
                            checkbox = ui.checkbox(
                                value=is_enabled and is_available,
                            )
                            if not is_available:
                                checkbox.disable()

                            # Store reference for dependent metrics so they can be updated
                            if metric_id in METRICS_REQUIRING_EQUIPPED:
                                custom_metric_checkboxes[metric_id] = checkbox

                            def make_handler(mid):
                                def handler(e):
                                    set_metric_enabled(mid, e.value)
                                    notify_metric_change()
                                return handler

                            checkbox.on_value_change(make_handler(metric_id))

                            # Label and description
                            with ui.column().classes('flex-1 gap-0'):
                                label_text = METRIC_LABELS.get(metric_id, metric_id)
                                if metric_id in METRICS_REQUIRING_EQUIPPED:
                                    label_text += " (requires Currently Equipped)"
                                ui.label(label_text).classes('font-medium')
                                ui.label(METRIC_DESCRIPTIONS.get(metric_id, '')).classes('text-xs text-gray-500')

                            # Settings gear icon (if metric has settings)
                            if has_settings:
                                ui.button(
                                    icon='settings',
                                    on_click=lambda mid=metric_id: toggle_custom_settings_panel(mid)
                                ).props('flat dense round').classes('text-gray-500')

                        # Settings panel (hidden by default)
                        if has_settings:
                            with ui.element('div').classes('pl-8 pt-2 w-full') as settings_panel:
                                settings_panel.set_visibility(False)
                                custom_metric_settings_panels[metric_id] = settings_panel

                                if metric_id == "attendance":
                                    def save_attendance_lookback_custom(e):
                                        try:
                                            val = int(e.value) if e.value else 60
                                        except ValueError:
                                            val = 60
                                        config.set_attendance_lookback_days(val)

                                    ui_refs['attendance_lookback_days_custom'] = ui.input(
                                        label='Attendance Lookback Days',
                                        value=str(config.get_attendance_lookback_days()),
                                        on_change=save_attendance_lookback_custom
                                    ).classes('w-full max-w-xs')
                                    ui.label('Number of days to consider for attendance calculation.').classes('text-xs text-gray-500')

                                elif metric_id == "recent_loot":
                                    def save_loot_lookback_custom(e):
                                        try:
                                            val = int(e.value) if e.value else 14
                                        except ValueError:
                                            val = 14
                                        config.set_loot_lookback_days(val)

                                    ui_refs['loot_lookback_days_custom'] = ui.input(
                                        label='Loot Lookback Days',
                                        value=str(config.get_loot_lookback_days()),
                                        on_change=save_loot_lookback_custom
                                    ).classes('w-full max-w-xs')
                                    ui.label('Number of days to consider for recent loot history.').classes('text-xs text-gray-500')

                                elif metric_id == "parses":
                                    def on_zone_change_custom(e):
                                        zone_id = e.value
                                        if zone_id:
                                            zone_options = get_zone_options_for_version()
                                            zone_label = zone_options.get(zone_id, "")
                                            config.set_parse_zone_id(zone_id)
                                            config.set_parse_zone_label(zone_label)
                                        else:
                                            config.set_parse_zone_id(None)
                                            config.set_parse_zone_label("")

                                    ui_refs['parse_zone_select_custom'] = ui.select(
                                        label='Parse Zone',
                                        options=get_zone_options_for_version(),
                                        value=config.get_parse_zone_id() if config.get_parse_zone_id() in get_zone_options_for_version() else None,
                                        on_change=on_zone_change_custom
                                    ).classes('w-full max-w-xs')

                                    ui_refs['parse_filter_select_custom'] = ui.select(
                                        label='Fetch Parses For',
                                        options=PARSE_FILTER_OPTIONS,
                                        value=config.get_parse_filter_mode(),
                                        on_change=lambda e: config.set_parse_filter_mode(e.value)
                                    ).classes('w-full max-w-xs')

                                    def refresh_parse_zone_options_custom():
                                        new_options = get_zone_options_for_version()
                                        ui_refs['parse_zone_select_custom'].options = new_options
                                        current_value = ui_refs['parse_zone_select_custom'].value
                                        if current_value not in new_options:
                                            ui_refs['parse_zone_select_custom'].value = None
                                            config.set_parse_zone_id(None)
                                            config.set_parse_zone_label("")
                                        ui_refs['parse_zone_select_custom'].update()

                                    register_game_version_callback(refresh_parse_zone_options_custom)

        # Custom Policy Editor card
        with ui.card().classes('w-full p-4 mb-4'):
            with ui.row().classes('w-full items-center gap-2 mb-2'):
                ui.icon('edit_note')
                ui.label('Custom Loot Policy').classes('text-lg font-semibold')

            ui.label('Write your custom guild loot policy below. This will be sent to the LLM.').classes('text-sm text-gray-500 mb-4')

            policy_editor = ui.textarea(
                label='Guild Loot Policy',
                value=load_policy_content()
            ).classes('w-full').props('rows=8 outlined counter')
            ui_refs['policy_editor'] = policy_editor

            # Warning label for excessive length
            warning_label = ui.label('').classes('text-xs')

            def update_policy_warning():
                char_count = len(policy_editor.value or '')
                if char_count > 600:
                    warning_label.text = f'Warning: Excessive policy length ({char_count} chars) can reduce AI response quality and increase API costs.'
                    warning_label.classes(replace='text-xs text-orange-500')
                else:
                    warning_label.text = ''
                    warning_label.classes(replace='text-xs')

            policy_editor.on('update:model-value', lambda: update_policy_warning())
            update_policy_warning()

            ui.button(
                'Save Policy',
                icon='save',
                on_click=lambda: save_policy_content(policy_editor.value)
            ).classes('mt-2')

    # Set initial visibility based on saved policy mode
    is_simple_mode = config.get_policy_mode() == 'simple'
    simple_mode_container.set_visibility(is_simple_mode)
    custom_mode_container.set_visibility(not is_simple_mode)

    # Register callbacks for Currently Equipped changes (applies to both modes)
    register_currently_equipped_callback(update_equipped_dependent_metrics)
    register_currently_equipped_callback(update_custom_equipped_dependent_metrics)

    # ==================== SECTION 1.5: Currently Equipped ====================
    with ui.card().classes('w-full p-4 mb-4'):
        with ui.row().classes('w-full items-center justify-between mb-4'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('checkroom')
                ui.label('Currently Equipped').classes('text-lg font-semibold')

        ui.label(
            'If you would like ilvl comparisons, or tier token counts in loot council decisions, '
            'you can activate the "Currently Equipped" metric. You will need to select either the '
            'Blizzard API or Warcraftlogs API:'
        ).classes('text-sm mb-2')

        with ui.column().classes('w-full gap-1 mb-4'):
            ui.label(
                '- If you select the Blizzard API, the app will check the character\'s currently equipped gear.'
            ).classes('text-sm text-gray-600')
            ui.label(
                '- If you select the Warcraftlogs API, the app will scan WCL to find their last log and '
                'return gear equipped during the last encounter of that log.'
            ).classes('text-sm text-gray-600')

        # Disclaimer box
        with ui.element('div').classes('w-full p-3 bg-amber-100 rounded mb-4'):
            ui.label('IMPORTANT: Either method has flaws that need to be considered:').classes('text-sm font-semibold text-amber-800 mb-2')
            ui.label(
                '- The Blizzard API looks at currently equipped items. The character could have logged off '
                'with items that don\'t reflect their PvE set (e.g. PvP set, RP set etc.).'
            ).classes('text-sm text-amber-800')
            ui.label(
                '- The Warcraftlogs API looks at the items they used in their last recorded WCL boss fight. '
                'If they were playing a different role, or if they were in a unique fight (e.g. a resistance-based fight), '
                'the items may not reflect their optimal PvE setup.'
            ).classes('text-sm text-amber-800')

        # On/Off switch for Currently Equipped
        with ui.column().classes('w-full gap-1'):
            currently_equipped_switch = ui.switch(
                'Enable Currently Equipped',
                value=config.get_currently_equipped_enabled()
            )
            ui.label('Include currently equipped gear data in loot council decisions.').classes('text-xs text-gray-500 ml-10')

            # Helper to convert API source display value to config value
            def api_source_to_config(display_value: str) -> str:
                return "warcraftlogs" if display_value == "Warcraftlogs API" else "blizzard"

            def config_to_api_source(config_value: str) -> str:
                return "Warcraftlogs API" if config_value == "warcraftlogs" else "Blizzard API"

            # API source toggle (shown when enabled)
            with ui.element('div').classes('pl-10') as api_source_container:
                ui.label('Select data source:').classes('text-sm mb-2')

                def on_api_source_change(e):
                    config.set_currently_equipped_api_source(api_source_to_config(e.value))
                    notify_currently_equipped_change()

                ui_refs['currently_equipped_api_source'] = ui.toggle(
                    ['Blizzard API', 'Warcraftlogs API'],
                    value=config_to_api_source(config.get_currently_equipped_api_source()),
                    on_change=on_api_source_change
                )

            # Initialize visibility based on saved config
            initial_equipped_enabled = config.get_currently_equipped_enabled()
            api_source_container.set_visibility(initial_equipped_enabled)

            def on_currently_equipped_change(e):
                print(f"[DEBUG] on_currently_equipped_change called with value: {e.value}")
                config.set_currently_equipped_enabled(e.value)
                print(f"[DEBUG] After set, config.get_currently_equipped_enabled() = {config.get_currently_equipped_enabled()}")
                api_source_container.set_visibility(e.value)
                notify_metric_change()
                notify_currently_equipped_change()

            currently_equipped_switch.on_value_change(on_currently_equipped_change)

        ui_refs['currently_equipped_enabled'] = currently_equipped_switch

    # ==================== SECTION 2: WoW Server Settings ====================
    with ui.card().classes('w-full p-4 mb-4'):
        with ui.row().classes('w-full items-center justify-between mb-4'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('dns')
                ui.label('WoW Server Settings').classes('text-lg font-semibold')

        # Warning label for missing credentials
        credentials_warning = ui.label(
            'Blizzard API credentials required. Configure them in Core Connections tab.'
        ).classes('text-amber-500 w-full mb-2')

        # Server Region field with unsaved indicator
        with ui.row().classes('w-full items-center gap-2'):
            ui_refs['server_region'] = ui.select(
                label='Server Region',
                options=["EU", "US"],
                value=config.get_wcl_server_region() or "US"
            ).classes('flex-grow')
            server_region_unsaved = ui.label('Unsaved changes!').classes('text-red-500 text-xs')
            server_region_unsaved.visible = False

        initial_region = config.get_wcl_server_region() or "US"
        register_field_for_tracking('server_region', initial_region, server_region_unsaved)
        ui_refs['server_region'].on_value_change(
            lambda e: check_field_changed('server_region', e.value)
        )

        # Server field with unsaved indicator
        with ui.row().classes('w-full items-center gap-2'):
            ui_refs['server_slug'] = ui.select(
                label='Server',
                options=[],
                value=None
            ).classes('flex-grow')
            server_slug_unsaved = ui.label('Unsaved changes!').classes('text-red-500 text-xs')
            server_slug_unsaved.visible = False

        # We'll register server_slug tracking after initialization

        # Check credentials and update UI state
        has_credentials = check_blizzard_credentials()
        credentials_warning.set_visibility(not has_credentials)
        if not has_credentials:
            ui_refs['server_region'].disable()
            ui_refs['server_slug'].disable()

        def refresh_server_section():
            """Refresh the server section after credentials are saved."""
            global _current_realms

            if check_blizzard_credentials():
                credentials_warning.set_visibility(False)
                ui_refs['server_region'].enable()
                ui_refs['server_slug'].enable()

                prefetch_realms()

                region = ui_refs['server_region'].value
                game_version = game_version_toggle.value
                if region and game_version:
                    _current_realms = fetch_realm_data(game_version, region)
                    if _current_realms:
                        realm_names = sorted(_current_realms.keys())
                        ui_refs['server_slug'].options = realm_names
                        ui_refs['server_slug'].value = realm_names[0]

        ui_refs['_refresh_server_section'] = refresh_server_section
        register_blizzard_cred_callback(refresh_server_section)

        # Update servers when region changes
        def on_region_change():
            update_server_options(
                ui_refs['server_region'],
                ui_refs['server_slug'],
                game_version_toggle
            )
            check_field_changed('server_region', ui_refs['server_region'].value)

        ui_refs['server_region'].on_value_change(on_region_change)

        # Update servers when game version toggle changes
        game_version_toggle.on_value_change(
            lambda: update_server_options(
                ui_refs['server_region'],
                ui_refs['server_slug'],
                game_version_toggle
            )
        )

        def initialize_servers():
            """Initialize the server dropdown on page load."""
            global _current_realms

            if not check_blizzard_credentials():
                return

            region = ui_refs['server_region'].value
            game_version = game_version_toggle.value
            current_server_slug = config.get_wcl_server_slug()

            if region and game_version:
                _current_realms = fetch_realm_data(game_version, region)

                if _current_realms:
                    realm_names = sorted(_current_realms.keys())
                    ui_refs['server_slug'].options = realm_names

                    selected_name = None
                    for name, slug in _current_realms.items():
                        if slug == current_server_slug:
                            selected_name = name
                            break

                    if selected_name:
                        ui_refs['server_slug'].value = selected_name
                    else:
                        ui_refs['server_slug'].value = realm_names[0]

                    # Now register tracking with the initialized value
                    register_field_for_tracking('server_slug', ui_refs['server_slug'].value, server_slug_unsaved)
                    ui_refs['server_slug'].on_value_change(
                        lambda e: check_field_changed('server_slug', e.value)
                    )

        ui.timer(0.1, initialize_servers, once=True)

        # Save button for Server Settings
        def save_server_settings():
            global _current_realms
            if ui_refs['server_slug'].value and ui_refs['server_slug'].value in _current_realms:
                slug = _current_realms[ui_refs['server_slug'].value]
            else:
                slug = ""
            config.set_wcl_server_slug(slug)
            config.set_wcl_server_region(ui_refs['server_region'].value)

            mark_field_saved('server_region', ui_refs['server_region'].value)
            mark_field_saved('server_slug', ui_refs['server_slug'].value)

            ui.notify('Server settings saved!', type='positive')

        with ui.row().classes('w-full gap-2 mt-4'):
            ui.button('Save', on_click=save_server_settings, icon='save')

    # ==================== SECTION 3: Raider Custom Notes ====================
    with ui.card().classes('w-full p-4 mb-4'):
        with ui.row().classes('w-full items-center justify-between mb-4'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('edit_note')
                ui.label('Raider Custom Notes').classes('text-lg font-semibold')

        ui.label('Press the button to pull available raiders, and update custom notes per-raider if required.').classes('text-sm mb-4')

        ui_refs['raider_table_container'] = ui.column().classes('w-full')

        with ui.row().classes('w-full gap-2 mb-4'):
            ui.button(
                'Fetch Raiders',
                on_click=lambda: fetch_raiders(tmb_guild_id_ref, ui_refs['raider_table_container'])
            )

        with ui.row().classes('w-full gap-2'):
            ui.button('Save', on_click=save_all_raider_notes, icon='save')

    # ==================== SECTION 4: TMB Data Management ====================
    with ui.card().classes('w-full p-4 mb-4'):
        with ui.row().classes('w-full items-center justify-between mb-4'):
            with ui.row().classes('items-center gap-2'):
                ui.icon('refresh')
                ui.label('TMB Data Management').classes('text-lg font-semibold')

        ui.label("TMB data is cached once per session. Use the button below to fetch the latest data from That's My BIS.").classes('text-sm mb-4')

        def refresh_tmb_data():
            """Refresh TMB data from the server."""
            guild_id = config.get_tmb_guild_id()
            if not guild_id:
                ui.notify('TMB Guild ID is not configured. Go to Core Connections tab.', type='negative')
                return

            try:
                manager = TMBDataManager(guild_id=guild_id, guild_slug="placeholder")
                if not manager.is_session_valid():
                    ui.notify('TMB session is invalid or expired. Please re-authenticate.', type='negative')
                    return

                manager.refresh_all()
                ui.notify('TMB data refreshed successfully!', type='positive')
            except TMBSessionNotFoundError:
                ui.notify('TMB session not found. Please authenticate first.', type='negative')
            except TMBSessionExpiredError:
                ui.notify('TMB session expired. Please re-authenticate.', type='negative')
            except TMBFetchError as e:
                ui.notify(f'Failed to refresh TMB data: {str(e)}', type='negative')
            except Exception as e:
                ui.notify(f'Error refreshing TMB data: {str(e)}', type='negative')

        with ui.row().classes('w-full gap-2'):
            ui.button(
                'Refresh TMB Data',
                on_click=refresh_tmb_data,
                icon='sync'
            )

    return ui_refs
