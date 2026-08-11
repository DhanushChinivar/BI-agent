"""Tests for the SQL compute node.

Two things matter here: the arithmetic is exact over the *whole* table rather
than the sampled rows, and model-written SQL cannot reach outside the process.
"""
from unittest.mock import AsyncMock, patch

import duckdb
import pandas as pd
import pytest

from app.graph.nodes.compute import (
    _coerce_numeric,
    _connect,
    _is_single_select,
    _tabular,
    compute_node,
    will_compute,
)


def _state(rows, question_type="aggregation", question="what was total revenue?", **entry):
    return {
        "question_type": question_type,
        "retrieved_data": [{"source": "google_sheets", "resource": {"title": "Sales"},
                            "data": {"rows": rows}, **entry}],
        "messages": [{"role": "human", "content": question}],
        "user_id": "u1",
        "conversation_id": "c1",
    }


def _sql(query: str):
    return patch("app.graph.nodes.compute.chat", new_callable=AsyncMock, return_value=query)


# ── guards ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "sql,ok",
    [
        ("SELECT sum(rev) FROM t0", True),
        ("  select 1  ", True),
        ("WITH x AS (SELECT 1) SELECT * FROM x", True),
        ("SELECT 1;", True),                       # single trailing semicolon
        ("SELECT 1; DROP TABLE t0", False),        # chaining
        ("COPY t0 TO '/tmp/x.csv'", False),
        ("INSTALL httpfs", False),
        ("SET enable_external_access=true", False),
        ("DELETE FROM t0", False),
        ("", False),
    ],
)
def test_is_single_select(sql, ok):
    assert _is_single_select(sql) is ok


def test_generated_sql_cannot_touch_the_filesystem():
    """The guard above is defence in depth; this is the actual control.

    If DuckDB's external-access lock ever regresses, executing model-written SQL
    in-process stops being safe — so it is asserted rather than assumed.
    """
    con = _connect({"t0": pd.DataFrame([{"a": 1}])})
    try:
        for sql in (
            "SELECT * FROM read_csv('/etc/hosts')",
            "SELECT * FROM glob('/etc/*')",
            "COPY t0 TO '/tmp/should-not-exist.csv'",
            "INSTALL httpfs",
            "ATTACH '/tmp/x.db' AS x",
        ):
            # duckdb.Error, not bare Exception: a NameError or a typo in the
            # probe would otherwise read as "safely blocked".
            with pytest.raises(duckdb.Error):
                con.execute(sql).fetchall()
        assert con.execute("SELECT count(*) FROM t0").fetchone()[0] == 1
    finally:
        con.close()


# ── numeric coercion ──────────────────────────────────────────────────────────

def test_sheets_strings_become_numbers():
    """Sheets returns every cell as text, so sum() would concatenate."""
    frame = _coerce_numeric(pd.DataFrame([
        {"month": "October", "revenue": "120000", "spend": "$1,200"},
        {"month": "November", "revenue": "145000", "spend": "$2,400"},
    ]))
    assert frame["revenue"].sum() == 265000
    assert frame["spend"].sum() == 3600
    # Asserted as "not numeric" rather than "== object": pandas 3 represents
    # text as StringDtype, and an identity check on the dtype hides the bug
    # this test exists to catch.
    assert not pd.api.types.is_numeric_dtype(frame["month"])


def test_mostly_text_columns_are_left_alone():
    frame = _coerce_numeric(pd.DataFrame([
        {"note": "widget"}, {"note": "gadget"}, {"note": "3"},
    ]))
    assert not pd.api.types.is_numeric_dtype(frame["note"])


# ── which rows compute sees ───────────────────────────────────────────────────

def test_tabular_prefers_the_untrimmed_payload():
    """The analyst reads `data`; compute must read `full_data` when they differ."""
    entry = {"data": {"rows": [{"a": 1}]}, "full_data": {"rows": [{"a": 1}, {"a": 2}]}}
    assert len(_tabular(entry)) == 2
    assert _tabular({"data": {"rows": [{"a": 1}]}}) == [{"a": 1}]
    assert _tabular({"data": {"content": "notion page"}}) is None


@pytest.mark.asyncio
async def test_total_is_exact_over_rows_the_analyst_never_saw():
    """The point of the node: 300 rows in, a total over all 300 out.

    The analyst's own view is capped at _MAX_ROWS, so without this the answer
    would be a partial sum caveated with omitted_items.
    """
    full = [{"month": f"m{i}", "revenue": 100} for i in range(300)]
    state = _state(rows=full[:60], full_data={"rows": full}, omitted_items=240)

    with _sql("SELECT sum(revenue) AS total FROM t0"):
        result = await compute_node(state)

    assert result["computation"]["rows"] == [{"total": 30000}]
    assert result["computation"]["source_rows"] == 300


