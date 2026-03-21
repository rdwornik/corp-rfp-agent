import os
import json
import logging
import re
import time
import random
from pathlib import Path
from dotenv import load_dotenv

# Define project root
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Global API keys (Documents/.secrets/.env)
_global_env = Path.home() / "Documents" / ".secrets" / ".env"
if _global_env.exists():
    load_dotenv(_global_env, override=False)

# Local .env (project-specific vars only)
load_dotenv(PROJECT_ROOT / ".env", override=False)

from google import genai
from google.genai import types

# Optional imports
try:
    import anthropic

    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    from openai import OpenAI

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

# ChromaDB — optional fallback (being phased out in favour of vault)
try:
    import chromadb
    from chromadb.utils import embedding_functions

    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False

from vault_adapter import retrieve as vault_retrieve

logger = logging.getLogger(__name__)

# --- CONFIGURATION ---
KB_JSON_PATH = PROJECT_ROOT / "data/kb/canonical/RFP_Database_UNIFIED_CANONICAL.json"
DB_PATH = PROJECT_ROOT / "data/kb/chroma_store"
COLLECTION_NAME = "rfp_knowledge_base"
SYSTEM_PROMPT_PATH = PROJECT_ROOT / "prompts/rfp_system_prompt_universal.txt"
DEBUG = (
    os.environ.get("DEBUG_RAG", "0") == "1"
)  # Set DEBUG_RAG=1 to enable debug logging

# --- MODEL REGISTRY ---
MODELS = {
    # Google Gemini
    "gemini": {"name": "gemini-3.1-pro-preview", "provider": "google"},
    "gemini-flash": {"name": "gemini-3-flash-preview", "provider": "google"},
    # Anthropic Claude
    "sonnet": {"name": "claude-sonnet-4-6", "provider": "anthropic"},
    # OpenAI
    "gpt": {"name": "gpt-4o", "provider": "openai"},
}


def load_system_prompt() -> str:
    """Load system prompt from external file."""
    if not SYSTEM_PROMPT_PATH.exists():
        raise FileNotFoundError(f"System prompt not found: {SYSTEM_PROMPT_PATH}")
    with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read()


def clean_bold_markdown(text: str) -> str:
    """Remove bold/italic markdown but keep list structure."""
    # Remove bold **text** -> text
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    # Remove italic *text* -> text
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    # Remove headers
    text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
    return text.strip()


