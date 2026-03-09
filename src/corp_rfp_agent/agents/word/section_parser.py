"""Section parser for Word documents.

Ported from src/rfp_answer_word.py -- exact same heading detection logic.
Parses a document into a section tree using auto-detected headings
(bold + numbering patterns), then collects answerable sections.
"""

import re
from typing import Optional

from docx import Document

from corp_rfp_agent.agents.word.models import Section, AnswerableBlock


def detect_heading_level(paragraph) -> Optional[int]:
    """Detect heading level from formatting patterns. Returns 0-3 or None.

    Logic ported exactly from src/rfp_answer_word.py:
    - Method 1: Proper Word heading styles ("Heading 1" -> level 0, etc.)
    - Method 2: All-bold text + numbering pattern:
        - Roman numeral (VIII.) -> level 0
        - Numbered (1.) -> level 1
        - Sub-numbered (1.1) -> level 2
        - Short bold text (<60 chars) -> level 2
    """
    text = paragraph.text.strip()
    if not text:
        return None

    style = paragraph.style.name if paragraph.style else "Normal"

    # Method 1: Proper Word heading styles (if they exist)
    if "Heading" in style:
        m = re.search(r'(\d+)', style)
        if m:
            return int(m.group(1)) - 1  # 0-indexed

    # Method 2: Auto-detect from bold + numbering patterns
    # Check if ALL runs with text are bold
    runs_with_text = [r for r in paragraph.runs if r.text.strip()]
    if not runs_with_text:
        return None
    all_bold = all(r.bold for r in runs_with_text)

    if not all_bold:
        return None  # Not a heading

    # Bold text -- now determine LEVEL from numbering pattern

    # Level 0: Roman numeral chapter (VIII. Architecture)
    if re.match(r'^[IVXLCDM]+[\.\)]\s', text):
        return 0

    # Level 1: Numbered section (1. Macro Technical Architecture)
    if re.match(r'^\d+[\.\)]\s', text):
        return 1

    # Level 2: Sub-numbered (1.1 or 1.1. pattern)
    if re.match(r'^\d+\.\d+[\.\)]?\s', text):
        return 2

    # Level 2: Short bold text without numbers = subsection header
    # (e.g. "Containerization", "Database", "APIs")
    if len(text) < 60:
        return 2

    return None  # Not a heading -- it's content


def build_section_tree(doc) -> list[Section]:
    """Parse document into section tree using auto-detected headings."""
    sections = []
    current_stack = []  # stack of sections by level

    for i, para in enumerate(doc.paragraphs):
        text = para.text.strip()
        if not text:
            continue

        level = detect_heading_level(para)

        if level is not None:
            # This is a heading -- create new section
            section = Section(
                level=level,
                title=text,
                para_index=i,
            )

            # Find parent: pop stack until we find a section with lower level
            while current_stack and current_stack[-1].level >= level:
                current_stack.pop()

            if current_stack:
                current_stack[-1].children.append(section)
            else:
                sections.append(section)

            current_stack.append(section)
        else:
            # This is content -- add to deepest current section
            if current_stack:
                current_stack[-1].content_paragraphs.append((i, text))

    return sections


def collect_answerable_sections(sections: list[Section]) -> list[AnswerableBlock]:
    """Collect leaf sections (and parents with own content) that need BY answers."""
    blocks = []

    def walk(section, parent_context=""):
        context = f"{parent_context} > {section.title}" if parent_context else section.title

        if section.children:
            # Section has children -- answer each child, not the parent
            # BUT if parent has its own content paragraphs, answer those too
            if section.content_paragraphs:
                blocks.append(AnswerableBlock(
                    section=section,
                    breadcrumb=context,
                    content=section.full_content,
                    insert_after_para=section.content_paragraphs[-1][0],
                ))
            for child in section.children:
                walk(child, context)
        else:
            # Leaf section -- answer it
            if section.content_paragraphs:
                blocks.append(AnswerableBlock(
                    section=section,
                    breadcrumb=context,
                    content=section.full_content,
                    insert_after_para=section.content_paragraphs[-1][0],
                ))

    for s in sections:
        walk(s)

    return blocks


def count_sections_recursive(sections: list[Section]) -> int:
    """Count total sections in the tree."""
    count = 0
    for s in sections:
        count += 1
        count += count_sections_recursive(s.children)
    return count


def print_section_tree(sections: list[Section], indent: int = 0):
    """Print the section tree for confirmation."""
    for i, section in enumerate(sections):
        is_last = (i == len(sections) - 1)
        prefix = " " * indent
        connector = "`-- " if is_last else "|-- "

        content_count = len(section.content_paragraphs)
        children_count = len(section.children)

        info_parts = []
        if children_count:
            info_parts.append(f"{children_count} subsections")
        if content_count:
            info_parts.append(f"{content_count} paragraphs")
        info = f" ({', '.join(info_parts)})" if info_parts else ""

        print(f"{prefix}{connector}{section.title}{info}")

        if section.children:
            child_indent = indent + 4
            print_section_tree(section.children, child_indent)
