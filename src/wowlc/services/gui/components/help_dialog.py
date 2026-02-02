"""
Help dialog component for the GUI configuration interface.
Provides a guide for using the Let Me LC That For You tool.
"""
import webbrowser
from nicegui import ui


def create_help_dialog():
    """
    Create and return a Help dialog with usage instructions.

    Returns:
        ui.dialog: The dialog element that can be opened with .open()
    """
    with ui.dialog() as dialog:
        dialog.props('maximized')

        with ui.card().classes('w-full h-full'):
            # Header with title and close button
            with ui.row().classes('w-full items-center justify-between mb-4 sticky top-0 z-10 pb-2'):
                ui.label('Help').classes('text-2xl font-bold')
                ui.button(icon='close', on_click=dialog.close).props('flat round')

            # Scrollable content area
            with ui.scroll_area().classes('w-full flex-grow'):
                with ui.column().classes('w-full max-w-4xl mx-auto gap-4 p-4'):
                    # Introduction Section
                    with ui.card().classes('w-full p-4'):
                        ui.label('Welcome').classes('text-lg font-semibold mb-2')
                        ui.label(
                            'Let Me LC That For You helps guilds make loot council decisions by combining '
                            'data from multiple sources with AI-powered analysis.'
                        ).classes('mb-2')

                        with ui.column().classes('ml-4 gap-1'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('groups', size='sm')
                                ui.label("ThatsMyBIS — Wishlists, attendance, loot history")
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('assessment', size='sm')
                                ui.label('WarcraftLogs — Parse performance and gear data')
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('cloud', size='sm')
                                ui.label('Blizzard API — Real-time character equipment')
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('smart_toy', size='sm')
                                ui.label('LLM — AI analysis to rank candidates')

                    # Disclaimer Section
                    with ui.card().classes('w-full p-4 bg-amber-100 dark:bg-amber-900 border-l-4 border-amber-500'):
                        with ui.row().classes('items-start gap-3'):
                            ui.icon('warning', size='md').classes('text-amber-600 dark:text-amber-400 mt-1')
                            with ui.column().classes('flex-1'):
                                ui.label('Disclaimer').classes('text-lg font-semibold text-amber-800 dark:text-amber-200 mb-2')
                                ui.label(
                                    'LLMs can make mistakes. Always review recommendations before making final '
                                    'decisions — this tool assists your guild, it does not replace human judgement.'
                                ).classes('text-amber-800 dark:text-amber-200')

                    # First-Time Setup Section
                    with ui.card().classes('w-full p-4'):
                        with ui.row().classes('items-center gap-2 mb-3'):
                            ui.icon('rocket_launch', size='sm')
                            ui.label('First-Time Setup').classes('text-lg font-semibold')

                        with ui.column().classes('gap-2'):
                            ui.label('1. Select your game version using the toggle at the top (Era or TBC Anniversary)').classes('text-sm')
                            ui.label('2. Click the "WoW Server" button in the header to select your region and realm').classes('text-sm')
                            ui.label('3. Configure your API connections in the Core Connections tab').classes('text-sm')
                            ui.label('4. Customise player metrics in the Settings tab').classes('text-sm')
                            ui.label('5. Run loot council analysis in the Run LC tab').classes('text-sm')

                    # Core Connections
                    with ui.card().classes('w-full p-4'):
                        with ui.row().classes('items-center gap-2 mb-3'):
                            ui.icon('link', size='sm')
                            ui.label('Core Connections').classes('text-lg font-semibold')

                        ui.label(
                            'Configure your API connections here. All credentials are stored locally.'
                        ).classes('mb-3')

                        with ui.column().classes('w-full gap-3'):
                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('ThatsMyBIS').classes('font-semibold mb-1')
                                ui.label(
                                    'Enter your TMB Guild ID (from your TMB guild URL) and click "Authenticate TMB" '
                                    'to log in via Discord.'
                                ).classes('text-sm')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('WarcraftLogs').classes('font-semibold mb-1')
                                with ui.row().classes('text-sm flex-wrap items-baseline gap-1'):
                                    ui.label('Create an API client at')
                                    ui.label('warcraftlogs.com/api/clients').classes(
                                        'text-blue-600 dark:text-blue-400 cursor-pointer hover:underline'
                                    ).on('click', lambda: webbrowser.open('https://www.warcraftlogs.com/api/clients'))
                                    ui.label('and enter your Client ID and Secret.')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Blizzard API').classes('font-semibold mb-1')
                                with ui.row().classes('text-sm flex-wrap items-baseline gap-1'):
                                    ui.label('Create an application at')
                                    ui.label('develop.battle.net').classes(
                                        'text-blue-600 dark:text-blue-400 cursor-pointer hover:underline'
                                    ).on('click', lambda: webbrowser.open('https://develop.battle.net'))
                                    ui.label('and enter your Client ID and Secret.')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('LLM Provider').classes('font-semibold mb-1')
                                ui.label(
                                    'Select your provider (Anthropic, OpenAI, Google, etc.), enter your API key, '
                                    'and click "Test Connection" to verify and load available models.'
                                ).classes('text-sm')

                    # Settings
                    with ui.card().classes('w-full p-4'):
                        with ui.row().classes('items-center gap-2 mb-3'):
                            ui.icon('settings', size='sm')
                            ui.label('Settings').classes('text-lg font-semibold')

                        ui.label(
                            'Configure your guild\'s loot policy and player metrics.'
                        ).classes('mb-3')

                        with ui.column().classes('w-full gap-3'):
                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Candidate Rules').classes('font-semibold mb-1')
                                ui.label(
                                    'Toggle who can receive loot — allow alts, give mains priority, '
                                    'enable tank priority for tank items, and include raider notes.'
                                ).classes('text-sm')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Policy Mode').classes('font-semibold mb-1')
                                ui.label(
                                    'Simple Mode: Drag and drop metrics to set priority order. '
                                    'Custom Mode: Write your own loot policy in Markdown.'
                                ).classes('text-sm')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Currently Equipped').classes('font-semibold mb-1')
                                ui.label(
                                    'Enable gear comparison using Blizzard API or WarcraftLogs to calculate '
                                    'item level upgrades and tier set progress.'
                                ).classes('text-sm')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Raider Notes').classes('font-semibold mb-1')
                                ui.label(
                                    'Click "Fetch Raiders" to load your roster, then add custom notes for individual '
                                    'players (e.g., "Returning player, needs catch-up gear").'
                                ).classes('text-sm')

                    # Run LC
                    with ui.card().classes('w-full p-4'):
                        with ui.row().classes('items-center gap-2 mb-3'):
                            ui.icon('play_arrow', size='sm')
                            ui.label('Run LC').classes('text-lg font-semibold')

                        ui.label(
                            'Execute loot council analysis here.'
                        ).classes('mb-3')

                        with ui.column().classes('w-full gap-3'):
                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Single Item Mode').classes('font-semibold mb-1')
                                ui.label(
                                    'Select a raid zone and item for a quick recommendation with the top 3 candidates.'
                                ).classes('text-sm')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Raid Zone Mode').classes('font-semibold mb-1')
                                ui.label(
                                    'Process all items from one or more raid zones. Results are saved to CSV automatically.'
                                ).classes('text-sm')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Raider Gear Cache').classes('font-semibold mb-1')
                                ui.label(
                                    'Pre-cache equipped gear for all raiders. Only visible when Currently Equipped is enabled in Settings.'
                                ).classes('text-sm')

    return dialog
