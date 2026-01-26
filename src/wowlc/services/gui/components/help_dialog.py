"""
Help dialog component for the GUI configuration interface.
Provides a comprehensive how-to guide for using the WoW Loot Council tool.
"""
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
                ui.label('Help & Tutorial').classes('text-2xl font-bold')
                ui.button(icon='close', on_click=dialog.close).props('flat round')

            # Scrollable content area
            with ui.scroll_area().classes('w-full flex-grow'):
                with ui.column().classes('w-full max-w-4xl mx-auto gap-4 p-4'):
                    # Introduction Section
                    with ui.card().classes('w-full p-4'):
                        ui.label('Welcome to Let Me LC That For You').classes('text-lg font-semibold mb-2')
                        ui.label(
                            'This tool helps guilds playing WoW Classic Era or WoW Classic The Burning Crusade '
                            'Anniversary Edition make loot council decisions. It combines data from multiple sources '
                            'to provide informed recommendations:'
                        ).classes('mb-2')

                        with ui.column().classes('ml-4 gap-1'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('groups', size='sm')
                                ui.label("That's My BIS (TMB) - Raider profiles, wishlists, attendance, received loot, and item lists")
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('assessment', size='sm')
                                ui.label('Warcraftlogs (WCL) - Parse and equipment data')
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('cloud', size='sm')
                                ui.label('Blizzard API - Server information and equipment data')
                            with ui.row().classes('items-center gap-2'):
                                ui.icon('smart_toy', size='sm')
                                ui.label('LLM Reasoning - AI-powered analysis to rank candidates')

                    # Disclaimer Section (Prominent Warning)
                    with ui.card().classes('w-full p-4 bg-amber-100 dark:bg-amber-900 border-l-4 border-amber-500'):
                        with ui.row().classes('items-start gap-3'):
                            ui.icon('warning', size='md').classes('text-amber-600 dark:text-amber-400 mt-1')
                            with ui.column().classes('flex-1'):
                                ui.label('Important Disclaimer').classes('text-lg font-semibold text-amber-800 dark:text-amber-200 mb-2')
                                ui.label(
                                    'This tool uses Large Language Models (LLMs) to help reason about loot decisions. '
                                    'While helpful, LLMs can make mistakes or provide suboptimal recommendations.'
                                ).classes('text-amber-800 dark:text-amber-200 mb-2')
                                with ui.column().classes('gap-1'):
                                    ui.label(
                                        'Guild officers must ALWAYS double-check the recommendations before making final decisions.'
                                    ).classes('text-amber-800 dark:text-amber-200 font-semibold')
                                    ui.label(
                                        'Never blindly follow the suggestions - use this as a starting point for discussion, '
                                        'not as the final word.'
                                    ).classes('text-amber-800 dark:text-amber-200')
                                    ui.label(
                                        'This is simply a tool meant to help your guild reach its own loot decisions, '
                                        'not a replacement for human judgement and guild knowledge.'
                                    ).classes('text-amber-800 dark:text-amber-200')

                    # Tab Explanations - Static Sections

                    # 1. Core Connections
                    with ui.card().classes('w-full p-4'):
                        with ui.row().classes('items-center gap-2 mb-3'):
                            ui.icon('link', size='sm')
                            ui.label('Core Connections').classes('text-lg font-semibold')

                        ui.label(
                            'This is the first tab you need to configure. The tool requires connections to several '
                            'external services to function properly.'
                        ).classes('mb-3')

                        with ui.column().classes('gap-3'):
                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label("That's My BIS (TMB)").classes('font-semibold mb-1')
                                ui.label(
                                    'Enter your TMB Guild ID (found in your TMB guild URL). Click "Authenticate TMB" to '
                                    'open a browser window where you can log in to TMB. This creates a session that '
                                    'allows the tool to fetch your guild\'s raider data, wishlists, and loot history.'
                                ).classes('text-sm')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('WarcraftLogs API').classes('font-semibold mb-1')
                                with ui.row().classes('text-sm flex-wrap items-baseline'):
                                    ui.label('Create an API client at')
                                    ui.link(
                                        'warcraftlogs.com/api/clients',
                                        'https://www.warcraftlogs.com/api/clients',
                                        new_tab=True
                                    ).classes('text-blue-600 dark:text-blue-400')
                                    ui.label('to get your Client ID and Client Secret.')
                                ui.label(
                                    'These credentials allow the tool to fetch attendance and parse data for your '
                                    'raiders. The User Token is optional and provides access to private logs if needed.'
                                ).classes('text-sm')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Blizzard API').classes('font-semibold mb-1')
                                with ui.row().classes('text-sm flex-wrap items-baseline'):
                                    ui.label('Create an application at')
                                    ui.link(
                                        'develop.battle.net',
                                        'https://develop.battle.net',
                                        new_tab=True
                                    ).classes('text-blue-600 dark:text-blue-400')
                                    ui.label('to get your Client ID and Client Secret.')
                                ui.label(
                                    'This is used to fetch the list of available realms for your region.'
                                ).classes('text-sm')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('LLM Configuration').classes('font-semibold mb-1')
                                ui.label(
                                    'Select your preferred LLM provider (OpenAI, Anthropic, Google, etc.) and enter '
                                    'your API key. Click "Test Connection" to verify your key works and see available '
                                    'models. Select a model and save your settings. The delay setting controls the '
                                    'pause between API calls when processing multiple items.'
                                ).classes('text-sm')

                    # 2. Settings (Combined General + Council)
                    with ui.card().classes('w-full p-4'):
                        with ui.row().classes('items-center gap-2 mb-3'):
                            ui.icon('settings', size='sm')
                            ui.label('Settings').classes('text-lg font-semibold')

                        ui.label(
                            'Configure your server settings, cache preferences, and loot council metrics in this tab.'
                        ).classes('mb-3')

                        with ui.column().classes('gap-3'):
                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('WoW Server Settings').classes('font-semibold mb-1')
                                ui.label(
                                    'Select your server region (EU or US) and your realm from the dropdown. The realm '
                                    'list is fetched from the Blizzard API based on your configured game version '
                                    '(Era or TBC Anniversary) selected in the header toggle.'
                                ).classes('text-sm')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Cache Settings').classes('font-semibold mb-1')
                                ui.label(
                                    'Configure how long TMB data is cached locally (in minutes). Caching reduces the '
                                    'number of requests to TMB and speeds up repeated operations. Set to 0 to disable '
                                    'caching entirely.'
                                ).classes('text-sm')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Player Metrics').classes('font-semibold mb-1')
                                ui.label(
                                    'Toggle which metrics are included when the LLM evaluates candidates for loot. '
                                    'Each metric can be individually enabled or disabled:'
                                ).classes('text-sm mb-2')
                                with ui.column().classes('ml-4 gap-1 text-sm'):
                                    ui.label('• Attendance: Consider player raid attendance percentages')
                                    ui.label('• Recent Loot: Consider how many items a player has recently received')
                                    ui.label('• Alt Status: Consider whether a character is a main or an alt')
                                    ui.label('• Wishlist Position: Consider where the item ranks on a player\'s wishlist')
                                    ui.label('• Parses: Consider character parse performance for a selected zone')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Lookback Periods').classes('font-semibold mb-1')
                                ui.label(
                                    'When Attendance or Recent Loot metrics are enabled, you can configure how far back '
                                    'to look. For example, setting Attendance Lookback to 60 days means only raids from '
                                    'the last 60 days are considered for attendance calculations.'
                                ).classes('text-sm')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Raider Custom Notes').classes('font-semibold mb-1')
                                ui.label(
                                    'Click "Fetch Raiders" to load your guild roster from TMB. You can then add custom '
                                    'notes for individual raiders. These notes are included in the LLM prompt and can '
                                    'provide context like "Returning player, needs gear catch-up" or "Considering '
                                    'rerolling soon". Save your notes when done.'
                                ).classes('text-sm')

                    # 3. Run LC
                    with ui.card().classes('w-full p-4'):
                        with ui.row().classes('items-center gap-2 mb-3'):
                            ui.icon('play_arrow', size='sm')
                            ui.label('Run LC').classes('text-lg font-semibold')

                        ui.label(
                            'This is where you run the actual loot council analysis. There are two modes available:'
                        ).classes('mb-3')

                        with ui.column().classes('gap-3'):
                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Single Item Mode').classes('font-semibold mb-1')
                                ui.label(
                                    'Quick lookup for a single item. Select a raid zone, then select an item from that '
                                    'zone. Click "Run Loot Council" to get a recommendation. Results include the top 3 '
                                    'priority players and a rationale explaining the recommendation. You can copy the '
                                    'results to clipboard for easy sharing.'
                                ).classes('text-sm')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Raid Zone Mode').classes('font-semibold mb-1')
                                ui.label(
                                    'Batch processing for an entire raid. Select one or more raid zones and click '
                                    '"Run Loot Council" to process all items from those zones. Progress is shown as '
                                    'items are processed. You can cancel at any time. Results are automatically saved '
                                    'to a CSV file in the Exports folder.'
                                ).classes('text-sm')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Policy Modes').classes('font-semibold mb-1')
                                ui.label(
                                    'Simple Mode: Drag and drop metrics to set their priority order. The top metric '
                                    'is the most important consideration. Rules are automatically generated based on '
                                    'your ordering.'
                                ).classes('text-sm mb-1')
                                ui.label(
                                    'Custom Mode: Write your own guild loot policy in free-form text. This gives you '
                                    'full control over the instructions given to the LLM. Keep it concise for best results.'
                                ).classes('text-sm')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Debug Info').classes('font-semibold mb-1')
                                ui.label(
                                    'Enable "Show Debug Info" to see the exact prompt sent to the LLM and the raw '
                                    'response received. Useful for understanding how the LLM made its decision or '
                                    'troubleshooting unexpected results.'
                                ).classes('text-sm')

                    # 4. Developer Tools
                    with ui.card().classes('w-full p-4'):
                        with ui.row().classes('items-center gap-2 mb-3'):
                            ui.icon('science', size='sm')
                            ui.label('Developer Tools').classes('text-lg font-semibold')

                        ui.label(
                            'Advanced settings for development and testing. Access via the flask icon in the header. '
                            'Most users will not need these options.'
                        ).classes('mb-3')

                        with ui.column().classes('gap-3'):
                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Developer Mode').classes('font-semibold mb-1')
                                ui.label(
                                    'Pyrewood Developer Mode overrides server settings to use a fixed test server. '
                                    'Only enable this if you\'re developing or testing the application.'
                                ).classes('text-sm')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('WCL Authentication').classes('font-semibold mb-1')
                                ui.label(
                                    'Authenticate with WarcraftLogs to obtain a user token. This opens a browser '
                                    'window for OAuth authentication and stores the token locally.'
                                ).classes('text-sm')

                            with ui.card().classes('w-full p-3 bg-gray-50 dark:bg-gray-700'):
                                ui.label('Testing Settings').classes('font-semibold mb-1')
                                ui.label(
                                    'Reference Date: Override the current date for testing time-based calculations.'
                                ).classes('text-sm')

    return dialog
