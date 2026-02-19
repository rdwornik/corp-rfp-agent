"""
kb_extract_historical.py
Extract Q&A pairs from historical RFP Excel files and build/improve canonical KB entries.

Modes:
  CREATE  — for families with 0 or few entries (WMS, Logistics, etc.)
  IMPROVE — for Planning (806 entries) to find gaps and flag improvements

Usage:
  # Extract WMS from historical RFPs and append to WMS canonical
  python src/kb_extract_historical.py --family wms --mode create --model gemini

  # Find gaps in Planning canonical from historical RFPs
  python src/kb_extract_historical.py --family planning --mode improve --model gemini

  # Dry run (no writes)
  python src/kb_extract_historical.py --family wms --mode create --model gemini --dry-run

  # Process single file
  python src/kb_extract_historical.py --family wms --mode create --model gemini --file "path/to/rfp.xlsx"
"""

import os
import sys
import json
import re
import argparse
from pathlib import Path
from datetime import date
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env", override=True)

# =============================================================================
# PATHS
# =============================================================================

HISTORICAL_DIR = PROJECT_ROOT / "data/kb/historical"
CANONICAL_DIR  = PROJECT_ROOT / "data/kb/canonical"
STAGING_DIR    = PROJECT_ROOT / "data/kb/staging"
SCHEMA_DIR     = PROJECT_ROOT / "data/kb/schema"
FAMILY_CONFIG  = SCHEMA_DIR / "family_config.json"

TODAY = date.today().isoformat()

# =============================================================================
# LLM CALL LAYER (no RAG — plain text in, text out)
# =============================================================================

def _load_models():
    """Import MODELS registry from llm_router without triggering ChromaDB init."""
    from src.llm_router import MODELS
    return MODELS

def call_llm(prompt: str, model: str = "gemini") -> str:
    """
    Call any configured LLM with a plain prompt (no RAG, no KB context).
    Returns the raw text response. Raises on error.
    """
    models = _load_models()
    model_config = models.get(model, models["gemini"])
    provider = model_config["provider"]
    model_name = model_config["name"]

    if provider == "google":
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        response = client.models.generate_content(
            model=model_name,
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=8192),
        )
        return response.text.strip() if response.text else ""

    elif provider == "anthropic":
        import anthropic
        client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        msg = client.messages.create(
            model=model_name,
            max_tokens=4096,
            temperature=0.1,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()

    elif provider in ("openai", "deepseek", "moonshot", "together", "xai",
                      "perplexity", "mistral", "alibaba", "zhipu"):
        from openai import OpenAI
        base_urls = {
            "openai":     None,
            "deepseek":   "https://api.deepseek.com/v1",
            "moonshot":   "https://api.moonshot.ai/v1",
            "together":   "https://api.together.xyz/v1",
            "xai":        "https://api.x.ai/v1",
            "perplexity": "https://api.perplexity.ai",
            "mistral":    "https://api.mistral.ai/v1",
            "alibaba":    "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "zhipu":      "https://api.z.ai/api/paas/v4/",
        }
        api_key_env = {
            "openai": "OPENAI_API_KEY", "deepseek": "DEEPSEEK_API_KEY",
            "moonshot": "MOONSHOT_API_KEY", "together": "TOGETHER_API_KEY",
            "xai": "XAI_API_KEY", "perplexity": "PERPLEXITY_API_KEY",
            "mistral": "MISTRAL_API_KEY", "alibaba": "DASHSCOPE_API_KEY",
            "zhipu": "ZHIPU_API_KEY",
        }
        kwargs = {"api_key": os.environ.get(api_key_env[provider])}
        if base_urls[provider]:
            kwargs["base_url"] = base_urls[provider]
        client = OpenAI(**kwargs)
        response = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4096,
            temperature=0.1,
        )
        return response.choices[0].message.content.strip()

    else:
        raise ValueError(f"Unknown provider: {provider}")


