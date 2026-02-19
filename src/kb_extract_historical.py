"""
kb_extract_historical.py  —  3-Stage RFP Extraction Pipeline

Stages:
  1. Structure Analysis  — openpyxl prescan + interactive metadata + LLM analysis
  2. Extraction          — programmatic Q/A pair extraction from detected columns
  3. Classification + Review — LLM classify (gemini-flash) + interactive terminal review

File flow:
  historical/{family}/inbox/file.xlsx
      -> Stage 1: move to processing/, analyze structure
      -> Stage 2: extract Q/A pairs
      -> Stage 3: classify + Rob reviews interactively
      -> accepted pairs -> canonical (appended)
      -> archive: file + structure + extractions + registry updated
      -> cleanup: processing/ emptied

Usage:
  python src/kb_extract_historical.py --family wms
  python src/kb_extract_historical.py --family wms --file "Acme_RFP.xlsx"
  python src/kb_extract_historical.py --family planning          # auto IMPROVE mode
  python src/kb_extract_historical.py --family wms --resume
  python src/kb_extract_historical.py --family wms --model gemini-flash
"""

import os, sys, json, re, shutil, textwrap
from pathlib import Path
from datetime import date, datetime
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env", override=True)

# ============================================================
# PATHS
# ============================================================
HISTORICAL_DIR = PROJECT_ROOT / "data/kb/historical"
CANONICAL_DIR  = PROJECT_ROOT / "data/kb/canonical"
STAGING_DIR    = PROJECT_ROOT / "data/kb/staging"
ARCHIVE_DIR    = PROJECT_ROOT / "data/kb/archive"
SCHEMA_DIR     = PROJECT_ROOT / "data/kb/schema"
FAMILY_CONFIG  = SCHEMA_DIR / "family_config.json"
REGISTRY_PATH  = ARCHIVE_DIR / "archive_registry.json"

TODAY     = date.today().isoformat()
NOW_ISO   = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
WRAP_W    = 72   # terminal text wrap width

CATEGORY_CODES = {
    "functional": "FUNC", "technical": "TECH", "security": "SEC",
    "deployment": "DEPL", "commercial": "COM",  "general":  "GEN",
}

# ============================================================
# SECTION 1 — LLM CALL LAYER
# ============================================================
def _load_models() -> dict:
    from src.llm_router import MODELS
    return MODELS


def call_llm(prompt: str, model: str = "gemini-flash") -> str:
    """Plain LLM call — no RAG. Returns raw text response."""
    models = _load_models()
    cfg   = models.get(model, models["gemini-flash"])
    prov  = cfg["provider"]
    mname = cfg["name"]

    if prov == "google":
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        r = client.models.generate_content(
            model=mname,
            contents=[{"role": "user", "parts": [{"text": prompt}]}],
            config=types.GenerateContentConfig(temperature=0.1, max_output_tokens=8192),
        )
        return r.text.strip() if r.text else ""

    elif prov == "anthropic":
        import anthropic
        c = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        m = c.messages.create(model=mname, max_tokens=4096, temperature=0.1,
                              messages=[{"role": "user", "content": prompt}])
        return m.content[0].text.strip()

    else:  # OpenAI-compatible providers
        from openai import OpenAI
        urls = {
            "openai": None, "deepseek": "https://api.deepseek.com/v1",
            "moonshot": "https://api.moonshot.ai/v1", "together": "https://api.together.xyz/v1",
            "xai": "https://api.x.ai/v1", "perplexity": "https://api.perplexity.ai",
            "mistral": "https://api.mistral.ai/v1",
            "alibaba": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "zhipu": "https://api.z.ai/api/paas/v4/",
        }
        keys = {
            "openai": "OPENAI_API_KEY", "deepseek": "DEEPSEEK_API_KEY",
            "moonshot": "MOONSHOT_API_KEY", "together": "TOGETHER_API_KEY",
            "xai": "XAI_API_KEY", "perplexity": "PERPLEXITY_API_KEY",
            "mistral": "MISTRAL_API_KEY", "alibaba": "DASHSCOPE_API_KEY",
            "zhipu": "ZHIPU_API_KEY",
        }
        kw = {"api_key": os.environ.get(keys.get(prov, ""))}
        if urls.get(prov):
            kw["base_url"] = urls[prov]
        c = OpenAI(**kw)
        r = c.chat.completions.create(model=mname, temperature=0.1, max_tokens=4096,
                                      messages=[{"role": "user", "content": prompt}])
        return r.choices[0].message.content.strip()


def call_llm_json(prompt: str, model: str) -> object:
    """Call LLM expecting JSON. Strips markdown fences and parses."""
    raw = call_llm(prompt, model)
    raw = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r"```\s*$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"(\{[\s\S]*\}|\[[\s\S]*\])", raw)
        if m:
            return json.loads(m.group(1))
        raise ValueError(f"LLM returned non-JSON: {raw[:300]}")


# ============================================================
# SECTION 2 — FAMILY + CONFIG HELPERS
# ============================================================
def load_family_config() -> dict:
    with open(FAMILY_CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)["families"]


def get_family(key: str) -> dict:
    cfg = load_family_config()
    if key not in cfg:
        raise ValueError(f"Unknown family '{key}'. Valid: {list(cfg.keys())}")
    return cfg[key]


