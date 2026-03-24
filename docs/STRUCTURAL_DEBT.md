# RFP Agent Structural Debt

## For Monorepo Phase 3 (during merge)

### Namespace migration
- Current: flat `src/` with `sys.path.insert(0, "src")` in `rfp_answer_word.py`
- Target: `src/corp_rfp_agent/` proper package
- Same pattern as CKE Phase 0a migration
- pytest already uses `pythonpath = ["src"]` in pyproject.toml

### CLI migration
- Current: 7 argparse scripts in `src/`
  - `rfp_excel_agent.py`
  - `rfp_answer_word.py`
  - `llm_router.py`
  - `validate_profiles.py`
  - `rfp_feedback.py`
  - `kb_to_markdown.py`
  - `anonymization/clean_kb.py`
- Target: Click-based CLI with unified entry point `rfp`
- Click already in dependencies (>=8.0)

## Post-merge (separate tasks)

### Print cleanup
- 246 bare `print()` calls across 9 files in `src/`
- Heaviest: `rfp_excel_agent.py` (76), `rfp_answer_word.py` (57), `rfp_feedback.py` (29), `llm_router.py` (28)
- Target: Rich console output (consistent with ecosystem)
- Rich already in dependencies (>=14.0)

### vault_adapter.py enhancements
- Parse `verification_status` from vault notes
- Polarity filtering in `llm_router.py`
- Trust boundary prompts ("only verified facts for customer-facing")

### corp-os-meta integration
- Currently no references to `corp_os_meta` or `NoteFrontmatter`
- RFP agent reads vault notes as raw markdown via `corp retrieve --format json`
- Planned: use shared schema for structured note parsing
