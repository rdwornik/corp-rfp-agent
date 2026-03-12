"""KB Health Evaluation — deterministic self-checks (no LLM).

Runs four checks against verified/ and drafts/ entries:
  1. CONTRADICTIONS: entries vs product profile forbidden_claims
  2. COVERAGE: capability gaps per active product family
  3. QUALITY: programmatic heuristic scoring
  4. CONSISTENCY: missing fields, orphan families

Usage:
  python src/kb_eval.py
  python src/kb_eval.py --check contradictions
  python src/kb_eval.py --check coverage
  python src/kb_eval.py --check quality
  python src/kb_eval.py --check consistency
  python src/kb_eval.py --family wms
  python src/kb_eval.py --json > kb_health.json
  python src/kb_eval.py --compare data/kb/health_history/2026-03-12.json
"""

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
KB_DIR = PROJECT_ROOT / "data" / "kb"
VERIFIED_DIR = KB_DIR / "verified"
DRAFTS_DIR = KB_DIR / "drafts"
PROFILES_DIR = PROJECT_ROOT / "config" / "product_profiles" / "_effective"
HEALTH_DIR = KB_DIR / "health_history"


# ---------------------------------------------------------------------------
# Capability topics for coverage check
# ---------------------------------------------------------------------------

CAPABILITY_TOPICS = {
    "deployment":        ["hosting", "deployment", "azure", "cloud", "saas", "on-prem", "on-premise"],
    "integration":       ["api", "rest", "integration", "sftp", "kafka", "event", "webhook"],
    "security":          ["authentication", "sso", "saml", "oauth", "encryption", "soc2", "iso", "security"],
    "data_management":   ["ingestion", "data", "csv", "parquet", "snowflake", "pdc", "etl"],
    "scalability":       ["performance", "sla", "availability", "rto", "rpo", "scaling", "uptime"],
    "architecture":      ["microservice", "architecture", "platform", "container", "kubernetes"],
    "ui":                ["user interface", "dashboard", "portal", "mobile", "web ui"],
    "compliance":        ["gdpr", "compliance", "audit", "certification", "hipaa"],
    "disaster_recovery": ["disaster", "recovery", "backup", "failover", "continuity"],
    "monitoring":        ["monitoring", "logging", "alert", "siem", "observability"],
}

# Quality red flags
RED_FLAGS = ["see attached", "refer to", "as discussed", "tbd", "n/a",
             "to be confirmed", "please contact"]

# Deprecated terms
DEPRECATED_TERMS = ["jda", "luminate", "i2 technologies", "manugistics", "redprairie"]

# Valid categories
VALID_CATEGORIES = {"technical", "functional", "consulting", "customer_executive",
                    "security", "deployment", "commercial"}

# Required entry fields
REQUIRED_FIELDS = ["id", "question", "answer", "family_code", "category", "confidence"]


# ---------------------------------------------------------------------------
# Entry loading
# ---------------------------------------------------------------------------

def load_all_entries(family_filter: Optional[str] = None,
                     verified_dir: Path = VERIFIED_DIR,
                     drafts_dir: Path = DRAFTS_DIR) -> list[dict]:
    """Load all entries from verified/ and drafts/."""
    entries = []
    for base_dir in [verified_dir, drafts_dir]:
        if not base_dir.exists():
            continue
        for json_file in sorted(base_dir.rglob("*.json")):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    entry = json.load(f)
                if family_filter and entry.get("family_code") != family_filter:
                    continue
                # Tag with directory source
                entry["_dir"] = "drafts" if "drafts" in json_file.parts else "verified"
                entries.append(entry)
            except (json.JSONDecodeError, OSError):
                continue
    return entries


def load_all_profiles(profiles_dir: Path = PROFILES_DIR) -> dict[str, dict]:
    """Load all effective profiles. Returns {family: profile}."""
    profiles = {}
    if not profiles_dir.exists():
        return profiles
    for yaml_file in sorted(profiles_dir.glob("*.yaml")):
        if yaml_file.name.startswith("."):
            continue
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                profile = yaml.safe_load(f) or {}
            profiles[yaml_file.stem] = profile
        except (yaml.YAMLError, OSError):
            continue
    return profiles


# ---------------------------------------------------------------------------
# Helper: negation detection (reused from rfp_feedback)
# ---------------------------------------------------------------------------

