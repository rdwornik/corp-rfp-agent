# Corp RFP Agent

AI-powered RFP answering engine for Blue Yonder pre-sales engineering. Reads Excel or Word RFP documents, retrieves relevant answers from a knowledge vault of 1,329 curated entries across 41 product families, generates professional responses via LLM (Gemini, Claude, or GPT), and writes them back into the original file. Includes product profile guardrails to prevent hallucinations and client name anonymization for safe API calls.

## Features

- **Excel agent** -- detects green-highlighted cells as questions, writes answers to the adjacent column
- **Word agent** -- parses document section tree, inserts answers below each section heading
- **Multi-model routing** -- Gemini 3.1 Pro (default), Claude Sonnet 4.6, GPT-4o, with model comparison mode
- **Knowledge vault** -- FTS5 full-text search over 1,329 markdown entries with product/topic filtering
- **Product profiles** -- 41 YAML profiles with `forbidden_claims` guardrails (e.g., WMS won't claim Snowflake support)
- **Anonymization** -- YAML-based blocklist masks client names before LLM API calls
- **Answer quality scoring** -- 5-stage pipeline for IMPROVE mode (red flag detection, similarity bucketing, scoring, LLM judge)

## Installation

```bash
pip install -e ".[dev]"
```

Create `.env` with at least `GEMINI_API_KEY=...` (Claude and GPT keys optional).

## Usage

```bash
# Answer RFP Excel (green-cell detection)
python src/rfp_excel_agent.py --input "RFP.xlsx" --client acme --solution planning --model gemini

# Answer RFP Word doc
python src/rfp_answer_word.py --input "RFP.docx" --solution wms --model gemini

# Compare models side by side
python src/llm_router.py --compare --query "How does WMS handle integration?"

# Correct a KB entry
python src/rfp_feedback.py correct KB_0234 --text "Updated answer" --offline --apply

# Validate product profiles for contradictions
python src/validate_profiles.py

# Dry-run mode (analyze without writing)
python src/rfp_excel_agent.py --input "RFP.xlsx" --client test --dry-run
```

## Architecture

```
src/
  rfp_excel_agent.py       Excel RFP agent (green-cell detection)
  rfp_answer_word.py        Word RFP agent (section tree parsing)
  llm_router.py             LLM routing (4 models, 3 providers)
  vault_adapter.py          Knowledge retrieval (vault FTS5)
  answer_selector.py        Answer quality scoring (5-stage)
  rfp_feedback.py           KB corrections (show/correct/search)
  validate_profiles.py      Product profile validation
  kb_to_markdown.py         KB migration utility
  anonymization/            Client name masking package
```

## Testing

```bash
python -m pytest
```

151 tests across 8 files covering Excel/Word agents, LLM routing, answer scoring, profile validation, vault retrieval, and feedback CLI.

## Related repos

- **corp-by-os** -- orchestrator
- **corp-os-meta** -- shared schemas
- **corp-knowledge-extractor** -- extraction engine
- **corp-rfp-agent** -- this repo
- **ai-council** -- multi-model debate

## License

Internal use only -- Blue Yonder Pre-Sales Engineering.
