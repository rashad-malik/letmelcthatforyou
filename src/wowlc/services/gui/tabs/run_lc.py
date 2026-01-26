"""
Run LC tab for the GUI configuration interface.
Contains Zone Selection, Run/Cancel controls, Progress tracking, and Results display.
Supports two modes: Single Item (for quick lookups) and Raid Zone (batch processing).
"""
import asyncio
import os
from nicegui import ui, run
from ..shared import config, register_metric_change_callback, register_connection_save_callback, register_game_version_callback, register_currently_equipped_callback, POLICY_PATH
from wowlc.tools.fetching_current_items import cache_all_raiders_gear, get_cache_info
from ...lc_processor import (
    LootCouncilProcessor,
    LootDecision,
    HAS_LITELLM,
)
from wowlc.tools.get_item_candidates import get_zone_items
from .connections import check_connections_configured

# Raid zones by game version
TBC_RAID_ZONES = [
    "Gruul's Lair",
    "Magtheridon's Lair",
    "Serpentshrine Cavern",
    "Tempest Keep",
    "Black Temple",
    "Hyjal Summit",
    "Sunwell Plateau",
]

ERA_RAID_ZONES = [
    "Molten Core",
    "Blackwing Lair",
    "Temple of Ahn'Qiraj",
    "Naxxramas",
]

# Mode options
MODE_SINGLE_ITEM = "Single Item"
MODE_RAID_ZONE = "Raid Zone"
LC_MODES = [MODE_SINGLE_ITEM, MODE_RAID_ZONE]

# Policy mode options
POLICY_SIMPLE = "Simple"
POLICY_CUSTOM = "Custom"
POLICY_MODES = [POLICY_SIMPLE, POLICY_CUSTOM]

# Metric display labels
METRIC_LABELS = {
    "attendance": "Attendance",
    "recent_loot": "Recent Loot",
    "wishlist_position": "Wishlist Position",
    "alt_status": "Alt Status",
    "parses": "Parses",
    "ilvl_comparison": "ilvl Comparison",
    "tier_token_counts": "Tier Token Counts",
    "last_item_received": "Last Item Received"
}

# Rule templates for preview (should match get_item_candidates.py)
METRIC_RULE_TEMPLATES = {
    "attendance": "Give preference to raiders with higher attendance.",
    "recent_loot": "Give preference to raiders who received fewer items recently.",
    "wishlist_position": "Give preference to raiders who want this item more.",
    "alt_status": "Give preference to main characters over alts.",
    "parses": "Give preference to raiders with better parses.",
    "ilvl_comparison": "Give preference to raiders with a larger ilvl difference.",
    "tier_token_counts": "Prioritise raiders who are closer to completing 2 or 4 set tier bonus.",
    "last_item_received": "Give preference to raiders who received an item for this slot longest ago."
}


# Module-level state for cancellation
_cancel_requested = False

# Policy file constants
POLICY_MAX_CHARS = 500

# Stale cache threshold (in hours)
STALE_CACHE_THRESHOLD_HOURS = 24


def check_stale_cache_warning():
    """
    Check if the raider gear cache is stale and show a warning if needed.

    Only warns when:
    1. Currently equipped is enabled
    2. API source is warcraftlogs
    3. Cache is older than 24 hours or doesn't exist
    """
    if not config.get_currently_equipped_enabled():
        return

    if config.get_currently_equipped_api_source() != "warcraftlogs":
        return

    cache_info = get_cache_info()

    if not cache_info.get("exists"):
        ui.notify(
            "No raider gear cache found. Consider caching gear data before running LC.",
            type='warning',
            multi_line=True
        )
        return

    age_hours = cache_info.get("age_hours", 0)
    if age_hours and age_hours > STALE_CACHE_THRESHOLD_HOURS:
        if age_hours < 48:
            age_str = f"{age_hours:.1f} hours"
        else:
            age_str = f"{age_hours / 24:.1f} days"

        ui.notify(
            f"Raider gear cache is {age_str} old. Consider refreshing before running LC.",
            type='warning',
            multi_line=True
        )


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


def reset_cancel_flag():
    """Reset the cancellation flag."""
    global _cancel_requested
    _cancel_requested = False


def request_cancel():
    """Request cancellation of processing."""
    global _cancel_requested
    _cancel_requested = True


def is_cancel_requested():
    """Check if cancellation was requested."""
    return _cancel_requested


