# RFP Answer Engine - Project Context

> This file provides context for Claude Code. Update it after significant changes.

## Project Overview

**Purpose:** AI-powered RFP (Request for Proposal) answering system for Blue Yonder solutions.

**Core Flow:**
```
RFP Questions (Excel) → Anonymization → RAG Retrieval → LLM Generation → De-anonymization → Answers
```

## Current State (v0.2)

### Architecture
- **Embeddings:** Local BGE-large-en-v1.5 (no API cost)
- **Vector DB:** ChromaDB (local, persistent)
- **LLM Router:** Multi-provider (Gemini, Claude, GPT-5, DeepSeek, GLM, Kimi, Llama, Grok)
- **Anonymization:** YAML-based blocklist with middleware pattern

### Knowledge Base Structure
| Domain | File | Entries | Description |
|--------|------|---------|-------------|
| planning | RFP_Database_Cognitive_Planning_CANONICAL.json | 807 | Cognitive Planning Q&A (most complete) |
| aiml | RFP_Database_AIML_CANONICAL.json | 54 | AI/ML capabilities |
| wms | RFP_Database_WMS_CANONICAL.json | 38 | Warehouse Management (from video workshops) |

### KB Entry Schema
```json
{
  "kb_id": "wms_0001",
  "domain": "wms",
  "scope": "platform | product_specific",
  "category": "Integration",
  "subcategory": "APIs",
  "canonical_question": "...",
  "canonical_answer": "...",
  "versioning": {
    "valid_from": "2025.1",
    "valid_until": null,
    "deprecated": false,
    "superseded_by": null,
    "version_notes": ["2024", "2025"]
  },
  "rich_metadata": {
    "keywords": ["API", "REST"],
    "question_type": "WHAT",
    "source_type": "video_workshop",
    "source_id": "frame_123",
    "scope_confidence": 0.85,
    "auto_classified": true
  },
  "search_blob": "...",
  "last_updated": "2025-01-15",
  "created_date": "2025-01-15"
}
```

## File Structure

