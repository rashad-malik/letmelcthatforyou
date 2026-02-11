"""
Developer Tools dialog for the GUI configuration interface.
"""
from nicegui import ui
from datetime import datetime
from pathlib import Path
import json

from ..shared import (
    config,
    register_field_for_tracking,
    check_field_changed,
    mark_field_saved,
    notify_pyrewood_mode_change,
)
from wowlc.core.paths import get_path_manager
from wowlc.auth.wcl_authenticate import authenticate as wcl_authenticate


def check_wcl_token_valid() -> bool:
    """Check if WCL token exists and is not expired."""
    paths = get_path_manager()
    token_path = paths.get_wcl_token_path()

    if not token_path.exists():
        return False

    try:
        with open(token_path) as f:
            data = json.load(f)

        # Check if expired
        if data.get("expires_at"):
            expires_at = datetime.fromisoformat(data["expires_at"])
            if datetime.now() > expires_at:
                return False

        return "access_token" in data
    except Exception:
        return False


async def run_wcl_authentication(auth_button):
    """Run WCL authentication and update button color based on result."""
    try:
        # Disable button during authentication
        auth_button.disable()
        auth_button.text = 'Authenticating...'

        # Get credentials from config
        client_id = config.get_wcl_client_id()
        client_secret = config.get_wcl_client_secret()

        if not client_id or not client_secret:
            ui.notify('Missing WCL client ID or secret. Please configure in Core Connections tab.', type='negative')
            auth_button.props(remove='color')
            auth_button.text = 'Authenticate WCL'
            return

        # Run authentication in background thread (Playwright blocks)
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: wcl_authenticate(client_id, client_secret))

        # Success - turn button green
        auth_button.props('color=positive')
        auth_button.text = 'WCL Authenticated'
        ui.notify('WCL authentication successful!', type='positive')

    except Exception as e:
        # Failed - remove color prop to return to default
        auth_button.props(remove='color')
        ui.notify(f'WCL Authentication failed: {str(e)}', type='negative')
        auth_button.text = 'Authenticate WCL'
    finally:
        auth_button.enable()


def check_initial_wcl_auth_status(auth_button):
    """Check if WCL token already exists and is valid, update button color."""
    if check_wcl_token_valid():
        auth_button.props('color=positive')
        auth_button.text = 'WCL Authenticated'


