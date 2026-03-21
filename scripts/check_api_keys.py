#!/usr/bin/env python3
"""Verify which API keys are configured."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Global API keys (Documents/.secrets/.env)
_global_env = Path.home() / "Documents" / ".secrets" / ".env"
if _global_env.exists():
    load_dotenv(_global_env, override=False)

# Local .env (project-specific vars only)
PROJECT_ROOT = Path(__file__).parent.parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)

API_KEYS = {
    "Claude (Anthropic)": "ANTHROPIC_API_KEY",
    "GPT (OpenAI)": "OPENAI_API_KEY",
    "Gemini (Google)": "GEMINI_API_KEY",
    "DeepSeek": "DEEPSEEK_API_KEY",
    "GLM (Zhipu)": "ZHIPU_API_KEY",
    "Kimi (Moonshot)": "MOONSHOT_API_KEY",
    "Grok (xAI)": "XAI_API_KEY",
    "Llama (Together)": "TOGETHER_API_KEY",
}

print("=" * 50)
print("API Keys Status")
print("=" * 50)

configured = 0
for name, env_var in API_KEYS.items():
    key = os.getenv(env_var)
    if key:
        # Show only first/last 4 chars
        masked = f"{key[:8]}...{key[-4:]}"
        print(f"[OK] {name}: {masked}")
        configured += 1
    else:
        print(f"[--] {name}: Not configured")

print("=" * 50)
print(f"Total: {configured}/{len(API_KEYS)} configured")

if configured == 0:
    print("\n[WARNING] No API keys found!")
    print("Copy .env.example to .env and add your keys.")