```
rfp-answer-engine/
├── CLAUDE.md                          # THIS FILE - project context
├── README.md                          # User documentation
│
├── config/
│   ├── anonymization.yaml             # Blocklist and session config
│   ├── platform_matrix.json           # Platform services matrix (from Excel)
│   └── product_profiles/              # Product profile system
│       ├── _generated/                # Auto from CKE + Matrix (safe to regenerate)
│       ├── _overrides/                # Manual corrections (NEVER overwritten)
│       ├── _effective/                # Merged profiles (pipeline reads ONLY here)
│       ├── _changelog.jsonl           # Every change tracked
│       └── _generation_report.json    # Audit trail
│
├── data/
│   ├── kb/
│   │   ├── raw/                       # Source files (JSONL from workshops)
│   │   │   └── knowledge_wms.jsonl
│   │   ├── canonical/                 # Transformed KB files (backward compat)
│   │   │   ├── RFP_Database_Cognitive_Planning_CANONICAL.json
│   │   │   ├── RFP_Database_AIML_CANONICAL.json
│   │   │   ├── RFP_Database_WMS_CANONICAL.json
│   │   │   └── RFP_Database_UNIFIED_CANONICAL.json  # Merged
│   │   ├── verified/                  # Production entries (1 JSON per entry)
│   │   │   ├── planning/KB_0001.json
│   │   │   ├── wms/
│   │   │   └── ...
│   │   ├── drafts/                    # Auto-generated, pending review
│   │   ├── rejected/                  # Rejected with reason (audit trail)
│   │   ├── feedback_log.jsonl         # Append-only feedback audit trail
│   │   ├── file_state.json            # Sync manifest (gitignored, machine-specific)
│   │   └── chroma_store/              # ChromaDB vector index
│   ├── input/                         # RFP input files (Excel/CSV)
│   └── output/                        # Generated RFP answers
│
├── src/                               # Core Python modules
│   ├── __init__.py
│   ├── llm_router.py                  # Multi-LLM provider router
│   ├── rfp_batch_universal.py         # Main batch processor
│   ├── rfp_excel_agent.py             # Excel agent for green-cell processing
│   ├── kb_build_canonical.py          # Build KB from raw sources
│   ├── kb_transform_knowledge.py      # Transform JSONL -> Canonical
│   ├── kb_merge_canonical.py          # Merge all KBs into unified
│   ├── kb_index_sync.py              # ChromaDB incremental sync + Blue/Green rebuild
│   ├── kb_embed_chroma.py             # [DEPRECATED] Full re-index (use kb_index_sync.py)
│   ├── batch_llm.py                  # Gemini Batch API wrapper (50% cost)
│   ├── generate_product_profiles.py  # Generate profiles from CKE + Matrix
│   ├── merge_profiles.py             # Merge generated + overrides -> effective
│   ├── validate_profiles.py          # Detect contradictions, missing data, auto-fix
│   ├── kb_migrate_to_files.py        # Migrate canonical arrays -> individual files
│   ├── kb_ingest.py                  # CKE facts -> draft KB entries (ingestion pipeline)
│   ├── rfp_feedback.py               # Feedback CLI: correct/approve/reject/retag/propagate
│   ├── excel_to_platform_matrix.py    # Convert Excel to platform_matrix.json
│   ├── solution_filter.py             # Solution filtering utilities
│   └── anonymization/                 # Anonymization package
│       ├── __init__.py
│       ├── config.py                  # YAML loader
│       ├── core.py                    # anonymize(), deanonymize()
│       ├── middleware.py              # Pipeline middleware
│       ├── scan_kb.py                 # CLI: scan KB for sensitive terms
│       └── clean_kb.py                # CLI: clean KB with backup
│
├── scripts/                           # Utility scripts
│   ├── test_api_keys.py               # Test API key connectivity
│   ├── debug_rag_retrieval.py         # Debug RAG retrieval
│   └── check_api_keys.py              # Check API key presence
│
├── prompts/                           # Prompt templates
│   ├── rfp_system_prompt_universal.txt
│   ├── platform_context.md
│   └── kb_distiller_prompt.txt
│
├── docs/                              # Documentation
│   ├── BUGFIX_NOT_IN_KB.md
│   └── KB_WORKFLOW.md
│
└── tests/                             # Test files
```

## Key Commands

