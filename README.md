# Corp RFP Agent

AI-powered RFP answering engine for Blue Yonder pre-sales.
Reads Excel/Word RFP files, finds answers from knowledge vault,
returns filled documents with Blue Yonder responses.

## How It Works

1. Drop Excel with green-highlighted question cells (or Word doc)
2. Agent finds relevant answers from knowledge vault
3. LLM generates professional Blue Yonder response
4. Output: same file with answers filled in

## Usage

```bash
# Answer RFP Excel
python src/rfp_excel_agent.py --input "RFP.xlsx" --client acme --solution planning --model gemini

# Answer RFP Word doc
python src/rfp_answer_word.py --input "RFP.docx" --solution wms --model gemini

# Compare models on a question
python src/llm_router.py --compare --query "How does WMS integrate?"

# Correct a KB entry
python src/rfp_feedback.py correct KB_0234 --text "Fix: remove JSON" --offline --apply

# Validate product profiles
python src/validate_profiles.py
```

## Models

| Key | Model | Use |
|-----|-------|-----|
| gemini | Gemini 3.1 Pro | Default for answers |
| gemini-flash | Gemini 3 Flash | Classification |
| sonnet | Claude Sonnet 4.6 | Alternative |
| gpt | GPT-4o | Alternative |

## Project Structure

```
src/
  rfp_excel_agent.py       Excel RFP answering (green cells)
  rfp_answer_word.py        Word RFP answering (section tree)
  llm_router.py             LLM routing (4 models, 3 providers)
  vault_adapter.py          Knowledge retrieval (vault FTS5)
  answer_selector.py        Answer quality scoring (5-stage)
  rfp_feedback.py           KB corrections (show/correct/search)
  validate_profiles.py      Product profile validation
  kb_to_markdown.py         KB migration utility
  anonymization/            Client name masking
config/
  product_profiles/         41 product profiles + overrides
  overrides.yaml            Term replacements (JDA -> Blue Yonder)
  anonymization.yaml        Anonymization patterns
prompts/
  rfp_system_prompt_universal.txt
data/kb/
  verified/{family}/        1,155 production KB entries (JSON)
  drafts/{family}/          174 draft entries pending review
tests/                      151 tests
```

## Product Profiles

41 profiles with `forbidden_claims` guardrails prevent
hallucinations (e.g., WMS won't claim to use Snowflake).
Override via `config/product_profiles/_overrides/`.

## Knowledge Base

1,329 entries migrated to `corp_data/rfp_kb/` as markdown.
Retrieved via corp vault FTS5 index.
JSON source files retained in `data/kb/` as backup.

## Configuration

- `.env`: `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`
- `config/overrides.yaml`: term replacements
- `config/product_profiles/_effective/`: active profiles
- `config/anonymization.yaml`: client name masking patterns

## Testing

```bash
python -m pytest tests/ -v
```

## License

Internal use only -- Blue Yonder Presales.
