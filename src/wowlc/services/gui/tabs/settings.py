"""
Settings tab for the GUI configuration interface.
Combines General Settings (server, cache) and Council Settings (metrics, raider notes).
"""
from nicegui import ui, run
import json
import os
import re
from pathlib import Path
from wowlc.core.paths import get_path_manager
from wowlc.core.zones import canonical_version_key, resolve_version_key, get_zone_options
from ..shared import (
    config,
    POLICY_PATH,
    notify_metric_change,
    notify_currently_equipped_change,
    register_game_version_callback,
    register_pyrewood_mode_callback,
    register_currently_equipped_callback,
    clear_currently_equipped_callbacks,
    register_field_for_tracking,
    check_field_changed,
    mark_field_saved,
)
from wowlc.core.paths import get_path_manager

# Module-level cache for current realm data (name -> slug mapping)
_current_realms: dict[str, str] = {}

# Cached realm data loaded from realms.json
_realm_data: dict | None = None

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

# Rule templates for generated rules preview (single source of truth in get_item_candidates)
from wowlc.tools.get_item_candidates import METRIC_RULE_TEMPLATES

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
    "tank_priority": "Tanks get priority for any mainspec items.",
    "raider_notes": "Notes from TMB about the raider.",
    "professions": "Only characters with the correct profession can be considered for relevant recipes.",
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


# --- File location helpers ---

def _validate_dir(raw: str) -> tuple[bool, str]:
    """Validate a folder path for use as an export/log location.

    An empty string is valid (means "use the default").
    """
    if not raw:
        return True, ""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        return False, 'Please enter an absolute folder path'
    try:
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".wowlc_write_test"
        probe.touch()
        probe.unlink()
    except OSError as e:
        return False, f'Folder is not writable: {e}'
    return True, ""


# --- Realm helpers ---

def _load_realm_data() -> dict:
    """Load realm data from bundled realms.json (cached after first load)."""
    global _realm_data
    if _realm_data is None:
        realms_file = get_path_manager().get_bundled_resource("data/realms.json")
        if realms_file is None:
            raise FileNotFoundError("Bundled realms.json not found")
        with open(realms_file, "r", encoding="utf-8") as f:
            _realm_data = json.load(f)
    return _realm_data


def _version_key(game_version: str) -> str:
    """Map the header toggle label to the canonical realms.json key."""
    return canonical_version_key(game_version)


def slugify_realm_name(name: str) -> str:
    """Slugify a realm display name following WCL conventions (Nek'Rosh -> nekrosh)."""
    s = re.sub(r"['’`]", "", name.strip().lower())
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


# Valid slug: lowercase alphanumeric segments separated by single hyphens
REALM_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def fetch_realm_data(game_version: str, region: str) -> dict[str, str]:
    """Get realms for a game version and region (bundled list merged with custom realms)."""
    data = _load_realm_data()
    version_key = _version_key(game_version)
    bundled = data.get(version_key, {}).get(region.upper(), {})
    return {**bundled, **config.get_custom_realms(version_key, region)}


def update_server_options(server_region, server_slug, game_version_toggle, preferred_name: str | None = None):
    """Update the server dropdown based on selected region and game version."""
    global _current_realms

    region = server_region.value
    game_version = game_version_toggle.value

    if region and game_version:
        _current_realms = fetch_realm_data(game_version, region)

        if _current_realms:
            realm_names = sorted(_current_realms.keys())
            server_slug.options = realm_names
            if preferred_name in realm_names:
                server_slug.value = preferred_name
            elif server_slug.value not in realm_names:
                server_slug.value = realm_names[0]
        else:
            server_slug.options = []
            server_slug.value = None
            ui.notify(f'No realms found for {region} - {game_version}', type='warning')
    else:
        server_slug.options = []
        server_slug.value = None


# --- Server Settings Dialog ---

