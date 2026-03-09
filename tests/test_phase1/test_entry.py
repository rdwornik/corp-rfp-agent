"""Tests for KBEntry dataclass."""

from corp_rfp_agent.kb.entry import KBEntry


def test_valid_entry():
    """KBEntry with question+answer is valid."""
    entry = KBEntry(question="How does X work?", answer="X works by doing Y.")
    assert entry.is_valid()


def test_empty_answer_invalid():
    """KBEntry with empty answer is invalid."""
    entry = KBEntry(question="How?", answer="")
    assert not entry.is_valid()


def test_empty_question_invalid():
    """KBEntry with empty question is invalid."""
    entry = KBEntry(question="", answer="Some answer")
    assert not entry.is_valid()


def test_whitespace_only_invalid():
    """KBEntry with whitespace-only fields is invalid."""
    entry = KBEntry(question="  ", answer="  \n  ")
    assert not entry.is_valid()


def test_auto_generates_id():
    """KBEntry generates deterministic ID from content."""
    entry = KBEntry(
        question="Does BY support REST APIs?",
        answer="Yes.",
        family_code="wms",
        category="technical",
    )
    assert entry.id != ""
    assert entry.id.startswith("WMS-TECH-")


def test_same_content_same_id():
    """Same content produces the same ID (stability)."""
    kwargs = dict(question="Does BY support REST APIs?", answer="Yes.",
                  family_code="wms", category="technical")
    entry1 = KBEntry(**kwargs)
    entry2 = KBEntry(**kwargs)
    assert entry1.id == entry2.id


def test_different_content_different_id():
    """Different content produces different IDs."""
    entry1 = KBEntry(question="Question A?", answer="Answer A.")
    entry2 = KBEntry(question="Question B?", answer="Answer B.")
    assert entry1.id != entry2.id


def test_explicit_id_preserved():
    """Explicit ID is not overwritten by auto-generation."""
    entry = KBEntry(id="MY-CUSTOM-001", question="Q?", answer="A.")
    assert entry.id == "MY-CUSTOM-001"


def test_last_updated_defaults_to_today():
    """last_updated is set to today if not provided."""
    from datetime import date
    entry = KBEntry(question="Q?", answer="A.")
    assert entry.last_updated == date.today().isoformat()


def test_default_values():
    """Default values are sensible."""
    entry = KBEntry(question="Q?", answer="A.")
    assert entry.category == "general"
    assert entry.confidence == "draft"
    assert entry.source_type == "legacy"
    assert entry.solution_codes == []
    assert entry.tags == []
    assert entry.cloud_native_only is False
