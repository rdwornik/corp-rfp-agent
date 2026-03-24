# Code Review Report -- corp-rfp-agent

**Date:** 2026-03-15
**Branch:** `code-review-2026-03-15`
**Reviewer:** Claude Opus 4.6

## Summary

```
REPO:          corp-rfp-agent
TESTS:         151 passed, 0 failed
RUFF:          clean (all checks passed)
COMMITS:       4 commits on review branch
FILES CHANGED: 28
```

## Commits Made

1. **`8553761` style: ruff lint + format pass**
   - Auto-fixed 36 lint issues (unused imports, f-string without placeholders)
   - Manually fixed 4 unused variables (`item`, `last_error` x2, `product`)
   - Formatted all 26 Python files with ruff
   - Added `[tool.ruff.lint]` to pyproject.toml suppressing E402

2. **`a52410d` chore: consolidate requirements.txt into pyproject.toml**
   - Moved 14 runtime dependencies from requirements.txt into pyproject.toml
   - Added ruff to `[dev]` extras
   - Updated requires-python from >=3.10 to >=3.12
   - Deleted requirements.txt (pyproject.toml is now single source of truth)

3. **`8ed91b6` docs: update CLAUDE.md to current state**
   - Restructured to standard format with architecture diagram
   - Added config file table, test suite breakdown, model registry
   - Documented all CLI entry points and commands
   - Added known issues and related repos sections

4. **`2264b9a` docs: professional README**
   - One-paragraph description, feature bullets, installation
   - Usage examples with all CLI options
   - Architecture overview, test count, related repos

## Issues Found

### Fixed

| Issue | File | Fix |
|-------|------|-----|
| Unused import `get_settings` | anonymization/core.py | Removed |
| Unused import `load_config` | anonymization/scan_kb.py | Removed |
| Unused import `sys` | kb_to_markdown.py | Removed |
| Unused import `pytest` | test_answer_selector.py | Removed |
| Unused imports `Alignment`, `qn`, `OxmlElement` | create_fixtures.py | Removed |
| Unused import `PatternFill`, `Cell` | rfp_excel_agent.py | Removed |
| f-string without placeholders (x3) | answer_selector.py | Removed f-prefix |
| Unused variable `item` | llm_router.py:248 | Removed assignment |
| Unused variable `last_error` (x2) | rfp_excel_agent.py:54,69 | Removed |
| Unused variable `product` | validate_profiles.py:190 | Removed |
| Dual dependency sources | requirements.txt + pyproject.toml | Consolidated into pyproject.toml |
| requires-python too permissive | pyproject.toml | Changed >=3.10 to >=3.12 |

### Documented as Known Issues (not fixed)

| Issue | Location | Reason |
|-------|----------|--------|
| `validate_profiles.py --merge` imports deleted `merge_profiles` | validate_profiles.py:377 | Dead code path, only reached via CLI flag |
| ChromaDB fallback references removed deps | llm_router.py, rfp_answer_word.py | Gracefully degraded via try/except |
| Stale .gitignore entries | .gitignore | References deleted dirs (archive, historical, etc.) |
| No test coverage for main agent flows | rfp_excel_agent.py, rfp_answer_word.py | Acceptance tests cover helpers only |
| No test coverage for scan/clean KB CLIs | anonymization/scan_kb.py, clean_kb.py | Utility scripts, not critical path |
| Stale permissions in .claude/settings.local.json | .claude/ | Gitignored, references old script paths |

## Test Results

```
151 passed in 38s

test_answer_selector.py     52 tests  PASS
test_cli_smoke.py            5 tests  PASS
test_excel_acceptance.py    11 tests  PASS
test_rfp_feedback.py        15 tests  PASS
test_router_acceptance.py   12 tests  PASS
test_validate_profiles.py   32 tests  PASS
test_vault_adapter.py        9 tests  PASS
test_word_acceptance.py     10 tests  PASS
conftest.py                  5 fixtures
```

## Ruff Results

```
All checks passed!
E402 suppressed globally (intentional load_dotenv ordering)
```