def create_decision_card(decision: LootDecision, show_debug: bool = False) -> ui.card:
    """Create a card displaying a loot decision result."""
    card = ui.card().classes('w-full p-3 mb-2')

    with card:
        with ui.row().classes('items-center justify-between w-full'):
            with ui.column().classes('flex-1'):
                status_icon = 'check_circle' if decision.success else 'error'
                status_color = 'text-green-400' if decision.success else 'text-red-400'

                with ui.row().classes('items-center gap-2'):
                    ui.icon(status_icon).classes(status_color)
                    ui.label(decision.item_name).classes('font-semibold')
                    if decision.item_slot:
                        ui.label(f'({decision.item_slot})').classes('text-sm')

            if decision.success:
                with ui.row().classes('gap-4'):
                    ui.label(f'S1: {decision.suggestion_1}')
                    ui.label(f'S2: {decision.suggestion_2}')
                    ui.label(f'S3: {decision.suggestion_3}')
            else:
                ui.label(decision.error or 'Unknown error').classes('text-red-400 text-sm')

        if decision.success and decision.rationale:
            with ui.expansion('Rationale', icon='info').classes('w-full mt-2'):
                ui.label(decision.rationale).classes('text-sm')

        # Debug section - show prompt and response
        if show_debug and (decision.debug_prompt or decision.debug_response):
            with ui.expansion('Debug: API Request/Response', icon='bug_report').classes('w-full mt-2'):
                if decision.debug_prompt:
                    ui.label('Prompt Sent:').classes('font-semibold text-sm mt-1')
                    ui.textarea(value=decision.debug_prompt).props('readonly outlined').classes('w-full text-xs').style('font-family: monospace; min-height: 200px;')

                if decision.debug_response:
                    ui.label('Response Received:').classes('font-semibold text-sm mt-3')
                    ui.textarea(value=decision.debug_response).props('readonly outlined').classes('w-full text-xs').style('font-family: monospace; min-height: 100px;')

    return card


async def run_lc_processing(
    run_button,
    cancel_button,
    progress_bar,
    status_label,
    results_container,
    provider_ref,
    api_key_ref,
    model_ref,
    zone_select,
    delay_ref,
    debug_toggle,
):
    """
    Run the loot council processing asynchronously.

    This function:
    1. Validates inputs
    2. Creates the processor
    3. Processes items one at a time
    4. Updates progress UI
    5. Shows results
    """
    global _cancel_requested

    reset_cancel_flag()

    # Get values from references (these come from Connections tab)
    provider = provider_ref.value if hasattr(provider_ref, 'value') else provider_ref
    api_key = api_key_ref.value.strip() if hasattr(api_key_ref, 'value') and api_key_ref.value else ""
    model = model_ref.value if hasattr(model_ref, 'value') else model_ref
    delay = float(delay_ref.value) if hasattr(delay_ref, 'value') and delay_ref.value else 2.0
    show_debug = debug_toggle.value if hasattr(debug_toggle, 'value') else False

    # Validate inputs
    if not provider:
        ui.notify('Please select an LLM provider in Core Connections tab', type='negative')
        return

    if not api_key:
        ui.notify(f'Please enter your API key for {provider} in Core Connections tab', type='negative')
        return

    selected_zones = zone_select.value
    if not selected_zones:
        ui.notify('Please select at least one raid zone', type='negative')
        return

    # Check for stale cache (shows warning if needed)
    check_stale_cache_warning()

    # Update UI state
    run_button.disable()
    cancel_button.enable()
    progress_bar.value = 0
    status_label.text = 'Initializing...'

    results_container.clear()

    try:
        if not HAS_LITELLM:
            ui.notify(
                'litellm package not installed. Run: pip install litellm',
                type='negative',
                multi_line=True
            )
            return

        processor = LootCouncilProcessor(
            api_key=api_key,
            provider=provider,
            model=model,
            delay_seconds=delay
        )

        # Collect items from all selected zones
        items = []
        for zone_name in selected_zones:
            zone_items = get_zone_items(zone_name)
            items.extend(zone_items)

        if not items:
            zone_list = ', '.join(selected_zones)
            ui.notify(f'No items found for zones: {zone_list}', type='negative')
            return

        total = len(items)
        status_label.text = f'Found {total} items to process'

        decisions = []

        for i, item_name in enumerate(items):
            if is_cancel_requested():
                status_label.text = f'Cancelled after {i} items'
                ui.notify('Processing cancelled by user', type='warning')
                break

            status_label.text = f'Processing ({i + 1}/{total}): {item_name}'

            decision = await run.io_bound(processor.process_item, item_name)
            decisions.append(decision)

            progress_bar.value = (i + 1) / total

            with results_container:
                create_decision_card(decision, show_debug=show_debug)

            if i < total - 1 and not is_cancel_requested():
                await asyncio.sleep(delay)

        if decisions:
            output_path = await run.io_bound(processor.save_decisions_to_csv, decisions)
            status_label.text = f'Complete! Saved to {output_path.name}'
            ui.notify(
                f'Processed {len(decisions)} items. Results saved to {output_path.name}',
                type='positive'
            )
        else:
            status_label.text = 'No items processed'

    except Exception as e:
        status_label.text = f'Error: {str(e)}'
        ui.notify(f'Error during processing: {str(e)}', type='negative', multi_line=True)

    finally:
        run_button.enable()
        cancel_button.disable()
        reset_cancel_flag()


