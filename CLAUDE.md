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
│   └── platform_matrix.json           # Platform services matrix (from Excel)
│
├── data/
│   ├── kb/
│   │   ├── raw/                       # Source files (JSONL from workshops)
│   │   │   └── knowledge_wms.jsonl
│   │   ├── canonical/                 # Transformed KB files
│   │   │   ├── RFP_Database_Cognitive_Planning_CANONICAL.json
│   │   │   ├── RFP_Database_AIML_CANONICAL.json
│   │   │   ├── RFP_Database_WMS_CANONICAL.json
│   │   │   └── RFP_Database_UNIFIED_CANONICAL.json  # Merged
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
│   ├── kb_embed_chroma.py             # Index to ChromaDB
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

# Re-index to ChromaDB
python src/kb_embed_chroma.py

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

## KB Expansion — Phase 1 Infrastructure

### Overview

The KB currently covers Planning (806 entries), WMS (38), and AIML (54). Phase 1 expands to all
Blue Yonder product families by extracting Q&A pairs from historical RFP Excel files.

### Folder Structure

```
data/kb/
├── historical/          # Drop historical RFP .xlsx files here (gitignored)
│   ├── planning/        # Phase 2 (improve mode)
│   ├── wms/
│   ├── logistics/
│   ├── scpo/
│   ├── catman/
│   ├── workforce/
│   ├── commerce/
│   ├── flexis/
│   ├── network/
│   ├── doddle/
│   └── aiml/
├── staging/             # Human review queue (gitignored)
│   └── {family}_improvements.jsonl
└── schema/              # Versioned schemas (committed)
    ├── family_config.json
    └── canonical_entry_v2.json
```

### Extraction Workflow

**Step 1: Drop historical RFP files**
```
data/kb/historical/wms/  ← copy .xlsx files here
```

**Step 2: Check status**
```bash
python src/kb_stats.py
```

**Step 3: Extract (CREATE mode — families with 0 entries)**
```bash
python src/kb_extract_historical.py --family wms --mode create --model gemini
# Always dry-run first:
python src/kb_extract_historical.py --family wms --mode create --model gemini --dry-run
```

**Step 4: Find gaps (IMPROVE mode — Planning with 806 entries)**
```bash
python src/kb_extract_historical.py --family planning --mode improve --model gemini
# New entries → auto-added to Planning canonical
# Better answers → staging/planning_improvements.jsonl for human review
```

**Step 5: Re-merge and re-index**
```bash
python src/kb_merge_canonical.py
python src/kb_embed_chroma.py
```

### KB Entry Schema v2

v2 entries are a superset of v1 — all new fields have defaults, so existing entries are still valid.

Key new fields vs v1:
- `id`: structured format `{PREFIX}-{CAT}-{NNNN}` e.g. `WMS-FUNC-0042`
- `family_code`: explicit product family (for ChromaDB filtering)
- `question_variants`: alternative phrasings for better RAG recall
- `solution_codes`: which specific solutions within the family this applies to
- `tags`: keyword array for search boosting
- `confidence`: `verified | draft | needs_review | outdated`
- `source_rfps`: traceability to source RFP files
- `cloud_native_only`: flag for SaaS-only features

Schema file: `data/kb/schema/canonical_entry_v2.json`
Family config: `data/kb/schema/family_config.json`

### Phase Tracking

| Phase | Mode | Description |
|-------|------|-------------|
| Phase 1 | CREATE | New families (0 entries) — extract from historicals |
| Phase 2 | IMPROVE | Planning (806 entries) — find gaps, flag improvements |

## Current Tasks

- [ ] Drop historical RFP Excel files into `data/kb/historical/{family}/` folders
- [ ] Run `kb_extract_historical.py --mode create` for Phase 1 families
- [ ] Run `kb_extract_historical.py --mode improve` for Planning (Phase 2)
- [ ] Review `staging/planning_improvements.jsonl` and apply best improvements
- [ ] Create `kb_deprecate.py` CLI tool for marking old entries
- [ ] Add `--solution wms|planning|catman` flag to batch processor
- [ ] Update `kb_embed_chroma.py` to filter deprecated entries

## Recent Changes

### 2026-02-19
- **FEATURE:** KB Expansion Phase 1 Infrastructure
  - Created `data/kb/historical/{family}/` folders (11 product families)
  - Created `data/kb/staging/` for human review queue
  - Created `data/kb/schema/family_config.json` — product family → solution codes / ID prefix / phase
  - Created `data/kb/schema/canonical_entry_v2.json` — JSON Schema for v2 KB entries (superset of v1)
  - Built `src/kb_extract_historical.py` — CREATE and IMPROVE modes for Excel extraction
    - CREATE: scans historical/, detects column structure via LLM, extracts Q&A, classifies, deduplicates, appends to canonical
    - IMPROVE: embeds historical Q&A, checks cosine similarity against existing (threshold 0.80), new entries → canonical, better answers → staging/
  - Built `src/kb_stats.py` — dashboard showing entry counts per family + historical file counts
  - Updated `.gitignore` to block historical RFP files and staging dir
  - Updated `CLAUDE.md` with KB expansion workflow

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