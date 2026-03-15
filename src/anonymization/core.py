"""
anonymization/core.py
Core anonymize and de-anonymize functions.
"""

import re
from typing import Dict, List, Tuple
from .config import get_blocklist, get_session


def anonymize(text: str) -> Tuple[str, Dict[str, str]]:
    """
    Anonymize text by replacing blocklist terms.

    Returns:
        tuple: (anonymized_text, mapping_dict)
    """
    if not text:
        return text, {}

    session = get_session()
    blocklist = get_blocklist()
    placeholder_base = session.get("placeholder", "[CUSTOMER]")

    mapping = {}
    result = text
    counter = 1

    for term in blocklist:
        if not term:
            continue

        pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)

        if pattern.search(result):
            if len(blocklist) > 1:
                placeholder = f"{placeholder_base.rstrip(']')}_{counter}]"
            else:
                placeholder = placeholder_base

            mapping[placeholder] = term
            result = pattern.sub(placeholder, result)
            counter += 1

    return result, mapping


def deanonymize(text: str, mapping: Dict[str, str] = None) -> str:
    """
    Restore original terms using mapping or session customer.
    """
    if not text:
        return text

    result = text

    # Use mapping if provided
    if mapping:
        for placeholder, original in mapping.items():
            result = result.replace(placeholder, original)

    # Replace remaining [CUSTOMER] with session customer
    session = get_session()
    customer_name = session.get("customer_name", "")
    placeholder = session.get("placeholder", "[CUSTOMER]")

    if customer_name and placeholder in result:
        result = result.replace(placeholder, customer_name)

    return result


def check(text: str) -> List[str]:
    """
    Check text for blocklist terms without modifying.
    """
    if not text:
        return []

    blocklist = get_blocklist()
    found = []

    for term in blocklist:
        if not term:
            continue
        pattern = re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)
        matches = pattern.findall(text)
        found.extend(matches)

    return found