def _get_context(text: str, term: str, window: int = 60) -> str:
    """Get surrounding context for a term in text."""
    text_lower = text.lower()
    term_lower = term.lower()
    idx = text_lower.find(term_lower)
    if idx < 0:
        return ""
    start = max(0, idx - window)
    end = min(len(text), idx + len(term) + window)
    return text[start:end]


def _is_negated(context: str, term: str) -> bool:
    """Check if term appears in a negated context."""
    context_lower = context.lower()
    term_lower = term.lower()
    negation_patterns = [
        f"not {term_lower}", f"not use {term_lower}", f"not support {term_lower}",
        f"does not {term_lower}", f"do not {term_lower}", f"cannot {term_lower}",
        f"no {term_lower}", f"without {term_lower}",
        f"doesn't {term_lower}", f"don't {term_lower}",
    ]
    return any(pat in context_lower for pat in negation_patterns)


_STOP_WORDS = frozenset({
    "available", "service", "product", "platform", "not", "does", "use",
    "is", "the", "for", "this", "that", "with", "and", "or", "as", "in",
    "of", "has", "have", "can", "will", "may", "a", "an", "are", "was",
    "were", "been", "being", "be", "to", "from", "by", "on", "at", "it",
    "its", "their", "same", "way", "directly", "natively",
})


def _extract_check_terms(claim: str) -> list[str]:
    """Extract key terms from a forbidden claim.

    Handles three patterns:
    1. Platform service claims: "Platform service 'X' is not available..."
       -> extracts only the quoted service name
    2. Bare technology claims: "GraphQL APIs", "SOAP APIs"
       -> extracts the full claim as a term
    3. Negation claims: "NOT microservices", "Does NOT use Snowflake"
       -> extracts the key technical term after NOT/use/support
    """
    terms = []

    # Pattern 1: Platform service claims with quoted name
    svc_match = re.search(r"Platform service\s+'([^']+)'", claim, re.IGNORECASE)
    if svc_match:
        terms.append(svc_match.group(1))
        return terms

    # Pattern 2: Bare technology claims (no NOT/negation — e.g. "GraphQL APIs", "SOAP APIs")
    if not re.search(r'\b(?:NOT|not|does not|do not|cannot|is not)\b', claim):
        cleaned = claim.strip(".,;:'\" ")
        if len(cleaned) >= 3:
            terms.append(cleaned)
        return terms

    # Pattern 3: Negation claims — extract the technical term
    # "NOT microservices (WMS Classic)" -> "microservices"
    # "Does NOT use Snowflake" -> "Snowflake"
    # "NOT cloud-native in the same way..." -> "cloud-native"
    matches = re.findall(
        r'(?:NOT|not)\s+(?:use\s+|support\s+|have\s+|offer\s+|integrated\s+with\s+)?(\S+)',
        claim,
    )
    for m in matches:
        cleaned = m.strip(".,;:'\"()")
        if len(cleaned) < 4 and cleaned.upper() != cleaned:
            # Skip short words unless they're acronyms (e.g. "API")
            continue
        if cleaned.lower() in _STOP_WORDS:
            continue
        if cleaned not in terms:
            terms.append(cleaned)

    # Also try "Snowflake (...)" pattern — capitalized nouns after negation
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
    """Check if term appears in text, using whole-word matching.

    For multi-word terms (like 'Api Management'), checks substring.
    For single words, checks word boundary to avoid partial matches.
    """
    term_lower = term.lower()
    if " " in term_lower:
        # Multi-word: substring match is fine
        return term_lower in text_lower
    # Single word: whole-word match
    pattern = r'\b' + re.escape(term_lower) + r'\b'
    return bool(re.search(pattern, text_lower))


def _entry_dir(entry: dict) -> str:
    """Return 'drafts' or 'verified' for an entry."""
    if entry.get("_dir"):
        return entry["_dir"]
    return "drafts" if "DRAFT" in entry.get("id", "") else "verified"


# ---------------------------------------------------------------------------
# CHECK 1: Contradictions
# ---------------------------------------------------------------------------

