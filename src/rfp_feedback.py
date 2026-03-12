"""Feedback CLI for KB entry management.

Provides subcommands to correct, approve, reject, retag, propagate,
show, log, and search KB entries.

Usage:
  python src/rfp_feedback.py correct KB_0234 --text "Fix info" --dry-run
  python src/rfp_feedback.py approve KB_1001
  python src/rfp_feedback.py reject KB_1001 --reason "Outdated"
  python src/rfp_feedback.py retag KB_0234 --product wms_native
  python src/rfp_feedback.py retag KB_0234 --category functional
  python src/rfp_feedback.py propagate KB_0234 --dry-run
  python src/rfp_feedback.py show KB_0234
  python src/rfp_feedback.py log --last 20
  python src/rfp_feedback.py search "JSON bulk ingestion" --family planning
"""

import argparse
import hashlib
import json
import os
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
            # Count lines in feedback log as fallback
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
    """Find an entry by ID across KB directories.

    Search order: verified, drafts, rejected (or specified dirs).
    Returns path to the entry file, or None.
    """
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
        # Search in family subdirs
        for family_dir in sorted(base.iterdir()):
            if family_dir.is_dir():
                candidate = family_dir / filename
                if candidate.exists():
                    return candidate
        # Also check directly in base dir
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


def _entry_location(path: Path) -> str:
    """Return 'verified', 'drafts', or 'rejected' from path."""
    parts = path.parts
    for name in ("verified", "drafts", "rejected"):
        if name in parts:
            return name
    return "unknown"


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


def read_feedback_log(last_n: int = 20) -> list[dict]:
    """Read last N entries from feedback log."""
    if not FEEDBACK_LOG.exists():
        return []
    lines = FEEDBACK_LOG.read_text(encoding="utf-8").strip().split("\n")
    lines = [l for l in lines if l.strip()]
    entries = []
    for line in lines[-last_n:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


# ---------------------------------------------------------------------------
# Product profile / forbidden claims
# ---------------------------------------------------------------------------

def load_profile(family_code: str) -> dict:
    """Load effective product profile for a family."""
    # Try exact match first, then try family as-is
    candidates = [
        PROFILES_DIR / f"{family_code}.yaml",
    ]
    for path in candidates:
        if path.exists():
            import yaml
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
    return {}


def check_forbidden_claims(answer: str, profile: dict) -> list[str]:
    """Check if answer violates any forbidden claims from product profile.

    Returns list of violation descriptions.
    """
    violations = []
    forbidden = profile.get("forbidden_claims", [])
    answer_lower = answer.lower()

    for claim in forbidden:
        terms = _extract_check_terms(claim)
        for term in terms:
            if _match_term_in_text(term, answer_lower):
                # Check if the term is used in a negated context
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
    """Extract key terms from a forbidden claim for checking.

    Handles three patterns:
    1. "Platform service 'X' is not available..." -> ["X"]
    2. Bare claims: "GraphQL APIs" -> ["GraphQL APIs"]
    3. Negation: "Does NOT use Snowflake" -> ["Snowflake"]
    """
    terms = []

    # Pattern 1: Platform service with quoted name
    svc_match = re.search(r"Platform service\s+'([^']+)'", claim, re.IGNORECASE)
    if svc_match:
        terms.append(svc_match.group(1))
        return terms

    # Pattern 2: Bare technology claims (no negation)
    if not re.search(r'\b(?:NOT|not|does not|do not|cannot|is not)\b', claim):
        cleaned = claim.strip(".,;:'\" ")
        if len(cleaned) >= 3:
            terms.append(cleaned)
        return terms

    # Pattern 3: Negation claims
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
        f"not {term}",
        f"not use {term}",
        f"not support {term}",
        f"does not {term}",
        f"do not {term}",
        f"cannot {term}",
        f"no {term}",
        f"without {term}",
        f"doesn't {term}",
        f"don't {term}",
    ]
    context_lower = context.lower()
    return any(pat in context_lower for pat in negation_patterns)