def load_canonical(canon_file: str) -> list:
    p = CANONICAL_DIR / canon_file
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_canonical(canon_file: str, entries: list) -> None:
    p = CANONICAL_DIR / canon_file
    with open(p, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    print(f"[OK] Wrote {len(entries)} entries -> {p.name}")


def next_seq(existing: list, prefix: str) -> int:
    pat = re.compile(rf"^{re.escape(prefix)}-\w+-(\d+)$")
    mx = 0
    for e in existing:
        m = pat.match(e.get("kb_id", "") or e.get("id", ""))
        if m:
            mx = max(mx, int(m.group(1)))
    return mx + 1


# ============================================================
# SECTION 3 — ARCHIVE HELPERS
# ============================================================
def load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {"version": "1.0", "last_updated": "", "total_files": 0,
                "total_qa_extracted": 0, "entries": []}
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_registry(reg: dict) -> None:
    reg["last_updated"] = NOW_ISO
    with open(REGISTRY_PATH, "w", encoding="utf-8") as f:
        json.dump(reg, f, indent=2, ensure_ascii=False)


def next_archive_id(reg: dict) -> str:
    existing = [e["archive_id"] for e in reg.get("entries", [])]
    nums = [int(re.search(r"\d+", a).group()) for a in existing if re.search(r"\d+", a)]
    n = max(nums) + 1 if nums else 1
    return f"ARC-{n:04d}"


def make_archived_filename(metadata: dict, family_key: str, ext: str = ".xlsx") -> str:
    date_str  = (metadata.get("date_estimated") or "unknown").replace(" ", "")
    client    = re.sub(r"[^\w\s]", "", metadata.get("client", "unknown")).strip()
    client    = re.sub(r"\s+", "_", client) or "unknown"
    fam       = family_key.upper()
    rtype     = metadata.get("rfp_type", "response")
    return f"{date_str}_{client}_{fam}_{rtype}{ext}"


# ============================================================
# SECTION 4 — METADATA AUTO-DETECTION FROM FILENAME
# ============================================================
def parse_filename_metadata(filename: str) -> dict:
    """Heuristic detection of client, date, type from filename."""
    stem = Path(filename).stem
    name = re.sub(r"[_\-]+", " ", stem)

    # Year
    yr_m = re.search(r"\b(20\d{2})\b", name)
    year = yr_m.group(1) if yr_m else None

    # Quarter
    q_m = re.search(r"Q([1-4])", name, re.IGNORECASE)
    if q_m:
        quarter = f"Q{q_m.group(1)}"
    else:
        month_map = {"jan":"Q1","feb":"Q1","mar":"Q1","apr":"Q2","may":"Q2","jun":"Q2",
                     "jul":"Q3","aug":"Q3","sep":"Q3","oct":"Q4","nov":"Q4","dec":"Q4"}
        mo_m = re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b",
                         name, re.IGNORECASE)
        quarter = month_map.get(mo_m.group(1).lower()) if mo_m else None

    date_est = f"{year}-{quarter}" if (year and quarter) else (year or "")

    # Type
    nl = name.lower()
    if any(k in nl for k in ["response", "answer", "proposal"]):
        rfp_type = "response"
    elif any(k in nl for k in ["source", "request", "rfp", "rfi", "rfq", "rft"]):
        rfp_type = "source"
    else:
        rfp_type = "response"

    # Client: strip known keywords and take first words
    clean = re.sub(r"\b20\d{2}\b", "", name)
    clean = re.sub(r"\bQ[1-4]\b", "", clean, flags=re.IGNORECASE)
    for kw in ["rfp","rfi","rft","rfq","response","source","combined","wms","tms",
               "planning","logistics","scpo","catman","workforce","commerce","flexis",
               "network","doddle","aiml","blue yonder","by","question","answer",
               "proposal","request","tender","template"]:
        clean = re.sub(rf"\b{re.escape(kw)}\b", "", clean, flags=re.IGNORECASE)
    words = [w for w in clean.split() if len(w) > 2]
    client_guess = " ".join(words[:3]).strip()

    return {"client": client_guess, "date_estimated": date_est, "rfp_type": rfp_type}


# ============================================================
# SECTION 5 — STAGE 1: STRUCTURE ANALYSIS
# ============================================================
def prescan_excel(filepath: Path) -> dict:
    """Read first 20 rows of each sheet — no LLM, zero cost."""
    import openpyxl
    wb = openpyxl.load_workbook(filepath, data_only=True)
    result = {"filename": filepath.name, "sheets": []}

    for sname in wb.sheetnames:
        ws = wb[sname]
        sample_rows, merged = [], []
        for row in ws.iter_rows(max_row=20):
            row_data = {}
            for cell in row:
                if cell.value is not None:
                    row_data[cell.column_letter] = {
                        "value": str(cell.value)[:200],
                        "row": cell.row,
                    }
            if row_data:
                sample_rows.append(row_data)
        try:
            merged = [str(m) for m in ws.merged_cells.ranges]
        except Exception:
            merged = []
        result["sheets"].append({
            "name": sname,
            "total_rows": ws.max_row or 0,
            "total_cols": ws.max_column or 0,
            "sample_rows": sample_rows,
            "merged_cells": merged[:20],
        })
    wb.close()
    return result


