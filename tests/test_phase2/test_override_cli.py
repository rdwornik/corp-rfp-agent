"""Tests for override CLI."""

import yaml
from pathlib import Path

from corp_rfp_agent.overrides.cli import main


def _write_yaml(tmp_path, overrides_list):
    """Helper to write an overrides YAML file."""
    path = tmp_path / "overrides.yaml"
    data = {"overrides": overrides_list}
    path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    return path


def test_cli_list(tmp_path, capsys):
    """CLI list command shows overrides."""
    path = _write_yaml(tmp_path, [
        {"id": "OVR-0001", "find": "Splunk", "replace": "CrowdStrike",
         "description": "SIEM change"},
    ])
    main(["--config", str(path), "list"])
    out = capsys.readouterr().out
    assert "OVR-0001" in out
    assert "Splunk" in out
    assert "CrowdStrike" in out


def test_cli_test(tmp_path, capsys):
    """CLI test command shows replacements."""
    path = _write_yaml(tmp_path, [
        {"id": "OVR-0001", "find": "Splunk", "replace": "CrowdStrike"},
    ])
    main(["--config", str(path), "test", "We use Splunk for monitoring."])
    out = capsys.readouterr().out
    assert "CrowdStrike" in out
    assert "Replacements:" in out


def test_cli_stats(tmp_path, capsys):
    """CLI stats command shows summary."""
    path = _write_yaml(tmp_path, [
        {"id": "OVR-0001", "find": "A", "replace": "B"},
        {"id": "OVR-0002", "find": "C", "replace": "D", "enabled": False},
    ])
    main(["--config", str(path), "stats"])
    out = capsys.readouterr().out
    assert "Total overrides: 2" in out
    assert "Enabled:  1" in out
    assert "Disabled: 1" in out