# ---------------------------------------------------------------------------
# LLM call for corrections
# ---------------------------------------------------------------------------

def call_llm_correct(question: str, current_answer: str,
                     correction: str, profile: dict,
                     model: str = "gemini-flash") -> str:
    """Call LLM to apply a correction to an answer.

    Returns the corrected answer text.
    """
    family = profile.get("product", "unknown")
    forbidden = profile.get("forbidden_claims", [])
    forbidden_text = "\n".join(f"  - {c}" for c in forbidden[:20]) if forbidden else "  (none)"

    prompt = f"""You are editing a knowledge base entry for Blue Yonder's {family} product.

CURRENT QUESTION:
{question}

CURRENT ANSWER:
{current_answer}

CORRECTION INSTRUCTION:
{correction}

FORBIDDEN CLAIMS (do NOT include these in the answer):
{forbidden_text}

Apply the correction instruction to the answer. Keep the same professional tone
and structure. Do NOT add information beyond what the correction specifies.
Output ONLY the corrected answer text, nothing else."""

    # Import call_llm from kb_extract_historical
    from kb_extract_historical import call_llm
    return call_llm(prompt, model=model)


# ---------------------------------------------------------------------------
# Similarity search for propagate
# ---------------------------------------------------------------------------

def search_similar(query: str, family: str | None = None,
                   threshold: float = 0.75,
                   exclude: list[str] | None = None,
                   top_k: int = 20) -> list[dict]:
    """Search for similar entries using ChromaDB.

    Returns list of entries with similarity scores.
    """
    try:
        import chromadb
        from chromadb.utils import embedding_functions
    except ImportError:
        print("[WARN] chromadb not available, cannot search")
        return []

    chroma_path = KB_DIR / "chroma_store"
    if not chroma_path.exists():
        return []

    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="BAAI/bge-large-en-v1.5"
    )
    client = chromadb.PersistentClient(path=str(chroma_path))

    try:
        collection = client.get_collection(
            name="rfp_knowledge_base",
            embedding_function=ef,
        )
    except Exception:
        return []

    where_filter = None
    if family:
        where_filter = {"domain": family}

    results = collection.query(
        query_texts=[query],
        n_results=top_k,
        where=where_filter,
    )

    if not results or not results["ids"] or not results["ids"][0]:
        return []

    exclude_set = set(exclude or [])
    similar = []
    ids = results["ids"][0]
    distances = results["distances"][0] if results.get("distances") else [0] * len(ids)
    metadatas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(ids)

    for chroma_id, dist, meta in zip(ids, distances, metadatas):
        # ChromaDB returns L2 distances; convert to approximate similarity
        # For normalized embeddings, similarity ~ 1 - distance/2
        similarity = max(0.0, 1.0 - dist / 2.0)
        if similarity < threshold:
            continue

        kb_id = meta.get("kb_id", chroma_id)
        if kb_id in exclude_set:
            continue

        similar.append({
            "id": kb_id,
            "question": meta.get("canonical_question", ""),
            "answer": meta.get("canonical_answer", ""),
            "family": meta.get("domain", ""),
            "similarity": round(similarity, 3),
        })

    return similar


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


