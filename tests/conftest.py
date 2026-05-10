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
def papertriage_pr1() -> dict:
    with open(FIXTURES_DIR / "papertriage_pr1.json") as f:
        return json.load(f)
