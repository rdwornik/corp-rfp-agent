"""Smoke tests -- all CLI commands respond to --help without error."""

import subprocess
import sys

import pytest

COMMANDS = [
    [sys.executable, "src/rfp_answer_word.py", "--help"],
    [sys.executable, "src/rfp_batch_universal.py", "--help"],
    [sys.executable, "src/rfp_excel_agent.py", "--help"],
    # kb_build_canonical.py and kb_embed_chroma.py have no --help (no argparse)
    [sys.executable, "src/kb_extract_historical.py", "--help"],
    [sys.executable, "src/kb_archive_search.py", "--help"],
    [sys.executable, "src/excel_to_platform_matrix.py", "--help"],
]


@pytest.mark.parametrize("cmd", COMMANDS, ids=[c[1] for c in COMMANDS])
def test_cli_help(cmd):
    """CLI command exits 0 with --help."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(subprocess.os.path.dirname(subprocess.os.path.dirname(__file__)) or "."),
        timeout=30,
    )
    assert result.returncode == 0, (
        f"{' '.join(cmd)} failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout[:500]}\n"
        f"stderr: {result.stderr[:500]}"
    )
