"""Feedback CLI for KB entry management.

Provides subcommands to show, correct, and search KB entries.

Usage:
  python src/rfp_feedback.py show KB_0234
  python src/rfp_feedback.py correct KB_0234 --text "Fix info" --dry-run
  python src/rfp_feedback.py correct KB_0234 --text "New answer" --offline --apply
  python src/rfp_feedback.py search "JSON bulk ingestion" --family planning
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
KB_DIR = PROJECT_ROOT / "data" / "kb"
VERIFIED_DIR = KB_DIR / "verified"
DRAFTS_DIR = KB_DIR / "drafts"
REJECTED_DIR = KB_DIR / "rejected"
FEEDBACK_LOG = KB_DIR / "feedback_log.jsonl"
PROFILES_DIR = PROJECT_ROOT / "config" / "product_profiles" / "_effective"

# Feedback ID counter file
_FB_COUNTER_PATH = KB_DIR / ".fb_counter"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _content_hash(text: str) -> str:
    """Short SHA-256 hash of text content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _next_feedback_id() -> str:
    """Generate next sequential feedback ID (FB_001, FB_002, ...)."""
    counter = 1
    if _FB_COUNTER_PATH.exists():
        try:
            counter = int(_FB_COUNTER_PATH.read_text().strip()) + 1
        except (ValueError, OSError):
            if FEEDBACK_LOG.exists():
                counter = sum(1 for _ in open(FEEDBACK_LOG, encoding="utf-8")) + 1
    _FB_COUNTER_PATH.parent.mkdir(parents=True, exist_ok=True)
    _FB_COUNTER_PATH.write_text(str(counter))
    return f"FB_{counter:03d}"


# ---------------------------------------------------------------------------
# Entry I/O
# ---------------------------------------------------------------------------

def load_entry(path: Path) -> dict:
    """Load a single entry JSON file."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_entry(entry: dict, path: Path) -> None:
    """Save entry to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2, ensure_ascii=False)


def find_entry(entry_id: str, search_dirs: list[str] | None = None) -> Optional[Path]:
    """Find an entry by ID across KB directories."""
    dir_map = {
        "verified": VERIFIED_DIR,
        "drafts": DRAFTS_DIR,
        "rejected": REJECTED_DIR,
    }
    dirs_to_search = search_dirs or ["verified", "drafts", "rejected"]

    filename = f"{entry_id}.json"

    for dir_name in dirs_to_search:
        base = dir_map.get(dir_name)
        if base is None or not base.exists():
            continue
        for family_dir in sorted(base.iterdir()):
            if family_dir.is_dir():
                candidate = family_dir / filename
                if candidate.exists():
                    return candidate
        candidate = base / filename
        if candidate.exists():
            return candidate

    return None


def find_entry_dir(entry_id: str) -> tuple[Optional[Path], Optional[str]]:
    """Find entry and return (path, directory_type)."""
    for dir_type in ["verified", "drafts", "rejected"]:
        path = find_entry(entry_id, [dir_type])
        if path:
            return path, dir_type
    return None, None


# ---------------------------------------------------------------------------
# Feedback log (append-only)
# ---------------------------------------------------------------------------