def call_llm_json(prompt: str, model: str) -> dict:
    """Call LLM expecting JSON back. Strips markdown fences and parses."""
    raw = call_llm(prompt, model)
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw.strip(), flags=re.MULTILINE)
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        # Try to extract first JSON object/array
        match = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", raw)
        if match:
            return json.loads(match.group(1))
        raise ValueError(f"LLM returned non-JSON: {raw[:200]}") from e


# =============================================================================
# FAMILY CONFIG
# =============================================================================

def load_family_config() -> dict:
    with open(FAMILY_CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)["families"]


def get_family(family_key: str) -> dict:
    config = load_family_config()
    if family_key not in config:
        raise ValueError(f"Unknown family '{family_key}'. Valid: {list(config.keys())}")
    return config[family_key]


# =============================================================================
# CANONICAL FILE I/O
# =============================================================================

def load_canonical(canonical_file: str) -> list:
    path = CANONICAL_DIR / canonical_file
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_canonical(canonical_file: str, entries: list, dry_run: bool = False) -> None:
    path = CANONICAL_DIR / canonical_file
    if dry_run:
        print(f"[DRY-RUN] Would write {len(entries)} entries to {path}")
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    print(f"[SUCCESS] Wrote {len(entries)} entries to {path.name}")


def next_sequence_number(existing_entries: list, prefix: str) -> int:
    """Find the highest sequence number for entries matching prefix, return next."""
    max_seq = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-\w+-(\d+)$")
    for entry in existing_entries:
        kb_id = entry.get("kb_id", "") or entry.get("id", "")
        m = pattern.match(kb_id)
        if m:
            max_seq = max(max_seq, int(m.group(1)))
    return max_seq + 1


# =============================================================================
# EXCEL PARSING
# =============================================================================

def get_excel_files(family_key: str, specific_file: Optional[str] = None) -> list[Path]:
    """Return list of Excel files to process for a family."""
    if specific_file:
        p = Path(specific_file)
        if not p.exists():
            raise FileNotFoundError(f"File not found: {specific_file}")
        return [p]

    folder = HISTORICAL_DIR / family_key
    files = list(folder.glob("*.xlsx")) + list(folder.glob("*.xls"))
    files = [f for f in files if not f.name.startswith("~$")]  # skip temp files
    return sorted(files)


def detect_column_structure(ws, model: str) -> dict:
    """
    Send first ~20 non-empty rows to LLM and ask it to identify column roles.
    Returns dict like: {"question_col": 1, "answer_col": 3, "num_col": 0, "header_row": 1}
    col indices are 1-based (openpyxl convention). None = not present.
    """
    # Collect first 20 non-empty rows as a text table
    rows_text = []
    row_count = 0
    for row in ws.iter_rows(values_only=True):
        if any(cell is not None for cell in row):
            row_str = " | ".join(str(c) if c is not None else "" for c in row[:10])
            rows_text.append(f"Row {row_count + 1}: {row_str}")
            row_count += 1
            if row_count >= 20:
                break

    if not rows_text:
        return {}

    sample = "\n".join(rows_text)

    prompt = f"""You are analyzing an RFP (Request for Proposal) spreadsheet.
Here are the first rows (up to 10 columns shown, pipe-separated):

{sample}

Task: Identify which columns contain RFP data. Return ONLY a JSON object with these keys:
- "header_row": row number (1-based) of the column headers, or null if no header row
- "question_col": column number (1-based) containing the RFP question text, or null
- "answer_col": column number (1-based) containing the answer/response text, or null
- "num_col": column number (1-based) containing question numbers/IDs, or null
- "category_col": column number (1-based) containing category/section labels, or null
- "notes": brief explanation (max 1 sentence)

Rules:
- If there is no answer column (questions-only RFP), set answer_col to null
- Questions are usually longer text asking "Does the system...", "How does...", "Please describe..."
- Answers are typically detailed paragraphs or "Yes"/"No" plus explanation
- Section headers (short text like "Integration", "Security") may appear in the question column but without a matching answer
- Return ONLY valid JSON, no other text"""

    try:
        result = call_llm_json(prompt, model)
        return result
    except Exception as e:
        print(f"   [WARNING] Structure detection failed: {e}")
        return {}