async def run_single_item_processing(
    run_button,
    status_label,
    results_container,
    provider_ref,
    api_key_ref,
    model_ref,
    item_select,
    debug_toggle,
    ui_refs,
):
    """
    Run loot council processing for a single item.

    This function:
    1. Validates inputs
    2. Creates the processor
    3. Processes a single item
    4. Shows results (no CSV saved)
    5. Provides copyable output
    """
    # Get values from references
    provider = provider_ref.value if hasattr(provider_ref, 'value') else provider_ref
    api_key = api_key_ref.value.strip() if hasattr(api_key_ref, 'value') and api_key_ref.value else ""
    model = model_ref.value if hasattr(model_ref, 'value') else model_ref
    show_debug = debug_toggle.value if hasattr(debug_toggle, 'value') else False

    # Validate inputs
    if not provider:
        ui.notify('Please select an LLM provider in Core Connections tab', type='negative')
        return

    if not api_key:
        ui.notify(f'Please enter your API key for {provider} in Core Connections tab', type='negative')
        return

    selected_item = item_select.value
    if not selected_item:
        ui.notify('Please select an item', type='negative')
        return

    # Check for stale cache (shows warning if needed)
    check_stale_cache_warning()

    # Update UI state
    run_button.disable()
    status_label.text = 'Processing...'
    results_container.clear()
    ui_refs['_copy_output_text'] = ''

    try:
        if not HAS_LITELLM:
            ui.notify(
                'litellm package not installed. Run: pip install litellm',
                type='negative',
                multi_line=True
            )
            return

        processor = LootCouncilProcessor(
            api_key=api_key,
            provider=provider,
            model=model,
            delay_seconds=0  # No delay needed for single item
        )

        status_label.text = f'Processing: {selected_item}'

        decision = await run.io_bound(processor.process_item, selected_item, True)

        with results_container:
            create_decision_card(decision, show_debug=show_debug)

        # Format copyable output
        if decision.success:
            output_text = f"""Item: {decision.item_name}
Slot: {decision.item_slot or 'N/A'}
Suggestion 1: {decision.suggestion_1}
Suggestion 2: {decision.suggestion_2}
Suggestion 3: {decision.suggestion_3}
Rationale: {decision.rationale}"""
            ui_refs['_copy_output_text'] = output_text
            status_label.text = 'Complete!'
            ui.notify('Item processed successfully', type='positive')
        else:
            ui_refs['_copy_output_text'] = f"Error processing {decision.item_name}: {decision.error}"
            status_label.text = f'Error: {decision.error}'

    except Exception as e:
        status_label.text = f'Error: {str(e)}'
        ui.notify(f'Error during processing: {str(e)}', type='negative', multi_line=True)

    finally:
        run_button.enable()


async def run_cache_processing(ui_refs: dict):
    """
    Run the raider gear caching process asynchronously.

    Fetches equipped items and last received items for all raiders
    from WCL and TMB, saving to a cache file.
    """
    cache_button = ui_refs['cache_button']
    cache_progress = ui_refs['cache_progress']
    cache_status = ui_refs['cache_status']

    # Update UI state
    cache_button.disable()
    cache_progress.value = 0
    cache_progress.set_visibility(True)
    cache_status.set_visibility(True)
    cache_status.text = 'Initializing...'

    try:
        def progress_callback(current, total, raider_name):
            """Update progress UI from callback."""
            if total > 0:
                cache_progress.value = current / total
            cache_status.text = f'Processing ({current}/{total}): {raider_name}'

        # Run the cache operation in a thread pool
        cache_path = await run.io_bound(
            cache_all_raiders_gear,
            progress_callback=progress_callback
        )

        cache_progress.value = 1.0
        cache_status.text = f'Complete! Saved to {cache_path.name}'
        ui.notify('Raider gear cache updated successfully!', type='positive')

        # Update cache status display
        if 'update_cache_status' in ui_refs:
            ui_refs['update_cache_status']()

    except Exception as e:
        cache_status.text = f'Error: {str(e)}'
        ui.notify(f'Error caching raider gear: {str(e)}', type='negative', multi_line=True)

    finally:
        cache_button.enable()
        # Hide progress after a delay
        await asyncio.sleep(2)
        cache_progress.set_visibility(False)
        cache_status.set_visibility(False)