def collect_metadata_interactive(filename: str, family_key: str) -> dict:
    """Ask Rob for file metadata. Auto-detect from filename, Rob confirms."""
    guess = parse_filename_metadata(filename)

    print(f"\n{'='*60}")
    print(f" New file: {filename}")
    print(f" Family:   {family_key.upper()}")
    print(f"{'='*60}")

    def ask(label: str, default: str, options: str = "") -> str:
        opts = f"  ({options})" if options else ""
        prompt = f"  {label} [{default}]{opts}: "
        val = input(prompt).strip()
        return val if val else default

    client   = ask("Client name", guess["client"] or "unknown")
    industry = ask("Industry",    "retail",
                   "retail/cpg/manufacturing/3pl/auto/pharma/fmcg/grocery/fashion/other")
    date_est = ask("Date (YYYY-QN)", guess["date_estimated"] or "unknown")
    region   = ask("Region", "EMEA", "EMEA/NA/APAC/LATAM")
    rtype    = ask("File type", guess["rfp_type"], "source/response/combined")
    notes    = ask("Notes (optional)", "")

    return {
        "client": client,
        "client_industry": industry,
        "date_estimated": date_est,
        "region": region,
        "rfp_type": rtype,
        "notes": notes,
    }


def analyze_structure_llm(prescan: dict, family_display: str, model: str) -> dict:
    """One LLM call to detect column structure across all sheets."""
    # Trim prescan to a compact summary for the prompt
    compact = {"filename": prescan["filename"], "sheets": []}
    for sh in prescan["sheets"]:
        rows_text = []
        for row in sh["sample_rows"][:15]:
            row_text = "  ".join(
                f"{col}:{info['value'][:80]}" for col, info in sorted(row.items())
            )
            rows_text.append(f"Row{list(row.values())[0]['row']}: {row_text}")
        compact["sheets"].append({
            "name": sh["name"],
            "total_rows": sh["total_rows"],
            "merged_cells": sh["merged_cells"][:5],
            "sample": rows_text,
        })

    prompt = f"""Analyze this RFP Excel file structure for Blue Yonder {family_display}.

Metadata and sample rows:
{json.dumps(compact, indent=2)}

This file may contain: client questions, BY answers, category headers,
requirement IDs, compliance indicators (Y/N/Partial), comments.

Return ONLY a JSON object:
{{
  "relevant_sheets": [
    {{
      "sheet_name": "...",
      "purpose": "questions_and_answers" | "questions_only" | "answers_only" | "metadata" | "skip",
      "data_start_row": <int>,
      "columns": {{
        "question_id":  "<col_letter_or_null>",
        "category":     "<col_letter_or_null>",
        "question":     "<col_letter>",
        "answer":       "<col_letter_or_null>",
        "compliance":   "<col_letter_or_null>",
        "comments":     "<col_letter_or_null>"
      }},
      "notes": "..."
    }}
  ],
  "file_type":           "source_rfp" | "response" | "combined" | "unknown",
  "estimated_questions": <int>
}}

Rules:
- Include only sheets that have RFP content (skip cover pages, TOC, scoring sheets)
- "question" column must always be provided if the sheet is relevant
- "answer" column is null for source_rfp (questions only)
- Return ONLY valid JSON, no other text"""

    return call_llm_json(prompt, model)


def confirm_structure(structure: dict, filename: str) -> bool:
    """Print LLM structure analysis, ask Rob to confirm."""
    print(f"\n--- Structure Analysis: {filename} ---")
    sheets = structure.get("relevant_sheets", [])
    if not sheets:
        print("  No relevant sheets detected.")
        return False

    for sh in sheets:
        if sh.get("purpose") == "skip":
            print(f"  [SKIP] {sh['sheet_name']}")
            continue
        cols = sh.get("columns", {})
        q_col = cols.get("question", "?")
        a_col = cols.get("answer", "-")
        cat_col = cols.get("category", "-")
        print(f"  Sheet '{sh['sheet_name']}' ({sh.get('purpose','?')}):")
        print(f"    Q={q_col}  A={a_col}  Cat={cat_col}  Start row={sh.get('data_start_row','?')}")
        if sh.get("notes"):
            print(f"    Note: {sh['notes']}")

    est = structure.get("estimated_questions", "?")
    print(f"\n  Estimated Q/A pairs: {est}")
    print(f"  File type: {structure.get('file_type', 'unknown')}")

    ans = input("\n  Proceed with extraction? [Y/n]: ").strip().lower()
    return ans in ("", "y", "yes")


# ============================================================
# SECTION 6 — STAGE 2: EXTRACTION
# ============================================================
def _cell_val(row_tuple: tuple, col_letter: Optional[str]) -> str:
    """Get value from a row tuple by 1-based column index (A=1, B=2…)."""
    if not col_letter:
        return ""
    idx = ord(col_letter.upper()) - ord("A")
    if 0 <= idx < len(row_tuple):
        v = row_tuple[idx]
        return str(v).strip() if v is not None else ""
    return ""