def extract_qa_from_sheet(ws, col_map: dict, source_name: str) -> list[dict]:
    """
    Extract Q&A pairs from a worksheet given a column mapping.
    Returns list of {"question": ..., "answer": ..., "source": ..., "row": ...}
    """
    if not col_map.get("question_col"):
        return []

    q_col = col_map["question_col"]
    a_col = col_map.get("answer_col")
    num_col = col_map.get("num_col")
    header_row = col_map.get("header_row") or 1

    pairs = []
    for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        if row_idx <= header_row:
            continue

        # Safely get cell values (row is a tuple of column values)
        def cell(col_1based):
            idx = col_1based - 1
            if col_1based and 0 <= idx < len(row):
                return row[idx]
            return None

        q_raw = cell(q_col)
        a_raw = cell(a_col) if a_col else None

        if not q_raw:
            continue

        q_text = str(q_raw).strip()
        a_text = str(a_raw).strip() if a_raw else ""

        # Skip section header rows (very short question, no answer)
        if len(q_text) < 15 and not a_text:
            continue

        # Skip rows that look like instructions or metadata
        lower_q = q_text.lower()
        if any(skip in lower_q for skip in ["please read", "instructions:", "note:", "section "]):
            if len(q_text) < 80:
                continue

        # Must look like a question or substantial statement
        if len(q_text) < 20:
            continue

        pairs.append({
            "question": q_text,
            "answer": a_text,
            "source": source_name,
            "row": row_idx,
        })

    return pairs


def extract_from_workbook(wb_path: Path, model: str) -> list[dict]:
    """
    Open an Excel workbook, detect structure on each sheet, extract Q&A pairs.
    Returns combined list from all sheets.
    """
    try:
        import openpyxl
    except ImportError:
        print("[ERROR] openpyxl not installed. Run: pip install openpyxl")
        sys.exit(1)

    print(f"\n[INFO] Processing: {wb_path.name}")

    try:
        wb = openpyxl.load_workbook(wb_path, read_only=True, data_only=True)
    except Exception as e:
        print(f"   [ERROR] Cannot open workbook: {e}")
        return []

    all_pairs = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]

        # Skip obviously irrelevant sheets
        lower_name = sheet_name.lower()
        if any(skip in lower_name for skip in ["cover", "toc", "table of contents", "instructions",
                                                "template", "changelog", "glossary", "scoring"]):
            print(f"   [SKIP] Sheet '{sheet_name}' (looks like metadata/instructions)")
            continue

        # Quick check: does this sheet have content?
        row_count = 0
        for row in ws.iter_rows(values_only=True):
            if any(c is not None for c in row):
                row_count += 1
            if row_count > 3:
                break

        if row_count < 3:
            print(f"   [SKIP] Sheet '{sheet_name}' (too few rows)")
            continue

        print(f"   [INFO] Detecting structure in sheet: '{sheet_name}'...")
        col_map = detect_column_structure(ws, model)

        if not col_map.get("question_col"):
            print(f"   [SKIP] Sheet '{sheet_name}' — no question column detected")
            continue

        notes = col_map.get("notes", "")
        print(f"   [INFO] Col map: Q={col_map.get('question_col')} A={col_map.get('answer_col')} | {notes}")

        source_name = f"{wb_path.name}::{sheet_name}"
        pairs = extract_qa_from_sheet(ws, col_map, source_name)
        print(f"   [INFO] Extracted {len(pairs)} pairs from '{sheet_name}'")
        all_pairs.extend(pairs)

    wb.close()
    return all_pairs


# =============================================================================
# LLM CLASSIFICATION
# =============================================================================

CATEGORY_CODES = {
    "functional":  "FUNC",
    "technical":   "TECH",
    "security":    "SEC",
    "deployment":  "DEPL",
    "commercial":  "COM",
    "general":     "GEN",
}

