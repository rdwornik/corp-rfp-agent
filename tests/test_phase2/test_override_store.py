"""Tests for YAMLOverrideStore."""

import yaml
import pytest
from pathlib import Path

from corp_rfp_agent.overrides.models import Override, OverrideResult
from corp_rfp_agent.overrides.store import YAMLOverrideStore


def _write_yaml(tmp_path, overrides_list):
    """Helper to write an overrides YAML file."""
    path = tmp_path / "overrides.yaml"
    data = {"overrides": overrides_list}
    path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
    return path


def test_load_from_yaml(tmp_path):
    """Store loads overrides from YAML file."""
    path = _write_yaml(tmp_path, [
        {"id": "OVR-0001", "find": "Splunk", "replace": "CrowdStrike"},
        {"id": "OVR-0002", "find": "JDA", "replace": "Blue Yonder"},
    ])
    store = YAMLOverrideStore(yaml_path=path)
    assert store.count() == 2


def test_apply_simple_replacement(tmp_path):
    """apply() replaces matching text."""
    path = _write_yaml(tmp_path, [
        {"id": "OVR-0001", "find": "Splunk", "replace": "CrowdStrike"},
    ])
    store = YAMLOverrideStore(yaml_path=path)
    result = store.apply("We integrate with Splunk for logging.")
    assert "CrowdStrike" in result.modified
    assert "Splunk" not in result.modified
    assert result.changed is True
    assert result.total_replacements == 1


def test_apply_case_insensitive(tmp_path):
    """apply() is case-insensitive by default."""
    path = _write_yaml(tmp_path, [
        {"id": "OVR-0001", "find": "splunk", "replace": "CrowdStrike"},
    ])
    store = YAMLOverrideStore(yaml_path=path)
    result = store.apply("SPLUNK and Splunk and splunk")
    assert result.modified == "CrowdStrike and CrowdStrike and CrowdStrike"
    assert result.total_replacements == 3


def test_apply_whole_word(tmp_path):
    """whole_word=true only matches word boundaries."""
    path = _write_yaml(tmp_path, [
        {"id": "OVR-0001", "find": "JDA", "replace": "Blue Yonder", "whole_word": True},
    ])
    store = YAMLOverrideStore(yaml_path=path)

    # Should match standalone JDA
    result = store.apply("JDA provides supply chain solutions.")
    assert "Blue Yonder" in result.modified
    assert result.changed is True

    # Should NOT match JDA inside another word
    result2 = store.apply("The XJDAX system is ready.")
    assert result2.changed is False
    assert result2.modified == "The XJDAX system is ready."


def test_apply_no_match(tmp_path):
    """apply() returns unchanged text when no overrides match."""
    path = _write_yaml(tmp_path, [
        {"id": "OVR-0001", "find": "Splunk", "replace": "CrowdStrike"},
    ])
    store = YAMLOverrideStore(yaml_path=path)
    result = store.apply("No matching terms here.")
    assert result.changed is False
    assert result.modified == result.original


def test_apply_multiple_overrides(tmp_path):
    """Multiple overrides applied in order."""
    path = _write_yaml(tmp_path, [
        {"id": "OVR-0001", "find": "Splunk", "replace": "CrowdStrike"},
        {"id": "OVR-0002", "find": "JDA", "replace": "Blue Yonder", "whole_word": True},
    ])
    store = YAMLOverrideStore(yaml_path=path)
    result = store.apply("JDA uses Splunk for monitoring.")
    assert "Blue Yonder" in result.modified
    assert "CrowdStrike" in result.modified
    assert len(result.matches) == 2


def test_apply_family_filter(tmp_path):
    """Family filter restricts which overrides apply."""
    path = _write_yaml(tmp_path, [
        {"id": "OVR-0001", "find": "Splunk", "replace": "CrowdStrike", "family": "wms"},
        {"id": "OVR-0002", "find": "JDA", "replace": "Blue Yonder"},
    ])
    store = YAMLOverrideStore(yaml_path=path)

    # With family=planning, OVR-0001 (wms-only) should be skipped
    result = store.apply("Splunk and JDA", family="planning")
    assert "Splunk" in result.modified  # NOT replaced (wrong family)
    assert "Blue Yonder" in result.modified  # Replaced (no family restriction)