def extract_pairs_from_workbook(filepath: Path, structure: dict) -> list[dict]:
    """Use detected structure to extract Q/A pairs from the workbook."""
    import openpyxl
    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    all_pairs = []
    current_category = ""

    for sh_map in structure.get("relevant_sheets", []):
        if sh_map.get("purpose") == "skip":
            continue
        sname = sh_map["sheet_name"]
        if sname not in wb.sheetnames:
            continue

        ws   = wb[sname]
        cols = sh_map.get("columns", {})
        q_col   = cols.get("question")
        a_col   = cols.get("answer")
        cat_col = cols.get("category")
        start   = max(int(sh_map.get("data_start_row", 2)), 2)

        for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if row_idx < start:
                continue

            q_text   = _cell_val(row, q_col)
            a_text   = _cell_val(row, a_col)
            cat_text = _cell_val(row, cat_col)

            if not q_text:
                continue

            # Category header row: short text in Q col, no A
            if len(q_text) < 60 and not a_text:
                current_category = q_text
                continue

            # Skip very short non-question rows
            if len(q_text) < 20:
                continue

            all_pairs.append({
                "question": q_text,
                "answer":   a_text,
                "category_hint": cat_text or current_category,
                "source_sheet": sname,
                "source_row":   row_idx,
                "source_file":  filepath.name,
            })

    wb.close()
    return all_pairs


# ============================================================
# SECTION 7 — STAGE 3: CLASSIFICATION
# ============================================================
def classify_pairs_batch(pairs: list[dict], family: dict, model: str) -> list[dict]:
    """LLM classification in batches of 15."""
    BATCH = 15
    sol_codes = ", ".join(family["solution_codes"])
    fname     = family["display_name"]
    classified = []

    for i in range(0, len(pairs), BATCH):
        batch = pairs[i : i + BATCH]
        items = ""
        for j, p in enumerate(batch):
            q = p["question"][:250]
            a = (p["answer"] or "")[:150]
            items += f'\n[{j}] Q: {q}\n    A: {a}\n'

        prompt = f"""Classify Q&A pairs from a {fname} RFP.
Available solution codes: {sol_codes}

Return a JSON ARRAY (one object per pair, same order):
[{{
  "idx": <int>,
  "category": "functional|technical|security|deployment|commercial|general",
  "subcategory": "<snake_case>",
  "tags": ["tag1","tag2","tag3"],
  "solution_codes": [<applicable codes or empty for all>],
  "question_variants": ["alt phrasing 1","alt phrasing 2"],
  "scope": "platform|product_specific",
  "confidence": "draft|verified",
  "question_quality": "good|duplicate|too_vague|not_a_question",
  "answer_quality": "good|incomplete|generic|empty"
}}]

Q&A pairs:
{items}

Return ONLY valid JSON array."""

        try:
            result = call_llm_json(prompt, model)
            if isinstance(result, list):
                for item in result:
                    idx = item.get("idx", -1)
                    if 0 <= idx < len(batch):
                        batch[idx].update({
                            "category":          item.get("category", "functional"),
                            "subcategory":       item.get("subcategory", ""),
                            "tags":              item.get("tags", []),
                            "solution_codes":    item.get("solution_codes", []),
                            "question_variants": item.get("question_variants", []),
                            "scope":             item.get("scope", "product_specific"),
                            "confidence":        item.get("confidence", "draft"),
                            "question_quality":  item.get("question_quality", "good"),
                            "answer_quality":    item.get("answer_quality", "good"),
                        })
        except Exception as e:
            print(f"  [WARN] Classify batch {i//BATCH+1} failed: {e}")
            for p in batch:
                p.setdefault("category", "functional")
                p.setdefault("subcategory", "")
                p.setdefault("tags", [])
                p.setdefault("solution_codes", [])
                p.setdefault("question_variants", [])
                p.setdefault("scope", "product_specific")
                p.setdefault("confidence", "draft")
                p.setdefault("question_quality", "good")
                p.setdefault("answer_quality", "good")

        classified.extend(batch)
        print(f"  Classified {min(i+BATCH, len(pairs))}/{len(pairs)}...", end="\r")

    print()
    return classified


# ============================================================
# SECTION 8 — STAGE 3: INTERACTIVE REVIEW (CREATE mode)
# ============================================================
def _wrap(text: str, indent: int = 4) -> str:
    prefix = " " * indent
    return textwrap.fill(text, width=WRAP_W, initial_indent=prefix,
                         subsequent_indent=prefix)


def _print_review_card(pair: dict, idx: int, total: int, mode: str = "create") -> None:
    print(f"\n{'='*60}")
    print(f" {mode.upper()} | {pair.get('source_file','?')} | {idx+1}/{total}")
    print(f" Cat: {pair.get('category','?')} > {pair.get('subcategory','?')}")
    print(f"{'='*60}")
    print()
    print("Q:", )
    print(_wrap(pair["question"]))
    print()
    if pair.get("answer"):
        print("A:")
        print(_wrap(pair["answer"][:400]))
    else:
        print("A: (no answer)")
    print()
    tags = ", ".join(pair.get("tags", []))
    sols = ", ".join(pair.get("solution_codes", [])) or "all"
    qq   = pair.get("question_quality", "?")
    aq   = pair.get("answer_quality", "?")
    print(f"  Tags: {tags}")
    print(f"  Solutions: {sols}")
    print(f"  Quality: Q={qq}  A={aq}")
    print()
    print("-" * 60)
    if mode == "create":
        print("  [Y/Enter] Accept  [N] Skip  [E] Edit")
        print("  [A] Accept all   [S] Stats  [Q] Quit & save")
    else:
        print("  [Y/Enter] Accept as new  [N] Skip  [E] Edit")
        print("  [S] Stats  [Q] Quit & save")
    print("-" * 60)


