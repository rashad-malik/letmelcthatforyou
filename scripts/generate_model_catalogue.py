"""
Generate data/model_catalogue.json from genai_prices.

The catalogue powers the GUI's model dropdown for hosted providers without
needing a network call or a user-supplied API key. Each entry stores the
model's context window when known.

Local providers (Ollama, LM Studio, etc.) are not catalogued here — their
models are queried at runtime against the user's local server.

Providers absent from genai_prices (huggingface, sambanova, nebius, zai)
get empty entries; the GUI falls back to the "Test Connection" flow
(any_llm.list_models) for those.

Usage:
    uv run python scripts/generate_model_catalogue.py
"""

from __future__ import annotations

import json
from pathlib import Path

from genai_prices.data import providers as genai_providers


# Map our PROVIDERS keys → genai_prices provider IDs. Keys missing from
# this map (or mapped to None) get an empty catalogue entry.
PROVIDER_MAP: dict[str, str | None] = {
    "anthropic": "anthropic",
    "openai": "openai",
    "gemini": "google",
    "mistral": "mistral",
    "groq": "groq",
    "xai": "x-ai",
    "cohere": "cohere",
    "together": "together",
    "deepseek": "deepseek",
    "openrouter": "openrouter",
    "perplexity": "perplexity",
    "fireworks": "fireworks",
    "cerebras": "cerebras",
    "moonshot": "moonshotai",
    # Not currently in genai_prices — empty entries; GUI uses Test Connection.
    "sambanova": None,
    "nebius": None,
    "huggingface": None,
    "zai": None,
}


def build_catalogue() -> dict[str, dict[str, dict[str, int | None]]]:
    by_id = {p.id: p for p in genai_providers}
    catalogue: dict[str, dict[str, dict[str, int | None]]] = {}

    for our_key, genai_key in PROVIDER_MAP.items():
        if genai_key is None:
            catalogue[our_key] = {}
            continue

        provider = by_id.get(genai_key)
        if provider is None:
            print(f"WARN: provider '{genai_key}' (mapped from '{our_key}') not in genai_prices")
            catalogue[our_key] = {}
            continue

        models: dict[str, dict[str, int | None]] = {}
        for model in provider.models:
            if getattr(model, "deprecated", False):
                continue
            ctx = getattr(model, "context_window", None)
            models[model.id] = {"context_window": int(ctx) if ctx else None}

        catalogue[our_key] = dict(sorted(models.items()))
        print(f"  {our_key}: {len(models)} models")

    return catalogue


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    out_path = repo_root / "data" / "model_catalogue.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("Building model catalogue from genai_prices...")
    catalogue = build_catalogue()

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(catalogue, f, indent=2, sort_keys=True)

    total_models = sum(len(v) for v in catalogue.values())
    print(f"\nWrote {total_models} models across {len(catalogue)} providers to {out_path}")


if __name__ == "__main__":
    main()
