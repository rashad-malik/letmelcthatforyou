"""
Run LC tab for the GUI configuration interface.
Contains Zone Selection, Run/Cancel controls, Progress tracking, and Results display.
Supports two modes: Single Item (for quick lookups) and Raid Zone (batch processing).
"""
import asyncio
from nicegui import ui, run
from ..shared import config, register_connection_save_callback, register_game_version_callback, register_currently_equipped_callback
from wowlc.tools.fetching_current_items import cache_all_raiders_gear, get_cache_info
from ...lc_processor import (
    LootCouncilProcessor,
    LootDecision,
    TokenUsage,
    HAS_LITELLM,
)
from ...llm_providers import get_display_name
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

# Module-level state for cancellation
_cancel_requested = False

# Stale cache threshold (in hours)
STALE_CACHE_THRESHOLD_HOURS = 24


def get_token_usage_indicator(token_usage: TokenUsage) -> tuple:
    """
    Calculate traffic light indicator based on token usage.

    Args:
        token_usage: TokenUsage object with usage information

    Returns:
        Tuple of (icon_name, icon_color, tooltip_text)
    """
    if not token_usage or not token_usage.total_tokens or not token_usage.max_tokens:
        return ('help_outline', 'text-gray-400', 'Token usage data unavailable')

    usage_ratio = token_usage.total_tokens / token_usage.max_tokens

    if usage_ratio < 0.5:
        return ('check_circle', 'text-green-500', f'Token usage: {usage_ratio:.1%} of limit')
    elif usage_ratio < 0.8:
        return ('warning', 'text-yellow-500', f'Token usage: {usage_ratio:.1%} of limit')
    else:
        return ('error', 'text-red-500', f'Token usage: {usage_ratio:.1%} of limit - approaching limit!')


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

                # Token Usage Section
                if decision.token_usage:
                    tu = decision.token_usage

                    ui.separator().classes('my-3')
                    with ui.row().classes('items-center gap-2 mb-2'):
                        icon_name, icon_color, tooltip = get_token_usage_indicator(tu)
                        with ui.element('div').tooltip(tooltip):
                            ui.icon(icon_name).classes(icon_color)
                        ui.label('Token Usage').classes('font-semibold text-sm')

                    # Token counts grid
                    with ui.row().classes('w-full gap-4 flex-wrap'):
                        with ui.column().classes('gap-1'):
                            ui.label('Prompt Tokens:').classes('text-xs text-gray-500')
                            ui.label(f'{tu.prompt_tokens:,}' if tu.prompt_tokens else 'N/A').classes('text-sm font-mono')

                        with ui.column().classes('gap-1'):
                            ui.label('Completion Tokens:').classes('text-xs text-gray-500')
                            ui.label(f'{tu.completion_tokens:,}' if tu.completion_tokens else 'N/A').classes('text-sm font-mono')

                        with ui.column().classes('gap-1'):
                            ui.label('Total Tokens:').classes('text-xs text-gray-500')
                            ui.label(f'{tu.total_tokens:,}' if tu.total_tokens else 'N/A').classes('text-sm font-mono')

                        with ui.column().classes('gap-1'):
                            ui.label('Model Max:').classes('text-xs text-gray-500')
                            ui.label(f'{tu.max_tokens:,}' if tu.max_tokens else 'N/A').classes('text-sm font-mono')

                    # Cost display (if available)
                    if tu.estimated_cost is not None:
                        with ui.row().classes('mt-2'):
                            ui.label('Estimated Cost:').classes('text-xs text-gray-500')
                            ui.label(f'${tu.estimated_cost:.6f}').classes('text-sm font-mono ml-2')

                    # Model name (with pretty display name)
                    if tu.model_name:
                        with ui.row().classes('mt-1'):
                            ui.label('Model:').classes('text-xs text-gray-500')
                            ui.label(get_display_name(tu.model_name)).classes('text-xs font-mono ml-2 text-gray-400')

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
        # LC Mode Section
        with ui.card().classes('w-full p-4 mb-4'):
            # Header with icon
            with ui.row().classes('w-full items-center gap-2 mb-2'):
                ui.icon('swap_horiz')
                ui.label('LC Mode').classes('text-lg font-semibold')

            # Description
            ui.label('Single Item: Quick LC for one item. Raid Zone: Batch LC for all items in a raid.').classes('text-sm text-gray-500 mb-4')

            # Left-aligned toggle
            ui_refs['lc_mode'] = ui.toggle(
                LC_MODES,
                value=MODE_SINGLE_ITEM
            ).classes('text-base')

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

                # Check if cache is stale
                is_stale = age_hours is not None and age_hours >= STALE_CACHE_THRESHOLD_HOURS

                if is_stale:
                    ui_refs['cache_status_icon'].name = 'schedule'
                    ui_refs['cache_status_icon'].classes(replace='text-amber-500')
                else:
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
                        ui.notify('Copied to clipboard!', type='positive')
                    except Exception as e:
                        ui.notify(f'Clipboard error: {e}', type='negative')
                else:
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