def _edit_pair(pair: dict) -> dict:
    """Interactive edit of a pair's key fields."""
    print("\n  -- EDIT MODE --")

    def edit_field(label: str, current: str) -> str:
        preview = current[:80] + ("..." if len(current) > 80 else "")
        print(f"  {label} [{preview}]")
        val = input("  New value (Enter to keep): ").strip()
        return val if val else current

    pair = dict(pair)  # copy
    pair["question"] = edit_field("Question", pair["question"])
    pair["answer"]   = edit_field("Answer",   pair.get("answer", ""))

    tag_str = input(f"  Tags [{', '.join(pair.get('tags',[]))}] (Enter to keep): ").strip()
    if tag_str:
        pair["tags"] = [t.strip() for t in tag_str.split(",") if t.strip()]

    return pair


def _print_stats(accepted: int, skipped: int, total: int) -> None:
    print(f"\n  -- Stats --")
    print(f"  Accepted: {accepted}  |  Skipped: {skipped}  |  Remaining: {total - accepted - skipped}")


def session_path(family_key: str) -> Path:
    STAGING_DIR.mkdir(exist_ok=True)
    return STAGING_DIR / f"{family_key}_session.json"


def save_session(family_key: str, filename: str, reviewed: list, next_idx: int,
                 metadata: dict, structure: dict) -> None:
    data = {
        "family": family_key,
        "file": filename,
        "metadata": metadata,
        "structure": structure,
        "reviewed": reviewed,
        "next_index": next_idx,
        "timestamp": NOW_ISO,
    }
    with open(session_path(family_key), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"\n  [SAVED] Session saved. Resume with: --family {family_key} --resume")


def load_session(family_key: str) -> Optional[dict]:
    p = session_path(family_key)
    if not p.exists():
        return None
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def interactive_review_create(classified: list[dict], family_key: str,
                               filename: str, metadata: dict, structure: dict,
                               start_idx: int = 0,
                               prior_reviewed: Optional[list] = None) -> list[dict]:
    """Review loop for CREATE mode. Returns list of accepted pairs."""
    accepted  = []
    skipped   = 0
    reviewed  = prior_reviewed or []
    total     = len(classified)

    # If resuming, already-reviewed pairs contribute to accepted count
    for r in reviewed:
        if r.get("decision") == "accept":
            accepted.append(r["pair"])

    idx = start_idx
    while idx < total:
        pair = classified[idx]

        # Auto-skip clearly bad quality
        if pair.get("question_quality") == "not_a_question":
            idx += 1
            skipped += 1
            continue

        _print_review_card(pair, idx, total)
        try:
            choice = input("> ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            choice = "Q"

        if choice in ("Y", ""):
            accepted.append(pair)
            reviewed.append({"index": idx, "decision": "accept", "pair": pair})
            idx += 1

        elif choice == "N":
            reviewed.append({"index": idx, "decision": "skip"})
            skipped += 1
            idx += 1

        elif choice == "E":
            pair = _edit_pair(pair)
            accepted.append(pair)
            reviewed.append({"index": idx, "decision": "accept", "pair": pair})
            idx += 1

        elif choice == "A":
            # Accept all remaining
            for rem in classified[idx:]:
                if rem.get("question_quality") != "not_a_question":
                    accepted.append(rem)
            print(f"\n  [INFO] Accepted all remaining {total-idx} pairs.")
            idx = total

        elif choice == "S":
            _print_stats(len(accepted), skipped, total)

        elif choice == "Q":
            save_session(family_key, filename, reviewed, idx, metadata, structure)
            return accepted  # partial result — caller checks session

    return accepted


# ============================================================
# SECTION 9 — IMPROVE MODE (SIMILARITY)
# ============================================================
def embed_texts(texts: list[str]) -> list:
    from chromadb.utils import embedding_functions
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="BAAI/bge-large-en-v1.5"
    )
    return ef(texts)


def cosine_sim(a: list, b: list) -> float:
    import math
    dot   = sum(x*y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x*x for x in a))
    mag_b = math.sqrt(sum(y*y for y in b))
    return dot / (mag_a * mag_b) if mag_a and mag_b else 0.0


def find_best_match(q_emb: list, embs: list, threshold: float = 0.80
                    ) -> tuple[int, float]:
    best_i, best_s = -1, 0.0
    for i, emb in enumerate(embs):
        s = cosine_sim(q_emb, emb)
        if s > best_s:
            best_s, best_i = s, i
    return (best_i, best_s) if best_s >= threshold else (-1, best_s)


