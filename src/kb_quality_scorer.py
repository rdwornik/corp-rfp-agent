"""LLM Quality Scorer — scores KB entries on 5 dimensions using Gemini Flash.

Scores each entry on: ACCURACY, SPECIFICITY, TONE, COMPLETENESS, SELF_CONTAINED.
Saves _quality field into each entry's JSON file.

Usage:
  python src/kb_quality_scorer.py --scope drafts
  python src/kb_quality_scorer.py --scope verified
  python src/kb_quality_scorer.py --family wms
  python src/kb_quality_scorer.py --scope verified --sample 10
  python src/kb_quality_scorer.py --scope drafts --batch
  python src/kb_quality_scorer.py --family wms --sync
"""

import argparse
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
KB_DIR = PROJECT_ROOT / "data" / "kb"
VERIFIED_DIR = KB_DIR / "verified"
DRAFTS_DIR = KB_DIR / "drafts"
PROFILES_DIR = PROJECT_ROOT / "config" / "product_profiles" / "_effective"

DIMENSIONS = ["accuracy", "specificity", "tone", "completeness", "self_contained"]

SCORING_PROMPT = """You are evaluating RFP knowledge base entries for quality.

PRODUCT: {product_display_name}
PRODUCT PROFILE:
- Cloud-native: {cloud_native}
- Deployment: {deployment}
- APIs: {apis}
- Microservices: {microservices}
- Uses Snowflake: {uses_snowflake}

FORBIDDEN CLAIMS (answer must NOT assert these):
{forbidden_claims}

ENTRY TO EVALUATE:
Question: {question}
Answer: {answer}

Score on 5 dimensions (0-5 each):
1. ACCURACY: alignment with product profile, no forbidden claims
2. SPECIFICITY: concrete products, technologies, numbers named
3. TONE: professional BY pre-sales voice
4. COMPLETENESS: fully addresses the question
5. SELF_CONTAINED: standalone, no external references needed

Return JSON only:
{{"accuracy": 4, "specificity": 3, "tone": 5, "completeness": 4, "self_contained": 5, "average": 4.2, "issues": ["Missing API version numbers"], "verdict": "GOOD"}}

Verdicts: "EXCELLENT" (avg >= 4.5), "GOOD" (avg >= 3.5), "NEEDS_WORK" (avg >= 2.5), "POOR" (avg < 2.5)"""


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------

def load_profile(family: str, profiles_dir: Path = PROFILES_DIR) -> dict:
    """Load effective product profile for a family."""
    path = profiles_dir / f"{family}.yaml"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Entry loading
# ---------------------------------------------------------------------------

