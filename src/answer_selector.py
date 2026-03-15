"""5-Stage Answer Selection Algorithm for KB IMPROVE Mode.

Decides whether to KEEP existing KB entry, REPLACE it with a new one
from a historical RFP, or ADD the new entry as a separate topic.

Stages:
  0. Hard Gates — instant reject/accept on red flags and deprecated terms
  1. Similarity Bucketing — COMPARE / TOPIC_CHECK / ADD_NEW
  2. Heuristic Scoring — score both answers on quality signals
  3. Decision Logic — clear winner or tie zone
  4. LLM Judge — topic guard + answer comparison (only for ties)

Conservative bias: when in doubt, KEEP EXISTING.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np


class NumpyEncoder(json.JSONEncoder):
    """JSON encoder that handles numpy types from embeddings."""

    def default(self, obj):
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Red flags — answer is garbage, don't use it
RED_FLAG_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"see attached",
        r"refer to (?:the )?(?:attached|appendix|document|section)",
        r"as (?:discussed|mentioned|agreed) (?:in |during )?(?:our |the )?(?:meeting|call|session)",
        r"please see (?:the )?(?:attached|appendix|separate)",
        r"attached herewith",
        r"per our (?:earlier |previous )?(?:discussion|conversation)",
        r"will be provided (?:separately|later|upon request)",
        r"to be (?:confirmed|determined|discussed)",
        r"\bTBD\b",
        r"\bN/?A\b",
        r"\bnot applicable\b",
    ]
]

# Deprecated branding/products
DEPRECATED_TERMS = [
    "JDA Software",
    "JDA ",  # trailing space to avoid matching inside words
    "Luminate",
    "i2 Technologies",
    "Manugistics",
    "RedPrairie",
]

# Blue Yonder product/tech terms — positive specificity signals
BY_TERMS = [
    "Blue Yonder",
    "BY Platform",
    "Snowflake",
    "Azure",
    "AKS",
    "Kubernetes",
    "microservices",
    "SaaS",
    "REST API",
    "GraphQL",
    "OAuth2",
    "SAML",
    "SSO",
    "SOC 2",
    "SOC2",
    "ISO 27001",
    "GDPR",
    "Platform Data Cloud",
    "PDC",
    "Stratosphere",
    "Demand Planning",
    "Supply Planning",
    "Fulfillment",
    "Cognitive Planning",
    "Machine Learning",
    "GenAI",
]

# Modern tech terms for currency scoring
MODERN_TERMS = [
    "cloud-native",
    "microservices",
    "kubernetes",
    "saas",
    "azure",
    "genai",
    "ai/ml",
]

# Concrete detail patterns (percentages, SLAs, compliance)
CONCRETE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\d+\.?\d*\s*%",
        r"\d+/\d+",
        r"(?:SOC|ISO|GDPR|HIPAA)",
        r"(?:RTO|RPO)\s*[<\u2264=]\s*\d",
        r"\d+\s*(?:hours?|minutes?|days?|ms|seconds?)",
    ]
]

# Thresholds
SIMILARITY_HIGH = 0.85
SIMILARITY_LOW = 0.70
REPLACE_DELTA = 4
LLM_BUDGET_PER_FILE = 50

# ---------------------------------------------------------------------------
# Stage 0: Hard Gates
# ---------------------------------------------------------------------------


def _count_red_flags(text: str) -> int:
    """Count red flag pattern matches in text."""
    return sum(1 for p in RED_FLAG_PATTERNS if p.search(text))


def _count_deprecated(text: str) -> int:
    """Count deprecated term occurrences in text."""
    lower = text.lower()
    return sum(1 for term in DEPRECATED_TERMS if term.lower() in lower)


def apply_hard_gates(
    existing_answer: str, new_answer: str, client_name: str = ""
) -> Optional[str]:
    """Apply hard gates. Returns instant decision or None.

    Returns:
        "KEEP_EXISTING" — new answer has red flags or deprecated terms
        "REPLACE" — existing answer has red flags or deprecated terms
        None — no instant decision, continue to scoring
    """
    new_flags = _count_red_flags(new_answer)
    existing_flags = _count_red_flags(existing_answer)

    # Client name leakage
    if client_name:
        cl = client_name.lower()
        if cl in new_answer.lower():
            new_flags += 2
        if cl in existing_answer.lower():
            existing_flags += 2

    # Deprecated terms
    new_deprecated = _count_deprecated(new_answer)
    existing_deprecated = _count_deprecated(existing_answer)

    # Gate logic
    if new_flags >= 2 and existing_flags == 0:
        return "KEEP_EXISTING"
    if existing_flags >= 2 and new_flags == 0:
        return "REPLACE"
    if new_deprecated >= 1 and existing_deprecated == 0:
        return "KEEP_EXISTING"
    if existing_deprecated >= 1 and new_deprecated == 0:
        return "REPLACE"

    return None


# ---------------------------------------------------------------------------
# Stage 1: Similarity Bucketing
# ---------------------------------------------------------------------------


def get_similarity_action(similarity: float) -> str:
    """Determine action based on similarity bucket.

    Returns:
        "COMPARE" — same topic, compare answers (>= 0.85)
        "TOPIC_CHECK" — maybe same topic, needs verification (0.70-0.85)
        "ADD_NEW" — different topic (< 0.70)
    """
    if similarity >= SIMILARITY_HIGH:
        return "COMPARE"
    elif similarity >= SIMILARITY_LOW:
        return "TOPIC_CHECK"
    else:
        return "ADD_NEW"


# ---------------------------------------------------------------------------
# Stage 2: Heuristic Scoring
# ---------------------------------------------------------------------------


def score_answer(answer: str) -> dict:
    """Score an answer on multiple quality signals.

    Returns dict with individual scores and total.
    """
    scores = {}
    lower = answer.lower()

    # Red flags: -5 each (uncapped)
    red_count = _count_red_flags(answer)
    scores["red_flags"] = red_count * -5

    # BY product/tech terms: +2 each, cap at +8
    by_count = sum(1 for term in BY_TERMS if term.lower() in lower)
    scores["specificity"] = min(by_count * 2, 8)

    # Concrete details (percentages, SLAs): +2 each, cap +8
    concrete_count = sum(1 for p in CONCRETE_PATTERNS if p.search(answer))
    scores["concrete_details"] = min(concrete_count * 2, 8)

    # Structure: +2 for bullets/numbers, -1 for wall of text
    has_structure = bool(re.search(r"(?:\d+[.\)]\s|[-\u2022]\s|\n\n)", answer))
    is_wall = len(answer) > 500 and "\n" not in answer
    scores["structure"] = 2 if has_structure else (-1 if is_wall else 0)

    # Currency (modern terms): +2
    has_modern = any(term in lower for term in MODERN_TERMS)
    scores["currency"] = 2 if has_modern else 0

    # Deprecated terms: -3 each
    deprecated_count = _count_deprecated(answer)
    scores["deprecated"] = deprecated_count * -3

    # Total
    scores["total"] = sum(scores.values())

    return scores


# ---------------------------------------------------------------------------
# Stage 3: Decision Logic
# ---------------------------------------------------------------------------


def make_decision(
    existing_answer: str,
    new_answer: str,
    similarity: float,
    client_name: str = "",
    existing_date: str = "",
    new_date: str = "",
) -> dict:
    """Run stages 0-3. Returns decision dict (may be LLM_JUDGE for ties)."""
    result = {"similarity": similarity, "scores": {}}

    # Stage 0: Hard gates
    gate = apply_hard_gates(existing_answer, new_answer, client_name)
    if gate:
        result["decision"] = gate
        result["stage"] = "gate"
        result["reason"] = (
            "New has red flags/deprecated"
            if gate == "KEEP_EXISTING"
            else "Existing has red flags/deprecated"
        )
        return result

    # Stage 1: Similarity bucket
    action = get_similarity_action(similarity)
    if action == "ADD_NEW":
        result["decision"] = "ADD_NEW"
        result["stage"] = "similarity"
        result["reason"] = (
            f"Low similarity ({similarity:.2f} < {SIMILARITY_LOW}) -- different topic"
        )
        return result

    if action == "TOPIC_CHECK":
        result["needs_topic_check"] = True

    # Stage 2: Score both answers
    existing_scores = score_answer(existing_answer)
    new_scores = score_answer(new_answer)

    # Recency bonus
    if new_date and existing_date and new_date > existing_date:
        new_scores["recency"] = 2
        new_scores["total"] += 2

    result["scores"] = {"existing": existing_scores, "new": new_scores}

    # Stage 3: Decision
    delta = new_scores["total"] - existing_scores["total"]

    if delta >= REPLACE_DELTA:
        result["decision"] = "REPLACE"
        result["stage"] = "scoring"
        result["reason"] = (
            f"New scores {delta} points higher "
            f"({new_scores['total']} vs {existing_scores['total']})"
        )
        return result

    if delta <= -REPLACE_DELTA:
        result["decision"] = "KEEP_EXISTING"
        result["stage"] = "scoring"
        result["reason"] = (
            f"Existing scores {-delta} points higher "
            f"({existing_scores['total']} vs {new_scores['total']})"
        )
        return result

    # Tie zone
    result["decision"] = "LLM_JUDGE"
    result["stage"] = "scoring_tie"
    result["reason"] = f"Score tie zone (delta={delta}), needs LLM judge"
    return result


# ---------------------------------------------------------------------------
# Stage 4: LLM Judge
# ---------------------------------------------------------------------------

TOPIC_GUARD_PROMPT = """Are these two RFP questions about the SAME topic?

