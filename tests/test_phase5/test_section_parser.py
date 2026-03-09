"""Tests for section_parser -- validates against golden fixture.

These tests verify that v2 section_parser finds the exact same sections
as the v1 code from src/rfp_answer_word.py.
"""

import pytest
from pathlib import Path
from docx import Document

from corp_rfp_agent.agents.word.section_parser import (
    detect_heading_level,
    build_section_tree,
    collect_answerable_sections,
    count_sections_recursive,
)

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


@pytest.fixture
def golden_doc():
    path = FIXTURES_DIR / "word_golden.docx"
    assert path.exists(), f"Fixture not found: {path}"
    return Document(str(path))


def test_section_tree_detection(golden_doc):
    """Auto-detect heading hierarchy builds correct tree."""
    sections = build_section_tree(golden_doc)

    # Top level: "VIII. Architecture" at level 0
    assert len(sections) == 1
    root = sections[0]
    assert root.title == "VIII. Architecture"
    assert root.level == 0

    # Children: 3 numbered sections
    assert len(root.children) == 3
    assert root.children[0].title == "1. Integration Capabilities"
    assert root.children[1].title == "2. Security"
    assert root.children[2].title == "3. Monitoring"


def test_integration_subsections(golden_doc):
    """APIs, File Exchanges, Event Bus are level 2 under Integration."""
    sections = build_section_tree(golden_doc)
    integration = sections[0].children[0]
    assert integration.title == "1. Integration Capabilities"
    assert len(integration.children) == 3

    titles = [c.title for c in integration.children]
    assert titles == ["APIs", "File Exchanges", "Event Bus"]

    for child in integration.children:
        assert child.level == 2


def test_content_paragraph_counts(golden_doc):
    """Each section has the expected number of content paragraphs."""
    sections = build_section_tree(golden_doc)
    integration = sections[0].children[0]

    # Integration has 1 own content paragraph
    assert len(integration.content_paragraphs) == 1

    # APIs: 2, File Exchanges: 3, Event Bus: 2
    assert len(integration.children[0].content_paragraphs) == 2
    assert len(integration.children[1].content_paragraphs) == 3
    assert len(integration.children[2].content_paragraphs) == 2


def test_answerable_sections(golden_doc):
    """Only sections with content are collected for answering."""
    sections = build_section_tree(golden_doc)
    blocks = collect_answerable_sections(sections)

    # 6 answerable: Integration(own content) + APIs + File Exchanges + Event Bus + Security + Monitoring
    assert len(blocks) == 6

    # Chapter heading alone (no own content) should NOT be answerable
    breadcrumbs = [b.breadcrumb for b in blocks]
    assert not any(bc == "VIII. Architecture" for bc in breadcrumbs)


def test_breadcrumbs(golden_doc):
    """Breadcrumb paths correctly reflect hierarchy."""
    sections = build_section_tree(golden_doc)
    blocks = collect_answerable_sections(sections)

    breadcrumbs = [b.breadcrumb for b in blocks]
    assert "VIII. Architecture > 1. Integration Capabilities > APIs" in breadcrumbs
    assert "VIII. Architecture > 1. Integration Capabilities > File Exchanges" in breadcrumbs
    assert "VIII. Architecture > 1. Integration Capabilities > Event Bus" in breadcrumbs
    assert "VIII. Architecture > 2. Security" in breadcrumbs
    assert "VIII. Architecture > 3. Monitoring" in breadcrumbs


def test_heading_level_detection_patterns(golden_doc):
    """Verify heading level detection for various formatting patterns."""
    heading_levels = {}
    for para in golden_doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        level = detect_heading_level(para)
        if level is not None:
            heading_levels[text] = level

    # Roman numeral -> level 0
    assert heading_levels.get("VIII. Architecture") == 0
    # Numbered sections -> level 1
    assert heading_levels.get("1. Integration Capabilities") == 1
    assert heading_levels.get("2. Security") == 1
    assert heading_levels.get("3. Monitoring") == 1
    # Short bold text -> level 2
    assert heading_levels.get("APIs") == 2
    assert heading_levels.get("File Exchanges") == 2
    assert heading_levels.get("Event Bus") == 2


def test_count_sections_recursive(golden_doc):
    """count_sections_recursive returns total section count."""
    sections = build_section_tree(golden_doc)
    total = count_sections_recursive(sections)
    # 1 (VIII.Arch) + 3 (Integration, Security, Monitoring) + 3 (APIs, File Exchanges, Event Bus) = 7
    assert total == 7


def test_full_content_property(golden_doc):
    """Section.full_content joins content paragraphs."""
    sections = build_section_tree(golden_doc)
    apis = sections[0].children[0].children[0]
    assert apis.title == "APIs"
    content = apis.full_content
    assert "RESTful APIs" in content
    assert "rate limiting" in content


def test_full_content_with_children(golden_doc):
    """Section.full_content_with_children includes child content."""
    sections = build_section_tree(golden_doc)
    integration = sections[0].children[0]
    full = integration.full_content_with_children
    assert "integrate seamlessly" in full
    assert "### APIs" in full
    assert "RESTful APIs" in full


def test_insert_after_para_correct(golden_doc):
    """insert_after_para points to the last content paragraph of each section."""
    sections = build_section_tree(golden_doc)
    blocks = collect_answerable_sections(sections)

    for block in blocks:
        # The insert_after_para should be the index of the last content paragraph
        if block.section.content_paragraphs:
            last_para_idx = block.section.content_paragraphs[-1][0]
            assert block.insert_after_para == last_para_idx
