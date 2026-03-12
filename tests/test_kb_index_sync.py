"""Tests for kb_index_sync -- incremental sync and Blue/Green rebuild."""

import json
import sys
import hashlib
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# Add src/ to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kb_index_sync import (
    load_file_state,
    save_file_state,
    hash_file,
    discover_canonical_files,
    load_entries,
    make_vector_id,
    compute_delta,
    sync,
    force_rebuild,
    EMBEDDING_MODEL,
    COLLECTION_NAME,
    CHUNKING_VERSION,
    _get_question,
    _get_answer,
    _get_domain,
    _get_entry_id,
    _make_search_doc,
    _build_metadata,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_v1_entry(**kwargs):
    """Create a v1-style KB entry."""
    base = {
        "kb_id": "kb_0001",
        "domain": "planning",
        "category": "technical",
        "subcategory": "Access Management",
        "canonical_question": "What is Blue Yonder?",
        "canonical_answer": "Blue Yonder is a supply chain platform.",
        "search_blob": "CAT: technical || Q: What is Blue Yonder? || A: Blue Yonder is a supply chain platform.",
        "last_updated": "2025-01-01",
    }
    base.update(kwargs)
    return base


def _make_v2_entry(**kwargs):
    """Create a v2-style KB entry."""
    base = {
        "id": "WMS-FUNC-0001",
        "family_code": "wms",
        "category": "functional",
        "subcategory": "Picking",
        "question": "How does WMS handle picking?",
        "answer": "BY WMS uses voice-directed picking for optimal warehouse operations.",
        "tags": ["picking", "warehouse"],
        "confidence": "verified",
        "last_updated": "2026-01-15",
    }
    base.update(kwargs)
    return base


def _write_canonical(tmp_path, filename, entries):
    """Write canonical file and return path."""
    canonical_dir = tmp_path / "canonical"
    canonical_dir.mkdir(parents=True, exist_ok=True)
    filepath = canonical_dir / filename
    filepath.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    return filepath


@pytest.fixture
def canonical_dir(tmp_path):
    """Create temp canonical directory with sample files."""
    d = tmp_path / "canonical"
    d.mkdir()
    return d


@pytest.fixture
def state_path(tmp_path):
    """Temp file_state.json path."""
    return tmp_path / "file_state.json"


@pytest.fixture
def sample_planning_entries():
    return [
        _make_v1_entry(kb_id="kb_0001", canonical_question="What is BY?"),
        _make_v1_entry(kb_id="kb_0002", canonical_question="What is SaaS?"),
    ]


@pytest.fixture
def sample_wms_entries():
    return [
        _make_v2_entry(id="WMS-FUNC-0001", question="How does picking work?"),
        _make_v2_entry(id="WMS-FUNC-0002", question="How does packing work?"),
    ]


# ===================================================================
# File state management
# ===================================================================

