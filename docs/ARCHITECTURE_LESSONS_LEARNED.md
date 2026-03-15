# Architecture Lessons Learned

> Snapshot taken at `v1.0-full-system` (commit 1880eab, 2026-03-15)
> To restore the full system: `git checkout v1.0-full-system`

## What Was Built

An AI-powered RFP answering engine for Blue Yonder solutions, grown from a simple RAG pipeline into a comprehensive knowledge management system with three self-learning loops.

### Full Component Inventory (at v1.0)

| Component | Files | Tests | Purpose |
|-----------|-------|-------|---------|
| **LLM Router** | `llm_router.py` | `test_router_acceptance.py` | Multi-provider routing (Gemini, Claude, GPT-5, DeepSeek, GLM, Kimi, Llama, Grok) |
| **Answer Selector** | `answer_selector.py` | `test_answer_selector.py` | Multi-model comparison, best-answer selection |
| **Batch Processor** | `rfp_batch_universal.py` | `test_cli_smoke.py` | Main RFP batch processing pipeline |
| **Excel Agent** | `rfp_excel_agent.py` | `test_excel_acceptance.py` | Green-cell Excel processing |
| **Word Export** | `rfp_answer_word.py` | `test_word_acceptance.py` | Word document generation |
| **Anonymization** | `anonymization/` | (in phase tests) | YAML-based blocklist, middleware pattern |
| **Product Profiles** | `generate_product_profiles.py`, `merge_profiles.py`, `validate_profiles.py` | `test_product_profiles.py`, `test_validate_profiles.py` | 3-layer profile system (generated/overrides/effective) |
| **KB Transform** | `kb_merge_canonical.py`, `kb_migrate_to_files.py` | (in phase tests) | Knowledge base build pipeline |
| **KB Historical Extraction** | `kb_extract_historical.py` | (in phase tests) | 3-stage interactive pipeline from Excel RFPs |
| **KB Index Sync** | `kb_index_sync.py` | `test_kb_index_sync.py` | ChromaDB incremental sync + Blue/Green rebuild |
| **KB Reclassify** | `kb_reclassify.py` | `test_kb_reclassify.py` | LLM-based KB entry reclassification |
| **KB Dedup** | `kb_dedup.py` | `test_kb_dedup.py` | Embedding-based deduplication |
| **KB Ingest** | `kb_ingest.py` | `test_kb_ingest.py` | CKE facts to draft KB entries (5-stage pipeline) |
| **KB Eval** | `kb_eval.py` | `test_kb_eval.py` | Deterministic health evaluation (no LLM) |
| **KB Quality Scorer** | `kb_quality_scorer.py` | `test_kb_quality_scorer.py` | LLM 5-dimension quality scoring |
| **KB Simulate RFP** | `kb_simulate_rfp.py` | `test_kb_simulate_rfp.py` | Simulated RFP test suite |
| **Feedback CLI** | `rfp_feedback.py` | `test_rfp_feedback.py` | Correct, approve, reject, retag, propagate, auto-promote |
| **Batch LLM** | `batch_llm.py` | `test_batch_llm.py` | Gemini Batch API wrapper (50% cost) |
| **Review Pack** | `generate_review_pack.py`, `import_review_pack.py` | `test_generate_review_pack.py`, `test_import_review_pack.py` | SME review workflow |
| **Archive Search** | `kb_archive_search.py` | — | Query archive registry |
| **Platform Matrix** | `excel_to_platform_matrix.py` | — | Excel to platform_matrix.json |

**Total: 633 tests across ~20 test files**

### The Three Learning Loops

1. **Loop 1 — Feedback Loop:** `rfp_feedback.py` correct/approve/reject + `propagate` for similar entries
2. **Loop 2 — Ingestion Loop:** `kb_ingest.py` (CKE facts) → `kb_quality_scorer.py` → `auto-promote`
3. **Loop 3 — Simulation Loop:** `kb_simulate_rfp.py` generates synthetic RFP questions, answers via RAG, scores accuracy

## What Worked Well

### Answer Selector (`answer_selector.py`)
- Multi-model comparison is genuinely useful for high-stakes RFP answers
- Clean abstraction: ask N models, pick the best, explain why
- Low complexity, high value

### Product Profiles (3-layer system)
- `_generated/` (safe to regenerate) + `_overrides/` (manual corrections) + `_effective/` (pipeline reads here)
- Override merge with `forbidden_claims_add/remove`, `platform_services_add/remove` is elegant
- `forbidden_claims` concept is critical — prevents hallucinating features a product doesn't have
- `validate_profiles.py` catches contradictions between fields and forbidden_claims
- Zero LLM calls, pure programmatic — fast and deterministic

### Forbidden Claims
- Simple idea, huge impact: list what a product does NOT support
- Prevents the most dangerous RFP failure mode: promising something that doesn't exist
- Used in validation, feedback approval, and ingestion pipelines

### ChromaDB Incremental Sync (`kb_index_sync.py`)
- Blue/Green rebuild is production-safe — never leaves live collection empty
- SHA-256 hash tracking for true incremental updates
- `--dry-run` for safe previewing

