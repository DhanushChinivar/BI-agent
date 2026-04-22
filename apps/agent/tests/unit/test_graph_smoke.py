"""Smoke test: the graph compiles and runs end-to-end with stub nodes."""
import pytest

from app.graph.builder import graph


@pytest.mark.asyncio
async def test_graph_runs_end_to_end():
    initial_state = {
        "messages": [{"role": "user", "content": "What were sales last quarter?"}],
        "user_id": "test-user",
        "conversation_id": "test-convo",
    }
    final = await graph.ainvoke(initial_state)
    assert "final_answer" in final
    assert final["final_answer"]
