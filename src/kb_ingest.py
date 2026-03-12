"""Knowledge Ingestion Pipeline — CKE facts to KB draft entries.

Takes CKE-extracted facts, generates RFP-quality Q&A entries validated
against product profiles, writes to data/kb/drafts/ for review.

Sources:
  - architecture: CKE Service Description + Architecture JSON files
  - projects: project _knowledge/facts.yaml files (if available)
  - all: both sources

Usage:
  python src/kb_ingest.py --family wms --source architecture --svc svc.json --arch arch.json
  python src/kb_ingest.py --family wms --dry-run
  python src/kb_ingest.py --family wms --batch
  python src/kb_ingest.py --family wms --min-confidence 0.9
  python src/kb_ingest.py --family wms --fact "WMS supports REST API"
"""

import argparse
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
PROFILES_DIR = PROJECT_ROOT / "config" / "product_profiles" / "_effective"

# Confidence mapping for CKE confidence strings
CONFIDENCE_MAP = {"high": 0.95, "medium": 0.75, "low": 0.50}

# Categories to skip when collecting facts (these are negative constraints)
SKIP_CATEGORIES = frozenset({"not_supported"})


# ---------------------------------------------------------------------------
# Generation prompt
# ---------------------------------------------------------------------------

GENERATION_PROMPT = """You are a Blue Yonder pre-sales engineer writing RFP answers.

PRODUCT: {product_display_name}
PRODUCT PROFILE SUMMARY:
- Cloud-native: {cloud_native}
- Deployment: {deployment}
- APIs: {apis}
- Platform services available: {available_services}

FORBIDDEN — NEVER claim these for this product:
{forbidden_claims_list}

SOURCE FACTS (ground truth — your answer must be based ONLY on these):
{facts_list}

TASK: Generate an RFP Q&A entry based on the source facts above.

OUTPUT FORMAT (JSON):
{{
  "question": "A realistic RFP question that these facts would answer",
  "answer": "Professional BY pre-sales answer (2-4 sentences, confident, specific, no 'see attached')",
  "question_variants": ["2-3 alternative phrasings of the question"],
  "category": "technical|functional|consulting|customer_executive",
  "tags": ["3-5 relevant tags"]
}}

RULES:
1. Answer must be GROUNDED in the source facts — do not add information not in the facts
2. Answer must NOT violate any forbidden claims
3. Use professional BY pre-sales tone (confident, specific, no hedging)
4. Do not reference attachments, meetings, or client-specific context
5. Answer must be self-contained and reusable across any client"""


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------

def load_effective_profile(family: str) -> Optional[dict]:
    """Load effective product profile for a family."""
    import yaml
    path = PROFILES_DIR / f"{family}.yaml"
    if not path.exists():
        return None
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Stage 1: Collect facts
# ---------------------------------------------------------------------------

# Map effective profile family codes -> PRODUCT_NAME_MAP keys in
# generate_product_profiles.py.  When a profile family code doesn't exist
# directly in PRODUCT_NAME_MAP, this tells us which canonical key(s) to
# look up instead.  Multiple keys means "collect facts from ALL of them"
# (e.g. "planning" aggregates demand_planning + supply_planning).
FAMILY_TO_CKE_KEYS: dict[str, list[str]] = {
    # Planning umbrella -> both demand and supply
    "planning":        ["demand_planning", "supply_planning"],
    "planning_ibp":    ["ibp"],
    "planning_pps":    ["supply_planning"],
    # Retail families
    "retail_mfp":      ["merchandise_planning"],
    "retail_ar":       ["allocation_replenishment"],
    "retail_demand_edge": ["forecasting_retail"],
    # Category management
    "catman":          ["category_management"],
    "catman_assortment": ["assortment"],
    "catman_space":    ["category_management"],
    # SCP / sequencing
    "scp":             ["flexis", "order_sequencing"],
    "scp_sequencing":  ["order_sequencing"],
    # Logistics
    "logistics":       ["tms"],
    # Commerce / OMS
    "commerce":        ["oms"],
    "commerce_orders": ["oms"],
}


