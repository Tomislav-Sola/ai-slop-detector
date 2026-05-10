import pytest

from pr_triage.budget import BudgetContext, BudgetExceeded, consume, get_budget, set_budget


def test_budget_context_consume_within_limit():
    ctx = BudgetContext(max_tokens=100)
    ctx.consume(40)
    ctx.consume(50)
    assert ctx.used == 90
    assert ctx.remaining == 10


def test_budget_context_exceeds_limit():
    ctx = BudgetContext(max_tokens=50)
    with pytest.raises(BudgetExceeded, match="budget exhausted"):
        ctx.consume(51)


def test_budget_context_exactly_at_limit():
    ctx = BudgetContext(max_tokens=100)
    ctx.consume(100)
    assert ctx.remaining == 0


def test_set_and_get_budget():
    ctx = set_budget(200)
    assert get_budget() is ctx
    assert ctx.max_tokens == 200


def test_consume_helper_deducts_from_context_var():
    set_budget(100)
    consume(30)
    assert get_budget().used == 30


def test_consume_noop_when_no_budget_set():
    # ContextVar isolation: reset by setting to None
    from pr_triage.budget import _budget_var
    _budget_var.set(None)
    consume(9999)  # should not raise


def test_consume_raises_when_over_budget():
    set_budget(10)
    with pytest.raises(BudgetExceeded):
        consume(11)
