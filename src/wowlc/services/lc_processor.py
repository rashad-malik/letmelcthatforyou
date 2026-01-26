"""
Loot Council Processor - API-based loot decision engine

This module handles the orchestration of loot council decisions via LLM APIs.
It processes items one at a time to stay within API rate limits while preserving
full LLM decision-making capability.

Supports multiple providers via LiteLLM: Anthropic, OpenAI, Google, Mistral, Groq, etc.
"""

import logging
import os
import re
import time
import pandas as pd
from dataclasses import dataclass
from typing import List, Dict, Optional, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import litellm
    from litellm import completion
    HAS_LITELLM = True
except ImportError:
    HAS_LITELLM = False

from ..core.paths import get_path_manager
from ..tools.get_item_candidates import (
    get_item_candidates_prompt,
    get_zone_items,
)

# Get PathManager instance
paths = get_path_manager()

# Base system prompt components
_SYSTEM_PROMPT_BASE = """You are an expert World of Warcraft loot council assistant making fair loot distribution decisions.

Use the guild policy rules as the basis for all decisions.

IMPORTANT CONTEXT:
- "Item Priority: Mainspec" means this item is for the player's primary raid role
- "Item Priority: Offspec" means this item is for an alternate role the player sometimes plays"""

_WISHLIST_POSITION_CONTEXT = """- "Wishlist Position" indicates how much the player wants this item (lower = more desired)"""

_ILVL_COMPARISON_CONTEXT = """- "Upgrade size" is measured in item level difference compared to currently equipped gear (higher = better upgrade)"""

_SESSION_TRACKING_CONTEXT = """- "Items Already Won This Session" tracks how many items a player has received in the current loot council session, in order to prevent funnelling loot to the same players repeatedly."""

_CUSTOM_NOTE_CONTEXT = """- "Custom Note" contains officer-provided notes about specific raiders relevant to loot decisions."""

_GUILD_PRIORITY_NOTE_CONTEXT = """- "Guild Priority Note" contains overarching guidelines on how this item should be distributed."""

_SYSTEM_PROMPT_FOOTER = """
Be concise. Output only the requested format with a brief rationale."""


def get_system_prompt(
    include_session_tracking: bool = False,
    has_custom_notes: bool = False,
    has_wishlist_position: bool = True,
    has_ilvl_comparison: bool = False,
    has_guild_priority_note: bool = False
) -> str:
    """Build the system prompt dynamically based on context.

    Args:
        include_session_tracking: Include session tracking context (raid zone mode)
        has_custom_notes: Include custom note context (only if candidates have notes)
        has_wishlist_position: Include wishlist position context (only if metric is enabled)
        has_ilvl_comparison: Include ilvl comparison context (only if metric is enabled)
        has_guild_priority_note: Include guild priority note context (only if item has a note)

    Returns:
        Complete system prompt string
    """
    parts = [_SYSTEM_PROMPT_BASE]

    if has_wishlist_position:
        parts.append(_WISHLIST_POSITION_CONTEXT)

    if has_ilvl_comparison:
        parts.append(_ILVL_COMPARISON_CONTEXT)

    if include_session_tracking:
        parts.append(_SESSION_TRACKING_CONTEXT)

    if has_custom_notes:
        parts.append(_CUSTOM_NOTE_CONTEXT)

    if has_guild_priority_note:
        parts.append(_GUILD_PRIORITY_NOTE_CONTEXT)

    parts.append(_SYSTEM_PROMPT_FOOTER)

    return "\n".join(parts)


@dataclass
class LootDecision:
    """Container for a single loot decision."""
    item_name: str
    item_slot: Optional[str]
    suggestion_1: str
    suggestion_2: str
    suggestion_3: str
    rationale: str
    success: bool
    error: Optional[str] = None
    # Debug fields for viewing API request/response
    debug_prompt: Optional[str] = None
    debug_response: Optional[str] = None


