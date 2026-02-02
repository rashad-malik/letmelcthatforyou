"""
Core Connections tab for the GUI configuration interface.
Handles TMB authentication, WCL API, Blizzard API, and LLM configuration.
"""
from nicegui import ui
import asyncio
import requests
import webbrowser
from ..shared import (
    config,
    notify_tmb_auth_change,
    notify_blizzard_cred_change,
    notify_connection_save,
    register_field_for_tracking,
    check_field_changed,
    mark_field_saved,
)
from ...tmb_manager import (
    TMBDataManager,
    TMBSessionNotFoundError,
    TMBSessionExpiredError,
    TMBFetchError,
)
from ...wcl_client import (
    WarcraftLogsClient,
    WCLAuthenticationError,
    WCLQueryError
)
from ...llm_providers import (
    get_available_providers,
    get_provider_key_placeholder,
    get_validated_models,
    PROVIDERS,
)
from wowlc.auth.tmb_authenticate import authenticate


async def run_tmb_authentication(auth_button):
    """Run TMB authentication and update button color based on result."""
    try:
        auth_button.disable()
        auth_button.text = 'Authenticating...'

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, authenticate)

        auth_button.props('color=positive')
        auth_button.text = 'TMB Authenticated'
        ui.notify('TMB authentication successful!', type='positive')
        notify_tmb_auth_change()  # Notify other tabs that auth status changed

    except Exception as e:
        auth_button.props(remove='color')
        ui.notify(f'Authentication failed: {str(e)}', type='negative')
        auth_button.text = 'Authenticate TMB'
    finally:
        auth_button.enable()


def check_initial_tmb_auth_status(auth_button, tmb_guild_id):
    """Check if TMB session is already valid and update button color."""
    guild_id = tmb_guild_id.value.strip() if tmb_guild_id.value else ""
    if not guild_id:
        return

    try:
        manager = TMBDataManager(guild_id=guild_id, guild_slug="placeholder")
        if manager.is_session_valid():
            auth_button.props('color=positive')
            auth_button.text = 'TMB Authenticated'
    except:
        pass


def check_tmb_session(tmb_guild_id):
    """Validate TMB session and verify guild access."""
    import json
    results = []
    errors = []

    guild_id = tmb_guild_id.value.strip()

    if not guild_id:
        errors.append("TMB Guild ID missing")
        ui.notify('\n'.join(errors), type='negative', multi_line=True)
        return

    guild_slug = "placeholder"

    try:
        manager = TMBDataManager(guild_id=guild_id, guild_slug=guild_slug)
        session_info = manager.get_session_info()

        if not session_info.get('valid', False):
            if 'error' in session_info:
                errors.append(f"TMB session error: {session_info['error']}")
            else:
                errors.append("TMB session is invalid or expired")
        else:
            results.append("TMB session is valid")

            try:
                content = manager._fetch_url(manager.ENDPOINTS["characters"])
                characters = json.loads(content)
                results.append(f"Guild accessible ({len(characters)} raiders found)")

            except TMBSessionExpiredError as e:
                errors.append(f"TMB session expired during access: {str(e)}")
            except TMBFetchError as e:
                errors.append(f"Failed to access guild data: {str(e)}")
            except Exception as e:
                errors.append(f"Unexpected error accessing guild: {str(e)}")

    except TMBSessionNotFoundError as e:
        errors.append(f"TMB session not found: {str(e)}")
        errors.append("Run tmb_authenticate.py to create a session")
    except ValueError as e:
        errors.append(f"Configuration error: {str(e)}")
    except Exception as e:
        errors.append(f"TMB validation error: {str(e)}")

    if errors:
        ui.notify('\n'.join(errors), type='negative', multi_line=True)
    if results:
        ui.notify('\n'.join(results), type='positive' if not errors else 'info', multi_line=True)