def classify_pairs_batch(pairs: list[dict], family: dict, model: str) -> list[dict]:
    """
    Use LLM to classify a batch of Q&A pairs.
    Returns pairs with classification fields added.
    Processes in batches of 10 to limit prompt size.
    """
    BATCH_SIZE = 10
    family_name = family["display_name"]
    solution_codes_str = ", ".join(family["solution_codes"])
    classified = []

    for i in range(0, len(pairs), BATCH_SIZE):
        batch = pairs[i : i + BATCH_SIZE]

        items_text = ""
        for j, pair in enumerate(batch):
            q = pair["question"][:300]
            a = (pair["answer"] or "")[:200]
            items_text += f'\n[{j}] Q: {q}\n    A: {a}\n'

        prompt = f"""You are classifying Q&A pairs extracted from RFP documents for {family_name}.

Available solution codes for this product family: {solution_codes_str}

Classify each Q&A pair below. Return a JSON array with one object per pair (same order).

Each object must have:
- "idx": the number in brackets (integer)
- "category": one of: functional | technical | security | deployment | commercial | general
- "subcategory": short snake_case label e.g. core_process, integration, reporting, architecture, mobile, authentication, pricing
- "tags": array of 3-6 lowercase keyword strings (no spaces, use hyphens)
- "solution_codes": array — which solution codes from the list above this Q&A applies to. If it applies to all, return []
- "question_variants": array of 2-3 alternative phrasings of the question (for better search recall)
- "scope": "platform" if this is a general Blue Yonder platform feature (SSO, SLAs, APIs), else "product_specific"
- "confidence": "draft" (default), "verified" only if the answer is clearly factual and complete

Q&A pairs to classify:
{items_text}

Return ONLY a valid JSON array. No extra text."""

        try:
            result = call_llm_json(prompt, model)
            if isinstance(result, list):
                for item in result:
                    idx = item.get("idx", -1)
                    if 0 <= idx < len(batch):
                        batch[idx].update({
                            "category": item.get("category", "functional"),
                            "subcategory": item.get("subcategory", ""),
                            "tags": item.get("tags", []),
                            "solution_codes": item.get("solution_codes", []),
                            "question_variants": item.get("question_variants", []),
                            "scope": item.get("scope", "product_specific"),
                            "confidence": item.get("confidence", "draft"),
                        })
        except Exception as e:
            print(f"   [WARNING] Classification batch failed: {e}")
            # Fallback: mark all in batch as unclassified
            for pair in batch:
                pair.setdefault("category", "functional")
                pair.setdefault("subcategory", "")
                pair.setdefault("tags", [])
                pair.setdefault("solution_codes", [])
                pair.setdefault("question_variants", [])
                pair.setdefault("scope", "product_specific")
                pair.setdefault("confidence", "draft")

        classified.extend(batch)

    return classified


# =============================================================================
# DEDUPLICATION
# =============================================================================

def deduplicate_pairs(pairs: list[dict], threshold: float = 0.85) -> list[dict]:
    """
    Remove near-duplicate questions within a batch using simple token overlap.
    For large batches, this avoids loading embedding models just for dedup.
    """
    def tokenize(text: str) -> set:
        return set(re.findall(r"\b\w{4,}\b", text.lower()))

    def jaccard(a: set, b: set) -> float:
        if not a or not b:
            return 0.0
        return len(a & b) / len(a | b)

    kept = []
    kept_tokens = []

    for pair in pairs:
        q_tokens = tokenize(pair["question"])
        is_dup = False
        for existing_tokens in kept_tokens:
            if jaccard(q_tokens, existing_tokens) >= threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(pair)
            kept_tokens.append(q_tokens)

    removed = len(pairs) - len(kept)
    if removed > 0:
        print(f"   [INFO] Deduplication: removed {removed} near-duplicate pairs ({len(kept)} kept)")

    return kept


# =============================================================================
# ENTRY BUILDER (v2)
# =============================================================================

def build_search_blob(entry: dict) -> str:
    parts = [
        f"DOMAIN: {entry['domain']} | SCOPE: {entry['scope']}",
        f"|| CAT: {entry['category']} / {entry['subcategory']}",
        f"|| TAGS: {', '.join(entry.get('tags', []))}",
        f"|| Q: {entry['canonical_question']}",
    ]
    variants = entry.get("question_variants", [])
    if variants:
        parts.append(f"|| VARIANTS: {' | '.join(variants)}")
    parts.append(f"|| A: {entry['canonical_answer'][:300]}")
    return " ".join(parts)


