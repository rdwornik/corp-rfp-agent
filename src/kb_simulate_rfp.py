"""Simulated RFP Test Suite — generates questions, answers via RAG, scores.

System generates realistic RFP questions from product profiles,
answers them via ChromaDB RAG retrieval, and scores the results.

Usage:
  python src/kb_simulate_rfp.py --family wms --count 50
  python src/kb_simulate_rfp.py --all --count 30
  python src/kb_simulate_rfp.py --family wms --count 10
  python src/kb_simulate_rfp.py --family wms --batch
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
KB_DIR = PROJECT_ROOT / "data" / "kb"
VERIFIED_DIR = KB_DIR / "verified"
PROFILES_DIR = PROJECT_ROOT / "config" / "product_profiles" / "_effective"
SIMULATIONS_DIR = KB_DIR / "simulations"

# Topics to generate questions for (aligned with kb_eval CAPABILITY_TOPICS)
QUESTION_TOPICS = [
    "deployment", "integration", "security", "data_management",
    "scalability", "architecture", "ui", "compliance",
    "disaster_recovery", "monitoring",
]

QUESTION_GEN_PROMPT = """You are generating realistic RFP questions for a Blue Yonder product.

PRODUCT: {product_display_name}
PRODUCT PROFILE:
- Cloud-native: {cloud_native}
- Deployment: {deployment}
- APIs: {apis}
- Microservices: {microservices}
- Uses Snowflake: {uses_snowflake}

Generate {count} realistic RFP questions that a customer would ask about this product.
Distribute questions across these topics (at least 1 per topic if count allows):
{topics}

Mix categories: technical, functional, consulting, customer_executive.

Return JSON array:
[{{"question": "...", "topic": "deployment", "category": "technical"}}, ...]

Rules:
1. Questions should be realistic (like actual RFP questionnaires)
2. Each question should be answerable from a product knowledge base
3. Vary difficulty: simple factual, comparison, scenario-based
4. Do NOT include questions about pricing or commercial terms"""

SCORING_PROMPT = """Score this RFP answer for accuracy against the product profile.

PRODUCT: {product_display_name}
PRODUCT PROFILE:
- Cloud-native: {cloud_native}
- Deployment: {deployment}
- APIs: {apis}
- Microservices: {microservices}

FORBIDDEN CLAIMS:
{forbidden_claims}

QUESTION: {question}
ANSWER: {answer}

Score accuracy 0-5:
5 = Perfectly aligned with profile, specific, no forbidden claims
4 = Mostly accurate, minor gaps
3 = Acceptable but vague or incomplete
2 = Some inaccuracies or forbidden claims
1 = Mostly wrong
0 = Completely wrong or empty

