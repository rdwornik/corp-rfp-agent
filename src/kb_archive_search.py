"""
kb_archive_search.py
Query the archive registry without a database.

Usage:
  python src/kb_archive_search.py --list
  python src/kb_archive_search.py --client "Acme"
  python src/kb_archive_search.py --family wms
  python src/kb_archive_search.py --from 2023-Q1 --to 2024-Q4
  python src/kb_archive_search.py --id ARC-0001
  python src/kb_archive_search.py --json                  # machine-readable
"""

import json
import argparse
from pathlib import Path

PROJECT_ROOT  = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "data/kb/archive/archive_registry.json"


# ── helpers ──────────────────────────────────────────────────

def load_registry() -> list[dict]:
    if not REGISTRY_PATH.exists():
        return []
    with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("entries", [])


def _quarter_to_sort_key(q_str: str) -> str:
    """Convert '2024-Q3' to '2024-3' for consistent lexicographic sort."""
    return q_str.replace("-Q", "-") if q_str else "0000-0"


def in_date_range(date_est: str, from_q: str, to_q: str) -> bool:
    """Check if date_estimated falls within [from_q, to_q] range."""
    if not date_est:
        return False
    key = _quarter_to_sort_key(date_est)
    lo  = _quarter_to_sort_key(from_q) if from_q else "0000-0"
    hi  = _quarter_to_sort_key(to_q)   if to_q   else "9999-9"
    return lo <= key <= hi


def filter_entries(entries: list[dict], client: str = None, family: str = None,
                   from_q: str = None, to_q: str = None,
                   arc_id: str = None) -> list[dict]:
    result = entries
    if arc_id:
        result = [e for e in result if e.get("archive_id","").upper() == arc_id.upper()]
    if client:
        cl = client.lower()
        result = [e for e in result if cl in e.get("client","").lower()]
    if family:
        result = [e for e in result if e.get("family_code","").lower() == family.lower()]
    if from_q or to_q:
        result = [e for e in result if in_date_range(e.get("date_estimated",""), from_q, to_q)]
    return result


# ── display ──────────────────────────────────────────────────

def print_list(entries: list[dict]) -> None:
    if not entries:
        print("  (no matching entries)")
        return
    print()
    print(f"  {'ID':<10} {'Date':>8} {'Family':<12} {'Type':<10} {'Accepted':>9}  {'Client'}")
    print("  " + "-" * 70)
    for e in sorted(entries, key=lambda x: x.get("date_estimated","") or ""):
        arc_id    = e.get("archive_id","?")
        date_est  = e.get("date_estimated","?")
        family    = e.get("family_code","?").upper()
        rtype     = e.get("rfp_type","?")
        accepted  = e.get("extraction_stats",{}).get("accepted",0)
        client    = e.get("client","?")
        print(f"  {arc_id:<10} {date_est:>8} {family:<12} {rtype:<10} {accepted:>9}  {client}")
    print()
    print(f"  Total: {len(entries)} file(s)")
    print()


def print_detail(entry: dict) -> None:
    print()
    print(f"  Archive ID:    {entry.get('archive_id','?')}")
    print(f"  Original file: {entry.get('original_filename','?')}")
    print(f"  Archived as:   {entry.get('archived_filename','?')}")
    print(f"  Client:        {entry.get('client','?')} ({entry.get('client_industry','?')})")
    print(f"  Family:        {entry.get('family_code','?').upper()}")
    print(f"  Solutions:     {', '.join(entry.get('solution_codes',[])) or 'all'}")
    print(f"  Type:          {entry.get('rfp_type','?')}")
    print(f"  Date:          {entry.get('date_estimated','?')}")
    print(f"  Region:        {entry.get('region','?')}")
    print(f"  Processed:     {entry.get('date_processed','?')}")
    print()
    st = entry.get("extraction_stats", {})
    print(f"  Extraction stats:")
    print(f"    Sheets processed:  {st.get('sheets_processed','?')} / {st.get('total_sheets','?')}")
    print(f"    Q/A extracted:     {st.get('total_qa_extracted','?')}")
    print(f"    Accepted:          {st.get('accepted','?')}")
    print(f"    Skipped:           {st.get('skipped','?')}")
    cats = st.get("categories", {})
    if cats:
        cat_str = "  ".join(f"{k}:{v}" for k, v in sorted(cats.items()))
        print(f"    Categories:        {cat_str}")
    print()
    if entry.get("notes"):
        print(f"  Notes: {entry['notes']}")
    if entry.get("tags"):
        print(f"  Tags:  {', '.join(entry['tags'])}")
    print(f"  Structure file:    {entry.get('structure_file','?')}")
    print(f"  Extraction file:   {entry.get('extraction_file','?')}")
    print()


# ── main ─────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search the KB archive registry.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python src/kb_archive_search.py --list
  python src/kb_archive_search.py --client "Acme"
  python src/kb_archive_search.py --family wms
  python src/kb_archive_search.py --from 2023-Q1 --to 2024-Q4
  python src/kb_archive_search.py --id ARC-0001
  python src/kb_archive_search.py --json
        """,
    )
    parser.add_argument("--list",   action="store_true", help="List all archived files")
    parser.add_argument("--client", default=None, help="Filter by client name (partial match)")
    parser.add_argument("--family", default=None, help="Filter by family code (wms, logistics, …)")
    parser.add_argument("--from",   dest="from_q", default=None, metavar="YYYY-QN",
                        help="Start of date range, e.g. 2023-Q1")
    parser.add_argument("--to",     dest="to_q",   default=None, metavar="YYYY-QN",
                        help="End of date range, e.g. 2024-Q4")
    parser.add_argument("--id",     default=None, help="Show detail for a specific archive ID")
    parser.add_argument("--json",   action="store_true", help="Output raw JSON")
    args = parser.parse_args()

    entries = load_registry()

    if not entries and not args.json:
        print("\n  Archive is empty. Process some files first:")
        print("    python src/kb_extract_historical.py --family wms")
        return

    filtered = filter_entries(entries,
                               client=args.client,
                               family=args.family,
                               from_q=args.from_q,
                               to_q=args.to_q,
                               arc_id=args.id)

    if args.json:
        print(json.dumps(filtered, indent=2))
        return

    if args.id and len(filtered) == 1:
        print_detail(filtered[0])
    else:
        print_list(filtered)


if __name__ == "__main__":
    main()
