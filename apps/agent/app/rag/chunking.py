"""Split a connector's read payload into embeddable chunks.

Chunking decides retrieval quality more than the embedding model does. Two
choices drive everything here:

**Chunks carry their own context.** An embedded fragment is retrieved alone and
read alone, so a paragraph that says "we agreed to push it to Q3" is useless
without the thread it came from. Every chunk is prefixed with its source's
title (and, for email, the sender), which costs a few tokens and makes the
difference between a hit that answers the question and one that merely matches
it.

**Splits follow the document's own boundaries.** Email splits per message and
Notion per paragraph, because those are the seams the author already put there.
Only when a single unit is too large does it fall back to a sliding window.
"""
import re

# ~1200 characters is roughly 300 tokens: large enough to hold a full argument,
# small enough that a hit points at something specific rather than at a page.
_MAX_CHARS = 1200
# One sentence or so of overlap, so a fact that straddles a split boundary
# survives in at least one chunk intact.
_OVERLAP = 180
# Below this a chunk is noise — a signature block, a "thanks!" reply — and
# embedding it only adds near-duplicate neighbours that crowd out real hits.
_MIN_CHARS = 40

_PARAGRAPH = re.compile(r"\n\s*\n")
_QUOTED_REPLY = re.compile(
    r"^\s*(>|On .{0,80}wrote:|-{2,}\s*Original Message|_{5,})", re.MULTILINE
)


class Chunk:
    """A span of text plus what is needed to cite and re-find it."""

    __slots__ = ("chunk_index", "content", "resource_id", "resource_title")

    def __init__(self, content: str, chunk_index: int, resource_id: str, resource_title: str):
        self.content = content
        self.chunk_index = chunk_index
        self.resource_id = resource_id
        self.resource_title = resource_title

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return f"Chunk({self.resource_title!r}#{self.chunk_index}, {len(self.content)} chars)"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Chunk) and (
            self.content,
            self.chunk_index,
            self.resource_id,
        ) == (other.content, other.chunk_index, other.resource_id)


def _strip_quoted(body: str) -> str:
    """Drop the quoted history below a reply.

    Without this every message in a thread re-embeds the whole conversation
    beneath it, so a ten-message thread produces ten near-identical vectors and
    the top-k fills with copies of one exchange.
    """
    match = _QUOTED_REPLY.search(body)
    return body[: match.start()].strip() if match else body.strip()


def _window(text: str) -> list[str]:
    """Slide a fixed window over text too long to keep whole."""
    if len(text) <= _MAX_CHARS:
        return [text]

    out: list[str] = []
    start = 0
    step = _MAX_CHARS - _OVERLAP
    while start < len(text):
        piece = text[start : start + _MAX_CHARS]
        # Prefer to end on a sentence boundary so a chunk does not stop
        # mid-clause, but only if one exists reasonably late in the window.
        if start + _MAX_CHARS < len(text):
            cut = max(piece.rfind(". "), piece.rfind("\n"))
            if cut > _MAX_CHARS // 2:
                piece = piece[: cut + 1]
        piece = piece.strip()
        if piece:
            out.append(piece)
        start += max(step, len(piece) - _OVERLAP) if piece else step
    return out


def _pack(units: list[str]) -> list[str]:
    """Greedily combine short units up to `_MAX_CHARS`, splitting long ones.

    A Notion page of one-line bullets should not become one vector per bullet:
    each is too small to carry meaning, and the retrieval budget fills with
    fragments.
    """
    out: list[str] = []
    buffer = ""

    for unit in units:
        unit = unit.strip()
        if not unit:
            continue
        if len(unit) > _MAX_CHARS:
            if buffer:
                out.append(buffer)
                buffer = ""
            out.extend(_window(unit))
            continue
        if not buffer:
            buffer = unit
        elif len(buffer) + len(unit) + 2 <= _MAX_CHARS:
            buffer = f"{buffer}\n\n{unit}"
        else:
            out.append(buffer)
            buffer = unit

    if buffer:
        out.append(buffer)
    return out


def _emit(
    bodies: list[str], resource_id: str, title: str, prefixes: list[str] | None = None
) -> list[Chunk]:
    """Attach context and indices, dropping anything too short to be useful."""
    chunks: list[Chunk] = []
    for body in bodies:
        if len(body.strip()) < _MIN_CHARS:
            continue
        header = " · ".join(p for p in ([title] + (prefixes or [])) if p)
        content = f"{header}\n\n{body}".strip() if header else body.strip()
        chunks.append(Chunk(content, len(chunks), resource_id, title))
    return chunks


def chunk_gmail(payload: dict, resource_id: str, title: str) -> list[Chunk]:
    """One chunk per message (or per window of a long message).

    Split per message rather than per thread because a thread can span months
    and several subjects, and a single vector for the whole thing matches
    everything weakly and nothing well.
    """
    chunks: list[Chunk] = []
    for message in payload.get("messages") or []:
        if not isinstance(message, dict):
            continue
        body = _strip_quoted(str(message.get("body") or ""))
        if not body:
            continue
        sender = str(message.get("from") or "")
        date = str(message.get("date") or "")
        subject = str(message.get("subject") or title)
        for piece in _window(body):
            if len(piece.strip()) < _MIN_CHARS:
                continue
            header = " · ".join(p for p in (subject, sender, date) if p)
            chunks.append(
                Chunk(f"{header}\n\n{piece}".strip(), len(chunks), resource_id, title)
            )
    return chunks


def chunk_notion(payload: dict, resource_id: str, title: str) -> list[Chunk]:
    """Pack the page's paragraphs into chunks, respecting blank-line breaks."""
    content = str(payload.get("content") or "")
    if not content.strip():
        return []
    return _emit(_pack(_PARAGRAPH.split(content)), resource_id, title)


_CHUNKERS = {"gmail": chunk_gmail, "notion": chunk_notion}


def chunk(connector: str, payload: dict, resource_id: str, title: str) -> list[Chunk]:
    """Chunk a read payload, or return [] for a source with no chunker.

    Returning empty rather than raising keeps a newly added connector from
    breaking ingest for every other one; it simply is not indexed until someone
    writes its chunker.
    """
    chunker = _CHUNKERS.get(connector)
    if chunker is None or not isinstance(payload, dict) or payload.get("error"):
        return []
    return chunker(payload, resource_id, title)