Return JSON: {{"accuracy": 4, "issues": ["minor issue"]}}"""


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------

def load_profile(family: str, profiles_dir: Path = PROFILES_DIR) -> dict:
    """Load effective product profile."""
    path = profiles_dir / f"{family}.yaml"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# Stage 1: Generate questions
# ---------------------------------------------------------------------------

def build_question_prompt(profile: dict, count: int) -> str:
    """Build the question generation prompt."""
    forbidden = profile.get("forbidden_claims", [])
    topics_per_count = max(1, count // len(QUESTION_TOPICS))
    topics_str = "\n".join(
        f"- {t} ({topics_per_count} questions)" for t in QUESTION_TOPICS
    )

    return QUESTION_GEN_PROMPT.format(
        product_display_name=profile.get("display_name", profile.get("product", "unknown")),
        cloud_native=profile.get("cloud_native", "unknown"),
        deployment=profile.get("deployment", []),
        apis=profile.get("apis", []),
        microservices=profile.get("microservices", "unknown"),
        uses_snowflake=profile.get("uses_snowflake", "unknown"),
        count=count,
        topics=topics_str,
    )


def parse_questions(raw_text: str) -> list[dict]:
    """Parse LLM response into list of question dicts."""
    if not raw_text:
        return []

    text = re.sub(r'^```(?:json)?\s*', '', raw_text.strip(), flags=re.MULTILINE)
    text = re.sub(r'```\s*$', '', text.strip(), flags=re.MULTILINE).strip()

    for attempt in [text, raw_text.strip()]:
        try:
            data = json.loads(attempt)
            if isinstance(data, list):
                return [q for q in data if isinstance(q, dict) and "question" in q]
        except json.JSONDecodeError:
            pass

        m = re.search(r'(\[[\s\S]*\])', attempt)
        if m:
            try:
                data = json.loads(m.group(1))
                if isinstance(data, list):
                    return [q for q in data if isinstance(q, dict) and "question" in q]
            except json.JSONDecodeError:
                pass

    return []


def generate_questions(profile: dict, count: int,
                       model: str = "gemini-flash") -> list[dict]:
    """Generate RFP questions from product profile."""
    from kb_extract_historical import call_llm

    prompt = build_question_prompt(profile, count)
    raw = call_llm(prompt, model=model)
    questions = parse_questions(raw)

    # Ensure topic field
    for q in questions:
        if "topic" not in q:
            q["topic"] = "general"
        if "category" not in q:
            q["category"] = "technical"

    return questions[:count]


# ---------------------------------------------------------------------------
# Stage 2: Answer via RAG
# ---------------------------------------------------------------------------

def answer_via_rag(question: str, family: str,
                   top_k: int = 3,
                   chroma_path: Optional[Path] = None) -> dict:
    """Query ChromaDB to answer a question. Returns answer dict."""
    if chroma_path is None:
        chroma_path = KB_DIR / "chroma_store"

    if not chroma_path.exists():
        return {"answered": False, "answer": "", "entry_ids": [], "confidence": 0.0}

    try:
        import chromadb
        from chromadb.utils import embedding_functions

        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="BAAI/bge-large-en-v1.5"
        )
        client = chromadb.PersistentClient(path=str(chroma_path))
        collection = client.get_collection(
            name="rfp_knowledge_base",
            embedding_function=ef,
        )

        results = collection.query(
            query_texts=[question],
            n_results=top_k,
            where={"domain": family} if family else None,
        )

        if not results or not results["ids"] or not results["ids"][0]:
            return {"answered": False, "answer": "", "entry_ids": [], "confidence": 0.0}

        ids = results["ids"][0]
        distances = results["distances"][0] if results.get("distances") else [999] * len(ids)
        metadatas = results["metadatas"][0] if results.get("metadatas") else [{}] * len(ids)

        # Use best match
        best_dist = distances[0]
        best_meta = metadatas[0]
        confidence = max(0.0, 1.0 - best_dist / 2.0)

        answer = best_meta.get("canonical_answer", "")
        entry_ids = [m.get("kb_id", i) for i, m in zip(ids, metadatas)]

        return {
            "answered": bool(answer and confidence > 0.3),
            "answer": answer,
            "entry_ids": entry_ids,
            "confidence": round(confidence, 3),
        }

    except Exception:
        return {"answered": False, "answer": "", "entry_ids": [], "confidence": 0.0}


# ---------------------------------------------------------------------------
# Stage 3: Score answers
# ---------------------------------------------------------------------------

def build_scoring_prompt(profile: dict, question: str, answer: str) -> str:
    """Build accuracy scoring prompt."""
    forbidden = profile.get("forbidden_claims", [])
    return SCORING_PROMPT.format(
        product_display_name=profile.get("display_name", profile.get("product", "unknown")),
        cloud_native=profile.get("cloud_native", "unknown"),
        deployment=profile.get("deployment", []),
        apis=profile.get("apis", []),
        microservices=profile.get("microservices", "unknown"),
        forbidden_claims="\n".join(f"- {fc}" for fc in forbidden[:15]) if forbidden else "(none)",
        question=question,
        answer=answer,
    )


def parse_accuracy(raw_text: str) -> dict:
    """Parse accuracy score from LLM response."""
    if not raw_text:
        return {"accuracy": 0, "issues": ["No response"]}

    text = re.sub(r'^```(?:json)?\s*', '', raw_text.strip(), flags=re.MULTILINE)
    text = re.sub(r'```\s*$', '', text.strip(), flags=re.MULTILINE).strip()

    for attempt in [text, raw_text.strip()]:
        try:
            data = json.loads(attempt)
            if isinstance(data, dict) and "accuracy" in data:
                return data
        except json.JSONDecodeError:
            pass

        m = re.search(r'(\{[\s\S]*\})', attempt)
        if m:
            try:
                data = json.loads(m.group(1))
                if isinstance(data, dict) and "accuracy" in data:
                    return data
            except json.JSONDecodeError:
                pass

    return {"accuracy": 0, "issues": ["Could not parse scoring response"]}


# ---------------------------------------------------------------------------
# Stage 4: Report
# ---------------------------------------------------------------------------

def build_simulation_report(family: str, questions: list[dict],
                            rag_results: list[dict],
                            score_results: list[dict]) -> dict:
    """Build simulation report dict."""
    answered = sum(1 for r in rag_results if r.get("answered"))
    unanswered = len(questions) - answered

    accuracies = [s.get("accuracy", 0) for s in score_results if s.get("accuracy", 0) > 0]
    avg_accuracy = round(sum(accuracies) / len(accuracies), 1) if accuracies else 0.0

    # Coverage by topic
    topic_results = {}
    for q, rag, score in zip(questions, rag_results, score_results):
        topic = q.get("topic", "general")
        if topic not in topic_results:
            topic_results[topic] = {"total": 0, "answered": 0, "accuracies": []}
        topic_results[topic]["total"] += 1
        if rag.get("answered"):
            topic_results[topic]["answered"] += 1
            topic_results[topic]["accuracies"].append(score.get("accuracy", 0))

    coverage = {}
    for topic in QUESTION_TOPICS:
        tr = topic_results.get(topic, {"total": 0, "answered": 0, "accuracies": []})
        total = tr["total"]
        ans = tr["answered"]
        accs = tr["accuracies"]
        coverage[topic] = {
            "total": total,
            "answered": ans,
            "pct": round(ans / total * 100) if total else 0,
            "avg_accuracy": round(sum(accs) / len(accs), 1) if accs else 0,
        }

    return {
        "family": family,
        "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "question_count": len(questions),
        "answered": answered,
        "unanswered": unanswered,
        "avg_accuracy": avg_accuracy,
        "coverage": coverage,
        "details": [
            {
                "question": q.get("question", ""),
                "topic": q.get("topic", ""),
                "answered": rag.get("answered", False),
                "confidence": rag.get("confidence", 0),
                "entry_ids": rag.get("entry_ids", []),
                "accuracy": score.get("accuracy", 0),
                "issues": score.get("issues", []),
            }
            for q, rag, score in zip(questions, rag_results, score_results)
        ],
    }


def print_simulation_report(report: dict) -> None:
    """Print formatted simulation report."""
    border = "=" * 66
    print(f"\n{border}")
    print(f"  Simulated RFP Report: {report['family']}")
    print(f"{border}")
    print(f"  Questions: {report['question_count']}")
    print(f"  Answered:  {report['answered']} "
          f"({report['answered']*100//max(report['question_count'],1)}%)")
    print(f"  Unanswered: {report['unanswered']}")
    print(f"  Avg accuracy: {report['avg_accuracy']}/5")

    print(f"\n  Coverage by topic:")
    for topic in QUESTION_TOPICS:
        cov = report["coverage"].get(topic, {})
        total = cov.get("total", 0)
        answered = cov.get("answered", 0)
        pct = cov.get("pct", 0)
        bar_len = pct * 10 // 100
        bar = "#" * bar_len
        if total > 0:
            print(f"    {topic:<22}: {answered}/{total} ({pct}%) {bar}")
        else:
            print(f"    {topic:<22}: (no questions)")

    print(f"{border}")


def save_simulation(report: dict, simulations_dir: Path = SIMULATIONS_DIR) -> Path:
    """Save simulation report to data/kb/simulations/."""
    simulations_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = simulations_dir / f"{report['family']}_{today}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    return path


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def simulate(family: str, count: int = 50,
             model: str = "gemini-flash",
             batch_mode: bool = False,
             dry_run: bool = False,
             profiles_dir: Path = PROFILES_DIR,
             chroma_path: Optional[Path] = None,
             simulations_dir: Path = SIMULATIONS_DIR) -> dict:
    """Run full simulation pipeline for one family."""
    profile = load_profile(family, profiles_dir)
    if not profile:
        print(f"[ERROR] No profile for '{family}'")
        return {}

    # Stage 1: Generate questions
    print(f"[Stage 1] Generating {count} RFP questions for {family}...")
    if dry_run:
        print(f"[DRY RUN] Would generate {count} questions and answer via RAG")
        return {"family": family, "question_count": count, "dry_run": True}

    questions = generate_questions(profile, count, model=model)
    print(f"  Generated {len(questions)} questions")

    if not questions:
        print("[WARN] No questions generated")
        return {"family": family, "question_count": 0}

    # Stage 2: Answer via RAG
    print(f"[Stage 2] Answering via RAG...")
    rag_results = []
    for i, q in enumerate(questions):
        print(f"  Answering {i+1}/{len(questions)}...", end="\r")
        result = answer_via_rag(q["question"], family, chroma_path=chroma_path)
        rag_results.append(result)
    print()

    answered = sum(1 for r in rag_results if r["answered"])
    print(f"  Answered: {answered}/{len(questions)}")

    # Stage 3: Score answered questions
    print(f"[Stage 3] Scoring answers...")
    from kb_extract_historical import call_llm

    score_results = []
    for i, (q, rag) in enumerate(zip(questions, rag_results)):
        if not rag["answered"]:
            score_results.append({"accuracy": 0, "issues": ["Unanswered"]})
            continue

        print(f"  Scoring {i+1}/{len(questions)}...", end="\r")
        prompt = build_scoring_prompt(profile, q["question"], rag["answer"])
        try:
            raw = call_llm(prompt, model=model)
            score = parse_accuracy(raw)
        except Exception:
            score = {"accuracy": 0, "issues": ["Scoring failed"]}
        score_results.append(score)
    print()

    # Stage 4: Report
    report = build_simulation_report(family, questions, rag_results, score_results)
    path = save_simulation(report, simulations_dir)
    print(f"  Saved: {path.relative_to(PROJECT_ROOT)}")

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Simulated RFP Test Suite -- generate, answer, score",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--family", help="Product family to simulate")
    group.add_argument("--all", action="store_true", dest="all_families",
                       help="Simulate all families with active profiles")
    parser.add_argument("--count", type=int, default=50,
                        help="Number of questions per family (default: 50)")
    parser.add_argument("--model", default="gemini-flash",
                        help="LLM model (default: gemini-flash)")
    parser.add_argument("--batch", action="store_true",
                        help="Use Batch API")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show plan without executing")
    args = parser.parse_args()

    if args.all_families:
        profiles = {}
        if PROFILES_DIR.exists():
            for p in sorted(PROFILES_DIR.glob("*.yaml")):
                if not p.name.startswith("."):
                    with open(p, "r", encoding="utf-8") as f:
                        prof = yaml.safe_load(f) or {}
                    if prof.get("_meta", {}).get("status") == "active":
                        profiles[p.stem] = prof

        if not profiles:
            print("[ERROR] No active profiles found")
            return 1

        for family in profiles:
            report = simulate(family, args.count, args.model,
                              args.batch, args.dry_run)
            if report and not args.dry_run:
                print_simulation_report(report)
    else:
        report = simulate(args.family, args.count, args.model,
                          args.batch, args.dry_run)
        if report and not args.dry_run and "details" in report:
            print_simulation_report(report)

    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