def check_contradictions(entries: list[dict], profiles: dict[str, dict]) -> list[dict]:
    """Check every KB entry against its product profile.

    Returns list of contradiction dicts.
    """
    contradictions = []

    for entry in entries:
        family = entry.get("family_code", "")
        profile = profiles.get(family)
        if not profile:
            continue

        answer = entry.get("answer", "")
        answer_lower = answer.lower()
        entry_id = entry.get("id", "UNKNOWN")
        directory = _entry_dir(entry)

        # Check forbidden_claims
        for claim in profile.get("forbidden_claims", []):
            terms = _extract_check_terms(claim)
            for term in terms:
                if _match_term_in_text(term, answer_lower):
                    context = _get_context(answer, term, 50)
                    if not _is_negated(context, term):
                        contradictions.append({
                            "entry_id": entry_id,
                            "family": family,
                            "type": "forbidden_claim",
                            "claim": claim,
                            "found_term": term,
                            "context": context[:120],
                            "severity": "high",
                            "directory": directory,
                        })

        # Check boolean fields vs answer content
        bool_checks = [
            ("uses_snowflake", "snowflake"),
            ("cloud_native", "cloud-native"),
            ("microservices", "microservice"),
        ]
        for field, keyword in bool_checks:
            value = profile.get(field)
            if value is False and keyword in answer_lower:
                context = _get_context(answer, keyword, 50)
                if not _is_negated(context, keyword):
                    contradictions.append({
                        "entry_id": entry_id,
                        "family": family,
                        "type": "field_contradiction",
                        "claim": f"{field}=false",
                        "found_term": keyword,
                        "context": context[:120],
                        "severity": "high",
                        "directory": directory,
                    })

        # Check unavailable platform services
        not_available = profile.get("platform_services", {}).get("not_available", [])
        for svc in not_available:
            svc_name = svc.replace("_", " ")
            if svc_name.lower() in answer_lower:
                context = _get_context(answer, svc_name, 50)
                if not _is_negated(context, svc_name):
                    contradictions.append({
                        "entry_id": entry_id,
                        "family": family,
                        "type": "unavailable_service",
                        "claim": f"{svc} not available",
                        "found_term": svc_name,
                        "context": context[:120],
                        "severity": "medium",
                        "directory": directory,
                    })

    return contradictions


# ---------------------------------------------------------------------------
# CHECK 2: Coverage
# ---------------------------------------------------------------------------

def check_coverage(entries: list[dict], profiles: dict[str, dict],
                   active_only: bool = True) -> dict[str, dict]:
    """Check KB coverage per product family per capability.

    Returns {family: {total_entries, capabilities, coverage_pct, gaps}}.
    """
    coverage = {}

    for family, profile in profiles.items():
        if active_only:
            status = profile.get("_meta", {}).get("status", "draft")
            if status not in ("active",):
                continue

        family_entries = [e for e in entries if e.get("family_code") == family]
        caps = {}

        for cap_name, keywords in CAPABILITY_TOPICS.items():
            matching = []
            for entry in family_entries:
                text = (entry.get("question", "") + " " +
                        entry.get("answer", "")).lower()
                if any(kw in text for kw in keywords):
                    matching.append(entry.get("id", ""))
            caps[cap_name] = {
                "count": len(matching),
                "entry_ids": matching[:5],
                "status": "covered" if matching else "GAP",
            }

        covered = sum(1 for c in caps.values() if c["status"] == "covered")
        total = len(caps)
        gaps = sorted(name for name, c in caps.items() if c["status"] == "GAP")

        coverage[family] = {
            "total_entries": len(family_entries),
            "capabilities": caps,
            "coverage_pct": round(covered / total * 100, 1) if total else 0,
            "gaps": gaps,
        }

    return coverage


# ---------------------------------------------------------------------------
# CHECK 3: Quality
# ---------------------------------------------------------------------------

def score_entry(entry: dict) -> tuple[int, list[str]]:
    """Score a single entry on quality heuristics. Returns (score, issues)."""
    answer = entry.get("answer", "")
    question = entry.get("question", "")
    issues = []
    score = 10

    # Too short
    if len(answer) < 50:
        issues.append("answer too short (<50 chars)")
        score -= 3

    # Wall of text
    if len(answer) > 2000 and "\n" not in answer:
        issues.append("answer is wall of text (>2000 chars, no breaks)")
        score -= 2

    # Red flags
    answer_lower = answer.lower()
    for flag in RED_FLAGS:
        if flag in answer_lower:
            issues.append(f"contains red flag: '{flag}'")
            score -= 4

    # No BY branding
    if "blue yonder" not in answer_lower and "by " not in answer_lower:
        issues.append("no Blue Yonder branding in answer")
        score -= 1

    # Question too short
    if len(question) < 20:
        issues.append("question too short (<20 chars)")
        score -= 2

    # Deprecated terms
    for dep in DEPRECATED_TERMS:
        if dep in answer_lower:
            issues.append(f"contains deprecated term: '{dep}'")
            score -= 3

    # No question variants
    if not entry.get("question_variants"):
        issues.append("no question variants")
        score -= 1

    # No tags
    if not entry.get("tags"):
        issues.append("no tags")
        score -= 1

    # Invalid category
    if entry.get("category", "") not in VALID_CATEGORIES:
        issues.append(f"invalid category: '{entry.get('category')}'")
        score -= 2

    return max(0, score), issues


