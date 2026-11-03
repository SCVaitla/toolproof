"""Model registry. Specs look like ``anthropic:claude-sonnet-4-5``, ``openai:gpt-5``,
``gemini:gemini-2.5-flash`` or ``mock:golden``. Model IDs change often; pass whatever your
provider currently serves."""

from __future__ import annotations

from typing import Any

from toolproof.evals.models.anthropic import AnthropicAdapter
from toolproof.evals.models.base import (
    AssistantTurn,
    Message,
    ModelAdapter,
    ModelError,
    ToolCall,
    ToolResultMsg,
    ToolSpec,
    Usage,
)
from toolproof.evals.models.gemini import GeminiAdapter, sanitize_schema
from toolproof.evals.models.openai import OpenAIAdapter
from toolproof.evals.models.scripted import ScriptedModel

PROVIDERS = {
    "anthropic": AnthropicAdapter,
    "claude": AnthropicAdapter,
    "openai": OpenAIAdapter,
    "gpt": OpenAIAdapter,
    "gemini": GeminiAdapter,
    "google": GeminiAdapter,
}


def make_model(spec: str, trajectories: dict[str, Any] | None = None, **kwargs: Any) -> ModelAdapter:
    provider, sep, model = spec.partition(":")
    if not sep or not model:
        raise ValueError(f"model spec must look like 'provider:model', got {spec!r}")
    if provider == "mock":
        return ScriptedModel(model, trajectories or {})
    cls = PROVIDERS.get(provider.lower())
    if cls is None:
        raise ValueError(f"unknown provider {provider!r}; choose from {sorted({*PROVIDERS, 'mock'})}")
    return cls(model, **kwargs)


__all__ = [
    "AnthropicAdapter",
    "AssistantTurn",
    "GeminiAdapter",
    "Message",
    "ModelAdapter",
    "ModelError",
    "OpenAIAdapter",
    "ScriptedModel",
    "ToolCall",
    "ToolResultMsg",
    "ToolSpec",
    "Usage",
    "make_model",
    "sanitize_schema",
]
