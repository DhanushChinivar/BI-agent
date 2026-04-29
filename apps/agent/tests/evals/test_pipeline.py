"""
Pipeline evals: run full graph against golden Q&A pairs, assert key terms appear in the answer.

Run with:  uv run pytest tests/evals/ -v --timeout=120
Requires:  ANTHROPIC_API_KEY set in environment (real LLM calls are made).
"""
import json
from pathlib import Path

import pytest

from app.graph.builder import graph
from app.graph.state import AgentState

_GOLDEN_PAIRS = json.loads(
    (Path(__file__).parent / "golden_pairs.json").read_text()
)


def _keywords_found(answer: str, keywords: list[str]) -> list[str]:
    lower = answer.lower()
    return [kw for kw in keywords if kw.lower() not in lower]


@pytest.mark.asyncio
@pytest.mark.parametrize("pair", _GOLDEN_PAIRS, ids=[p["id"] for p in _GOLDEN_PAIRS])
async def test_pipeline_golden_pair(pair: dict) -> None:
    """Full pipeline run: answer must contain all expected keywords."""
    state: AgentState = {
        "messages": [{"role": "human", "content": pair["question"]}],
        "user_id": "eval-user",
        "conversation_id": f"eval-{pair['id']}",
    }
    final = await graph.ainvoke(state)

    answer: str = final.get("final_answer", "")
    assert answer, f"[{pair['id']}] Got empty answer for: {pair['question']}"

    missing = _keywords_found(answer, pair["expected_keywords"])
    assert not missing, (
        f"[{pair['id']}] Missing keywords {missing} in answer:\n{answer}"
    )

    if pair.get("expected_metrics_present"):
        analysis = final.get("analysis", {})
        metrics = analysis.get("metrics", {})
        assert metrics, (
            f"[{pair['id']}] Expected non-empty metrics but got: {metrics}"
        )
