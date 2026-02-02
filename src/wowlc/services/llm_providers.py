"""
LLM Provider Configuration Registry

Uses LiteLLM's model registry to dynamically discover available models per provider.
Provides fallback defaults and friendly labels for common models.
"""

from typing import List, Dict, TypedDict
import time
import json
import re
from pathlib import Path

import logging
_logger = logging.getLogger(__name__)

try:
    import litellm
    HAS_LITELLM = True
    _logger.info("[LLM] LiteLLM imported successfully")
except ImportError as e:
    HAS_LITELLM = False
    _logger.error(f"[LLM] LiteLLM import failed: {e}")
    import traceback
    _logger.error(f"[LLM] Import traceback:\n{traceback.format_exc()}")

# In-memory cache for model display names
_model_display_names: Dict[str, str] = {}
_display_names_loaded: bool = False

# OpenRouter API for fetching pretty model names
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
CACHE_MAX_AGE_DAYS = 7


class ModelInfo(TypedDict):
    value: str      # Model ID for API calls
    label: str      # Display name in UI


class ProviderInfo(TypedDict):
    name: str                   # Display name
    key_prefix: str             # Expected API key prefix (for validation hints)
    key_placeholder: str        # Placeholder text for API key input
    env_var: str                # Environment variable for API key
    litellm_provider: str       # LiteLLM provider name for model lookup


# Provider metadata - models are fetched dynamically from LiteLLM
PROVIDERS: Dict[str, ProviderInfo] = {
    "anthropic": {
        "name": "Anthropic (Claude)",
        "key_prefix": "sk-ant-",
        "key_placeholder": "sk-ant-...",
        "env_var": "ANTHROPIC_API_KEY",
        "litellm_provider": "anthropic",
    },
    "openai": {
        "name": "OpenAI (GPT)",
        "key_prefix": "sk-",
        "key_placeholder": "sk-...",
        "env_var": "OPENAI_API_KEY",
        "litellm_provider": "openai",
    },
    "gemini": {
        "name": "Google (Gemini)",
        "key_prefix": "AI",
        "key_placeholder": "AIza...",
        "env_var": "GEMINI_API_KEY",
        "litellm_provider": "gemini",
    },
    "mistral": {
        "name": "Mistral AI",
        "key_prefix": "",
        "key_placeholder": "API key...",
        "env_var": "MISTRAL_API_KEY",
        "litellm_provider": "mistral",
    },
    "groq": {
        "name": "Groq (Fast Inference)",
        "key_prefix": "gsk_",
        "key_placeholder": "gsk_...",
        "env_var": "GROQ_API_KEY",
        "litellm_provider": "groq",
    },
    "xai": {
        "name": "xAI (Grok)",
        "key_prefix": "xai-",
        "key_placeholder": "xai-...",
        "env_var": "XAI_API_KEY",
        "litellm_provider": "xai",
    },
    "cohere": {
        "name": "Cohere",
        "key_prefix": "",
        "key_placeholder": "API key...",
        "env_var": "COHERE_API_KEY",
        "litellm_provider": "cohere",
    },
    "together_ai": {
        "name": "Together AI",
        "key_prefix": "",
        "key_placeholder": "API key...",
        "env_var": "TOGETHER_API_KEY",
        "litellm_provider": "together_ai",
    },
    "deepseek": {
        "name": "DeepSeek",
        "key_prefix": "sk-",
        "key_placeholder": "sk-...",
        "env_var": "DEEPSEEK_API_KEY",
        "litellm_provider": "deepseek",
    },
}


# =============================================================================
# Model Display Name Functions (OpenRouter API)
# =============================================================================

def _get_cache_path() -> Path:
    """Get the cache file path for OpenRouter models."""
    from ..core import paths
    return paths.get_path_manager().get_openrouter_models_cache_path()


def _load_display_names_from_cache() -> Dict[str, str]:
    """Load model display names from cache file if valid."""
    cache_path = _get_cache_path()

    if not cache_path.exists():
        return {}

    try:
        # Check cache age
        file_age_days = (time.time() - cache_path.stat().st_mtime) / 86400
        if file_age_days > CACHE_MAX_AGE_DAYS:
            _logger.info(f"[LLM] OpenRouter cache expired ({file_age_days:.1f} days old)")
            return {}

        with open(cache_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            _logger.info(f"[LLM] Loaded {len(data)} model display names from cache")
            return data
    except Exception as e:
        _logger.warning(f"[LLM] Failed to load display names cache: {e}")
        return {}


def _save_display_names_to_cache(names: Dict[str, str]) -> None:
    """Save model display names to cache file."""
    cache_path = _get_cache_path()

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, 'w', encoding='utf-8') as f:
            json.dump(names, f, indent=2)
        _logger.info(f"[LLM] Saved {len(names)} model display names to cache")
    except Exception as e:
        _logger.warning(f"[LLM] Failed to save display names cache: {e}")


