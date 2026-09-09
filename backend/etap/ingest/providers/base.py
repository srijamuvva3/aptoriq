"""Provider interface for vision-capable models.

Deliberately spoken over plain HTTP rather than three vendor SDKs: the request shapes
are small, and this keeps the model swappable without a dependency per vendor.
"""

from __future__ import annotations

import abc
import base64
import json
import os
import re
import time
from typing import Any

import httpx

FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")


class ProviderError(RuntimeError):
    pass


class VisionProvider(abc.ABC):
    name: str = "base"
    default_model: str = ""
    env_key: str = ""

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 3,
    ):
        self.model = model or os.getenv("ETAP_VISION_MODEL") or self.default_model
        self.api_key = api_key or os.getenv(self.env_key, "")
        if not self.api_key:
            raise ProviderError(
                f"No API key for provider '{self.name}'. Set {self.env_key} in the environment or .env file."
            )
        self.timeout = timeout
        self.max_retries = max_retries

    @staticmethod
    def encode(image_png: bytes) -> str:
        return base64.b64encode(image_png).decode("ascii")

    @abc.abstractmethod
    def _request(self, instruction: str, image_png: bytes | None) -> dict[str, Any]:
        """Build the provider-specific request payload and endpoint call."""

    def extract(self, image_png: bytes, instruction: str) -> dict[str, Any]:
        return self._call(instruction, image_png)

    def complete(self, instruction: str) -> dict[str, Any]:
        """Text-only call, used for tagging where the stem is already extracted."""
        return self._call(instruction, None)

    def _call(self, instruction: str, image_png: bytes | None) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self._request(instruction, image_png)
            except (httpx.HTTPError, ProviderError, json.JSONDecodeError) as error:
                last_error = error
                if attempt == self.max_retries - 1:
                    break
                time.sleep(2.0 * (attempt + 1))
        raise ProviderError(f"{self.name} call failed: {last_error}") from last_error

    @staticmethod
    def parse_json(text: str) -> dict[str, Any]:
        cleaned = FENCE.sub("", text.strip())
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Models occasionally wrap the object in commentary.
            start, end = cleaned.find("{"), cleaned.rfind("}")
            if start == -1 or end <= start:
                raise
            return json.loads(cleaned[start : end + 1])

    def post(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            raise ProviderError(f"HTTP {response.status_code}: {response.text[:400]}")
        return response.json()
