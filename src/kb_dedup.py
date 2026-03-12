"""KB Deduplication -- semantic dedup using BGE embeddings.

Clusters near-duplicate entries by cosine similarity, picks the best
entry per cluster, absorbs removed entries' questions as question_variants.

Usage:
    python src/kb_dedup.py --dry-run --threshold 0.85
    python src/kb_dedup.py --threshold 0.85
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DIR = PROJECT_ROOT / "data" / "kb" / "canonical"
DEDUP_REPORT_PATH = PROJECT_ROOT / "data" / "kb" / "dedup_report.json"


def load_all_entries(canonical_dir: Path) -> list[dict]:
    """Load all canonical entries from JSON files (skip UNIFIED)."""
    entries = []
    for f in sorted(canonical_dir.glob("*.json")):
        if "UNIFIED" in f.name:
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                data = json.load(fh)
            for item in data:
                item["_source_file"] = f.name
            entries.extend(data)
        except Exception as e:
            print(f"[WARNING] Skipping {f.name}: {e}")
    return entries


def get_question_text(entry: dict) -> str:
    """Extract question text from entry (v1 or v2 schema)."""
    return entry.get("canonical_question", entry.get("question", "")).strip()


def get_answer_text(entry: dict) -> str:
    """Extract answer text from entry (v1 or v2 schema)."""
    return entry.get("canonical_answer", entry.get("answer", "")).strip()


def score_entry(entry: dict) -> tuple:
    """Higher = better entry to keep."""
    answer_len = len(get_answer_text(entry))
    is_v2 = 1 if entry.get("id", "") else 0
    confidence_score = {"verified": 4, "draft": 2, "needs_review": 1, "outdated": 0}.get(
        entry.get("confidence", "draft"), 2
    )
    date_str = entry.get("last_updated", "2020-01-01")
    has_variants = 1 if entry.get("question_variants") else 0

    return (confidence_score, is_v2, has_variants, answer_len, date_str)


def embed_questions(questions: list[str]) -> np.ndarray:
    """Embed questions using BGE-large-en-v1.5."""
    from sentence_transformers import SentenceTransformer

    print("[INFO] Loading BGE-large-en-v1.5 embeddings model...")
    model = SentenceTransformer("BAAI/bge-large-en-v1.5")
    print(f"[INFO] Embedding {len(questions)} questions...")
    embeddings = model.encode(questions, show_progress_bar=True, normalize_embeddings=True)
    return np.array(embeddings)


def find_duplicates(embeddings: np.ndarray, threshold: float) -> list[tuple[int, int, float]]:
    """Find all pairs with cosine similarity >= threshold."""
    # Normalized embeddings -> cosine = dot product
    sim_matrix = embeddings @ embeddings.T

    pairs = []
    n = len(embeddings)
    for i in range(n):
        for j in range(i + 1, n):
            if sim_matrix[i, j] >= threshold:
                pairs.append((i, j, float(sim_matrix[i, j])))
    return pairs


def cluster_duplicates(pairs: list[tuple[int, int, float]], n: int) -> list[list[int]]:
    """Union-find clustering from duplicate pairs."""
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i, j, _ in pairs:
        union(i, j)

    clusters: dict[int, list[int]] = {}
    for idx in range(n):
        root = find(idx)
        clusters.setdefault(root, []).append(idx)

    # Only return clusters with 2+ entries
    return [members for members in clusters.values() if len(members) >= 2]


def deduplicate(entries: list[dict], threshold: float, dry_run: bool = True) -> dict:
    """Run full dedup pipeline.

    Returns report dict with keep/remove lists and cluster details.
    """
    questions = [get_question_text(e) for e in entries]
    embeddings = embed_questions(questions)

    print(f"[INFO] Finding duplicates (threshold={threshold})...")
    pairs = find_duplicates(embeddings, threshold)
    print(f"[INFO] Found {len(pairs)} duplicate pairs")

    clusters = cluster_duplicates(pairs, len(entries))
    print(f"[INFO] Formed {len(clusters)} duplicate clusters")

    keep_indices = set(range(len(entries)))
    remove_indices = set()
    cluster_details = []

    for cluster_members in clusters:
        # Score each entry in cluster
        scored = [(idx, score_entry(entries[idx])) for idx in cluster_members]
        scored.sort(key=lambda x: x[1], reverse=True)

        winner_idx = scored[0][0]
        losers = [idx for idx, _ in scored[1:]]

        # Absorb loser questions as variants on the winner
        winner = entries[winner_idx]
        existing_variants = list(winner.get("question_variants", []))
        for loser_idx in losers:
            loser_q = get_question_text(entries[loser_idx])
            if loser_q and loser_q not in existing_variants:
                existing_variants.append(loser_q)
            remove_indices.add(loser_idx)
            keep_indices.discard(loser_idx)

        winner["question_variants"] = existing_variants

        # Compute average similarity within cluster
        cluster_sims = [
            sim for i, j, sim in pairs
            if i in cluster_members and j in cluster_members
        ]
        avg_sim = sum(cluster_sims) / len(cluster_sims) if cluster_sims else 0.0

        cluster_details.append({
            "keep": {
                "index": winner_idx,
                "question": get_question_text(entries[winner_idx])[:100],
                "answer_len": len(get_answer_text(entries[winner_idx])),
                "id": entries[winner_idx].get("kb_id", entries[winner_idx].get("id", "")),
            },
            "remove": [
                {
                    "index": idx,
                    "question": get_question_text(entries[idx])[:100],
                    "answer_len": len(get_answer_text(entries[idx])),
                    "id": entries[idx].get("kb_id", entries[idx].get("id", "")),
                }
                for idx in losers
            ],
            "avg_similarity": round(avg_sim, 3),
            "size": len(cluster_members),
        })

    report = {
        "timestamp": datetime.now().isoformat(),
        "threshold": threshold,
        "total_entries": len(entries),
        "duplicate_clusters": len(clusters),
        "entries_to_keep": len(keep_indices),
        "entries_to_remove": len(remove_indices),
        "questions_absorbed_as_variants": len(remove_indices),
        "clusters": sorted(cluster_details, key=lambda c: -c["avg_similarity"]),
        "dry_run": dry_run,
    }

    return report


def print_report(report: dict) -> None:
    """Print dedup report to console."""
    border = "=" * 55
    print(f"\n{border}")
    title = "KB Deduplication -- Dry Run" if report["dry_run"] else "KB Deduplication -- EXECUTED"
    print(f"  {title}")
    print(f"{border}")
    print(f"  Total entries:                {report['total_entries']:>6}")
    print(f"  Duplicate clusters:           {report['duplicate_clusters']:>6}")
    print(f"  Entries to KEEP:              {report['entries_to_keep']:>6}")
    print(f"  Entries to REMOVE:            {report['entries_to_remove']:>6}")
    print(f"  Questions absorbed as variants:{report['questions_absorbed_as_variants']:>5}")
    print(f"{border}\n")

    # Show sample clusters
    for i, cluster in enumerate(report["clusters"][:10]):
        print(f"Cluster {i+1} (similarity: {cluster['avg_similarity']:.2f}, size: {cluster['size']}):")
        keep = cluster["keep"]
        print(f"  [KEEP]   {keep['id']}: \"{keep['question']}\"")
        print(f"           {keep['answer_len']} chars")
        for rem in cluster["remove"]:
            print(f"  [REMOVE] {rem['id']}: \"{rem['question']}\"")
            print(f"           {rem['answer_len']} chars")
        print()

    if len(report["clusters"]) > 10:
        print(f"  ... and {len(report['clusters']) - 10} more clusters")


def apply_dedup(entries: list[dict], report: dict) -> list[dict]:
    """Apply dedup: return only kept entries (with absorbed variants)."""
    remove_set = set()
    for cluster in report["clusters"]:
        for rem in cluster["remove"]:
            remove_set.add(rem["index"])

    return [e for i, e in enumerate(entries) if i not in remove_set]


def save_deduplicated(entries: list[dict], canonical_dir: Path) -> None:
    """Save deduplicated entries back to canonical files."""
    # Group by source file
    by_file: dict[str, list[dict]] = {}
    for entry in entries:
        source = entry.pop("_source_file", "unknown.json")
        by_file.setdefault(source, []).append(entry)

    for filename, file_entries in by_file.items():
        out_path = canonical_dir / filename
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(file_entries, f, indent=2, ensure_ascii=False)
        print(f"  [OK] {filename}: {len(file_entries)} entries")


def main():
    parser = argparse.ArgumentParser(description="KB Deduplication using BGE semantic similarity")
    parser.add_argument("--threshold", type=float, default=0.85,
                        help="Cosine similarity threshold for duplicates (default: 0.85)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be removed without changing files")
    parser.add_argument("--canonical-dir", type=str, default=None,
                        help="Path to canonical directory")
    args = parser.parse_args()

    canonical_dir = Path(args.canonical_dir) if args.canonical_dir else CANONICAL_DIR
    entries = load_all_entries(canonical_dir)

    if not entries:
        print("[ERROR] No entries found.")
        sys.exit(1)

    print(f"[INFO] Loaded {len(entries)} entries from {canonical_dir}")

    report = deduplicate(entries, args.threshold, dry_run=args.dry_run)
    print_report(report)

    # Save report
    report_path = Path(args.canonical_dir).parent / "dedup_report.json" if args.canonical_dir else DEDUP_REPORT_PATH
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"[OK] Report saved to {report_path}")

    if not args.dry_run:
        print("\n[INFO] Applying dedup...")
        kept = apply_dedup(entries, report)
        save_deduplicated(kept, canonical_dir)
        print(f"\n[DONE] {len(kept)} entries saved. {report['entries_to_remove']} removed.")

        # Rebuild UNIFIED canonical from deduped per-family files
        print("\n[INFO] Rebuilding UNIFIED canonical...")
        try:
            from kb_merge_canonical import merge
            merge()
        except Exception as e:
            print(f"[WARNING] Auto-merge failed: {e}")
            print("  Run manually: python src/kb_merge_canonical.py")

        # Sync ChromaDB index
        print("\n[INFO] Syncing ChromaDB index...")
        try:
            from kb_index_sync import sync
            sync()
        except Exception as e:
            print(f"[WARNING] Auto-sync failed: {e}")
            print("  Run manually: python src/kb_index_sync.py")
    else:
        print("[DRY RUN] No files changed.")


if __name__ == "__main__":
    main()
