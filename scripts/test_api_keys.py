#!/usr/bin/env python3
"""
Quick API key tester - sends "Say hello" to each configured provider.

Usage:
    python scripts/test_api_keys.py
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")

# Optional imports with graceful fallback
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    from google import genai
    from google.genai import types
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False


def test_claude():
    """Test Anthropic Claude API."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    if not api_key:
        print("[SKIP] Claude: No API key set")
        return

    if not ANTHROPIC_AVAILABLE:
        print("[FAIL] Claude: anthropic package not installed")
        return

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=10,
            messages=[{"role": "user", "content": "Say hello"}]
        )
        response_text = message.content[0].text.strip()
        print(f"[ OK ] Claude: '{response_text}'")
    except Exception as e:
        error_msg = str(e)
        # Shorten error message if too long
        if len(error_msg) > 100:
            error_msg = error_msg[:100] + "..."
        print(f"[FAIL] Claude: {error_msg}")


def test_openai():
    """Test OpenAI GPT API."""
    api_key = os.environ.get("OPENAI_API_KEY")

    if not api_key:
        print("[SKIP] OpenAI: No API key set")
        return

    if not OPENAI_AVAILABLE:
        print("[FAIL] OpenAI: openai package not installed")
        return

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Say hello"}],
            max_tokens=10
        )
        response_text = response.choices[0].message.content.strip()
        print(f"[ OK ] OpenAI: '{response_text}'")
    except Exception as e:
        error_msg = str(e)
        if len(error_msg) > 100:
            error_msg = error_msg[:100] + "..."
        print(f"[FAIL] OpenAI: {error_msg}")


def test_gemini():
    """Test Google Gemini API."""
    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        print("[SKIP] Gemini: No API key set")
        return

    if not GOOGLE_AVAILABLE:
        print("[FAIL] Gemini: google-genai package not installed")
        return

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.0-flash-exp",
            contents=[{"role": "user", "parts": [{"text": "Say hello"}]}],
            config=types.GenerateContentConfig(max_output_tokens=10)
        )
        response_text = response.text.strip() if response.text else "Empty response"
        print(f"[ OK ] Gemini: '{response_text}'")
    except Exception as e:
        error_msg = str(e)
        if len(error_msg) > 100:
            error_msg = error_msg[:100] + "..."
        print(f"[FAIL] Gemini: {error_msg}")


def main():
    """Run all API tests."""
    print("\n" + "="*60)
    print("API Key Test - Sending 'Say hello' to each provider")
    print("="*60 + "\n")

    test_claude()
    test_openai()
    test_gemini()

    print("\n" + "="*60)
    print("Legend: [OK] = Success | [FAIL] = Error | [SKIP] = No key")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
