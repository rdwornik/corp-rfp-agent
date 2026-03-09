"""KB Reclassification -- migrate 6 categories to 4 RFP response teams.

Step 1: Mechanical migration (no API) -- maps old categories to new.
Step 2: LLM reclassification (Gemini Flash) -- reclassifies all entries.

Usage:
    python src/kb_reclassify.py --migrate-only
    python src/kb_reclassify.py --dry-run --model gemini-flash
    python src/kb_reclassify.py --model gemini-flash
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DIR = PROJECT_ROOT / "data" / "kb" / "canonical"

# Step 1: Mechanical migration from 6 → 4 categories
CATEGORY_MIGRATION = {
    "security": "technical",
    "Security & Compliance": "technical",
    "deployment": "technical",
    "Infrastructure": "technical",
    "commercial": "customer_executive",
    "general": "customer_executive",
    # These stay as-is
    "technical": "technical",
    "functional": "functional",
    "customer_executive": "customer_executive",
    "consulting": "consulting",
}

VALID_CATEGORIES = {"technical", "functional", "customer_executive", "consulting"}

LLM_CLASSIFY_PROMPT = """Classify each RFP Q&A pair into the team that answers it.

1. TECHNICAL -- Platform architects. Topics: architecture, APIs, integrations (SAP, EDI, REST), security (SSO, encryption), hosting (Azure), performance, SLA, data model, disaster recovery, environments, monitoring, cloud, microservices.

2. FUNCTIONAL -- Product consultants. Topics: business capabilities, planning workflows, demand forecasting, replenishment, inventory optimization, UI, reporting, dashboards, configuration, KPIs, what-if analysis.
   KEY: describes WHAT the product does for the business.

3. CUSTOMER_EXECUTIVE -- Sales leadership. Topics: company overview, revenue, references, case studies, partnerships, vision, roadmap, licensing, pricing.
   KEY: about the COMPANY or COMMERCIAL TERMS.

4. CONSULTING -- Implementation services. Topics: methodology, project plan, timelines, training, change management, data migration, go-live, hypercare.
   KEY: about HOW you deliver/implement.

Return ONLY a JSON array:
[{{"index": 0, "category": "technical|functional|customer_executive|consulting", "confidence": 0.9}}]

