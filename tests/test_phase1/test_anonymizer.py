"""Tests for Anonymizer service."""

from corp_rfp_agent.anonymize.service import Anonymizer


def test_anonymize_client_name():
    """Anonymize replaces client name with [Customer]."""
    anon = Anonymizer(client_name="Acme Corp")
    result = anon.anonymize("This RFP is for Acme Corp's warehouse.")
    assert "[Customer]" in result
    assert "Acme Corp" not in result


def test_anonymize_case_insensitive():
    """Anonymize is case-insensitive."""
    anon = Anonymizer(client_name="Acme")
    result = anon.anonymize("acme wants ACME features from AcMe systems.")
    assert "acme" not in result.lower().replace("[customer]", "")


def test_de_anonymize_restores():
    """De-anonymize restores original terms."""
    anon = Anonymizer(client_name="Acme Corp")
    anonymized = anon.anonymize("Acme Corp needs a WMS solution.")
    restored = anon.de_anonymize(anonymized)
    assert "Acme Corp" in restored
    assert "[Customer]" not in restored


def test_roundtrip():
    """anonymize -> de_anonymize = original text."""
    anon = Anonymizer(client_name="BigRetailer")
    original = "BigRetailer wants to deploy Blue Yonder WMS for BigRetailer warehouses."
    restored = anon.de_anonymize(anon.anonymize(original))
    assert restored == original


def test_multiple_terms():
    """Multiple terms handled correctly."""
    anon = Anonymizer(
        client_name="Acme",
        extra_terms={"secret-project": "[Project]", "CEO John": "[Executive]"},
    )
    text = "Acme's secret-project led by CEO John is strategic."
    result = anon.anonymize(text)
    assert "Acme" not in result
    assert "secret-project" not in result
    assert "CEO John" not in result
    assert "[Customer]" in result
    assert "[Project]" in result
    assert "[Executive]" in result


def test_no_terms_unchanged():
    """No terms configured -> text unchanged."""
    anon = Anonymizer()
    text = "Nothing sensitive here."
    assert anon.anonymize(text) == text
    assert anon.de_anonymize(text) == text


def test_term_count():
    """term_count reflects number of configured terms."""
    anon = Anonymizer()
    assert anon.term_count == 0

    anon = Anonymizer(client_name="X")
    assert anon.term_count == 1

    anon = Anonymizer(client_name="X", extra_terms={"a": "b", "c": "d"})
    assert anon.term_count == 3