class TestFileState:

    def test_empty_state_first_run(self, tmp_path):
        """No file_state.json -> returns empty dict."""
        with patch("kb_index_sync.STATE_PATH", tmp_path / "nonexistent.json"):
            state = load_file_state()
        assert state == {}

    def test_corrupted_state_file(self, tmp_path):
        """Malformed JSON -> treated as first run (empty dict)."""
        bad = tmp_path / "file_state.json"
        bad.write_text("{bad json!!!", encoding="utf-8")
        with patch("kb_index_sync.STATE_PATH", bad):
            state = load_file_state()
        assert state == {}

    def test_state_roundtrip(self, tmp_path):
        """Write -> read -> compare matches."""
        state_path = tmp_path / "file_state.json"
        manifest = {
            "version": "1.0",
            "embedding_model": EMBEDDING_MODEL,
            "chunking_version": CHUNKING_VERSION,
            "last_sync": "2026-03-12T10:00:00",
            "collection_name": COLLECTION_NAME,
            "files": {
                "RFP_Database_WMS_CANONICAL.json": {
                    "hash": "abc123",
                    "entry_count": 10,
                    "last_synced": "2026-03-12T10:00:00",
                },
            },
        }
        with patch("kb_index_sync.STATE_PATH", state_path):
            save_file_state(manifest)
            loaded = load_file_state()
        assert loaded == manifest

    def test_atomic_state_write(self, tmp_path):
        """State written via tmp+rename pattern (no .tmp left behind)."""
        state_path = tmp_path / "file_state.json"
        tmp_file = state_path.with_suffix(".tmp")
        with patch("kb_index_sync.STATE_PATH", state_path):
            save_file_state({"version": "1.0", "files": {}})
        assert state_path.exists()
        assert not tmp_file.exists()

    def test_overwrite_existing_state(self, tmp_path):
        """Overwriting existing state works on Windows (unlink + rename)."""
        state_path = tmp_path / "file_state.json"
        state_path.write_text('{"old": true}', encoding="utf-8")
        with patch("kb_index_sync.STATE_PATH", state_path):
            save_file_state({"version": "2.0", "files": {}})
            loaded = load_file_state()
        assert loaded["version"] == "2.0"


# ===================================================================
# Helper functions
# ===================================================================

class TestHelpers:

    def test_hash_file(self, tmp_path):
        """SHA-256 hash of file contents."""
        f = tmp_path / "test.json"
        f.write_bytes(b'[{"hello": "world"}]')
        h = hash_file(f)
        assert h == hashlib.sha256(b'[{"hello": "world"}]').hexdigest()

    def test_discover_skips_unified(self, canonical_dir):
        """discover_canonical_files skips UNIFIED file."""
        (canonical_dir / "RFP_Database_UNIFIED_CANONICAL.json").write_text("[]")
        (canonical_dir / "RFP_Database_WMS_CANONICAL.json").write_text("[]")
        (canonical_dir / "RFP_Database_Planning_CANONICAL.json").write_text("[]")
        files = discover_canonical_files(canonical_dir)
        names = [f.name for f in files]
        assert "RFP_Database_UNIFIED_CANONICAL.json" not in names
        assert "RFP_Database_WMS_CANONICAL.json" in names
        assert "RFP_Database_Planning_CANONICAL.json" in names

    def test_discover_sorted(self, canonical_dir):
        """Files returned in sorted order."""
        (canonical_dir / "RFP_Database_WMS_CANONICAL.json").write_text("[]")
        (canonical_dir / "RFP_Database_AIML_CANONICAL.json").write_text("[]")
        files = discover_canonical_files(canonical_dir)
        names = [f.name for f in files]
        assert names == sorted(names)

    def test_get_question_v1(self):
        assert _get_question({"canonical_question": "Q1"}) == "Q1"

    def test_get_question_v2(self):
        assert _get_question({"question": "Q2"}) == "Q2"

    def test_get_question_prefers_v1(self):
        """canonical_question takes precedence over question."""
        assert _get_question({"canonical_question": "Q1", "question": "Q2"}) == "Q1"

    def test_get_answer_v1(self):
        assert _get_answer({"canonical_answer": "A1"}) == "A1"

    def test_get_answer_v2(self):
        assert _get_answer({"answer": "A2"}) == "A2"

    def test_get_domain_v1(self):
        assert _get_domain({"domain": "planning"}) == "planning"

    def test_get_domain_v2(self):
        assert _get_domain({"family_code": "wms"}) == "wms"

    def test_get_entry_id_v1(self):
        assert _get_entry_id({"kb_id": "kb_0001"}) == "kb_0001"

    def test_get_entry_id_v2(self):
        assert _get_entry_id({"id": "WMS-FUNC-0001"}) == "WMS-FUNC-0001"

    def test_make_search_doc_uses_blob(self):
        e = _make_v1_entry(search_blob="my blob")
        assert _make_search_doc(e) == "my blob"

    def test_make_search_doc_fallback(self):
        e = _make_v2_entry()
        e.pop("search_blob", None)
        doc = _make_search_doc(e)
        assert "How does WMS handle picking?" in doc
        assert "BY WMS uses voice-directed picking" in doc

    def test_deterministic_ids(self):
        """Same content -> same ID across runs."""
        id1 = make_vector_id("file.json", "What is BY?")
        id2 = make_vector_id("file.json", "What is BY?")
        assert id1 == id2

    def test_different_file_different_id(self):
        """Different file -> different ID even for same question."""
        id1 = make_vector_id("file_a.json", "What is BY?")
        id2 = make_vector_id("file_b.json", "What is BY?")
        assert id1 != id2

    def test_build_metadata_source_file(self):
        """Every vector has source_file in metadata."""
        meta = _build_metadata(_make_v1_entry(), "test_file.json")
        assert meta["source_file"] == "test_file.json"

    def test_build_metadata_truncates_answer(self):
        """Long answers are truncated in metadata."""
        long_answer = "x" * 2000
        entry = _make_v1_entry(canonical_answer=long_answer)
        meta = _build_metadata(entry, "test.json")
        assert len(meta["canonical_answer"]) == 1003  # 1000 + "..."
        assert meta["canonical_answer"].endswith("...")

    def test_build_metadata_v2_fields(self):
        """v2 entries correctly mapped."""
        entry = _make_v2_entry()
        meta = _build_metadata(entry, "wms.json")
        assert meta["domain"] == "wms"
        assert meta["kb_id"] == "WMS-FUNC-0001"
        assert meta["canonical_question"] == "How does WMS handle picking?"