def check_quality(entries: list[dict], threshold: int = 7) -> list[dict]:
    """Score entries on quality heuristics. Returns low-quality entries."""
    low_quality = []
    for entry in entries:
        score, issues = score_entry(entry)
        if score < threshold:
            low_quality.append({
                "entry_id": entry.get("id", "UNKNOWN"),
                "family": entry.get("family_code", ""),
                "score": score,
                "issues": issues,
                "directory": _entry_dir(entry),
            })
    return low_quality


# ---------------------------------------------------------------------------
# CHECK 4: Consistency
# ---------------------------------------------------------------------------

def check_consistency(entries: list[dict],
                      profiles_dir: Path = PROFILES_DIR) -> list[dict]:
    """Check internal consistency across KB entries."""
    issues = []

    # Missing required fields
    for entry in entries:
        missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
        if missing:
            issues.append({
                "entry_id": entry.get("id", "UNKNOWN"),
                "type": "missing_fields",
                "fields": missing,
                "severity": "high",
            })

    # Orphan families (entry references family with no profile)
    profile_families = set()
    if profiles_dir.exists():
        for p in profiles_dir.glob("*.yaml"):
            if not p.name.startswith("."):
                profile_families.add(p.stem)

    for entry in entries:
        family = entry.get("family_code", "")
        if family and profile_families and family not in profile_families:
            issues.append({
                "entry_id": entry.get("id", "UNKNOWN"),
                "type": "orphan_family",
                "family": family,
                "severity": "low",
            })

    return issues


# ---------------------------------------------------------------------------
# Health score
# ---------------------------------------------------------------------------

def calculate_health_score(contradictions: list, coverage: dict,
                           quality: list, consistency: list) -> float:
    """Calculate overall health score 0-100."""
    score = 100.0

    score -= len(contradictions) * 10

    total_gaps = sum(len(c["gaps"]) for c in coverage.values())
    score -= total_gaps * 2

    score -= len(quality) * 0.5

    high_severity = [i for i in consistency if i.get("severity") == "high"]
    score -= len(high_severity) * 5

    return max(0.0, min(100.0, round(score, 1)))


# ---------------------------------------------------------------------------
# Report output
# ---------------------------------------------------------------------------

def build_report(entries: list[dict], profiles: dict[str, dict],
                 checks: list[str] | None = None,
                 profiles_dir: Path = PROFILES_DIR) -> dict:
    """Build full health report."""
    all_checks = {"contradictions", "coverage", "quality", "consistency"}
    run_checks = set(checks) if checks else all_checks

    report = {
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "entry_counts": {
            "verified": sum(1 for e in entries if _entry_dir(e) == "verified"),
            "drafts": sum(1 for e in entries if _entry_dir(e) == "drafts"),
            "total": len(entries),
        },
        "profile_counts": {
            "total": len(profiles),
            "active": sum(1 for p in profiles.values()
                          if p.get("_meta", {}).get("status") == "active"),
        },
    }

    contradictions = []
    coverage = {}
    quality = []
    consistency = []

    if "contradictions" in run_checks:
        contradictions = check_contradictions(entries, profiles)
        report["contradictions"] = contradictions
        report["contradiction_count"] = len(contradictions)

    if "coverage" in run_checks:
        coverage = check_coverage(entries, profiles, active_only=True)
        report["coverage"] = coverage

    if "quality" in run_checks:
        quality = check_quality(entries)
        report["low_quality"] = quality
        report["low_quality_count"] = len(quality)

    if "consistency" in run_checks:
        consistency = check_consistency(entries, profiles_dir=profiles_dir)
        report["consistency_issues"] = consistency
        report["consistency_count"] = len(consistency)

    report["score"] = calculate_health_score(
        contradictions, coverage, quality, consistency,
    )

    return report


