"""KB Reclassification -- migrate fine-grained categories to 4 RFP response teams.

Step 1: Mechanical migration (no API) -- partial-match maps old categories to new.
Step 2: LLM reclassification (Gemini Flash) -- reclassifies remaining entries.
Step 3: Default fallback -- any entry still not in 4 valid categories -> technical.

Usage:
    python src/kb_reclassify.py --migrate-only
    python src/kb_reclassify.py --dry-run --model gemini-flash
    python src/kb_reclassify.py --model gemini-flash
"""

import argparse
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DIR = PROJECT_ROOT / "data" / "kb" / "canonical"

VALID_CATEGORIES = {"technical", "functional", "customer_executive", "consulting"}

# --- FIX 1: Partial-match term sets (ordered longest-first within each set) ---

# Checked BEFORE consulting (order matters: "project management" -> consulting,
# but "company overview" -> customer_executive)
CUSTOMER_EXECUTIVE_TERMS = sorted([
    "company overview", "references", "pricing", "licensing",
    "financial", "commercial", "general", "case study", "case studies",
    "partnership", "revenue", "vision", "roadmap",
], key=len, reverse=True)

CONSULTING_TERMS = sorted([
    "project management", "change management", "data migration",
    "implementation", "methodology", "training",
    "go-live", "hypercare", "knowledge transfer",
    "project plan", "timeline",
], key=len, reverse=True)


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


def _safe_category(val) -> str:
    """Convert category value to safe string (handles NaN, None, float)."""
    if val is None:
        return ""
    if isinstance(val, float):
        if math.isnan(val):
            return ""
        return str(val)
    s = str(val).strip()
    return s


def _classify_by_terms(cat_lower: str) -> Optional[str]:
    """Partial-match classification. Returns category or None.

    Checks customer_executive terms BEFORE consulting terms.
    Within each set, longest terms match first to avoid false positives.
    """
    # Already a valid 4-category?
    if cat_lower in VALID_CATEGORIES:
        return cat_lower

    # Customer executive terms (checked first)
    for term in CUSTOMER_EXECUTIVE_TERMS:
        if term in cat_lower:
            return "customer_executive"

    # Consulting terms
    for term in CONSULTING_TERMS:
        if term in cat_lower:
            return "consulting"

    # No match -- caller decides default
    return None


def mechanical_migrate(entries: list[dict]) -> dict:
    """Apply mechanical category migration using partial-match. Returns stats."""
    stats = {"migrated": 0, "already_correct": 0, "unmapped": 0, "changes": []}

    for entry in entries:
        raw_cat = entry.get("category", "")
        safe_cat = _safe_category(raw_cat)
        cat_lower = safe_cat.lower()

        if not cat_lower or cat_lower == "nan":
            # Empty/NaN -> technical (safe default)
            entry["category"] = "technical"
            stats["migrated"] += 1
            stats["changes"].append({
                "id": entry.get("kb_id", entry.get("id", "?")),
                "old": raw_cat, "new": "technical",
            })
            continue

        new_cat = _classify_by_terms(cat_lower)

        if new_cat is None:
            # No term matched -> technical (safe default)
            new_cat = "technical"

        if new_cat == cat_lower:
            stats["already_correct"] += 1
        else:
            stats["changes"].append({
                "id": entry.get("kb_id", entry.get("id", "?")),
                "old": safe_cat, "new": new_cat,
            })
            entry["category"] = new_cat
            stats["migrated"] += 1

    return stats


# --- FIX 2: Robust JSON parsing with 3 strategies + retry ---

def _parse_llm_json(text: str) -> list[dict]:
    """Parse LLM response as JSON array with 3 fallback strategies."""
    if not text:
        return []

    # Strategy 1: direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: strip markdown code fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        # Remove optional language tag like "json"
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

    # Strategy 3: regex extract JSON array
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    raise json.JSONDecodeError("All 3 parse strategies failed", text, 0)


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
        return _parse_llm_json(text)

    return retry_with_backoff(call_llm)