def append_feedback_log(entry: dict) -> str:
    """Append one entry to feedback_log.jsonl. Returns feedback_id."""
    fb_id = _next_feedback_id()
    entry["feedback_id"] = fb_id
    entry["timestamp"] = _now_iso()

    FEEDBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(FEEDBACK_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return fb_id


# ---------------------------------------------------------------------------
# Product profile / forbidden claims
# ---------------------------------------------------------------------------

def load_profile(family_code: str) -> dict:
    """Load effective product profile for a family."""
    path = PROFILES_DIR / f"{family_code}.yaml"
    if path.exists():
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def check_forbidden_claims(answer: str, profile: dict) -> list[str]:
    """Check if answer violates any forbidden claims from product profile."""
    violations = []
    forbidden = profile.get("forbidden_claims", [])
    answer_lower = answer.lower()

    for claim in forbidden:
        terms = _extract_check_terms(claim)
        for term in terms:
            if _match_term_in_text(term, answer_lower):
                context = _get_context(answer_lower, term.lower())
                if not _is_negated(context, term.lower()):
                    violations.append(
                        f"'{term}' in answer may violate: {claim}"
                    )
    return violations


_STOP_WORDS = frozenset({
    "available", "service", "product", "platform", "not", "does", "use",
    "is", "the", "for", "this", "that", "with", "and", "or", "as", "in",
    "of", "has", "have", "can", "will", "may", "a", "an", "are", "was",
    "were", "been", "being", "be", "to", "from", "by", "on", "at", "it",
    "its", "their", "same", "way", "directly", "natively",
})


def _extract_check_terms(claim: str) -> list[str]:
    """Extract key terms from a forbidden claim for checking."""
    terms = []

    svc_match = re.search(r"Platform service\s+'([^']+)'", claim, re.IGNORECASE)
    if svc_match:
        terms.append(svc_match.group(1))
        return terms

    if not re.search(r'\b(?:NOT|not|does not|do not|cannot|is not)\b', claim):
        cleaned = claim.strip(".,;:'\" ")
        if len(cleaned) >= 3:
            terms.append(cleaned)
        return terms

    matches = re.findall(
        r'(?:NOT|not)\s+(?:use\s+|support\s+|have\s+|offer\s+|integrated\s+with\s+)?(\S+)',
        claim,
    )
    for m in matches:
        cleaned = m.strip(".,;:'\"()")
        if len(cleaned) < 4 and cleaned.upper() != cleaned:
            continue
        if cleaned.lower() in _STOP_WORDS:
            continue
        if cleaned not in terms:
            terms.append(cleaned)

    matches2 = re.findall(
        r'(?:does not|do not|cannot|is not|are not)\s+\w+\s+(\w+(?:\s+\w+)?)',
        claim, re.IGNORECASE,
    )
    for m in matches2:
        cleaned = m.strip(".,;:'\"()")
        if cleaned.lower() in _STOP_WORDS:
            continue
        if len(cleaned) < 4 and cleaned.upper() != cleaned:
            continue
        if cleaned not in terms:
            terms.append(cleaned)

    return terms


def _match_term_in_text(term: str, text_lower: str) -> bool:
    """Check if term appears in text using whole-word matching."""
    term_lower = term.lower()
    if " " in term_lower:
        return term_lower in text_lower
    pattern = r'\b' + re.escape(term_lower) + r'\b'
    return bool(re.search(pattern, text_lower))


def _get_context(text: str, term: str, window: int = 60) -> str:
    """Get surrounding context for a term in text."""
    idx = text.find(term)
    if idx < 0:
        return ""
    start = max(0, idx - window)
    end = min(len(text), idx + len(term) + window)
    return text[start:end]


def _is_negated(context: str, term: str) -> bool:
    """Check if term appears in a negated context."""
    negation_patterns = [
        f"not {term}", f"not use {term}", f"not support {term}",
        f"does not {term}", f"do not {term}", f"cannot {term}",
        f"no {term}", f"without {term}", f"doesn't {term}", f"don't {term}",
    ]
    context_lower = context.lower()
    return any(pat in context_lower for pat in negation_patterns)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_show(entry_id: str) -> int:
    """Show entry details."""
    path, dir_type = find_entry_dir(entry_id)
    if not path:
        print(f"[ERROR] {entry_id} not found in verified/, drafts/, or rejected/")
        return 1

    entry = load_entry(path)
    print(f"\nEntry: {entry_id}")
    print(f"Location: {dir_type}/{entry.get('family_code', '?')}/")
    print(f"Confidence: {entry.get('confidence', '?')}")
    print(f"Family: {entry.get('family_code', '?')}")
    print(f"Category: {entry.get('category', '?')} / {entry.get('subcategory', '')}")
    print(f"Last updated: {entry.get('last_updated', '?')}")
    print(f"\nQuestion:\n  {entry.get('question', '?')}")
    print(f"\nAnswer:\n  {entry.get('answer', '?')[:500]}")
    if len(entry.get("answer", "")) > 500:
        print(f"  ... ({len(entry['answer'])} chars total)")

    tags = entry.get("tags", [])
    if tags:
        print(f"\nTags: {', '.join(tags)}")

    history = entry.get("feedback_history", [])
    if history:
        print(f"\nFeedback history ({len(history)} entries):")
        for h in history[-5:]:
            print(f"  [{h.get('timestamp', '?')}] {h.get('action', '?')}: "
                  f"{h.get('reason', h.get('correction', ''))[:80]}")

    return 0


def cmd_correct_offline(entry_id: str, text: str, dry_run: bool = True) -> int:
    """Correct an entry's answer directly (no LLM, text IS the new answer)."""
    path, dir_type = find_entry_dir(entry_id)
    if not path:
        print(f"[ERROR] {entry_id} not found")
        return 1

    entry = load_entry(path)
    family = entry.get("family_code", "")
    profile = load_profile(family)

    before_answer = entry["answer"]
    before_hash = _content_hash(before_answer)
    after_hash = _content_hash(text)

    print(f"\nEntry: {entry_id}")
    print(f"Question: {entry['question'][:100]}")
    print(f"\nBEFORE:")
    print(f"  {before_answer[:300]}{'...' if len(before_answer) > 300 else ''}")
    print(f"\nAFTER:")
    print(f"  {text[:300]}{'...' if len(text) > 300 else ''}")

    violations = check_forbidden_claims(text, profile)
    if violations:
        print(f"\n[WARN] New answer has {len(violations)} forbidden claim issue(s):")
        for v in violations:
            print(f"  - {v}")

    if dry_run:
        print("\n[DRY RUN] No changes made.")
        return 0

    entry["answer"] = text
    entry["last_updated"] = _today()
    entry.setdefault("feedback_history", []).append({
        "action": "corrected",
        "timestamp": _now_iso(),
        "correction": "(direct replacement)",
        "before_hash": before_hash,
        "after_hash": after_hash,
    })

    save_entry(entry, path)

    append_feedback_log({
        "action": "correct",
        "entry_id": entry_id,
        "before_hash": before_hash,
        "after_hash": after_hash,
        "correction": "(direct replacement)",
        "family": family,
    })

    print(f"\n[OK] {entry_id} corrected and saved")
    return 0


def cmd_search(query: str, family: str | None = None,
               top_k: int = 10) -> int:
    """Search KB entries by text."""
    results = _text_search(query, family, limit=top_k)

    if not results:
        print("[INFO] No matching entries found")
        return 0

    print(f"\nSearch results for \"{query}\":")
    if family:
        print(f"  (filtered to family: {family})")
    print()

    for i, r in enumerate(results, 1):
        print(f"  {i}. [{r['id']}] {r.get('question', '')[:80]}")

    return 0


def _text_search(query: str, family: str | None = None,
                 limit: int = 10) -> list[dict]:
    """Simple text search across KB entries."""
    results = []
    query_lower = query.lower()

    search_dirs = [VERIFIED_DIR]
    if DRAFTS_DIR.exists():
        search_dirs.append(DRAFTS_DIR)

    for base in search_dirs:
        if not base.exists():
            continue
        for json_file in base.rglob("*.json"):
            try:
                entry = load_entry(json_file)
            except (json.JSONDecodeError, OSError):
                continue

            if family and entry.get("family_code") != family:
                continue

            text = (entry.get("question", "") + " " +
                    entry.get("answer", "")).lower()
            if query_lower in text:
                results.append({
                    "id": entry.get("id", json_file.stem),
                    "question": entry.get("question", ""),
                    "family": entry.get("family_code", ""),
                })

            if len(results) >= limit:
                break

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="KB Feedback CLI -- show, correct, search entries",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python src/rfp_feedback.py show KB_0234
  python src/rfp_feedback.py correct KB_0234 --text "New answer" --offline --dry-run
  python src/rfp_feedback.py correct KB_0234 --text "New answer" --offline --apply
  python src/rfp_feedback.py search "JSON ingestion" --family planning
""",
    )
    sub = parser.add_subparsers(dest="command", help="Command")

    # show
    p_show = sub.add_parser("show", help="Show entry details")
    p_show.add_argument("entry_id", help="Entry ID (e.g., KB_0234)")

    # correct
    p_correct = sub.add_parser("correct", help="Correct an entry's answer")
    p_correct.add_argument("entry_id", help="Entry ID")
    p_correct.add_argument("--text", required=True, help="New answer text")
    p_correct.add_argument("--dry-run", action="store_true", default=True,
                           help="Preview changes without applying (default)")
    p_correct.add_argument("--apply", action="store_true",
                           help="Actually apply the correction")
    p_correct.add_argument("--offline", action="store_true",
                           help="Use --text as literal new answer (no LLM)")

    # search
    p_search = sub.add_parser("search", help="Search KB entries")
    p_search.add_argument("query", help="Search text")
    p_search.add_argument("--family", help="Filter by family code")
    p_search.add_argument("--top", type=int, default=10, help="Max results")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "show":
        return cmd_show(args.entry_id)

    elif args.command == "correct":
        dry_run = not args.apply
        return cmd_correct_offline(args.entry_id, args.text, dry_run=dry_run)

    elif args.command == "search":
        return cmd_search(args.query, family=args.family, top_k=args.top)

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
