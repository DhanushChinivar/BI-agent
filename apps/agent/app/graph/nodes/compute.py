"""Compute node: does the arithmetic in SQL instead of in a prompt.

`analyst_node` receives at most `_MAX_ROWS` rows and derives totals by reading
JSON, so a sum over a larger sheet is both truncated *and* model-derived. This
node loads the **untrimmed** table into an in-memory DuckDB, asks Claude for a
single SELECT, and executes it. The analyst then reports a figure that was
computed rather than one it inferred.

It only engages for question types whose answer depends on every row
(aggregation / trend / comparison) and only when tabular data is present —
a Gmail or Notion question never reaches it.
"""
import re
import time

import pandas as pd
import structlog

from app.graph.message_utils import last_human_message
from app.graph.state import AgentState
from app.llm import chat

log = structlog.get_logger(__name__)

_COMPUTED_TYPES = {"aggregation", "trend", "comparison"}
_MAX_TABLES = 3
_MAX_INPUT_ROWS = 50_000   # bounds memory for one request
_MAX_RESULT_ROWS = 100     # what we hand back to the analyst
_SAMPLE_ROWS = 3           # rows of each table shown to the query writer

# Values arrive from the Sheets API as strings, often formatted for humans.
_NUMERIC_NOISE = re.compile(r"[,$€£%\s]")
_NUMERIC_SHARE = 0.8       # a column this numeric is treated as numeric

_STARTS_A_QUERY = re.compile(r"^\s*(with|select)\b", re.IGNORECASE)
_NO_QUERY = "NO_QUERY"

_SYSTEM = f"""You are the query-writing module of a Business Intelligence agent.
Given table schemas and a question, write ONE DuckDB SQL query that answers it.

Rules:
- Respond with SQL only. No prose, no explanation, no markdown fences.
- Exactly one statement, a SELECT (optionally preceded by WITH). Never INSERT,
  UPDATE, DELETE, COPY, ATTACH, INSTALL, or SET.
- Use only the tables and columns listed. Quote identifiers in double quotes
  when they contain spaces, punctuation, or capitals.
- Aggregate. The caller wants a computed answer, not raw rows echoed back.
- Return at most {_MAX_RESULT_ROWS} rows.
- Numeric-looking columns have already been converted to numbers.
- If the question cannot be answered from these tables, reply with exactly
  {_NO_QUERY} and nothing else."""


def _tabular(entry: dict) -> list[dict] | None:
    """The untrimmed rows on a retrieved entry, if it holds a table.

    The retriever stores the pre-trim payload under `full_data` when it had to
    cut rows; otherwise `data` is already complete.
    """
    payload = entry.get("full_data") or entry.get("data")
    if not isinstance(payload, dict):
        return None
    rows = payload.get("rows")
    if isinstance(rows, list) and rows and all(isinstance(r, dict) for r in rows):
        return rows[:_MAX_INPUT_ROWS]
    return None


def _coerce_numeric(frame: pd.DataFrame) -> pd.DataFrame:
    """Convert mostly-numeric text columns to numbers.

    Sheets returns every cell as a string, so `sum(revenue)` over the raw frame
    would concatenate rather than add — and "$1,200" would not parse at all.
    Columns that are only partly numeric are left as text.
    """
    for col in frame.columns:
        # Test what a column *is*, not what dtype represents it: pandas 3 stores
        # text as StringDtype rather than object, so an `== object` check here
        # silently skipped every Sheets column.
        if pd.api.types.is_numeric_dtype(frame[col]) or pd.api.types.is_datetime64_any_dtype(
            frame[col]
        ):
            continue
        cleaned = frame[col].astype(str).str.replace(_NUMERIC_NOISE, "", regex=True)
        converted = pd.to_numeric(cleaned, errors="coerce")
        if converted.notna().any() and converted.notna().mean() >= _NUMERIC_SHARE:
            frame[col] = converted
    return frame


def _describe(name: str, title: str, frame: pd.DataFrame) -> str:
    columns = ", ".join(f'"{c}" {frame[c].dtype}' for c in frame.columns)
    sample = frame.head(_SAMPLE_ROWS).to_dict("records")
    return f"Table {name} — {title} ({len(frame)} rows)\n  columns: {columns}\n  sample: {sample}"