def check_wcl_credentials(wcl_client_id, wcl_client_secret, wcl_user_token):
    """Validate WCL credentials by testing authentication and token validity."""
    results = []
    errors = []

    client_id = wcl_client_id.value.strip()
    client_secret = wcl_client_secret.value.strip()

    if client_id and client_secret:
        try:
            client = WarcraftLogsClient(
                client_id=client_id,
                client_secret=client_secret
            )
            client.authenticate()
            results.append("Client credentials valid")
        except WCLAuthenticationError as e:
            errors.append(f"Client credentials failed: {str(e)}")
    else:
        errors.append("Client ID or Secret missing")

    user_token = wcl_user_token.value.strip()

    if user_token:
        try:
            test_client = WarcraftLogsClient()
            test_client.set_user_token(user_token)

            test_query = """
            query TestUserToken {
                rateLimitData {
                    limitPerHour
                    pointsSpentThisHour
                }
            }
            """
            test_client.query(test_query)
            results.append("User token valid")

        except (WCLAuthenticationError, WCLQueryError) as e:
            errors.append(f"User token failed: {str(e)}")
    else:
        results.append("User token not provided (optional)")

    if errors:
        ui.notify('\n'.join(errors), type='negative', multi_line=True)
    if results:
        ui.notify('\n'.join(results), type='positive' if not errors else 'info', multi_line=True)


def check_blizzard_credentials(blizzard_client_id, blizzard_client_secret):
    """Validate Blizzard API credentials by testing authentication."""
    results = []
    errors = []

    client_id = blizzard_client_id.value.strip()
    client_secret = blizzard_client_secret.value.strip()

    if client_id and client_secret:
        try:
            url = "https://oauth.battle.net/token"
            body = {
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret
            }

            response = requests.post(url, data=body, timeout=30)
            response.raise_for_status()

            token_data = response.json()
            if token_data.get("access_token"):
                results.append("Blizzard API credentials valid")
            else:
                errors.append("Blizzard API authentication failed: No access token in response")

        except requests.exceptions.HTTPError as e:
            errors.append(f"Blizzard API authentication failed: HTTP {response.status_code}")
        except requests.exceptions.RequestException as e:
            errors.append(f"Blizzard API connection failed: {str(e)}")
        except Exception as e:
            errors.append(f"Blizzard API validation error: {str(e)}")
    else:
        errors.append("Blizzard Client ID or Secret missing")

    if errors:
        ui.notify('\n'.join(errors), type='negative', multi_line=True)
    if results:
        ui.notify('\n'.join(results), type='positive', multi_line=True)


def check_llm_connection(lc_provider, lc_model, lc_api_key, show_notification=True):
    """
    Validate LLM API key by querying the provider's /models endpoint.
    This is free and also updates the model list to show only valid models.

    Args:
        lc_provider: Provider select element
        lc_model: Model select element
        lc_api_key: API key input element
        show_notification: Whether to show UI notifications (default True)
    """
    provider = lc_provider.value
    api_key = lc_api_key.value.strip() if lc_api_key.value else ""

    if not api_key:
        if show_notification:
            ui.notify('API Key is required', type='negative')
        return

    provider_info = PROVIDERS.get(provider, {})
    provider_name = provider_info.get('name', provider)

    try:
        # Get validated models - this queries the provider's /models endpoint
        validated_models = get_validated_models(provider, api_key)

        if validated_models:
            # Update the model dropdown with only valid models
            lc_model.options = {m['value']: m['label'] for m in validated_models}
            # Try to restore saved model, otherwise use first available
            saved_model = config.get_llm_model()
            if any(m['value'] == saved_model for m in validated_models):
                lc_model.value = saved_model
            else:
                lc_model.value = validated_models[0]['value']
            lc_model.props('label=Model')
            lc_model.enable()
            lc_model.update()

            if show_notification:
                ui.notify(
                    f'{provider_name} API key is valid. Found {len(validated_models)} available models.',
                    type='positive'
                )
        else:
            # Disable model dropdown on failure
            lc_model.options = {}
            lc_model.value = None
            lc_model.props('label=Model (test connection first)')
            lc_model.disable()
            lc_model.update()

            if show_notification:
                ui.notify(
                    f'{provider_name} API key appears invalid or no models available',
                    type='negative'
                )
    except Exception as e:
        # Disable model dropdown on error
        lc_model.options = {}
        lc_model.value = None
        lc_model.props('label=Model (test connection first)')
        lc_model.disable()
        lc_model.update()

        if show_notification:
            ui.notify(f'Connection test failed: {str(e)}', type='negative')


