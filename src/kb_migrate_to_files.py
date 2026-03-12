"""Migrate KB canonical JSON arrays to individual entry files.

Reads existing canonical/*.json files and writes one JSON file per entry
into data/kb/verified/{family}/KB_XXXX.json.

Canonical files are kept intact for backward compatibility.

Usage:
  python src/kb_migrate_to_files.py
  python src/kb_migrate_to_files.py --dry-run
  python src/kb_migrate_to_files.py --family planning
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DIR = PROJECT_ROOT / "data" / "kb" / "canonical"
VERIFIED_DIR = PROJECT_ROOT / "data" / "kb" / "verified"
DRAFTS_DIR = PROJECT_ROOT / "data" / "kb" / "drafts"
REJECTED_DIR = PROJECT_ROOT / "data" / "kb" / "rejected"
SCHEMA_PATH = PROJECT_ROOT / "data" / "kb" / "schema" / "family_config.json"

NOW_ISO = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
TODAY = datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Schema helpers — normalize v1 and v2 entries to unified per-file format
# ---------------------------------------------------------------------------

def _get(entry: dict, *keys, default=None):
    """Return first non-None value from entry for the given keys."""
    for k in keys:
        v = entry.get(k)
        if v is not None:
            return v
    return default


def normalize_entry(entry: dict, family_code: str, source_file: str) -> dict:
    """Normalize a v1 or v2 entry to the individual-file format."""
    # ID: v2 uses "id", v1 uses "kb_id"
    entry_id = _get(entry, "id", "kb_id", default="UNKNOWN")

    # Normalize ID to uppercase KB_ format for v1 entries
    raw_id = entry_id
    if raw_id.startswith("kb_"):
        # v1 format: kb_0001 -> KB_0001
        entry_id = raw_id.upper()
    else:
        # v2 format: NET-FUNC-0001 — keep as-is
        entry_id = raw_id

    question = _get(entry, "question", "canonical_question", default="")
    answer = _get(entry, "answer", "canonical_answer", default="")

    # Extract question_variants from either location
    variants = entry.get("question_variants", [])
    if not variants:
        rm = entry.get("rich_metadata", {})
        variants = rm.get("question_variants", [])

    # Tags/keywords
    tags = entry.get("tags", [])
    if not tags:
        rm = entry.get("rich_metadata", {})
        tags = rm.get("keywords", [])

    return {
        "id": entry_id,
        "question": question,
        "answer": answer,
        "question_variants": variants,
        "solution_codes": entry.get("solution_codes", []),
        "family_code": _get(entry, "family_code", "domain", default=family_code),
        "category": entry.get("category", ""),
        "subcategory": entry.get("subcategory", ""),
        "tags": tags,
        "confidence": entry.get("confidence", "verified"),
        "source_rfps": entry.get("source_rfps", []),
        "last_updated": entry.get("last_updated", TODAY),
        "cloud_native_only": entry.get("cloud_native_only", False),
        "notes": entry.get("notes", ""),
        "feedback_history": [],
        "provenance": {
            "original_source": source_file,
            "original_id": raw_id,
            "migrated_at": NOW_ISO,
        },
    }


def load_family_config() -> dict:
    """Load family_config.json."""
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["families"]


def _family_for_file(filename: str, families: dict) -> str | None:
    """Find family_code for a canonical filename."""
    for code, cfg in families.items():
        if cfg.get("canonical_file") == filename:
            return code
    return None


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def migrate(
    canonical_dir: Path = CANONICAL_DIR,
    verified_dir: Path = VERIFIED_DIR,
    family_filter: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Migrate canonical JSON arrays to individual files.

    Returns summary dict: {family: count, ...}
    """
    families = load_family_config()
    summary: dict[str, int] = {}
    skipped_files: list[str] = []

    # Find all canonical files (skip UNIFIED — it's a merge of others)
    json_files = sorted(canonical_dir.glob("*.json"))

    for filepath in json_files:
        if "UNIFIED" in filepath.name:
            continue

        family_code = _family_for_file(filepath.name, families)
        if family_code is None:
            skipped_files.append(filepath.name)
            continue

        if family_filter and family_code != family_filter:
            continue

        with open(filepath, "r", encoding="utf-8") as f:
            entries = json.load(f)

        if not isinstance(entries, list):
            print(f"  [SKIP] {filepath.name}: not a JSON array")
            continue

        family_dir = verified_dir / family_code
        if not dry_run:
            family_dir.mkdir(parents=True, exist_ok=True)

        count = 0
        for entry in entries:
            normalized = normalize_entry(entry, family_code, filepath.name)
            entry_id = normalized["id"]
            filename = f"{entry_id}.json"
            out_path = family_dir / filename

            if not dry_run:
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(normalized, f, indent=2, ensure_ascii=False)

            count += 1

        summary[family_code] = count
        print(f"  {family_code:<16} {count:>5} entries"
              f"{' (dry run)' if dry_run else ''}")

    if skipped_files:
        print(f"\n  [INFO] Skipped (no family match): {', '.join(skipped_files)}")

    return summary


# ---------------------------------------------------------------------------
# Setup directories
# ---------------------------------------------------------------------------

def setup_directories(dry_run: bool = False) -> None:
    """Create the new KB directory structure."""
    dirs = [VERIFIED_DIR, DRAFTS_DIR, REJECTED_DIR]
    for d in dirs:
        if not dry_run:
            d.mkdir(parents=True, exist_ok=True)
        print(f"  {'[EXISTS]' if d.exists() else '[CREATE]'} {d.relative_to(PROJECT_ROOT)}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Migrate KB canonical JSON arrays to individual entry files",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without writing files")
    parser.add_argument("--family", type=str, default=None,
                        help="Migrate only one family")
    parser.add_argument("--canonical-dir", type=str, default=str(CANONICAL_DIR))
    parser.add_argument("--verified-dir", type=str, default=str(VERIFIED_DIR))
    args = parser.parse_args()

    print("=" * 60)
    print("  KB Migration: canonical arrays -> individual files")
    print("=" * 60)

    print("\nDirectory setup:")
    setup_directories(args.dry_run)

    print("\nMigrating entries:")
    summary = migrate(
        canonical_dir=Path(args.canonical_dir),
        verified_dir=Path(args.verified_dir),
        family_filter=args.family,
        dry_run=args.dry_run,
    )

    total = sum(summary.values())
    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Total: {total} entries "
          f"across {len(summary)} families")

    if not args.dry_run:
        print("\n[OK] Migration complete. Canonical files preserved.")
        print("     RAG still reads from canonical/ until kb_index_sync "
              "is updated.")


if __name__ == "__main__":
    main()
