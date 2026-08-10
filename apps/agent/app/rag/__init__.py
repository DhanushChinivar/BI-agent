"""Retrieval-augmented generation: chunking, embedding, indexing, vector search.

Scope is deliberate. Unstructured sources — Gmail threads, Notion pages — go
through this layer, because a question about them is a semantic lookup. Google
Sheets does not: "what was Q4 revenue?" needs an exact sum over every row, and
nearest-neighbour search over embedded rows answers a different question while
looking like it answered the right one. Tabular data stays on the
provider-search → read → `compute_node` path.
"""

# Connectors whose content is indexed. Adding a connector here is not enough on
# its own — `chunking.py` needs to know how to split its read payload.
TEXT_CONNECTORS = frozenset({"gmail", "notion"})