# ===================================================================
# Delta computation
# ===================================================================

class TestComputeDelta:

    def test_all_new_no_state(self, canonical_dir, sample_planning_entries):
        """No previous state -> all files treated as added."""
        _write_canonical(canonical_dir.parent, "RFP_Database_Planning_CANONICAL.json",
                         sample_planning_entries)
        delta = compute_delta(canonical_dir, prev_state={})
        assert len(delta["added"]) == 1
        assert delta["changed"] == []
        assert delta["deleted"] == []
        assert delta["model_changed"] is False

    def test_unchanged_files_skip(self, canonical_dir, sample_planning_entries):
        """Same hash -> file is unchanged."""
        fp = _write_canonical(canonical_dir.parent, "RFP_Database_Planning_CANONICAL.json",
                              sample_planning_entries)
        file_hash = hash_file(fp)
        prev = {
            "embedding_model": EMBEDDING_MODEL,
            "files": {
                "RFP_Database_Planning_CANONICAL.json": {
                    "hash": file_hash, "entry_count": 2,
                }
            }
        }
        delta = compute_delta(canonical_dir, prev)
        assert delta["added"] == []
        assert delta["changed"] == []
        assert delta["unchanged"] == ["RFP_Database_Planning_CANONICAL.json"]

    def test_new_file_detected(self, canonical_dir, sample_planning_entries, sample_wms_entries):
        """New canonical file -> flagged as added."""
        fp = _write_canonical(canonical_dir.parent, "RFP_Database_Planning_CANONICAL.json",
                              sample_planning_entries)
        _write_canonical(canonical_dir.parent, "RFP_Database_WMS_CANONICAL.json",
                         sample_wms_entries)
        prev = {
            "embedding_model": EMBEDDING_MODEL,
            "files": {
                "RFP_Database_Planning_CANONICAL.json": {
                    "hash": hash_file(fp), "entry_count": 2,
                }
            }
        }
        delta = compute_delta(canonical_dir, prev)
        assert "RFP_Database_WMS_CANONICAL.json" in delta["added"]
        assert "RFP_Database_Planning_CANONICAL.json" in delta["unchanged"]

    def test_changed_file_detected(self, canonical_dir, sample_planning_entries):
        """Different hash -> flagged as changed."""
        _write_canonical(canonical_dir.parent, "RFP_Database_Planning_CANONICAL.json",
                         sample_planning_entries)
        prev = {
            "embedding_model": EMBEDDING_MODEL,
            "files": {
                "RFP_Database_Planning_CANONICAL.json": {
                    "hash": "old_hash_that_doesnt_match", "entry_count": 2,
                }
            }
        }
        delta = compute_delta(canonical_dir, prev)
        assert "RFP_Database_Planning_CANONICAL.json" in delta["changed"]

    def test_deleted_file_detected(self, canonical_dir):
        """File in prev_state but not on disk -> flagged as deleted."""
        prev = {
            "embedding_model": EMBEDDING_MODEL,
            "files": {
                "RFP_Database_OldDomain_CANONICAL.json": {
                    "hash": "whatever", "entry_count": 5,
                }
            }
        }
        delta = compute_delta(canonical_dir, prev)
        assert "RFP_Database_OldDomain_CANONICAL.json" in delta["deleted"]

    def test_embedding_model_version_check(self, canonical_dir, sample_planning_entries):
        """Model change -> model_changed flag set."""
        _write_canonical(canonical_dir.parent, "RFP_Database_Planning_CANONICAL.json",
                         sample_planning_entries)
        prev = {
            "embedding_model": "some-other-model/v2",
            "files": {}
        }
        delta = compute_delta(canonical_dir, prev)
        assert delta["model_changed"] is True

    def test_first_run_no_model_change(self, canonical_dir, sample_planning_entries):
        """Empty prev_state -> model_changed is False (first run)."""
        _write_canonical(canonical_dir.parent, "RFP_Database_Planning_CANONICAL.json",
                         sample_planning_entries)
        delta = compute_delta(canonical_dir, prev_state={})
        assert delta["model_changed"] is False