class LootCouncilProcessor:
    """
    Processes loot council decisions via LLM APIs.

    This class handles:
    - Generating compact prompts for each item
    - Making API calls with rate limiting
    - Parsing LLM responses into structured decisions
    - Tracking session allocations to avoid funneling
    - Saving results to CSV

    Supports multiple providers via LiteLLM.
    """

    # Map provider names to their environment variable for API keys
    API_KEY_ENV_MAP = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "gemini": "GEMINI_API_KEY",
        "mistral": "MISTRAL_API_KEY",
        "groq": "GROQ_API_KEY",
        "xai": "XAI_API_KEY",
        "cohere": "COHERE_API_KEY",
        "together_ai": "TOGETHER_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }

    # Map provider names to their LiteLLM model prefix
    PROVIDER_PREFIX_MAP = {
        "anthropic": "anthropic/",
        "openai": "",  # OpenAI is default, no prefix needed
        "gemini": "gemini/",
        "mistral": "mistral/",
        "groq": "groq/",
        "xai": "xai/",
        "cohere": "cohere/",
        "together_ai": "together_ai/",
        "deepseek": "deepseek/",
    }

    # Retry configuration for transient errors
    MAX_RETRIES = 3
    RETRY_DELAY = 2.0  # Initial delay in seconds (exponential backoff)

    def __init__(
        self,
        api_key: str,
        provider: str = "anthropic",
        model: str = "claude-sonnet-4-20250514",
        delay_seconds: float = 2.0
    ):
        """
        Initialize the processor.

        Args:
            api_key: API key for the selected provider
            provider: LLM provider (anthropic, openai, google, mistral, groq)
            model: Model to use (default: claude-sonnet-4-20250514)
            delay_seconds: Delay between API calls (default: 2.0 for rate limiting)
        """
        if not HAS_LITELLM:
            raise ImportError(
                "litellm package not installed. "
                "Install with: pip install litellm"
            )

        self.provider = provider.lower()
        self.model = model
        self.delay_seconds = delay_seconds

        # Session allocation tracking: {player_name: suggestion_1_count}
        self.session_allocations: Dict[str, int] = {}

        # Configure API key for the selected provider
        self._configure_api_key(api_key)

    def _configure_api_key(self, api_key: str) -> None:
        """Set the API key as an environment variable for LiteLLM."""
        env_var = self.API_KEY_ENV_MAP.get(self.provider)
        if env_var:
            os.environ[env_var] = api_key

    def reset_session_allocations(self) -> None:
        """Reset session allocation tracking. Call at start of a new LC run."""
        self.session_allocations = {}

    def record_allocation(self, player_name: str) -> None:
        """Record a Suggestion 1 allocation for a player in this session."""
        if player_name:
            self.session_allocations[player_name] = self.session_allocations.get(player_name, 0) + 1

    def get_candidate_allocations(self, candidate_names: List[str]) -> Dict[str, int]:
        """
        Get session allocation counts for specific candidates.

        Args:
            candidate_names: List of candidate names to look up

        Returns:
            Dict of {player_name: allocation_count} for candidates with allocations
        """
        return {
            name: count
            for name, count in self.session_allocations.items()
            if name in candidate_names and count > 0
        }

    def _get_litellm_model_string(self) -> str:
        """
        Get the LiteLLM-formatted model string with provider prefix.

        LiteLLM uses prefixes for non-OpenAI providers:
        - anthropic/claude-3-opus-20240229
        - gemini/gemini-1.5-pro
        - mistral/mistral-large-latest

        Note: Some providers (like Gemini) return models that already include
        the prefix in litellm.models_by_provider, so we check first to avoid
        duplicating the prefix.
        """
        prefix = self.PROVIDER_PREFIX_MAP.get(self.provider, "")
        # Don't add prefix if model already starts with it
        if prefix and self.model.startswith(prefix):
            return self.model
        return f"{prefix}{self.model}"

    def process_item(self, item_name: str, single_item_mode: bool = False) -> LootDecision:
        """
        Process a single item and return a loot decision.

        Args:
            item_name: Name of the item to process
            single_item_mode: If True, uses simplified prompt without session tracking

        Returns:
            LootDecision object with suggestion assignments and rationale
        """
        # Generate prompt - skip session allocations in single item mode
        prompt_result = get_item_candidates_prompt(
            item_name,
            session_allocations={} if single_item_mode else self.session_allocations
        )

        if not prompt_result["success"]:
            return LootDecision(
                item_name=item_name,
                item_slot=prompt_result.get("item_slot"),
                suggestion_1="",
                suggestion_2="",
                suggestion_3="",
                rationale="",
                success=False,
                error=prompt_result["error"],
                debug_prompt=None,
                debug_response=None
            )

        # Build system prompt based on mode and enabled metrics
        has_custom_notes = prompt_result.get("has_custom_notes", False)
        has_wishlist_position = prompt_result.get("has_wishlist_position", True)
        has_ilvl_comparison = prompt_result.get("has_ilvl_comparison", False)
        has_guild_priority_note = prompt_result.get("has_guild_priority_note", False)
        system_prompt = get_system_prompt(
            include_session_tracking=not single_item_mode,
            has_custom_notes=has_custom_notes,
            has_wishlist_position=has_wishlist_position,
            has_ilvl_comparison=has_ilvl_comparison,
            has_guild_priority_note=has_guild_priority_note
        )

        # Build the full prompt for debug display
        user_prompt = prompt_result["prompt"]
        full_prompt = f"[SYSTEM]\n{system_prompt}\n\n[USER]\n{user_prompt}"

        # Call API via LiteLLM with retry logic for transient errors
        response_text = None
        try:
            model_string = self._get_litellm_model_string()

            # Retry loop for transient errors (503, rate limits, connection issues)
            last_error = None
            for attempt in range(self.MAX_RETRIES + 1):
                try:
                    response = completion(
                        model=model_string,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                        max_tokens=500
                    )
                    break  # Success, exit retry loop
                except (litellm.InternalServerError, litellm.RateLimitError,
                        litellm.APIConnectionError) as e:
                    last_error = e
                    if attempt < self.MAX_RETRIES:
                        delay = self.RETRY_DELAY * (2 ** attempt)
                        logger.warning(
                            f"Attempt {attempt + 1}/{self.MAX_RETRIES + 1} failed for '{item_name}': {e}. "
                            f"Retrying in {delay:.1f}s..."
                        )
                        time.sleep(delay)
                        continue
                    # Max retries exhausted, re-raise to be caught by outer handlers
                    raise

            response_text = response.choices[0].message.content

            # Parse response
            decision = self._parse_response(
                response_text,
                item_name,
                prompt_result["item_slot"],
                debug_prompt=full_prompt,
                debug_response=response_text
            )

            # Record Suggestion 1 allocation for session tracking
            if decision.success and decision.suggestion_1:
                self.record_allocation(decision.suggestion_1)

            return decision

        except litellm.RateLimitError as e:
            return LootDecision(
                item_name=item_name,
                item_slot=prompt_result.get("item_slot"),
                suggestion_1="",
                suggestion_2="",
                suggestion_3="",
                rationale="",
                success=False,
                error=f"Rate limit exceeded: {str(e)}",
                debug_prompt=full_prompt,
                debug_response=response_text
            )
        except litellm.AuthenticationError as e:
            return LootDecision(
                item_name=item_name,
                item_slot=prompt_result.get("item_slot"),
                suggestion_1="",
                suggestion_2="",
                suggestion_3="",
                rationale="",
                success=False,
                error=f"Invalid API key for {self.provider}: {str(e)}",
                debug_prompt=full_prompt,
                debug_response=response_text
            )
        except litellm.APIConnectionError as e:
            return LootDecision(
                item_name=item_name,
                item_slot=prompt_result.get("item_slot"),
                suggestion_1="",
                suggestion_2="",
                suggestion_3="",
                rationale="",
                success=False,
                error=f"Connection error to {self.provider}: {str(e)}",
                debug_prompt=full_prompt,
                debug_response=response_text
            )
        except Exception as e:
            return LootDecision(
                item_name=item_name,
                item_slot=prompt_result.get("item_slot"),
                suggestion_1="",
                suggestion_2="",
                suggestion_3="",
                rationale="",
                success=False,
                error=f"API error ({self.provider}): {str(e)}",
                debug_prompt=full_prompt,
                debug_response=response_text
            )

    def _parse_response(
        self,
        response_text: str,
        item_name: str,
        item_slot: Optional[str],
        debug_prompt: Optional[str] = None,
        debug_response: Optional[str] = None
    ) -> LootDecision:
        """
        Parse the LLM response into a structured decision.

        Expected format:
        Suggestion 1: [Name]
        Suggestion 2: [Name]
        Suggestion 3: [Name]
        Rationale: [Text]
        """
        suggestion_1 = ""
        suggestion_2 = ""
        suggestion_3 = ""
        rationale = ""

        # Extract Suggestion 1
        match = re.search(r'Suggestion\s*1[:\s]+([^\n]+)', response_text, re.IGNORECASE)
        if match:
            suggestion_1 = match.group(1).strip()

        # Extract Suggestion 2
        match = re.search(r'Suggestion\s*2[:\s]+([^\n]+)', response_text, re.IGNORECASE)
        if match:
            suggestion_2 = match.group(1).strip()

        # Extract Suggestion 3
        match = re.search(r'Suggestion\s*3[:\s]+([^\n]+)', response_text, re.IGNORECASE)
        if match:
            suggestion_3 = match.group(1).strip()

        # Extract Rationale (everything after "Rationale:")
        match = re.search(r'Rationale[:\s]+(.+)', response_text, re.IGNORECASE | re.DOTALL)
        if match:
            rationale = match.group(1).strip()

        return LootDecision(
            item_name=item_name,
            item_slot=item_slot,
            suggestion_1=suggestion_1,
            suggestion_2=suggestion_2,
            suggestion_3=suggestion_3,
            rationale=rationale,
            success=True,
            debug_prompt=debug_prompt,
            debug_response=debug_response
        )

    def process_zone(
        self,
        zone_name: str,
        progress_callback: Optional[Callable[[int, int, str, LootDecision], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None
    ) -> List[LootDecision]:
        """
        Process all items in a raid zone.

        Args:
            zone_name: Name of the raid zone
            progress_callback: Optional callback(current, total, item_name, decision)
            cancel_check: Optional callback that returns True if processing should stop

        Returns:
            List of LootDecision objects
        """
        items = get_zone_items(zone_name)

        if not items:
            return []

        decisions = []
        total = len(items)

        for i, item_name in enumerate(items):
            # Check for cancellation
            if cancel_check and cancel_check():
                break

            # Process item
            decision = self.process_item(item_name)
            decisions.append(decision)

            # Callback for progress updates
            if progress_callback:
                progress_callback(i + 1, total, item_name, decision)

            # Rate limiting delay (skip on last item)
            if i < total - 1:
                time.sleep(self.delay_seconds)

        return decisions

    def save_decisions_to_csv(
        self,
        decisions: List[LootDecision],
        output_path: Optional[Path] = None
    ) -> Path:
        """
        Save loot decisions to a CSV file.

        Args:
            decisions: List of LootDecision objects
            output_path: Optional custom output path

        Returns:
            Path to the saved CSV file
        """
        if output_path is None:
            output_path = paths.get_export_path("loot_decisions_api.csv")

        data = []
        for d in decisions:
            data.append({
                "Name": d.item_name,
                "Slot": d.item_slot or "",
                "Suggestion 1": d.suggestion_1,
                "Suggestion 2": d.suggestion_2,
                "Suggestion 3": d.suggestion_3,
                "Rationale": d.rationale,
                "Status": "OK" if d.success else f"Error: {d.error}"
            })

        df = pd.DataFrame(data)
        df.to_csv(output_path, index=False, encoding='utf-8-sig')

        return output_path


def get_available_models(provider: str = "anthropic") -> List[Dict[str, str]]:
    """
    Get list of available models for the specified provider.

    Args:
        provider: Provider key (e.g., "anthropic", "openai", "google")

    Returns:
        List of dicts with 'value' and 'label' keys
    """
    from .llm_providers import get_provider_models
    return get_provider_models(provider)
