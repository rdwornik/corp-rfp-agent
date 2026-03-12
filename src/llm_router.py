import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Define project root and load .env file explicitly
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
load_dotenv(ENV_PATH, override=True)

import chromadb
from chromadb.utils import embedding_functions
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

# --- CONFIGURATION ---
KB_JSON_PATH = PROJECT_ROOT / "data/kb/canonical/RFP_Database_UNIFIED_CANONICAL.json"
DB_PATH = PROJECT_ROOT / "data/kb/chroma_store"
COLLECTION_NAME = "rfp_knowledge_base"
SYSTEM_PROMPT_PATH = PROJECT_ROOT / "prompts/rfp_system_prompt_universal.txt"
DEBUG = os.environ.get("DEBUG_RAG", "0") == "1"  # Set DEBUG_RAG=1 to enable debug logging

# --- MODEL REGISTRY ---
MODELS = {
    # Google
    "gemini": {"name": "gemini-3.1-pro-preview", "provider": "google"},
    "gemini-flash": {"name": "gemini-3-flash-preview", "provider": "google"},
    # Anthropic
    "sonnet": {"name": "claude-sonnet-4-6", "provider": "anthropic"},
    "claude": {"name": "claude-sonnet-4-6", "provider": "anthropic"},
    "claude-opus": {"name": "claude-opus-4-6", "provider": "anthropic"},
    # OpenAI
    "gpt5": {"name": "gpt-5.2", "provider": "openai"},
    "o3": {"name": "o3", "provider": "openai"},
    # xAI
    "grok": {"name": "grok-3-beta", "provider": "xai"},
    # DeepSeek
    "deepseek": {"name": "deepseek-chat", "provider": "deepseek"},
    "deepseek-r1": {"name": "deepseek-reasoner", "provider": "deepseek"},
    # Moonshot (Kimi)
    "kimi": {"name": "kimi-k2-0905", "provider": "moonshot"},
    # Meta (via Together)
    "llama": {"name": "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8", "provider": "together"},
    # Perplexity
    "perplexity": {"name": "sonar-pro", "provider": "perplexity"},
    # Mistral
    "mistral": {"name": "mistral-large-latest", "provider": "mistral"},
    # Alibaba (Qwen)
    "qwen": {"name": "qwen3-235b-a22b", "provider": "alibaba"},
    # Zhipu (GLM)
    "glm": {"name": "glm-4.7", "provider": "zhipu"},    
}

def load_system_prompt() -> str:
    """Load system prompt from external file."""
    if not SYSTEM_PROMPT_PATH.exists():
        raise FileNotFoundError(f"System prompt not found: {SYSTEM_PROMPT_PATH}")
    with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read()


def clean_bold_markdown(text: str) -> str:
    """Remove bold/italic markdown but keep list structure."""
    import re
    # Remove bold **text** → text
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    # Remove italic *text* → text
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    # Remove headers
    text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
    return text.strip()

import time
import random