Question A: {q_existing}
Question B: {q_new}

Return JSON only:
{{"same_topic": true/false, "confidence": 1-10, "reason": "2-3 words"}}

Rules:
- "Same topic" means a single KB answer could address both
- Related but distinct questions = NOT same topic
- Example SAME: "What database do you use?" vs "Which DB technology powers the platform?"
- Example NOT SAME: "What database do you use?" vs "How do you handle database backups?"
"""

ANSWER_JUDGE_PROMPT = """Which answer is better for a reusable RFP knowledge base?

Question: {question}

Answer A (existing):
{existing_answer}

Answer B (new):
{new_answer}

Evaluation criteria:
1. Self-contained (no "see attached", no references to external docs)
2. Specific (names products, features, technologies)
3. Reusable (works for any client, no client-specific details)
4. Professional (BY pre-sales tone, confident but accurate)
5. Current (modern terminology, current product names)

Return JSON only:
{{"winner": "A" or "B", "confidence": 1-10, "reason": "one sentence"}}

IMPORTANT: If unsure or confidence < 8, choose "A" (keep existing).
"""


def _parse_llm_json_obj(text: str) -> dict:
    """Parse LLM response as JSON object with 3 fallback strategies."""
    if not text:
        return {}

    # Strategy 1: direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Strategy 2: strip markdown code fences
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            pass

    # Strategy 3: regex extract JSON object
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except (json.JSONDecodeError, TypeError):
            pass

    return {}


def llm_topic_check(q_existing: str, q_new: str, llm_call) -> dict:
    """Check if two questions are about the same topic.

    Args:
        llm_call: callable(prompt: str) -> str (returns raw LLM text)
    """
    prompt = TOPIC_GUARD_PROMPT.format(q_existing=q_existing, q_new=q_new)
    try:
        text = llm_call(prompt)
        result = _parse_llm_json_obj(text)
        return (
            result
            if result
            else {"same_topic": False, "confidence": 0, "reason": "parse error"}
        )
    except Exception:
        return {"same_topic": False, "confidence": 0, "reason": "LLM error"}


def llm_judge(question: str, existing_answer: str, new_answer: str, llm_call) -> dict:
    """LLM judges which answer is better.

    Args:
        llm_call: callable(prompt: str) -> str (returns raw LLM text)
    """
    prompt = ANSWER_JUDGE_PROMPT.format(
        question=question,
        existing_answer=existing_answer[:1500],
        new_answer=new_answer[:1500],
    )
    try:
        text = llm_call(prompt)
        result = _parse_llm_json_obj(text)
        if not result:
            return {
                "winner": "A",
                "confidence": 0,
                "reason": "parse error, defaulting to existing",
            }
        # Conservative: low confidence -> keep existing
        if result.get("confidence", 0) < 8:
            result["winner"] = "A"
            result["reason"] = (
                f"Low confidence ({result.get('confidence')}), defaulting to existing"
            )
        return result
    except Exception:
        return {
            "winner": "A",
            "confidence": 0,
            "reason": "LLM error, defaulting to existing",
        }


# ---------------------------------------------------------------------------
# Stage 5: Full Pipeline
# ---------------------------------------------------------------------------


def select_answer(
    existing_question: str,
    existing_answer: str,
    new_question: str,
    new_answer: str,
    similarity: float,
    llm_call=None,
    client_name: str = "",
    existing_date: str = "",
    new_date: str = "",
    llm_calls_remaining: int = LLM_BUDGET_PER_FILE,
) -> dict:
    """Complete 5-stage answer selection.

    Args:
        llm_call: optional callable(prompt: str) -> str for LLM stages.
            If None, LLM stages are skipped (conservative defaults).

    Returns dict:
        decision: "KEEP_EXISTING" | "REPLACE" | "ADD_NEW"
        reason: human-readable explanation
        stage: which stage made the decision
        scores: heuristic scores for both answers
        similarity: input similarity
        llm_used: whether LLM was called
        llm_response: LLM response dict (if used)
    """
    # Run stages 0-3
    result = make_decision(
        existing_answer,
        new_answer,
        similarity,
        client_name,
        existing_date,
        new_date,
    )
    result["llm_used"] = False
    result["llm_response"] = None

    # Stage 4a: Topic check for 0.70-0.85 zone
    if result.get("needs_topic_check"):
        if llm_call and llm_calls_remaining > 0:
            topic_result = llm_topic_check(existing_question, new_question, llm_call)
            result["llm_used"] = True
            result["llm_response"] = {"topic_check": topic_result}

            if not topic_result.get("same_topic", False):
                result["decision"] = "ADD_NEW"
                result["stage"] = "topic_guard"
                result["reason"] = (
                    f"Different topic (LLM confidence: {topic_result.get('confidence', 0)})"
                )
                result.pop("needs_topic_check", None)
                return result
        else:
            # No LLM or budget exhausted -- conservative: add as new
            result["decision"] = "ADD_NEW"
            result["stage"] = "topic_guard_budget"
            result["reason"] = "Topic uncertain, LLM unavailable -- adding as new"
            result.pop("needs_topic_check", None)
            return result

    result.pop("needs_topic_check", None)

    # Stage 4b: LLM Judge for ties
    if result["decision"] == "LLM_JUDGE":
        if llm_call and llm_calls_remaining > 0:
            judge_result = llm_judge(
                new_question,
                existing_answer,
                new_answer,
                llm_call,
            )
            result["llm_used"] = True
            llm_resp = result.get("llm_response") or {}
            llm_resp["judge"] = judge_result
            result["llm_response"] = llm_resp

            if judge_result.get("winner") == "B":
                result["decision"] = "REPLACE"
                result["stage"] = "llm_judge"
                result["reason"] = (
                    f"LLM chose new (confidence: {judge_result.get('confidence', 0)}): "
                    f"{judge_result.get('reason', '')}"
                )
            else:
                result["decision"] = "KEEP_EXISTING"
                result["stage"] = "llm_judge"
                result["reason"] = (
                    f"LLM chose existing (confidence: {judge_result.get('confidence', 0)}): "
                    f"{judge_result.get('reason', '')}"
                )
        else:
            # No LLM or budget exhausted -- conservative: keep existing
            result["decision"] = "KEEP_EXISTING"
            result["stage"] = "scoring_tie_no_llm"
            result["reason"] = (
                "Tie zone, LLM unavailable -- keeping existing (conservative)"
            )

    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_improve_report(
    decisions: dict, audit_log: list, llm_budget_used: int = 0
) -> None:
    """Print IMPROVE mode summary to console."""
    total = sum(decisions.values())
    border = "=" * 58

    print(f"\n{border}")
    print("  IMPROVE Mode Complete")
    print(f"{border}")
    print(f"  Total processed:        {total:>6}")
    print(f"  {'':->40}")
    print(f"  Added (new topics):     {decisions.get('ADD_NEW', 0):>6}")
    print(f"  Replaced (better):      {decisions.get('REPLACE', 0):>6}")
    print(f"  Kept existing:          {decisions.get('KEEP_EXISTING', 0):>6}")

    # Stage breakdown
    stages = {}
    for entry in audit_log:
        stage = entry.get("stage", "unknown")
        stages[stage] = stages.get(stage, 0) + 1

    if stages:
        print(f"  {'':->40}")
        print("  Decisions by stage:")
        for stage, count in sorted(stages.items()):
            print(f"    {stage:<28} {count:>4}")

    print(f"  {'':->40}")
    print(f"  LLM calls used:         {llm_budget_used:>4} / {LLM_BUDGET_PER_FILE}")
    print(f"{border}")

    # Top replacements
    replacements = [e for e in audit_log if e.get("decision") == "REPLACE"]
    if replacements:
        print("\n  Top replacements:")
        for r in replacements[:5]:
            print(f"    {r.get('reason', '')[:70]}")


def save_improve_report(
    audit_log: list, source_file: str, output_dir: Optional[Path] = None
) -> Path:
    """Save full audit to improve_report.json."""
    report = {
        "source_file": source_file,
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "total": len(audit_log),
            "kept": sum(1 for r in audit_log if r.get("decision") == "KEEP_EXISTING"),
            "replaced": sum(1 for r in audit_log if r.get("decision") == "REPLACE"),
            "added": sum(1 for r in audit_log if r.get("decision") == "ADD_NEW"),
        },
        "decisions": audit_log,
    }
    out_dir = output_dir or Path(__file__).resolve().parents[1] / "data" / "kb"
    path = out_dir / "improve_report.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, cls=NumpyEncoder)
    return path