def _resolve_cke_keys_for_family(family: str) -> list[str]:
    """Return list of PRODUCT_NAME_MAP keys to look up for a family.

    Uses FAMILY_TO_CKE_KEYS if the family isn't directly in PRODUCT_NAME_MAP.
    """
    from generate_product_profiles import PRODUCT_NAME_MAP

    # Direct match — family IS a PRODUCT_NAME_MAP key
    if family in PRODUCT_NAME_MAP:
        return [family]

    # Alias mapping
    if family in FAMILY_TO_CKE_KEYS:
        return FAMILY_TO_CKE_KEYS[family]

    return []


def load_architecture_facts(family: str,
                            svc_path: Optional[Path] = None,
                            arch_path: Optional[Path] = None) -> list[dict]:
    """Load architectural facts for a product from CKE extraction files."""
    from generate_product_profiles import PRODUCT_NAME_MAP, _resolve_cke_key

    cke_keys = _resolve_cke_keys_for_family(family)
    if not cke_keys:
        return []

    facts = []
    sources = []
    if svc_path and svc_path.exists():
        sources.append(svc_path)
    if arch_path and arch_path.exists():
        sources.append(arch_path)

    # Auto-discover CKE files if not provided
    if not sources:
        for p in PROJECT_ROOT.rglob("*.json"):
            name_lower = p.name.lower()
            if ("service" in name_lower and "description" in name_lower) or \
               ("architecture" in name_lower and "platform" in name_lower):
                sources.append(p)

    fact_idx = 0
    for path in sources:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        products = data.get("products", data)
        if not isinstance(products, dict):
            continue

        # Resolve each CKE key against this file's products
        for canonical_key in cke_keys:
            cke_key = _resolve_cke_key(products, canonical_key)
            if not cke_key:
                continue

            prod_data = products[cke_key]
            source_name = path.name

            for category, items in prod_data.items():
                if category in SKIP_CATEGORIES:
                    continue
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict):
                        fact_text = item.get("fact", "")
                        conf_str = item.get("confidence", "high")
                    else:
                        fact_text = str(item)
                        conf_str = "medium"

                    if not fact_text.strip():
                        continue

                    confidence = CONFIDENCE_MAP.get(conf_str, 0.75)
                    facts.append({
                        "fact": fact_text.strip(),
                        "source": source_name,
                        "category": category,
                        "products": [cke_key],
                        "confidence": confidence,
                        "fact_id": f"arch_{family}_{category}_{fact_idx:03d}",
                    })
                    fact_idx += 1

    return facts