```bash
# Transform new knowledge from workshops
python src/kb_transform_knowledge.py \
    --input data/kb/raw/knowledge_wms.jsonl \
    --domain wms \
    --source-type video_workshop \
    --version 2025.1

# Append more knowledge to existing KB
python src/kb_transform_knowledge.py \
    --input data/kb/raw/knowledge_wms_session2.jsonl \
    --domain wms \
    --append

# Merge all KBs
python src/kb_merge_canonical.py

# Sync ChromaDB index (incremental — only changed files)
python src/kb_index_sync.py

# See what would change without modifying ChromaDB
python src/kb_index_sync.py --dry-run

# Full safe rebuild (Blue/Green swap)
python src/kb_index_sync.py --force-rebuild

# [DEPRECATED] Full re-index (use kb_index_sync.py instead)
python src/kb_embed_chroma.py

# Reclassify KB (synchronous, default)
python src/kb_reclassify.py --model gemini-flash

# Reclassify KB (Batch API — 50% cheaper, async)
python src/kb_reclassify.py --model gemini-3-flash-preview --batch

# Extract historical (Batch API for classification)
python src/kb_extract_historical.py --family wms --batch

# Generate product profiles from CKE + platform matrix
python src/generate_product_profiles.py --svc svc.json --arch arch.json
python src/generate_product_profiles.py --product wms --dry-run

# Merge generated + overrides → effective profiles
python src/merge_profiles.py
python src/merge_profiles.py --validate

# Validate effective profiles (detect contradictions, missing data)
python src/validate_profiles.py
python src/validate_profiles.py --product wms

# Auto-fix ERROR contradictions (generates override YAMLs)
python src/validate_profiles.py --auto-fix

# Auto-fix + re-merge effective profiles
python src/validate_profiles.py --auto-fix --merge

# Migrate canonical KB arrays to individual files
python src/kb_migrate_to_files.py
python src/kb_migrate_to_files.py --dry-run
python src/kb_migrate_to_files.py --family planning

# Feedback CLI — correct, approve, reject, retag, propagate
python src/rfp_feedback.py show KB_0234
python src/rfp_feedback.py correct KB_0234 --text "Remove JSON" --dry-run
python src/rfp_feedback.py correct KB_0234 --text "Remove JSON" --apply
python src/rfp_feedback.py correct KB_0234 --text "New answer" --offline --apply
python src/rfp_feedback.py approve KB_1001
python src/rfp_feedback.py reject KB_1001 --reason "Outdated"
python src/rfp_feedback.py retag KB_0234 --product wms_native
python src/rfp_feedback.py retag KB_0234 --category functional
python src/rfp_feedback.py propagate KB_0234 --dry-run
python src/rfp_feedback.py log --last 20
python src/rfp_feedback.py search "JSON ingestion" --family planning

# KB Ingestion Pipeline — CKE facts to draft entries
python src/kb_ingest.py --family wms --source architecture --svc svc.json --arch arch.json
python src/kb_ingest.py --family wms --dry-run
python src/kb_ingest.py --family wms --batch
python src/kb_ingest.py --family wms --min-confidence 0.9
python src/kb_ingest.py --family wms --fact "WMS supports REST API"

# Generate + merge in one step
python src/generate_product_profiles.py --svc svc.json --arch arch.json --full

# Run batch processor
python src/rfp_batch_universal.py \
    --test \
    --model gemini \
    --anonymize \
    --workers 4

# Run Excel agent
python src/rfp_excel_agent.py \
    --input "RFP.xlsx" \
    --client acme \
    --solution planning \
    --model gemini

# Scan KB for sensitive terms
python -m src.anonymization.scan_kb

# Clean KB (dry run first!)
python -m src.anonymization.clean_kb --dry-run

# Test API keys
python scripts/test_api_keys.py

# Debug RAG retrieval
python scripts/debug_rag_retrieval.py "your question here"
```

## Environment Variables

```bash
GEMINI_API_KEY=...       # Google Gemini
ANTHROPIC_API_KEY=...    # Claude
OPENAI_API_KEY=...       # GPT-5
DEEPSEEK_API_KEY=...     # DeepSeek
ZHIPU_API_KEY=...        # GLM (use --workers 2)
MOONSHOT_API_KEY=...     # Kimi
TOGETHER_API_KEY=...     # Llama
XAI_API_KEY=...          # Grok
```

## Architecture Decisions

### Why local embeddings?
- Zero API cost
- Zero data exposure (embeddings never leave machine)
- BGE-large-en-v1.5 is high quality (top 10 on MTEB)

### Why YAML for anonymization config?
- Human-readable for non-technical users
- Easy to maintain blocklists
- Supports comments

### Why unified KB with domain metadata?
- RFPs often mix topics (planning + platform + AI/ML)
- Single ChromaDB collection is simpler
- Domain field enables optional filtering
- Scope field (platform/product_specific) helps with cross-solution questions

### Why versioning in KB entries?
- Product versions change (2024 → 2025 → 2026)
- Some features get deprecated
- Need to track when information becomes stale
- `valid_from`, `valid_until`, `deprecated`, `superseded_by` fields

## Scope Classification

| Scope | Description | Examples |
|-------|-------------|----------|
| `platform` | Shared across all products | SSO, API Management, Data Cloud, SLAs |
| `product_specific` | Unique to one product | Android app (WMS), Demand Sensing (Planning) |

**Auto-classification:** `kb_transform_knowledge.py` uses keyword matching with confidence scores.

## KB Expansion — 3-Stage Extraction Pipeline

### Overview

The KB currently covers Planning (807), WMS (38), AIML (54). Phase 1 expands all other families
by processing historical RFP Excel files. Phase 2 finds gaps in the Planning KB.