def build_v2_entry(pair: dict, family_key: str, family: dict, seq_num: int) -> dict:
    """Convert a classified pair dict into a canonical v2 entry."""
    category = pair.get("category", "functional")
    cat_code = CATEGORY_CODES.get(category, "GEN")
    prefix = family["id_prefix"]
    kb_id = f"{prefix}-{cat_code}-{seq_num:04d}"

    entry = {
        "kb_id": kb_id,
        "id": kb_id,
        "domain": family_key,
        "family_code": family_key,
        "scope": pair.get("scope", "product_specific"),
        "category": category,
        "subcategory": pair.get("subcategory", ""),
        "canonical_question": pair["question"],
        "question_variants": pair.get("question_variants", []),
        "canonical_answer": pair.get("answer", ""),
        "solution_codes": pair.get("solution_codes", []),
        "tags": pair.get("tags", []),
        "confidence": pair.get("confidence", "draft"),
        "source_rfps": [pair.get("source", "")],
        "cloud_native_only": family.get("cloud_native", True),
        "versioning": {
            "valid_from": None,
            "valid_until": None,
            "deprecated": False,
            "superseded_by": None,
            "version_notes": [],
        },
        "rich_metadata": {
            "keywords": pair.get("tags", []),
            "question_type": _detect_question_type(pair["question"]),
            "source_type": "rfp_historical",
            "source_id": pair.get("source", ""),
            "scope_confidence": 0.7,
            "auto_classified": True,
        },
        "last_updated": TODAY,
        "created_date": TODAY,
    }
    entry["search_blob"] = build_search_blob(entry)
    return entry


def _detect_question_type(question: str) -> str:
    q = question.strip().lower()
    if q.startswith("what") or q.startswith("which"):
        return "WHAT"
    if q.startswith("how"):
        return "HOW"
    if q.startswith("can ") or q.startswith("can you") or q.startswith("is it possible"):
        return "CAN"
    if q.startswith("does ") or q.startswith("do "):
        return "DOES"
    if q.startswith("is ") or q.startswith("are "):
        return "IS"
    if q.startswith("why"):
        return "WHY"
    if q.startswith("where"):
        return "WHERE"
    if q.startswith("when"):
        return "WHEN"
    if q.startswith("who"):
        return "WHO"
    if q.startswith("please") or q.startswith("describe") or q.startswith("explain"):
        return "HOW"
    return "WHAT"


# =============================================================================
# SIMILARITY (for IMPROVE mode)
# =============================================================================

def embed_texts(texts: list[str]) -> list:
    """Embed texts using BGE-large (same model as ChromaDB index)."""
    try:
        from chromadb.utils import embedding_functions
        ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="BAAI/bge-large-en-v1.5"
        )
        return ef(texts)
    except Exception as e:
        print(f"   [WARNING] Embedding failed: {e}")
        return []


def cosine_similarity(a: list, b: list) -> float:
    import math
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def find_best_match(query_emb: list, existing_embs: list, threshold: float = 0.80) -> tuple[int, float]:
    """Return (index, similarity) of best matching existing entry, or (-1, 0.0) if below threshold."""
    best_idx, best_sim = -1, 0.0
    for i, emb in enumerate(existing_embs):
        sim = cosine_similarity(query_emb, emb)
        if sim > best_sim:
            best_sim = sim
            best_idx = i
    if best_sim >= threshold:
        return best_idx, best_sim
    return -1, best_sim


# =============================================================================
# MODE: CREATE
# =============================================================================

