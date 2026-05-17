from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.graph import END, StateGraph

from ai_slop_detector import budget as _budget
from ai_slop_detector.budget import BudgetExceeded
from ai_slop_detector.graph.nodes import (
    aggregate_node,
    architecture_critic_node,
    classify_size_node,
    ingest_pr_node,
    retrieve_context_node,
    slop_signals_critic_node,
)
from ai_slop_detector.state import TriageState

if TYPE_CHECKING:
    from ai_slop_detector.claude_client import ClaudeClient
    from ai_slop_detector.rag import RAGIndex

_DEFAULT_MAX_TOKENS = 50_000


def build_graph(claude: ClaudeClient, rag: RAGIndex, *, critic_model: str | None = None):
    """Assemble and compile the binary triage StateGraph.

    Topology:
      ingest_pr
        → classify_size
        → retrieve_context
          → architecture_critic ─┐
          → slop_signals_critic ─┴→ aggregate → END

    Two critic nodes run in parallel within one LangGraph superstep.
    critic_outputs uses operator.add so each critic's result is appended.
    The aggregator produces a binary verdict (approve / reject = not-slop / slop).

    critic_model overrides each critic's production default when set (e.g. for
    eval runs: pass MODEL_HAIKU to run cheaply).
    """
    graph = StateGraph(TriageState)

    graph.add_node("ingest_pr", ingest_pr_node)
    graph.add_node("classify_size", lambda s: classify_size_node(s, claude))
    graph.add_node("retrieve_context", lambda s: retrieve_context_node(s, rag))
    graph.add_node("architecture_critic", lambda s: architecture_critic_node(s, claude, model=critic_model))
    graph.add_node("slop_signals_critic", lambda s: slop_signals_critic_node(s, claude, model=critic_model))
    graph.add_node("aggregate", aggregate_node)

    graph.set_entry_point("ingest_pr")
    graph.add_edge("ingest_pr", "classify_size")

    graph.add_edge("classify_size", "retrieve_context")

    # Fan-out: two critics run in parallel after context retrieval.
    graph.add_edge("retrieve_context", "architecture_critic")
    graph.add_edge("retrieve_context", "slop_signals_critic")

    # Fan-in: aggregate waits for both critics.
    graph.add_edge("architecture_critic", "aggregate")
    graph.add_edge("slop_signals_critic", "aggregate")

    graph.add_edge("aggregate", END)

    return graph.compile()


def run_pipeline(
    state: TriageState,
    claude: ClaudeClient,
    rag: RAGIndex,
    *,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    critic_model: str | None = None,
) -> TriageState:
    """Run the full triage pipeline and return the updated TriageState."""
    _check_budget(state, max_tokens)
    _budget.set_budget(max_tokens)

    compiled = build_graph(claude, rag, critic_model=critic_model)
    result = compiled.invoke(state)

    if isinstance(result, dict):
        return TriageState(**result)
    return result


def _check_budget(state: TriageState, max_tokens: int) -> None:
    """Estimate token usage and refuse to start if the cap would be blown.

    Two critics run in parallel; each consumes roughly the same budget.
    Multiply the per-critic estimate by 2.

    Estimate formula:
      (raw_diff + contributing_md + agents_md + merged_titles) / 4  × 2 critics
      + 8 retrieved chunks × ~1000 chars / 4  × 2 (context sent to each critic)
      + 6000 flat overhead  (system prompts, classify call, response budgets)
    """
    text_chars = (
        len(state.raw_diff or "")
        + len(state.contributing_md or "")
        + len(state.agents_md or "")
        + sum(len(t) for t in state.recent_merged_titles)
    )
    per_critic_tokens = text_chars // 4 + (8 * 1000) // 4
    estimated = per_critic_tokens * 2 + 6_000

    if estimated > max_tokens:
        raise BudgetExceeded(
            f"Pre-flight estimate of ~{estimated:,} tokens exceeds the "
            f"{max_tokens:,}-token cap. Use --max-tokens to raise the limit."
        )