**Mode is auto-detected** from `family_config.json` `phase` field:
- Phase 1 families (WMS, Logistics, etc.) → **CREATE mode** — extract and build from scratch
- Phase 2 families (Planning) → **IMPROVE mode** — find gaps, flag better answers for review

### Folder Structure

```
data/kb/
├── historical/
│   └── {family}/
│       ├── inbox/       <- Drop .xlsx files here (gitignored by extension)
│       └── processing/  <- Temp while processing (gitignored)
├── archive/             <- Central archive (NOT per-family)
│   ├── archive_registry.json  <- COMMITTED (no customer data)
│   ├── files/           <- Original Excel files renamed (gitignored)
│   └── extractions/     <- Structure + extraction JSONL (gitignored)
├── staging/             <- Review queue: {family}_improvements.jsonl
├── canonical/           <- Final KB files (ChromaDB source)
└── schema/
    ├── family_config.json
    └── canonical_entry_v2.json
```

### 3-Stage Pipeline

```
Stage 1 — Structure Analysis (per file):
  a. prescan_excel(): reads first 20 rows per sheet with openpyxl (zero LLM cost)
  b. collect_metadata_interactive(): prompts for client/industry/date/region/type
     Auto-detects from filename; Rob confirms or overrides
  c. analyze_structure_llm(): one gemini-flash call detects Q/A/category columns
  d. confirm_structure(): prints layout summary, Rob confirms before proceeding

Stage 2 — Extraction:
  extract_pairs_from_workbook(): uses detected column map, handles category header
  rows, tracks source_sheet and source_row for traceability

Stage 3 — Classification + Interactive Review:
  classify_pairs_batch(): gemini-flash classifies 15 pairs per call
    (category, subcategory, tags, solution_codes, question_variants, quality)
  CREATE mode review: Y/N/E/A/S/Q for each pair
  IMPROVE mode review: BGE cosine similarity vs existing (threshold 0.80)
    - K (keep) / R (replace) / M (merge/edit) / N (add as new) for matches

File flow:
  inbox/file.xlsx -> processing/ -> canonical (appended) -> archive/ -> inbox cleaned
```

### Commands

```bash
# Check what's in inbox and what's been archived
python src/kb_stats.py

# Process all files in a family's inbox (auto-detects mode from phase)
python src/kb_extract_historical.py --family wms
python src/kb_extract_historical.py --family wms --model gemini-flash

# Process a specific file
python src/kb_extract_historical.py --family wms --file "path/to/file.xlsx"

# Planning (auto IMPROVE mode — finds gaps against existing 807 entries)
python src/kb_extract_historical.py --family planning

# Resume interrupted review session
python src/kb_extract_historical.py --family wms --resume

# Re-merge and re-index after extraction (auto-triggered by pipeline)
python src/kb_merge_canonical.py && python src/kb_index_sync.py

# Search the archive
python src/kb_archive_search.py --list
python src/kb_archive_search.py --client "Acme"
python src/kb_archive_search.py --family wms
python src/kb_archive_search.py --from 2023-Q1 --to 2024-Q4
python src/kb_archive_search.py --id ARC-0001
```

### KB Entry Schema v2

v2 is a **superset of v1** — all new fields have defaults, existing entries work unchanged.

New fields vs v1:
- `id` / `kb_id`: structured format `{PREFIX}-{CAT}-{NNNN}` e.g. `WMS-FUNC-0042`
- `family_code`: product family (for ChromaDB filtered queries)
- `question_variants`: alternative phrasings for better RAG recall
- `solution_codes`: which specific solutions within the family this applies to
- `tags`: keyword array for search boosting
- `confidence`: `verified | draft | needs_review | outdated`
- `source_rfps`: archive IDs that contributed this answer
- `cloud_native_only`: flag for SaaS-only answers
- `notes`: internal notes (not used in RAG)

Schema: `data/kb/schema/canonical_entry_v2.json`
Family config: `data/kb/schema/family_config.json`

### Archive Registry

`data/kb/archive/archive_registry.json` is committed. It records:
- `archive_id`: ARC-0001, ARC-0002, …
- `client`, `client_industry`, `family_code`, `rfp_type`, `date_estimated`, `region`
- `extraction_stats`: sheets_processed, total_qa_extracted, accepted, categories breakdown
- `structure_file` and `extraction_file`: paths within archive/