def _fetch_display_names_from_api() -> Dict[str, str]:
    """Fetch model display names from OpenRouter API."""
    import urllib.request
    import ssl

    try:
        _logger.info("[LLM] Fetching model names from OpenRouter API...")

        # Create request with timeout
        request = urllib.request.Request(
            OPENROUTER_MODELS_URL,
            headers={'User-Agent': 'letmelcthatforyou/1.0'}
        )

        # Use default SSL context
        context = ssl.create_default_context()

        with urllib.request.urlopen(request, timeout=10, context=context) as response:
            data = json.loads(response.read().decode('utf-8'))

        # Parse response: {"data": [{"id": "anthropic/claude-3-opus", "name": "Claude 3 Opus", ...}, ...]}
        names = {}
        for model in data.get('data', []):
            model_id = model.get('id', '')
            model_name = model.get('name', '')
            if model_id and model_name:
                names[model_id] = model_name

        _logger.info(f"[LLM] Fetched {len(names)} model names from OpenRouter")
        return names

    except Exception as e:
        _logger.warning(f"[LLM] Failed to fetch from OpenRouter API: {e}")
        return {}


def get_model_display_names() -> Dict[str, str]:
    """
    Get mapping of model IDs to human-readable display names.

    Uses caching strategy:
    1. Return in-memory cache if already loaded
    2. Load from local cache file if exists and < 7 days old
    3. Fetch from OpenRouter API and save to cache
    4. Graceful fallback: returns empty dict if all fails (raw IDs used as fallback)

    Returns:
        Dict mapping model IDs to display names (e.g., {"anthropic/claude-3-opus": "Claude 3 Opus"})
    """
    global _model_display_names, _display_names_loaded

    # Return cached if already loaded this session
    if _display_names_loaded:
        return _model_display_names

    # Try loading from file cache
    _model_display_names = _load_display_names_from_cache()

    if _model_display_names:
        _display_names_loaded = True
        return _model_display_names

    # Fetch from API
    _model_display_names = _fetch_display_names_from_api()

    if _model_display_names:
        _save_display_names_to_cache(_model_display_names)
        _display_names_loaded = True

    return _model_display_names


def _parse_model_id_to_display_name(model_id: str) -> str:
    """
    Parse a raw model ID into a human-readable display name.

    Handles patterns like:
    - anthropic/claude-opus-4-5-20251101 → Claude Opus 4.5
    - anthropic/claude-3-haiku-20240307 → Claude 3 Haiku
    - openai/gpt-4o-2024-08-06 → Gpt 4o
    - mistral/mistral-large-2411 → Mistral Large

    Args:
        model_id: Raw model ID (e.g., "anthropic/claude-opus-4-5-20251101")

    Returns:
        Human-readable display name
    """
    # Remove provider prefix if present
    if '/' in model_id:
        model_id = model_id.split('/', 1)[1]

    # Remove date suffix (8 digits at end, optionally preceded by hyphen)
    model_id = re.sub(r'-?\d{8}$', '', model_id)
    # Also handle YYMM format like mistral-large-2411
    model_id = re.sub(r'-\d{4}$', '', model_id)

    # Split into parts
    parts = model_id.split('-')

    # Process version numbers: look for patterns like "4-5" (meaning 4.5) or "4-1" (meaning 4.1)
    processed_parts = []
    i = 0
    while i < len(parts):
        part = parts[i]

        # Check if this is a single digit that might be a major version
        if part.isdigit() and len(part) == 1:
            # Check if next part is also a single digit (minor version)
            if i + 1 < len(parts) and parts[i + 1].isdigit() and len(parts[i + 1]) == 1:
                # Combine as version: "4" + "5" → "4.5"
                processed_parts.append(f"{part}.{parts[i + 1]}")
                i += 2
                continue
            else:
                # Just a major version: "4" or "3"
                processed_parts.append(part)
        else:
            # Regular word - title case it
            processed_parts.append(part.title())
        i += 1

    return ' '.join(processed_parts)


def get_display_name(model_id: str) -> str:
    """
    Get human-readable display name for a model ID.

    Args:
        model_id: Raw model ID (e.g., "claude-3-opus-20240229" or "anthropic/claude-3-opus")

    Returns:
        Display name if found, otherwise a parsed version of the model_id.
    """
    names = get_model_display_names()

    # Direct match
    if model_id in names:
        return names[model_id]

    # Try with common provider prefixes
    for prefix in ['anthropic/', 'openai/', 'google/', 'mistral/', 'meta-llama/', 'cohere/']:
        prefixed_id = f"{prefix}{model_id}"
        if prefixed_id in names:
            return names[prefixed_id]

    # Fallback: parse the model ID into a readable name
    return _parse_model_id_to_display_name(model_id)