def scan_project_facts(family: str, min_confidence: float = 0.8) -> list[dict]:
    """Scan project _knowledge/facts.yaml files for this family."""
    import yaml

    facts = []
    # Look for facts.yaml in project directories
    for facts_file in PROJECT_ROOT.rglob("facts.yaml"):
        try:
            with open(facts_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (yaml.YAMLError, OSError):
            continue

        if not isinstance(data, list):
            continue

        for item in data:
            if not isinstance(item, dict):
                continue

            products = item.get("products", [])
            # Check if any product alias matches this family
            from generate_product_profiles import PRODUCT_NAME_MAP
            family_aliases = [a.lower() for a in PRODUCT_NAME_MAP.get(family, [])]
            matched = any(p.lower() in family_aliases for p in products)
            if not matched:
                continue

            conf = item.get("confidence", 0.8)
            if isinstance(conf, str):
                conf = CONFIDENCE_MAP.get(conf, 0.75)
            if conf < min_confidence:
                continue

            facts.append({
                "fact": item.get("fact", ""),
                "source": str(facts_file.relative_to(PROJECT_ROOT)),
                "category": item.get("topics", ["general"])[0].lower() if item.get("topics") else "general",
                "products": products,
                "confidence": conf,
                "fact_id": f"proj_{family}_{len(facts):03d}",
            })

    return facts


def deduplicate_facts(facts: list[dict], threshold: float = 0.90) -> list[dict]:
    """Remove duplicate facts by text similarity.

    Uses simple normalized text comparison for efficiency.
    Falls back to embedding similarity if sentence-transformers available.
    """
    if not facts:
        return facts

    seen: set[str] = set()
    unique = []
    for f in facts:
        normalized = f["fact"].lower().strip()
        # Simple exact/near-exact dedup
        if normalized in seen:
            continue
        # Check substring containment (one fact fully contained in another)
        is_dup = False
        for s in seen:
            if normalized in s or s in normalized:
                if abs(len(normalized) - len(s)) < 20:
                    is_dup = True
                    break
        if not is_dup:
            seen.add(normalized)
            unique.append(f)

    return unique


def load_existing_questions(family: str) -> list[str]:
    """Load questions from existing verified and draft entries for this family."""
    questions = []
    for base_dir in [VERIFIED_DIR, DRAFTS_DIR]:
        family_dir = base_dir / family
        if not family_dir.exists():
            continue
        for json_file in family_dir.glob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    entry = json.load(f)
                q = entry.get("question", "")
                if q:
                    questions.append(q)
            except (json.JSONDecodeError, OSError):
                continue
    return questions


def filter_already_covered(facts: list[dict], existing_questions: list[str],
                           threshold: float = 0.85) -> list[dict]:
    """Filter out facts already covered by existing KB entries.

    Uses simple keyword overlap when embeddings unavailable.
    """
    if not existing_questions or not facts:
        return facts

    # Try embedding-based filtering
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np

        model = SentenceTransformer("BAAI/bge-large-en-v1.5")
        fact_texts = [f["fact"] for f in facts]

        fact_embs = model.encode(fact_texts, normalize_embeddings=True,
                                 show_progress_bar=False)
        q_embs = model.encode(existing_questions, normalize_embeddings=True,
                              show_progress_bar=False)

        # Cosine similarity (normalized = dot product)
        sim_matrix = np.dot(fact_embs, q_embs.T)
        max_sims = sim_matrix.max(axis=1)

        filtered = []
        for i, f in enumerate(facts):
            if max_sims[i] < threshold:
                filtered.append(f)
        return filtered

    except ImportError:
        # Fallback: simple keyword overlap
        existing_lower = " ".join(existing_questions).lower()
        filtered = []
        for f in facts:
            words = set(re.findall(r'\b\w{4,}\b', f["fact"].lower()))
            overlap = sum(1 for w in words if w in existing_lower)
            ratio = overlap / len(words) if words else 0
            if ratio < 0.7:  # Less than 70% keyword overlap
                filtered.append(f)
        return filtered


def collect_facts(family: str, source: str = "architecture",
                  min_confidence: float = 0.8,
                  svc_path: Optional[Path] = None,
                  arch_path: Optional[Path] = None) -> list[dict]:
    """Collect relevant CKE facts for a product family."""
    facts = []

    if source in ("architecture", "all"):
        arch_facts = load_architecture_facts(family, svc_path, arch_path)
        facts.extend(arch_facts)

    if source in ("projects", "all"):
        project_facts = scan_project_facts(family, min_confidence)
        facts.extend(project_facts)

    # Filter by confidence
    facts = [f for f in facts if f.get("confidence", 0) >= min_confidence]

    # Deduplicate
    facts = deduplicate_facts(facts, threshold=0.90)

    # Filter out facts already covered by existing KB
    existing_questions = load_existing_questions(family)
    if existing_questions:
        before = len(facts)
        facts = filter_already_covered(facts, existing_questions, threshold=0.85)
        filtered = before - len(facts)
        if filtered:
            print(f"  Filtered {filtered} facts already covered in KB")

    return facts


# ---------------------------------------------------------------------------
# Stage 2: Cluster facts and generate Q&A pairs
# ---------------------------------------------------------------------------

def cluster_facts(facts: list[dict], threshold: float = 0.75) -> list[list[dict]]:
    """Group related facts into clusters by category and keyword overlap.

    Each cluster becomes one Q&A entry.
    """
    if not facts:
        return []

    # Group by CKE category first
    by_category: dict[str, list[dict]] = {}
    for f in facts:
        cat = f.get("category", "general")
        by_category.setdefault(cat, []).append(f)

    clusters = []
    for cat, cat_facts in by_category.items():
        if len(cat_facts) <= 3:
            # Small category = one cluster
            clusters.append(cat_facts)
        else:
            # Split large categories by keyword overlap
            subclusters = _subcluster_by_keywords(cat_facts, max_per_cluster=5)
            clusters.extend(subclusters)

    return clusters


def _subcluster_by_keywords(facts: list[dict], max_per_cluster: int = 5) -> list[list[dict]]:
    """Split facts into subclusters based on keyword overlap."""
    if len(facts) <= max_per_cluster:
        return [facts]

    subclusters = []
    remaining = list(facts)

    while remaining:
        seed = remaining.pop(0)
        cluster = [seed]
        seed_words = set(re.findall(r'\b\w{4,}\b', seed["fact"].lower()))

        still_remaining = []
        for f in remaining:
            if len(cluster) >= max_per_cluster:
                still_remaining.append(f)
                continue
            f_words = set(re.findall(r'\b\w{4,}\b', f["fact"].lower()))
            overlap = len(seed_words & f_words)
            if overlap >= 2:
                cluster.append(f)
            else:
                still_remaining.append(f)

        subclusters.append(cluster)
        remaining = still_remaining

    return subclusters


def build_prompt(cluster: list[dict], profile: dict) -> str:
    """Build generation prompt for a fact cluster."""
    forbidden = profile.get("forbidden_claims", [])
    ps = profile.get("platform_services", {})

    return GENERATION_PROMPT.format(
        product_display_name=profile.get("display_name", profile.get("product", "unknown")),
        cloud_native=profile.get("cloud_native", "unknown"),
        deployment=profile.get("deployment", []),
        apis=profile.get("apis", []),
        available_services=", ".join(ps.get("available", [])[:15]),
        forbidden_claims_list="\n".join(f"- {fc}" for fc in forbidden[:20]) if forbidden else "(none)",
        facts_list="\n".join(
            f"- {f['fact']} (source: {f.get('source', 'unknown')})"
            for f in cluster
        ),
    )


def generate_qa_prompts(clusters: list[list[dict]], profile: dict) -> list[dict]:
    """Build LLM prompts for each cluster."""
    prompts = []
    for cluster in clusters:
        prompt = build_prompt(cluster, profile)
        prompts.append({
            "prompt": prompt,
            "source_facts": cluster,
            "family": profile.get("product", "unknown"),
        })
    return prompts


# ---------------------------------------------------------------------------
# Stage 2b: LLM generation (sync and batch)
# ---------------------------------------------------------------------------

def _parse_qa_response(raw_text: str) -> Optional[dict]:
    """Parse LLM JSON response into Q&A dict."""
    if not raw_text or not raw_text.strip():
        return None

    # Strip markdown fences
    text = re.sub(r'^```(?:json)?\s*', '', raw_text.strip(), flags=re.MULTILINE)
    text = re.sub(r'```\s*$', '', text.strip(), flags=re.MULTILINE).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try extracting JSON object
        m = re.search(r'(\{[\s\S]*\})', text)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    return None


def generate_sync(prompts: list[dict], model: str = "gemini-flash") -> list[dict]:
    """Generate Q&A entries synchronously (one LLM call per cluster)."""
    from kb_extract_historical import call_llm

    entries = []
    for i, qp in enumerate(prompts):
        print(f"  Generating {i+1}/{len(prompts)}...", end="\r")
        try:
            raw = call_llm(qp["prompt"], model=model)
            parsed = _parse_qa_response(raw)
            if parsed:
                parsed["source_facts"] = [
                    {"fact_id": f.get("fact_id", ""), "text": f["fact"],
                     "source": f.get("source", "")}
                    for f in qp["source_facts"]
                ]
                parsed["family_code"] = qp["family"]
                entries.append(parsed)
        except Exception as e:
            print(f"  [WARN] Generation failed for cluster {i+1}: {e}")

    print()
    return entries


def generate_batch(prompts: list[dict], model: str = "gemini-3-flash-preview") -> list[dict]:
    """Generate Q&A entries using Batch API (50% cheaper)."""
    from batch_llm import BatchProcessor, parse_json_from_batch

    bp = BatchProcessor(model=model)
    for i, qp in enumerate(prompts):
        bp.add(key=f"qa_{i}", prompt=qp["prompt"])

    result = bp.run(display_name="kb-ingest", verbose=True)

    entries = []
    for i, qp in enumerate(prompts):
        key = f"qa_{i}"
        raw = result.results.get(key, "")
        if not raw:
            continue
        try:
            parsed = parse_json_from_batch(raw)
            if isinstance(parsed, dict):
                parsed["source_facts"] = [
                    {"fact_id": f.get("fact_id", ""), "text": f["fact"],
                     "source": f.get("source", "")}
                    for f in qp["source_facts"]
                ]
                parsed["family_code"] = qp["family"]
                entries.append(parsed)
        except (ValueError, json.JSONDecodeError):
            continue

    return entries


# ---------------------------------------------------------------------------
# Stage 3: Validate against product profile
# ---------------------------------------------------------------------------

def validate_entry(entry: dict, profile: dict) -> dict:
    """Validate generated entry against product profile.

    Adds _validation field: PASSED, WARNING, or REJECTED.
    """
    from rfp_feedback import check_forbidden_claims

    answer = entry.get("answer", "")

    # Check forbidden claims
    violations = check_forbidden_claims(answer, profile)
    if violations:
        entry["_validation"] = "REJECTED"
        entry["_violations"] = violations
        return entry

    # Check unavailable services mentioned positively
    ps = profile.get("platform_services", {})
    not_available = ps.get("not_available", [])
    warnings = []
    for svc_key in not_available:
        svc_name = svc_key.replace("_", " ")
        if svc_name.lower() in answer.lower():
            warnings.append(
                f"Mentions '{svc_name}' which is not available for this product"
            )

    if warnings:
        entry["_validation"] = "WARNING"
        entry["_warnings"] = warnings
    else:
        entry["_validation"] = "PASSED"

    entry["profile_validated"] = True
    entry["forbidden_claims_checked"] = True
    return entry


# ---------------------------------------------------------------------------
# Stage 4: Deduplicate against existing KB
# ---------------------------------------------------------------------------

def deduplicate_against_kb(new_entries: list[dict], family: str) -> list[dict]:
    """Remove entries that duplicate existing KB entries.

    Uses embedding similarity — if new question is > 0.85 similar
    to existing verified/draft entry, skip it.
    """
    existing_questions = load_existing_questions(family)
    if not existing_questions:
        return new_entries

    new_texts = [e.get("question", "") for e in new_entries]
    if not new_texts:
        return new_entries

    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np

        model = SentenceTransformer("BAAI/bge-large-en-v1.5")
        new_embs = model.encode(new_texts, normalize_embeddings=True,
                                show_progress_bar=False)
        existing_embs = model.encode(existing_questions, normalize_embeddings=True,
                                     show_progress_bar=False)

        sim_matrix = np.dot(new_embs, existing_embs.T)
        max_sims = sim_matrix.max(axis=1)

        kept = []
        for i, entry in enumerate(new_entries):
            if max_sims[i] >= 0.85:
                print(f"  [SKIP] '{entry['question'][:60]}' -- "
                      f"similar to existing ({max_sims[i]:.2f})")
            else:
                kept.append(entry)
        return kept

    except ImportError:
        # Fallback: simple text comparison
        existing_lower = set(q.lower().strip() for q in existing_questions)
        kept = []
        for entry in new_entries:
            q_lower = entry.get("question", "").lower().strip()
            if q_lower not in existing_lower:
                kept.append(entry)
        return kept


# ---------------------------------------------------------------------------
# Stage 5: Write drafts
# ---------------------------------------------------------------------------

def _next_draft_id(family_dir: Path) -> int:
    """Get next draft ID number."""
    existing = list(family_dir.glob("KB_DRAFT_*.json"))
    if not existing:
        return 1
    max_id = 0
    for f in existing:
        m = re.search(r'KB_DRAFT_(\d+)', f.stem)
        if m:
            max_id = max(max_id, int(m.group(1)))
    return max_id + 1


def write_drafts(entries: list[dict], family: str, model: str = "gemini-flash",
                 drafts_dir: Path = DRAFTS_DIR) -> int:
    """Write validated entries to data/kb/drafts/{family}/."""
    family_dir = drafts_dir / family
    family_dir.mkdir(parents=True, exist_ok=True)

    next_id = _next_draft_id(family_dir)
    now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")

    written = 0
    for entry in entries:
        if entry.get("_validation") == "REJECTED":
            continue

        entry_id = f"KB_DRAFT_{next_id:04d}"

        draft = {
            "id": entry_id,
            "question": entry.get("question", ""),
            "answer": entry.get("answer", ""),
            "question_variants": entry.get("question_variants", []),
            "solution_codes": [],
            "family_code": family,
            "category": entry.get("category", "technical"),
            "subcategory": "",
            "tags": entry.get("tags", []),
            "confidence": "draft",
            "source_rfps": [],
            "last_updated": today,
            "cloud_native_only": False,
            "notes": "",
            "source_facts": entry.get("source_facts", []),
            "generated_by": "kb_ingest.py",
            "generated_at": now_iso,
            "model": model,
            "profile_validated": entry.get("profile_validated", False),
            "forbidden_claims_checked": entry.get("forbidden_claims_checked", False),
            "feedback_history": [],
            "provenance": {
                "pipeline": "fact_to_kb_v1",
                "input_facts": len(entry.get("source_facts", [])),
                "profile_used": family,
            },
        }

        # Remove internal validation fields
        for key in ("_validation", "_violations", "_warnings"):
            draft.pop(key, None)

        path = family_dir / f"{entry_id}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(draft, f, indent=2, ensure_ascii=False)

        next_id += 1
        written += 1

    return written


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def ingest(family: str, source: str = "architecture",
           min_confidence: float = 0.8,
           batch_mode: bool = False, dry_run: bool = False,
           model: str = "gemini-flash",
           svc_path: Optional[Path] = None,
           arch_path: Optional[Path] = None,
           single_fact: Optional[str] = None,
           drafts_dir: Path = DRAFTS_DIR) -> dict:
    """Full ingestion pipeline.

    Returns summary dict.
    """
    summary = {
        "family": family,
        "facts_collected": 0,
        "clusters": 0,
        "entries_generated": 0,
        "profile_rejected": 0,
        "duplicates_filtered": 0,
        "drafts_written": 0,
    }

    # 0. Load product profile
    profile = load_effective_profile(family)
    if not profile:
        print(f"[ERROR] No effective profile for '{family}' in {PROFILES_DIR}")
        return summary

    status = profile.get("_meta", {}).get("status", "unknown")
    if status not in ("active", "draft", "unknown"):
        print(f"[WARN] Profile '{family}' status: {status}")

    # 1. Collect facts
    print(f"[Stage 1] Collecting facts for {family}...")
    if single_fact:
        facts = [{
            "fact": single_fact,
            "source": "cli",
            "category": "general",
            "products": [],
            "confidence": 1.0,
            "fact_id": "cli_000",
        }]
    else:
        facts = collect_facts(family, source, min_confidence, svc_path, arch_path)

    summary["facts_collected"] = len(facts)
    print(f"  Found {len(facts)} facts")

    if not facts:
        print("[OK] No new facts to process")
        return summary

    # 2. Cluster and build prompts
    print(f"[Stage 2] Clustering facts...")
    clusters = cluster_facts(facts, threshold=0.75)
    summary["clusters"] = len(clusters)
    print(f"  Formed {len(clusters)} topic clusters")

    prompts = generate_qa_prompts(clusters, profile)

    if dry_run:
        print(f"\n[DRY RUN] Would generate {len(prompts)} Q&A entries")
        for i, qp in enumerate(prompts[:5]):
            print(f"\n  Cluster {i+1} ({len(qp['source_facts'])} facts):")
            for sf in qp["source_facts"][:3]:
                print(f"    - {sf['fact'][:80]}")
        if len(prompts) > 5:
            print(f"\n  ... and {len(prompts) - 5} more clusters")
        return summary

    # 2b. Call LLM
    print(f"[Stage 2b] Generating Q&A pairs ({len(prompts)} clusters)...")
    if batch_mode:
        entries = generate_batch(prompts, model=model)
    else:
        entries = generate_sync(prompts, model=model)

    summary["entries_generated"] = len(entries)
    print(f"  Generated {len(entries)} entries")

    if not entries:
        print("[WARN] No entries generated")
        return summary

    # 3. Validate against profile
    print(f"[Stage 3] Validating against {family} profile...")
    validated = []
    rejected = 0
    for entry in entries:
        entry = validate_entry(entry, profile)
        if entry.get("_validation") == "REJECTED":
            rejected += 1
            violations = entry.get("_violations", [])
            print(f"  [REJECTED] {entry.get('question', '')[:60]} -- "
                  f"{violations[0] if violations else 'unknown'}")
        else:
            validated.append(entry)

    summary["profile_rejected"] = rejected
    print(f"  Passed: {len(validated)}, Rejected: {rejected}")

    if not validated:
        print("[WARN] All entries rejected by profile validation")
        return summary

    # 4. Deduplicate against existing KB
    print(f"[Stage 4] Deduplicating against existing KB...")
    unique = deduplicate_against_kb(validated, family)
    summary["duplicates_filtered"] = len(validated) - len(unique)
    print(f"  Unique: {len(unique)} "
          f"(filtered {len(validated) - len(unique)} duplicates)")

    if not unique:
        print("[OK] All entries already covered in KB")
        return summary

    # 5. Write drafts
    print(f"[Stage 5] Writing drafts to {DRAFTS_DIR / family}/...")
    written = write_drafts(unique, family, model=model, drafts_dir=drafts_dir)
    summary["drafts_written"] = written

    # Report
    print(f"\n{'='*60}")
    print(f"  Ingestion Complete: {family}")
    print(f"{'='*60}")
    print(f"  Facts collected:     {summary['facts_collected']}")
    print(f"  Clusters formed:     {summary['clusters']}")
    print(f"  Entries generated:   {summary['entries_generated']}")
    print(f"  Profile violations:  {summary['profile_rejected']}")
    print(f"  Duplicates filtered: {summary['duplicates_filtered']}")
    print(f"  Drafts written:      {summary['drafts_written']}")
    print(f"{'='*60}")
    print(f"  Review: python src/rfp_feedback.py search \"\" --family {family}")
    print(f"  Approve: python src/rfp_feedback.py approve KB_DRAFT_XXXX")

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def ingest_all(source: str = "architecture",
               min_confidence: float = 0.8,
               batch_mode: bool = False, dry_run: bool = False,
               model: str = "gemini-flash",
               svc_path: Optional[Path] = None,
               arch_path: Optional[Path] = None) -> list[dict]:
    """Run ingestion pipeline for ALL families with effective profiles.

    Returns list of per-family summary dicts.
    """
    import yaml

    if not PROFILES_DIR.exists():
        print(f"[ERROR] No profiles directory: {PROFILES_DIR}")
        return []

    families = sorted(
        p.stem for p in PROFILES_DIR.glob("*.yaml")
        if not p.name.startswith(".")
    )

    if not families:
        print("[ERROR] No effective profiles found")
        return []

    print(f"[BULK] Found {len(families)} families with effective profiles")
    print(f"{'='*66}")

    summaries = []
    for family in families:
        print(f"\n--- {family} ---")
        summary = ingest(
            family=family,
            source=source,
            min_confidence=min_confidence,
            batch_mode=batch_mode,
            dry_run=dry_run,
            model=model,
            svc_path=svc_path,
            arch_path=arch_path,
        )
        summaries.append(summary)

    # Bulk summary
    print(f"\n{'='*66}")
    print(f"  Bulk Ingestion Summary")
    print(f"{'='*66}")
    print(f"  {'Family':<25} {'Facts':>6} {'Gen':>6} {'Valid':>6} {'Drafts':>6}")
    print(f"  {'-'*25} {'-'*6} {'-'*6} {'-'*6} {'-'*6}")
    total_facts = total_gen = total_valid = total_drafts = 0
    for s in summaries:
        facts = s["facts_collected"]
        gen = s["entries_generated"]
        valid = gen - s["profile_rejected"]
        drafts = s["drafts_written"]
        total_facts += facts
        total_gen += gen
        total_valid += valid
        total_drafts += drafts
        if facts > 0 or drafts > 0:
            print(f"  {s['family']:<25} {facts:>6} {gen:>6} {valid:>6} {drafts:>6}")
    skipped = sum(1 for s in summaries if s["facts_collected"] == 0)
    print(f"  {'-'*25} {'-'*6} {'-'*6} {'-'*6} {'-'*6}")
    print(f"  {'TOTAL':<25} {total_facts:>6} {total_gen:>6} {total_valid:>6} {total_drafts:>6}")
    print(f"  Skipped (0 facts): {skipped}")
    print(f"{'='*66}")

    return summaries


def main():
    parser = argparse.ArgumentParser(
        description="KB Ingestion Pipeline -- CKE facts to draft KB entries",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--family",
                       help="Product family (e.g., wms, planning, logistics)")
    group.add_argument("--all", action="store_true", dest="all_families",
                       help="Run ingestion for ALL families with effective profiles")
    parser.add_argument("--source", default="architecture",
                        choices=["architecture", "projects", "all"],
                        help="Fact source (default: architecture)")
    parser.add_argument("--svc", type=str, default=None,
                        help="Path to CKE Service Description JSON")
    parser.add_argument("--arch", type=str, default=None,
                        help="Path to CKE Architecture JSON")
    parser.add_argument("--model", default="gemini-flash",
                        help="LLM model for generation (default: gemini-flash)")
    parser.add_argument("--batch", action="store_true",
                        help="Use Batch API (50%% cheaper, async)")
    parser.add_argument("--min-confidence", type=float, default=0.8,
                        help="Minimum fact confidence (default: 0.8)")
    parser.add_argument("--fact", type=str, default=None,
                        help="Single fact to test (skips collection)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be generated without LLM calls")
    args = parser.parse_args()

    svc_path = Path(args.svc) if args.svc else None
    arch_path = Path(args.arch) if args.arch else None

    if args.all_families:
        ingest_all(
            source=args.source,
            min_confidence=args.min_confidence,
            batch_mode=args.batch,
            dry_run=args.dry_run,
            model=args.model,
            svc_path=svc_path,
            arch_path=arch_path,
        )
    else:
        ingest(
            family=args.family,
            source=args.source,
            min_confidence=args.min_confidence,
            batch_mode=args.batch,
            dry_run=args.dry_run,
            model=args.model,
            svc_path=svc_path,
            arch_path=arch_path,
            single_fact=args.fact,
        )


if __name__ == "__main__":
    main()