This registry is the foundation for future cross-family analysis and model training.

## Current Tasks

- [ ] Drop historical RFP Excel files into `data/kb/historical/{family}/inbox/`
- [ ] Run `kb_extract_historical.py` for Phase 1 families (WMS, Logistics, SCPO, etc.)
- [ ] Run `kb_extract_historical.py --family planning` for Phase 2 (find gaps)
- [ ] Review `staging/planning_improvements.jsonl` and apply best improvements manually
- [ ] Create `kb_deprecate.py` CLI tool for marking old entries
- [ ] Add `--solution wms|planning|catman` flag to batch processor
- [ ] Update `kb_embed_chroma.py` to filter deprecated entries

## Recent Changes

### 2026-03-12 — Knowledge Ingestion Pipeline
- **FEATURE:** `src/kb_ingest.py` — automated CKE facts to KB draft entries
  - 5-stage pipeline: Collect -> Generate Q&A -> Validate -> Dedup -> Write Drafts
  - Loads CKE architecture facts (same JSONs as product profile generation)
  - Scans project `facts.yaml` files for additional facts
  - Clusters related facts by category + keyword overlap (1 Q&A per cluster)
  - Gemini Flash for Q&A generation, Batch API support with `--batch`
  - Profile validation: rejects entries violating forbidden_claims
  - Embedding-based dedup against existing verified/ and drafts/
  - Writes to `data/kb/drafts/{family}/KB_DRAFT_XXXX.json`
  - `--dry-run`, `--min-confidence`, `--fact` (single-fact test mode)
- **TESTS:** 39 new tests in `tests/test_kb_ingest.py` (493 total)

### 2026-03-12 — Feedback CLI + KB Directory Restructure
- **FEATURE:** KB per-entry file system: `verified/`, `drafts/`, `rejected/` directories
  - `src/kb_migrate_to_files.py` — migrates canonical JSON arrays to individual files
  - Normalizes v1 (kb_id/canonical_question) and v2 (id/question) schemas
  - Canonical files preserved for backward compatibility
- **FEATURE:** `src/rfp_feedback.py` — Feedback CLI for KB management
  - `correct` — LLM-assisted answer correction (Gemini Flash) with diff preview
  - `approve` — moves drafts to verified (validates forbidden claims first)
  - `reject` — moves to rejected with reason (audit trail)
  - `retag` — change product/family or category (moves file to new dir)
  - `propagate` — find similar entries needing same fix (ChromaDB similarity)
  - `show` / `log` / `search` — inspect entries and feedback history
  - `--dry-run` default for correct and propagate (safety first)
  - Forbidden claims check on every approve and correct
  - Append-only `feedback_log.jsonl` + per-entry `feedback_history[]`
- **TESTS:** 40 new tests in `tests/test_rfp_feedback.py` (454 total)

### 2026-03-12 — Profile Validation
- **FEATURE:** `src/validate_profiles.py` — automatic profile quality checks
  - 4 rule categories: CONTRADICTIONS (field vs forbidden_claims), MISSING DATA,
    SUSPICIOUS INFERENCES (field claims not backed by key_facts), PLATFORM SERVICE CONSISTENCY
  - `--auto-fix`: generates override YAMLs for ERROR-level contradictions
  - `--auto-fix --merge`: auto-fix + re-merge effective profiles
  - `--product`: validate single product
  - ASCII table output with per-product errors/warnings/status
- **TESTS:** 32 new tests in `tests/test_validate_profiles.py` (414 total)