# =============================================================================
# Provider Functions
# =============================================================================

def get_available_providers() -> List[Dict[str, str]]:
    """Get list of available providers for UI dropdown."""
    return [
        {"value": key, "label": info["name"]}
        for key, info in PROVIDERS.items()
    ]


def get_provider_models(provider: str) -> List[ModelInfo]:
    """
    Get available models for a specific provider using LiteLLM's model registry.

    Returns models from litellm.models_by_provider if available,
    filtered to only include chat/completion models (excludes embeddings, images, audio).
    """
    if not HAS_LITELLM:
        return []

    if provider not in PROVIDERS:
        return []

    litellm_provider = PROVIDERS[provider]["litellm_provider"]

    try:
        # Get models from LiteLLM's registry
        models = litellm.models_by_provider.get(litellm_provider, [])

        if not models:
            return []

        # Filter to only chat/completion models (exclude embeddings, images, audio, etc.)
        ALLOWED_MODES = {"chat", "completion"}
        filtered_models = []

        for model_id in models:
            # Check model_cost for mode information - skip models not in model_cost
            model_info = litellm.model_cost.get(model_id)
            if model_info is None:
                continue

            mode = model_info.get("mode")
            if mode in ALLOWED_MODES:
                filtered_models.append(model_id)

        # Convert to ModelInfo format with pretty display names, sorted alphabetically
        return [{"value": model_id, "label": get_display_name(model_id)} for model_id in sorted(filtered_models)]

    except Exception:
        return []


def get_provider_key_placeholder(provider: str) -> str:
    """Get the API key placeholder for a provider."""
    if provider in PROVIDERS:
        return PROVIDERS[provider]["key_placeholder"]
    return "API key..."


def get_provider_env_var(provider: str) -> str:
    """Get the environment variable name for a provider's API key."""
    if provider in PROVIDERS:
        return PROVIDERS[provider]["env_var"]
    return ""


def get_default_model(provider: str) -> str:
    """Get the default (first) model for a provider."""
    models = get_provider_models(provider)
    return models[0]["value"] if models else ""


def get_validated_models(provider: str, api_key: str) -> List[ModelInfo]:
    """
    Get models that are actually available for the given API key.

    Uses LiteLLM's get_valid_models with check_provider_endpoint=True
    to query the provider's /models endpoint and return only accessible models.

    Args:
        provider: Provider key (e.g., "anthropic", "openai", "gemini")
        api_key: The API key to validate against

    Returns:
        List of ModelInfo dicts with 'value' and 'label' keys for valid models
    """
    import logging
    import sys
    import os

    logger = logging.getLogger(__name__)

    # Debug: Log SSL environment
    logger.info(f"[LLM] get_validated_models called for provider: {provider}")
    logger.info(f"[LLM] sys.frozen: {getattr(sys, 'frozen', False)}")
    logger.info(f"[LLM] SSL_CERT_FILE: {os.environ.get('SSL_CERT_FILE', 'NOT SET')}")
    logger.info(f"[LLM] REQUESTS_CA_BUNDLE: {os.environ.get('REQUESTS_CA_BUNDLE', 'NOT SET')}")

    if not HAS_LITELLM:
        logger.warning("[LLM] LiteLLM not available")
        return []

    if provider not in PROVIDERS:
        logger.warning(f"[LLM] Provider '{provider}' not in PROVIDERS")
        return []

    litellm_provider = PROVIDERS[provider]["litellm_provider"]
    logger.info(f"[LLM] Using litellm_provider: {litellm_provider}")

    try:
        from litellm import get_valid_models

        logger.info("[LLM] Calling get_valid_models...")
        valid_models = get_valid_models(
            check_provider_endpoint=True,
            custom_llm_provider=litellm_provider,
            api_key=api_key
        )
        logger.info(f"[LLM] get_valid_models returned: {valid_models}")

        if not valid_models:
            logger.warning("[LLM] No valid models returned")
            return []

        # Convert to ModelInfo format with pretty display names, sorted alphabetically
        result = [{"value": model_id, "label": get_display_name(model_id)} for model_id in sorted(valid_models)]
        logger.info(f"[LLM] Returning {len(result)} models")
        return result

    except Exception as e:
        logger.error(f"[LLM] Exception in get_validated_models: {type(e).__name__}: {e}")
        import traceback
        logger.error(f"[LLM] Traceback:\n{traceback.format_exc()}")
        return []
