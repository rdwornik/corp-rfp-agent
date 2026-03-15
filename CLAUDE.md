# Corp RFP Agent - Project Context

> Context for Claude Code. Update after significant changes.

## Overview

AI-powered RFP answering system for Blue Yonder solutions.

**Core Flow:**
```
RFP (Excel/Word) -> Anonymize -> Vault Retrieval -> LLM Generation -> De-anonymize -> Answers
```

## Architecture

- **Retrieval:** Vault FTS5 full-text search (via vault_adapter.py)
- **LLM Router:** 4 models, 3 providers (Gemini, Claude, OpenAI)
- **Anonymization:** YAML-based blocklist with middleware pattern
- **Profiles:** 41 product profiles with forbidden_claims guardrails

## File Structure

```
src/
  rfp_excel_agent.py       # Excel RFP agent (green-cell detection)
  rfp_answer_word.py        # Word RFP agent (section tree parsing)
  llm_router.py             # Multi-LLM routing (gemini, sonnet, gpt, gemini-flash)
  vault_adapter.py          # Knowledge retrieval (vault CLI + SQLite fallback)
  answer_selector.py        # 5-stage answer quality scoring
  rfp_feedback.py           # KB corrections CLI (show, correct, search)
  validate_profiles.py      # Product profile validation (contradictions, missing data)
  kb_to_markdown.py         # KB JSON -> markdown migration
  anonymization/            # Client name masking package
    config.py               #   YAML loader
    core.py                 #   anonymize(), deanonymize()
    middleware.py            #   Pipeline middleware
    scan_kb.py               #   CLI: scan KB for sensitive terms
    clean_kb.py              #   CLI: clean KB with backup

config/
  product_profiles/         # Per-family YAML profiles
    _generated/             #   Auto from CKE (safe to regenerate)
    _overrides/             #   Manual corrections (NEVER overwritten)
    _effective/             #   Merged (pipeline reads ONLY here)
  anonymization.yaml        # Blocklist config
  overrides.yaml            # Term replacements (JDA -> Blue Yonder)
  platform_matrix.json      # Platform services per solution

data/kb/
  verified/{family}/        # 1,155 production entries (JSON, backup)
  drafts/{family}/          # 174 draft entries
  canonical/                # Legacy canonical files (backward compat)

prompts/
  rfp_system_prompt_universal.txt

tests/                      # 151 tests across 8 test files
```

## Key Commands

```bash
# Answer RFP Excel (green-cell detection)
python src/rfp_excel_agent.py \
    --input "RFP.xlsx" \
    --client acme \
    --solution planning \
    --model gemini

# Answer RFP Word doc
python src/rfp_answer_word.py \
    --input "RFP.docx" \
    --solution wms \
    --model gemini

# Compare models
python src/llm_router.py --compare --query "How does WMS integrate?"

# KB feedback (show, correct, search)
python src/rfp_feedback.py show KB_0234
python src/rfp_feedback.py correct KB_0234 --text "New answer" --offline --apply
python src/rfp_feedback.py search "JSON ingestion" --family planning

# Validate product profiles
python src/validate_profiles.py
python src/validate_profiles.py --product wms --auto-fix

# Migrate KB to markdown
python src/kb_to_markdown.py --dry-run
python src/kb_to_markdown.py

# Scan/clean KB for sensitive terms
python -m src.anonymization.scan_kb
python -m src.anonymization.clean_kb --dry-run
```

## Environment Variables

```bash
GEMINI_API_KEY=...       # Google Gemini (required)
ANTHROPIC_API_KEY=...    # Claude (optional)
OPENAI_API_KEY=...       # GPT (optional)
```

## Architecture Decisions

### Why vault FTS5 instead of ChromaDB?
- ChromaDB + sentence-transformers + torch added ~2GB of dependencies
- Vault FTS5 provides full-text search with zero additional deps
- KB entries migrated to markdown in corp_data/rfp_kb/

### Why YAML for anonymization config?
- Human-readable for non-technical users
- Easy to maintain blocklists

### Why product profiles with forbidden_claims?
- Prevents LLM from hallucinating capabilities a product doesn't have
- 41 profiles cover all BY product families
- Override system allows manual corrections without regeneration

## Notes for Claude Code

1. **Commit frequently** with clear messages. Push after each working feature.
2. **Anonymization:** Run `scan_kb` before `clean_kb`, always `--dry-run` first.
3. **Tests:** 151 tests, all must pass. Run `python -m pytest tests/ -v`.
4. **Rollback tag:** `v1.0-full-system` has the full pre-simplification codebase.

## Model Selection Guide

Use Sonnet (default) for:
- Small edits, quick fixes, running tests
- Iterative development, simple CRUD

Use Opus for:
- System architecture decisions
- Complex debugging across multiple files
- Large context analysis
- When Sonnet fails 2+ times on same task