def create_run_lc_tab(connection_refs: dict, game_version_toggle):
    """
    Build the Run LC tab UI and return UI element references.

    Args:
        connection_refs: Dictionary of all connection references from Connections tab
        game_version_toggle: Reference to the game version toggle from main page
    """
    ui_refs = {}

    def get_raid_zones_for_version():
        """Get raid zones based on current game version."""
        version = game_version_toggle.value if hasattr(game_version_toggle, 'value') else 'Era'
        if version == 'Era':
            return ERA_RAID_ZONES
        return TBC_RAID_ZONES

    # Extract LLM refs for processing
    lc_provider_ref = connection_refs['lc_provider']
    lc_api_key_ref = connection_refs['lc_api_key']
    lc_model_ref = connection_refs['lc_model']
    lc_delay_ref = connection_refs['lc_delay']

    # Warning banner for unconfigured connections (hidden by default)
    warning_banner = ui.card().classes('w-full p-4 mb-4 bg-amber-100 dark:bg-amber-900')
    with warning_banner:
        with ui.row().classes('items-center gap-3'):
            ui.icon('warning', color='amber').classes('text-2xl')
            ui.label('Please configure your settings in the Core Connections tab').classes(
                'text-amber-800 dark:text-amber-200 font-medium'
            )

    # Main content container that can be disabled
    content_container = ui.column().classes('w-full')

    with content_container:
        # Combined Mode & Policy Section
        with ui.card().classes('w-full p-4 mb-4'):
            # Mode toggle row
            with ui.row().classes('items-center justify-between w-full mb-4'):
                ui.label('Mode').classes('text-sm font-semibold')
                ui_refs['lc_mode'] = ui.toggle(
                    LC_MODES,
                    value=MODE_SINGLE_ITEM
                ).classes('')

            # Policy mode toggle row
            with ui.row().classes('items-center justify-between w-full mb-2'):
                ui.label('Policy Mode').classes('text-sm font-semibold')
                ui_refs['policy_mode'] = ui.toggle(
                    POLICY_MODES,
                    value=POLICY_SIMPLE if config.get_policy_mode() == "simple" else POLICY_CUSTOM
                ).classes('')

            # Simple mode container with sortable metrics
            simple_mode_container = ui.column().classes('w-full')
            ui_refs['simple_mode_container'] = simple_mode_container

            with simple_mode_container:
                ui.label('Drag metrics to set priority order (top = highest priority):').classes('text-xs text-gray-500 mb-2')

                def get_enabled_metrics():
                    """Get dict of which metrics are enabled."""
                    currently_equipped_enabled = config.get_currently_equipped_enabled()
                    show_ilvl = config.get_show_ilvl_comparisons()
                    show_tier = config.get_show_tier_token_counts()
                    print(f"[DEBUG] get_enabled_metrics: currently_equipped={currently_equipped_enabled}, show_ilvl={show_ilvl}, show_tier={show_tier}")
                    result = {
                        "attendance": config.get_show_attendance(),
                        "recent_loot": config.get_show_recent_loot(),
                        "wishlist_position": config.get_show_wishlist_position(),
                        "alt_status": config.get_show_alt_status() and config.get_mains_over_alts(),
                        "parses": config.get_show_parses(),
                        # ilvl_comparison requires currently equipped AND show_ilvl_comparisons
                        "ilvl_comparison": currently_equipped_enabled and show_ilvl,
                        # tier_token_counts requires currently equipped AND show_tier_token_counts
                        # Note: The actual rule will only appear for tier tokens (handled in get_item_candidates.py)
                        "tier_token_counts": currently_equipped_enabled and show_tier,
                        "last_item_received": config.get_show_last_item_received(),
                    }
                    print(f"[DEBUG] get_enabled_metrics result: {result}")
                    return result

                def get_clean_metric_order():
                    """Get metric order, deduplicating and adding any missing metrics."""
                    # All known metrics that should be in the order
                    all_metrics = ["attendance", "recent_loot", "wishlist_position", "alt_status", "parses", "ilvl_comparison", "tier_token_counts", "last_item_received"]

                    current_order = config.get_metric_order()
                    seen = set()
                    clean_order = []

                    # Add existing metrics in their saved order (deduplicating)
                    for m in current_order:
                        if m not in seen:
                            seen.add(m)
                            clean_order.append(m)

                    # Add any missing metrics at the end
                    for m in all_metrics:
                        if m not in seen:
                            print(f"[DEBUG] Adding missing metric to order: {m}")
                            clean_order.append(m)
                            seen.add(m)

                    # Save if order changed
                    if clean_order != current_order:
                        print(f"[DEBUG] Fixing metric_order: {current_order} -> {clean_order}")
                        config.set_metric_order(clean_order)

                    return clean_order

                @ui.refreshable
                def metrics_list():
                    """Refreshable metrics list using ui.refreshable pattern."""
                    enabled = get_enabled_metrics()
                    current_order = get_clean_metric_order()
                    should_display = [m for m in current_order if enabled.get(m, False)]

                    print(f"[DEBUG] metrics_list rendering: {should_display}")

                    with ui.column().classes('w-full sortable-metrics'):
                        for metric_id in should_display:
                            with ui.card().classes('w-full cursor-grab metric-item p-2 mb-1') as card:
                                card._props['data-id'] = metric_id
                                with ui.row().classes('items-center gap-2 w-full'):
                                    ui.icon('drag_indicator').classes('text-gray-400')
                                    ui.label(METRIC_LABELS.get(metric_id, metric_id)).classes('flex-1 text-sm')

                @ui.refreshable
                def rule_preview():
                    """Refreshable rule preview."""
                    current_order = get_clean_metric_order()
                    enabled = get_enabled_metrics()

                    with ui.column().classes('w-full bg-gray-100 dark:bg-gray-800 p-3 rounded mt-3'):
                        ui.label('Generated Rules Preview:').classes('text-xs font-semibold mb-1')
                        rule_num = 1
                        for metric_id in current_order:
                            if enabled.get(metric_id, False):
                                rule_text = METRIC_RULE_TEMPLATES.get(metric_id, "")
                                ui.label(f"RULE {rule_num}: {rule_text}").classes('text-xs')
                                rule_num += 1
                        if rule_num == 1:
                            ui.label("No metrics enabled. Enable metrics in Council Settings tab.").classes('text-xs text-gray-500')

                def refresh_metrics_list():
                    """Refresh both the metrics list and rule preview."""
                    print("[DEBUG] refresh_metrics_list called")
                    metrics_list.refresh()
                    rule_preview.refresh()

                def on_metric_reorder(new_order):
                    """Handle metric reordering from drag-drop."""
                    print(f"[DEBUG] on_metric_reorder called with: {new_order}")

                    enabled = get_enabled_metrics()
                    old_order = get_clean_metric_order()
                    disabled_metrics = [m for m in old_order if not enabled.get(m, False)]

                    # Deduplicate
                    seen = set()
                    full_order = []
                    for m in list(new_order) + disabled_metrics:
                        if m not in seen:
                            seen.add(m)
                            full_order.append(m)

                    print(f"[DEBUG] on_metric_reorder saving: {full_order}")
                    config.set_metric_order(full_order)
                    rule_preview.refresh()

                # Render the refreshable components
                metrics_list()
                rule_preview()

                # Setup SortableJS for drag-drop
                ui.add_body_html('''
                <script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js"></script>
                <script>
                function initSortableMetrics() {
                    const container = document.querySelector('.sortable-metrics');
                    if (container && !container._sortableInit) {
                        container._sortableInit = true;
                        Sortable.create(container, {
                            animation: 150,
                            ghostClass: 'opacity-50',
                            handle: '.metric-item',
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
                // Initialize on load and observe for dynamic content
                if (document.readyState === 'loading') {
                    document.addEventListener('DOMContentLoaded', function() {
                        setTimeout(initSortableMetrics, 500);
                    });
                } else {
                    setTimeout(initSortableMetrics, 500);
                }
                // Re-init when content changes
                new MutationObserver(function() {
                    setTimeout(initSortableMetrics, 100);
                }).observe(document.body, {childList: true, subtree: true});
                </script>
                ''')

                ui.on('metric-reorder', lambda e: on_metric_reorder(e.args.get('order', [])))

                # Store refresh function for external use
                ui_refs['refresh_metrics_list'] = refresh_metrics_list

                # Register for cross-tab notifications when metrics change in Council Settings
                register_metric_change_callback(refresh_metrics_list)

            # Custom mode container with policy editor
            custom_mode_container = ui.column().classes('w-full')
            ui_refs['custom_mode_container'] = custom_mode_container
            custom_mode_container.set_visibility(False)

            with custom_mode_container:
                ui.label('Edit your custom guild loot policy below:').classes('text-xs text-gray-500 mb-2')

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
                update_policy_warning()  # Initial check

                ui.button(
                    'Save Policy',
                    icon='save',
                    on_click=lambda: save_policy_content(ui_refs['policy_editor'].value)
                ).classes('mt-2')

            # Policy mode toggle handler
            def on_policy_mode_change(e):
                mode = e.sender.value
                if mode == POLICY_SIMPLE:
                    config.set_policy_mode("simple")
                    simple_mode_container.set_visibility(True)
                    custom_mode_container.set_visibility(False)
                    refresh_metrics_list()
                else:
                    config.set_policy_mode("custom")
                    simple_mode_container.set_visibility(False)
                    custom_mode_container.set_visibility(True)

            ui_refs['policy_mode'].on('update:model-value', on_policy_mode_change)

            # Set initial visibility based on saved config
            if config.get_policy_mode() == "custom":
                simple_mode_container.set_visibility(False)
                custom_mode_container.set_visibility(True)

        # === CACHE RAIDER GEAR SECTION ===
        cache_section = ui.card().classes('w-full p-4 mb-4')
        ui_refs['cache_section'] = cache_section

        with cache_section:
            with ui.row().classes('w-full items-center gap-2 mb-4'):
                ui.icon('inventory')
                ui.label('Raider Gear Cache').classes('text-lg font-semibold')

            # Dynamic description based on API source
            def get_cache_description_text():
                source = config.get_currently_equipped_api_source()
                if source == "warcraftlogs":
                    return 'Pre-cache equipped gear for all raiders from Warcraftlogs.'
                else:
                    return 'Pre-cache equipped gear for all raiders from Blizzard API.'

            ui_refs['cache_description'] = ui.label(get_cache_description_text()).classes('text-sm mb-4')

            # Cache status row
            with ui.row().classes('w-full items-center gap-4 mb-4'):
                ui_refs['cache_status_icon'] = ui.icon('help_outline')
                ui_refs['cache_status_label'] = ui.label('Checking cache...')

            # Progress section
            ui_refs['cache_progress'] = ui.linear_progress(value=0, show_value=False).classes('w-full')
            ui_refs['cache_progress'].set_visibility(False)
            ui_refs['cache_status'] = ui.label('Ready').classes('text-sm mt-2')
            ui_refs['cache_status'].set_visibility(False)

            # Cache button
            async def on_cache_click():
                await run_cache_processing(ui_refs)

            ui_refs['cache_button'] = ui.button(
                'Cache Raider Gear',
                icon='cached',
                on_click=on_cache_click
            )

        def update_cache_status():
            """Update the cache status display."""
            cache_info = get_cache_info()
            if cache_info.get("exists"):
                age_hours = cache_info.get("age_hours", 0)
                raider_count = cache_info.get("raider_count", 0)
                created_at = cache_info.get("created_at")
                api_source = cache_info.get("api_source", "warcraftlogs")

                if age_hours is not None:
                    if age_hours < 1:
                        age_str = f"{int(age_hours * 60)} minutes ago"
                    elif age_hours < 24:
                        age_str = f"{age_hours:.1f} hours ago"
                    else:
                        age_str = f"{age_hours / 24:.1f} days ago"
                else:
                    age_str = "unknown age"

                # Display source label (Blizzard or WCL)
                source_label = "Blizzard" if api_source == "blizzard" else "WCL"

                ui_refs['cache_status_icon'].name = 'check_circle'
                ui_refs['cache_status_icon'].classes(replace='text-green-500')
                ui_refs['cache_status_label'].text = f"Cache ({source_label}): {raider_count} raiders, {age_str}"
            else:
                ui_refs['cache_status_icon'].name = 'warning'
                ui_refs['cache_status_icon'].classes(replace='text-amber-500')
                ui_refs['cache_status_label'].text = "No cache found"

        ui_refs['update_cache_status'] = update_cache_status

        def update_cache_section_visibility():
            """Update visibility and description based on currently equipped settings."""
            should_show = config.get_currently_equipped_enabled()
            cache_section.set_visibility(should_show)
            if should_show:
                # Update description text based on current API source
                ui_refs['cache_description'].text = get_cache_description_text()
                update_cache_status()

        # Initialize visibility
        cache_section.set_visibility(config.get_currently_equipped_enabled())

        # Initial cache status check
        ui.timer(0.5, lambda: update_cache_status() if cache_section.visible else None, once=True)

        # Register callback to show/hide section when settings change
        register_currently_equipped_callback(update_cache_section_visibility)

        # === SINGLE ITEM MODE UI ===
        single_item_container = ui.column().classes('w-full')
        ui_refs['single_item_container'] = single_item_container

        with single_item_container:
            # Raid and Item Selection for Single Item mode
            with ui.expansion('Item Selection', icon='inventory_2', value=True).classes('w-full mb-4'):
                ui_refs['single_raid'] = ui.select(
                    label='Raid Zone',
                    options=get_raid_zones_for_version(),
                    value=None
                ).classes('w-full mb-2')

                ui_refs['single_item'] = ui.select(
                    label='Item',
                    options=[],
                    value=None
                ).classes('w-full')
                ui_refs['single_item'].disable()

                # Update items when raid changes
                def on_raid_change(e):
                    selected_raid = e.sender.value
                    if selected_raid:
                        items = get_zone_items(selected_raid)
                        # Sort alphabetically for user-friendly dropdown display
                        items = sorted(items, key=str.lower)
                        ui_refs['single_item'].options = items
                        ui_refs['single_item'].value = None
                        ui_refs['single_item'].enable()
                    else:
                        ui_refs['single_item'].options = []
                        ui_refs['single_item'].value = None
                        ui_refs['single_item'].disable()
                    ui_refs['single_item'].update()  # Force UI refresh

                ui_refs['single_raid'].on('update:model-value', on_raid_change)

            # Debug toggle for single item
            ui_refs['single_debug_toggle'] = ui.checkbox('Show Debug Info (API Request/Response)').classes('mb-2')

            # Run button for single item
            async def on_single_run_click():
                await run_single_item_processing(
                    ui_refs['single_run_button'],
                    ui_refs['single_status'],
                    ui_refs['single_results_container'],
                    lc_provider_ref,
                    lc_api_key_ref,
                    lc_model_ref,
                    ui_refs['single_item'],
                    ui_refs['single_debug_toggle'],
                    ui_refs,
                )

            ui_refs['single_run_button'] = ui.button(
                'Run Loot Council',
                icon='play_arrow',
                on_click=on_single_run_click
            ).classes('mb-4')

            # Status for single item
            with ui.card().classes('w-full p-4 mb-4'):
                ui_refs['single_status'] = ui.label('Ready').classes('text-sm')

            # Results Section for single item (always visible)
            with ui.card().classes('w-full p-4 mb-4'):
                ui.label('Results').classes('text-sm font-semibold mb-2')
                ui_refs['single_results_container'] = ui.column().classes('w-full gap-2')

            # Copy to clipboard button
            def copy_to_clipboard():
                output_text = ui_refs.get('_copy_output_text', '')
                print(f"[DEBUG] copy_to_clipboard called, output_text length: {len(output_text) if output_text else 0}")
                if output_text:
                    try:
                        # Use textarea fallback method - more reliable across browsers
                        import json
                        escaped_text = json.dumps(output_text)
                        js_code = f'''(function() {{
                            var textarea = document.createElement('textarea');
                            textarea.value = {escaped_text};
                            textarea.style.position = 'fixed';
                            textarea.style.opacity = '0';
                            document.body.appendChild(textarea);
                            textarea.select();
                            document.execCommand('copy');
                            document.body.removeChild(textarea);
                        }})();'''
                        ui.run_javascript(js_code)
                        print("[DEBUG] JavaScript execCommand copy completed")
                        ui.notify('Copied to clipboard!', type='positive')
                    except Exception as e:
                        print(f"[DEBUG] clipboard write error: {e}")
                        ui.notify(f'Clipboard error: {e}', type='negative')
                else:
                    print("[DEBUG] No output text to copy")
                    ui.notify('No output to copy', type='warning')

            ui.button('Copy to Clipboard', icon='content_copy', on_click=copy_to_clipboard)

        # === RAID ZONE MODE UI ===
        raid_zone_container = ui.column().classes('w-full')
        ui_refs['raid_zone_container'] = raid_zone_container
        raid_zone_container.set_visibility(False)

        with raid_zone_container:
            # Zone Selection Section
            with ui.expansion('Zone Selection', icon='map', value=True).classes('w-full mb-4'):
                ui_refs['lc_zone'] = ui.select(
                    label='Raid Zones',
                    options=get_raid_zones_for_version(),
                    multiple=True,
                    value=[]
                ).classes('w-full')

            # Progress Section
            with ui.card().classes('w-full p-4 mb-4'):
                ui.label('Progress').classes('text-sm font-semibold mb-2')

                ui_refs['lc_progress'] = ui.linear_progress(value=0, show_value=False).classes('w-full')
                ui_refs['lc_status'] = ui.label('Ready').classes('text-sm mt-2')

            # Debug toggle
            ui_refs['lc_debug_toggle'] = ui.checkbox('Show Debug Info (API Request/Response)').classes('mb-2')

            # Control Buttons
            async def on_run_click():
                await run_lc_processing(
                    ui_refs['lc_run_button'],
                    ui_refs['lc_cancel_button'],
                    ui_refs['lc_progress'],
                    ui_refs['lc_status'],
                    ui_refs['lc_results_container'],
                    lc_provider_ref,
                    lc_api_key_ref,
                    lc_model_ref,
                    ui_refs['lc_zone'],
                    lc_delay_ref,
                    ui_refs['lc_debug_toggle'],
                )

            with ui.row().classes('w-full gap-4 mb-4'):
                ui_refs['lc_run_button'] = ui.button(
                    'Run Loot Council',
                    icon='play_arrow',
                    on_click=on_run_click
                ).classes('flex-1')

                ui_refs['lc_cancel_button'] = ui.button(
                    'Cancel',
                    icon='stop',
                    on_click=request_cancel
                ).classes('flex-1')
                ui_refs['lc_cancel_button'].disable()

            # Results Section (collapsible, open by default)
            with ui.expansion('Results', icon='list_alt', value=True).classes('w-full'):
                ui_refs['lc_results_container'] = ui.column().classes('w-full gap-2')

            # Info note
            with ui.card().classes('w-full p-3 mt-4'):
                with ui.row().classes('items-center gap-2'):
                    ui.icon('info', size='sm')
                    ui.label(
                        'Results are automatically saved to Exports/loot_decisions_api.csv'
                    ).classes('text-sm')

        # Mode switching handler
        def on_mode_change(e):
            mode = e.sender.value
            if mode == MODE_SINGLE_ITEM:
                single_item_container.set_visibility(True)
                raid_zone_container.set_visibility(False)
            else:
                single_item_container.set_visibility(False)
                raid_zone_container.set_visibility(True)

        ui_refs['lc_mode'].on('update:model-value', on_mode_change)

    def update_tab_state():
        """Update the Run LC tab state based on connection configuration."""
        is_configured, missing_fields = check_connections_configured(connection_refs)

        if is_configured:
            warning_banner.set_visibility(False)
            content_container.classes(remove='opacity-50 pointer-events-none')
            # Enable single item mode controls
            ui_refs['single_run_button'].enable()
            ui_refs['single_raid'].enable()
            ui_refs['single_debug_toggle'].enable()
            # Enable raid zone mode controls
            ui_refs['lc_run_button'].enable()
            ui_refs['lc_zone'].enable()
            ui_refs['lc_debug_toggle'].enable()
        else:
            warning_banner.set_visibility(True)
            content_container.classes(add='opacity-50 pointer-events-none')
            # Disable single item mode controls
            ui_refs['single_run_button'].disable()
            ui_refs['single_raid'].disable()
            ui_refs['single_debug_toggle'].disable()
            # Disable raid zone mode controls
            ui_refs['lc_run_button'].disable()
            ui_refs['lc_zone'].disable()
            ui_refs['lc_debug_toggle'].disable()

    # Store update function for external calls
    ui_refs['update_tab_state'] = update_tab_state

    # Initial state check on startup
    # Delay must be longer than the LLM model dropdown initialization (0.5s in connections.py)
    # to ensure the model value is populated before we check configuration status
    ui.timer(1.0, update_tab_state, once=True)

    # Register for connection save events (so tab updates when any Save button is pressed)
    register_connection_save_callback(update_tab_state)

    # Register for game version changes to update zone selectors
    def refresh_zone_options():
        """Refresh zone options when game version changes."""
        new_zones = get_raid_zones_for_version()

        # Update single item mode raid selector
        ui_refs['single_raid'].options = new_zones
        # Clear selection if current value is not in new options
        if ui_refs['single_raid'].value not in new_zones:
            ui_refs['single_raid'].value = None
            # Also clear item selection since it depends on raid
            ui_refs['single_item'].options = []
            ui_refs['single_item'].value = None
            ui_refs['single_item'].disable()
        ui_refs['single_raid'].update()

        # Update raid zone mode multi-select
        ui_refs['lc_zone'].options = new_zones
        # Clear any invalid selections
        current_selections = ui_refs['lc_zone'].value or []
        valid_selections = [z for z in current_selections if z in new_zones]
        if valid_selections != current_selections:
            ui_refs['lc_zone'].value = valid_selections
        ui_refs['lc_zone'].update()

    register_game_version_callback(refresh_zone_options)

    return ui_refs