# ===================================================================
# Incremental sync
# ===================================================================

class TestSync:

    @patch("kb_index_sync._get_embedding_function")
    @patch("kb_index_sync._get_chroma_client")
    def test_sync_noop_unchanged(self, mock_client_fn, mock_ef_fn, canonical_dir,
                                  sample_planning_entries, tmp_path):
        """Unchanged files -> ChromaDB untouched."""
        fp = _write_canonical(canonical_dir.parent, "RFP_Database_Planning_CANONICAL.json",
                              sample_planning_entries)
        state_path = tmp_path / "file_state.json"
        manifest = {
            "version": "1.0",
            "embedding_model": EMBEDDING_MODEL,
            "files": {
                "RFP_Database_Planning_CANONICAL.json": {
                    "hash": hash_file(fp), "entry_count": 2,
                }
            }
        }
        state_path.write_text(json.dumps(manifest), encoding="utf-8")
        with patch("kb_index_sync.STATE_PATH", state_path):
            result = sync(canonical_dir)
        assert result["unchanged"] == 1
        assert result.get("added", 0) == 0
        mock_client_fn.assert_not_called()

    @patch("kb_index_sync._embed_and_upsert", return_value=5)
    @patch("kb_index_sync._get_embedding_function")
    @patch("kb_index_sync._get_chroma_client")
    def test_sync_adds_new_vectors(self, mock_client_fn, mock_ef_fn, mock_upsert,
                                    canonical_dir, sample_wms_entries, tmp_path):
        """New file -> vectors appear in ChromaDB."""
        _write_canonical(canonical_dir.parent, "RFP_Database_WMS_CANONICAL.json",
                         sample_wms_entries)
        state_path = tmp_path / "file_state.json"

        mock_collection = MagicMock()
        mock_collection.count.return_value = 5
        mock_client = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_collection
        mock_client_fn.return_value = mock_client

        with patch("kb_index_sync.STATE_PATH", state_path):
            result = sync(canonical_dir)

        assert result["added"] == 1
        assert result["total_upserted"] == 5
        mock_upsert.assert_called_once()

    @patch("kb_index_sync._embed_and_upsert", return_value=3)
    @patch("kb_index_sync._get_embedding_function")
    @patch("kb_index_sync._get_chroma_client")
    def test_sync_updates_changed(self, mock_client_fn, mock_ef_fn, mock_upsert,
                                   canonical_dir, sample_planning_entries, tmp_path):
        """Changed file -> old vectors deleted, new upserted."""
        _write_canonical(canonical_dir.parent, "RFP_Database_Planning_CANONICAL.json",
                         sample_planning_entries)
        state_path = tmp_path / "file_state.json"
        manifest = {
            "version": "1.0",
            "embedding_model": EMBEDDING_MODEL,
            "files": {
                "RFP_Database_Planning_CANONICAL.json": {
                    "hash": "old_hash", "entry_count": 2,
                }
            }
        }
        state_path.write_text(json.dumps(manifest), encoding="utf-8")

        mock_collection = MagicMock()
        mock_collection.count.return_value = 3
        mock_client = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_collection
        mock_client_fn.return_value = mock_client

        with patch("kb_index_sync.STATE_PATH", state_path):
            result = sync(canonical_dir)

        assert result["changed"] == 1
        mock_collection.delete.assert_called_once_with(
            where={"source_file": "RFP_Database_Planning_CANONICAL.json"}
        )
        mock_upsert.assert_called_once()

    @patch("kb_index_sync._get_embedding_function")
    @patch("kb_index_sync._get_chroma_client")
    def test_sync_deletes_removed(self, mock_client_fn, mock_ef_fn, canonical_dir, tmp_path):
        """Deleted file -> vectors removed from ChromaDB."""
        state_path = tmp_path / "file_state.json"
        manifest = {
            "version": "1.0",
            "embedding_model": EMBEDDING_MODEL,
            "files": {
                "RFP_Database_Gone_CANONICAL.json": {
                    "hash": "abc", "entry_count": 10,
                }
            }
        }
        state_path.write_text(json.dumps(manifest), encoding="utf-8")

        mock_collection = MagicMock()
        mock_collection.count.return_value = 0
        mock_client = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_collection
        mock_client_fn.return_value = mock_client

        with patch("kb_index_sync.STATE_PATH", state_path):
            result = sync(canonical_dir)

        assert result["deleted"] == 1
        mock_collection.delete.assert_called_once_with(
            where={"source_file": "RFP_Database_Gone_CANONICAL.json"}
        )

    @patch("kb_index_sync._get_embedding_function")
    @patch("kb_index_sync._get_chroma_client")
    def test_source_file_metadata(self, mock_client_fn, mock_ef_fn,
                                   canonical_dir, sample_wms_entries, tmp_path):
        """Every vector has source_file in metadata (checked via _build_metadata)."""
        _write_canonical(canonical_dir.parent, "RFP_Database_WMS_CANONICAL.json",
                         sample_wms_entries)
        # Already tested via _build_metadata, but verify end-to-end by checking
        # the upsert call includes source_file
        state_path = tmp_path / "file_state.json"

        mock_collection = MagicMock()
        mock_collection.count.return_value = 2
        mock_client = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_collection
        mock_client_fn.return_value = mock_client

        with patch("kb_index_sync.STATE_PATH", state_path):
            sync(canonical_dir)

        # Check upsert was called and metadatas contain source_file
        upsert_calls = mock_collection.upsert.call_args_list
        assert len(upsert_calls) > 0
        for c in upsert_calls:
            metadatas = c.kwargs.get("metadatas") or c[1].get("metadatas", [])
            for meta in metadatas:
                assert "source_file" in meta
                assert meta["source_file"] == "RFP_Database_WMS_CANONICAL.json"

    def test_sync_dry_run(self, canonical_dir, sample_wms_entries, tmp_path):
        """Dry run shows plan without modifying ChromaDB."""
        _write_canonical(canonical_dir.parent, "RFP_Database_WMS_CANONICAL.json",
                         sample_wms_entries)
        state_path = tmp_path / "file_state.json"
        with patch("kb_index_sync.STATE_PATH", state_path):
            result = sync(canonical_dir, dry_run=True)
        assert result["dry_run"] is True
        assert result["added"] == 1

    def test_sync_model_changed_refuses(self, canonical_dir, sample_planning_entries, tmp_path):
        """Model change -> refuses incremental, requires rebuild."""
        _write_canonical(canonical_dir.parent, "RFP_Database_Planning_CANONICAL.json",
                         sample_planning_entries)
        state_path = tmp_path / "file_state.json"
        manifest = {
            "version": "1.0",
            "embedding_model": "different-model/v2",
            "files": {}
        }
        state_path.write_text(json.dumps(manifest), encoding="utf-8")
        with patch("kb_index_sync.STATE_PATH", state_path):
            result = sync(canonical_dir)
        assert result.get("error") == "model_changed"

    @patch("kb_index_sync._embed_and_upsert", return_value=2)
    @patch("kb_index_sync._get_embedding_function")
    @patch("kb_index_sync._get_chroma_client")
    def test_sync_saves_state_after_success(self, mock_client_fn, mock_ef_fn, mock_upsert,
                                             canonical_dir, sample_wms_entries, tmp_path):
        """file_state.json is written after successful sync."""
        _write_canonical(canonical_dir.parent, "RFP_Database_WMS_CANONICAL.json",
                         sample_wms_entries)
        state_path = tmp_path / "file_state.json"

        mock_collection = MagicMock()
        mock_collection.count.return_value = 2
        mock_client = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_collection
        mock_client_fn.return_value = mock_client

        with patch("kb_index_sync.STATE_PATH", state_path):
            sync(canonical_dir)

        assert state_path.exists()
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        assert saved["embedding_model"] == EMBEDDING_MODEL
        assert "RFP_Database_WMS_CANONICAL.json" in saved["files"]
        assert saved["files"]["RFP_Database_WMS_CANONICAL.json"]["entry_count"] == 2

    @patch("kb_index_sync._embed_and_upsert", return_value=10)
    @patch("kb_index_sync._get_embedding_function")
    @patch("kb_index_sync._get_chroma_client")
    def test_count_validation_warns_on_mismatch(self, mock_client_fn, mock_ef_fn, mock_upsert,
                                                 canonical_dir, sample_wms_entries, tmp_path,
                                                 capsys):
        """Large count mismatch triggers warning."""
        _write_canonical(canonical_dir.parent, "RFP_Database_WMS_CANONICAL.json",
                         sample_wms_entries)
        state_path = tmp_path / "file_state.json"

        mock_collection = MagicMock()
        mock_collection.count.return_value = 999  # Big mismatch vs 2 entries
        mock_client = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_collection
        mock_client_fn.return_value = mock_client

        with patch("kb_index_sync.STATE_PATH", state_path):
            sync(canonical_dir)

        captured = capsys.readouterr()
        assert "WARN" in captured.out
        assert "mismatch" in captured.out.lower()