def llm_reclassify(entries: list[dict], model: str, dry_run: bool, batch_size: int = 10) -> dict:
    """Full LLM reclassification pipeline with retry on failed batches."""
    stats = {"reclassified": 0, "unchanged": 0, "errors": 0, "defaulted": 0, "changes": []}
    total_batches = (len(entries) + batch_size - 1) // batch_size

    for batch_idx in range(0, len(entries), batch_size):
        batch = entries[batch_idx:batch_idx + batch_size]
        batch_num = batch_idx // batch_size + 1
        print(f"  [{batch_num}/{total_batches}] Classifying {len(batch)} entries...")

        results = None
        try:
            results = llm_reclassify_batch(batch, model)
        except Exception as e:
            # FIX 2: Retry failed batch with smaller batch size
            if len(batch) > 5:
                print(f"    [RETRY] Batch {batch_num} failed ({e}), retrying as 2 sub-batches...")
                mid = len(batch) // 2
                for sub_batch, offset in [(batch[:mid], 0), (batch[mid:], mid)]:
                    try:
                        sub_results = llm_reclassify_batch(sub_batch, model)
                        # Adjust indices
                        for r in sub_results:
                            r["index"] = r.get("index", 0) + offset
                        if results is None:
                            results = []
                        results.extend(sub_results)
                    except Exception as e2:
                        print(f"    [ERROR] Sub-batch failed: {e2}")
            else:
                print(f"    [ERROR] Batch {batch_num}: {e}")

        # Track which indices got an LLM result
        covered = set()
        if results:
            for result in results:
                idx = result.get("index", -1)
                new_cat = result.get("category", "")
                confidence = result.get("confidence", 0.0)

                if idx < 0 or idx >= len(batch):
                    continue
                if new_cat not in VALID_CATEGORIES:
                    continue

                covered.add(idx)
                entry = batch[idx]
                old_cat = entry.get("category", "")

                if new_cat != old_cat and confidence >= 0.7:
                    stats["changes"].append({
                        "id": entry.get("kb_id", entry.get("id", "?")),
                        "old": old_cat, "new": new_cat, "confidence": confidence,
                    })
                    if not dry_run:
                        entry["category"] = new_cat
                    stats["reclassified"] += 1
                else:
                    stats["unchanged"] += 1

        # FIX 3: Default uncovered entries to "technical"
        for idx in range(len(batch)):
            if idx not in covered:
                entry = batch[idx]
                old_cat = entry.get("category", "")
                if old_cat not in VALID_CATEGORIES:
                    stats["changes"].append({
                        "id": entry.get("kb_id", entry.get("id", "?")),
                        "old": old_cat, "new": "technical", "confidence": 0.0,
                    })
                    if not dry_run:
                        entry["category"] = "technical"
                    stats["defaulted"] += 1
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
        cat = _safe_category(e.get("category")) or "uncategorized"
        counts[cat] = counts.get(cat, 0) + 1

    print("\nCategory distribution:")
    print(f"  {'Category':<25} {'Count':>6}")
    print(f"  {'-'*25} {'-'*6}")
    for cat in sorted(counts.keys()):
        print(f"  {cat:<25} {counts[cat]:>6}")
    print(f"  {'TOTAL':<25} {sum(counts.values()):>6}")


def main():
    parser = argparse.ArgumentParser(description="KB Reclassification -- categories to 4 teams")
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

    # Step 1: Mechanical migration (partial-match)
    print("\n[Step 1] Mechanical migration (partial-match)...")
    mech_stats = mechanical_migrate(entries)
    print(f"  Migrated: {mech_stats['migrated']}, Already correct: {mech_stats['already_correct']}")
    if mech_stats["changes"][:10]:
        for ch in mech_stats["changes"][:10]:
            print(f"    {ch['id']}: {ch['old']} -> {ch['new']}")
        if len(mech_stats["changes"]) > 10:
            print(f"    ... and {len(mech_stats['changes']) - 10} more")

    print_category_stats(entries)

    # Step 2: LLM reclassification (unless --migrate-only)
    if not args.migrate_only:
        print(f"\n[Step 2] LLM reclassification ({args.model})...")
        llm_stats = llm_reclassify(entries, args.model, args.dry_run, args.batch_size)
        print(f"  Reclassified: {llm_stats['reclassified']}, "
              f"Unchanged: {llm_stats['unchanged']}, "
              f"Defaulted: {llm_stats['defaulted']}, "
              f"Errors: {llm_stats['errors']}")

    print_category_stats(entries)

    # Verify: all entries should now have a valid category
    invalid = [e for e in entries if e.get("category", "") not in VALID_CATEGORIES]
    if invalid:
        print(f"\n[WARNING] {len(invalid)} entries still have invalid categories")
    else:
        print(f"\n[OK] All {len(entries)} entries have valid categories")

    if not args.dry_run:
        print("\n[INFO] Saving entries...")
        save_entries(entries, canonical_dir)
        print("[DONE] Reclassification complete.")
    else:
        for e in entries:
            e.pop("_source_file", None)
        print("[DRY RUN] No files changed.")


if __name__ == "__main__":
    main()