def run_create(family_key: str, model: str, dry_run: bool, specific_file: Optional[str]):
    """
    CREATE mode: Extract Q&A pairs from historical Excels, generate v2 entries,
    append to existing canonical (or create new).
    """
    family = get_family(family_key)
    print(f"\n[CREATE] Family: {family['display_name']} | Model: {model}")
    if dry_run:
        print("[DRY-RUN] No files will be written.\n")

    # Load existing canonical to avoid overwriting
    existing = load_canonical(family["canonical_file"])
    print(f"[INFO] Existing canonical entries: {len(existing)}")

    # Get Excel files
    excel_files = get_excel_files(family_key, specific_file)
    if not excel_files:
        print(f"[WARNING] No Excel files found in data/kb/historical/{family_key}/")
        print(f"   Drop .xlsx files there and re-run.")
        return

    print(f"[INFO] Found {len(excel_files)} Excel file(s) to process")

    # Extract all pairs
    all_pairs = []
    for excel_path in excel_files:
        pairs = extract_from_workbook(excel_path, model)
        all_pairs.extend(pairs)

    print(f"\n[INFO] Total pairs extracted: {len(all_pairs)}")
    if not all_pairs:
        print("[WARNING] No Q&A pairs found. Check column detection or file content.")
        return

    # Deduplicate within batch
    all_pairs = deduplicate_pairs(all_pairs)

    # Classify with LLM
    print(f"\n[INFO] Classifying {len(all_pairs)} pairs with {model.upper()}...")
    classified = classify_pairs_batch(all_pairs, family, model)

    # Build v2 entries
    seq_start = next_sequence_number(existing, family["id_prefix"])
    new_entries = []
    for i, pair in enumerate(classified):
        entry = build_v2_entry(pair, family_key, family, seq_start + i)
        new_entries.append(entry)

    print(f"\n[INFO] Generated {len(new_entries)} new v2 entries (starting at seq {seq_start})")

    # Append and save
    combined = existing + new_entries
    save_canonical(family["canonical_file"], combined, dry_run)

    if not dry_run:
        print(f"\n[DONE] Canonical now has {len(combined)} entries.")
        print(f"   Next step: python src/kb_merge_canonical.py && python src/kb_embed_chroma.py")


# =============================================================================
# MODE: IMPROVE
# =============================================================================

