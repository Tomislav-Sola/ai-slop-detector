from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field


class BudgetExceeded(Exception):
    """Raised when a run exhausts its token budget."""


@dataclass
class BudgetContext:
    max_tokens: int
    _used: int = field(default=0, init=False, repr=False)

    def consume(self, tokens: int) -> None:
        self._used += tokens
        if self._used > self.max_tokens:
            raise BudgetExceeded(
                f"Token budget exhausted: used {self._used}, limit {self.max_tokens}"
            )

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        return max(0, self.max_tokens - self._used)


_budget_var: ContextVar[BudgetContext | None] = ContextVar("budget", default=None)


def set_budget(max_tokens: int) -> BudgetContext:
    ctx = BudgetContext(max_tokens=max_tokens)
    _budget_var.set(ctx)
    return ctx


def get_budget() -> BudgetContext | None:
    return _budget_var.get()


def consume(tokens: int) -> None:
    """Deduct tokens from the current run's budget; no-op if no budget is set."""
    ctx = _budget_var.get()
    if ctx is not None:
        ctx.consume(tokens)