def init_llm_model_dropdown(lc_provider, lc_model, lc_api_key):
    """
    Initialize LLM model dropdown on startup if API key is saved.
    Validates the saved key silently and populates models if valid.
    """
    api_key = lc_api_key.value.strip() if lc_api_key.value else ""
    if api_key:
        # Silently validate and populate models without showing notifications
        check_llm_connection(lc_provider, lc_model, lc_api_key, show_notification=False)


def create_connections_tab():
    """Build the Core Connections tab UI and return UI element references."""
    ui_refs = {}

    # ==================== TMB Section ====================
    with ui.card().classes('w-full p-4 mb-4'):
        with ui.row().classes('w-full items-center gap-2 mb-2'):
            ui.icon('groups')
            ui.label("TMB (That's My BIS)").classes('text-lg font-semibold')

        with ui.column().classes('gap-1 mb-4'):
            ui.label(
                "Connect to That's My BIS to access guild wishlists, loot history, and raider profiles."
            ).classes('text-sm text-gray-500')
            with ui.row().classes('text-sm text-gray-500 flex-wrap items-baseline gap-1'):
                ui.label("Find your Guild ID in your TMB URL (e.g.,")
                ui.html("thatsmybis.com/<b>1234</b>/my-guild/...").classes('font-mono bg-gray-100 dark:bg-gray-700 px-1 rounded')
                ui.label(") and authenticate to establish a session.")

        ui_refs['tmb_guild_id'] = ui.input(
            label='TMB Guild ID',
            value=config.get_tmb_guild_id()
        ).classes('w-full')
        tmb_guild_id_unsaved = ui.label('Unsaved changes!').classes('text-red-500 text-xs')
        tmb_guild_id_unsaved.visible = False

        initial_tmb_guild_id = config.get_tmb_guild_id() or ""
        register_field_for_tracking('tmb_guild_id', initial_tmb_guild_id, tmb_guild_id_unsaved)
        ui_refs['tmb_guild_id'].on_value_change(
            lambda e: check_field_changed('tmb_guild_id', e.value or "")
        )

        with ui.row().classes('w-full gap-2 mt-4'):
            auth_button = ui.button(
                'Authenticate TMB',
                on_click=lambda: run_tmb_authentication(auth_button)
            )
            ui_refs['auth_button'] = auth_button

            ui.button(
                'Check Session',
                on_click=lambda: check_tmb_session(ui_refs['tmb_guild_id'])
            )

            def save_tmb_settings():
                value = ui_refs['tmb_guild_id'].value.strip() if ui_refs['tmb_guild_id'].value else ""
                config.set_tmb_guild_id(value)
                mark_field_saved('tmb_guild_id', value)
                ui.notify('TMB settings saved!', type='positive')
                notify_connection_save()

            ui.button('Save', on_click=save_tmb_settings, icon='save')

        ui.timer(0.2, lambda: check_initial_tmb_auth_status(auth_button, ui_refs['tmb_guild_id']), once=True)

        # --- TMB Data Management subsection ---
        ui.separator().classes('my-4')

        with ui.row().classes('w-full items-center gap-2 mb-2'):
            ui.icon('refresh')
            ui.label('Data Management').classes('text-lg font-semibold')

        ui.label("TMB data is cached once per session. Use the button below to fetch the latest data from That's My BIS.").classes('text-sm text-gray-500 mb-4')

        def refresh_tmb_data():
            """Refresh TMB data from the server."""
            guild_id = config.get_tmb_guild_id()
            if not guild_id:
                ui.notify('TMB Guild ID is not configured.', type='negative')
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

    # ==================== WCL Section ====================
    with ui.card().classes('w-full p-4 mb-4'):
        with ui.row().classes('w-full items-center gap-2 mb-2'):
            ui.icon('assessment')
            ui.label('WarcraftLogs API').classes('text-lg font-semibold')

        with ui.column().classes('gap-1 mb-4'):
            ui.label(
                "Connect to WarcraftLogs to fetch player parse data and combat logs."
            ).classes('text-sm text-gray-500')
            with ui.row().classes('text-sm text-gray-500 flex-wrap items-baseline gap-1'):
                ui.label("Create an API client at")
                ui.label('warcraftlogs.com/api/clients').classes(
                    'text-blue-600 dark:text-blue-400 cursor-pointer hover:underline'
                ).on('click', lambda: webbrowser.open('https://www.warcraftlogs.com/api/clients'))
                ui.label("to get your credentials.")

        ui_refs['wcl_client_id'] = ui.input(
            label='WCL Client ID',
            value=config.get_wcl_client_id()
        ).classes('w-full')
        wcl_client_id_unsaved = ui.label('Unsaved changes!').classes('text-red-500 text-xs')
        wcl_client_id_unsaved.visible = False

        initial_wcl_client_id = config.get_wcl_client_id() or ""
        register_field_for_tracking('wcl_client_id', initial_wcl_client_id, wcl_client_id_unsaved)
        ui_refs['wcl_client_id'].on_value_change(
            lambda e: check_field_changed('wcl_client_id', e.value or "")
        )

        ui_refs['wcl_client_secret'] = ui.input(
            label='WCL Client Secret',
            value=config.get_wcl_client_secret(),
            password=True,
            password_toggle_button=True
        ).classes('w-full')
        wcl_client_secret_unsaved = ui.label('Unsaved changes!').classes('text-red-500 text-xs')
        wcl_client_secret_unsaved.visible = False

        initial_wcl_client_secret = config.get_wcl_client_secret() or ""
        register_field_for_tracking('wcl_client_secret', initial_wcl_client_secret, wcl_client_secret_unsaved)
        ui_refs['wcl_client_secret'].on_value_change(
            lambda e: check_field_changed('wcl_client_secret', e.value or "")
        )

        ui_refs['wcl_user_token'] = ui.input(
            label='WCL User Token (optional)',
            value=config.get_wcl_user_token(),
            password=True,
            password_toggle_button=True
        ).classes('w-full')
        wcl_user_token_unsaved = ui.label('Unsaved changes!').classes('text-red-500 text-xs')
        wcl_user_token_unsaved.visible = False

        initial_wcl_user_token = config.get_wcl_user_token() or ""
        register_field_for_tracking('wcl_user_token', initial_wcl_user_token, wcl_user_token_unsaved)
        ui_refs['wcl_user_token'].on_value_change(
            lambda e: check_field_changed('wcl_user_token', e.value or "")
        )

        ui_refs['wcl_redirect_uri'] = ui.input(
            label='WCL Redirect URI',
            value=config.get_wcl_redirect_uri()
        ).classes('w-full')
        wcl_redirect_uri_unsaved = ui.label('Unsaved changes!').classes('text-red-500 text-xs')
        wcl_redirect_uri_unsaved.visible = False

        initial_wcl_redirect_uri = config.get_wcl_redirect_uri() or ""
        register_field_for_tracking('wcl_redirect_uri', initial_wcl_redirect_uri, wcl_redirect_uri_unsaved)
        ui_refs['wcl_redirect_uri'].on_value_change(
            lambda e: check_field_changed('wcl_redirect_uri', e.value or "")
        )

        def save_wcl_settings():
            client_id = ui_refs['wcl_client_id'].value.strip() if ui_refs['wcl_client_id'].value else ""
            client_secret = ui_refs['wcl_client_secret'].value.strip() if ui_refs['wcl_client_secret'].value else ""
            user_token = ui_refs['wcl_user_token'].value.strip() if ui_refs['wcl_user_token'].value else ""
            redirect_uri = ui_refs['wcl_redirect_uri'].value.strip() if ui_refs['wcl_redirect_uri'].value else ""

            config.set_wcl_client_id(client_id)
            config.set_wcl_client_secret(client_secret)
            config.set_wcl_user_token(user_token)
            config.set_wcl_redirect_uri(redirect_uri)

            mark_field_saved('wcl_client_id', client_id)
            mark_field_saved('wcl_client_secret', client_secret)
            mark_field_saved('wcl_user_token', user_token)
            mark_field_saved('wcl_redirect_uri', redirect_uri)

            ui.notify('WCL settings saved!', type='positive')
            notify_connection_save()

        with ui.row().classes('w-full gap-2 mt-4'):
            ui.button(
                'Check Credentials',
                on_click=lambda: check_wcl_credentials(
                    ui_refs['wcl_client_id'],
                    ui_refs['wcl_client_secret'],
                    ui_refs['wcl_user_token']
                )
            )
            ui.button('Save', on_click=save_wcl_settings, icon='save')

    # ==================== Blizzard API Section ====================
    with ui.card().classes('w-full p-4 mb-4'):
        with ui.row().classes('w-full items-center gap-2 mb-2'):
            ui.icon('sports_esports')
            ui.label('Blizzard API').classes('text-lg font-semibold')

        with ui.column().classes('gap-1 mb-4'):
            ui.label(
                "Connect to the Blizzard API to fetch realm lists and item data."
            ).classes('text-sm text-gray-500')
            with ui.row().classes('text-sm text-gray-500 flex-wrap items-baseline gap-1'):
                ui.label("Create an API client at")
                ui.label('develop.battle.net/access/clients').classes(
                    'text-blue-600 dark:text-blue-400 cursor-pointer hover:underline'
                ).on('click', lambda: webbrowser.open('https://develop.battle.net/access/clients'))
                ui.label("to get your credentials.")

        ui_refs['blizzard_client_id'] = ui.input(
            label='Blizzard Client ID',
            value=config.get_blizzard_client_id()
        ).classes('w-full')
        blizzard_client_id_unsaved = ui.label('Unsaved changes!').classes('text-red-500 text-xs')
        blizzard_client_id_unsaved.visible = False

        initial_blizzard_client_id = config.get_blizzard_client_id() or ""
        register_field_for_tracking('blizzard_client_id', initial_blizzard_client_id, blizzard_client_id_unsaved)
        ui_refs['blizzard_client_id'].on_value_change(
            lambda e: check_field_changed('blizzard_client_id', e.value or "")
        )

        ui_refs['blizzard_client_secret'] = ui.input(
            label='Blizzard Client Secret',
            value=config.get_blizzard_client_secret(),
            password=True,
            password_toggle_button=True
        ).classes('w-full')
        blizzard_client_secret_unsaved = ui.label('Unsaved changes!').classes('text-red-500 text-xs')
        blizzard_client_secret_unsaved.visible = False

        initial_blizzard_client_secret = config.get_blizzard_client_secret() or ""
        register_field_for_tracking('blizzard_client_secret', initial_blizzard_client_secret, blizzard_client_secret_unsaved)
        ui_refs['blizzard_client_secret'].on_value_change(
            lambda e: check_field_changed('blizzard_client_secret', e.value or "")
        )

        def save_blizzard_settings():
            client_id = ui_refs['blizzard_client_id'].value.strip() if ui_refs['blizzard_client_id'].value else ""
            client_secret = ui_refs['blizzard_client_secret'].value.strip() if ui_refs['blizzard_client_secret'].value else ""

            config.set_blizzard_client_id(client_id)
            config.set_blizzard_client_secret(client_secret)

            mark_field_saved('blizzard_client_id', client_id)
            mark_field_saved('blizzard_client_secret', client_secret)

            ui.notify('Blizzard API settings saved!', type='positive')
            notify_blizzard_cred_change()  # Notify Settings tab to refresh server section
            notify_connection_save()

        with ui.row().classes('w-full gap-2 mt-4'):
            ui.button(
                'Check Credentials',
                on_click=lambda: check_blizzard_credentials(
                    ui_refs['blizzard_client_id'],
                    ui_refs['blizzard_client_secret']
                )
            )
            ui.button('Save', on_click=save_blizzard_settings, icon='save')

    # ==================== LLM Configuration Section ====================
    with ui.card().classes('w-full p-4 mb-4'):
        with ui.row().classes('w-full items-center gap-2 mb-2'):
            ui.icon('smart_toy')
            ui.label('LLM Configuration').classes('text-lg font-semibold')

        ui.label(
            "Configure the AI model used for loot council recommendations. "
            "Enter your API key and click 'Test Connection' to load available models. "
            "Adjust the delay slider if you encounter rate limit errors."
        ).classes('text-sm text-gray-500 mb-4')

        providers = get_available_providers()
        initial_provider = config.get_llm_provider()

        ui_refs['lc_provider'] = ui.select(
            label='LLM Provider',
            options={p['value']: p['label'] for p in providers},
            value=initial_provider
        ).classes('w-full')
        lc_provider_unsaved = ui.label('Unsaved changes!').classes('text-red-500 text-xs')
        lc_provider_unsaved.visible = False

        register_field_for_tracking('lc_provider', initial_provider or "", lc_provider_unsaved)
        ui_refs['lc_provider'].on_value_change(
            lambda e: check_field_changed('lc_provider', e.value or "")
        )

        ui_refs['lc_api_key'] = ui.input(
            label='API Key',
            password=True,
            password_toggle_button=True,
            placeholder=get_provider_key_placeholder(initial_provider),
            value=config.get_llm_api_key(initial_provider)
        ).classes('w-full')
        lc_api_key_unsaved = ui.label('Unsaved changes!').classes('text-red-500 text-xs')
        lc_api_key_unsaved.visible = False

        initial_api_key = config.get_llm_api_key(initial_provider) or ""
        register_field_for_tracking('lc_api_key', initial_api_key, lc_api_key_unsaved)
        ui_refs['lc_api_key'].on_value_change(
            lambda e: check_field_changed('lc_api_key', e.value or "")
        )

        # Model selector - starts disabled until connection is tested
        ui_refs['lc_model'] = ui.select(
            label='Model (test connection first)',
            options={},
            value=None
        ).classes('w-full')
        lc_model_unsaved = ui.label('Unsaved changes!').classes('text-red-500 text-xs')
        lc_model_unsaved.visible = False
        ui_refs['lc_model'].disable()

        initial_model = config.get_llm_model() or ""
        register_field_for_tracking('lc_model', initial_model, lc_model_unsaved)
        ui_refs['lc_model'].on_value_change(
            lambda e: check_field_changed('lc_model', e.value or "")
        )

        def on_provider_change(e):
            new_provider = e.sender.value
            # Reset model dropdown when provider changes
            ui_refs['lc_model'].options = {}
            ui_refs['lc_model'].value = None
            ui_refs['lc_model'].props('label=Model (test connection first)')
            ui_refs['lc_model'].disable()
            ui_refs['lc_api_key'].props('placeholder=' + get_provider_key_placeholder(new_provider))
            ui_refs['lc_api_key'].value = config.get_llm_api_key(new_provider)
            config.set_llm_provider(new_provider)
            check_field_changed('lc_provider', new_provider or "")

        ui_refs['lc_provider'].on('update:model-value', on_provider_change)

        saved_delay = config.get_llm_delay_seconds()
        initial_delay = saved_delay if saved_delay is not None else 2.0
        with ui.row().classes('w-full items-center gap-4 mt-2'):
            ui.label('Delay between items:').classes('text-sm')
            ui_refs['lc_delay'] = ui.slider(
                value=initial_delay,
                min=1,
                max=10,
                step=1
            ).classes('flex-grow')
            delay_display = ui.label(f'{int(initial_delay)}s').classes('text-sm w-8')

            def update_delay_display(e):
                delay_display.text = f'{int(e.value)}s'
                check_field_changed('lc_delay', str(e.value) if e.value else "2.0")

            ui_refs['lc_delay'].on_value_change(update_delay_display)

        lc_delay_unsaved = ui.label('Unsaved changes!').classes('text-red-500 text-xs')
        lc_delay_unsaved.visible = False

        register_field_for_tracking('lc_delay', str(initial_delay), lc_delay_unsaved)

        def save_llm_settings():
            provider = ui_refs['lc_provider'].value
            api_key = ui_refs['lc_api_key'].value
            model = ui_refs['lc_model'].value
            delay = ui_refs['lc_delay'].value
            if not model:
                ui.notify('Please test connection and select a model first', type='warning')
                return
            config.set_llm_api_key(api_key, provider)
            config.set_llm_model(model)
            config.set_llm_delay_seconds(float(delay) if delay else 2.0)

            mark_field_saved('lc_provider', provider or "")
            mark_field_saved('lc_api_key', api_key or "")
            mark_field_saved('lc_model', model or "")
            mark_field_saved('lc_delay', str(delay) if delay else "2.0")

            ui.notify(f'Saved LLM settings for {provider}', type='positive')
            notify_connection_save()

        with ui.row().classes('w-full gap-2 mt-4'):
            ui.button(
                'Test Connection',
                on_click=lambda: check_llm_connection(
                    ui_refs['lc_provider'],
                    ui_refs['lc_model'],
                    ui_refs['lc_api_key']
                ),
                icon='wifi'
            )
            ui.button('Save', on_click=save_llm_settings, icon='save')

        # Initialize model dropdown on startup if API key is saved
        ui.timer(0.5, lambda: init_llm_model_dropdown(
            ui_refs['lc_provider'],
            ui_refs['lc_model'],
            ui_refs['lc_api_key']
        ), once=True)

    return ui_refs