# ===================================================================
# Blue/Green full rebuild
# ===================================================================

class TestForceRebuild:

    @patch("kb_index_sync._embed_and_upsert", return_value=5)
    @patch("kb_index_sync._get_embedding_function")
    @patch("kb_index_sync._get_chroma_client")
    def test_force_rebuild_creates_new_collection(self, mock_client_fn, mock_ef_fn,
                                                   mock_upsert, canonical_dir,
                                                   sample_wms_entries, tmp_path):
        """Rebuild creates a temp collection first."""
        _write_canonical(canonical_dir.parent, "RFP_Database_WMS_CANONICAL.json",
                         sample_wms_entries)
        state_path = tmp_path / "file_state.json"

        mock_new_collection = MagicMock()
        mock_new_collection.count.return_value = 5
        mock_new_collection.get.return_value = {
            "ids": ["a", "b"], "embeddings": [[0.1], [0.2]],
            "documents": ["d1", "d2"], "metadatas": [{"k": "v"}, {"k": "v"}],
        }
        mock_final_collection = MagicMock()
        mock_final_collection.count.return_value = 5

        mock_client = MagicMock()
        mock_client.create_collection.side_effect = [
            mock_new_collection, mock_final_collection
        ]
        mock_client_fn.return_value = mock_client

        with patch("kb_index_sync.STATE_PATH", state_path):
            result = force_rebuild(canonical_dir)

        assert result["total"] == 5
        assert mock_client.create_collection.call_count == 2

    @patch("kb_index_sync._embed_and_upsert", return_value=0)
    @patch("kb_index_sync._get_embedding_function")
    @patch("kb_index_sync._get_chroma_client")
    def test_force_rebuild_empty_aborts(self, mock_client_fn, mock_ef_fn, mock_upsert,
                                        canonical_dir, sample_wms_entries, tmp_path):
        """If new collection is empty, don't swap."""
        _write_canonical(canonical_dir.parent, "RFP_Database_WMS_CANONICAL.json",
                         sample_wms_entries)
        state_path = tmp_path / "file_state.json"

        mock_new_collection = MagicMock()
        mock_new_collection.count.return_value = 0  # Empty!

        mock_client = MagicMock()
        mock_client.create_collection.return_value = mock_new_collection
        mock_client_fn.return_value = mock_client

        with patch("kb_index_sync.STATE_PATH", state_path):
            result = force_rebuild(canonical_dir)

        assert result.get("error") == "empty_new_collection"
        # Old collection should NOT be deleted
        mock_client.delete_collection.assert_called_once()  # Only the temp one

    @patch("kb_index_sync._embed_and_upsert", return_value=3)
    @patch("kb_index_sync._get_embedding_function")
    @patch("kb_index_sync._get_chroma_client")
    def test_force_rebuild_validates_before_swap(self, mock_client_fn, mock_ef_fn,
                                                  mock_upsert, canonical_dir,
                                                  sample_wms_entries, tmp_path):
        """New collection count is checked before swap."""
        _write_canonical(canonical_dir.parent, "RFP_Database_WMS_CANONICAL.json",
                         sample_wms_entries)
        state_path = tmp_path / "file_state.json"

        mock_new_collection = MagicMock()
        mock_new_collection.count.return_value = 3
        mock_new_collection.get.return_value = {
            "ids": ["a"], "embeddings": [[0.1]],
            "documents": ["d"], "metadatas": [{"k": "v"}],
        }
        mock_final = MagicMock()
        mock_final.count.return_value = 3

        mock_client = MagicMock()
        mock_client.create_collection.side_effect = [mock_new_collection, mock_final]
        mock_client_fn.return_value = mock_client

        with patch("kb_index_sync.STATE_PATH", state_path):
            result = force_rebuild(canonical_dir)

        # count() called on new collection before swap
        mock_new_collection.count.assert_called()
        assert result["total"] == 3

    @patch("kb_index_sync._embed_and_upsert", return_value=3)
    @patch("kb_index_sync._get_embedding_function")
    @patch("kb_index_sync._get_chroma_client")
    def test_force_rebuild_old_collection_survives_error(self, mock_client_fn, mock_ef_fn,
                                                          mock_upsert, canonical_dir,
                                                          sample_wms_entries, tmp_path):
        """If old collection delete raises, rebuild still proceeds."""
        _write_canonical(canonical_dir.parent, "RFP_Database_WMS_CANONICAL.json",
                         sample_wms_entries)
        state_path = tmp_path / "file_state.json"

        mock_new_collection = MagicMock()
        mock_new_collection.count.return_value = 3
        mock_new_collection.get.return_value = {
            "ids": ["a"], "embeddings": [[0.1]],
            "documents": ["d"], "metadatas": [{"k": "v"}],
        }
        mock_final = MagicMock()
        mock_final.count.return_value = 3

        mock_client = MagicMock()
        mock_client.create_collection.side_effect = [mock_new_collection, mock_final]
        # delete_collection raises for old, succeeds for temp
        mock_client.delete_collection.side_effect = [ValueError("no such collection"), None]
        mock_client_fn.return_value = mock_client

        with patch("kb_index_sync.STATE_PATH", state_path):
            result = force_rebuild(canonical_dir)

        assert result["total"] == 3

    @patch("kb_index_sync._embed_and_upsert", return_value=5)
    @patch("kb_index_sync._get_embedding_function")
    @patch("kb_index_sync._get_chroma_client")
    def test_force_rebuild_saves_state(self, mock_client_fn, mock_ef_fn, mock_upsert,
                                        canonical_dir, sample_wms_entries, tmp_path):
        """file_state.json written after successful rebuild."""
        _write_canonical(canonical_dir.parent, "RFP_Database_WMS_CANONICAL.json",
                         sample_wms_entries)
        state_path = tmp_path / "file_state.json"

        mock_new = MagicMock()
        mock_new.count.return_value = 5
        mock_new.get.return_value = {
            "ids": ["a"], "embeddings": [[0.1]],
            "documents": ["d"], "metadatas": [{"k": "v"}],
        }
        mock_final = MagicMock()
        mock_final.count.return_value = 5

        mock_client = MagicMock()
        mock_client.create_collection.side_effect = [mock_new, mock_final]
        mock_client_fn.return_value = mock_client

        with patch("kb_index_sync.STATE_PATH", state_path):
            force_rebuild(canonical_dir)

        assert state_path.exists()
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        assert saved["embedding_model"] == EMBEDDING_MODEL
        assert "RFP_Database_WMS_CANONICAL.json" in saved["files"]

    def test_force_rebuild_dry_run(self, canonical_dir, sample_wms_entries, tmp_path):
        """Dry run shows entry counts without touching ChromaDB."""
        _write_canonical(canonical_dir.parent, "RFP_Database_WMS_CANONICAL.json",
                         sample_wms_entries)
        result = force_rebuild(canonical_dir, dry_run=True)
        assert result["dry_run"] is True
        assert result["total_entries"] == 2