def retry_with_backoff(func, max_retries=5, base_delay=2):
    """Retry a function with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            error_str = str(e)
            # Check for rate limit errors (429, 1302, concurrency)
            if (
                "429" in error_str
                or "1302" in error_str
                or "concurrency" in error_str.lower()
            ):
                delay = base_delay * (2**attempt) + random.uniform(0, 1)
                print(
                    f"   [WARNING] Rate limited. Retry {attempt + 1}/{max_retries} in {delay:.1f}s..."
                )
                time.sleep(delay)
            else:
                raise  # Re-raise non-rate-limit errors
    raise Exception(f"Max retries ({max_retries}) exceeded")


# ---------------------------------------------------------------------------
# Vault note content parsing helpers
# ---------------------------------------------------------------------------


def extract_question(content: str) -> str:
    """Extract the ## Question section from vault markdown content."""
    match = re.search(
        r"^##\s+Question\s*\n(.*?)(?=^##\s|\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    # Fallback: if no ## Question header, return first non-empty line
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("---") and not stripped.startswith("#"):
            return stripped
    return ""


def extract_answer(content: str) -> str:
    """Extract the ## Answer section from vault markdown content."""
    match = re.search(
        r"^##\s+Answer\s*\n(.*?)(?=^##\s|\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    # Fallback: return everything after ## Question block (or full content)
    q_end = re.search(
        r"^##\s+Question\s*\n.*?(?=^##\s|\Z)", content, re.MULTILINE | re.DOTALL
    )
    if q_end:
        remainder = content[q_end.end() :].strip()
        # Strip any remaining ## headers
        remainder = re.sub(r"^##\s+\w+\s*\n", "", remainder, flags=re.MULTILINE).strip()
        if remainder:
            return remainder
    return content.strip()


def _vault_notes_to_kb_items(notes: list[dict]) -> list[dict]:
    """Convert vault note dicts to the KB item format that format_context() expects."""
    items = []
    for n in notes:
        content = n.get("content", "")
        items.append(
            {
                "kb_id": str(n.get("note_id", "")),
                "category": ", ".join(n.get("topics", [])[:2])
                if n.get("topics")
                else "",
                "subcategory": "",
                "canonical_question": extract_question(content),
                "canonical_answer": extract_answer(content),
                "domain": ", ".join(n.get("products", [])[:1])
                if n.get("products")
                else "",
            }
        )
    return items


# ---------------------------------------------------------------------------
# LLMRouter
# ---------------------------------------------------------------------------


class LLMRouter:
    def __init__(self, solution: str = None):
        print("[INFO] Initializing Universal LLM Router...")

        # Store family label (legacy parameter name: solution)
        self.family = solution or "planning"

        # Load system prompt
        self.system_prompt_template = load_system_prompt()
        print(f"[SUCCESS] Loaded prompt from: {SYSTEM_PROMPT_PATH.name}")

        # ChromaDB fallback: load KB lookup + connect (optional)
        self.kb_lookup = {}
        self.collection = None
        self._init_chromadb_fallback()

    def _init_chromadb_fallback(self):
        """Attempt to load ChromaDB + KB JSON for fallback retrieval."""
        if not CHROMADB_AVAILABLE:
            print("[INFO] ChromaDB not available (optional fallback)")
            return

        try:
            if KB_JSON_PATH.exists():
                with open(KB_JSON_PATH, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
                for item in raw_data:
                    kb_id = item.get("kb_id")
                    domain = item.get("domain", "")
                    if kb_id:
                        self.kb_lookup[kb_id] = item
                        if domain and not kb_id.startswith(f"{domain}_"):
                            self.kb_lookup[f"{domain}_{kb_id}"] = item
                print(f"[INFO] ChromaDB fallback: loaded {len(raw_data)} KB entries")

            if DB_PATH.exists():
                client_db = chromadb.PersistentClient(path=str(DB_PATH))
                ef = embedding_functions.SentenceTransformerEmbeddingFunction(
                    model_name="BAAI/bge-large-en-v1.5"
                )
                self.collection = client_db.get_collection(
                    name=COLLECTION_NAME, embedding_function=ef
                )
                print(f"[INFO] ChromaDB fallback: connected to {COLLECTION_NAME}")
        except Exception as exc:
            logger.warning("ChromaDB fallback init failed: %s", exc)

    def retrieve_context(self, query, k=8):
        """Retrieve relevant KB entries for a query.

        Primary: vault_adapter.retrieve (corp CLI / SQLite FTS5).
        Fallback: ChromaDB (kept during transition).

        Returns list of KB items (dicts with canonical_question, canonical_answer, etc.)
        """
        # --- Primary: vault retrieval ---
        try:
            products = [self.family] if self.family else None
            notes = vault_retrieve(query, products=products, limit=k)
            if notes:
                if DEBUG:
                    print(
                        f"[DEBUG] Vault returned {len(notes)} notes for: {query[:60]}"
                    )
                return _vault_notes_to_kb_items(notes)
        except Exception as exc:
            logger.warning("Vault retrieval failed: %s, trying ChromaDB fallback", exc)

        # --- Fallback: ChromaDB ---
        return self._retrieve_chromadb(query, k=k)

    def _retrieve_chromadb(self, query, k=8):
        """ChromaDB fallback retrieval (transition period)."""
        if self.collection is None:
            if DEBUG:
                print("[DEBUG] No ChromaDB collection available")
            return []

        results = self.collection.query(query_texts=[query], n_results=k)
        found_items = []

        if DEBUG:
            print("\n[DEBUG] === ChromaDB Fallback Retrieval ===")
            print(f"[DEBUG] Query: {query}")

        if results["ids"] and len(results["ids"]) > 0:
            chroma_ids = results["ids"][0]
            distances = (
                results["distances"][0]
                if "distances" in results
                else [None] * len(chroma_ids)
            )

            for i, chroma_id in enumerate(chroma_ids):
                if chroma_id in self.kb_lookup:
                    found_items.append(self.kb_lookup[chroma_id])

                    if DEBUG:
                        print(f"[DEBUG] {i + 1}. {chroma_id} | dist={distances[i]}")

        if DEBUG:
            print(f"[DEBUG] ChromaDB fallback retrieved: {len(found_items)}/{k}")

        return found_items

    def format_context(self, items) -> str:
        """Format KB items into context string."""
        parts = []
        for item in items:
            parts.append(
                f"---\n"
                f"Category: {item.get('category', '')} / {item.get('subcategory', '')}\n"
                f"Canonical Question: {item.get('canonical_question', '')}\n"
                f"Canonical Answer: {item.get('canonical_answer', '')}\n"
                f"---"
            )
        return "\n\n".join(parts)

    def generate_answer(self, query, model="gemini"):
        context_items = self.retrieve_context(query, k=8)

        if not context_items:
            return "Not in KB"

        # Build prompt from template
        context_str = self.format_context(context_items)
        prompt = self.system_prompt_template.format(context=context_str, query=query)

        model_config = MODELS.get(model, MODELS["gemini"])
        provider = model_config["provider"]
        model_name = model_config["name"]

        print(f"[INFO] Generating with {model.upper()} ({model_name})...")

        try:
            # --- GOOGLE GEMINI ---
            if provider == "google":
                client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
                response = client.models.generate_content(
                    model=model_name,
                    contents=[{"role": "user", "parts": [{"text": prompt}]}],
                    config=types.GenerateContentConfig(
                        temperature=0.3, max_output_tokens=8192
                    ),
                )
                return (
                    clean_bold_markdown(response.text.strip())
                    if response.text
                    else "Error: Empty response"
                )

            # --- ANTHROPIC CLAUDE ---
            elif provider == "anthropic":
                if not ANTHROPIC_AVAILABLE:
                    return "Error: Anthropic SDK not installed"
                client = anthropic.Anthropic(
                    api_key=os.environ.get("ANTHROPIC_API_KEY")
                )
                message = client.messages.create(
                    model=model_name,
                    max_tokens=4096,
                    temperature=0.3,
                    messages=[{"role": "user", "content": prompt}],
                )
                return message.content[0].text.strip()

            # --- OPENAI ---
            elif provider == "openai":
                if not OPENAI_AVAILABLE:
                    return "Error: OpenAI SDK not installed"
                client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_completion_tokens=4096,
                    temperature=0.3,
                )
                return response.choices[0].message.content.strip()

            else:
                return f"Error: Unknown provider {provider}"

        except Exception as e:
            return f"Error: {str(e)}"


def compare_models(query: str, models: list[str] | None = None) -> dict:
    """Run the same query through multiple models side by side.

    Returns dict mapping model key to {answer, elapsed, chars}.
    """
    if models is None:
        models = ["gemini", "sonnet"]

    router = LLMRouter()
    results = {}

    for model_key in models:
        if model_key not in MODELS:
            print(f"[WARN] Unknown model: {model_key}")
            continue
        model_name = MODELS[model_key]["name"]
        start = time.time()
        answer = router.generate_answer(query, model=model_key)
        elapsed = time.time() - start
        results[model_key] = {
            "model_name": model_name,
            "answer": answer,
            "elapsed": round(elapsed, 1),
            "chars": len(answer),
        }

    # Print comparison
    border = "=" * 60
    print(f"\n{border}")
    print("  Model Comparison")
    print(border)
    print(f"  Query: {query}")
    print()
    for key, r in results.items():
        print(f"  --- {r['model_name']} ({r['elapsed']}s, {r['chars']} chars) ---")
        print(f"  {r['answer'][:500]}")
        if len(r["answer"]) > 500:
            print(f"  ... ({r['chars'] - 500} more chars)")
        print()
    print(border)

    return results


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="LLM Router -- generate answers via RAG"
    )
    parser.add_argument(
        "--compare", action="store_true", help="Compare models side by side"
    )
    parser.add_argument(
        "--query",
        "-q",
        type=str,
        default="How do you handle data encryption?",
        help="Query to answer",
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=None,
        help="Models to compare (default: gemini sonnet)",
    )
    parser.add_argument(
        "--model", "-m", default="gemini", help="Single model to use (default: gemini)"
    )
    args = parser.parse_args()

    if args.compare:
        compare_models(args.query, args.models)
    else:
        router = LLMRouter()
        answer = router.generate_answer(args.query, model=args.model)
        print("\n" + "=" * 50)
        print("[ANSWER]")
        print("=" * 50)
        print(answer)
