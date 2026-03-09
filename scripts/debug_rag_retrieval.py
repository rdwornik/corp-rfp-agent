"""
Diagnostic script to analyze RAG retrieval quality.
Run: python scripts/debug_rag_retrieval.py "your question here"
"""
import sys
import json
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from llm_router import LLMRouter

def diagnose_retrieval(query: str, k: int = 8):
    print("=" * 60)
    print("RAG RETRIEVAL DIAGNOSTIC")
    print("=" * 60)
    print(f"\nQuery: {query}\n")
    print(f"Retrieving top {k} results...\n")

    router = LLMRouter()
    results = router.retrieve_context(query, k=k)

    print(f"Found {len(results)} results:\n")

    for i, item in enumerate(results, 1):
        kb_id = item.get('kb_id', 'N/A')
        question = item.get('canonical_question', '')[:80]
        answer = item.get('canonical_answer', '')[:150]

        print(f"--- Result {i}: {kb_id} ---")
        print(f"Q: {question}...")
        print(f"A: {answer}...")
        print()

    # Check if browser-specific entries were retrieved
    browser_ids = {'kb_0648', 'kb_0649', 'kb_0669', 'kb_0670'}
    found_browser = [item.get('kb_id') for item in results if item.get('kb_id') in browser_ids]

    print("=" * 60)
    print("BROWSER ENTRIES CHECK")
    print("=" * 60)
    print(f"Browser-specific KB IDs: {browser_ids}")
    print(f"Found in results: {found_browser if found_browser else 'NONE'}")

    if not found_browser:
        print("\n[WARNING] No browser-specific entries retrieved!")
        print("   This explains why agent doesn't mention Chrome/Edge/IE11")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        # Default test query
        query = """The system should support all functionality via a web user interface (via browser).
        Please state, whether additional user interface options are provided, e.g., Excel-plugin,
        desktop client (which needs to be installed on the local computer).
        Could you name the preferred supported internet browser?"""

    diagnose_retrieval(query)
