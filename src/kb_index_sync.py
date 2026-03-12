"""ChromaDB index sync -- incremental by default, Blue/Green full rebuild on demand.

Replaces manual kb_embed_chroma.py runs. Tracks file hashes in file_state.json
so only changed/new canonical files get re-embedded.

Usage:
  python src/kb_index_sync.py              # incremental sync
  python src/kb_index_sync.py --dry-run    # show what would change
  python src/kb_index_sync.py --force-rebuild  # full Blue/Green rebuild
"""

import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DIR = PROJECT_ROOT / "data" / "kb" / "canonical"
CHROMA_DIR = str(PROJECT_ROOT / "data" / "kb" / "chroma_store")
STATE_PATH = PROJECT_ROOT / "data" / "kb" / "file_state.json"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
COLLECTION_NAME = "rfp_knowledge_base"
EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"
CHUNKING_VERSION = "1"
BATCH_SIZE = 100

# ---------------------------------------------------------------------------
# Helpers: file state
# ---------------------------------------------------------------------------

def load_file_state() -> dict:
    """Load previous file_state.json, or return empty state."""
    if not STATE_PATH.exists():
        return {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        print("[WARN] Corrupted file_state.json -- treating as first run")
        return {}


def save_file_state(manifest: dict) -> None:
    """Atomic write: tmp file -> rename."""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = STATE_PATH.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    # On Windows, rename fails if target exists -- remove first
    if STATE_PATH.exists():
        STATE_PATH.unlink()
    tmp_path.rename(STATE_PATH)


def hash_file(filepath: Path) -> str:
    """SHA-256 hex digest of file contents."""
    return hashlib.sha256(filepath.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Helpers: canonical entries
# ---------------------------------------------------------------------------

def discover_canonical_files(canonical_dir: Path = CANONICAL_DIR) -> list[Path]:
    """Find all per-family canonical files (skip UNIFIED)."""
    files = []
    for f in sorted(canonical_dir.glob("RFP_Database_*_CANONICAL.json")):
        if "UNIFIED" in f.name:
            continue
        files.append(f)
    return files


def load_entries(filepath: Path) -> list[dict]:
    """Load entries from a canonical JSON file."""
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_question(entry: dict) -> str:
    """Extract question text from v1 or v2 entry."""
    return entry.get("canonical_question") or entry.get("question") or ""


def _get_answer(entry: dict) -> str:
    """Extract answer text from v1 or v2 entry."""
    return entry.get("canonical_answer") or entry.get("answer") or ""


def _get_domain(entry: dict) -> str:
    """Extract domain/family from v1 or v2 entry."""
    return entry.get("family_code") or entry.get("domain") or ""


def _get_entry_id(entry: dict) -> str:
    """Extract entry ID from v1 or v2."""
    return entry.get("id") or entry.get("kb_id") or ""


def _make_search_doc(entry: dict) -> str:
    """Build the document text for embedding (same logic as kb_embed_chroma)."""
    blob = entry.get("search_blob", "")
    if blob:
        return blob
    return f"{_get_question(entry)} {_get_answer(entry)}"


def make_vector_id(filename: str, question: str) -> str:
    """Deterministic ID: hash of (filename + question)."""
    return hashlib.sha256(f"{filename}:{question}".encode()).hexdigest()[:16]


def _build_metadata(entry: dict, source_file: str) -> dict:
    """Build ChromaDB metadata dict for one entry."""
    answer = _get_answer(entry)
    safe_answer = (answer[:1000] + "...") if len(answer) > 1000 else answer
    return {
        "source_file": source_file,
        "kb_id": str(_get_entry_id(entry)),
        "domain": str(_get_domain(entry)),
        "category": str(entry.get("category", "")),
        "subcategory": str(entry.get("subcategory", "")),
        "canonical_question": str(_get_question(entry)),
        "canonical_answer": safe_answer,
        "last_updated": str(entry.get("last_updated", "")),
    }


# ---------------------------------------------------------------------------
# Helpers: ChromaDB + embeddings
# ---------------------------------------------------------------------------

def _get_chroma_client():
    """Create ChromaDB PersistentClient."""
    import chromadb
    return chromadb.PersistentClient(path=CHROMA_DIR)


def _get_embedding_function():
    """Create SentenceTransformer embedding function."""
    from chromadb.utils import embedding_functions
    return embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBEDDING_MODEL
    )


def _get_or_create_collection(client=None, name=None, ef=None):
    """Get existing collection or create new one."""
    client = client or _get_chroma_client()
    ef = ef or _get_embedding_function()
    name = name or COLLECTION_NAME
    return client.get_or_create_collection(name=name, embedding_function=ef)


def _embed_and_upsert(collection, filepath: Path, entries: list[dict]) -> int:
    """Embed entries from one file and upsert into collection. Returns count."""
    filename = filepath.name
    ids = []
    documents = []
    metadatas = []

    for entry in entries:
        doc_text = _make_search_doc(entry)
        if not doc_text.strip():
            continue

        question = _get_question(entry)
        ids.append(make_vector_id(filename, question))
        documents.append(doc_text)
        metadatas.append(_build_metadata(entry, filename))

    if not ids:
        return 0

    for start in range(0, len(ids), BATCH_SIZE):
        end = min(start + BATCH_SIZE, len(ids))
        collection.upsert(
            ids=ids[start:end],
            documents=documents[start:end],
            metadatas=metadatas[start:end],
        )

    return len(ids)


# ---------------------------------------------------------------------------
# Delta computation
# ---------------------------------------------------------------------------

def compute_delta(
    canonical_dir: Path = CANONICAL_DIR,
    prev_state: dict | None = None,
) -> dict:
    """Compute what changed since last sync.

    Returns dict with keys: added, changed, deleted, unchanged, new_hashes,
    and model_changed flag.
    """
    if prev_state is None:
        prev_state = load_file_state()

    prev_files = prev_state.get("files", {})

    # Check embedding model version
    model_changed = bool(
        prev_state.get("embedding_model")
        and prev_state["embedding_model"] != EMBEDDING_MODEL
    )

    # Hash all current canonical files
    new_hashes: dict[str, str] = {}
    for f in discover_canonical_files(canonical_dir):
        new_hashes[f.name] = hash_file(f)

    added = []
    changed = []
    unchanged = []

    for filename, new_hash in new_hashes.items():
        prev_info = prev_files.get(filename, {})
        prev_hash = prev_info.get("hash")
        if prev_hash is None:
            added.append(filename)
        elif prev_hash != new_hash:
            changed.append(filename)
        else:
            unchanged.append(filename)

    deleted = [fn for fn in prev_files if fn not in new_hashes]

    return {
        "added": added,
        "changed": changed,
        "deleted": deleted,
        "unchanged": unchanged,
        "new_hashes": new_hashes,
        "model_changed": model_changed,
    }


# ---------------------------------------------------------------------------
# Incremental sync
# ---------------------------------------------------------------------------

def sync(canonical_dir: Path = CANONICAL_DIR, dry_run: bool = False) -> dict:
    """Incremental sync -- only re-embed changed/new files.

    Returns summary dict with counts.
    """
    prev_state = load_file_state()
    delta = compute_delta(canonical_dir, prev_state)

    # Model version mismatch
    if delta["model_changed"]:
        print("[WARN] Embedding model changed -- full rebuild required")
        print("       Run: python src/kb_index_sync.py --force-rebuild")
        return {"error": "model_changed"}

    added = delta["added"]
    changed = delta["changed"]
    deleted = delta["deleted"]
    unchanged = delta["unchanged"]

    if not added and not changed and not deleted:
        print("[OK] ChromaDB is up to date. No changes detected.")
        return {"added": 0, "changed": 0, "deleted": 0, "unchanged": len(unchanged)}

    print(f"  Added:     {len(added)} files")
    print(f"  Changed:   {len(changed)} files")
    print(f"  Deleted:   {len(deleted)} files")
    print(f"  Unchanged: {len(unchanged)} files")

    if dry_run:
        for fn in added:
            print(f"    [ADD] {fn}")
        for fn in changed:
            print(f"    [UPD] {fn}")
        for fn in deleted:
            print(f"    [DEL] {fn}")
        return {
            "added": len(added), "changed": len(changed),
            "deleted": len(deleted), "unchanged": len(unchanged),
            "dry_run": True,
        }

    # Connect to ChromaDB
    client = _get_chroma_client()
    ef = _get_embedding_function()
    collection = _get_or_create_collection(client, COLLECTION_NAME, ef)

    # Delete vectors for changed + deleted files
    for filename in changed + deleted:
        try:
            collection.delete(where={"source_file": filename})
            print(f"  [DEL] Removed vectors for {filename}")
        except Exception as e:
            print(f"  [WARN] Delete failed for {filename}: {e}")

    # Embed + upsert added + changed files
    total_upserted = 0
    for filename in added + changed:
        filepath = canonical_dir / filename
        entries = load_entries(filepath)
        count = _embed_and_upsert(collection, filepath, entries)
        total_upserted += count
        print(f"  [ADD] {filename}: {count} entries indexed")

    # Build and save new manifest
    now_iso = datetime.now().isoformat()
    new_manifest = {
        "version": "1.0",
        "embedding_model": EMBEDDING_MODEL,
        "chunking_version": CHUNKING_VERSION,
        "last_sync": now_iso,
        "collection_name": COLLECTION_NAME,
        "files": {},
    }
    for filename, file_hash in delta["new_hashes"].items():
        entries = load_entries(canonical_dir / filename)
        new_manifest["files"][filename] = {
            "hash": file_hash,
            "entry_count": len(entries),
            "last_synced": now_iso,
        }

    save_file_state(new_manifest)

    # Validate
    chroma_count = collection.count()
    manifest_count = sum(fi["entry_count"] for fi in new_manifest["files"].values())
    if abs(chroma_count - manifest_count) > manifest_count * 0.05:
        print(f"  [WARN] Count mismatch: ChromaDB={chroma_count}, Manifest={manifest_count}")
        print("         Consider: python src/kb_index_sync.py --force-rebuild")

    print(f"\n[OK] Sync complete: {total_upserted} upserted, "
          f"{len(deleted)} files removed, {len(unchanged)} unchanged")
    print(f"     ChromaDB total: {chroma_count} vectors")

    return {
        "added": len(added), "changed": len(changed),
        "deleted": len(deleted), "unchanged": len(unchanged),
        "total_upserted": total_upserted, "chroma_count": chroma_count,
    }


# ---------------------------------------------------------------------------
# Blue/Green full rebuild
# ---------------------------------------------------------------------------

def force_rebuild(canonical_dir: Path = CANONICAL_DIR, dry_run: bool = False) -> dict:
    """Blue/Green full rebuild -- build new collection, validate, then swap.

    NEVER leaves the live collection empty.
    """
    files = discover_canonical_files(canonical_dir)

    if dry_run:
        print("[DRY RUN] Would do full Blue/Green rebuild")
        total = 0
        for filepath in files:
            entries = load_entries(filepath)
            print(f"    {filepath.name}: {len(entries)} entries")
            total += len(entries)
        print(f"    Total: {total} entries")
        return {"dry_run": True, "files": len(files), "total_entries": total}

    client = _get_chroma_client()
    ef = _get_embedding_function()

    # 1. Create NEW collection (don't touch the live one)
    new_name = f"rfp_kb_next_{int(time.time())}"
    new_collection = client.create_collection(name=new_name, embedding_function=ef)
    print(f"[INFO] Building new collection: {new_name}")

    # 2. Embed ALL entries into new collection
    total = 0
    new_file_state: dict[str, dict] = {}
    now_iso = datetime.now().isoformat()

    for filepath in files:
        entries = load_entries(filepath)
        file_hash = hash_file(filepath)
        count = _embed_and_upsert(new_collection, filepath, entries)
        total += count
        new_file_state[filepath.name] = {
            "hash": file_hash,
            "entry_count": len(entries),
            "last_synced": now_iso,
        }
        print(f"  [OK] {filepath.name}: {count} entries")

    # 3. Validate new collection BEFORE swap
    new_count = new_collection.count()
    print(f"\n[INFO] New collection: {new_count} vectors")

    if new_count == 0:
        print("[ERROR] New collection is empty! Aborting swap.")
        client.delete_collection(new_name)
        return {"error": "empty_new_collection"}

    # 4. Swap: delete old, rebuild with correct name
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass  # Old collection might not exist

    final_collection = client.create_collection(
        name=COLLECTION_NAME, embedding_function=ef
    )

    # Copy all from new to final (ChromaDB has no rename)
    all_data = new_collection.get(include=["embeddings", "documents", "metadatas"])
    if all_data["ids"]:
        for start in range(0, len(all_data["ids"]), BATCH_SIZE):
            end = min(start + BATCH_SIZE, len(all_data["ids"]))
            final_collection.upsert(
                ids=all_data["ids"][start:end],
                embeddings=all_data["embeddings"][start:end],
                documents=all_data["documents"][start:end],
                metadatas=all_data["metadatas"][start:end],
            )

    # Delete temp collection
    client.delete_collection(new_name)

    # 5. Write file_state.json
    manifest = {
        "version": "1.0",
        "embedding_model": EMBEDDING_MODEL,
        "chunking_version": CHUNKING_VERSION,
        "last_sync": now_iso,
        "collection_name": COLLECTION_NAME,
        "files": new_file_state,
    }
    save_file_state(manifest)

    final_count = final_collection.count()
    print(f"\n[OK] Full rebuild complete: {total} entries indexed")
    print(f"     Collection: {COLLECTION_NAME} ({final_count} vectors)")

    return {"total": total, "chroma_count": final_count}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Sync ChromaDB index with canonical KB files.",
        epilog=(
            "Default: incremental sync (only changed files).\n"
            "--force-rebuild: full Blue/Green rebuild (safe but slow)."
        ),
    )
    parser.add_argument("--force-rebuild", action="store_true",
                        help="Full Blue/Green rebuild (safe, but slow)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without modifying ChromaDB")
    parser.add_argument("--canonical-dir", type=str, default=None,
                        help="Override canonical directory path")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose output")
    args = parser.parse_args()

    canon_dir = Path(args.canonical_dir) if args.canonical_dir else CANONICAL_DIR

    if args.force_rebuild:
        force_rebuild(canon_dir, dry_run=args.dry_run)
    else:
        sync(canon_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