def interactive_review_improve(classified: list[dict], existing: list[dict],
                                existing_embs: list, family_key: str,
                                filename: str, metadata: dict, structure: dict,
                                start_idx: int = 0) -> tuple[list, list]:
    """IMPROVE mode review. Returns (new_accepted, improvements)."""
    print(f"\n[INFO] Embedding {len(classified)} historical questions...")
    hist_embs  = embed_texts([p["question"] for p in classified])
    new_entries, improvements = [], []
    total = len(classified)

    for idx, (pair, h_emb) in enumerate(zip(classified[start_idx:], hist_embs[start_idx:]),
                                         start=start_idx):
        best_i, best_s = find_best_match(h_emb, existing_embs)

        if best_i == -1:
            # New — show as regular create-mode card
            _print_review_card(pair, idx, total, mode="improve-new")
            try:
                choice = input("> ").strip().upper()
            except (EOFError, KeyboardInterrupt):
                save_session(family_key, filename, [], idx, metadata, structure)
                return new_entries, improvements

            if choice in ("Y", ""):
                new_entries.append(pair)
            elif choice == "E":
                new_entries.append(_edit_pair(pair))
            elif choice == "Q":
                save_session(family_key, filename, [], idx, metadata, structure)
                return new_entries, improvements
            # N / other = skip

        else:
            # Match found — compare
            exist = existing[best_i]
            print(f"\n{'='*60}")
            print(f" IMPROVE | {idx+1}/{total} | Match similarity: {best_s:.2f}")
            print(f"{'='*60}")
            print("\n Q (historical):")
            print(_wrap(pair["question"]))
            print("\n NEW answer:")
            print(_wrap((pair.get("answer") or "")[:400]))
            print(f"\n EXISTING ({exist.get('kb_id','?')}):")
            print(_wrap(exist.get("canonical_question","")[:120]))
            print(_wrap((exist.get("canonical_answer",""))[:400]))
            print()
            print("-"*60)
            print("  [K] Keep existing  [R] Replace existing")
            print("  [M] Merge (edit)   [N] Not a match (add as new)")
            print("  [Q] Quit & save")
            print("-"*60)

            try:
                choice = input("> ").strip().upper()
            except (EOFError, KeyboardInterrupt):
                choice = "Q"

            if choice == "K":
                pass  # keep existing, no action
            elif choice == "R":
                improvements.append({
                    "action": "replace",
                    "existing_kb_id": exist.get("kb_id"),
                    "similarity": round(best_s, 4),
                    "new_answer": pair.get("answer", ""),
                    "source": pair.get("source_file", ""),
                })
            elif choice == "M":
                pair = _edit_pair(pair)
                improvements.append({
                    "action": "merge",
                    "existing_kb_id": exist.get("kb_id"),
                    "similarity": round(best_s, 4),
                    "merged_answer": pair.get("answer", ""),
                    "source": pair.get("source_file", ""),
                })
            elif choice == "N":
                new_entries.append(pair)
            elif choice == "Q":
                save_session(family_key, filename, [], idx, metadata, structure)
                return new_entries, improvements

    return new_entries, improvements


# ============================================================
# SECTION 10 — ENTRY BUILDER
# ============================================================
def _question_type(q: str) -> str:
    q = q.strip().lower()
    if q.startswith("what") or q.startswith("which"):  return "WHAT"
    if q.startswith("how"):                             return "HOW"
    if q.startswith("can") or "possible" in q[:30]:    return "CAN"
    if q.startswith("does") or q.startswith("do "):    return "DOES"
    if q.startswith("is ") or q.startswith("are "):    return "IS"
    if q.startswith("why"):                             return "WHY"
    if q.startswith("where"):                           return "WHERE"
    if q.startswith("when"):                            return "WHEN"
    return "WHAT"


def build_search_blob(e: dict) -> str:
    parts = [
        f"DOMAIN: {e['domain']} | SCOPE: {e['scope']}",
        f"|| CAT: {e['category']} / {e['subcategory']}",
        f"|| TAGS: {', '.join(e.get('tags',[]))}",
        f"|| Q: {e['canonical_question']}",
    ]
    variants = e.get("question_variants", [])
    if variants:
        parts.append(f"|| VARIANTS: {' | '.join(variants)}")
    parts.append(f"|| A: {e['canonical_answer'][:300]}")
    return " ".join(parts)


def build_v2_entry(pair: dict, family_key: str, family: dict,
                   archive_id: str, seq: int) -> dict:
    cat    = pair.get("category", "functional")
    code   = CATEGORY_CODES.get(cat, "GEN")
    kb_id  = f"{family['id_prefix']}-{code}-{seq:04d}"
    entry  = {
        "kb_id":              kb_id,
        "id":                 kb_id,
        "domain":             family_key,
        "family_code":        family_key,
        "scope":              pair.get("scope", "product_specific"),
        "category":           cat,
        "subcategory":        pair.get("subcategory", ""),
        "canonical_question": pair["question"],
        "question_variants":  pair.get("question_variants", []),
        "canonical_answer":   pair.get("answer", ""),
        "solution_codes":     pair.get("solution_codes", []),
        "tags":               pair.get("tags", []),
        "confidence":         pair.get("confidence", "draft"),
        "source_rfps":        [archive_id],
        "cloud_native_only":  family.get("cloud_native", True),
        "notes":              "",
        "versioning": {
            "valid_from": None, "valid_until": None,
            "deprecated": False, "superseded_by": None, "version_notes": [],
        },
        "rich_metadata": {
            "keywords":       pair.get("tags", []),
            "question_type":  _question_type(pair["question"]),
            "source_type":    "rfp_historical",
            "source_id":      pair.get("source_file", ""),
            "scope_confidence": 0.7,
            "auto_classified": True,
        },
        "last_updated":  TODAY,
        "created_date":  TODAY,
    }
    entry["search_blob"] = build_search_blob(entry)
    return entry


# ============================================================
# SECTION 11 — ARCHIVE
# ============================================================
def _cat_counts(pairs: list[dict]) -> dict:
    cats = {}
    for p in pairs:
        c = p.get("category", "general")
        cats[c] = cats.get(c, 0) + 1
    return cats


