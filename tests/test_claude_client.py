from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ai_slop_detector import budget as _budget
from ai_slop_detector.claude_client import ClaudeClient, FakeResponsesExhausted

_MESSAGES = [{"role": "user", "content": "Hello"}]


# ------------------------------------------------------------------
# Fake mode — sequential replay
# ------------------------------------------------------------------

def test_fake_returns_first_response():
    client = ClaudeClient(api_key="x", fake=True, fake_responses=["alpha", "beta"])
    assert client.complete(_MESSAGES) == "alpha"


def test_fake_returns_second_response():
    client = ClaudeClient(api_key="x", fake=True, fake_responses=["alpha", "beta"])
    client.complete(_MESSAGES)
    assert client.complete(_MESSAGES) == "beta"


def test_fake_exhausted_raises():
    client = ClaudeClient(api_key="x", fake=True, fake_responses=["only"])
    client.complete(_MESSAGES)
    with pytest.raises(FakeResponsesExhausted):
        client.complete(_MESSAGES)


def test_fake_mode_deducts_budget():
    _budget.set_budget(10_000)
    client = ClaudeClient(api_key="x", fake=True, fake_responses=["hello world"])
    client.complete(_MESSAGES)
    ctx = _budget.get_budget()
    assert ctx is not None
    assert ctx.used > 0


def test_fake_mode_empty_responses_raises_immediately():
    client = ClaudeClient(api_key="x", fake=True, fake_responses=[])
    with pytest.raises(FakeResponsesExhausted):
        client.complete(_MESSAGES)


def test_model_routing_kwarg_passes_through():
    client = ClaudeClient(api_key="x", fake=True, fake_responses=["ok"])
    result = client.complete(_MESSAGES, model="claude-haiku-4-5-20251001", max_tokens=16)
    assert result == "ok"


# ------------------------------------------------------------------
# Live mode — mocked Anthropic client, no network calls
# ------------------------------------------------------------------

def _mock_message(text: str, input_tokens: int = 100, output_tokens: int = 50) -> MagicMock:
    msg = MagicMock()
    msg.usage.input_tokens = input_tokens
    msg.usage.output_tokens = output_tokens
    msg.content = [MagicMock(text=text)]
    return msg


def test_live_complete_returns_text():
    _budget.set_budget(10_000)
    with patch("ai_slop_detector.claude_client.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = _mock_message("small")
        client = ClaudeClient(api_key="sk-ant-fake")
        result = client.complete(_MESSAGES)
    assert result == "small"


def test_live_complete_tracks_tokens_in_budget():
    _budget.set_budget(10_000)
    with patch("ai_slop_detector.claude_client.anthropic.Anthropic") as mock_cls:
        mock_cls.return_value.messages.create.return_value = _mock_message("ok", input_tokens=80, output_tokens=40)
        client = ClaudeClient(api_key="sk-ant-fake")
        client.complete(_MESSAGES)
    assert _budget.get_budget().used == 120


def test_live_complete_passes_system_prompt():
    _budget.set_budget(10_000)
    with patch("ai_slop_detector.claude_client.anthropic.Anthropic") as mock_cls:
        mock_api = mock_cls.return_value
        mock_api.messages.create.return_value = _mock_message("ok")
        client = ClaudeClient(api_key="sk-ant-fake")
        client.complete(_MESSAGES, system="You are a critic.")
    call_kwargs = mock_api.messages.create.call_args.kwargs
    assert call_kwargs["system"] == "You are a critic."


def test_live_complete_omits_system_when_none():
    _budget.set_budget(10_000)
    with patch("ai_slop_detector.claude_client.anthropic.Anthropic") as mock_cls:
        mock_api = mock_cls.return_value
        mock_api.messages.create.return_value = _mock_message("ok")
        client = ClaudeClient(api_key="sk-ant-fake")
        client.complete(_MESSAGES)
    call_kwargs = mock_api.messages.create.call_args.kwargs
    assert "system" not in call_kwargs


def test_live_complete_uses_default_model():
    _budget.set_budget(10_000)
    with patch("ai_slop_detector.claude_client.anthropic.Anthropic") as mock_cls:
        mock_api = mock_cls.return_value
        mock_api.messages.create.return_value = _mock_message("ok")
        client = ClaudeClient(api_key="sk-ant-fake")
        client.complete(_MESSAGES)
    call_kwargs = mock_api.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-6"


def test_live_complete_respects_model_override():
    _budget.set_budget(10_000)
    with patch("ai_slop_detector.claude_client.anthropic.Anthropic") as mock_cls:
        mock_api = mock_cls.return_value
        mock_api.messages.create.return_value = _mock_message("ok")
        client = ClaudeClient(api_key="sk-ant-fake")
        client.complete(_MESSAGES, model="claude-haiku-4-5-20251001")
    call_kwargs = mock_api.messages.create.call_args.kwargs
    assert call_kwargs["model"] == "claude-haiku-4-5-20251001"


def test_live_complete_construction_does_not_raise():
    with patch("ai_slop_detector.claude_client.anthropic.Anthropic"):
        client = ClaudeClient(api_key="sk-ant-fake")
    assert client.default_model == "claude-sonnet-4-6"