def check_connections_configured(ui_refs: dict) -> tuple[bool, list[str]]:
    """
    Check if all Core Connections fields are configured.

    Args:
        ui_refs: Dictionary of UI element references from connections tab

    Returns:
        Tuple of (is_configured, list_of_missing_fields)
    """
    missing = []

    # TMB Guild ID - use SAVED config value, not UI value
    saved_tmb_guild_id = config.get_tmb_guild_id()
    if not saved_tmb_guild_id or not saved_tmb_guild_id.strip():
        missing.append("TMB Guild ID")

    # WCL credentials
    wcl_client_id = ui_refs.get('wcl_client_id')
    if not wcl_client_id or not (wcl_client_id.value and wcl_client_id.value.strip()):
        missing.append("WCL Client ID")

    wcl_client_secret = ui_refs.get('wcl_client_secret')
    if not wcl_client_secret or not (wcl_client_secret.value and wcl_client_secret.value.strip()):
        missing.append("WCL Client Secret")

    wcl_redirect_uri = ui_refs.get('wcl_redirect_uri')
    if not wcl_redirect_uri or not (wcl_redirect_uri.value and wcl_redirect_uri.value.strip()):
        missing.append("WCL Redirect URI")

    # Blizzard credentials
    blizzard_client_id = ui_refs.get('blizzard_client_id')
    if not blizzard_client_id or not (blizzard_client_id.value and blizzard_client_id.value.strip()):
        missing.append("Blizzard Client ID")

    blizzard_client_secret = ui_refs.get('blizzard_client_secret')
    if not blizzard_client_secret or not (blizzard_client_secret.value and blizzard_client_secret.value.strip()):
        missing.append("Blizzard Client Secret")

    # LLM configuration
    lc_provider = ui_refs.get('lc_provider')
    if not lc_provider or not lc_provider.value:
        missing.append("LLM Provider")

    lc_api_key = ui_refs.get('lc_api_key')
    if not lc_api_key or not (lc_api_key.value and lc_api_key.value.strip()):
        missing.append("LLM API Key")

    lc_model = ui_refs.get('lc_model')
    if not lc_model or not lc_model.value:
        missing.append("LLM Model (test connection first)")

    return (len(missing) == 0, missing)