### 2026-03-12 — Product Profile System
- **FEATURE:** `src/generate_product_profiles.py` + `src/merge_profiles.py`
  - Auto-generates product profiles from CKE JSON extractions + platform_matrix.json
  - Layered architecture: `_generated/` (safe to regenerate) + `_overrides/` (manual corrections) + `_effective/` (pipeline reads only here)
  - Override merge: field replacement, `forbidden_claims_add/remove`, `platform_services_add/remove`
  - Platform services from matrix → convenience booleans (has_analytics, has_bdm, etc.)
  - CKE fact inference: cloud_native, microservices, uses_snowflake, uses_pdc, APIs, security
  - Missing platform services auto-added to forbidden_claims
  - Validation, conflict detection (override wins), changelog tracking
  - `--dry-run`, `--diff`, `--product`, `--full` (generate+merge), `--validate`
- **TESTS:** 52 new tests in `tests/test_product_profiles.py` (382 total)
- Zero LLM calls — pure programmatic

### 2026-03-12 — Gemini Batch API Support
- **FEATURE:** `src/batch_llm.py` — Gemini Batch API wrapper for 50% cost reduction
  - `BatchProcessor` class: add requests, submit, poll, extract results
  - Auto-selects inline (<=100 requests) vs file upload (>100)
  - `parse_json_from_batch()` — 3-strategy JSON parsing consistent with codebase
  - Handles both inline and file-based result extraction
- **INTEGRATION:** `--batch` flag added to:
  - `kb_reclassify.py` — `llm_reclassify_async()` submits all batches as one job
  - `kb_extract_historical.py` — `classify_rows_batch_async()` for Stage 3
- **TESTS:** 27 new tests in `tests/test_batch_llm.py` (330 total)
- Uses `google-genai` SDK (already in requirements.txt)

### 2026-03-12 — ChromaDB Incremental Sync + Blue/Green Rebuild
- **FEATURE:** `src/kb_index_sync.py` — new default way to update ChromaDB
  - Incremental sync: tracks SHA-256 hashes in `data/kb/file_state.json`,
    only re-embeds changed/new/deleted canonical files
  - Blue/Green rebuild (`--force-rebuild`): builds new collection, validates,
    then swaps — NEVER leaves live collection empty
  - `--dry-run` flag to preview changes without modifying ChromaDB
  - Indexes per-family canonical files (not UNIFIED), each vector tagged with `source_file`
  - Deterministic vector IDs: hash of (filename + question)
  - Embedding model version check: refuses incremental if model changed
  - Atomic state writes (tmp + rename) for crash safety
- **AUTO-TRIGGER:** `kb_extract_historical.py`, `kb_dedup.py`, `kb_reclassify.py`
  now auto-call `sync()` after successful changes
- **DEPRECATED:** `kb_embed_chroma.py` — prints deprecation warning, still works
- **TESTS:** 49 new tests in `tests/test_kb_index_sync.py` (303 total)
- Updated `.gitignore` for `file_state.json`