def load_entries(scope: Optional[str] = None,
                 family: Optional[str] = None,
                 sample_pct: Optional[int] = None,
                 verified_dir: Path = VERIFIED_DIR,
                 drafts_dir: Path = DRAFTS_DIR) -> list[tuple[Path, dict]]:
    """Load entries from verified/drafts dirs.

    Returns list of (file_path, entry_dict) tuples.
    """
    dirs = []
    if scope == "verified":
        dirs = [verified_dir]
    elif scope == "drafts":
        dirs = [drafts_dir]
    else:
        dirs = [verified_dir, drafts_dir]

    entries = []
    for base_dir in dirs:
        if not base_dir.exists():
            continue
        for json_file in sorted(base_dir.rglob("*.json")):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    entry = json.load(f)
                if family and entry.get("family_code") != family:
                    continue
                entries.append((json_file, entry))
            except (json.JSONDecodeError, OSError):
                continue

    if sample_pct is not None and 0 < sample_pct < 100 and entries:
        count = max(1, len(entries) * sample_pct // 100)
        entries = random.sample(entries, count)

    return entries


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def build_scoring_prompt(entry: dict, profile: dict) -> str:
    """Build the scoring prompt for one entry."""
    forbidden = profile.get("forbidden_claims", [])
    return SCORING_PROMPT.format(
        product_display_name=profile.get("display_name", profile.get("product", "unknown")),
        cloud_native=profile.get("cloud_native", "unknown"),
        deployment=profile.get("deployment", []),
        apis=profile.get("apis", []),
        microservices=profile.get("microservices", "unknown"),
        uses_snowflake=profile.get("uses_snowflake", "unknown"),
        forbidden_claims="\n".join(f"- {fc}" for fc in forbidden[:20]) if forbidden else "(none)",
        question=entry.get("question", ""),
        answer=entry.get("answer", ""),
    )


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def parse_scores(raw_text: str) -> Optional[dict]:
    """Parse LLM JSON response into scores dict. 3-strategy parser."""
    if not raw_text or not raw_text.strip():
        return None

    # Strategy 1: strip markdown fences
    text = re.sub(r'^```(?:json)?\s*', '', raw_text.strip(), flags=re.MULTILINE)
    text = re.sub(r'```\s*$', '', text.strip(), flags=re.MULTILINE).strip()

    for attempt_text in [text, raw_text.strip()]:
        # Strategy 2: direct parse
        try:
            data = json.loads(attempt_text)
            if _validate_scores(data):
                return _normalize_scores(data)
        except json.JSONDecodeError:
            pass

        # Strategy 3: extract JSON object
        m = re.search(r'(\{[\s\S]*\})', attempt_text)
        if m:
            try:
                data = json.loads(m.group(1))
                if _validate_scores(data):
                    return _normalize_scores(data)
            except json.JSONDecodeError:
                pass

    return None


def _validate_scores(data: dict) -> bool:
    """Check that data has at least some scoring dimensions."""
    if not isinstance(data, dict):
        return False
    return any(d in data for d in DIMENSIONS)


def _normalize_scores(data: dict) -> dict:
    """Ensure all dimensions present and compute average/verdict."""
    for d in DIMENSIONS:
        val = data.get(d, 0)
        if not isinstance(val, (int, float)):
            val = 0
        data[d] = max(0, min(5, val))

    avg = sum(data[d] for d in DIMENSIONS) / len(DIMENSIONS)
    data["average"] = round(avg, 1)

    if avg >= 4.5:
        data["verdict"] = "EXCELLENT"
    elif avg >= 3.5:
        data["verdict"] = "GOOD"
    elif avg >= 2.5:
        data["verdict"] = "NEEDS_WORK"
    else:
        data["verdict"] = "POOR"

    if "issues" not in data or not isinstance(data["issues"], list):
        data["issues"] = []

    return data


def zero_scores() -> dict:
    """Return zeroed-out scores for parse failures."""
    data = {d: 0 for d in DIMENSIONS}
    data["average"] = 0.0
    data["verdict"] = "POOR"
    data["issues"] = ["LLM scoring failed — could not parse response"]
    return data


# ---------------------------------------------------------------------------
# Scoring: sync and batch
# ---------------------------------------------------------------------------

def score_sync(entries_with_prompts: list[dict],
               model: str = "gemini-flash") -> list[dict]:
    """Score entries synchronously (one LLM call per entry)."""
    from kb_extract_historical import call_llm

    results = []
    for i, item in enumerate(entries_with_prompts):
        print(f"  Scoring {i+1}/{len(entries_with_prompts)}...", end="\r")
        try:
            raw = call_llm(item["prompt"], model=model)
            scores = parse_scores(raw)
            if scores:
                results.append({"entry_id": item["entry_id"], "scores": scores})
            else:
                results.append({"entry_id": item["entry_id"], "scores": zero_scores()})
        except Exception as e:
            print(f"  [WARN] Scoring failed for {item['entry_id']}: {e}")
            results.append({"entry_id": item["entry_id"], "scores": zero_scores()})
    print()
    return results


def score_batch(entries_with_prompts: list[dict],
                model: str = "gemini-3-flash-preview") -> list[dict]:
    """Score entries using Batch API (50% cheaper)."""
    from batch_llm import BatchProcessor, parse_json_from_batch

    bp = BatchProcessor(model=model)
    for item in entries_with_prompts:
        bp.add(key=item["entry_id"], prompt=item["prompt"])

    result = bp.run(display_name="kb-quality-score", verbose=True)

    results = []
    for item in entries_with_prompts:
        raw = result.results.get(item["entry_id"], "")
        if raw:
            try:
                data = parse_json_from_batch(raw)
                if isinstance(data, dict) and _validate_scores(data):
                    scores = _normalize_scores(data)
                else:
                    scores = zero_scores()
            except (ValueError, json.JSONDecodeError):
                scores = zero_scores()
        else:
            scores = zero_scores()
        results.append({"entry_id": item["entry_id"], "scores": scores})

    return results


# ---------------------------------------------------------------------------
# Save scores into entry files
# ---------------------------------------------------------------------------

def save_scores(results: list[dict], path_map: dict[str, Path]) -> int:
    """Save _quality field into each entry's JSON file.

    Returns number of entries updated.
    """
    updated = 0
    for r in results:
        entry_id = r["entry_id"]
        path = path_map.get(entry_id)
        if not path or not path.exists():
            continue

        try:
            with open(path, "r", encoding="utf-8") as f:
                entry = json.load(f)

            entry["_quality"] = {
                **r["scores"],
                "scored_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            }

            with open(path, "w", encoding="utf-8") as f:
                json.dump(entry, f, indent=2, ensure_ascii=False)
            updated += 1
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [WARN] Could not save scores for {entry_id}: {e}")

    return updated


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_report(results: list[dict]) -> None:
    """Print formatted scoring report."""
    if not results:
        print("[INFO] No entries scored")
        return

    border = "=" * 66
    print(f"\n{border}")
    print(f"  Quality Scoring Report")
    print(f"{border}")
    print(f"  Entries scored: {len(results)}")

    # Verdict distribution
    verdicts = {"EXCELLENT": 0, "GOOD": 0, "NEEDS_WORK": 0, "POOR": 0}
    for r in results:
        v = r["scores"].get("verdict", "POOR")
        verdicts[v] = verdicts.get(v, 0) + 1

    print(f"  Verdicts:")
    max_count = max(verdicts.values()) if verdicts else 1
    for verdict in ["EXCELLENT", "GOOD", "NEEDS_WORK", "POOR"]:
        count = verdicts[verdict]
        bar = "#" * (count * 30 // max(max_count, 1))
        print(f"    {verdict:<12}: {count:>3} {bar}")

    # Dimension averages
    print(f"\n  Dimension averages:")
    for dim in DIMENSIONS:
        vals = [r["scores"].get(dim, 0) for r in results]
        avg = sum(vals) / len(vals) if vals else 0
        print(f"    {dim:<16}: {avg:.1f}/5")

    # Entries needing attention
    needs_work = [r for r in results
                  if r["scores"].get("average", 0) < 3.5]
    if needs_work:
        needs_work.sort(key=lambda x: x["scores"].get("average", 0))
        print(f"\n  Entries needing attention ({len(needs_work)}):")
        for r in needs_work[:15]:
            avg = r["scores"].get("average", 0)
            issues = r["scores"].get("issues", [])
            issue_str = "; ".join(issues[:2]) if issues else ""
            print(f"    {r['entry_id']}: avg={avg:.1f} -- {issue_str[:70]}")

    print(f"{border}")


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def score_entries(scope: Optional[str] = None,
                  family: Optional[str] = None,
                  sample_pct: Optional[int] = None,
                  batch_mode: bool = True,
                  model: str = "gemini-flash",
                  verified_dir: Path = VERIFIED_DIR,
                  drafts_dir: Path = DRAFTS_DIR,
                  profiles_dir: Path = PROFILES_DIR) -> list[dict]:
    """Full scoring pipeline. Returns list of score results."""

    # Load entries
    entries = load_entries(scope, family, sample_pct, verified_dir, drafts_dir)
    if not entries:
        print("[INFO] No entries to score")
        return []

    print(f"[INFO] Scoring {len(entries)} entries...")

    # Build prompts
    profile_cache: dict[str, dict] = {}
    entries_with_prompts = []
    path_map: dict[str, Path] = {}

    for path, entry in entries:
        fam = entry.get("family_code", "")
        if fam not in profile_cache:
            profile_cache[fam] = load_profile(fam, profiles_dir)
        profile = profile_cache[fam]

        entry_id = entry.get("id", path.stem)
        prompt = build_scoring_prompt(entry, profile)
        entries_with_prompts.append({
            "entry_id": entry_id,
            "prompt": prompt,
        })
        path_map[entry_id] = path

    # Score
    if batch_mode:
        results = score_batch(entries_with_prompts, model=model)
    else:
        results = score_sync(entries_with_prompts, model=model)

    # Save scores into entry files
    updated = save_scores(results, path_map)
    print(f"[INFO] Updated {updated}/{len(results)} entry files with _quality scores")

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="LLM Quality Scorer -- score KB entries on 5 dimensions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--scope", choices=["verified", "drafts"],
                        help="Score only verified or drafts")
    parser.add_argument("--family", help="Filter to one family")
    parser.add_argument("--sample", type=int, default=None, metavar="PCT",
                        help="Random sample percentage (1-99)")
    parser.add_argument("--batch", action="store_true", default=True,
                        help="Use Batch API (default, 50%% cheaper)")
    parser.add_argument("--sync", action="store_true",
                        help="Use synchronous mode (one call at a time)")
    parser.add_argument("--model", default="gemini-flash",
                        help="LLM model (default: gemini-flash)")
    args = parser.parse_args()

    batch_mode = not args.sync

    results = score_entries(
        scope=args.scope,
        family=args.family,
        sample_pct=args.sample,
        batch_mode=batch_mode,
        model=args.model,
    )

    print_report(results)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
