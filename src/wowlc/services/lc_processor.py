"""
Loot Council Processor - API-based loot decision engine

This module handles the orchestration of loot council decisions via LLM APIs.
It processes items one at a time to stay within API rate limits while preserving
full LLM decision-making capability.

Supports multiple providers via any-llm: hosted (Anthropic, OpenAI, Google, etc.)
and local runtimes (Ollama, LM Studio, etc.).
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
    import any_llm
    from any_llm import completion
    from any_llm.exceptions import (
        AnyLLMError,
        AuthenticationError,
        InvalidRequestError,
        MissingApiKeyError,
        ProviderError,
        RateLimitError,
    )
    from genai_prices import Usage, calc_price
    HAS_ANY_LLM = True
except ImportError:
    HAS_ANY_LLM = False


# Substrings (lowercase) of model IDs known to reject system messages. Add
# entries here when a new family surfaces. The runtime fallback in
# process_item also catches unknown models on first failure, so this list is
# an optimisation (skip the failed first attempt) rather than a requirement.
_FOLD_SYSTEM_MODEL_HINTS: tuple[str, ...] = ("gemma",)

# Matches provider error messages that indicate the model rejected the
# system message. Liberal on purpose — false positives just trigger a single
# fold-retry which still produces a valid call.
_SYSTEM_REJECTION_PATTERNS = re.compile(
    r"system[_ ]?instruction|developer[_ ]?instruction|"
    r"does not support system|system message.*not (allowed|supported)",
    re.IGNORECASE,
)

from ..core.paths import get_path_manager
from ..tools.get_item_candidates import (
    get_item_candidates_prompt,
    get_zone_items,
)
from .llm_providers import PROVIDERS, get_model_context_window

# Get PathManager instance
paths = get_path_manager()

# Base system prompt components
_SYSTEM_PROMPT_BASE = """You are an expert World of Warcraft loot council assistant making fair loot distribution decisions.

Use the guild policy rules as the basis for all decisions.

