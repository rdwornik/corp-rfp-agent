"""Vault retrieval adapter — wraps corp retrieve CLI for RFP Agent."""

import json
import logging
import os
import sqlite3
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Known index paths (Windows)
_DEFAULT_INDEX_PATHS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "corp-by-os" / "index.db",
]

# Confidence / trust ordering (lower = more trusted)
_TRUST_RANK = {"verified": 0, "extracted": 1, "generated": 2, "draft": 3}

LOW_CONFIDENCE_THRESHOLD = 0.25


def _find_index_db() -> Path | None:
    """Return first existing index.db path, or None."""
    for p in _DEFAULT_INDEX_PATHS:
        if p.exists():
            return p
    return None


def retrieve(
    query: str,
    products: list[str] | None = None,
    limit: int = 10,
    min_trust: str = "draft",
) -> list[dict]:
    """Query vault via corp retrieve CLI, falling back to direct SQLite.

    Args:
        query: Natural-language search query.
        products: Optional product filter list (e.g. ["wms", "planning"]).
        limit: Max results to return.
        min_trust: Minimum trust level (verified > extracted > generated > draft).

    Returns:
        List of note dicts with: note_id, title, content, products, topics,
        relevance_score, confidence (trust_level).
    """
    # --- Try CLI first ---
    try:
        notes = _retrieve_via_cli(query, products=products, limit=limit)
    except (FileNotFoundError, OSError) as exc:
        logger.warning("corp CLI unavailable (%s), falling back to direct SQLite", exc)
        notes = _retrieve_via_sqlite(query, products=products, limit=limit)

    # Filter by trust level
    max_rank = _TRUST_RANK.get(min_trust, 3)
    notes = [
        n for n in notes if _TRUST_RANK.get(n.get("confidence", "draft"), 3) <= max_rank
    ]

    return notes[:limit]


def retrieve_for_rfp(
    question: str,
    family: str | None = None,
) -> dict:
    """High-level: get best answer context for an RFP question.

    Returns:
        dict with keys: answer, sources, confidence, status.
        status is one of: "OK", "NO_DATA", "LOW_CONFIDENCE".
    """
    notes = retrieve(question, products=[family] if family else None)

    if not notes:
        return {
            "answer": "",
            "sources": [],
            "confidence": 0.0,
            "status": "NO_DATA",
        }

    best = notes[0]
    score = best.get("relevance_score", 0.0)

    if score < LOW_CONFIDENCE_THRESHOLD:
        return {
            "answer": best.get("content", ""),
            "sources": [n.get("note_id") for n in notes[:3]],
            "confidence": score,
            "status": "LOW_CONFIDENCE",
        }

    return {
        "answer": best.get("content", ""),
        "sources": [n.get("note_id") for n in notes[:3]],
        "confidence": score,
        "status": "OK",
    }


# ---------------------------------------------------------------------------
# CLI backend
# ---------------------------------------------------------------------------


def _retrieve_via_cli(
    query: str,
    products: list[str] | None = None,
    limit: int = 10,
) -> list[dict]:
    """Call ``corp retrieve --format json`` and parse output."""
    cmd = ["corp", "retrieve", query, "--format", "json", "--top", str(limit)]

    if products:
        for prod in products:
            cmd.extend(["--product", prod])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=30,
    )

    if result.returncode != 0:
        logger.warning(
            "corp retrieve failed (rc=%d): %s", result.returncode, result.stderr.strip()
        )
        return []

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("corp retrieve returned invalid JSON")
        return []

    return data.get("notes", [])


# ---------------------------------------------------------------------------
# Direct SQLite fallback
# ---------------------------------------------------------------------------


def _retrieve_via_sqlite(
    query: str,
    products: list[str] | None = None,
    limit: int = 10,
) -> list[dict]:
    """Fallback: query index.db FTS5 directly (read-only)."""
    db_path = _find_index_db()
    if db_path is None:
        logger.warning("No index.db found at known paths")
        return []

    try:
        # Build FTS5 query: split terms, join with OR
        terms = [t for t in query.split() if len(t) > 2]
        if not terms:
            return []
        fts_query = " OR ".join(terms)

        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        sql = """
            SELECT n.id       AS note_id,
                   n.title,
                   n.topics,
                   n.products,
                   n.domains,
                   n.confidence,
                   n.note_path,
                   n.project_id,
                   rank          AS bm25_rank
            FROM   notes_fts
            JOIN   notes n ON n.id = notes_fts.rowid
            WHERE  notes_fts MATCH ?
        """
        params: list = [fts_query]

        if products:
            clauses = []
            for prod in products:
                clauses.append("n.products LIKE ?")
                params.append(f"%{prod}%")
            sql += " AND (" + " OR ".join(clauses) + ")"

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        conn.close()

        notes = []
        for row in rows:
            content = _load_note_content(row["note_path"])
            notes.append(
                {
                    "note_id": row["note_id"],
                    "title": row["title"],
                    "content": content,
                    "products": _parse_json_field(row["products"]),
                    "topics": _parse_json_field(row["topics"]),
                    "domains": _parse_json_field(row["domains"]),
                    "confidence": row["confidence"] or "draft",
                    "relevance_score": _bm25_to_score(row["bm25_rank"]),
                    "project_id": row["project_id"],
                }
            )

        return notes

    except Exception as exc:
        logger.warning("Direct SQLite retrieval failed: %s", exc)
        return []


def _load_note_content(note_path: str | None) -> str:
    """Read markdown file content, return empty string on failure."""
    if not note_path:
        return ""
    p = Path(note_path)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8")
    except Exception:
        return ""


def _parse_json_field(value: str | None) -> list[str]:
    """Parse a JSON array string or comma-separated string into a list."""
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return [s.strip() for s in value.split(",") if s.strip()]


def _bm25_to_score(rank: float | None) -> float:
    """Convert FTS5 BM25 rank (negative, lower=better) to 0-1 score."""
    if rank is None:
        return 0.0
    # BM25 ranks are negative; typical range roughly -20 to 0
    # Any match (even weak) gets a minimum score so callers can distinguish
    # "found something" from "found nothing".
    raw = -rank  # flip sign so positive = better
    if raw <= 0:
        return 0.0
    clamped = min(raw, 20.0)
    score = clamped / 20.0
    return round(max(score, 0.01), 3)  # floor at 0.01 for any match