def print_report(report: dict) -> None:
    """Print formatted health report to console."""
    border = "=" * 66
    print(f"\n{border}")
    print(f"  KB Health Report -- {report['timestamp'][:10]}")
    print(f"{border}")

    ec = report.get("entry_counts", {})
    pc = report.get("profile_counts", {})
    print(f"\n  ENTRIES:  {ec.get('verified', 0)} verified | "
          f"{ec.get('drafts', 0)} drafts")
    print(f"  PROFILES: {pc.get('total', 0)} total | "
          f"{pc.get('active', 0)} active")

    # Contradictions
    if "contradictions" in report:
        cc = report.get("contradiction_count", 0)
        print(f"\n  CONTRADICTIONS: {cc} found")
        if cc:
            for c in report["contradictions"][:10]:
                print(f"    [{c['severity'].upper()}] {c['entry_id']} ({c['family']}): "
                      f"'{c['found_term']}' vs {c['claim']}")
        else:
            print("    No KB entries violate product profile constraints.")

    # Coverage
    if "coverage" in report:
        print(f"\n  COVERAGE (active profiles):")
        for family, cov in report["coverage"].items():
            pct = cov.get("coverage_pct", 0)
            gaps = cov.get("gaps", [])
            total = cov.get("total_entries", 0)
            print(f"    {family}: {total} entries, {pct}% coverage")
            if gaps:
                print(f"      Gaps: {', '.join(gaps)}")
        if not report["coverage"]:
            print("    No active profiles to check coverage against.")

    # Quality
    if "low_quality" in report:
        lq = report.get("low_quality_count", 0)
        print(f"\n  QUALITY: {lq} low-quality entries (score < 7/10)")
        if lq:
            # Aggregate top issues
            all_issues = []
            for q in report["low_quality"]:
                all_issues.extend(q.get("issues", []))
            issue_counts = Counter(all_issues).most_common(5)
            for issue, count in issue_counts:
                print(f"    {count:>3}x {issue}")

    # Consistency
    if "consistency_issues" in report:
        ci = report.get("consistency_count", 0)
        print(f"\n  CONSISTENCY: {ci} issues")
        if ci:
            types = Counter(i["type"] for i in report["consistency_issues"])
            for t, count in types.most_common():
                print(f"    {count:>3}x {t}")

    score = report.get("score", 0)
    print(f"\n  HEALTH SCORE: {score}/100")
    print(f"{border}\n")


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def save_health_snapshot(report: dict, health_dir: Path = HEALTH_DIR) -> Path:
    """Save health report to data/kb/health_history/."""
    health_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = health_dir / f"{today}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return path


def compare_reports(current: dict, previous: dict) -> None:
    """Print comparison between current and previous health reports."""
    cur_score = current.get("score", 0)
    prev_score = previous.get("score", 0)
    delta = cur_score - prev_score
    arrow = "+" if delta > 0 else ""

    print(f"\n  Comparison vs {previous.get('timestamp', 'previous')[:10]}:")
    print(f"    Health score: {cur_score} (was {prev_score}: {arrow}{delta})")

    cur_cc = current.get("contradiction_count", 0)
    prev_cc = previous.get("contradiction_count", 0)
    print(f"    Contradictions: {cur_cc} (was {prev_cc})")

    cur_lq = current.get("low_quality_count", 0)
    prev_lq = previous.get("low_quality_count", 0)
    print(f"    Low quality: {cur_lq} (was {prev_lq})")

    cur_entries = current.get("entry_counts", {}).get("total", 0)
    prev_entries = previous.get("entry_counts", {}).get("total", 0)
    print(f"    Total entries: {cur_entries} (was {prev_entries})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="KB Health Evaluation -- deterministic self-checks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--check", type=str, default=None,
                        choices=["contradictions", "coverage", "quality", "consistency"],
                        help="Run only one check")
    parser.add_argument("--family", type=str, default=None,
                        help="Filter to one family")
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")
    parser.add_argument("--compare", type=str, default=None, metavar="FILE",
                        help="Compare to previous health snapshot")
    parser.add_argument("--save", action="store_true", default=True,
                        help="Save snapshot to health_history/ (default)")
    parser.add_argument("--no-save", action="store_true",
                        help="Do not save snapshot")
    args = parser.parse_args()

    entries = load_all_entries(family_filter=args.family)
    profiles = load_all_profiles()

    checks = [args.check] if args.check else None
    report = build_report(entries, profiles, checks=checks)

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print_report(report)

    if args.compare:
        prev_path = Path(args.compare)
        if prev_path.exists():
            with open(prev_path, "r", encoding="utf-8") as f:
                previous = json.load(f)
            compare_reports(report, previous)
        else:
            print(f"[WARN] Previous report not found: {args.compare}")

    if not args.no_save:
        path = save_health_snapshot(report)
        if not args.json:
            print(f"  Snapshot saved: {path.relative_to(PROJECT_ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