IMPORTANT CONTEXT:
- "Item Priority: Mainspec" means this item is for the player's primary raid role.
- "Item Priority: Offspec" means this item is for an alternate role the player sometimes plays."""

_WISHLIST_POSITION_CONTEXT = """- "Wishlist Position" indicates how much the player wants this item (lower = more desired)."""

_ILVL_COMPARISON_CONTEXT = """- "Upgrade size" is measured in item level difference compared to currently equipped gear (higher = better upgrade)."""

_SESSION_TRACKING_CONTEXT = """- "Items assigned this session" tracks how many items a player has received in the current loot council session. If the number is higher than others, consider distributing loot to other players to ensure fairness."""

_CUSTOM_NOTE_CONTEXT = """- "Raider Note" contains notes about specific raiders relevant to loot decisions."""

_GUILD_PRIORITY_NOTE_CONTEXT = """- "Guild Priority Note" contains overarching guidelines on how this item should be distributed."""

_LAST_ITEM_RECEIVED_CONTEXT = """- "Last [Slot] received: Never" means the player has NEVER received an item in this slot — treat this as the longest possible wait, higher priority than any number of days."""

_SYSTEM_PROMPT_FOOTER = """
Be concise. Output only the requested format with a brief rationale."""


def get_system_prompt(
    include_session_tracking: bool = False,
    has_custom_notes: bool = False,
    has_wishlist_position: bool = True,
    has_ilvl_comparison: bool = False,
    has_guild_priority_note: bool = False,
    has_last_item_received: bool = False
) -> str:
    """Build the system prompt dynamically based on context.

    Args:
        include_session_tracking: Include session tracking context (raid zone mode)
        has_custom_notes: Include custom note context (only if candidates have notes)
        has_wishlist_position: Include wishlist position context (only if metric is enabled)
        has_ilvl_comparison: Include ilvl comparison context (only if metric is enabled)
        has_guild_priority_note: Include guild priority note context (only if item has a note)
        has_last_item_received: Include last item received context (only if metric is enabled)

    Returns:
        Complete system prompt string
    """
    parts = [_SYSTEM_PROMPT_BASE]

    if has_wishlist_position:
        parts.append(_WISHLIST_POSITION_CONTEXT)

    if has_ilvl_comparison:
        parts.append(_ILVL_COMPARISON_CONTEXT)

    if has_last_item_received:
        parts.append(_LAST_ITEM_RECEIVED_CONTEXT)

    if include_session_tracking:
        parts.append(_SESSION_TRACKING_CONTEXT)

    if has_custom_notes:
        parts.append(_CUSTOM_NOTE_CONTEXT)

    if has_guild_priority_note:
        parts.append(_GUILD_PRIORITY_NOTE_CONTEXT)

    parts.append(_SYSTEM_PROMPT_FOOTER)

    return "\n".join(parts)


@dataclass
class TokenUsage:
    """Container for API token usage information."""
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    max_tokens: Optional[int] = None
    estimated_cost: Optional[float] = None
    model_name: Optional[str] = None


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
    # Token usage information
    token_usage: Optional[TokenUsage] = None


class LootCouncilProcessor:
    """
    Processes loot council decisions via LLM APIs.

    This class handles:
    - Generating compact prompts for each item
    - Making API calls with rate limiting
    - Parsing LLM responses into structured decisions
    - Tracking session allocations to avoid funneling
    - Saving results to CSV

    Supports multiple providers via any-llm.
    """

    # Retry configuration for transient errors
    MAX_RETRIES = 3
    RETRY_DELAY = 2.0  # Initial delay in seconds (exponential backoff)

    def __init__(
        self,
        api_key: str = "",
        provider: str = "anthropic",
        model: str = "claude-sonnet-4-20250514",
        delay_seconds: float = 2.0,
        base_url: str = ""
    ):
        """
        Initialize the processor.

        Args:
            api_key: API key for hosted providers (ignored for local)
            provider: any-llm provider key (e.g. "anthropic", "openai", "ollama")
            model: Model to use (default: claude-sonnet-4-20250514)
            delay_seconds: Delay between API calls (default: 2.0 for rate limiting)
            base_url: Base URL for local providers (ignored for hosted)
        """
        if not HAS_ANY_LLM:
            raise ImportError(
                "any-llm package not installed. "
                "Install with: pip install any-llm-sdk"
            )

        self.provider = provider.lower()
        self.model = model
        self.delay_seconds = delay_seconds
        self.api_key = api_key
        self.base_url = base_url

        info = PROVIDERS.get(self.provider, {})
        self.kind = info.get("kind", "hosted")
        self.any_llm_provider = info.get("any_llm_provider", self.provider)
        env_var = info.get("env_var", "")

        # Session allocation tracking: {player_name: suggestion_1_count}
        self.session_allocations: Dict[str, int] = {}

        # Models discovered at runtime to reject the system message. Adds to
        # the static _FOLD_SYSTEM_MODEL_HINTS hints for this session only.
        self._fold_system_for: set = set()

        # For hosted providers, also export the conventional env var so any
        # code path inside any-llm or its provider SDKs that falls back to
        # the env var still works. Harmless duplication.
        if self.kind == "hosted" and env_var and api_key:
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

    def _should_fold_system(self) -> bool:
        """True when the current model is known to reject system messages."""
        model_lc = self.model.lower()
        if self.model in self._fold_system_for:
            return True
        return any(hint in model_lc for hint in _FOLD_SYSTEM_MODEL_HINTS)

    def _build_messages(self, system_prompt: str, user_prompt: str) -> List[Dict[str, str]]:
        """Build the chat-completion messages list, folding the system prompt
        into the user message for models that reject `system` role inputs."""
        if self._should_fold_system():
            return [{"role": "user", "content": f"{system_prompt}\n\n{user_prompt}"}]
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _extract_token_usage(self, response) -> TokenUsage:
        """
        Extract token usage information from an any-llm completion response.

        Returns:
            TokenUsage object with available information
        """
        token_usage = TokenUsage(model_name=self.model)

        try:
            # Extract usage from response (OpenAI-shaped ChatCompletion)
            if hasattr(response, 'usage') and response.usage:
                token_usage.prompt_tokens = getattr(response.usage, 'prompt_tokens', None)
                token_usage.completion_tokens = getattr(response.usage, 'completion_tokens', None)
                token_usage.total_tokens = getattr(response.usage, 'total_tokens', None)

            # Get max tokens for the model from the bundled catalogue
            token_usage.max_tokens = get_model_context_window(self.provider, self.model)

            # Calculate estimated cost via genai-prices (hosted providers only;
            # local runtimes have no public pricing).
            if self.kind == "hosted":
                try:
                    price = calc_price(
                        Usage(
                            input_tokens=token_usage.prompt_tokens or 0,
                            output_tokens=token_usage.completion_tokens or 0,
                        ),
                        model_ref=self.model,
                        provider_id=self.any_llm_provider,
                    )
                    token_usage.estimated_cost = float(price.total_price)
                except Exception:
                    # genai-prices may not have entries for every model
                    pass

        except Exception as e:
            logger.debug(f"Failed to extract token usage: {e}")

        return token_usage

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
                debug_response=None,
                token_usage=None
            )

        # Build system prompt based on mode and enabled metrics
        has_custom_notes = prompt_result.get("has_custom_notes", False)
        has_wishlist_position = prompt_result.get("has_wishlist_position", True)
        has_ilvl_comparison = prompt_result.get("has_ilvl_comparison", False)
        has_guild_priority_note = prompt_result.get("has_guild_priority_note", False)
        has_last_item_received = prompt_result.get("has_last_item_received", False)
        system_prompt = get_system_prompt(
            include_session_tracking=not single_item_mode,
            has_custom_notes=has_custom_notes,
            has_wishlist_position=has_wishlist_position,
            has_ilvl_comparison=has_ilvl_comparison,
            has_guild_priority_note=has_guild_priority_note,
            has_last_item_received=has_last_item_received
        )

        # Build the full prompt for debug display
        user_prompt = prompt_result["prompt"]
        full_prompt = f"[SYSTEM]\n{system_prompt}\n\n[USER]\n{user_prompt}"

        # Call API via any-llm with retry logic for transient errors
        response_text = None
        try:
            completion_kwargs = {
                "model": self.model,
                "provider": self.any_llm_provider,
                "messages": self._build_messages(system_prompt, user_prompt),
                "max_tokens": 500,
            }
            if self.kind == "hosted":
                if self.api_key:
                    completion_kwargs["api_key"] = self.api_key
            else:  # local
                completion_kwargs["api_base"] = (
                    self.base_url or PROVIDERS.get(self.provider, {}).get("base_url_default", "")
                )

            # Retry loop for transient errors (rate limits, provider/server issues)
            last_error = None
            for attempt in range(self.MAX_RETRIES + 1):
                try:
                    response = completion(**completion_kwargs)
                    break  # Success, exit retry loop
                except InvalidRequestError as e:
                    # If the provider rejected the request because the model
                    # doesn't accept system messages, fold the system prompt
                    # into the user turn and retry once immediately. The model
                    # is remembered for the rest of this session so subsequent
                    # calls skip straight to the folded shape.
                    if (
                        self.model not in self._fold_system_for
                        and _SYSTEM_REJECTION_PATTERNS.search(str(e))
                    ):
                        logger.info(
                            f"Model '{self.model}' rejected system message; "
                            f"folding into user turn and retrying. ({e})"
                        )
                        self._fold_system_for.add(self.model)
                        completion_kwargs["messages"] = self._build_messages(
                            system_prompt, user_prompt
                        )
                        response = completion(**completion_kwargs)
                        break
                    raise
                except (RateLimitError, ProviderError) as e:
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

            # Extract token usage information
            token_usage = self._extract_token_usage(response)

            # Parse response
            decision = self._parse_response(
                response_text,
                item_name,
                prompt_result["item_slot"],
                debug_prompt=full_prompt,
                debug_response=response_text,
                token_usage=token_usage
            )

            # Record Suggestion 1 allocation for session tracking
            if decision.success and decision.suggestion_1:
                self.record_allocation(decision.suggestion_1)

            return decision

        except RateLimitError as e:
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
                debug_response=response_text,
                token_usage=None
            )
        except (AuthenticationError, MissingApiKeyError) as e:
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
                debug_response=response_text,
                token_usage=None
            )
        except ProviderError as e:
            return LootDecision(
                item_name=item_name,
                item_slot=prompt_result.get("item_slot"),
                suggestion_1="",
                suggestion_2="",
                suggestion_3="",
                rationale="",
                success=False,
                error=f"Connection or server error to {self.provider}: {str(e)}",
                debug_prompt=full_prompt,
                debug_response=response_text,
                token_usage=None
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
                debug_response=response_text,
                token_usage=None
            )

    def _parse_response(
        self,
        response_text: str,
        item_name: str,
        item_slot: Optional[str],
        debug_prompt: Optional[str] = None,
        debug_response: Optional[str] = None,
        token_usage: Optional[TokenUsage] = None
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
            debug_response=debug_response,
            token_usage=token_usage
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
        Save loot suggestions to a CSV file.

        Args:
            decisions: List of LootDecision objects
            output_path: Optional custom output path

        Returns:
            Path to the saved CSV file
        """
        if output_path is None:
            output_path = paths.get_export_path("loot_suggestions.csv")

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