def cmd_correct(entry_id: str, text: str, dry_run: bool = True,
                model: str = "gemini-flash") -> int:
    """Correct an entry's answer using LLM."""
    path, dir_type = find_entry_dir(entry_id)
    if not path:
        print(f"[ERROR] {entry_id} not found")
        return 1

    entry = load_entry(path)
    family = entry.get("family_code", "")
    profile = load_profile(family)

    before_answer = entry["answer"]
    before_hash = _content_hash(before_answer)

    # Call LLM to apply correction
    print(f"[INFO] Calling LLM to apply correction...")
    corrected = call_llm_correct(
        question=entry["question"],
        current_answer=before_answer,
        correction=text,
        profile=profile,
        model=model,
    )

    if not corrected or corrected.strip() == before_answer.strip():
        print("[INFO] No changes produced by LLM")
        return 0

    after_hash = _content_hash(corrected)

    # Show diff
    print(f"\nEntry: {entry_id}")
    print(f"Question: {entry['question'][:100]}")
    print(f"\nBEFORE:")
    print(f"  {before_answer[:300]}{'...' if len(before_answer) > 300 else ''}")
    print(f"\nAFTER:")
    print(f"  {corrected[:300]}{'...' if len(corrected) > 300 else ''}")
    print(f"\nReason: {text}")

    # Check forbidden claims on corrected answer
    violations = check_forbidden_claims(corrected, profile)
    if violations:
        print(f"\n[WARN] Corrected answer has {len(violations)} forbidden claim issue(s):")
        for v in violations:
            print(f"  - {v}")

    if dry_run:
        print("\n[DRY RUN] No changes made.")
        return 0

    # Apply
    entry["answer"] = corrected
    entry["last_updated"] = _today()
    entry.setdefault("feedback_history", []).append({
        "action": "corrected",
        "timestamp": _now_iso(),
        "correction": text,
        "before_hash": before_hash,
        "after_hash": after_hash,
    })

    save_entry(entry, path)

    append_feedback_log({
        "action": "correct",
        "entry_id": entry_id,
        "before_hash": before_hash,
        "after_hash": after_hash,
        "correction": text,
        "family": family,
    })

    print(f"\n[OK] {entry_id} corrected and saved")
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


def cmd_approve(entry_id: str) -> int:
    """Approve a draft entry -> move to verified."""
    path = find_entry(entry_id, ["drafts"])
    if not path:
        print(f"[ERROR] {entry_id} not found in drafts/")
        return 1

    entry = load_entry(path)
    family = entry.get("family_code", "unknown")
    profile = load_profile(family)

    # Check forbidden claims
    violations = check_forbidden_claims(entry.get("answer", ""), profile)
    if violations:
        print(f"[WARN] Answer violates {len(violations)} forbidden claim(s):")
        for v in violations:
            print(f"  - {v}")
        try:
            resp = input("Approve anyway? [y/N]: ").strip().lower()
        except EOFError:
            resp = ""
        if resp != "y":
            print("[ABORT] Not approved")
            return 1

    # Update entry
    entry["confidence"] = "verified"
    entry["last_updated"] = _today()
    entry.setdefault("feedback_history", []).append({
        "action": "approved",
        "timestamp": _now_iso(),
        "reason": "Manual review",
    })

    # Move: drafts -> verified
    verified_path = VERIFIED_DIR / family / f"{entry_id}.json"
    save_entry(entry, verified_path)
    path.unlink()

    append_feedback_log({
        "action": "approve",
        "entry_id": entry_id,
        "from": "drafts",
        "to": "verified",
        "family": family,
    })

    print(f"[OK] {entry_id} promoted to verified/{family}/")
    return 0


def cmd_reject(entry_id: str, reason: str) -> int:
    """Reject a draft entry -> move to rejected."""
    path = find_entry(entry_id, ["drafts"])
    if not path:
        print(f"[ERROR] {entry_id} not found in drafts/")
        return 1

    entry = load_entry(path)
    family = entry.get("family_code", "unknown")

    entry["confidence"] = "rejected"
    entry["last_updated"] = _today()
    entry.setdefault("feedback_history", []).append({
        "action": "rejected",
        "timestamp": _now_iso(),
        "reason": reason,
    })

    # Move: drafts -> rejected
    rejected_path = REJECTED_DIR / family / f"{entry_id}.json"
    save_entry(entry, rejected_path)
    path.unlink()

    append_feedback_log({
        "action": "reject",
        "entry_id": entry_id,
        "reason": reason,
        "family": family,
    })

    print(f"[OK] {entry_id} moved to rejected/{family}/ -- {reason}")
    return 0


