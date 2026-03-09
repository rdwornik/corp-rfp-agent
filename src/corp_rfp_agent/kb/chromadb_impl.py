"""ChromaDB implementation of KBClient."""

import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from corp_rfp_agent.core.types import KBMatch
from corp_rfp_agent.kb.entry import KBEntry

logger = logging.getLogger(__name__)

# Add legacy src/ to path for ChromaDB config constants
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SRC_DIR = str(_PROJECT_ROOT / "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

_DEFAULT_CHROMA_PATH = str(_PROJECT_ROOT / "data" / "kb" / "chroma_store")
_DEFAULT_KB_PATH = _PROJECT_ROOT / "data" / "kb" / "canonical" / "RFP_Database_UNIFIED_CANONICAL.json"
_COLLECTION_NAME = "rfp_knowledge_base"
_EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"
_BATCH_SIZE = 100


class ChromaKBClient:
    """KBClient backed by existing ChromaDB + BGE embeddings."""

    def __init__(
        self,
        chroma_path: Optional[str] = None,
        kb_json_path: Optional[Path] = None,
        create_if_missing: bool = False,
    ):
        """Initialize ChromaDB connection.

        Args:
            chroma_path: Path to ChromaDB persistent storage
            kb_json_path: Path to unified canonical JSON for full-answer lookup
            create_if_missing: Create collection if it doesn't exist
        """
        import chromadb
        from chromadb.utils import embedding_functions

        db_path = chroma_path or _DEFAULT_CHROMA_PATH
        self._chroma_path = db_path
        self._client = chromadb.PersistentClient(path=db_path)
        self._ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=_EMBEDDING_MODEL
        )

        if create_if_missing:
            self._collection = self._client.get_or_create_collection(
                name=_COLLECTION_NAME,
                embedding_function=self._ef,
            )
        else:
            self._collection = self._client.get_collection(
                name=_COLLECTION_NAME,
                embedding_function=self._ef,
            )
        logger.info("ChromaDB connected (%d entries)", self._collection.count())

        # Load full KB for answer lookup (ChromaDB metadata truncates long answers)
        kb_path = kb_json_path or _DEFAULT_KB_PATH
        self._kb_lookup: dict[str, dict] = {}
        if kb_path.exists():
            with open(kb_path, encoding="utf-8") as f:
                for item in json.load(f):
                    kb_id = item.get("kb_id", item.get("id", ""))
                    domain = item.get("domain", item.get("family_code", ""))
                    if kb_id:
                        self._kb_lookup[kb_id] = item
                        if domain and not kb_id.startswith(f"{domain}_"):
                            self._kb_lookup[f"{domain}_{kb_id}"] = item

        # Lazy-loaded override store
        self._override_store = None

    def _get_override_store(self):
        """Lazy-load override store for applying text overrides to answers."""
        if self._override_store is None:
            try:
                from corp_rfp_agent.overrides.store import YAMLOverrideStore
                overrides_path = Path(self._chroma_path).parent.parent.parent / "config" / "overrides.yaml"
                if overrides_path.exists():
                    self._override_store = YAMLOverrideStore(yaml_path=overrides_path)
                else:
                    self._override_store = False  # Sentinel: tried, not found
            except Exception:
                self._override_store = False
        return self._override_store if self._override_store is not False else None

    @staticmethod
    def _recency_boost(date_str: str) -> float:
        """Newer entries score higher. Returns 0.0-0.10 boost."""
        if not date_str:
            return 0.0
        try:
            entry_date = datetime.strptime(date_str[:10], "%Y-%m-%d").date()
            days_old = (date.today() - entry_date).days
            if days_old <= 180:
                return 0.10  # Last 6 months
            elif days_old <= 365:
                return 0.05  # Last 12 months
            elif days_old <= 730:
                return 0.02  # Last 2 years
            return 0.0
        except (ValueError, TypeError):
            return 0.0

    def query(
        self,
        question: str,
        *,
        family: Optional[str] = None,
        category: Optional[str] = None,
        top_k: int = 5,
        threshold: float = 0.75,
    ) -> list[KBMatch]:
        """Query ChromaDB for similar entries with recency re-ranking."""
        # Over-fetch for re-ranking
        fetch_k = top_k * 3
        results = self._collection.query(query_texts=[question], n_results=fetch_k)

        matches = []
        if results["ids"] and results["ids"][0]:
            ids = results["ids"][0]
            distances = results["distances"][0] if "distances" in results else [1.0] * len(ids)

            for chroma_id, dist in zip(ids, distances):
                similarity = 1.0 - dist
                if similarity < threshold:
                    continue

                item = self._kb_lookup.get(chroma_id, {})
                answer = item.get("canonical_answer", item.get("answer", ""))
                date_str = item.get("last_updated", "")
                boosted = similarity + self._recency_boost(date_str)

                matches.append(KBMatch(
                    entry_id=chroma_id,
                    question=item.get("canonical_question", item.get("question", "")),
                    answer=answer,
                    similarity=boosted,
                    family_code=item.get("domain", item.get("family_code", "")),
                    category=item.get("category", ""),
                    metadata=item,
                ))

        # Re-rank by boosted similarity
        matches.sort(key=lambda m: m.similarity, reverse=True)

        # Apply overrides to retrieved answers
        store = self._get_override_store()
        if store:
            for match in matches:
                result = store.apply(match.answer, family=family)
                if result.changed:
                    match.answer = result.modified

        # Boost family matches to the front
        if family:
            family_hits = [m for m in matches if m.family_code == family]
            platform_hits = [m for m in matches
                             if m.metadata.get("scope") == "platform"
                             or m.family_code == "platform"]
            other_hits = [m for m in matches
                          if m not in family_hits and m not in platform_hits]
            matches = family_hits + platform_hits + other_hits

        return matches[:top_k]

    def upsert(self, entries: list[KBEntry]) -> int:
        """Add or update entries in ChromaDB.

        For each entry, embeds question text and stores answer + metadata.
        Returns count of upserted entries.
        """
        ids = []
        documents = []
        metadatas = []

        for entry in entries:
            if not entry.is_valid():
                continue

            # Build ChromaDB ID
            domain = entry.family_code or "unknown"
            chroma_id = entry.id if entry.id else f"{domain}_{hash(entry.question)}"
            if not chroma_id.startswith(f"{domain}_"):
                chroma_id = f"{domain}_{chroma_id}"

            # Build document text for embedding
            doc_text = entry.question
            if entry.question_variants:
                doc_text += " " + " ".join(entry.question_variants)

            # Metadata (ChromaDB truncates, but useful for filtering)
            meta = {
                "kb_id": entry.id,
                "domain": entry.family_code,
                "category": entry.category,
                "subcategory": entry.subcategory,
                "canonical_question": entry.question[:500],
                "canonical_answer": entry.answer[:1000],
                "last_updated": entry.last_updated,
            }

            ids.append(chroma_id)
            documents.append(doc_text)
            metadatas.append(meta)

        # Batch upsert
        total = 0
        for i in range(0, len(ids), _BATCH_SIZE):
            batch_ids = ids[i:i + _BATCH_SIZE]
            batch_docs = documents[i:i + _BATCH_SIZE]
            batch_metas = metadatas[i:i + _BATCH_SIZE]
            self._collection.upsert(
                ids=batch_ids,
                documents=batch_docs,
                metadatas=batch_metas,
            )
            total += len(batch_ids)

        logger.info("Upserted %d entries to ChromaDB", total)
        return total

    def rebuild(self, entries: list[KBEntry]) -> int:
        """Drop collection and rebuild from scratch."""
        # Delete existing collection
        try:
            self._client.delete_collection(name=_COLLECTION_NAME)
        except Exception:
            pass

        # Create new collection
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            embedding_function=self._ef,
        )

        # Upsert all entries
        return self.upsert(entries)

    def count(self, family: Optional[str] = None) -> int:
        """Count entries in ChromaDB."""
        if family:
            return sum(1 for item in self._kb_lookup.values()
                       if item.get("domain", item.get("family_code")) == family)
        return self._collection.count()

    def families(self) -> dict[str, int]:
        """Count entries per family."""
        counts: dict[str, int] = {}
        for item in self._kb_lookup.values():
            domain = item.get("domain", item.get("family_code", "unknown"))
            counts[domain] = counts.get(domain, 0) + 1
        return counts

    def delete_by_family(self, family: str) -> int:
        """Delete all entries for a family.

        Returns count of deleted entries.
        """
        # Find all IDs for this family
        to_delete = []
        for chroma_id, item in self._kb_lookup.items():
            if item.get("domain", item.get("family_code")) == family:
                to_delete.append(chroma_id)

        if to_delete:
            # ChromaDB delete in batches
            for i in range(0, len(to_delete), _BATCH_SIZE):
                batch = to_delete[i:i + _BATCH_SIZE]
                self._collection.delete(ids=batch)

            # Clean up lookup
            for cid in to_delete:
                self._kb_lookup.pop(cid, None)

        logger.info("Deleted %d entries for family %s", len(to_delete), family)
        return len(to_delete)