def archive_file(proc_path: Path, archived_name: str, metadata: dict,
                 structure: dict, all_pairs: list[dict], accepted: list[dict],
                 archive_id: str, family_key: str) -> None:
    """Move processed file and outputs to archive."""
    ARCHIVE_DIR.mkdir(exist_ok=True)
    (ARCHIVE_DIR / "files").mkdir(exist_ok=True)
    (ARCHIVE_DIR / "extractions").mkdir(exist_ok=True)

    stem = Path(archived_name).stem

    # Move original file
    dest_file = ARCHIVE_DIR / "files" / archived_name
    shutil.move(str(proc_path), str(dest_file))

    # Save structure JSON
    struct_path = ARCHIVE_DIR / "extractions" / f"{stem}_structure.json"
    with open(struct_path, "w", encoding="utf-8") as f:
        json.dump(structure, f, indent=2, ensure_ascii=False)

    # Save extraction JSONL
    ext_path = ARCHIVE_DIR / "extractions" / f"{stem}_extracted.jsonl"
    with open(ext_path, "w", encoding="utf-8") as f:
        for p in all_pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    # Update registry
    reg = load_registry()
    n_sheets = sum(1 for sh in structure.get("relevant_sheets", [])
                   if sh.get("purpose") != "skip")
    cat_counts = _cat_counts(accepted)
    entry = {
        "archive_id":        archive_id,
        "original_filename": proc_path.name,
        "archived_filename": archived_name,
        "client":            metadata.get("client", ""),
        "client_industry":   metadata.get("client_industry", ""),
        "family_code":       family_key,
        "solution_codes":    [],  # populated later from accepted entries
        "rfp_type":          metadata.get("rfp_type", "response"),
        "date_estimated":    metadata.get("date_estimated", ""),
        "date_processed":    TODAY,
        "region":            metadata.get("region", ""),
        "extraction_stats": {
            "total_sheets":       len(structure.get("relevant_sheets", [])),
            "sheets_processed":   n_sheets,
            "total_qa_extracted": len(all_pairs),
            "accepted":           len(accepted),
            "skipped":            len(all_pairs) - len(accepted),
            "categories":         cat_counts,
        },
        "canonical_entries_added": len(accepted),
        "structure_file":    f"extractions/{stem}_structure.json",
        "extraction_file":   f"extractions/{stem}_extracted.jsonl",
        "notes":             metadata.get("notes", ""),
        "tags":              [],
    }
    reg["entries"].append(entry)
    reg["total_files"]        = len(reg["entries"])
    reg["total_qa_extracted"] = sum(
        e["extraction_stats"]["accepted"] for e in reg["entries"]
    )
    save_registry(reg)
    print(f"[OK] Archived -> {archived_name}  (registry: {archive_id})")


# ============================================================
# SECTION 12 — MAIN FILE FLOW
# ============================================================
def get_inbox_files(family_key: str, specific: Optional[str]) -> list[Path]:
    if specific:
        p = Path(specific)
        return [p] if p.exists() else []
    inbox = HISTORICAL_DIR / family_key / "inbox"
    files = list(inbox.glob("*.xlsx")) + list(inbox.glob("*.xls"))
    return sorted(f for f in files if not f.name.startswith("~$"))


def process_file(filepath: Path, family_key: str, family: dict,
                 model: str, is_improve: bool,
                 existing: list, existing_embs: list,
                 resume_session: Optional[dict]) -> Optional[dict]:
    """
    Full pipeline for one file. Returns dict with result stats, or None on skip/error.
    """
    proc_dir = HISTORICAL_DIR / family_key / "processing"
    proc_dir.mkdir(exist_ok=True)

    # --- Resume? ---
    if resume_session and resume_session.get("file") == filepath.name:
        print(f"\n[RESUME] Resuming {filepath.name} from index {resume_session['next_index']}")
        metadata  = resume_session["metadata"]
        structure = resume_session["structure"]
        proc_path = proc_dir / filepath.name
        if not proc_path.exists():
            shutil.copy(str(filepath), str(proc_path))
        start_idx      = resume_session["next_index"]
        prior_reviewed = resume_session.get("reviewed", [])
    else:
        # STAGE 1a — prescan
        print(f"\n[STAGE 1] Scanning: {filepath.name}")
        try:
            prescan = prescan_excel(filepath)
        except Exception as e:
            print(f"  [ERROR] Cannot read file: {e}")
            return None

        # STAGE 1b — collect metadata interactively
        metadata = collect_metadata_interactive(filepath.name, family_key)

        # STAGE 1c — LLM structure analysis
        print(f"\n[INFO] Analyzing structure with {model.upper()}...")
        try:
            structure = analyze_structure_llm(prescan, family["display_name"], model)
        except Exception as e:
            print(f"  [ERROR] Structure analysis failed: {e}")
            return None

        # STAGE 1d — confirm
        if not confirm_structure(structure, filepath.name):
            print(f"  [SKIP] {filepath.name}")
            return None

        # Move to processing
        proc_path = proc_dir / filepath.name
        shutil.copy(str(filepath), str(proc_path))
        start_idx      = 0
        prior_reviewed = []

    # STAGE 2 — extract
    print(f"\n[STAGE 2] Extracting Q/A pairs...")
    try:
        all_pairs = extract_pairs_from_workbook(proc_path, structure)
    except Exception as e:
        print(f"  [ERROR] Extraction failed: {e}")
        shutil.copy(str(proc_path), str(filepath))
        proc_path.unlink(missing_ok=True)
        return None

    with_ans  = sum(1 for p in all_pairs if p.get("answer"))
    print(f"  Extracted {len(all_pairs)} pairs  ({with_ans} with answers)")

    if not all_pairs:
        print("  [WARN] No pairs extracted. Check structure detection.")
        return None

    # STAGE 3a — classify
    print(f"\n[STAGE 3] Classifying with {model.upper()}...")
    classified = classify_pairs_batch(all_pairs, family, model)

    # STAGE 3b — interactive review
    print(f"\n[STAGE 3] Starting interactive review ({len(classified)} pairs)...")
    if is_improve:
        accepted, improvements = interactive_review_improve(
            classified, existing, existing_embs,
            family_key, filepath.name, metadata, structure, start_idx
        )
    else:
        accepted = interactive_review_create(
            classified, family_key, filepath.name, metadata, structure,
            start_idx, prior_reviewed
        )
        improvements = []

    if not accepted:
        print("  [INFO] No pairs accepted.")
        return None

    print(f"\n  Accepted {len(accepted)} pairs.")

    # Build canonical entries
    existing_canon = load_canonical(family["canonical_file"])
    seq_start = next_seq(existing_canon, family["id_prefix"])
    reg = load_registry()
    arc_id = next_archive_id(reg)

    new_entries = [
        build_v2_entry(p, family_key, family, arc_id, seq_start + i)
        for i, p in enumerate(accepted)
    ]

    # Append to canonical
    STAGING_DIR.mkdir(exist_ok=True)
    combined = existing_canon + new_entries
    save_canonical(family["canonical_file"], combined)

    # Save improvements to staging (IMPROVE mode)
    if improvements:
        imp_path = STAGING_DIR / f"{family_key}_improvements.jsonl"
        with open(imp_path, "a", encoding="utf-8") as f:
            for item in improvements:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
        print(f"  {len(improvements)} improvements -> {imp_path.name}")

    # Archive
    archived_name = make_archived_filename(metadata, family_key, filepath.suffix)
    archive_file(proc_path, archived_name, metadata, structure,
                 all_pairs, accepted, arc_id, family_key)

    # Clean up inbox (original file)
    filepath.unlink(missing_ok=True)

    # Clean session file if exists
    sp = session_path(family_key)
    sp.unlink(missing_ok=True)

    return {"accepted": len(accepted), "archive_id": arc_id}