def cmd_retag(entry_id: str, product: str | None = None,
              category: str | None = None) -> int:
    """Retag an entry's product/family or category."""
    path, dir_type = find_entry_dir(entry_id)
    if not path:
        print(f"[ERROR] {entry_id} not found")
        return 1

    entry = load_entry(path)
    changes = []

    if product:
        old_family = entry.get("family_code", "")
        entry["family_code"] = product
        changes.append(f"family_code: {old_family} -> {product}")

        # Move file to new family directory if in verified/drafts
        if dir_type in ("verified", "drafts"):
            base = VERIFIED_DIR if dir_type == "verified" else DRAFTS_DIR
            new_path = base / product / f"{entry_id}.json"
            save_entry(entry, new_path)
            path.unlink()
            # Clean up empty dirs
            if path.parent.exists() and not any(path.parent.iterdir()):
                path.parent.rmdir()
            path = new_path

        append_feedback_log({
            "action": "retag",
            "entry_id": entry_id,
            "field": "family_code",
            "old": old_family,
            "new": product,
        })

    if category:
        old_cat = entry.get("category", "")
        entry["category"] = category
        changes.append(f"category: {old_cat} -> {category}")

        append_feedback_log({
            "action": "retag",
            "entry_id": entry_id,
            "field": "category",
            "old": old_cat,
            "new": category,
        })

    if not changes:
        print("[ERROR] Specify --product and/or --category")
        return 1

    entry["last_updated"] = _today()
    entry.setdefault("feedback_history", []).append({
        "action": "retagged",
        "timestamp": _now_iso(),
        "changes": changes,
    })

    save_entry(entry, path)
    print(f"[OK] {entry_id} retagged: {'; '.join(changes)}")
    return 0


def cmd_propagate(entry_id: str, dry_run: bool = True) -> int:
    """Find entries similar to a corrected one that may need same fix."""
    path, dir_type = find_entry_dir(entry_id)
    if not path:
        print(f"[ERROR] {entry_id} not found")
        return 1

    entry = load_entry(path)
    history = entry.get("feedback_history", [])

    # Find last correction
    last_correction = None
    for h in reversed(history):
        if h.get("action") == "corrected":
            last_correction = h
            break

    if not last_correction:
        print(f"[INFO] {entry_id} has no correction history to propagate")
        return 0

    correction_text = last_correction.get("correction", "")
    family = entry.get("family_code", "")

    print(f"[INFO] Searching for entries similar to {entry_id} "
          f"in family '{family}'...")

    similar = search_similar(
        entry["question"],
        family=family,
        threshold=0.75,
        exclude=[entry_id],
    )

    if not similar:
        print("[INFO] No similar entries found above threshold")
        return 0

    # Check which similar entries might have the same issue
    flagged = []
    for sim in similar:
        if _has_same_issue(sim, last_correction):
            flagged.append(sim)

    if not flagged:
        print(f"[INFO] Found {len(similar)} similar entries but none "
              f"appear to have the same issue")
        return 0

    print(f"\nFound {len(flagged)} entries that may need same correction:")
    print(f"Original correction: \"{correction_text}\"")
    print()
    for f_entry in flagged:
        print(f"  {f_entry['id']}: {f_entry['question'][:80]}...")
        print(f"    similarity: {f_entry['similarity']}, family: {f_entry['family']}")

    if dry_run:
        print(f"\n[DRY RUN] No changes made. "
              f"Run without --dry-run to flag {len(flagged)} entries.")
        return 0

    # Flag entries for review
    flagged_ids = []
    for f_entry in flagged:
        f_path, _ = find_entry_dir(f_entry["id"])
        if f_path:
            full_entry = load_entry(f_path)
            full_entry.setdefault("feedback_history", []).append({
                "action": "flagged_for_review",
                "timestamp": _now_iso(),
                "reason": f"Propagated from {entry_id} correction",
                "related_correction": correction_text,
            })
            save_entry(full_entry, f_path)
            flagged_ids.append(f_entry["id"])

    append_feedback_log({
        "action": "propagate",
        "source_entry": entry_id,
        "flagged_entries": flagged_ids,
        "correction": correction_text,
    })

    print(f"\n[OK] {len(flagged_ids)} entries flagged for review")
    return 0