def create_dev_dialog():
    """Create Developer Tools as a modal dialog.

    Returns:
        Tuple of (dialog, ui_refs dict)
    """
    ui_refs = {}

    with ui.dialog() as dialog:
        dialog.props('maximized')

        with ui.card().classes('w-full h-full'):
            # Header with title and close button
            with ui.row().classes('w-full items-center justify-between mb-4 sticky top-0 z-10 pb-2'):
                ui.label('Developer Tools').classes('text-2xl font-bold')
                ui.button(icon='close', on_click=dialog.close).props('flat round')

            # Scrollable content area
            with ui.scroll_area().classes('w-full flex-grow'):
                with ui.column().classes('w-full max-w-4xl mx-auto gap-4 p-4'):
                    # Developer disclaimer banner
                    with ui.card().classes('w-full p-4 bg-amber-100 dark:bg-amber-900 border-l-4 border-amber-500'):
                        with ui.row().classes('items-start gap-3'):
                            ui.icon('warning', size='md').classes('text-amber-600 dark:text-amber-400')
                            with ui.column().classes('gap-1'):
                                ui.label('Developer Tools Only').classes('font-bold text-amber-800 dark:text-amber-200')
                                ui.label(
                                    'This page is intended for developers to test and debug the application. '
                                    'Regular users should not use these options. '
                                    'Only modify settings here if you know what you are doing.'
                                ).classes('text-sm text-amber-700 dark:text-amber-300')

                    # Developer Mode Section (flat card, no expansion)
                    with ui.card().classes('w-full p-4'):
                        with ui.row().classes('items-center gap-2 mb-2'):
                            ui.icon('code', size='sm')
                            ui.label('Developer Mode').classes('text-lg font-semibold')

                        ui.label('Override server settings for development and testing.').classes('mb-2')

                        def on_pyrewood_change(e):
                            config.set_pyrewood_dev_mode(e.value)
                            # Update thunderstrike toggle UI to reflect mutual exclusion
                            if e.value:
                                thunderstrike_toggle.value = False
                            notify_pyrewood_mode_change()

                        def on_thunderstrike_change(e):
                            config.set_thunderstrike_dev_mode(e.value)
                            # Update pyrewood toggle UI to reflect mutual exclusion
                            if e.value:
                                pyrewood_toggle.value = False

                        pyrewood_toggle = ui.switch(
                            'Pyrewood Developer Mode',
                            value=config.get_pyrewood_dev_mode(),
                            on_change=on_pyrewood_change
                        )
                        ui_refs['pyrewood_dev_mode'] = pyrewood_toggle

                        ui.label(
                            'When enabled, forces Region: EU and Server: pyrewood-village regardless of server settings.'
                        ).classes('text-xs text-gray-500 mt-1')

                        thunderstrike_toggle = ui.switch(
                            'Thunderstrike Developer Mode',
                            value=config.get_thunderstrike_dev_mode(),
                            on_change=on_thunderstrike_change
                        )
                        ui_refs['thunderstrike_dev_mode'] = thunderstrike_toggle

                        ui.label(
                            'When enabled, forces Region: EU and Server: thunderstrike regardless of server settings.'
                        ).classes('text-xs text-gray-500 mt-1')

                    # WCL Authentication Section (flat card, no expansion)
                    with ui.card().classes('w-full p-4'):
                        with ui.row().classes('items-center gap-2 mb-2'):
                            ui.icon('key', size='sm')
                            ui.label('WCL Authentication').classes('text-lg font-semibold')

                        ui.label('Authenticate with WarcraftLogs to obtain a user token for API access (used for archived logs).').classes('mb-2')

                        wcl_auth_button = ui.button(
                            'Authenticate WCL',
                            on_click=lambda: run_wcl_authentication(wcl_auth_button)
                        ).classes('w-full')
                        ui_refs['wcl_auth_button'] = wcl_auth_button

                        # Check initial auth status after UI is ready
                        ui.timer(0.2, lambda: check_initial_wcl_auth_status(wcl_auth_button), once=True)

                    # Testing Section (flat card, no expansion)
                    with ui.card().classes('w-full p-4'):
                        with ui.row().classes('items-center gap-2 mb-2'):
                            ui.icon('science', size='sm')
                            ui.label('Testing').classes('text-lg font-semibold')

                        ui_refs['reference_date'] = ui.input(
                            label='Reference Date',
                            value=config.get_reference_date()
                        ).classes('w-full')
                        reference_date_unsaved = ui.label('Unsaved changes!').classes('text-red-500 text-xs')
                        reference_date_unsaved.visible = False

                        initial_reference_date = config.get_reference_date() or ""
                        register_field_for_tracking('reference_date', initial_reference_date, reference_date_unsaved)
                        ui_refs['reference_date'].on_value_change(
                            lambda e: check_field_changed('reference_date', e.value or "")
                        )

                        def save_reference_date():
                            value = ui_refs['reference_date'].value.strip() if ui_refs['reference_date'].value else ""
                            config.set_reference_date(value)
                            mark_field_saved('reference_date', value)
                            ui.notify('Reference date saved!', type='positive')

                        with ui.row().classes('w-full gap-2 mt-4'):
                            ui.button('Save', on_click=save_reference_date, icon='save')

    def open_dialog():
        """Open dialog after refreshing values from config."""
        current_ref_date = config.get_reference_date() or ""
        ui_refs['reference_date'].value = current_ref_date
        ui_refs['reference_date'].update()  # Force sync to frontend
        # Re-register tracking with current value as the new baseline
        register_field_for_tracking('reference_date', current_ref_date, reference_date_unsaved)
        dialog.open()

    return dialog, ui_refs, open_dialog
