from __future__ import annotations

from typing import Any

from pr_triage import budget as _budget


class ClaudeClient:
    """Single gateway for all Claude API calls.

    Phase 1 stub: complete() raises NotImplementedError.
    Phase 2 will instantiate Anthropic() here — never elsewhere.
    """

    def __init__(self, api_key: str, default_model: str = "claude-sonnet-4-6") -> None:
        self._api_key = api_key
        self.default_model = default_model

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        max_tokens: int = 4096,
        system: str | None = None,
    ) -> str:
        """Send a chat completion request and return the text response.

        Deducts input + output tokens from the current run budget before returning.
        Raises BudgetExceeded if the budget is exhausted.
        Raises NotImplementedError in Phase 1.
        """
        raise NotImplementedError(
            "ClaudeClient.complete() is a Phase 1 stub. "
            "LLM calls are not wired until Phase 2."
        )