### 2026-02-19 (v2 — 3-stage pipeline)
- **FEATURE:** KB Expansion 3-Stage Interactive Pipeline
  - Restructured `data/kb/historical/{family}/` → added `inbox/` and `processing/` subfolders
  - Created central `data/kb/archive/` with `files/`, `extractions/` (both gitignored)
    and `archive_registry.json` (committed — no customer data)
  - **Rewrote** `src/kb_extract_historical.py` as full 3-stage pipeline:
    - Stage 1: `prescan_excel()` (zero LLM cost) + `collect_metadata_interactive()` +
      `analyze_structure_llm()` (one gemini-flash call per file) + `confirm_structure()`
    - Stage 2: `extract_pairs_from_workbook()` using detected column map, handles category headers
    - Stage 3: `classify_pairs_batch()` (15/call, gemini-flash) + interactive terminal review
      with Y/N/E/A/S/Q for CREATE mode; K/R/M/N comparison for IMPROVE mode
    - `--resume` flag: saves/loads `staging/{family}_session.json`
    - Archive: moves file + writes structure.json + extracted.jsonl + updates registry
  - **Updated** `src/kb_stats.py` — shows Inbox, Archived columns + archive summary
  - **Added** `src/kb_archive_search.py` — query archive_registry.json
    (--list, --client, --family, --from/--to, --id, --json)
  - Updated `.gitignore` for archive/files/, archive/extractions/, staging/*.jsonl
  - ASCII-only terminal output throughout (Windows cp1252 compatible)

### 2026-01-03
- **REFACTOR:** Complete project restructure for clean architecture
  - Created new directory structure: `data/`, `src/`, `scripts/`, `prompts/`, `tests/`
  - Moved core scripts from `scripts/core/` to `src/`
  - Moved KB data from `data_kb/` to `data/kb/`
  - Moved prompts from `prompts_instructions/` to `prompts/`
  - Moved utility scripts from `scripts/utils/` to `scripts/`
  - Deleted legacy folders: `scripts/archive/`, `scripts/custom/`, `scripts/maintenance/`, `logs/`
  - Updated all import paths and PROJECT_ROOT calculations
  - Updated `.gitignore` for new paths

### 2025-01-01
- **FEATURE:** Solution-aware response system with platform service context
  - Added `--solution` flag to `rfp_batch_universal.py` (41 solutions available)
  - Integrated `config/platform_matrix.json` for service status lookup
  - Added `docs/platform_context.md` with response templates
  - Updated `llm_router.py` to inject solution-specific context before KB context
  - Context includes: native/planned/infrastructure service lists
  - Response framing adjusts based on integration level
  - NEVER exposes version numbers or roadmap dates to customer
  - Used Opus model for complex integration task

### 2024-12-31
- **BUGFIX:** Fixed "Not in KB" issue in `rfp_batch_universal.py`
  - Root cause: ChromaDB IDs (`planning_kb_0001`) didn't match KB lookup keys (`kb_0001`)
  - Solution: Updated `llm_router.py` to build lookup dict with both formats
  - Changed KB path from Planning-only to Unified KB (`RFP_Database_UNIFIED_CANONICAL.json`)
  - Added DEBUG_RAG=1 env var for retrieval debugging
  - Removed all emojis for Windows console compatibility
- **INTEGRATION:** Completed `kb_transform_knowledge.py` + `kb_merge_canonical.py` workflow
  - Auto-discovery of canonical files by domain
  - Dynamic merge without hardcoded file lists
  - Support for WMS and future domains
- **CHROMADB:** Fixed ID uniqueness across domains
  - Format: `{domain}_{kb_id}` (e.g., `planning_kb_0001`, `wms_0001`)
  - Updated `kb_embed_chroma.py` to generate domain-prefixed IDs
  - Verified 899 entries indexed successfully

### 2024-12-30
- Created `kb_transform_knowledge.py` - universal transformer for JSONL → Canonical
- Created `platform_matrix.json` from Platform_Usage_by_Product.xlsx
- Transformed WMS knowledge: 38 entries (10 platform, 28 product-specific)
- Added versioning schema to KB entries
- Added scope classification (platform vs product_specific)

### Previous (v0.2)
- Universal RAG architecture with ChromaDB + BGE embeddings
- Multi-LLM router with 8 providers
- Anonymization system with YAML config and middleware
- GLM retry logic with exponential backoff

## Notes for Claude Code

1. **Before editing KB-related files:** Check the schema in this document
2. **When adding new domains:** Update `DOMAIN_KEYWORDS` in `kb_transform_knowledge.py`
3. **When running batch processor:** Always test with `--test` flag first
4. **GLM rate limits:** Use `--workers 2` to avoid 429 errors
5. **Anonymization:** Run `scan_kb` before `clean_kb`, always use `--dry-run` first

## Contact

Project maintained by [Your Name]. For questions about architecture decisions, check the transcript history in Claude Chat.

## Git

Commit frequently with clear messages. Push after each working feature.

## Model Selection Guide

Use `/model claude-sonnet-4-20250514` (default) for:
- Creating simple files and functions
- Small edits, quick fixes
- Running tests and commands
- Iterative development
- Simple CRUD operations

Use `/model claude-opus-4-20250514` for:
- System architecture decisions
- Complex debugging (errors spanning multiple files)
- Refactoring across multiple files
- Large context analysis (understanding whole codebase)
- Code review and optimization
- When Sonnet fails 2+ times on same task

Rule: Start with Sonnet. Switch to Opus when stuck or task is complex.