def run_family(family_key: str, model: str, resume: bool,
               specific_file: Optional[str]) -> None:
    family     = get_family(family_key)
    is_improve = family.get("phase", 1) == 2

    print(f"\n[START] Family: {family['display_name']}  Model: {model}")
    print(f"        Mode: {'IMPROVE' if is_improve else 'CREATE'}")

    # Load session for resume
    resume_session = load_session(family_key) if resume else None
    if resume and not resume_session:
        print(f"[WARN] No saved session for '{family_key}'.")

    # For IMPROVE mode: pre-embed existing canonical
    existing, existing_embs = [], []
    if is_improve:
        existing = load_canonical(family["canonical_file"])
        if existing:
            print(f"[INFO] Pre-embedding {len(existing)} existing entries (takes ~30s)...")
            existing_embs = embed_texts([e.get("canonical_question","") for e in existing])
            print(f"[OK]  Embeddings ready.")

    # Get files to process
    if specific_file:
        files = [Path(specific_file)]
    elif resume_session:
        f = HISTORICAL_DIR / family_key / "processing" / resume_session["file"]
        if not f.exists():
            f = HISTORICAL_DIR / family_key / "inbox" / resume_session["file"]
        files = [f] if f.exists() else []
    else:
        files = get_inbox_files(family_key, None)

    if not files:
        print(f"\n[INFO] No files found in historical/{family_key}/inbox/")
        print(f"       Drop .xlsx files there and re-run.")
        return

    print(f"[INFO] {len(files)} file(s) to process")

    total_accepted = 0
    for f in files:
        result = process_file(
            f, family_key, family, model, is_improve,
            existing, existing_embs, resume_session
        )
        if result:
            total_accepted += result["accepted"]
            resume_session = None  # don't reuse session for next file

    if total_accepted:
        print(f"\n[DONE] Total new entries added: {total_accepted}")
        print(f"       Next step:")
        print(f"         python src/kb_merge_canonical.py")
        print(f"         python src/kb_embed_chroma.py")


# ============================================================
# SECTION 13 — ENTRY POINT
# ============================================================
def main() -> None:
    import argparse
    FAMILIES = ["planning","wms","logistics","scpo","catman","workforce",
                "commerce","flexis","network","doddle","aiml"]

    p = argparse.ArgumentParser(
        description="3-Stage RFP Extraction Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python src/kb_extract_historical.py --family wms
  python src/kb_extract_historical.py --family wms --model gemini-flash
  python src/kb_extract_historical.py --family planning               # auto IMPROVE
  python src/kb_extract_historical.py --family wms --resume
  python src/kb_extract_historical.py --family wms --file "path/to.xlsx"
        """,
    )
    p.add_argument("--family",   required=True, choices=FAMILIES)
    p.add_argument("--model",    default="gemini-flash",
                   help="LLM model key from llm_router (default: gemini-flash)")
    p.add_argument("--resume",   action="store_true",
                   help="Resume interrupted review session")
    p.add_argument("--file",     default=None,
                   help="Process a specific Excel file instead of scanning inbox/")
    args = p.parse_args()

    run_family(args.family, args.model, args.resume, args.file)


if __name__ == "__main__":
    main()
