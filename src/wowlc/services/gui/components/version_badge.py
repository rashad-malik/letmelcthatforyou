"""
Version badge component — displays current version and checks for updates.
"""

import asyncio
import webbrowser
from nicegui import ui
from wowlc.core.version_checker import get_current_version, fetch_latest_release


def create_version_badge():
    """
    Render a version badge in the current NiceGUI layout context.

    Shows the current version immediately, then asynchronously checks
    GitHub for a newer release and updates the badge accordingly.
    """
    current = get_current_version()

    with ui.row().classes('items-center gap-1'):
        ui.label(f'v{current}').classes(
            'text-sm text-gray-500 dark:text-gray-400'
        )
        # Placeholder that gets populated after the async check
        status = ui.row().classes('items-center gap-1')

    async def _check():
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, fetch_latest_release)

        if result['error'] or not result['latest']:
            return  # degrade silently — version label is still visible

        with status:
            if result['update_available']:
                url = result['release_url']
                ui.button(
                    'Update available',
                    icon='open_in_new',
                    on_click=lambda _, u=url: webbrowser.open(u),
                ).props('flat dense size=sm').classes(
                    'text-amber-600 dark:text-amber-400'
                )
            else:
                ui.icon('check_circle', size='xs').classes(
                    'text-green-600 dark:text-green-400'
                )
                ui.label('Latest version').classes(
                    'text-xs text-green-600 dark:text-green-400'
                )

    asyncio.ensure_future(_check())