Q&A pairs:
{batch_json}"""


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


def mechanical_migrate(entries: list[dict]) -> dict:
    """Apply mechanical category migration. Returns stats."""
    stats = {"migrated": 0, "already_correct": 0, "unmapped": 0, "changes": []}

    for entry in entries:
        old_cat = entry.get("category", "").strip().lower()

        # Try exact match first, then case-insensitive lookup
        new_cat = CATEGORY_MIGRATION.get(old_cat)
        if new_cat is None:
            # Try original (non-lowered) value
            new_cat = CATEGORY_MIGRATION.get(entry.get("category", ""))

        if new_cat is None:
            if old_cat in VALID_CATEGORIES:
                stats["already_correct"] += 1
            else:
                stats["unmapped"] += 1
            continue

        if new_cat != old_cat:
            stats["changes"].append({
                "id": entry.get("kb_id", entry.get("id", "?")),
                "old": entry.get("category", ""),
                "new": new_cat,
            })
            entry["category"] = new_cat
            stats["migrated"] += 1
        else:
            stats["already_correct"] += 1

    return stats


def llm_reclassify_batch(batch: list[dict], model: str = "gemini-flash") -> list[dict]:
    """Reclassify a batch of entries using Gemini Flash.

    Returns list of {"index": int, "category": str, "confidence": float}.
    """
    from google import genai
    from google.genai import types

    sys.path.insert(0, str(PROJECT_ROOT / "src"))
    from llm_router import MODELS, retry_with_backoff

    batch_json = json.dumps([
        {
            "index": i,
            "question": e.get("canonical_question", e.get("question", "")),
            "answer": e.get("canonical_answer", e.get("answer", ""))[:300],
        }
        for i, e in enumerate(batch)
    ], ensure_ascii=False)

    prompt = LLM_CLASSIFY_PROMPT.format(batch_json=batch_json)

    model_config = MODELS.get(model, MODELS["gemini-flash"])
    model_name = model_config["name"]

    def call_llm():
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(
            model=model_name,
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=4096),
        )
        text = response.text.strip() if response.text else "[]"
        # Strip markdown code fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        return json.loads(text)

    return retry_with_backoff(call_llm)


def llm_reclassify(entries: list[dict], model: str, dry_run: bool, batch_size: int = 10) -> dict:
    """Full LLM reclassification pipeline."""
    stats = {"reclassified": 0, "unchanged": 0, "errors": 0, "changes": []}
    total_batches = (len(entries) + batch_size - 1) // batch_size

    for batch_idx in range(0, len(entries), batch_size):
        batch = entries[batch_idx:batch_idx + batch_size]
        batch_num = batch_idx // batch_size + 1
        print(f"  [{batch_num}/{total_batches}] Classifying {len(batch)} entries...")

        try:
            results = llm_reclassify_batch(batch, model)
        except Exception as e:
            print(f"    [ERROR] Batch {batch_num}: {e}")
            stats["errors"] += len(batch)
            continue

        for result in results:
            idx = result.get("index", -1)
            new_cat = result.get("category", "")
            confidence = result.get("confidence", 0.0)

            if idx < 0 or idx >= len(batch):
                continue
            if new_cat not in VALID_CATEGORIES:
                continue

            entry = batch[idx]
            old_cat = entry.get("category", "")

            if new_cat != old_cat and confidence >= 0.7:
                stats["changes"].append({
                    "id": entry.get("kb_id", entry.get("id", "?")),
                    "old": old_cat,
                    "new": new_cat,
                    "confidence": confidence,
                })
                if not dry_run:
                    entry["category"] = new_cat
                stats["reclassified"] += 1
            else:
                stats["unchanged"] += 1

        # Rate limit
        if batch_num < total_batches:
            time.sleep(0.5)

    return stats


def save_entries(entries: list[dict], canonical_dir: Path) -> None:
    """Save entries back to canonical files."""
    by_file: dict[str, list[dict]] = {}
    for entry in entries:
        source = entry.pop("_source_file", "unknown.json")
        by_file.setdefault(source, []).append(entry)

    for filename, file_entries in by_file.items():
        out_path = canonical_dir / filename
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(file_entries, f, indent=2, ensure_ascii=False)
        print(f"  [OK] {filename}: {len(file_entries)} entries")


def print_category_stats(entries: list[dict]) -> None:
    """Print category distribution."""
    counts: dict[str, int] = {}
    for e in entries:
        cat = e.get("category", "unknown")
        counts[cat] = counts.get(cat, 0) + 1

    print("\nCategory distribution:")
    print(f"  {'Category':<25} {'Count':>6}")
    print(f"  {'-'*25} {'-'*6}")
    for cat in sorted(counts.keys()):
        print(f"  {cat:<25} {counts[cat]:>6}")
    print(f"  {'TOTAL':<25} {sum(counts.values()):>6}")


def main():
    parser = argparse.ArgumentParser(description="KB Reclassification -- 6 categories to 4")
    parser.add_argument("--migrate-only", action="store_true",
                        help="Only run mechanical migration (no LLM)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show changes without saving")
    parser.add_argument("--model", default="gemini-flash",
                        help="LLM model for reclassification (default: gemini-flash)")
    parser.add_argument("--batch-size", type=int, default=10,
                        help="Entries per LLM batch (default: 10)")
    parser.add_argument("--canonical-dir", type=str, default=None)
    args = parser.parse_args()

    canonical_dir = Path(args.canonical_dir) if args.canonical_dir else CANONICAL_DIR
    entries = load_all_entries(canonical_dir)

    if not entries:
        print("[ERROR] No entries found.")
        sys.exit(1)

    print(f"[INFO] Loaded {len(entries)} entries")
    print_category_stats(entries)

    # Step 1: Mechanical migration
    print("\n[Step 1] Mechanical migration...")
    mech_stats = mechanical_migrate(entries)
    print(f"  Migrated: {mech_stats['migrated']}, Already correct: {mech_stats['already_correct']}, Unmapped: {mech_stats['unmapped']}")
    if mech_stats["changes"][:5]:
        for ch in mech_stats["changes"][:5]:
            print(f"    {ch['id']}: {ch['old']} -> {ch['new']}")

    # Step 2: LLM reclassification (unless --migrate-only)
    if not args.migrate_only:
        print(f"\n[Step 2] LLM reclassification ({args.model})...")
        llm_stats = llm_reclassify(entries, args.model, args.dry_run, args.batch_size)
        print(f"  Reclassified: {llm_stats['reclassified']}, Unchanged: {llm_stats['unchanged']}, Errors: {llm_stats['errors']}")

    print_category_stats(entries)

    if not args.dry_run:
        print("\n[INFO] Saving entries...")
        save_entries(entries, canonical_dir)
        print("[DONE] Reclassification complete.")
    else:
        # Remove _source_file keys to avoid polluting future loads
        for e in entries:
            e.pop("_source_file", None)
        print("[DRY RUN] No files changed.")


if __name__ == "__main__":
    main()
