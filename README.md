# Corp RFP Agent

AI-powered RFP answer engine for Blue Yonder pre-sales. Ingests product knowledge from multiple sources, builds a searchable knowledge base with quality scoring, and generates KB-grounded answers for RFP questions in Excel and Word format. Includes a human-in-the-loop feedback system with bilingual (Polish/English) support and automated quality evaluation across 18 product families.

---

## Architecture Overview

The system operates through three continuous learning loops:

```
                    LOOP 1: Knowledge Ingestion
                    ===========================
  CKE PDFs --> generate_product_profiles.py --> product profiles
  Historical RFPs --> kb_extract_historical.py --> Q&A pairs
  Architecture facts --> kb_ingest.py --> draft entries
                              |
                              v
                    data/kb/drafts/{family}/
                              |
                              v
                    LOOP 2: Human Feedback
                    ======================
  generate_review_pack.py --> Excel review pack --> Rob reviews
                              |
  import_review_pack.py  <----+  (bilingual PL/EN feedback)
        |
        +---> APPROVE --> data/kb/verified/
        +---> UPDATE  --> LLM or direct correction
        +---> REJECT  --> data/kb/rejected/
        +---> NEW     --> create draft from Rob's answer
                              |
                              v
                    LOOP 3: Self-Evaluation
                    =======================
  kb_quality_scorer.py --> 5-dimension LLM scoring
  kb_eval.py           --> deterministic health checks
  kb_simulate_rfp.py   --> simulated RFP test suite
  auto-promote         --> drafts graduate to verified (6 criteria)
                              |
                              v
                    kb_index_sync.py --> ChromaDB
                              |
                              v
                    RFP Answering (Excel / Word)
```

---

## Quick Start

```bash
pip install -r requirements.txt
```

Create `.env` with at least `GEMINI_API_KEY=...` (other providers optional).

### Answer an RFP

```bash
# Excel (green-cell detection)
python src/rfp_excel_agent.py --input "RFP.xlsx" --client acme --solution planning --model gemini

# Word document
python src/rfp_answer_word.py --input "RFP.docx" --solution wms --model gemini
```

### Ingest New Knowledge

```bash
# From architecture/CKE facts (all families)
python src/kb_ingest.py --all --source architecture --dry-run
python src/kb_ingest.py --family wms --source architecture

# From historical RFP Excel files
python src/kb_extract_historical.py --family wms
```

### Review and Give Feedback

```bash
# Generate branded Excel review pack
python src/generate_review_pack.py --family wms --count 40

# Import reviewed pack (dry-run first, then apply)
python src/import_review_pack.py path/to/PACK.xlsx --dry-run
python src/import_review_pack.py path/to/PACK.xlsx --apply

# Manual feedback CLI
python src/rfp_feedback.py correct KB_DRAFT_0001 "Add REST API details"
python src/rfp_feedback.py approve KB_DRAFT_0001
```

### Check KB Health

```bash
# Deterministic health report
python src/kb_eval.py --family wms

# LLM quality scoring (5 dimensions, batch API)
python src/kb_quality_scorer.py --scope drafts --batch

# Simulated RFP test
python src/kb_simulate_rfp.py --family wms --count 20

# Auto-promote qualifying drafts
python src/rfp_feedback.py auto-promote --dry-run
```

---

## Project Structure

```
corp-rfp-agent/
|-- src/                              # All scripts (see Key Commands below)
|   |-- corp_rfp_agent/               # v2 modular package (agents, KB, overrides)
|   +-- anonymization/                # Anonymization package
|-- config/
|   |-- product_profiles/             # Per-family YAML profiles
|   |   |-- _generated/               #   LLM-generated from CKE
|   |   |-- _overrides/               #   Manual corrections
|   |   +-- _effective/               #   Merged (generated + overrides)
|   |-- anonymization.yaml            # Blocklist config
|   +-- platform_matrix.json          # Platform services per solution
|-- data/kb/
|   |-- verified/{family}/            # Production-ready entries
|   |-- drafts/{family}/              # Pending review
|   |-- rejected/                     # Rejected entries
|   |-- archive/                      # Processed historical RFPs
|   |-- review_packs/                 # Generated Excel review packs
|   |-- schema/                       # Entry schema + family config
|   +-- chroma_store/                 # ChromaDB vector index
|-- tests/                            # 631 tests
|-- prompts/                          # LLM system prompts
+-- docs/                             # Documentation
```

---

## Key Commands Reference

### RFP Answering

| Command | Description |
|---------|-------------|
| `python src/rfp_excel_agent.py --input RFP.xlsx --client NAME --solution CODE` | Answer Excel RFP (green-cell detection) |
| `python src/rfp_answer_word.py --input RFP.docx --solution CODE --model MODEL` | Answer Word RFP (section detection) |

### Knowledge Management

| Command | Description |
|---------|-------------|
| `python src/kb_ingest.py --family FAMILY --source architecture` | Ingest CKE architecture facts as drafts |
| `python src/kb_ingest.py --all --source architecture --dry-run` | Preview ingestion for all 18 families |
| `python src/kb_extract_historical.py --family FAMILY` | 3-stage extraction from historical RFP Excel |
| `python src/kb_dedup.py --threshold 0.85 --dry-run` | Semantic dedup using BGE embeddings |
| `python src/kb_reclassify.py --family FAMILY` | Migrate categories to 4 RFP response teams |
| `python src/kb_index_sync.py` | Incremental ChromaDB sync |
| `python src/kb_index_sync.py --force` | Full Blue/Green rebuild |
| `python src/kb_merge_canonical.py` | Merge domain KBs into unified file |

### Feedback and Review