def _is_single_select(sql: str) -> bool:
    """Reject anything that is not one read-only statement.

    Defence in depth. The real control is DuckDB's `enable_external_access`,
    which blocks file, network, and extension access outright; this stops
    statement chaining and obvious non-SELECTs before they reach the engine.
    """
    stripped = sql.strip().rstrip(";").strip()
    return bool(stripped) and _STARTS_A_QUERY.match(stripped) is not None and ";" not in stripped


def _connect(tables: dict[str, pd.DataFrame]):
    """An in-memory DuckDB holding `tables`, with the outside world switched off."""
    import duckdb

    con = duckdb.connect()
    for name, frame in tables.items():
        con.register(name, frame)
    # Blocks read_csv/glob/COPY/ATTACH/INSTALL and any HTTP access. Verified by
    # tests/unit/test_compute.py::test_generated_sql_cannot_touch_the_filesystem.
    con.execute("SET enable_external_access=false")
    return con


def will_compute(state: AgentState) -> bool:
    """Whether `compute_node` will do real work for this state.

    Exported so the SSE layer can label the stage honestly without restating
    the condition. A second copy of this predicate would drift the first time
    either side changed, and the symptom would be a progress caption promising
    work that never happens.
    """
    if state.get("question_type", "other") not in _COMPUTED_TYPES:
        return False
    return any(_tabular(entry) is not None for entry in state.get("retrieved_data", []))


async def compute_node(state: AgentState) -> dict:
    t0 = time.monotonic()
    bound = log.bind(
        node="compute",
        conversation_id=state.get("conversation_id"),
        user_id=state.get("user_id"),
    )

    question_type = state.get("question_type", "other")
    retrieved = state.get("retrieved_data", [])

    if question_type not in _COMPUTED_TYPES:
        return {"computation": None, "next_node": "analyst"}

    tables: dict[str, pd.DataFrame] = {}
    descriptions: list[str] = []
    for entry in retrieved:
        if len(tables) >= _MAX_TABLES:
            break
        rows = _tabular(entry)
        if rows is None:
            continue
        name = f"t{len(tables)}"
        frame = _coerce_numeric(pd.DataFrame(rows))
        tables[name] = frame
        title = (entry.get("resource") or {}).get("title") or entry.get("source") or name
        descriptions.append(_describe(name, title, frame))

    if not tables:
        return {"computation": None, "next_node": "analyst"}

    question = last_human_message(state.get("messages", [])) or ""
    prompt = "\n\n".join(descriptions) + f"\n\nQuestion: {question}"

    try:
        sql = (await chat(messages=[{"role": "user", "content": prompt}], system=_SYSTEM,
                          max_tokens=512)).strip()
    except Exception as exc:
        bound.warning("sql_generation_failed", error=str(exc))
        return {"computation": {"error": f"query generation failed: {exc}"}, "next_node": "analyst"}

    if sql.startswith("```"):
        sql = sql.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    if sql == _NO_QUERY:
        bound.info("no_query", question_type=question_type)
        return {"computation": None, "next_node": "analyst"}

    if not _is_single_select(sql):
        bound.warning("rejected_sql", sql=sql[:200])
        return {
            "computation": {"error": "generated query was not a single SELECT", "sql": sql},
            "next_node": "analyst",
        }

    try:
        con = _connect(tables)
        try:
            result = con.execute(sql).fetch_df()
        finally:
            con.close()
    except Exception as exc:
        # A bad query is recoverable — the analyst still has the sampled rows.
        bound.warning("sql_failed", error=str(exc), sql=sql[:200])
        return {"computation": {"error": str(exc), "sql": sql}, "next_node": "analyst"}

    truncated = len(result) > _MAX_RESULT_ROWS
    rows = result.head(_MAX_RESULT_ROWS).to_dict("records")

    bound.info(
        "complete",
        duration_ms=round((time.monotonic() - t0) * 1000),
        tables=len(tables),
        input_rows=sum(len(f) for f in tables.values()),
        result_rows=len(rows),
    )
    return {
        "computation": {
            "sql": sql,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "source_rows": sum(len(f) for f in tables.values()),
        },
        "next_node": "analyst",
    }