# ===================================================================
# Edge cases
# ===================================================================

class TestEdgeCases:

    def test_empty_canonical_dir(self, tmp_path):
        """No canonical files -> nothing to sync."""
        empty_dir = tmp_path / "canonical"
        empty_dir.mkdir()
        state_path = tmp_path / "file_state.json"
        with patch("kb_index_sync.STATE_PATH", state_path):
            result = sync(empty_dir)
        assert result.get("unchanged", 0) == 0

    def test_entry_with_empty_question(self, canonical_dir, tmp_path):
        """Entries with no question text get empty-string ID (still deterministic)."""
        entries = [{"kb_id": "kb_0001", "domain": "planning",
                    "canonical_question": "", "canonical_answer": "some answer"}]
        _write_canonical(canonical_dir.parent, "RFP_Database_Planning_CANONICAL.json", entries)
        vid = make_vector_id("RFP_Database_Planning_CANONICAL.json", "")
        assert len(vid) == 16  # still produces a valid hash

    def test_mixed_v1_v2_entries(self, canonical_dir, tmp_path):
        """File with both v1 and v2 entries handles both correctly."""
        mixed = [_make_v1_entry(), _make_v2_entry()]
        fp = _write_canonical(canonical_dir.parent, "RFP_Database_Mixed_CANONICAL.json", mixed)
        entries = load_entries(fp)
        assert len(entries) == 2
        assert _get_question(entries[0]) == "What is Blue Yonder?"
        assert _get_question(entries[1]) == "How does WMS handle picking?"
