from __future__ import annotations

import time
from typing import Any

import anthropic
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from pr_triage import budget as _budget

# Models used by the pipeline
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_HAIKU = "claude-haiku-4-5-20251001"

# Approximate prices in USD per token (not per million).
# These are best-effort estimates — verify against https://www.anthropic.com/pricing.
_PRICE_PER_TOKEN: dict[str, dict[str, float]] = {
    MODEL_HAIKU:  {"input": 0.80e-6, "output": 4.00e-6},
    MODEL_SONNET: {"input": 3.00e-6, "output": 15.00e-6},
    "claude-opus-4-7": {"input": 15.00e-6, "output": 75.00e-6},
}
_PRICE_FALLBACK = {"input": 3.00e-6, "output": 15.00e-6}  # sonnet-class default

_RETRYABLE = (
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.APIConnectionError,
)


class FakeResponsesExhausted(Exception):
    """Raised in fake mode when complete() is called more times than there are canned responses."""


class ClaudeClient:
    """Single gateway for all Claude API calls.

    In live mode instantiates anthropic.Anthropic once and routes every call
    through complete().  In fake mode (fake=True) it consumes pre-loaded
    responses sequentially so tests and --fake CLI runs never touch the network.
    """

    def __init__(
        self,
        api_key: str,
        default_model: str = MODEL_SONNET,
        *,
        fake: bool = False,
        fake_responses: list[str] | None = None,
    ) -> None:
        self._api_key = api_key
        self.default_model = default_model
        self._fake = fake
        self._fake_queue: list[str] = list(fake_responses or [])
        self._fake_index = 0
        self._total_cost_usd: float = 0.0

        if not fake:
            self._client = anthropic.Anthropic(api_key=api_key)

    @property
    def total_cost_usd(self) -> float:
        return self._total_cost_usd

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        system: str | None = None,
    ) -> str:
        """Send a chat completion and return the text response.

        Deducts input + output tokens from the active run budget.
        In fake mode returns the next pre-loaded response without any network call.
        """
        if self._fake:
            return self._fake_complete()

        return self._live_complete(
            messages=messages,
            model=model or self.default_model,
            max_tokens=max_tokens,
            system=system,
        )

    def _fake_complete(self) -> str:
        if self._fake_index >= len(self._fake_queue):
            raise FakeResponsesExhausted(
                f"Fake mode: complete() called {self._fake_index + 1} times "
                f"but only {len(self._fake_queue)} responses were loaded."
            )
        response = self._fake_queue[self._fake_index]
        self._fake_index += 1
        # Charge a nominal token count so budget tracking still works in tests.
        _budget.consume(len(response) // 4 + 100)
        return response

    def _live_complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        max_tokens: int,
        system: str | None,
    ) -> str:
        @retry(
            retry=retry_if_exception_type(_RETRYABLE),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            reraise=True,
            before_sleep=_respect_retry_after,
        )
        def _call() -> anthropic.types.Message:
            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": max_tokens,
                "messages": messages,
            }
            if system:
                kwargs["system"] = system
            return self._client.messages.create(**kwargs)

        msg = _call()
        input_tokens = msg.usage.input_tokens
        output_tokens = msg.usage.output_tokens
        _budget.consume(input_tokens + output_tokens)
        pricing = _PRICE_PER_TOKEN.get(model, _PRICE_FALLBACK)
        self._total_cost_usd += (
            input_tokens * pricing["input"] + output_tokens * pricing["output"]
        )
        return msg.content[0].text


def _respect_retry_after(retry_state: Any) -> None:
    """Before sleeping, honour the Retry-After header when present."""
    exc = retry_state.outcome.exception()
    if isinstance(exc, anthropic.RateLimitError):
        headers = getattr(exc, "response", None) and exc.response.headers
        if headers:
            after = headers.get("retry-after") or headers.get("Retry-After")
            if after:
                try:
                    time.sleep(float(after))
                except (ValueError, TypeError):
                    pass
