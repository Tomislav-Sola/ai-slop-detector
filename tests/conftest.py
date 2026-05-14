import json
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--fake",
        action="store_true",
        default=False,
        help="Use recorded fixture data instead of live GitHub API calls.",
    )


@pytest.fixture
def use_fake(request: pytest.FixtureRequest) -> bool:
    return request.config.getoption("--fake")


@pytest.fixture
def papertriage_pr9() -> dict:
    with open(FIXTURES_DIR / "papertriage_pr9.json") as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def reset_budget():
    """Reset the budget ContextVar before each test to prevent cross-test leakage."""
    from pr_triage.budget import _budget_var
    _budget_var.set(None)
    yield
    _budget_var.set(None)
