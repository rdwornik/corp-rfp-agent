"""
anonymization/scan_kb.py
CLI tool to scan canonical KB for sensitive terms.

Usage:
    python -m scripts.core.anonymization.scan_kb
"""

import json
from pathlib import Path
from .config import get_blocklist
from .core import check

PROJECT_ROOT = Path(__file__).resolve().parents[3]
KB_PATH = (
    PROJECT_ROOT / "data_kb/canonical/RFP_Database_Cognitive_Planning_CANONICAL.json"
)


def scan() -> dict:
    """Scan canonical KB for sensitive terms."""
    if not KB_PATH.exists():
        print(f"❌ KB not found: {KB_PATH}")
        return {}

    with open(KB_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    findings = {}
    fields = ["canonical_question", "canonical_answer", "search_blob"]

    for item in data:
        kb_id = item.get("kb_id", "unknown")
        item_findings = []

        for field in fields:
            if field in item and item[field]:
                found = check(item[field])
                if found:
                    item_findings.append({"field": field, "matches": found})

        if item_findings:
            findings[kb_id] = item_findings

    return findings


def main():
    print("\n🔍 Scanning KB for sensitive terms...")
    print("=" * 50)

    blocklist = get_blocklist()
    print(f"Blocklist: {blocklist}\n")

    findings = scan()

    if findings:
        print(f"⚠️ Found sensitive terms in {len(findings)} entries:\n")
        for kb_id, items in findings.items():
            print(f"  [{kb_id}]")
            for item in items:
                print(f"    {item['field']}: {item['matches']}")
    else:
        print("✅ No sensitive terms found!")


if __name__ == "__main__":
    main()