def _has_same_issue(sim_entry: dict, correction: dict) -> bool:
    """Check if a similar entry might have the same issue as a correction.

    Simple keyword overlap heuristic.
    """
    correction_text = correction.get("correction", "").lower()
    answer = sim_entry.get("answer", "").lower()

    # Extract key terms from correction (words > 3 chars, not stop words)
    stop_words = {"the", "and", "for", "that", "this", "with", "from", "only",
                  "not", "are", "was", "were", "been", "have", "has", "will",
                  "should", "remove", "change", "update", "fix", "correct",
                  "replace", "delete", "instead", "also", "but"}
    words = re.findall(r'\b\w{4,}\b', correction_text)
    key_terms = [w for w in words if w not in stop_words]

    if not key_terms:
        return False

    # Check if answer contains any of the correction's key terms
    matches = sum(1 for t in key_terms if t in answer)
    return matches >= 1


def cmd_log(last_n: int = 20) -> int:
    """Show recent feedback log entries."""
    entries = read_feedback_log(last_n)
    if not entries:
        print("[INFO] No feedback log entries found")
        return 0

    print(f"\nRecent feedback ({len(entries)} entries):")
    print(f"{'ID':<10} {'Time':<20} {'Action':<10} {'Entry':<15} {'Details'}")
    print(f"{'-'*10} {'-'*20} {'-'*10} {'-'*15} {'-'*30}")

    for e in entries:
        fb_id = e.get("feedback_id", "?")
        ts = e.get("timestamp", "?")
        action = e.get("action", "?")
        eid = e.get("entry_id", e.get("source_entry", "?"))
        details = ""
        if action == "correct":
            details = e.get("correction", "")[:40]
        elif action == "reject":
            details = e.get("reason", "")[:40]
        elif action == "retag":
            details = f"{e.get('field', '')}: {e.get('old', '')} -> {e.get('new', '')}"
        elif action == "propagate":
            flagged = e.get("flagged_entries", [])
            details = f"flagged {len(flagged)} entries"

        print(f"{fb_id:<10} {ts:<20} {action:<10} {eid:<15} {details}")

    return 0


def cmd_search(query: str, family: str | None = None,
               top_k: int = 10) -> int:
    """Search KB entries by text."""
    results = search_similar(query, family=family, threshold=0.5, top_k=top_k)

    if not results:
        # Fallback: simple text search across verified files
        results = _text_search(query, family, limit=top_k)

    if not results:
        print("[INFO] No matching entries found")
        return 0

    print(f"\nSearch results for \"{query}\":")
    if family:
        print(f"  (filtered to family: {family})")
    print()

    for i, r in enumerate(results, 1):
        sim = r.get("similarity", "")
        sim_str = f" ({sim:.0%})" if isinstance(sim, float) else ""
        print(f"  {i}. [{r['id']}]{sim_str} {r.get('question', '')[:80]}")

    return 0


def _text_search(query: str, family: str | None = None,
                 limit: int = 10) -> list[dict]:
    """Simple text search fallback when ChromaDB is unavailable."""
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
# Usage tracking
# ---------------------------------------------------------------------------

