"""
Main page layout and entry point for the GUI configuration interface.
"""
from nicegui import ui, app
import os
import sys
from .tabs.connections import create_connections_tab
from .tabs.settings import create_settings_tab, create_server_settings_dialog
from .tabs.run_lc import create_run_lc_tab
from .tabs.dev import create_dev_dialog
from .components.help_dialog import create_help_dialog
from .components.version_badge import create_version_badge
from .shared import config, notify_game_version_change, clear_game_version_callbacks, clear_pyrewood_mode_callbacks


def _resolve_logo_path() -> str | None:
    if getattr(sys, 'frozen', False):
        candidate = os.path.join(sys._MEIPASS, 'assets', 'logo.png')
    else:
        candidate = os.path.join(os.path.dirname(__file__), '..', '..', '..', '..', 'assets', 'logo.png')
    return candidate if os.path.exists(candidate) else None


_LOGO_PATH = _resolve_logo_path()
_LOGO_URL = '/static/logo.png'
if _LOGO_PATH:
    app.add_static_file(local_file=_LOGO_PATH, url_path=_LOGO_URL)


@ui.page('/')
def main_page():
    """Main configuration page for the NiceGUI interface."""
    ui.page_title('Let Me LC That For You')

    # Main container
    with ui.column().classes('w-full items-center p-4'):
        # Header section
        with ui.card().classes('mb-4 w-full max-w-4xl'):
            with ui.column().classes('w-full p-6'):
                # Clear callbacks on page load to avoid duplicates
                clear_game_version_callbacks()
                clear_pyrewood_mode_callbacks()

                with ui.row().classes('items-center gap-4 w-full mb-4 flex-nowrap'):
                    # Left: logo + title block (title, subtitle, version badge)
                    if _LOGO_PATH:
                        ui.image(_LOGO_URL).classes('w-14 h-14 shrink-0').props('fit=contain no-spinner')
                    else:
                        ui.icon('gavel', size='xl')

                    with ui.column().classes('gap-0 items-start shrink-0'):
                        ui.label('Let Me LC That For You').classes(
                            'text-3xl font-bold leading-tight whitespace-nowrap'
                        )
                        ui.label('WoW Classic Loot Council Assistant').classes(
                            'text-sm text-gray-500 dark:text-gray-400 whitespace-nowrap'
                        )
                        create_version_badge()

                    ui.space()

                    # Right: game version toggle, then action buttons
                    initial_version = config.get_wcl_client_version()
                    if initial_version not in ['Era', 'TBC Anniversary']:
                        # Also migrates configs stored before the 'Era (WIP)' label was retired
                        initial_version = 'Era'
                    game_version_toggle = ui.toggle(
                        ['Era', 'TBC Anniversary'],
                        value=initial_version
                    ).props('dense no-caps').classes('mr-2 shrink-0')

                    def on_version_change():
                        config.set_wcl_client_version(game_version_toggle.value)
                        notify_game_version_change()

                    game_version_toggle.on_value_change(on_version_change)

                    # Server settings dialog button
                    server_dialog, server_refs, open_server_dialog = create_server_settings_dialog(game_version_toggle)
                    ui.button(icon='dns', on_click=open_server_dialog).props('flat round').tooltip('WoW Server Settings')

                    # Initialize dark mode with saved preference
                    saved_dark_mode = config.get_dark_mode()
                    dark_mode = ui.dark_mode(value=saved_dark_mode)

                    # Save dark mode preference whenever it changes
                    dark_mode.on_value_change(lambda e: config.set_dark_mode(e.value))

                    ui.button(
                        icon='dark_mode',
                        on_click=dark_mode.toggle
                    ).props('flat round').tooltip('Toggle dark mode')

                    dev_dialog, dev_refs, open_dev_dialog = create_dev_dialog()
                    ui.button(icon='science', on_click=open_dev_dialog).props('flat round').tooltip('Developer tools')

                    help_dialog = create_help_dialog()
                    ui.button(icon='help', on_click=help_dialog.open).props('flat round').tooltip('Help & tutorial')

                # Navigation explanation
                with ui.card().classes('w-full p-4 rounded-lg'):
                    ui.label('Quick Navigation Guide').classes('text-sm font-semibold mb-2')
                    with ui.column().classes('gap-1'):
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('play_arrow', size='sm')
                            ui.label('Run LC - Select zone and run loot council processing').classes('text-sm')
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('link', size='sm')
                            ui.label('Core Connections - Set up TMB, WCL, Blizzard, and LLM connections').classes('text-sm')
                        with ui.row().classes('items-center gap-2'):
                            ui.icon('settings', size='sm')
                            ui.label('Settings - Configure metrics and loot policy').classes('text-sm')

        # Main settings card
        with ui.card().classes('p-6 w-full max-w-4xl'):
            # Create tabs with icons
            with ui.tabs().classes('w-full') as tabs:
                run_lc_tab = ui.tab('Run LC', icon='play_arrow')
                connections_tab = ui.tab('Core Connections', icon='link')
                settings_tab = ui.tab('Settings', icon='settings')

            # Store all UI element references across tabs
            all_ui_refs = {}

            # We need to create tabs in dependency order, but NiceGUI will display them in visual order
            # Create Connections first to get tmb_guild_id and LLM refs
            with ui.tab_panels(tabs, value=run_lc_tab).classes('w-full'):
                # Run LC panel (depends on LLM settings from Connections)
                run_lc_panel = ui.tab_panel(run_lc_tab)

                # Connections tab (no dependencies - create first for refs)
                with ui.tab_panel(connections_tab):
                    connections_refs = create_connections_tab()
                    all_ui_refs.update(connections_refs)

                # Settings tab (depends on tmb_guild_id from Connections and game version toggle)
                settings_panel = ui.tab_panel(settings_tab)

            # Add dev_refs from dialog to all_ui_refs
            all_ui_refs.update(dev_refs)

            # Now populate the dependent tabs using the references
            with settings_panel:
                settings_refs = create_settings_tab(all_ui_refs['tmb_guild_id'], game_version_toggle)
                all_ui_refs.update(settings_refs)

            with run_lc_panel:
                # Pass all connection refs for validation
                connection_refs = {
                    'tmb_guild_id': all_ui_refs['tmb_guild_id'],
                    'wcl_client_id': all_ui_refs['wcl_client_id'],
                    'wcl_client_secret': all_ui_refs['wcl_client_secret'],
                    'wcl_redirect_uri': all_ui_refs['wcl_redirect_uri'],
                    'blizzard_client_id': all_ui_refs['blizzard_client_id'],
                    'blizzard_client_secret': all_ui_refs['blizzard_client_secret'],
                    'lc_provider': all_ui_refs['lc_provider'],
                    'lc_api_key': all_ui_refs['lc_api_key'],
                    'lc_base_url': all_ui_refs['lc_base_url'],
                    'lc_model': all_ui_refs['lc_model'],
                    'lc_delay': all_ui_refs['lc_delay'],
                }
                run_lc_refs = create_run_lc_tab(connection_refs, game_version_toggle)
                all_ui_refs.update(run_lc_refs)


def run_gui(splash=None):
    """Run the NiceGUI configuration interface with Qt native window."""
    import sys
    import logging
    import threading
    os.environ['NICEGUI_RELOAD'] = 'false'

    # Start NiceGUI server in background, capturing any errors
    server_error = []

    def start_server():
        try:
            ui.run(native=False, reload=False, show=False, port=8080)
        except Exception as e:
            logging.exception("NiceGUI server thread crashed")
            server_error.append(e)

    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # Launch Qt window (passes server_error so actual exceptions are surfaced)
    from wowlc.qt.window import run_qt_window
    try:
        exit_code = run_qt_window(port=8080, server_error=server_error, splash=splash)
    finally:
        # Flush log handlers before sys.exit kills the daemon NiceGUI thread.
        logging.shutdown()
    sys.exit(exit_code)