def create_server_settings_dialog(game_version_toggle):
    """Create WoW Server Settings as a modal dialog.

    Args:
        game_version_toggle: The game version toggle UI element from the header.

    Returns:
        Tuple of (dialog, ui_refs, open_function)
    """
    global _current_realms

    ui_refs = {}

    with ui.dialog() as dialog:
        dialog.props('persistent')

        with ui.card().classes('w-full max-w-md p-4'):
            # Header with close button
            with ui.row().classes('w-full items-center justify-between mb-4'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('dns')
                    ui.label('WoW Server Settings').classes('text-xl font-semibold')
                ui.button(icon='close', on_click=dialog.close).props('flat round')

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
            register_field_for_tracking('server_region_dialog', initial_region, server_region_unsaved)
            ui_refs['server_region'].on_value_change(
                lambda e: check_field_changed('server_region_dialog', e.value)
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

            # Custom realm management
            with ui.expansion('Manage custom realms', icon='edit').classes('w-full'):
                ui.label('Added to the currently selected region and game version.').classes('text-xs text-gray-500')

                name_input = ui.input(label='Realm name').classes('w-full')
                slug_input = ui.input(label='Slug').classes('w-full')

                # Auto-fill the slug from the name until the user edits it manually;
                # the syncing flag stops the programmatic fill re-triggering on_slug_change.
                _slug_state = {'manual': False, 'syncing': False}

                def on_name_change(e):
                    if not _slug_state['manual']:
                        _slug_state['syncing'] = True
                        slug_input.value = slugify_realm_name(e.value or '')
                        _slug_state['syncing'] = False

                def on_slug_change(e):
                    if _slug_state['syncing']:
                        return
                    # Clearing the slug re-enables auto-fill
                    _slug_state['manual'] = bool(e.value)

                name_input.on_value_change(on_name_change)
                slug_input.on_value_change(on_slug_change)

                def add_custom_realm_clicked():
                    name = (name_input.value or '').strip()
                    slug = (slug_input.value or '').strip()
                    region = ui_refs['server_region'].value
                    game_version = game_version_toggle.value

                    if not region or not game_version:
                        ui.notify('Select a region and game version first', type='negative')
                        return
                    if not name:
                        ui.notify('Realm name cannot be empty', type='negative')
                        return
                    if not REALM_SLUG_PATTERN.match(slug):
                        ui.notify(
                            'Slug must be lowercase letters/numbers separated by hyphens (e.g. pyrewood-village)',
                            type='negative'
                        )
                        return
                    existing = fetch_realm_data(game_version, region)
                    if name.lower() in (n.lower() for n in existing):
                        ui.notify(f'A realm named "{name}" already exists for {region}', type='negative')
                        return

                    config.add_custom_realm(_version_key(game_version), region, name, slug)
                    update_server_options(
                        ui_refs['server_region'],
                        ui_refs['server_slug'],
                        game_version_toggle,
                        preferred_name=name
                    )
                    custom_realm_list.refresh()
                    name_input.value = ''
                    slug_input.value = ''
                    _slug_state['manual'] = False
                    ui.notify(f'Realm "{name}" added', type='positive')

                ui.button('Add realm', icon='add', on_click=add_custom_realm_clicked)

                def delete_custom_realm(name: str, slug: str):
                    config.remove_custom_realm(
                        _version_key(game_version_toggle.value),
                        ui_refs['server_region'].value,
                        name
                    )
                    if slug == config.get_wcl_server_slug_raw():
                        ui.notify(
                            'Deleted realm was your saved server — select and save a new one.',
                            type='warning'
                        )
                    update_server_options(
                        ui_refs['server_region'],
                        ui_refs['server_slug'],
                        game_version_toggle
                    )
                    custom_realm_list.refresh()

                @ui.refreshable
                def custom_realm_list():
                    region = ui_refs['server_region'].value or 'US'
                    custom = config.get_custom_realms(_version_key(game_version_toggle.value), region)
                    if not custom:
                        ui.label('No custom realms for this region/version.').classes('text-xs text-gray-500 italic')
                        return
                    for realm_name in sorted(custom):
                        realm_slug = custom[realm_name]
                        with ui.row().classes('w-full items-center justify-between'):
                            ui.label(f'{realm_name} ({realm_slug})').classes('text-sm')
                            ui.button(
                                icon='delete',
                                on_click=lambda n=realm_name, s=realm_slug: delete_custom_realm(n, s)
                            ).props('flat round dense color=negative')

                custom_realm_list()

            # Update servers when region changes
            def on_region_change():
                update_server_options(
                    ui_refs['server_region'],
                    ui_refs['server_slug'],
                    game_version_toggle
                )
                check_field_changed('server_region_dialog', ui_refs['server_region'].value)
                custom_realm_list.refresh()

            ui_refs['server_region'].on_value_change(on_region_change)

            # Update servers when game version toggle changes
            def on_dialog_game_version_change():
                update_server_options(
                    ui_refs['server_region'],
                    ui_refs['server_slug'],
                    game_version_toggle
                )
                custom_realm_list.refresh()

            game_version_toggle.on_value_change(on_dialog_game_version_change)

            def initialize_servers():
                """Initialize the server dropdown on page load."""
                global _current_realms

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
                        register_field_for_tracking('server_slug_dialog', ui_refs['server_slug'].value, server_slug_unsaved)
                        ui_refs['server_slug'].on_value_change(
                            lambda e: check_field_changed('server_slug_dialog', e.value)
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

                mark_field_saved('server_region_dialog', ui_refs['server_region'].value)
                mark_field_saved('server_slug_dialog', ui_refs['server_slug'].value)

                ui.notify('Server settings saved!', type='positive')

            with ui.row().classes('w-full gap-2 mt-4'):
                ui.button('Save', on_click=save_server_settings, icon='save')

    def open_dialog():
        dialog.open()

    return dialog, ui_refs, open_dialog


# --- Custom Parse Zones Dialog ---

def create_custom_zones_dialog(game_version_toggle, on_zones_changed):
    """Create the Manage Custom Parse Zones dialog.

    Args:
        game_version_toggle: The game version toggle UI element from the header.
        on_zones_changed: Callback run after add/remove; accepts an optional
            preferred_id keyword to select the newly added zone.

    Returns:
        Tuple of (dialog, open_function)
    """
    with ui.dialog() as dialog:
        with ui.card().classes('w-full max-w-md p-4'):
            with ui.row().classes('w-full items-center justify-between mb-2'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('edit_location_alt')
                    ui.label('Manage Custom Parse Zones').classes('text-xl font-semibold')
                ui.button(icon='close', on_click=dialog.close).props('flat round')

            ui.label(
                'Added to the currently selected game version. Zone IDs come from '
                'WarcraftLogs URLs (e.g. .../zone/1056).'
            ).classes('text-xs text-gray-500')

            zone_id_input = ui.input(label='WCL zone ID').classes('w-full')
            label_input = ui.input(label='Label').classes('w-full')

            def add_custom_zone_clicked():
                version_key = canonical_version_key(game_version_toggle.value)
                raw_id = (zone_id_input.value or '').strip()
                zone_label = (label_input.value or '').strip()

                try:
                    zone_id = int(raw_id)
                except ValueError:
                    ui.notify('Zone ID must be a whole number', type='negative')
                    return
                if zone_id <= 0:
                    ui.notify('Zone ID must be a positive number', type='negative')
                    return
                if not zone_label:
                    ui.notify('Label cannot be empty', type='negative')
                    return
                if zone_id in get_zone_options(version_key):
                    ui.notify(f'Zone {zone_id} already exists for {version_key}', type='negative')
                    return

                config.add_custom_zone(version_key, zone_id, zone_label)
                custom_zone_list.refresh()
                on_zones_changed(preferred_id=zone_id)
                zone_id_input.value = ''
                label_input.value = ''
                ui.notify(f'Zone "{zone_label}" added', type='positive')

            ui.button('Add zone', icon='add', on_click=add_custom_zone_clicked)

            def delete_custom_zone(zone_id: int):
                version_key = canonical_version_key(game_version_toggle.value)
                config.remove_custom_zone(version_key, zone_id)
                if config.get_parse_zone_id() == zone_id:
                    config.set_parse_zone_id(None)
                    config.set_parse_zone_label("")
                    ui.notify(
                        'Deleted zone was your selected parse zone — pick a new one.',
                        type='warning'
                    )
                custom_zone_list.refresh()
                on_zones_changed()

            @ui.refreshable
            def custom_zone_list():
                version_key = canonical_version_key(game_version_toggle.value)
                custom = config.get_custom_zones(version_key)
                if not custom:
                    ui.label('No custom zones for this game version.').classes('text-xs text-gray-500 italic')
                    return
                for zone_id_str in sorted(custom, key=int):
                    zone_label = custom[zone_id_str]
                    with ui.row().classes('w-full items-center justify-between'):
                        ui.label(f'{zone_label} ({zone_id_str})').classes('text-sm')
                        ui.button(
                            icon='delete',
                            on_click=lambda z=int(zone_id_str): delete_custom_zone(z)
                        ).props('flat round dense color=negative')

            custom_zone_list()

    register_game_version_callback(custom_zone_list.refresh)

    def open_dialog():
        custom_zone_list.refresh()
        dialog.open()

    return dialog, open_dialog


# --- Main tab creation ---

def create_settings_tab(tmb_guild_id_ref, game_version_toggle):
    """Build the combined Settings tab UI and return UI element references."""
    global _current_realms

    # Clear callbacks from previous page loads to avoid duplicates
    clear_currently_equipped_callbacks()

    ui_refs = {}

    # ==================== SECTION 1: Player Metrics ====================

    PARSE_FILTER_OPTIONS = {
        "dps_only": "DPS Only",
        "everyone": "Everyone",
    }

    def get_zone_options_for_version():
        version = game_version_toggle.value if hasattr(game_version_toggle, 'value') else 'Era'
        return get_zone_options(resolve_version_key(version))

    # Refreshers for the parse-zone selects (one per policy panel); run whenever
    # custom zones change so both dropdowns stay in sync
    parse_zone_refreshers = []

    def on_zones_changed(preferred_id=None):
        for refresher in parse_zone_refreshers:
            refresher()
        if preferred_id is not None and preferred_id in get_zone_options_for_version():
            for select_key in ('parse_zone_select', 'parse_zone_select_custom'):
                if select_key in ui_refs:
                    ui_refs[select_key].value = preferred_id
                    ui_refs[select_key].update()

    zones_dialog, open_zones_dialog = create_custom_zones_dialog(game_version_toggle, on_zones_changed)

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

            # Raider Notes toggle with settings
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
                ui.button(
                    icon='settings',
                    on_click=lambda: raider_notes_settings_container.set_visibility(
                        not raider_notes_settings_container.visible
                    )
                ).props('flat dense round').classes('text-gray-500')

            with ui.element('div').classes('pl-8 pt-2 w-full') as raider_notes_settings_container:
                raider_notes_settings_container.set_visibility(False)
                ui.toggle(
                    ['Public Note', 'Officer Note'],
                    value='Public Note' if config.get_raider_note_source() == 'public_note' else 'Officer Note',
                    on_change=lambda e: config.set_raider_note_source(
                        'public_note' if e.value == 'Public Note' else 'officer_note'
                    )
                )

            # Professions toggle
            def on_professions_toggle(enabled: bool):
                config.set_show_professions(enabled)
                notify_metric_change()

            with ui.row().classes('items-center gap-2 w-full'):
                ui_refs['show_professions'] = ui.checkbox(
                    value=config.get_show_professions(),
                    on_change=lambda e: on_professions_toggle(e.value)
                )
                with ui.column().classes('flex-1 gap-0'):
                    ui.label('Professions').classes('font-medium')
                    ui.label(CANDIDATE_RULE_DESCRIPTIONS['professions']).classes('text-xs text-gray-500')

    # Guard flag to prevent checkbox handlers firing during mode switch sync
    _syncing_checkboxes = False

    # --- Section 1B: Policy Mode ---
    with ui.card().classes('w-full p-4 mb-4'):
        with ui.row().classes('w-full items-center gap-2 mb-2'):
            ui.icon('tune')
            ui.label('Policy Mode').classes('text-lg font-semibold')

        ui.label('Simple mode uses priority rules below. Custom mode uses your written policy.').classes('text-sm text-gray-500 mb-4')

        def on_policy_mode_change(e):
            nonlocal _syncing_checkboxes
            new_mode_display = e.sender.value  # "Simple" or "Custom"
            new_mode = 'simple' if new_mode_display == 'Simple' else 'custom'
            old_mode = config.get_policy_mode()

            # Save the outgoing mode's metric states from active flags
            config.save_mode_metrics(old_mode)

            # Switch the policy mode
            config.set_policy_mode(new_mode)

            # Load the incoming mode's metric states into active flags
            config.load_mode_metrics(new_mode)

            # Sync UI checkboxes to reflect the newly loaded active flags
            is_simple = (new_mode == 'simple')
            _syncing_checkboxes = True
            try:
                if is_simple:
                    for metric_id, checkbox in metric_checkboxes.items():
                        new_val = get_metric_enabled(metric_id) and is_metric_available(metric_id)
                        checkbox.value = new_val
                        row = metric_rows.get(metric_id)
                        if row:
                            if new_val:
                                row.classes(remove='opacity-50')
                            else:
                                row.classes(add='opacity-50')
                    rule_preview.refresh()
                else:
                    for metric_id, checkbox in custom_metric_checkboxes.items():
                        new_val = get_metric_enabled(metric_id) and is_metric_available(metric_id)
                        checkbox.value = new_val
            finally:
                _syncing_checkboxes = False

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
                """Handle metric checkbox toggle in Simple mode."""
                if _syncing_checkboxes:
                    return
                set_metric_enabled(metric_id, enabled)
                config.save_mode_metrics('simple')
                # Update row styling
                row = metric_rows.get(metric_id)
                if row:
                    if enabled:
                        row.classes(remove='opacity-50')
                    else:
                        row.classes(add='opacity-50')
                # Update parse zone warnings when parses checkbox changes
                if metric_id == "parses":
                    no_zone = enabled and config.get_parse_zone_id() not in get_zone_options_for_version()
                    if 'parse_zone_row_warning' in ui_refs:
                        ui_refs['parse_zone_row_warning'].set_visibility(no_zone)
                    if 'parse_zone_warning' in ui_refs:
                        ui_refs['parse_zone_warning'].set_visibility(no_zone)
                # Refresh rule preview
                rule_preview.refresh()
                notify_metric_change()

            def on_metric_reorder(e):
                """Handle metric reordering from drag-drop using indices."""
                old_index = e.args.get('oldIndex')
                new_index = e.args.get('newIndex')
                if old_index is not None and new_index is not None:
                    current = list(config.get_metric_order())
                    if 0 <= old_index < len(current) and 0 <= new_index < len(current):
                        item = current.pop(old_index)
                        current.insert(new_index, item)
                        config.set_metric_order(current)
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
                config.save_mode_metrics('simple')
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
                config.save_mode_metrics('custom')

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

                            # Parse zone warning icon (visible even when settings panel is closed)
                            if metric_id == "parses":
                                parse_zone_row_warning = ui.icon('warning_amber') \
                                    .classes('text-orange-500') \
                                    .tooltip('No parse zone selected')
                                no_zone = config.get_show_parses() and config.get_parse_zone_id() not in get_zone_options_for_version()
                                parse_zone_row_warning.set_visibility(no_zone)
                                ui_refs['parse_zone_row_warning'] = parse_zone_row_warning

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
                                        show_warning = config.get_show_parses() and not zone_id
                                        ui_refs['parse_zone_warning'].set_visibility(show_warning)
                                        ui_refs['parse_zone_row_warning'].set_visibility(show_warning)

                                    with ui.row().classes('w-full max-w-xs items-center gap-1'):
                                        ui_refs['parse_zone_select'] = ui.select(
                                            label='Parse Zone',
                                            options=get_zone_options_for_version(),
                                            value=config.get_parse_zone_id() if config.get_parse_zone_id() in get_zone_options_for_version() else None,
                                            on_change=on_zone_change
                                        ).classes('flex-grow')
                                        ui.button(icon='edit_location_alt', on_click=open_zones_dialog) \
                                            .props('flat round dense').classes('text-gray-500') \
                                            .tooltip('Manage custom parse zones')

                                    parse_zone_warning = ui.label('No parse zone selected \u2014 parses will not be fetched.') \
                                        .classes('text-xs text-orange-600')
                                    parse_zone_warning.set_visibility(
                                        config.get_show_parses() and config.get_parse_zone_id() not in get_zone_options_for_version()
                                    )
                                    ui_refs['parse_zone_warning'] = parse_zone_warning

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
                                        no_zone = ui_refs['parse_zone_select'].value is None
                                        show_warning = config.get_show_parses() and no_zone
                                        ui_refs['parse_zone_warning'].set_visibility(show_warning)
                                        ui_refs['parse_zone_row_warning'].set_visibility(show_warning)

                                    register_game_version_callback(refresh_parse_zone_options)
                                    register_pyrewood_mode_callback(refresh_parse_zone_options)
                                    parse_zone_refreshers.append(refresh_parse_zone_options)

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
                            emitEvent('metric-reorder', {oldIndex: evt.oldIndex, newIndex: evt.newIndex});
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

            ui.on('metric-reorder', lambda e: on_metric_reorder(e))

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

                            # Store reference for all custom mode checkboxes
                            custom_metric_checkboxes[metric_id] = checkbox

                            def make_handler(mid):
                                def handler(e):
                                    if _syncing_checkboxes:
                                        return
                                    set_metric_enabled(mid, e.value)
                                    config.save_mode_metrics('custom')
                                    if mid == "parses":
                                        no_zone = e.value and config.get_parse_zone_id() not in get_zone_options_for_version()
                                        if 'parse_zone_row_warning_custom' in ui_refs:
                                            ui_refs['parse_zone_row_warning_custom'].set_visibility(no_zone)
                                        if 'parse_zone_warning_custom' in ui_refs:
                                            ui_refs['parse_zone_warning_custom'].set_visibility(no_zone)
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

                            # Parse zone warning icon (visible even when settings panel is closed)
                            if metric_id == "parses":
                                parse_zone_row_warning_custom = ui.icon('warning_amber') \
                                    .classes('text-orange-500') \
                                    .tooltip('No parse zone selected')
                                no_zone = config.get_show_parses() and config.get_parse_zone_id() not in get_zone_options_for_version()
                                parse_zone_row_warning_custom.set_visibility(no_zone)
                                ui_refs['parse_zone_row_warning_custom'] = parse_zone_row_warning_custom

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
                                        show_warning = config.get_show_parses() and not zone_id
                                        ui_refs['parse_zone_warning_custom'].set_visibility(show_warning)
                                        ui_refs['parse_zone_row_warning_custom'].set_visibility(show_warning)

                                    with ui.row().classes('w-full max-w-xs items-center gap-1'):
                                        ui_refs['parse_zone_select_custom'] = ui.select(
                                            label='Parse Zone',
                                            options=get_zone_options_for_version(),
                                            value=config.get_parse_zone_id() if config.get_parse_zone_id() in get_zone_options_for_version() else None,
                                            on_change=on_zone_change_custom
                                        ).classes('flex-grow')
                                        ui.button(icon='edit_location_alt', on_click=open_zones_dialog) \
                                            .props('flat round dense').classes('text-gray-500') \
                                            .tooltip('Manage custom parse zones')

                                    parse_zone_warning_custom = ui.label('No parse zone selected \u2014 parses will not be fetched.') \
                                        .classes('text-xs text-orange-600')
                                    parse_zone_warning_custom.set_visibility(
                                        config.get_show_parses() and config.get_parse_zone_id() not in get_zone_options_for_version()
                                    )
                                    ui_refs['parse_zone_warning_custom'] = parse_zone_warning_custom

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
                                        no_zone = ui_refs['parse_zone_select_custom'].value is None
                                        show_warning = config.get_show_parses() and no_zone
                                        ui_refs['parse_zone_warning_custom'].set_visibility(show_warning)
                                        ui_refs['parse_zone_row_warning_custom'].set_visibility(show_warning)

                                    register_game_version_callback(refresh_parse_zone_options_custom)
                                    register_pyrewood_mode_callback(refresh_parse_zone_options_custom)
                                    parse_zone_refreshers.append(refresh_parse_zone_options_custom)

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
                config.set_currently_equipped_enabled(e.value)
                api_source_container.set_visibility(e.value)
                notify_metric_change()
                notify_currently_equipped_change()

            currently_equipped_switch.on_value_change(on_currently_equipped_change)

        ui_refs['currently_equipped_enabled'] = currently_equipped_switch

    # ==================== SECTION: File Locations ====================
    with ui.card().classes('w-full p-4 mb-4'):
        with ui.row().classes('w-full items-center gap-2 mb-2'):
            ui.icon('folder')
            ui.label('File Locations').classes('text-lg font-semibold')

        ui.label(
            'Choose where the app writes files. Leave a field at its default '
            '(or use the reset button) to restore the standard location.'
        ).classes('text-sm text-gray-500 mb-4')

        pm = get_path_manager()

        def make_folder_row(field_id: str, label: str, initial_override: str, default_dir: Path):
            initial = initial_override or str(default_dir)
            with ui.row().classes('w-full items-center gap-2'):
                folder_input = ui.input(label=label, value=initial).classes('flex-1')

                async def browse():
                    browse_btn.disable()
                    try:
                        from wowlc.qt import folder_picker
                        if not folder_picker.is_available():
                            ui.notify(
                                'Native folder picker unavailable — type the path manually',
                                type='warning'
                            )
                            return
                        start_dir = folder_input.value or str(default_dir)
                        folder = await run.io_bound(
                            folder_picker.pick_folder, f'Select {label}', start_dir)
                        if folder:
                            folder_input.value = folder
                            check_field_changed(field_id, folder)
                    finally:
                        browse_btn.enable()

                browse_btn = ui.button('Browse…', icon='folder_open', on_click=browse)

                def reset_to_default():
                    folder_input.set_value(str(default_dir))
                    check_field_changed(field_id, str(default_dir))

                ui.button(icon='restart_alt', on_click=reset_to_default) \
                    .props('flat round').tooltip('Reset to default')

            unsaved = ui.label('Unsaved changes!').classes('text-red-500 text-xs')
            unsaved.visible = False
            register_field_for_tracking(field_id, initial, unsaved)
            folder_input.on_value_change(lambda e: check_field_changed(field_id, e.value or ""))
            return folder_input

        export_input = make_folder_row(
            'export_dir_path', 'Export folder',
            config.get_export_dir_override(), pm.get_default_export_dir()
        )
        log_input = make_folder_row(
            'log_dir_path', 'Logs folder',
            config.get_log_dir_override(), pm.get_default_log_dir()
        )
        ui.label('Log folder changes take effect after restarting the app.') \
            .classes('text-xs text-gray-500')

        def save_file_locations():
            fields = (
                (export_input, 'export_dir_path', pm.get_default_export_dir(),
                 config.set_export_dir_override),
                (log_input, 'log_dir_path', pm.get_default_log_dir(),
                 config.set_log_dir_override),
            )
            for folder_input, field_id, default_dir, _setter in fields:
                ok, err = _validate_dir((folder_input.value or "").strip())
                if not ok:
                    ui.notify(err, type='negative')
                    return
            for folder_input, field_id, default_dir, setter in fields:
                raw = (folder_input.value or "").strip()
                # Store "" when the value matches the default -> "use default"
                setter("" if not raw or Path(raw) == default_dir else raw)
                mark_field_saved(field_id, folder_input.value)
            ui.notify('File locations saved!', type='positive')

        ui.button('Save', icon='save', on_click=save_file_locations).classes('mt-2')

        ui_refs['export_dir_path'] = export_input
        ui_refs['log_dir_path'] = log_input

    return ui_refs