# ── when it engages ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_skips_non_computational_questions():
    with _sql("SELECT 1") as chat:
        result = await compute_node(_state([{"a": 1}], question_type="lookup"))
    assert result["computation"] is None
    chat.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_there_is_no_table():
    state = {
        "question_type": "aggregation",
        "retrieved_data": [{"source": "notion", "data": {"content": "a page"}}],
        "messages": [{"role": "human", "content": "total revenue?"}],
    }
    with _sql("SELECT 1") as chat:
        result = await compute_node(state)
    assert result["computation"] is None
    chat.assert_not_called()


@pytest.mark.asyncio
async def test_no_query_sentinel_is_not_an_error():
    with _sql("NO_QUERY"):
        result = await compute_node(_state([{"a": 1}]))
    assert result["computation"] is None


# ── failure is recoverable ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unsafe_sql_is_rejected_without_executing():
    with _sql("COPY t0 TO '/tmp/pwn.csv'"):
        result = await compute_node(_state([{"a": 1}]))
    assert "not a single SELECT" in result["computation"]["error"]


@pytest.mark.asyncio
async def test_broken_sql_degrades_instead_of_failing_the_turn():
    """The analyst still has the sampled rows — a bad query must not end the run."""
    with _sql("SELECT nonexistent_column FROM t0"):
        result = await compute_node(_state([{"a": 1}]))
    assert "error" in result["computation"]
    assert result["next_node"] == "analyst"


# ── both pipelines run it ─────────────────────────────────────────────────────

def test_compiled_graph_includes_compute():
    from app.graph.builder import graph

    assert "compute" in graph.get_graph().nodes


@pytest.mark.asyncio
async def test_streaming_path_runs_compute_too():
    """The SSE path calls nodes directly rather than the compiled graph.

    Two hand-maintained orderings of the same pipeline drift; adding a node to
    `builder.py` and forgetting `_stream_pipeline` would silently skip it for
    every real user, since the UI only ever uses the streaming endpoint.
    """
    from app.api import query as query_api
    from app.schemas.query import QueryRequest

    async def fake_stream(**_):
        yield "answer"

    with (
        patch.object(query_api, "planner_node", new_callable=AsyncMock, return_value={}),
        patch.object(query_api, "retriever_node", new_callable=AsyncMock, return_value={}),
        patch.object(query_api, "compute_node", new_callable=AsyncMock, return_value={}) as compute,
        patch.object(query_api, "analyst_node", new_callable=AsyncMock, return_value={}),
        patch.object(query_api, "_ensure_conversation", new_callable=AsyncMock),
        patch.object(query_api, "_persist_messages", new_callable=AsyncMock),
        patch.object(query_api, "_maybe_update_title", new_callable=AsyncMock),
        patch.object(query_api, "llm_stream", fake_stream),
    ):
        async for _ in query_api._stream_pipeline("u1", QueryRequest(message="total revenue?")):
            pass

    compute.assert_awaited_once()


@pytest.mark.asyncio
async def test_markdown_fenced_sql_is_unwrapped():
    with _sql("```sql\nSELECT sum(revenue) AS total FROM t0\n```"):
        result = await compute_node(_state([{"revenue": 5}, {"revenue": 7}]))
    assert result["computation"]["rows"] == [{"total": 12}]


# ── stage labelling ───────────────────────────────────────────────────────────

def _stage_state(question_type: str, entries: list[dict]) -> dict:
    return {"question_type": question_type, "retrieved_data": entries}


@pytest.mark.parametrize("question_type", ["aggregation", "trend", "comparison"])
def test_will_compute_for_arithmetic_questions_over_tables(question_type):
    state = _stage_state(question_type, [{"data": {"rows": [{"revenue": "100"}]}}])

    assert will_compute(state) is True


@pytest.mark.parametrize("question_type", ["lookup", "other", "summary"])
def test_will_not_compute_for_non_arithmetic_questions(question_type):
    state = _stage_state(question_type, [{"data": {"rows": [{"revenue": "100"}]}}])

    assert will_compute(state) is False


def test_will_not_compute_without_a_table():
    """A Gmail or Notion aggregation has no rows to run SQL over, so the stage
    caption must not promise exact totals it cannot produce."""
    state = _stage_state("aggregation", [{"data": {"passages": ["we grew a lot"]}}])

    assert will_compute(state) is False


def test_will_compute_sees_the_untrimmed_rows():
    """`full_data` is what compute actually reads when the sample was cut."""
    entry = {"data": {"rows": []}, "full_data": {"rows": [{"revenue": "1"}]}}

    assert will_compute(_stage_state("aggregation", [entry])) is True


@pytest.mark.asyncio
async def test_the_predicate_agrees_with_what_compute_node_does():
    """The point of exporting it: a second copy of this condition would drift,
    and the symptom would be a progress caption promising work never done."""
    no_table = _stage_state("aggregation", [{"data": {"passages": ["text"]}}])

    assert will_compute(no_table) is False
    # compute_node reaches the same conclusion without calling the LLM.
    assert (await compute_node(no_table))["computation"] is None
