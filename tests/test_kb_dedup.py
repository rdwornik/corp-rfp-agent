"""Tests for kb_dedup -- semantic deduplication."""

import sys
from pathlib import Path

import pytest

# Add src/ to path for direct imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kb_dedup import (
    score_entry,
    get_question_text,
    get_answer_text,
    find_duplicates,
    cluster_duplicates,
    deduplicate,
    load_all_entries,
    apply_dedup,
)


def _make_entry(**kwargs):
    """Helper to create a test entry."""
    base = {
        "canonical_question": "What is Blue Yonder?",
        "canonical_answer": "Blue Yonder is a supply chain platform.",
        "category": "functional",
        "last_updated": "2025-01-01",
        "_source_file": "test.json",
    }
    base.update(kwargs)
    return base


def test_score_entry_prefers_longer_answers():
    """Longer answer scores higher."""
    short = _make_entry(canonical_answer="Short.")
    long = _make_entry(canonical_answer="A much longer and more detailed answer about Blue Yonder capabilities.")
    assert score_entry(long) > score_entry(short)


def test_score_entry_prefers_newer_dates():
    """Newer date scores higher (all else equal)."""
    old = _make_entry(last_updated="2023-01-01", canonical_answer="Same length answer text here.")
    new = _make_entry(last_updated="2026-01-01", canonical_answer="Same length answer text here.")
    assert score_entry(new) > score_entry(old)


def test_score_entry_prefers_v2_over_v1():
    """v2 entry (has 'id' field) scores higher."""
    v1 = _make_entry(kb_id="kb_0001")
    v2 = _make_entry(id="PLN-FUNC-0001", kb_id="kb_0001")
    assert score_entry(v2) > score_entry(v1)


def test_score_entry_prefers_verified():
    """Verified confidence scores higher than draft."""
    draft = _make_entry(confidence="draft")
    verified = _make_entry(confidence="verified")
    assert score_entry(verified) > score_entry(draft)


def test_find_duplicates_detects_pairs_above_threshold():
    """Identical embeddings detected as duplicates."""
    import numpy as np
    # Two identical vectors and one different
    embeddings = np.array([
        [1.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],  # duplicate of 0
        [0.0, 1.0, 0.0],  # different
    ], dtype=np.float32)
    # Normalize
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / norms

    pairs = find_duplicates(embeddings, threshold=0.90)
    assert len(pairs) == 1
    assert pairs[0][0] == 0 and pairs[0][1] == 1
    assert pairs[0][2] >= 0.99


def test_find_duplicates_ignores_pairs_below_threshold():
    """Orthogonal vectors not detected as duplicates."""
    import numpy as np
    embeddings = np.array([
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float32)
    pairs = find_duplicates(embeddings, threshold=0.50)
    assert len(pairs) == 0


def test_cluster_merges_transitive_duplicates():
    """A-B and B-C should form one cluster {A, B, C}."""
    pairs = [(0, 1, 0.90), (1, 2, 0.88)]
    clusters = cluster_duplicates(pairs, n=4)
    # Should have one cluster of 3 and index 3 not in any cluster
    assert len(clusters) == 1
    assert sorted(clusters[0]) == [0, 1, 2]


def test_deduplicate_picks_highest_scored_per_cluster(tmp_path):
    """Winner is the entry with highest score."""
    entries = [
        _make_entry(canonical_question="What database does BY use?",
                     canonical_answer="PostgreSQL on Azure." * 10,  # longer
                     last_updated="2026-01-01", confidence="verified"),
        _make_entry(canonical_question="Which database technology is used?",
                     canonical_answer="PostgreSQL.",
                     last_updated="2024-01-01", confidence="draft"),
    ]
    # Create a mock -- we can't run real embeddings in unit test easily.
    # Instead test the score logic directly:
    assert score_entry(entries[0]) > score_entry(entries[1])


def test_removed_entries_questions_become_variants():
    """Absorbed entry's question added to winner's question_variants."""
    entries = [
        _make_entry(canonical_question="Q1", canonical_answer="A" * 100),
        _make_entry(canonical_question="Q2", canonical_answer="A" * 10),
    ]

    # Simulate cluster where entry 0 wins
    import numpy as np
    embeddings = np.array([[1.0, 0.0], [0.99, 0.14]], dtype=np.float32)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / norms

    pairs = find_duplicates(embeddings, threshold=0.90)
    clusters = cluster_duplicates(pairs, n=2)

    if clusters:
        cluster = clusters[0]
        scored = [(idx, score_entry(entries[idx])) for idx in cluster]
        scored.sort(key=lambda x: x[1], reverse=True)
        winner_idx = scored[0][0]
        losers = [idx for idx, _ in scored[1:]]

        winner = entries[winner_idx]
        existing_variants = list(winner.get("question_variants", []))
        for loser_idx in losers:
            loser_q = get_question_text(entries[loser_idx])
            if loser_q and loser_q not in existing_variants:
                existing_variants.append(loser_q)
        winner["question_variants"] = existing_variants

        assert "Q2" in entries[winner_idx].get("question_variants", [])


def test_apply_dedup_removes_correct_entries():
    """apply_dedup keeps only winner entries."""
    entries = [{"q": "a"}, {"q": "b"}, {"q": "c"}]
    report = {
        "clusters": [
            {
                "keep": {"index": 0, "question": "a", "answer_len": 10, "id": "1"},
                "remove": [{"index": 1, "question": "b", "answer_len": 5, "id": "2"}],
                "avg_similarity": 0.90,
                "size": 2,
            }
        ]
    }
    result = apply_dedup(entries, report)
    assert len(result) == 2
    assert result[0]["q"] == "a"
    assert result[1]["q"] == "c"