def retry_with_backoff(func, max_retries=5, base_delay=2):
    """Retry a function with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            error_str = str(e)
            # Check for rate limit errors (429, 1302, concurrency)
            if "429" in error_str or "1302" in error_str or "concurrency" in error_str.lower():
                delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                print(f"   [WARNING] Rate limited. Retry {attempt + 1}/{max_retries} in {delay:.1f}s...")
                time.sleep(delay)
            else:
                raise  # Re-raise non-rate-limit errors
    raise Exception(f"Max retries ({max_retries}) exceeded")

class LLMRouter:
    def __init__(self, solution: str = None):
        print("[INFO] Initializing Universal LLM Router...")

        # Store family label (legacy parameter name: solution)
        self.family = solution or "planning"

        # Load system prompt
        self.system_prompt_template = load_system_prompt()
        print(f"[SUCCESS] Loaded prompt from: {SYSTEM_PROMPT_PATH.name}")

        # Load KB lookup with multiple key formats for backward compatibility
        with open(KB_JSON_PATH, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
            self.kb_lookup = {}

            for item in raw_data:
                kb_id = item.get("kb_id")
                domain = item.get("domain", "")

                # Store with original kb_id
                if kb_id:
                    self.kb_lookup[kb_id] = item

                    # Also store with domain-prefixed version for ChromaDB compatibility
                    # Handle both legacy (kb_0001) and new (wms_0001) formats
                    if domain:
                        # If kb_id doesn't already have domain prefix, add it
                        if not kb_id.startswith(f"{domain}_"):
                            prefixed_id = f"{domain}_{kb_id}"
                            self.kb_lookup[prefixed_id] = item

        print(f"[SUCCESS] Loaded {len(raw_data)} KB entries from unified database")
        if DEBUG:
            print(f"[DEBUG] Total lookup keys (with domain prefixes): {len(self.kb_lookup)}")

        # Connect to ChromaDB
        self.client_db = chromadb.PersistentClient(path=str(DB_PATH))
        self.ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="BAAI/bge-large-en-v1.5"
        )
        self.collection = self.client_db.get_collection(
            name=COLLECTION_NAME,
            embedding_function=self.ef
        )
        print(f"[SUCCESS] Connected to ChromaDB: {COLLECTION_NAME}")

    def retrieve_context(self, query, k=8):
        """
        Retrieve relevant KB entries for a query.

        Returns list of KB items (dicts with canonical_question, canonical_answer, etc.)
        """
        results = self.collection.query(query_texts=[query], n_results=k)
        found_items = []

        if DEBUG:
            print(f"\n[DEBUG] === RAG Retrieval ===")
            print(f"[DEBUG] Query: {query}")
            print(f"[DEBUG] Requested k={k} results")

        if results['ids'] and len(results['ids']) > 0:
            chroma_ids = results['ids'][0]
            distances = results['distances'][0] if 'distances' in results else [None] * len(chroma_ids)

            if DEBUG:
                print(f"[DEBUG] ChromaDB returned {len(chroma_ids)} IDs")

            for i, chroma_id in enumerate(chroma_ids):
                distance = distances[i]

                if DEBUG:
                    print(f"[DEBUG] {i+1}. ChromaDB ID: {chroma_id} | Distance: {distance}")

                # Lookup in KB dictionary
                if chroma_id in self.kb_lookup:
                    item = self.kb_lookup[chroma_id]
                    found_items.append(item)

                    if DEBUG:
                        domain = item.get('domain', 'N/A')
                        kb_id = item.get('kb_id', 'N/A')
                        question = item.get('canonical_question', '')[:60]
                        print(f"[DEBUG]    [OK] Found: domain={domain} | kb_id={kb_id} | Q={question}...")
                else:
                    if DEBUG:
                        print(f"[DEBUG]    [FAIL] NOT FOUND in kb_lookup! (ID: {chroma_id})")

            if DEBUG:
                print(f"[DEBUG] Total items retrieved: {len(found_items)}/{k}")
                print(f"[DEBUG] === End RAG Retrieval ===\n")
        else:
            if DEBUG:
                print(f"[DEBUG] ChromaDB returned NO results!")
                print(f"[DEBUG] === End RAG Retrieval ===\n")

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
        prompt = self.system_prompt_template.format(
            context=context_str,
            query=query
        )

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
                    config=types.GenerateContentConfig(temperature=0.3, max_output_tokens=8192)
                )
                return clean_bold_markdown(response.text.strip()) if response.text else "Error: Empty response"

            # --- ANTHROPIC CLAUDE ---
            elif provider == "anthropic":
                if not ANTHROPIC_AVAILABLE:
                    return "Error: Anthropic SDK not installed"
                client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
                message = client.messages.create(
                    model=model_name,
                    max_tokens=4096,
                    temperature=0.3,
                    messages=[{"role": "user", "content": prompt}]
                )
                return message.content[0].text.strip()

            # --- OPENAI GPT-5 ---
            elif provider == "openai":
                if not OPENAI_AVAILABLE:
                    return "Error: OpenAI SDK not installed"
                client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_completion_tokens=4096,
                    temperature=0.3
                )
                return response.choices[0].message.content.strip()

            # --- DEEPSEEK ---
            elif provider == "deepseek":
                if not OPENAI_AVAILABLE:
                    return "Error: OpenAI SDK not installed"
                client = OpenAI(
                    api_key=os.environ.get("DEEPSEEK_API_KEY"),
                    base_url="https://api.deepseek.com/v1"
                )
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4096
                )
                return response.choices[0].message.content.strip()

            # --- KIMI K2 (Moonshot) ---
            elif provider == "moonshot":
                if not OPENAI_AVAILABLE:
                    return "Error: OpenAI SDK not installed"
                client = OpenAI(
                    api_key=os.environ.get("MOONSHOT_API_KEY"),
                    base_url="https://api.moonshot.ai/v1"
                )
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4096
                )
                return response.choices[0].message.content.strip()

            # --- LLAMA 4 (via Together.ai) ---
            elif provider == "together":
                if not OPENAI_AVAILABLE:
                    return "Error: OpenAI SDK not installed"
                client = OpenAI(
                    api_key=os.environ.get("TOGETHER_API_KEY"),
                    base_url="https://api.together.xyz/v1"
                )
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4096
                )
                return response.choices[0].message.content.strip()

            # --- GROK (xAI) ---
            elif provider == "xai":
                if not OPENAI_AVAILABLE:
                    return "Error: OpenAI SDK not installed"
                client = OpenAI(
                    api_key=os.environ.get("XAI_API_KEY"),
                    base_url="https://api.x.ai/v1"
                )
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4096
                )
                return response.choices[0].message.content.strip()
            # --- PERPLEXITY ---
            elif provider == "perplexity":
                client = OpenAI(
                    api_key=os.environ.get("PERPLEXITY_API_KEY"),
                    base_url="https://api.perplexity.ai"
                )
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4096
                )
                return clean_bold_markdown(response.choices[0].message.content.strip())

            # --- MISTRAL ---
            elif provider == "mistral":
                client = OpenAI(
                    api_key=os.environ.get("MISTRAL_API_KEY"),
                    base_url="https://api.mistral.ai/v1"
                )
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4096
                )
                return clean_bold_markdown(response.choices[0].message.content.strip())

            # --- ALIBABA (QWEN) ---
            elif provider == "alibaba":
                client = OpenAI(
                    api_key=os.environ.get("DASHSCOPE_API_KEY"),
                    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
                )
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4096
                )
                return clean_bold_markdown(response.choices[0].message.content.strip())

            # --- ZHIPU (GLM) ---
            elif provider == "zhipu":
                def call_glm():
                    client = OpenAI(
                        api_key=os.environ.get("ZHIPU_API_KEY"),
                        base_url="https://api.z.ai/api/paas/v4/"
                    )
                    response = client.chat.completions.create(
                        model=model_name,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=8192
                    )
                    return response.choices[0].message.content.strip()
                
                result = retry_with_backoff(call_glm, max_retries=5, base_delay=2)
                return clean_bold_markdown(result)

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

    parser = argparse.ArgumentParser(description="LLM Router -- generate answers via RAG")
    parser.add_argument("--compare", action="store_true",
                        help="Compare models side by side")
    parser.add_argument("--query", "-q", type=str,
                        default="How do you handle data encryption?",
                        help="Query to answer")
    parser.add_argument("--models", nargs="+", default=None,
                        help="Models to compare (default: gemini sonnet)")
    parser.add_argument("--model", "-m", default="gemini",
                        help="Single model to use (default: gemini)")
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