def record_usage(entry_id: str) -> bool:
    """Increment usage_count on an entry. Returns True if updated."""
    path, _ = find_entry_dir(entry_id)
    if not path:
        return False

    entry = load_entry(path)
    entry["usage_count"] = entry.get("usage_count", 0) + 1
    entry["last_used"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    save_entry(entry, path)
    return True


# ---------------------------------------------------------------------------
# Auto-promotion
# ---------------------------------------------------------------------------

COOLING_PERIOD_DAYS = 7
MIN_USAGE_COUNT = 3
MIN_QUALITY_AVERAGE = 4.0


def check_promotion_eligibility(entry: dict, profile: dict) -> tuple[bool, list[str]]:
    """Check if a draft entry meets ALL 6 criteria for auto-promotion.

    Returns (eligible, list_of_reasons_why_not).
    """
    reasons = []

    # 1. Must be a draft
    if entry.get("confidence") != "draft":
        reasons.append("not a draft")

    # 2. Used in 3+ real RFPs
    usage = entry.get("usage_count", 0)
    if usage < MIN_USAGE_COUNT:
        reasons.append(f"usage_count={usage} (need >={MIN_USAGE_COUNT})")

    # 3. Zero corrections in feedback_history
    history = entry.get("feedback_history", [])
    corrections = [h for h in history if h.get("action") == "corrected"]
    if corrections:
        reasons.append(f"{len(corrections)} correction(s) in history")

    # 4. No forbidden claim violations
    answer = entry.get("answer", "")
    violations = check_forbidden_claims(answer, profile)
    if violations:
        reasons.append(f"{len(violations)} forbidden claim violation(s)")

    # 5. LLM quality score average >= 4.0
    quality = entry.get("_quality", {})
    avg = quality.get("average", 0)
    if avg < MIN_QUALITY_AVERAGE:
        reasons.append(f"quality avg={avg} (need >={MIN_QUALITY_AVERAGE})")

    # 6. At least 7 days old (cooling period)
    created = entry.get("generated_at") or entry.get("last_updated") or ""
    if created:
        try:
            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            age_days = (datetime.now() - created_dt.replace(tzinfo=None)).days
            if age_days < COOLING_PERIOD_DAYS:
                reasons.append(f"age={age_days}d (need >={COOLING_PERIOD_DAYS}d)")
        except (ValueError, TypeError):
            reasons.append("cannot parse creation date")
    else:
        reasons.append("no creation date")

    return (len(reasons) == 0, reasons)


def cmd_auto_promote(dry_run: bool = True) -> int:
    """Auto-promote qualifying drafts to verified."""
    # Scan all drafts
    if not DRAFTS_DIR.exists():
        print("[INFO] No drafts directory")
        return 0

    drafts = []
    for json_file in sorted(DRAFTS_DIR.rglob("*.json")):
        try:
            entry = load_entry(json_file)
            entry["_path"] = str(json_file)
            drafts.append(entry)
        except (json.JSONDecodeError, OSError):
            continue

    if not drafts:
        print("[INFO] No draft entries found")
        return 0

    print(f"[INFO] Checking {len(drafts)} drafts for auto-promotion...")

    eligible = []
    for entry in drafts:
        family = entry.get("family_code", "")
        profile = load_profile(family)
        ok, reasons = check_promotion_eligibility(entry, profile)
        if ok:
            eligible.append(entry)
        # In verbose/dry-run mode, show non-eligible too
        elif dry_run and entry.get("usage_count", 0) >= 1:
            entry_id = entry.get("id", "?")
            print(f"  [SKIP] {entry_id}: {'; '.join(reasons)}")

    if not eligible:
        print("[INFO] No drafts meet all 6 promotion criteria")
        return 0

    print(f"\n[INFO] {len(eligible)} draft(s) eligible for auto-promotion:")
    for entry in eligible:
        entry_id = entry.get("id", "?")
        family = entry.get("family_code", "?")
        avg = entry.get("_quality", {}).get("average", 0)
        usage = entry.get("usage_count", 0)
        print(f"  {entry_id} ({family}) -- quality={avg}, usage={usage}")

    if dry_run:
        print(f"\n[DRY RUN] No changes made. Use --apply to promote.")
        return 0

    promoted = 0
    for entry in eligible:
        entry_id = entry.get("id", "?")
        family = entry.get("family_code", "unknown")
        old_path = Path(entry.pop("_path"))

        entry["confidence"] = "verified"
        entry["last_updated"] = _today()
        entry.setdefault("feedback_history", []).append({
            "action": "auto_promoted",
            "timestamp": _now_iso(),
            "reason": "Met all 6 auto-promotion criteria",
        })

        new_path = VERIFIED_DIR / family / f"{entry_id}.json"
        save_entry(entry, new_path)
        if old_path.exists():
            old_path.unlink()

        append_feedback_log({
            "action": "auto_promote",
            "entry_id": entry_id,
            "from": "drafts",
            "to": "verified",
            "family": family,
            "usage_count": entry.get("usage_count", 0),
            "quality_avg": entry.get("_quality", {}).get("average", 0),
        })
        promoted += 1

    print(f"\n[OK] {promoted} draft(s) promoted to verified/")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="KB Feedback CLI -- correct, approve, reject, retag entries",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python src/rfp_feedback.py show KB_0234
  python src/rfp_feedback.py correct KB_0234 --text "Remove JSON" --dry-run
  python src/rfp_feedback.py approve KB_1001
  python src/rfp_feedback.py reject KB_1001 --reason "Outdated"
  python src/rfp_feedback.py retag KB_0234 --product wms_native
  python src/rfp_feedback.py propagate KB_0234 --dry-run
  python src/rfp_feedback.py log --last 20
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
    p_correct.add_argument("--text", required=True, help="Correction instruction")
    p_correct.add_argument("--dry-run", action="store_true", default=True,
                           help="Preview changes without applying (default)")
    p_correct.add_argument("--apply", action="store_true",
                           help="Actually apply the correction")
    p_correct.add_argument("--offline", action="store_true",
                           help="Use --text as literal new answer (no LLM)")
    p_correct.add_argument("--model", default="gemini-flash",
                           help="LLM model for correction (default: gemini-flash)")

    # approve
    p_approve = sub.add_parser("approve", help="Approve draft -> verified")
    p_approve.add_argument("entry_id", help="Entry ID in drafts/")

    # reject
    p_reject = sub.add_parser("reject", help="Reject draft -> rejected")
    p_reject.add_argument("entry_id", help="Entry ID in drafts/")
    p_reject.add_argument("--reason", required=True, help="Rejection reason")

    # retag
    p_retag = sub.add_parser("retag", help="Retag entry product/category")
    p_retag.add_argument("entry_id", help="Entry ID")
    p_retag.add_argument("--product", help="New product/family code")
    p_retag.add_argument("--category", help="New category")

    # propagate
    p_prop = sub.add_parser("propagate", help="Find similar entries needing same fix")
    p_prop.add_argument("entry_id", help="Entry ID with recent correction")
    p_prop.add_argument("--dry-run", action="store_true", default=True,
                        help="Preview only (default)")
    p_prop.add_argument("--apply", action="store_true",
                        help="Actually flag entries")

    # log
    p_log = sub.add_parser("log", help="Show recent feedback log")
    p_log.add_argument("--last", type=int, default=20, help="Number of entries")

    # search
    p_search = sub.add_parser("search", help="Search KB entries")
    p_search.add_argument("query", help="Search text")
    p_search.add_argument("--family", help="Filter by family code")
    p_search.add_argument("--top", type=int, default=10, help="Max results")

    # auto-promote
    p_autopromote = sub.add_parser("auto-promote",
                                    help="Auto-promote qualifying drafts")
    p_autopromote.add_argument("--dry-run", action="store_true", default=True,
                               help="Show eligible (default)")
    p_autopromote.add_argument("--apply", action="store_true",
                               help="Actually promote qualifying drafts")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "show":
        return cmd_show(args.entry_id)

    elif args.command == "correct":
        dry_run = not args.apply
        if args.offline:
            return cmd_correct_offline(args.entry_id, args.text, dry_run=dry_run)
        return cmd_correct(args.entry_id, args.text, dry_run=dry_run,
                           model=args.model)

    elif args.command == "approve":
        return cmd_approve(args.entry_id)

    elif args.command == "reject":
        return cmd_reject(args.entry_id, args.reason)

    elif args.command == "retag":
        return cmd_retag(args.entry_id, product=args.product,
                         category=args.category)

    elif args.command == "propagate":
        dry_run = not args.apply
        return cmd_propagate(args.entry_id, dry_run=dry_run)

    elif args.command == "log":
        return cmd_log(args.last)

    elif args.command == "search":
        return cmd_search(args.query, family=args.family, top_k=args.top)

    elif args.command == "auto-promote":
        dry_run = not args.apply
        return cmd_auto_promote(dry_run=dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
