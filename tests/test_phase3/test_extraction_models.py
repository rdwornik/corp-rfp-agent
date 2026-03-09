"""Tests for extraction pipeline data models."""

from corp_rfp_agent.pipelines.extraction.models import (
    StructureMap,
    RawRow,
    ClassifiedRow,
    ExtractionResult,
)


def test_raw_row_defaults():
    """RawRow creates with sensible defaults."""
    row = RawRow(row_num=5, sheet="Sheet1", question="Q?", answer="A.")
    assert row.row_num == 5
    assert row.sheet == "Sheet1"
    assert row.status == "candidate"
    assert row.category_from_excel == ""


def test_classified_row_all_fields():
    """ClassifiedRow stores classification data."""
    raw = RawRow(row_num=1, sheet="S1", question="Q?", answer="A.")
    classified = ClassifiedRow(
        row=raw,
        classification="BY_PRODUCT_ANSWER",
        keep=True,
        category="functional",
        subcategory="picking",
        tags=["voice", "mobile"],
        solution_codes=["wms"],
        question_generic="How does pick-by-voice work?",
    )
    assert classified.keep is True
    assert classified.classification == "BY_PRODUCT_ANSWER"
    assert "voice" in classified.tags


def test_extraction_result_counts():
    """ExtractionResult tracks counts."""
    result = ExtractionResult(
        filename="test.xlsx",
        family="wms",
        total_rows=100,
        extracted=80,
        accepted=60,
        skipped=20,
        by_product_answers=60,
        client_data_filtered=15,
        empty_filtered=5,
        archive_id="ARC-0001",
    )
    assert result.total_rows == 100
    assert result.accepted == 60
    assert result.archive_id == "ARC-0001"


def test_structure_map_holds_sheet_info():
    """StructureMap stores sheet metadata."""
    smap = StructureMap(
        filename="rfp.xlsx",
        sheets=[
            {"name": "Sheet1", "question_col": "B", "answer_col": "C"},
            {"name": "Sheet2", "question_col": "A", "answer_col": "D"},
        ],
        file_type="response",
        estimated_questions=45,
    )
    assert len(smap.sheets) == 2
    assert smap.file_type == "response"
    assert smap.estimated_questions == 45