def test_disabled_override_skipped(tmp_path):
    """Disabled overrides are not applied."""
    path = _write_yaml(tmp_path, [
        {"id": "OVR-0001", "find": "Splunk", "replace": "CrowdStrike", "enabled": False},
    ])
    store = YAMLOverrideStore(yaml_path=path)
    result = store.apply("Splunk is great.")
    assert result.changed is False


def test_skips_invalid_entries(tmp_path):
    """Invalid entries (missing find/id) are skipped."""
    path = _write_yaml(tmp_path, [
        {"id": "OVR-0001", "find": "Splunk", "replace": "CrowdStrike"},
        {"id": "", "find": "Bad", "replace": "Entry"},
        {"id": "OVR-0003", "find": "", "replace": "Empty"},
    ])
    store = YAMLOverrideStore(yaml_path=path)
    assert store.count() == 1


def test_audit_trail(tmp_path):
    """OverrideResult contains audit details for each match."""
    path = _write_yaml(tmp_path, [
        {"id": "OVR-0001", "find": "Splunk", "replace": "CrowdStrike"},
    ])
    store = YAMLOverrideStore(yaml_path=path)
    result = store.apply("Splunk, Splunk, Splunk!")
    assert len(result.matches) == 1
    assert result.matches[0].override_id == "OVR-0001"
    assert result.matches[0].count == 3
    assert result.total_replacements == 3


def test_list_overrides(tmp_path):
    """list_overrides with filtering."""
    path = _write_yaml(tmp_path, [
        {"id": "OVR-0001", "find": "A", "replace": "B", "enabled": True},
        {"id": "OVR-0002", "find": "C", "replace": "D", "enabled": False},
        {"id": "OVR-0003", "find": "E", "replace": "F", "family": "wms"},
    ])
    store = YAMLOverrideStore(yaml_path=path)

    assert len(store.list_overrides()) == 3
    assert len(store.list_overrides(enabled_only=True)) == 2
    assert len(store.list_overrides(family="wms")) == 3  # includes no-family overrides
    assert len(store.list_overrides(family="planning")) == 2  # excludes wms-only


def test_remove_override(tmp_path):
    """remove() deletes an override by ID."""
    path = _write_yaml(tmp_path, [
        {"id": "OVR-0001", "find": "A", "replace": "B"},
        {"id": "OVR-0002", "find": "C", "replace": "D"},
    ])
    store = YAMLOverrideStore(yaml_path=path)
    assert store.remove("OVR-0001") is True
    assert store.count() == 1
    assert store.remove("OVR-9999") is False


def test_stats(tmp_path):
    """stats() returns summary."""
    path = _write_yaml(tmp_path, [
        {"id": "OVR-0001", "find": "A", "replace": "B"},
        {"id": "OVR-0002", "find": "C", "replace": "D", "enabled": False},
        {"id": "OVR-0003", "find": "E", "replace": "F", "family": "wms"},
    ])
    store = YAMLOverrideStore(yaml_path=path)
    s = store.stats()
    assert s["total"] == 3
    assert s["enabled"] == 2
    assert s["disabled"] == 1
    assert "wms" in s["families"]


def test_empty_store():
    """Store with no YAML path is empty and functional."""
    store = YAMLOverrideStore()
    assert store.count() == 0
    result = store.apply("Some text")
    assert result.changed is False


def test_get_override_protocol(tmp_path):
    """get_override (protocol method) returns modified text when matched."""
    path = _write_yaml(tmp_path, [
        {"id": "OVR-0001", "find": "Splunk", "replace": "CrowdStrike"},
    ])
    store = YAMLOverrideStore(yaml_path=path)
    assert store.get_override("We use Splunk") == "We use CrowdStrike"
    assert store.get_override("No match here") is None