### Anonymization System
- YAML-based blocklist is accessible to non-technical users
- Middleware pattern integrates cleanly into the pipeline
- Scan before clean, dry-run first — good safety defaults

### Batch LLM (`batch_llm.py`)
- 50% cost savings on Gemini Batch API is significant at scale
- Clean abstraction over inline vs file-based submission

### LLM Router
- Multi-provider support provides resilience and comparison capability
- Solution-aware context injection is valuable for accurate answers

## Key Insight

**The bottleneck is data quality, not infrastructure.**

We built three learning loops, a 5-stage ingestion pipeline, an LLM quality scorer,
a simulated RFP test suite, and an auto-promotion system. None of these ran regularly
because the underlying knowledge base only had ~900 entries, most of which needed
manual review before they could be trusted.

The things that actually moved the needle were:
- **Product profiles with forbidden_claims** — prevented hallucinating features
- **Manual override system** — let SMEs correct generated profiles without touching code
- **Answer selector** — comparing 2-3 models on a single question caught bad answers

Infrastructure scales horizontally. Data quality scales through human judgement.
Build the infrastructure when you have enough data to justify it.

## What Was Premature

### Self-Learning Loops (Loop 2 + Loop 3)
- **KB Quality Scorer** (`kb_quality_scorer.py`): 5-dimension LLM scoring is sophisticated but the KB isn't large enough yet to need automated quality gating. Manual review is still faster and more trustworthy at current scale.
- **Simulated RFP** (`kb_simulate_rfp.py`): Generating synthetic questions to test RAG accuracy is a good idea in theory, but requires a mature, large KB to produce meaningful results. At 900 entries, manual spot-checks are sufficient.
- **Auto-Promote** (in `rfp_feedback.py`): 6-criteria automatic promotion from draft to verified. In practice, no entries have ever been auto-promoted because the pipeline hasn't been used long enough to accumulate usage counts and cooling periods.

### KB Ingestion Pipeline (`kb_ingest.py`)
- The 5-stage pipeline (Collect → Generate Q&A → Validate → Dedup → Write Drafts) is well-engineered but adds complexity for a process that currently runs infrequently.
- The `--all` flag for bulk ingestion across 41 families was built before having 41 families of real data.

### KB Eval Deterministic Health (`kb_eval.py`)
- Good concept (zero LLM cost health scoring), but the health score formula and thresholds were tuned without enough real data to validate them.
- History snapshots and `--compare` delta tracking assume regular re-evaluation that hasn't happened.

### Historical Extraction 3-Stage Pipeline (`kb_extract_historical.py`)
- The interactive review loop (Y/N/E/A/S/Q for CREATE mode, K/R/M/N for IMPROVE mode) is well-designed but has only been used a handful of times.
- Resume sessions, archive registry, and metadata collection are good foundations but built ahead of need.

## Classification: Archive vs Delete vs Keep

### KEEP (core value, actively used)
- `llm_router.py` — multi-provider routing
- `answer_selector.py` — multi-model comparison
- `rfp_batch_universal.py` — main batch processor
- `rfp_excel_agent.py` — Excel agent
- `rfp_answer_word.py` — Word export
- `anonymization/` — anonymization system
- `generate_product_profiles.py`, `merge_profiles.py` — profile generation
- `validate_profiles.py` — profile validation (forbidden_claims)
- `kb_index_sync.py` — ChromaDB sync
- `kb_merge_canonical.py` — KB merge
- `batch_llm.py` — Gemini Batch API
- `config/product_profiles/` — all three layers

### ARCHIVE (good code, premature for current scale)
- `kb_quality_scorer.py` — LLM quality scoring
- `kb_simulate_rfp.py` — simulated RFP test suite
- `kb_eval.py` — deterministic health evaluation
- `kb_ingest.py` — CKE facts to draft entries
- `rfp_feedback.py` auto-promote feature (keep the rest of feedback CLI)
- All associated tests for archived components

### NEEDS DISCUSSION (could go either way)
- `kb_extract_historical.py` — 3-stage pipeline (used but rarely)
- `kb_reclassify.py` — reclassification (useful but infrequent)
- `kb_dedup.py` — deduplication (useful but infrequent)
- `kb_migrate_to_files.py` — migration tool (one-time use, already run)
- `generate_review_pack.py`, `import_review_pack.py` — SME review workflow

## How to Restore

```bash
# Restore the entire system to v1.0-full-system
git checkout v1.0-full-system

# Or cherry-pick specific files from the tag
git checkout v1.0-full-system -- src/kb_simulate_rfp.py tests/test_kb_simulate_rfp.py

# Or create a branch from the tag to work on archived features
git checkout -b feature/restore-simulation v1.0-full-system
```

## Key Metrics at v1.0

- **633 tests** across ~20 test files
- **899 KB entries** (807 Planning + 54 AIML + 38 WMS)
- **8 LLM providers** supported
- **41 product families** in profile system
- **3 learning loops** implemented (1 actively used)
- **0 LLM calls** for profile generation and validation