def run_improve(family_key: str, model: str, dry_run: bool, specific_file: Optional[str]):
    """
    IMPROVE mode: Compare historical RFP Q&A against existing canonical.
    - New entries (cosine < 0.80): auto-add to canonical
    - Existing match (cosine >= 0.80): if historical answer is longer/richer, flag for review
    """
    family = get_family(family_key)
    print(f"\n[IMPROVE] Family: {family['display_name']} | Model: {model}")
    if dry_run:
        print("[DRY-RUN] No files will be written.\n")

    existing = load_canonical(family["canonical_file"])
    if not existing:
        print(f"[WARNING] No existing canonical found for {family_key}. Use --mode create instead.")
        return

    print(f"[INFO] Existing canonical entries: {len(existing)}")

    # Pre-embed all existing canonical questions
    print("[INFO] Embedding existing canonical questions (this may take a minute)...")
    existing_questions = [e.get("canonical_question", "") for e in existing]
    existing_embs = embed_texts(existing_questions)
    if not existing_embs:
        print("[ERROR] Could not embed existing entries. Is sentence-transformers installed?")
        return

    # Get Excel files
    excel_files = get_excel_files(family_key, specific_file)
    if not excel_files:
        print(f"[WARNING] No Excel files found in data/kb/historical/{family_key}/")
        return

    print(f"[INFO] Found {len(excel_files)} Excel file(s) to process")

    # Extract all pairs
    all_pairs = []
    for excel_path in excel_files:
        pairs = extract_from_workbook(excel_path, model)
        all_pairs.extend(pairs)

    print(f"\n[INFO] Total pairs extracted: {len(all_pairs)}")
    if not all_pairs:
        return

    all_pairs = deduplicate_pairs(all_pairs)

    # Classify
    print(f"\n[INFO] Classifying {len(all_pairs)} pairs with {model.upper()}...")
    classified = classify_pairs_batch(all_pairs, family, model)

    # Embed historical questions
    print("[INFO] Embedding historical questions...")
    hist_questions = [p["question"] for p in classified]
    hist_embs = embed_texts(hist_questions)
    if not hist_embs:
        print("[ERROR] Could not embed historical questions.")
        return

    # Compare each historical pair against canonical
    new_entries = []
    improvements = []
    seq_start = next_sequence_number(existing, family["id_prefix"])
    seq_offset = 0

    MATCH_THRESHOLD = 0.80

    for i, (pair, hist_emb) in enumerate(zip(classified, hist_embs)):
        best_idx, best_sim = find_best_match(hist_emb, existing_embs, MATCH_THRESHOLD)

        if best_idx == -1:
            # No match — new entry
            entry = build_v2_entry(pair, family_key, family, seq_start + seq_offset)
            new_entries.append(entry)
            seq_offset += 1
        else:
            # Match found — compare answer quality
            existing_answer = existing[best_idx].get("canonical_answer", "")
            hist_answer = pair.get("answer", "")
            existing_kb_id = existing[best_idx].get("kb_id", "?")

            if hist_answer and len(hist_answer) > len(existing_answer) * 1.3:
                # Historical answer is 30%+ longer — flag for review
                improvements.append({
                    "existing_kb_id": existing_kb_id,
                    "similarity": round(best_sim, 4),
                    "existing_question": existing_questions[best_idx],
                    "existing_answer": existing_answer,
                    "historical_question": pair["question"],
                    "historical_answer": hist_answer,
                    "source": pair.get("source", ""),
                    "action": "review_improvement",
                })

    print(f"\n[RESULTS]")
    print(f"  New entries (no match in canonical):  {len(new_entries)}")
    print(f"  Possible improvements flagged:        {len(improvements)}")

    # Write new entries to canonical
    if new_entries:
        combined = existing + new_entries
        save_canonical(family["canonical_file"], combined, dry_run)

    # Write improvements to staging
    if improvements:
        staging_path = STAGING_DIR / f"{family_key}_improvements.jsonl"
        if dry_run:
            print(f"[DRY-RUN] Would write {len(improvements)} improvements to {staging_path}")
        else:
            STAGING_DIR.mkdir(exist_ok=True)
            with open(staging_path, "a", encoding="utf-8") as f:
                for item in improvements:
                    f.write(json.dumps(item, ensure_ascii=False) + "\n")
            print(f"[SUCCESS] Appended {len(improvements)} improvement candidates to {staging_path.name}")
            print(f"   Review and manually apply improvements that are genuinely better.")

    if not dry_run and new_entries:
        print(f"\n[DONE] Canonical now has {len(existing) + len(new_entries)} entries.")
        print(f"   Next step: python src/kb_merge_canonical.py && python src/kb_embed_chroma.py")


# =============================================================================
# MAIN
# =============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Extract Q&A from historical RFP Excel files into the KB canonical format.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python src/kb_extract_historical.py --family wms --mode create --model gemini
  python src/kb_extract_historical.py --family logistics --mode create --model gemini --dry-run
  python src/kb_extract_historical.py --family planning --mode improve --model gemini
  python src/kb_extract_historical.py --family wms --mode create --model gemini --file "data/kb/historical/wms/ACME_2024.xlsx"
        """,
    )
    parser.add_argument(
        "--family", required=True,
        choices=["planning", "wms", "logistics", "scpo", "catman", "workforce",
                 "commerce", "flexis", "network", "doddle", "aiml"],
        help="Product family to process",
    )
    parser.add_argument(
        "--mode", required=True, choices=["create", "improve"],
        help="create: new entries for families with 0/few entries. improve: find gaps in existing KB.",
    )
    parser.add_argument(
        "--model", default="gemini",
        help="LLM model key from llm_router (default: gemini)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would happen without writing any files",
    )
    parser.add_argument(
        "--file", default=None,
        help="Process a single specific Excel file instead of all files in the family folder",
    )

    args = parser.parse_args()

    if args.mode == "create":
        run_create(args.family, args.model, args.dry_run, args.file)
    elif args.mode == "improve":
        run_improve(args.family, args.model, args.dry_run, args.file)


if __name__ == "__main__":
    main()
