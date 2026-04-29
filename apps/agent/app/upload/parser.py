"""Parse uploaded files into a common dict format the agent can query."""
from __future__ import annotations

import io
import json
from typing import Any

import pandas as pd


def parse_csv(data: bytes) -> list[dict[str, Any]]:
    df = pd.read_csv(io.BytesIO(data))
    return df.where(pd.notna(df), None).to_dict(orient="records")


def parse_excel(data: bytes) -> list[dict[str, Any]]:
    df = pd.read_excel(io.BytesIO(data))
    return df.where(pd.notna(df), None).to_dict(orient="records")


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _pdf_via_vision(data: bytes) -> list[dict[str, Any]]:
    """Render each PDF page to an image and ask Claude to extract structured data."""
    import base64

    import fitz  # pymupdf

    from app.llm import get_client

    doc = fitz.open(stream=data, filetype="pdf")
    images_b64: list[str] = []
    for page in doc:
        pix = page.get_pixmap(dpi=150)
        images_b64.append(base64.standard_b64encode(pix.tobytes("png")).decode())

    client = get_client()
    content: list[dict] = []
    for img in images_b64:
        content.append(
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": img},
            }
        )
    content.append(
        {
            "type": "text",
            "text": (
                "Extract all tabular or structured data from these PDF pages. "
                "Return ONLY a JSON array of objects (one object per row / data record). "
                "If there are no tables, return a JSON array with a single object "
                '{"content": "<full plain-text of the page>"}.'
            ),
        }
    )

    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    )
    raw = response.content[0].text.strip()
    # strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(raw)


def parse_pdf(data: bytes) -> list[dict[str, Any]]:
    text = _pdf_text(data)
    # If we got meaningful text (>200 chars), parse via text path
    if len(text.strip()) > 200:
        # Try to detect a CSV-like structure in the text
        lines = [l for l in text.splitlines() if l.strip()]
        if lines and "," in lines[0]:
            try:
                return parse_csv(text.encode())
            except Exception:
                pass
        return [{"content": text}]
    # Sparse/image PDF — use vision
    return _pdf_via_vision(data)


def parse_file(filename: str, data: bytes) -> list[dict[str, Any]]:
    name = filename.lower()
    if name.endswith(".csv"):
        return parse_csv(data)
    if name.endswith((".xlsx", ".xls", ".xlsm")):
        return parse_excel(data)
    if name.endswith(".pdf"):
        return parse_pdf(data)
    raise ValueError(f"Unsupported file type: {filename}")