| Command | Description |
|---------|-------------|
| `python src/generate_review_pack.py --family FAMILY` | Generate branded Excel with questions + RAG answers |
| `python src/generate_review_pack.py --all` | Generate packs for all active families |
| `python src/import_review_pack.py PACK.xlsx --dry-run` | Preview import of reviewed Excel feedback |
| `python src/import_review_pack.py PACK.xlsx --apply` | Apply feedback (approve/update/reject/new) |
| `python src/rfp_feedback.py correct ENTRY_ID "feedback"` | LLM-assisted correction |
| `python src/rfp_feedback.py correct-offline ENTRY_ID "new answer"` | Direct answer replacement |
| `python src/rfp_feedback.py approve ENTRY_ID` | Move draft to verified |
| `python src/rfp_feedback.py reject ENTRY_ID "reason"` | Move draft to rejected |
| `python src/rfp_feedback.py auto-promote --dry-run` | Auto-promote qualifying drafts (6 criteria) |

### Quality and Health

| Command | Description |
|---------|-------------|
| `python src/kb_eval.py --family FAMILY` | Deterministic health checks (coverage, freshness, depth, consistency) |
| `python src/kb_quality_scorer.py --scope drafts --batch` | LLM quality scoring (5 dimensions, Batch API) |
| `python src/kb_quality_scorer.py --scope verified --sync` | Sync scoring (one call at a time) |
| `python src/kb_simulate_rfp.py --family FAMILY --count 20` | Simulated RFP test suite |
| `python src/validate_profiles.py` | Validate product profiles for contradictions |

### Product Profiles

| Command | Description |
|---------|-------------|
| `python src/generate_product_profiles.py --svc CKE_DIR --arch CKE_DIR` | Generate profiles from CKE PDFs |
| `python src/merge_profiles.py` | Merge generated + manual overrides into effective |
| `python src/validate_profiles.py --product wms --auto-fix` | Validate and auto-fix profile issues |

### Utilities

| Command | Description |
|---------|-------------|
| `python src/kb_archive_search.py --list` | List all archived historical RFPs |
| `python src/kb_archive_search.py --family wms --from 2023-Q1` | Search archive by family/date |
| `python src/excel_to_platform_matrix.py` | Convert platform usage Excel to JSON |
| `python scripts/test_api_keys.py` | Test API key connectivity |
| `python scripts/debug_rag_retrieval.py "question"` | Debug RAG retrieval |

---

## Configuration

### API Keys (`.env`)

```env
GEMINI_API_KEY=...       # Required (primary LLM + Batch API)
ANTHROPIC_API_KEY=...    # Optional: Claude
OPENAI_API_KEY=...       # Optional: GPT-5
DEEPSEEK_API_KEY=...     # Optional: DeepSeek
```

Additional providers: `ZHIPU_API_KEY`, `MOONSHOT_API_KEY`, `TOGETHER_API_KEY`, `XAI_API_KEY`.

### Product Profiles (`config/product_profiles/`)

YAML files per product family with capabilities, forbidden claims, platform services. Three layers:
- `_generated/` -- LLM-extracted from CKE documents
- `_overrides/` -- Manual corrections (takes precedence)
- `_effective/` -- Merged result (auto-generated by `merge_profiles.py`)

### Anonymization (`config/anonymization.yaml`)

Blocklist-based system that replaces customer names with `[CUSTOMER]` before LLM API calls.

---

## Data Flow

```
Source Documents                    Knowledge Base                RFP Answering
================                    ==============                =============

CKE PDFs -----> product profiles
                     |
                     v
              kb_ingest.py -------> data/kb/drafts/
                                         |
Historical RFPs --> kb_extract_ ------>  |
                    historical.py        |
                                         v
                              quality_scorer.py (LLM)
                              kb_eval.py (deterministic)
                                         |
                                         v
                              review_pack --> Rob --> import
                                         |
                              auto-promote (6 criteria)
                                         |
                                         v
                                    data/kb/verified/
                                         |
                                         v
                                    kb_index_sync.py
                                         |
                                         v
                                    ChromaDB (BGE embeddings)
                                         |
                                         v
                              rfp_excel_agent.py (Excel)
                              rfp_answer_word.py (Word)
                                         |
                                         v
                                    Answered RFP
```

---

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_import_review_pack.py -v

# Run by phase
python -m pytest tests/test_phase4/ -v
```

**631 tests** across 38 test files covering:

| Area | Tests | Files |
|------|-------|-------|
| Core types, config, entry schema | 36 | test_phase1/ |
| Overrides system | 18 | test_phase2/ |
| KB pipeline (builder, loader, archive) | 30 | test_phase3/ |
| Excel agent (v2) | 22 | test_phase4/ |
| Word agent (v2) | 21 | test_phase5/ |
| Review pack generator | 26 | test_generate_review_pack.py |
| Review pack importer | 40 | test_import_review_pack.py |
| KB utilities (dedup, reclassify, eval, etc.) | ~200 | test_kb_*.py |
| Acceptance tests (Excel, Word, router) | ~30 | test_*_acceptance.py |
| Feedback, profiles, auto-promote | ~60 | test_rfp_feedback.py, test_auto_promote.py, etc. |

---

## Dependencies

Key packages from `requirements.txt`:

| Package | Purpose |
|---------|---------|
| `google-genai` | Gemini API (primary LLM + Batch API) |
| `anthropic` | Claude API |
| `openai` | GPT-5 API |
| `chromadb` | Local vector database |
| `sentence-transformers` | BGE-large-en-v1.5 embeddings |
| `openpyxl` | Excel read/write |
| `python-docx` | Word document processing |
| `pyyaml` | Config files |
| `python-dotenv` | Environment variable management |

---

## License

Internal use only -- Blue Yonder Presales.
