"""Concrete vision providers."""

from __future__ import annotations

import os
from typing import Any

from .base import ProviderError, VisionProvider


class OpenAIProvider(VisionProvider):
    name = "openai"
    default_model = "gpt-4o"
    env_key = "OPENAI_API_KEY"
    endpoint = "https://api.openai.com/v1/chat/completions"

    def _request(self, instruction: str, image_png: bytes | None) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "text", "text": instruction}]
        if image_png is not None:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{self.encode(image_png)}",
                        "detail": "high",
                    },
                }
            )
        payload = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": content}],
        }
        data = self.post(
            self.endpoint,
            {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            payload,
        )
        try:
            return self.parse_json(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError) as error:
            raise ProviderError(f"Unexpected OpenAI response shape: {data}") from error


class AnthropicProvider(VisionProvider):
    name = "anthropic"
    default_model = "claude-sonnet-4-5"
    env_key = "ANTHROPIC_API_KEY"
    endpoint = "https://api.anthropic.com/v1/messages"

    def _request(self, instruction: str, image_png: bytes | None) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        if image_png is not None:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": self.encode(image_png),
                    },
                }
            )
        content.append({"type": "text", "text": instruction})
        payload = {
            "model": self.model,
            "max_tokens": 8192,
            "temperature": 0,
            "messages": [{"role": "user", "content": content}],
        }
        data = self.post(
            self.endpoint,
            {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            payload,
        )
        try:
            return self.parse_json(data["content"][0]["text"])
        except (KeyError, IndexError) as error:
            raise ProviderError(f"Unexpected Anthropic response shape: {data}") from error


class GeminiProvider(VisionProvider):
    name = "gemini"
    default_model = "gemini-2.0-flash"
    env_key = "GEMINI_API_KEY"

    def _request(self, instruction: str, image_png: bytes | None) -> dict[str, Any]:
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent?key={self.api_key}"
        )
        parts: list[dict[str, Any]] = [{"text": instruction}]
        if image_png is not None:
            parts.append(
                {"inline_data": {"mime_type": "image/png", "data": self.encode(image_png)}}
            )
        payload = {
            "contents": [{"parts": parts}],
            "generationConfig": {"temperature": 0, "response_mime_type": "application/json"},
        }
        data = self.post(url, {"Content-Type": "application/json"}, payload)
        try:
            return self.parse_json(data["candidates"][0]["content"]["parts"][0]["text"])
        except (KeyError, IndexError) as error:
            raise ProviderError(f"Unexpected Gemini response shape: {data}") from error


PROVIDERS: dict[str, type[VisionProvider]] = {
    OpenAIProvider.name: OpenAIProvider,
    AnthropicProvider.name: AnthropicProvider,
    GeminiProvider.name: GeminiProvider,
}


def build_provider(name: str | None = None, model: str | None = None) -> VisionProvider:
    key = (name or "").lower()
    if key:
        if key not in PROVIDERS:
            raise ProviderError(
                f"Unknown provider '{key}'. Available: {', '.join(sorted(PROVIDERS))}."
            )
        return PROVIDERS[key](model=model)

    for provider in PROVIDERS.values():
        if os.getenv(provider.env_key):
            return provider(model=model)

    raise ProviderError(
        "No vision provider configured. Set one of "
        + ", ".join(provider.env_key for provider in PROVIDERS.values())
        + " in your .env file."
    )
