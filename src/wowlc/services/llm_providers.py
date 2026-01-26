"""
LLM Provider Configuration Registry

Uses LiteLLM's model registry to dynamically discover available models per provider.
Provides fallback defaults and friendly labels for common models.
"""

from typing import List, Dict, TypedDict

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

        # Convert to ModelInfo format - use model ID as both value and label, sorted alphabetically
        return [{"value": model_id, "label": model_id} for model_id in sorted(filtered_models)]

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

        # Convert to ModelInfo format, sorted alphabetically
        result = [{"value": model_id, "label": model_id} for model_id in sorted(valid_models)]
        logger.info(f"[LLM] Returning {len(result)} models")
        return result

    except Exception as e:
        logger.error(f"[LLM] Exception in get_validated_models: {type(e).__name__}: {e}")
        import traceback
        logger.error(f"[LLM] Traceback:\n{traceback.format_exc()}")
        return []
