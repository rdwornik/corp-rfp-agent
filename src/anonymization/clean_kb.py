"""
anonymization/clean_kb.py
CLI tool to clean sensitive terms from canonical KB.

Usage:
    python -m scripts.core.anonymization.clean_kb
    python -m scripts.core.anonymization.clean_kb --dry-run
"""

import json
import argparse
from pathlib import Path
from datetime import datetime
from .core import anonymize

PROJECT_ROOT = Path(__file__).resolve().parents[3]
KB_PATH = (
    PROJECT_ROOT / "data_kb/canonical/RFP_Database_Cognitive_Planning_CANONICAL.json"
)
BACKUP_DIR = KB_PATH.parent / "backups"


def backup_kb() -> Path:
    """Create backup of current KB."""
    BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"CANONICAL_backup_{timestamp}.json"

    with open(KB_PATH, "r", encoding="utf-8") as f:
        data = f.read()
    with open(backup_path, "w", encoding="utf-8") as f:
        f.write(data)

    return backup_path


def clean(dry_run: bool = False) -> int:
    """Clean KB and return count of replacements."""
    with open(KB_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    total = 0
    fields = ["canonical_question", "canonical_answer", "search_blob"]

    for item in data:
        for field in fields:
            if field in item and item[field]:
                cleaned, mapping = anonymize(item[field])
                if mapping:
                    total += len(mapping)
                    if not dry_run:
                        item[field] = cleaned

    if not dry_run and total > 0:
        backup_path = backup_kb()
        print(f"📦 Backup: {backup_path.name}")

        with open(KB_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    return total


def main():
    parser = argparse.ArgumentParser(description="Clean sensitive terms from KB")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    args = parser.parse_args()

    print(f"\n{'🔍 DRY RUN: ' if args.dry_run else '🧹 '}Cleaning KB...")
    print("=" * 50)

    count = clean(dry_run=args.dry_run)

    if count > 0:
        print(f"{'Would replace' if args.dry_run else 'Replaced'}: {count} occurrences")
    else:
        print("✅ Nothing to clean!")


if __name__ == "__main__":
    main()
