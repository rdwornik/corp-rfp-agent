# Python Environment — corp-rfp-agent

- Python >=3.12
- Virtual env: .venv\Scripts\Activate.ps1
- Install: pip install -e ".[dev]"
- E402 suppressed globally (load_dotenv must run before env-dependent imports)
- This is a CONSUMER — reads from vault via `corp retrieve`, does not write to vault
- KB lives at C:\Users\1028120\Documents\corp_data\rfp_kb\ (outside any repo)
- Multi-LLM: Gemini (primary), Claude (alternative), GPT (alternative)
